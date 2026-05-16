import hashlib
import re
from datetime import date, timedelta

from icalendar import Alarm, Calendar, Event, vText


def _event_emoji(title: str) -> str:
    t = title.lower()
    if "vide-grenier" in t or "vide grenier" in t:
        return "🏷️"
    if "brocante" in t:
        return "🛍️"
    return "📦"


def _extract_city(location: str) -> str:
    """Best-effort city extraction from a raw location string."""
    if not location:
        return ""
    parts = [p.strip() for p in location.split(",")]
    for part in reversed(parts):
        city = re.sub(r"^\d{4,5}\s*", "", part).strip()
        if 2 <= len(city) < 50:
            return city
    return ""


def _build_description(ev: dict) -> str:
    """Structured multi-section description readable in iOS Calendar."""
    parts = []
    if ev.get("location"):
        parts.append(f"📍 {ev['location']}")
    if ev.get("description"):
        parts.append(ev["description"])
    if ev.get("source"):
        parts.append(f"Via : {ev['source']}")
    if ev.get("url"):
        parts.append(f"🔗 {ev['url']}")
    return "\n\n".join(filter(None, parts))


def _add_alarm(vevent: Event, title: str, event_date: date) -> None:
    """Smart reminder: Friday 18 h for weekend events, noon the day before otherwise."""
    # weekday(): Monday=0 … Saturday=5, Sunday=6
    weekday = event_date.weekday()
    if weekday in (5, 6):
        # All-day DTSTART is midnight → -6 h = 18:00 the evening before
        trigger = timedelta(hours=-6)
        msg = f"Brocante demain 🛍️ {title}"
    else:
        trigger = timedelta(hours=-12)
        msg = f"Brocante demain 🛍️ {title}"

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", msg)
    alarm.add("trigger", trigger)
    vevent.add_component(alarm)


def generate_ics(events: list, config: dict) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Brocantes App//brocantes-calendar//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"🛍️ Brocantes – {config.get('city', 'Ma ville')}")
    cal.add("x-wr-timezone", "Europe/Paris")
    cal.add("x-published-ttl", "PT12H")
    # RFC 7986: hint to clients how often to re-fetch the feed
    cal.add("refresh-interval", timedelta(hours=1))
    # Brand the subscribed calendar in iOS with a warm rust colour
    cal.add("x-apple-calendar-color", "#B8481C")

    for ev in events:
        vevent = Event()
        vevent.add("status", "CONFIRMED")

        # ── SUMMARY: emoji + title + short city ──────────────────────────
        title = ev.get("title", "Événement")
        emoji = _event_emoji(title)
        geo   = ev.get("geo") or {}
        city  = geo.get("city") or _extract_city(ev.get("location", ""))
        summary = f"{emoji} {title} • {city}" if city else f"{emoji} {title}"
        vevent.add("summary", summary)

        # ── Dates (all-day) ──────────────────────────────────────────────
        raw_date = ev.get("date_parsed")
        event_date = None
        if raw_date:
            try:
                event_date = date.fromisoformat(raw_date)
                vevent.add("dtstart", event_date)
                vevent.add("dtend", event_date)
            except ValueError:
                pass

        # ── Location ─────────────────────────────────────────────────────
        location = ev.get("location", "")
        if location:
            vevent.add("location", vText(location))

        # ── GEO + X-APPLE-STRUCTURED-LOCATION → map card in iOS ─────────
        if geo.get("lat") and geo.get("lng"):
            lat, lng = geo["lat"], geo["lng"]

            # Standard GEO property (lat;lng)
            vevent.add("geo", (lat, lng))

            # Apple-specific rich location (shows inline map + Directions button)
            geo_uri = vText(f"geo:{lat},{lng}")
            geo_uri.params["VALUE"]          = "URI"
            geo_uri.params["X-ADDRESS"]      = location
            geo_uri.params["X-APPLE-RADIUS"] = "500"
            geo_uri.params["X-TITLE"]        = location
            vevent["x-apple-structured-location"] = geo_uri

        # ── URL (shown as tappable "Page web" link in iOS) ───────────────
        source_url = ev.get("url", "")
        if source_url:
            vevent.add("url", source_url)

        # ── Description ──────────────────────────────────────────────────
        vevent.add("description", _build_description(ev))

        # ── Smart VALARM ─────────────────────────────────────────────────
        if event_date:
            _add_alarm(vevent, title, event_date)

        # ── UID ──────────────────────────────────────────────────────────
        uid_base = ev.get("uid") or hashlib.md5(title.encode()).hexdigest()
        vevent.add("uid", f"{uid_base}@brocantes-app.local")

        cal.add_component(vevent)

    return cal.to_ical()
