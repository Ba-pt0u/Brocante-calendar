import asyncio
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from datetime import date, datetime
from typing import Optional
from urllib.parse import quote as url_quote, urljoin, urlparse, urlunparse, urlencode, parse_qs

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
    """Geocode uncached locations via Nominatim (max 1 req/sec, up to 100 per call)."""
    to_fetch = [loc for loc in locations if loc and loc.strip().lower() not in cache][:100]
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
                    f"?q={url_quote(loc)}&format=json&countrycodes=fr&limit=1&addressdetails=1"
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
                    postcode = p.get("address", {}).get("postcode", "")
                    cache[key] = {
                        "lat": float(p["lat"]),
                        "lng": float(p["lon"]),
                        "city": city or parts[0],
                        "postcode": postcode,
                    }
                    logger.debug("Geocoded '%s' → %s %s", loc, cache[key]["city"], postcode)
                # don't cache misses — allow retry on next scrape
            except Exception as exc:
                logger.debug("Geocode error '%s': %s", loc, exc)
                cache[key] = None
            await asyncio.sleep(1.1)  # Nominatim: max 1 req/sec


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _slugify(text: str) -> str:
    """Convert a French city name to a brocabrac.fr URL slug."""
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _dept_from_postcode(postcode: str) -> str:
    """Extract French department code from a 5-digit postal code."""
    if not postcode or len(postcode) < 2:
        return ""
    if postcode.startswith("97") or postcode.startswith("98"):
        return postcode[:3]
    return postcode[:2]


# ── Event type classification ──────────────────────────────────────────────────
# Order matters: specific patterns before generic ones.
_TYPE_PATTERNS = [
    ("vide-grenier",      re.compile(r"vide.?grenier", re.IGNORECASE)),
    ("vide-dressing",     re.compile(r"vide.?dressing", re.IGNORECASE)),
    ("vide-maison",       re.compile(r"vide.?maison", re.IGNORECASE)),
    ("braderie",          re.compile(r"braderie", re.IGNORECASE)),
    ("bourse-livres",     re.compile(r"bourse.{0,15}(?:livres?|cd|dvd|jeux)", re.IGNORECASE)),
    ("bourse-collection", re.compile(r"bourse.{0,10}collection", re.IGNORECASE)),
    ("bourse-jouets",     re.compile(r"bourse.{0,15}(?:jouets?|puéri|puericulture)", re.IGNORECASE)),
    ("bourse-vetements",  re.compile(r"bourse.{0,15}(?:vêtements?|vetements?)", re.IGNORECASE)),
    ("marche-livres",     re.compile(r"march[eé].{0,8}livres?", re.IGNORECASE)),
    ("marche-noel",       re.compile(r"march[eé].{0,10}no[eë]l|no[eë]l.{0,10}march[eé]", re.IGNORECASE)),
    ("marche-puces",      re.compile(r"march[eé].{0,8}puce|puce de", re.IGNORECASE)),
    ("bourse",            re.compile(r"\bbourse\b", re.IGNORECASE)),
    ("brocante",          re.compile(r"brocante", re.IGNORECASE)),
]


def _classify_event(title: str) -> str:
    for ev_type, pattern in _TYPE_PATTERNS:
        if pattern.search(title):
            return ev_type
    return "autre"


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
            location = ""
            geo_query = ""
            locality = ""
            addr_part = ""
            if isinstance(loc_obj, dict):
                addr = loc_obj.get("address", {})
                venue = loc_obj.get("name", "").strip()
                if isinstance(addr, dict):
                    postcode = addr.get("postalCode", "").strip()
                    locality = addr.get("addressLocality", "").strip()
                    street = addr.get("streetAddress", "").strip()
                    # Display location: prefer venue name, fall back to locality
                    location = venue or locality
                    # Primary geocoding query: full address gives Nominatim the best context
                    addr_part = f"{postcode} {locality}".strip() if (postcode or locality) else ""
                    if street and addr_part:
                        geo_query = f"{street}, {addr_part}"
                    elif venue and addr_part:
                        geo_query = f"{venue}, {addr_part}"
                    else:
                        geo_query = addr_part or venue
                else:
                    location = venue or (str(addr) if addr else "")
                    addr_part = ""
            else:
                location = str(loc_obj) if loc_obj else ""
                addr_part = ""
            if not geo_query:
                geo_query = location
            parsed = parse_french_date(start)
            if title and parsed and parsed >= date.today():
                uid = make_uid(title, str(parsed), location)
                ev = {
                    "title": title,
                    "date_parsed": str(parsed),
                    "location": location,
                    "description": item.get("description", ""),
                    "url": item.get("url", base_url),
                    "uid": uid,
                    "source": source_name,
                    "ev_type": _classify_event(title),
                }
                if geo_query != location:
                    ev["geo_query"] = geo_query
                # Fallback: just postcode+locality (drops confusing venue name that
                # can mislead Nominatim). Only stored when different from geo_query.
                if addr_part and addr_part != geo_query and addr_part != location:
                    ev["geo_query_fallback"] = addr_part
                # Store the canonical commune name for the ICS title.
                # Nominatim can return an administrative center instead of the
                # actual commune; addressLocality from the structured JSON-LD is
                # the authoritative name and is preferred in _build_summary.
                if locality:
                    ev["city"] = locality
                events.append(ev)
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
                    "ev_type": _classify_event(title),
                })
        if events:
            break
    return events


MAX_PAGES = 8  # safety cap per source


def _find_next_page(soup: BeautifulSoup, current_url: str, base_domain: str) -> str | None:
    """Return the absolute URL of the next page, or None if there isn't one."""
    candidates = []

    # <link rel="next"> (cleanest, RFC 5988)
    tag = soup.find("link", rel="next")
    if tag and tag.get("href"):
        candidates.append(tag["href"])

    # <a rel="next">
    tag = soup.find("a", rel="next")
    if tag and tag.get("href"):
        candidates.append(tag["href"])

    # Common CSS selectors for "next page" links
    for sel in [
        "a.next", "a.suivant", "a[aria-label='Suivant']", "a[aria-label='Next']",
        ".pagination .next a", ".pagination li.active + li a",
        "nav[aria-label*='ination'] a[rel='next']",
        ".pager__item--next a", ".pager-next a",
    ]:
        tag = soup.select_one(sel)
        if tag and tag.get("href"):
            candidates.append(tag["href"])
            break

    for href in candidates:
        href = href.strip()
        if not href or href == "#":
            continue
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return base_domain + href
        # relative URL — join with current page
        return urljoin(current_url, href)

    return None


async def _scrape_source(
    client: httpx.AsyncClient, url: str, source_name: str, base_domain: str,
) -> tuple:
    """Fetch and parse one source, following pagination up to MAX_PAGES.

    Retries up to 3 times on network errors for the first page only;
    HTTP errors (403, 500…) are not retried.
    """
    start = time.monotonic()
    result: dict = {"count": 0, "strategy": None, "error": None, "duration_s": 0.0, "url": url}
    all_events: list = []
    current_url = url
    seen_urls: set = {url}
    seen_event_uids: set = set()  # intra-source dedup + loop-back detection

    for page_num in range(MAX_PAGES):
        # Fetch with retry only on the first page
        resp = None
        for attempt in range(3 if page_num == 0 else 1):
            try:
                resp = await client.get(current_url, timeout=30)
                resp.raise_for_status()
                break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == 2 or page_num > 0:
                    if page_num == 0:
                        result["error"] = f"Network error: {type(exc).__name__}"
                        result["duration_s"] = round(time.monotonic() - start, 2)
                        logger.warning("Could not fetch %s after 3 attempts: %s", url, exc)
                        return [], result
                    resp = None
                    break
                await asyncio.sleep(2 ** attempt)
            except httpx.HTTPStatusError as exc:
                if page_num == 0:
                    result["error"] = f"HTTP {exc.response.status_code}"
                    result["duration_s"] = round(time.monotonic() - start, 2)
                    logger.warning("HTTP error from %s: %s", url, exc)
                    return [], result
                resp = None
                break
            except Exception as exc:
                if page_num == 0:
                    result["error"] = str(exc)[:200]
                    result["duration_s"] = round(time.monotonic() - start, 2)
                    logger.warning("Could not fetch %s: %s", url, exc)
                    return [], result
                resp = None
                break

        if resp is None:
            logger.warning(
                "%-25s pagination stopped at page %d — %d event(s) collected so far",
                source_name,
                page_num + 1,
                len(all_events),
            )
            break

        soup = BeautifulSoup(resp.text, "lxml")

        # Choose strategy: JSON-LD preferred, CSS cards as fallback (locked after page 1)
        if page_num == 0:
            events = _parse_jsonld(soup, current_url, source_name)
            if events:
                result["strategy"] = "json-ld"
            else:
                events = _parse_cards(soup, current_url, source_name, base_domain)
                if events:
                    result["strategy"] = "css"
        elif result["strategy"] == "json-ld":
            events = _parse_jsonld(soup, current_url, source_name)
        else:
            events = _parse_cards(soup, current_url, source_name, base_domain)

        # Only keep events not already collected (loop-back detection)
        new_events = [ev for ev in events if ev.get("uid") not in seen_event_uids]
        seen_event_uids.update(ev["uid"] for ev in new_events if ev.get("uid"))
        all_events.extend(new_events)
        logger.debug(
            "%-25s page %d → %d events (%d new)",
            source_name, page_num + 1, len(events), len(new_events),
        )

        next_url = _find_next_page(soup, current_url, base_domain)

        # Speculative ?p=N for sites without <link rel="next"> (brocabrac.fr uses ?p=)
        if next_url is None and new_events:
            parsed_url = urlparse(current_url)
            qs = parse_qs(parsed_url.query, keep_blank_values=True)
            current_page = int((qs.get("p") or qs.get("page") or ["1"])[0])
            qs.pop("page", None)  # normalise: always use ?p=
            qs["p"] = [str(current_page + 1)]
            next_url = urlunparse(parsed_url._replace(
                query=urlencode({k: v[0] for k, v in qs.items()})
            ))
            logger.debug("%-25s no HTML pagination — speculative: %s", source_name, next_url)

        if not next_url or next_url in seen_urls or not new_events:
            break
        seen_urls.add(next_url)
        current_url = next_url

    result["count"] = len(all_events)
    result["duration_s"] = round(time.monotonic() - start, 2)
    logger.info(
        "%-25s → %d events (%d page(s), strategy: %s)",
        source_name, len(all_events), len(seen_urls), result["strategy"],
    )
    return all_events, result


# ──────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────
async def scrape_all(lat: float, lng: float, radius_km: int, city: str = "") -> list:
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

    # Load geocache early — shared by city geocoding and event geocoding below.
    geocache = _load_geocache()

    # ── brocabrac.fr URL ──────────────────────────────────────────────────────
    # The reliable URL format is /{dept}/{commune-slug}/ (e.g. /78/saint-arnoult-en-yvelines/).
    # To get the dept code we need the postal code, which comes from geocoding the city.
    # vide-greniers.org uses explicit lat=/lng= → coordinates are fine there.
    brocabrac_city_raw = city.split(",")[0].strip() if city else ""
    city_key = brocabrac_city_raw.strip().lower()

    if city_key and city_key not in geocache:
        # Geocode the config city once to get its postal code / dept.
        await _geocode_batch([brocabrac_city_raw], geocache)
        _save_geocache(geocache)

    city_geo = geocache.get(city_key) if city_key else None
    dept = _dept_from_postcode((city_geo or {}).get("postcode", ""))
    city_slug = _slugify(brocabrac_city_raw)

    if dept and city_slug:
        brocabrac_url = f"https://brocabrac.fr/{dept}/{city_slug}/?rayon={radius_km}"
    elif brocabrac_city_raw:
        brocabrac_url = (
            f"https://brocabrac.fr/brocantes-vide-greniers"
            f"?localisation={url_quote(brocabrac_city_raw)}&rayon={radius_km}"
        )
    else:
        brocabrac_url = (
            f"https://brocabrac.fr/brocantes-vide-greniers"
            f"?localisation={lat},{lng}&rayon={radius_km}"
        )

    logger.info("brocabrac.fr URL: %s", brocabrac_url)

    sources = [
        (brocabrac_url, "brocabrac.fr", "https://brocabrac.fr"),
        (
            f"https://vide-greniers.org/recherche?lat={lat}&lng={lng}&distance={radius_km}",
            "vide-greniers.org",
            "https://vide-greniers.org",
        ),
    ]

    # ── Scrape (parallel) ────────────────────────────────────────────────────
    all_events: list = []

    async def _do_scrape(url: str, name: str, domain: str):
        src_events, src_result = await _scrape_source(client, url, name, domain)
        src_result["last_run"] = datetime.now().isoformat()
        return name, src_events, src_result

    # brocabrac.fr reads the search radius from the BROCA_DISTANCE cookie.
    # Use domain-restricted cookies so the cookie is only sent to brocabrac.fr.
    jar = httpx.Cookies()
    jar.set("BROCA_DISTANCE", str(radius_km), domain="brocabrac.fr")

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, cookies=jar) as client:
        gathered = await asyncio.gather(*[
            _do_scrape(url, name, domain) for url, name, domain in sources
        ])
        for name, src_events, src_result in gathered:
            _last_scrape_results[name] = src_result
            all_events.extend(src_events)

    # ── Deduplicate + sort ────────────────────────────────────────────────────
    seen: dict = {}
    for ev in all_events:
        uid = ev.get("uid", "")
        if uid and uid not in seen:
            seen[uid] = ev
    unique_events = sorted(seen.values(), key=lambda e: e.get("date_parsed", "9999-12-31"))

    # ── Geocode event locations — pass 1 : full address (venue + postcode + city) ──
    unique_queries = list({
        ev.get("geo_query") or ev.get("location", "")
        for ev in unique_events
        if ev.get("geo_query") or ev.get("location")
    })
    await _geocode_batch(unique_queries, geocache)

    def _assign_geo(ev: dict) -> None:
        key = (ev.get("geo_query") or ev.get("location", "")).strip().lower()
        if key and geocache.get(key):
            ev["geo"] = geocache[key]

    for ev in unique_events:
        _assign_geo(ev)

    # ── Geocode — pass 2 : postcode+city only (venue name can confuse Nominatim) ──
    fallback_queries = list({
        ev["geo_query_fallback"]
        for ev in unique_events
        if "geo" not in ev and ev.get("geo_query_fallback")
    })
    if fallback_queries:
        await _geocode_batch(fallback_queries, geocache)
        for ev in unique_events:
            if "geo" not in ev and ev.get("geo_query_fallback"):
                fb_key = ev["geo_query_fallback"].strip().lower()
                if geocache.get(fb_key):
                    ev["geo"] = geocache[fb_key]

    _save_geocache(geocache)

    # ── Distance filter ───────────────────────────────────────────────────────
    # Keep events geocoded within radius × 1.15. Events without geo coords
    # are kept as-is — both sources already filter by distance server-side,
    # so we trust their results rather than silently dropping events whose
    # venue name Nominatim couldn't resolve.
    filtered = []
    for ev in unique_events:
        geo = ev.get("geo")
        if geo:
            d = _haversine_km(lat, lng, geo["lat"], geo["lng"])
            if d <= radius_km * 1.15:
                filtered.append(ev)
            else:
                logger.debug(
                    "Distance filter: dropped '%s' geocoded %.0f km away",
                    ev.get("title", "?"), d,
                )
        else:
            filtered.append(ev)  # no geo → trust source's own geo filter

    dropped = len(unique_events) - len(filtered)
    if dropped:
        logger.info("Distance filter: dropped %d event(s) confirmed outside radius", dropped)

    return filtered
