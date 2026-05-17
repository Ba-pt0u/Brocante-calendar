import hashlib
import logging
import re
from datetime import date, timedelta

from icalendar import Alarm, Calendar, Event, vText

logger = logging.getLogger(__name__)

# ── Type → emoji + label ─────────────────────────────────────────────────────
# Set A chosen by user:  🛍️ brocante · 📦 vide-grenier · 🏷️ braderie
#                        👗 bourse · 🐾 marché-puces · 📅 autre
_TYPE_EMOJIS = {
    "brocante":    "🛍️",
    "vide-grenier": "📦",
    "braderie":    "🏷️",
    "bourse":      "👗",
    "marche-puces": "🐾",
    "autre":       "📅",
}

_TYPE_LABELS = {
    "brocante":    "Brocante",
    "vide-grenier": "Vide-grenier",
    "braderie":    "Braderie",
    "bourse":      "Bourse",
    "marche-puces": "Marché aux puces",
    # "autre" intentionally omitted → no label printed
}

# ── Contextual emoji detection (from description / title) ────────────────────
# 🍕 food/refreshments  🎪 animations/entertainment
_FOOD_RE  = re.compile(
    r"buvette|restaur|snack|repas|frites|crêpes?|crepes?|nourriture|manger|café|guinguette",
    re.IGNORECASE,
)
_ANIM_RE  = re.compile(
    r"animation|spectacle|musique|manège|manege|concert|artiste|cirque|jeux",
    re.IGNORECASE,
)


def _context_emojis(description: str, title: str = "") -> str:
    """Return trailing emoji string based on keywords in description + title."""
    text = f"{description} {title}"
    result = ""
    if _FOOD_RE.search(text):
        result += "🍕"
    if _ANIM_RE.search(text):
        result += "🎪"
    return result


def _build_summary(ev: dict) -> str:
    """
    Build the iCal SUMMARY field.

    Format:  {Ville} — {emoji} {Type label} — {Titre original} {ctx_emojis}
    Example: Saint-Arnoult — 🛍️ Brocante — Grande Brocante annuelle 🍕

    The type label is suppressed when the title already starts with it
    (e.g. "Vide-grenier de Breuillet" → no redundant "Vide-grenier —" prefix).
    Falls back gracefully when city or type label are missing.
    """
    title = (ev.get("title") or "Événement").strip()

    ev_type  = ev.get("ev_type") or "autre"
    emoji    = _TYPE_EMOJIS.get(ev_type, "📅")
    label    = _TYPE_LABELS.get(ev_type, "")

    # Suppress label when title already starts with the same type word(s)
    if label:
        norm = lambda s: re.sub(r"[-\s]+", " ", s).lower()
        if norm(title).startswith(norm(label)):
            label = ""

    geo  = ev.get("geo") or {}
    # Priority: JSON-LD addressLocality (ev["city"]) > Nominatim result (geo["city"])
    # > best-effort extraction from the location string.
    # Nominatim can return an administrative center (e.g. "Rambouillet") instead of
    # the actual commune ("Breuillet"), so structured JSON-LD data is preferred.
    city = (
        ev.get("city")
        or geo.get("city")
        or _extract_city(ev.get("location", ""))
    ).strip()

    ctx = _context_emojis(ev.get("description", ""), title)

    if city and label:
        core = f"{city} — {emoji} {label} — {title}"
    elif city:
        core = f"{city} — {emoji} {title}"
    elif label:
        core = f"{emoji} {label} — {title}"
    else:
        core = f"{emoji} {title}"

    return f"{core} {ctx}".rstrip() if ctx else core


# Words that indicate a venue/address rather than a city name.
# Used to avoid displaying "Salle des fêtes" or "Stade municipal" as a city.
_VENUE_PREFIX = re.compile(
    r"^(salle|stade|terrain|gymnase|espace|école|ecole|eglise|église|mairie|"
    r"boulodrome|complexe|centre|foyer|bois|parc|manège|manege|domaine|"
    r"ferme|château|chateau|champ|propriété|propriete|parking|"
    r"rue\b|avenue|boulevard|allée|allee|chemin|route\b|place\b|impasse|"
    r"esplanade|parvis|promenade|quai|cours\b|lieu.dit|lieudit)",
    re.IGNORECASE,
)


def _extract_city(location: str) -> str:
    """Best-effort city extraction from a raw location string.

    Iterates comma-separated parts in reverse (last part is usually the city).
    Parts that match venue/address keywords are skipped.
    """
    if not location:
        return ""
    parts = [p.strip() for p in location.split(",")]
    for part in reversed(parts):
        city = re.sub(r"^\d{4,5}\s*", "", part).strip()
        if 2 <= len(city) < 50 and not _VENUE_PREFIX.match(city):
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


def _add_alarm(vevent: Event, summary: str, event_date: date) -> None:
    """Smart reminder: Friday 18 h for weekend events, noon the day before otherwise."""
    weekday = event_date.weekday()
    if weekday in (5, 6):
        trigger = timedelta(hours=-6)
    else:
        trigger = timedelta(hours=-12)

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", f"Demain → {summary}")
    alarm.add("trigger", trigger)
    vevent.add_component(alarm)


def generate_ics(events: list, config: dict) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Brocantes App//brocantes-calendar//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    city = config.get("city", "Ma ville")
    types_filter = config.get("types")
    if types_filter:
        labels = [_TYPE_LABELS.get(t, t.capitalize()) for t in types_filter]
        cal_name = f"🛍️ {' · '.join(labels)} – {city}"
    else:
        cal_name = f"🛍️ Brocantes – {city}"
    cal.add("x-wr-calname", cal_name)
    cal.add("x-wr-timezone", "Europe/Paris")
    cal.add("x-published-ttl", "PT12H")
    cal.add("refresh-interval", timedelta(hours=1))
    cal.add("x-apple-calendar-color", "#B8481C")

    for ev in events:
        vevent = Event()
        vevent.add("status", "CONFIRMED")

        # ── SUMMARY ─────────────────────────────────────────────────────────
        summary = _build_summary(ev)
        vevent.add("summary", summary)

        # ── Dates (all-day) ─────────────────────────────────────────────────
        raw_date = ev.get("date_parsed")
        event_date = None
        if raw_date:
            try:
                event_date = date.fromisoformat(raw_date)
                vevent.add("dtstart", event_date)
                vevent.add("dtend", event_date)
            except ValueError:
                logger.warning(
                    "Malformed date for event %r: %r — skipping date fields",
                    ev.get("title", "?"),
                    raw_date,
                )

        # ── Location ────────────────────────────────────────────────────────
        location = ev.get("location", "")
        if location:
            vevent.add("location", vText(location))

        # ── GEO + X-APPLE-STRUCTURED-LOCATION → map card in iOS ────────────
        geo = ev.get("geo") or {}
        if geo.get("lat") and geo.get("lng"):
            lat, lng = geo["lat"], geo["lng"]
            vevent.add("geo", (lat, lng))

            geo_uri = vText(f"geo:{lat},{lng}")
            geo_uri.params["VALUE"]          = "URI"
            geo_uri.params["X-ADDRESS"]      = location
            geo_uri.params["X-APPLE-RADIUS"] = "500"
            geo_uri.params["X-TITLE"]        = location
            vevent["x-apple-structured-location"] = geo_uri

        # ── URL ─────────────────────────────────────────────────────────────
        source_url = ev.get("url", "")
        if source_url:
            vevent.add("url", source_url)

        # ── Description ─────────────────────────────────────────────────────
        vevent.add("description", _build_description(ev))

        # ── Smart VALARM ────────────────────────────────────────────────────
        if event_date:
            _add_alarm(vevent, summary, event_date)

        # ── UID ─────────────────────────────────────────────────────────────
        uid_base = ev.get("uid") or hashlib.md5((ev.get("title", "")).encode()).hexdigest()
        vevent.add("uid", f"{uid_base}@brocantes-app.local")

        cal.add_component(vevent)

    return cal.to_ical()
