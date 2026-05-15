import hashlib
from datetime import date

from icalendar import Calendar, Event, vText


def _event_emoji(title: str) -> str:
    t = title.lower()
    if "vide-grenier" in t or "vide grenier" in t:
        return "🏷️"
    if "brocante" in t:
        return "🛍️"
    return "📦"


def generate_ics(events: list, config: dict) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Brocantes App//brocantes-calendar//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"Brocantes – {config.get('city', 'Ma ville')}")
    cal.add("x-wr-timezone", "Europe/Paris")
    cal.add("x-published-ttl", "PT12H")

    for ev in events:
        vevent = Event()

        title = ev.get("title", "Événement")
        emoji = _event_emoji(title)
        vevent.add("summary", f"{emoji} {title}")

        raw_date = ev.get("date_parsed")
        if raw_date:
            try:
                d = date.fromisoformat(raw_date)
                vevent.add("dtstart", d)
                vevent.add("dtend", d)
            except ValueError:
                pass

        location = ev.get("location", "")
        if location:
            vevent.add("location", vText(location))

        desc_parts = []
        if ev.get("description"):
            desc_parts.append(ev["description"])
        if ev.get("url"):
            desc_parts.append(f"Source: {ev['url']}")
        if ev.get("source"):
            desc_parts.append(f"Via: {ev['source']}")
        vevent.add("description", "\n\n".join(desc_parts))

        uid_base = ev.get("uid") or hashlib.md5(title.encode()).hexdigest()
        vevent.add("uid", f"{uid_base}@brocantes-app.local")

        cal.add_component(vevent)

    return cal.to_ical()
