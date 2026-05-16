"""
Unit tests for calendar_gen.py.

Each ICS feature (emoji, alarm timing, GEO, city in SUMMARY, …) is tested
independently. The full generate_ics() output is also parsed back with
icalendar to catch silent serialisation bugs.
"""
import pytest
from datetime import date, timedelta

from icalendar import Calendar

from app.calendar_gen import (
    _build_summary,
    _context_emojis,
    _extract_city,
    _build_description,
    _add_alarm,
    generate_ics,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ics(raw: bytes) -> Calendar:
    return Calendar.from_ical(raw)

def _vevents(cal: Calendar) -> list:
    return [c for c in cal.walk() if c.name == "VEVENT"]

def _valarms(vevent) -> list:
    return [c for c in vevent.walk() if c.name == "VALARM"]

BASE_CONFIG = {"city": "Lyon", "lat": 45.764, "lng": 4.836, "radius_km": 30}
BASE_EVENT = {
    "title": "Grande Brocante de Lyon",
    "date_parsed": "2026-07-15",  # Wednesday
    "location": "Place Bellecour, Lyon",
    "description": "Brocante mensuelle",
    "url": "https://brocabrac.fr/event/123",
    "source": "brocabrac.fr",
    "uid": "abc123",
    "ev_type": "brocante",
}


# ── _context_emojis ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestContextEmojis:
    def test_food_keyword_buvette(self):
        assert "🍕" in _context_emojis("buvette sur place")

    def test_food_keyword_restaur(self):
        assert "🍕" in _context_emojis("restauration possible")

    def test_food_keyword_frites(self):
        assert "🍕" in _context_emojis("frites et merguez")

    def test_anim_keyword_animation(self):
        assert "🎪" in _context_emojis("animations pour les enfants")

    def test_anim_keyword_spectacle(self):
        assert "🎪" in _context_emojis("spectacle gratuit")

    def test_anim_keyword_in_title(self):
        assert "🎪" in _context_emojis("", "Concert et brocante")

    def test_both_emojis_when_both_keywords(self):
        result = _context_emojis("buvette et animation")
        assert "🍕" in result
        assert "🎪" in result

    def test_no_keywords_returns_empty(self):
        assert _context_emojis("Vente de meubles anciens") == ""

    def test_empty_inputs_return_empty(self):
        assert _context_emojis("", "") == ""


# ── _build_summary ─────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestBuildSummary:
    def test_brocante_type_emoji(self):
        ev = {**BASE_EVENT, "ev_type": "brocante"}
        assert "🛍️" in _build_summary(ev)

    def test_vide_grenier_type_emoji(self):
        ev = {**BASE_EVENT, "ev_type": "vide-grenier"}
        assert "📦" in _build_summary(ev)

    def test_braderie_type_emoji(self):
        ev = {**BASE_EVENT, "ev_type": "braderie"}
        assert "🏷️" in _build_summary(ev)

    def test_unknown_type_falls_back_to_agenda_emoji(self):
        ev = {**BASE_EVENT, "ev_type": "autre"}
        assert "📅" in _build_summary(ev)

    def test_type_label_present_for_brocante(self):
        ev = {**BASE_EVENT, "ev_type": "brocante"}
        assert "Brocante" in _build_summary(ev)

    def test_type_label_absent_for_autre(self):
        ev = {**BASE_EVENT, "ev_type": "autre"}
        summary = _build_summary(ev)
        assert "Autre" not in summary

    def test_city_from_geo(self):
        ev = {**BASE_EVENT, "geo": {"lat": 45.76, "lng": 4.83, "city": "Lyon"}}
        assert "Lyon" in _build_summary(ev)

    def test_city_from_location_fallback(self):
        ev = {**BASE_EVENT, "location": "Place du Marché, Villeurbanne"}
        assert "Villeurbanne" in _build_summary(ev)

    def test_title_present_in_summary(self):
        assert "Grande Brocante de Lyon" in _build_summary(BASE_EVENT)

    def test_full_format_with_label_and_city(self):
        ev = {**BASE_EVENT, "ev_type": "brocante", "geo": {"lat": 45.76, "lng": 4.83, "city": "Lyon"}}
        summary = _build_summary(ev)
        assert "🛍️" in summary
        assert "Brocante" in summary
        assert "Lyon" in summary
        assert "Grande Brocante de Lyon" in summary

    def test_city_comes_before_type(self):
        ev = {**BASE_EVENT, "ev_type": "brocante", "geo": {"lat": 45.76, "lng": 4.83, "city": "Lyon"}}
        summary = _build_summary(ev)
        assert summary.index("Lyon") < summary.index("Brocante")

    def test_type_comes_before_original_title(self):
        ev = {**BASE_EVENT, "ev_type": "brocante", "geo": {"lat": 45.76, "lng": 4.83, "city": "Lyon"}}
        summary = _build_summary(ev)
        assert summary.index("Brocante") < summary.index("Grande Brocante de Lyon")

    def test_context_emoji_appended_when_food(self):
        ev = {**BASE_EVENT, "description": "buvette sur place"}
        assert "🍕" in _build_summary(ev)

    def test_context_emoji_not_present_when_no_match(self):
        ev = {**BASE_EVENT, "description": "Vente de livres anciens"}
        summary = _build_summary(ev)
        assert "🍕" not in summary
        assert "🎪" not in summary

    def test_missing_title_falls_back_to_default(self):
        ev = {k: v for k, v in BASE_EVENT.items() if k != "title"}
        summary = _build_summary(ev)
        assert "Événement" in summary


# ── _extract_city ──────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestExtractCity:
    def test_city_after_comma(self):
        assert _extract_city("Place Bellecour, Lyon") == "Lyon"

    def test_city_with_postal_code(self):
        assert _extract_city("3 rue de la Paix, 69001 Lyon") == "Lyon"

    def test_multi_part_location(self):
        city = _extract_city("Salle des fêtes, Place du Général, Caluire-et-Cuire")
        assert city == "Caluire-et-Cuire"

    def test_single_word_location(self):
        # No comma → return the location itself (≤ 40 chars)
        assert _extract_city("Lyon") == "Lyon"

    def test_empty_string(self):
        assert _extract_city("") == ""

    def test_postal_code_only_stripped(self):
        result = _extract_city("69001 Lyon")
        assert result == "Lyon"


# ── _build_description ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestBuildDescription:
    def test_all_sections_present(self):
        ev = {
            "location": "Place Bellecour, Lyon",
            "description": "Brocante mensuelle",
            "source": "brocabrac.fr",
            "url": "https://brocabrac.fr/event/123",
        }
        desc = _build_description(ev)
        assert "📍 Place Bellecour" in desc
        assert "Brocante mensuelle" in desc
        assert "Via : brocabrac.fr" in desc
        assert "🔗 https://brocabrac.fr/event/123" in desc

    def test_missing_sections_omitted(self):
        desc = _build_description({"location": "Lyon"})
        assert "📍 Lyon" in desc
        assert "Via" not in desc
        assert "🔗" not in desc

    def test_empty_event(self):
        desc = _build_description({})
        assert desc == ""

    def test_sections_separated_by_blank_line(self):
        ev = {"location": "Lyon", "url": "https://x.com"}
        desc = _build_description(ev)
        assert "\n\n" in desc


# ── _add_alarm ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestAddAlarm:
    def _get_trigger(self, event_date: date) -> timedelta:
        from icalendar import Event
        vevent = Event()
        _add_alarm(vevent, "Test", event_date)
        alarms = [c for c in vevent.walk() if c.name == "VALARM"]
        assert len(alarms) == 1
        return alarms[0].get("TRIGGER").dt

    def test_saturday_triggers_friday_18h(self):
        saturday = date(2026, 7, 18)  # weekday() == 5
        assert saturday.weekday() == 5
        trigger = self._get_trigger(saturday)
        assert trigger == timedelta(hours=-6)

    def test_sunday_triggers_saturday_18h(self):
        sunday = date(2026, 7, 19)  # weekday() == 6
        assert sunday.weekday() == 6
        trigger = self._get_trigger(sunday)
        assert trigger == timedelta(hours=-6)

    def test_monday_triggers_noon_day_before(self):
        monday = date(2026, 7, 20)  # weekday() == 0
        trigger = self._get_trigger(monday)
        assert trigger == timedelta(hours=-12)

    def test_all_weekdays_use_12h_trigger(self):
        # Monday=0 to Friday=4 → all should use -12h
        base = date(2026, 7, 20)  # Monday
        for offset in range(5):
            d = date(base.year, base.month, base.day + offset)
            assert d.weekday() == offset
            trigger = self._get_trigger(d)
            assert trigger == timedelta(hours=-12), f"Failed for weekday {offset}"

    def test_alarm_action_is_display(self):
        from icalendar import Event
        vevent = Event()
        _add_alarm(vevent, "Test", date(2026, 7, 18))
        alarms = [c for c in vevent.walk() if c.name == "VALARM"]
        assert str(alarms[0].get("ACTION")) == "DISPLAY"

    def test_alarm_description_contains_title(self):
        from icalendar import Event
        vevent = Event()
        _add_alarm(vevent, "Brocante de Lyon", date(2026, 7, 18))
        alarms = [c for c in vevent.walk() if c.name == "VALARM"]
        assert "Brocante de Lyon" in str(alarms[0].get("DESCRIPTION"))


# ── generate_ics — calendar-level properties ──────────────────────────────────

@pytest.mark.unit
class TestCalendarProperties:
    def _cal(self, events=None, config=None):
        return _parse_ics(generate_ics(events or [BASE_EVENT], config or BASE_CONFIG))

    def test_prodid_present(self):
        cal = self._cal()
        assert "brocantes" in str(cal.get("PRODID")).lower()

    def test_version_is_2(self):
        cal = self._cal()
        assert str(cal.get("VERSION")) == "2.0"

    def test_calname_contains_city(self):
        cal = self._cal()
        assert "Lyon" in str(cal.get("X-WR-CALNAME"))

    def test_apple_calendar_color(self):
        cal = self._cal()
        color = str(cal.get("X-APPLE-CALENDAR-COLOR", ""))
        assert color.startswith("#"), f"Expected hex color, got: {color}"

    def test_published_ttl(self):
        cal = self._cal()
        assert cal.get("X-PUBLISHED-TTL") is not None

    def test_refresh_interval_present(self):
        cal = self._cal()
        assert cal.get("REFRESH-INTERVAL") is not None

    def test_empty_events_produces_valid_ics(self):
        raw = generate_ics([], BASE_CONFIG)
        cal = _parse_ics(raw)
        assert _vevents(cal) == []


# ── generate_ics — event-level properties ────────────────────────────────────

@pytest.mark.unit
class TestEventProperties:
    def _vevent(self, ev=None, config=None):
        raw = generate_ics([ev or BASE_EVENT], config or BASE_CONFIG)
        vevents = _vevents(_parse_ics(raw))
        assert len(vevents) == 1
        return vevents[0]

    def test_summary_has_emoji(self):
        v = self._vevent()
        summary = str(v.get("SUMMARY"))
        assert "🛍️" in summary

    def test_summary_has_city_from_geo(self):
        ev = {**BASE_EVENT, "geo": {"lat": 45.76, "lng": 4.83, "city": "Lyon"}}
        v = self._vevent(ev)
        assert "Lyon" in str(v.get("SUMMARY"))

    def test_summary_has_city_from_location_fallback(self):
        ev = {**BASE_EVENT, "location": "Place du Marché, Villeurbanne"}
        v = self._vevent(ev)
        assert "Villeurbanne" in str(v.get("SUMMARY"))

    def test_dtstart_is_allday(self):
        v = self._vevent()
        dtstart = v.get("DTSTART").dt
        assert isinstance(dtstart, date)
        assert type(dtstart) is date  # not datetime

    def test_dtstart_matches_date_parsed(self):
        v = self._vevent()
        assert v.get("DTSTART").dt == date(2026, 7, 15)

    def test_location_field(self):
        v = self._vevent()
        assert "Bellecour" in str(v.get("LOCATION"))

    def test_url_field(self):
        v = self._vevent()
        assert "brocabrac.fr" in str(v.get("URL"))

    def test_description_contains_source_url(self):
        v = self._vevent()
        assert "brocabrac.fr/event/123" in str(v.get("DESCRIPTION"))

    def test_status_confirmed(self):
        v = self._vevent()
        assert str(v.get("STATUS")) == "CONFIRMED"

    def test_uid_is_unique_per_event(self):
        ev1 = {**BASE_EVENT, "uid": "uid-one"}
        ev2 = {**BASE_EVENT, "uid": "uid-two", "title": "Autre événement"}
        raw = generate_ics([ev1, ev2], BASE_CONFIG)
        vevents = _vevents(_parse_ics(raw))
        uids = {str(v.get("UID")) for v in vevents}
        assert len(uids) == 2

    def test_geo_property_present_when_geo_data(self):
        ev = {**BASE_EVENT, "geo": {"lat": 45.764, "lng": 4.836, "city": "Lyon"}}
        v = self._vevent(ev)
        geo = v.get("GEO")
        assert geo is not None

    def test_geo_property_absent_when_no_geo_data(self):
        ev = {k: v for k, v in BASE_EVENT.items() if k != "geo"}
        v = self._vevent(ev)
        assert v.get("GEO") is None

    def test_apple_structured_location_present_when_geo(self):
        ev = {**BASE_EVENT, "geo": {"lat": 45.764, "lng": 4.836, "city": "Lyon"}}
        v = self._vevent(ev)
        assert v.get("X-APPLE-STRUCTURED-LOCATION") is not None

    def test_valarm_present(self):
        v = self._vevent()
        assert len(_valarms(v)) == 1

    def test_valarm_absent_when_no_date(self):
        ev = {**BASE_EVENT, "date_parsed": None}
        v = self._vevent(ev)
        assert len(_valarms(v)) == 0

    def test_multiple_events_all_included(self):
        ev2 = {**BASE_EVENT, "uid": "other-uid", "title": "Vide-grenier Caluire",
               "date_parsed": "2026-08-01"}
        raw = generate_ics([BASE_EVENT, ev2], BASE_CONFIG)
        assert len(_vevents(_parse_ics(raw))) == 2

    def test_ics_bytes_are_valid_utf8(self):
        raw = generate_ics([BASE_EVENT], BASE_CONFIG)
        raw.decode("utf-8")  # must not raise

    def test_ics_starts_with_vcalendar(self):
        raw = generate_ics([BASE_EVENT], BASE_CONFIG)
        assert raw.startswith(b"BEGIN:VCALENDAR")

    def test_invalid_date_parsed_does_not_raise(self):
        """generate_ics must return a valid ICS even when date_parsed is malformed."""
        ev = {**BASE_EVENT, "date_parsed": "not-a-date"}
        raw = generate_ics([ev], BASE_CONFIG)
        assert raw.startswith(b"BEGIN:VCALENDAR")
        cal = _parse_ics(raw)
        vevents = _vevents(cal)
        assert len(vevents) == 1
        assert vevents[0].get("DTSTART") is None

    def test_invalid_date_parsed_logs_warning(self, caplog):
        """generate_ics must log a warning with event title and raw date value."""
        import logging
        ev = {**BASE_EVENT, "title": "Brocante Test", "date_parsed": "32-13-2099"}
        with caplog.at_level(logging.WARNING, logger="app.calendar_gen"):
            generate_ics([ev], BASE_CONFIG)
        assert any(
            "Brocante Test" in r.message and "32-13-2099" in r.message
            for r in caplog.records
        )


# ── generate_ics — malformed / unparseable dates ──────────────────────────────

@pytest.mark.unit
class TestGenerateIcsMalformedDate:
    """generate_ics() must never raise even when date_parsed is garbage."""

    def _ics(self, date_value):
        ev = {**BASE_EVENT, "date_parsed": date_value}
        return generate_ics([ev], BASE_CONFIG)

    def _is_valid_ics(self, raw: bytes) -> bool:
        cal = _parse_ics(raw)
        return cal.get("VERSION") is not None

    def test_not_a_date_string_no_exception(self):
        """An event with date_parsed='not-a-date' must not raise."""
        raw = self._ics("not-a-date")
        assert self._is_valid_ics(raw)

    def test_not_a_date_string_returns_valid_ics(self):
        """The returned bytes must be a parseable iCal calendar."""
        raw = self._ics("not-a-date")
        assert raw.startswith(b"BEGIN:VCALENDAR")
        assert b"END:VCALENDAR" in raw

    def test_none_date_no_exception(self):
        """An event with date_parsed=None must not raise."""
        raw = self._ics(None)
        assert self._is_valid_ics(raw)

    def test_none_date_event_still_included(self):
        """Event with None date is still serialised (without DTSTART/VALARM)."""
        raw = self._ics(None)
        assert BASE_EVENT["title"].encode() in raw

    def test_none_date_no_valarm(self):
        """No VALARM should be added when date_parsed is None."""
        raw = self._ics(None)
        assert b"VALARM" not in raw

    def test_invalid_iso_date_no_exception(self):
        """An event with date_parsed='2026-99-99' (impossible date) must not raise."""
        raw = self._ics("2026-99-99")
        assert self._is_valid_ics(raw)

    def test_invalid_iso_date_returns_valid_ics(self):
        """The returned bytes must be a parseable iCal calendar."""
        raw = self._ics("2026-99-99")
        assert raw.startswith(b"BEGIN:VCALENDAR")
        assert b"END:VCALENDAR" in raw
