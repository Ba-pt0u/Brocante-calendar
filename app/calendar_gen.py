import hashlib
import logging
import re
from datetime import date, timedelta

from icalendar import Alarm, Calendar, Event, vText

logger = logging.getLogger(__name__)

# ── Type → emoji + label ─────────────────────────────────────────────────────
# Types mirror the brocabrac.fr search-what selector.
_TYPE_EMOJIS = {
    "brocante":          "🛍️",
    "vide-grenier":      "📦",
    "vide-dressing":     "👗",
    "vide-maison":       "🏠",
    "bourse":            "👛",
    "bourse-livres":     "📚",
    "bourse-collection": "🏆",
    "bourse-jouets":     "🧸",
    "bourse-vetements":  "🧥",
    "braderie":          "🏷️",
    "marche-livres":     "📖",
    "marche-noel":       "🎄",
    "marche-puces":      "🐾",
    "autre":             "📅",
}

_TYPE_LABELS = {
    "brocante":          "Brocante",
    "vide-grenier":      "Vide-grenier",
    "vide-dressing":     "Vide-Dressing",
    "vide-maison":       "Vide-Maison",
    "bourse":            "Bourse",
    "bourse-livres":     "Bourse aux livres",
    "bourse-collection": "Bourse de collection",
    "bourse-jouets":     "Bourse aux jouets",
    "bourse-vetements":  "Bourse aux vêtements",
    "braderie":          "Braderie",
    "marche-livres":     "Marché aux livres",
    "marche-noel":       "Marché de Noël",
    "marche-puces":      "Marché aux puces",
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


def _is_starred(ev: dict, keywords: list) -> bool:
    """Case-insensitive substring match on title, location, and city."""
    if not keywords:
        return False
    haystack = " ".join([
        ev.get("title") or "",
        ev.get("location") or "",
        ev.get("city") or "",
    ]).lower()
    return any(kw.lower() in haystack for kw in keywords if kw)


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

    geo = ev.get("geo") or {}
    raw_location = ev.get("location", "")
    extracted = _extract_city(raw_location)

    # A single-word extracted city (no spaces) means brocabrac.fr used the commune
    # name itself as the venue (e.g. "Orcemont").  This is more reliable than
    # addressLocality, which brocabrac.fr sometimes sets to the canton centre
    # ("Rambouillet") or even a street name ("Rue d'Arras").
    single_word_city = extracted if (extracted and " " not in extracted) else ""

    # Validate ev["city"] from JSON-LD addressLocality: discard if it looks like a
    # street or venue (e.g. "Rue d'Arras", "Avenue de la Gare").
    ev_city = ev.get("city") or ""
    if ev_city and _VENUE_PREFIX.match(ev_city):
        ev_city = ""

    city = (
        single_word_city    # commune name used as venue ("Orcemont") — most reliable
        or ev_city          # validated addressLocality ("Lyon", "Breuillet"…)
        or geo.get("city")  # Nominatim geocoding result
        or extracted        # multi-word fallback ("Caluire-et-Cuire, …")
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


_SIZE_DOTS = {
    "Moins de 50":  "•",
    "De 50 à 100":  "••",
    "De 100 à 200": "•••",
    "De 200 à 300": "••••",
    "Plus de 300":  "•••••",
}


def _build_description(ev: dict) -> str:
    """Structured multi-section description readable in iOS Calendar."""
    parts = []
    if ev.get("location"):
        parts.append(f"📍 {ev['location']}")
    size = ev.get("size_label", "")
    if size:
        dots = _SIZE_DOTS.get(size, "")
        parts.append(f"👥 {size} exposants{f'  {dots}' if dots else ''}")
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

    keywords = config.get("watch_keywords", [])

    for ev in events:
        vevent = Event()
        vevent.add("status", "CONFIRMED")

        starred = _is_starred(ev, keywords)

        # ── SUMMARY ─────────────────────────────────────────────────────────
        base_summary = _build_summary(ev)
        summary = f"⭐ {base_summary}" if starred else base_summary
        vevent.add("summary", summary)

        if starred:
            vevent.add("priority", 1)

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
            if starred:
                early = Alarm()
                early.add("action", "DISPLAY")
                early.add("trigger", timedelta(days=-21))
                early.add("description", f"⭐ Dans 3 semaines → {base_summary}")
                vevent.add_component(early)

        # ── UID ─────────────────────────────────────────────────────────────
        uid_base = ev.get("uid") or hashlib.md5((ev.get("title", "")).encode()).hexdigest()
        vevent.add("uid", f"{uid_base}@brocantes-app.local")

        cal.add_component(vevent)

    return cal.to_ical()
