import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Optional
from urllib.parse import quote as url_quote

import httpx
from bs4 import BeautifulSoup

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Geocache (persisted to data/geocache.json)
# ──────────────────────────────────────────────
_GEOCACHE_FILE = DATA_DIR / "geocache.json"

# Per-source scrape results (populated after each scrape_all call)
_last_scrape_results: dict = {}


def _load_geocache() -> dict:
    if _GEOCACHE_FILE.exists():
        try:
            return json.loads(_GEOCACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_geocache(cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _GEOCACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _geocode_batch(locations: list, cache: dict) -> None:
    """Geocode up to 25 uncached locations via Nominatim (max 1 req/sec)."""
    to_fetch = [loc for loc in locations if loc and loc.strip().lower() not in cache][:25]
    if not to_fetch:
        return

    headers = {
        "User-Agent": "BrocantesApp/1.0 (self-hosted calendar aggregator)",
        "Accept-Language": "fr",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for loc in to_fetch:
            key = loc.strip().lower()
            try:
                url = (
                    "https://nominatim.openstreetmap.org/search"
                    f"?q={url_quote(loc)}&format=json&countrycodes=fr&limit=1"
                )
                resp = await client.get(url, timeout=10)
                data = resp.json()
                if data:
                    p = data[0]
                    # Extract a short city from display_name parts
                    parts = [x.strip() for x in p["display_name"].split(",")]
                    city = ""
                    for part in parts[1:4]:
                        cleaned = re.sub(r"^\d{4,5}\s*", "", part).strip()
                        if cleaned and 2 <= len(cleaned) < 50:
                            city = cleaned
                            break
                    cache[key] = {
                        "lat": float(p["lat"]),
                        "lng": float(p["lon"]),
                        "city": city or parts[0],
                    }
                    logger.debug("Geocoded '%s' → %s", loc, cache[key]["city"])
                else:
                    cache[key] = None  # cache miss so we don't retry every refresh
            except Exception as exc:
                logger.debug("Geocode error '%s': %s", loc, exc)
                cache[key] = None
            await asyncio.sleep(1.1)  # Nominatim: max 1 req/sec


# ──────────────────────────────────────────────
# Date parsing
# ──────────────────────────────────────────────
FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "jan": 1, "fév": 2, "fev": 2, "avr": 4,
    "juil": 7, "sep": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
}

FRENCH_DAY_NAMES = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"
]


def parse_french_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    s = raw.strip().lower()
    for day in FRENCH_DAY_NAMES:
        s = s.replace(day, "")
    s = s.strip()

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    m = re.search(r"(\d{1,2})\s+([a-zéûùàâêîôèë]+)\.?\s+(\d{4})", s)
    if m:
        month_num = FRENCH_MONTHS.get(m.group(2).rstrip("."))
        if month_num:
            try:
                return date(int(m.group(3)), month_num, int(m.group(1)))
            except ValueError:
                pass

    m = re.search(r"(\d{1,2})\s+([a-zéûùàâêîôèë]+)\.?$", s)
    if m:
        month_num = FRENCH_MONTHS.get(m.group(2).rstrip("."))
        if month_num:
            today = date.today()
            year = today.year
            try:
                d = date(year, month_num, int(m.group(1)))
                if d < today:
                    d = date(year + 1, month_num, int(m.group(1)))
                return d
            except ValueError:
                pass

    return None


# ──────────────────────────────────────────────
# Scraping helpers
# ──────────────────────────────────────────────
def make_uid(title: str, event_date: str, location: str) -> str:
    key = f"{title.lower().strip()}|{event_date}|{location.lower().strip()}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


CARD_SELECTORS = [
    "article.card", ".event-card", "[data-event]", ".event-item",
    ".brocante-item", ".vide-grenier-item", "article.event",
    ".listing-item", ".search-result", ".result-item",
    "li.event", ".annonce", ".card", "article",
]
TITLE_SELECTORS    = [".title", ".card-title", ".event-title", ".name", "h2", "h3", "h4", "h5"]
DATE_SELECTORS     = [".date", ".event-date", ".date-str", ".datetime", "time", "[datetime]"]
LOCATION_SELECTORS = [".location", ".lieu", ".city", ".address", ".place", ".localisation", ".ville", ".commune"]
DESC_SELECTORS     = [".description", ".excerpt", ".summary", ".intro", "p"]


def _strip(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _extract(container, selectors: list) -> str:
    for sel in selectors:
        found = container.select_one(sel)
        if found:
            text = _strip(found)
            if text:
                return text
    return ""


def _parse_jsonld(soup: BeautifulSoup, base_url: str, source_name: str) -> list:
    events = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "Event":
                continue
            title = item.get("name", "").strip()
            start = item.get("startDate", "")
            loc_obj = item.get("location", {})
            if isinstance(loc_obj, dict):
                addr = loc_obj.get("address", {})
                location = loc_obj.get("name", "") or (
                    addr.get("addressLocality", "") if isinstance(addr, dict) else str(addr)
                )
            else:
                location = str(loc_obj)
            parsed = parse_french_date(start)
            if title and parsed and parsed >= date.today():
                uid = make_uid(title, str(parsed), location)
                events.append({
                    "title": title,
                    "date_parsed": str(parsed),
                    "location": location,
                    "description": item.get("description", ""),
                    "url": item.get("url", base_url),
                    "uid": uid,
                    "source": source_name,
                })
    return events


def _parse_cards(soup: BeautifulSoup, base_url: str, source_name: str, base_domain: str) -> list:
    events = []
    for selector in CARD_SELECTORS:
        cards = soup.select(selector)
        if not cards:
            continue
        for card in cards:
            title = _extract(card, TITLE_SELECTORS)
            if not title:
                continue
            date_text = ""
            for sel in DATE_SELECTORS:
                el = card.select_one(sel)
                if el:
                    date_text = el.get("datetime", "") or _strip(el)
                    if date_text:
                        break
            location = _extract(card, LOCATION_SELECTORS)
            description = _extract(card, DESC_SELECTORS)
            link_el = card.find("a", href=True)
            url = base_url
            if link_el:
                href = link_el["href"]
                url = href if href.startswith("http") else base_domain + href
            parsed = parse_french_date(date_text)
            if parsed and parsed >= date.today():
                uid = make_uid(title, str(parsed), location)
                events.append({
                    "title": title,
                    "date_parsed": str(parsed),
                    "location": location,
                    "description": description,
                    "url": url,
                    "uid": uid,
                    "source": source_name,
                })
        if events:
            break
    return events


async def _scrape_source(
    client: httpx.AsyncClient, url: str, source_name: str, base_domain: str
) -> tuple:
    """Fetch and parse one source. Returns (events, result_info).

    Retries up to 3 times on network errors; HTTP errors (403, 500…) are not retried.
    """
    start = time.monotonic()
    result: dict = {"count": 0, "strategy": None, "error": None, "duration_s": 0.0}

    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            break
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            if attempt == 2:
                result["error"] = f"Network error: {type(exc).__name__}"
                result["duration_s"] = round(time.monotonic() - start, 2)
                logger.warning("Could not fetch %s after 3 attempts: %s", url, exc)
                return [], result
            await asyncio.sleep(2 ** attempt)
        except httpx.HTTPStatusError as exc:
            result["error"] = f"HTTP {exc.response.status_code}"
            result["duration_s"] = round(time.monotonic() - start, 2)
            logger.warning("HTTP error from %s: %s", url, exc)
            return [], result
        except Exception as exc:
            result["error"] = str(exc)[:200]
            result["duration_s"] = round(time.monotonic() - start, 2)
            logger.warning("Could not fetch %s: %s", url, exc)
            return [], result

    soup = BeautifulSoup(resp.text, "lxml")
    events = _parse_jsonld(soup, url, source_name)
    if events:
        result["strategy"] = "json-ld"
    else:
        events = _parse_cards(soup, url, source_name, base_domain)
        if events:
            result["strategy"] = "css"

    result["count"] = len(events)
    result["duration_s"] = round(time.monotonic() - start, 2)
    logger.info("%-25s → %d events (strategy: %s)", source_name, len(events), result["strategy"])
    return events, result


# ──────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────
async def scrape_all(lat: float, lng: float, radius_km: int) -> list:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
    }
    sources = [
        (
            f"https://brocabrac.fr/brocantes-vide-greniers?localisation={lat},{lng}&rayon={radius_km}",
            "brocabrac.fr",
            "https://brocabrac.fr",
        ),
        (
            f"https://vide-greniers.org/recherche?lat={lat}&lng={lng}&distance={radius_km}",
            "vide-greniers.org",
            "https://vide-greniers.org",
        ),
    ]

    all_events: list = []
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for url, name, domain in sources:
            src_events, src_result = await _scrape_source(client, url, name, domain)
            src_result["last_run"] = datetime.now().isoformat()
            _last_scrape_results[name] = src_result
            all_events.extend(src_events)

    # Deduplicate
    seen: dict = {}
    for ev in all_events:
        uid = ev.get("uid", "")
        if uid and uid not in seen:
            seen[uid] = ev
    unique_events = sorted(seen.values(), key=lambda e: e.get("date_parsed", "9999-12-31"))

    # Geocode event locations for rich iOS calendar cards
    geocache = _load_geocache()
    unique_locations = list({ev["location"] for ev in unique_events if ev.get("location")})
    await _geocode_batch(unique_locations, geocache)
    _save_geocache(geocache)

    for ev in unique_events:
        key = ev.get("location", "").strip().lower()
        if key and geocache.get(key):
            ev["geo"] = geocache[key]

    return unique_events
