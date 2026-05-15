import hashlib
import json
import logging
import re
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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

# CSS selectors tried in order; first match wins
CARD_SELECTORS = [
    "article.card",
    ".event-card",
    "[data-event]",
    ".event-item",
    ".brocante-item",
    ".vide-grenier-item",
    "article.event",
    ".listing-item",
    ".search-result",
    ".result-item",
    "li.event",
    ".annonce",
    ".card",
    "article",
]

TITLE_SELECTORS = [
    ".title", ".card-title", ".event-title", ".name", "h2", "h3", "h4", "h5"
]
DATE_SELECTORS = [
    ".date", ".event-date", ".date-str", ".datetime", "time", "[datetime]"
]
LOCATION_SELECTORS = [
    ".location", ".lieu", ".city", ".address", ".place",
    ".localisation", ".ville", ".commune"
]
DESC_SELECTORS = [".description", ".excerpt", ".summary", ".intro", "p"]


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


def parse_french_date(raw: str) -> Optional[date]:
    """Parse multiple French date formats into a date object."""
    if not raw:
        return None
    s = raw.strip().lower()
    # strip day names
    for day in FRENCH_DAY_NAMES:
        s = s.replace(day, "")
    s = s.strip()

    # ISO: YYYY-MM-DD (also handles datetime strings)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # DD month YYYY  (e.g. "15 juin 2025")
    m = re.search(r"(\d{1,2})\s+([a-zéûùàâêîôèë]+)\.?\s+(\d{4})", s)
    if m:
        month_num = FRENCH_MONTHS.get(m.group(2).rstrip("."))
        if month_num:
            try:
                return date(int(m.group(3)), month_num, int(m.group(1)))
            except ValueError:
                pass

    # DD month (current / next year implied)
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


def make_uid(title: str, event_date: str, location: str) -> str:
    key = f"{title.lower().strip()}|{event_date}|{location.lower().strip()}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _parse_jsonld(soup: BeautifulSoup, base_url: str, source_name: str) -> list:
    events = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "Event":
                continue
            title = item.get("name", "").strip()
            start = item.get("startDate", "")
            loc_obj = item.get("location", {})
            if isinstance(loc_obj, dict):
                location = (
                    loc_obj.get("name", "")
                    or loc_obj.get("address", {}).get("addressLocality", "")
                    if isinstance(loc_obj.get("address"), dict)
                    else loc_obj.get("address", "")
                )
            else:
                location = str(loc_obj)
            description = item.get("description", "")
            url = item.get("url", base_url)
            parsed = parse_french_date(start)
            if title and parsed and parsed >= date.today():
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
    client: httpx.AsyncClient,
    url: str,
    source_name: str,
    base_domain: str,
) -> list:
    try:
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Could not fetch %s: %s", url, exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    events = _parse_jsonld(soup, url, source_name)
    if not events:
        events = _parse_cards(soup, url, source_name, base_domain)

    logger.info("%-25s → %d events", source_name, len(events))
    return events


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
            events = await _scrape_source(client, url, name, domain)
            all_events.extend(events)

    # Deduplicate by UID (keep first occurrence)
    seen: dict = {}
    for ev in all_events:
        uid = ev.get("uid", "")
        if uid and uid not in seen:
            seen[uid] = ev

    # Sort by date ascending
    return sorted(seen.values(), key=lambda e: e.get("date_parsed", "9999-12-31"))
