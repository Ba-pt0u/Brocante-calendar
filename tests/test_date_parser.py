"""
Unit tests for parse_french_date().
All supported input formats are exercised, plus edge-cases that previously
caused silent failures (wrong year, invalid day, empty input, etc.).
"""
import pytest
from datetime import date
from unittest.mock import patch

from app.scraper import parse_french_date

# ── helpers ───────────────────────────────────────────────────────────────────

def _freeze(today: date):
    """Context manager that freezes date.today() inside app.scraper."""
    # We need to patch the 'date' name as used inside scraper.py
    return patch("app.scraper.date", wraps=date, **{"today.return_value": today})


# ── ISO / datetime strings ────────────────────────────────────────────────────

@pytest.mark.unit
class TestISOFormat:
    def test_iso_date(self):
        assert parse_french_date("2026-07-15") == date(2026, 7, 15)

    def test_iso_datetime_truncated(self):
        # Sites sometimes include time component
        assert parse_french_date("2026-07-15T09:00:00") == date(2026, 7, 15)

    def test_iso_in_longer_string(self):
        assert parse_french_date("Date : 2026-08-10 — fin") == date(2026, 8, 10)


# ── Slash / dash numeric format ───────────────────────────────────────────────

@pytest.mark.unit
class TestNumericFormat:
    def test_slash_ddmmyyyy(self):
        assert parse_french_date("15/07/2026") == date(2026, 7, 15)

    def test_dash_ddmmyyyy(self):
        assert parse_french_date("15-07-2026") == date(2026, 7, 15)

    def test_single_digit_day(self):
        assert parse_french_date("5/06/2026") == date(2026, 6, 5)

    def test_single_digit_month(self):
        assert parse_french_date("15/6/2026") == date(2026, 6, 15)


# ── French long format with year ──────────────────────────────────────────────

@pytest.mark.unit
class TestFrenchLongWithYear:
    def test_day_month_year(self):
        assert parse_french_date("15 juin 2026") == date(2026, 6, 15)

    def test_with_day_name(self):
        assert parse_french_date("dimanche 15 juin 2026") == date(2026, 6, 15)

    def test_saturday(self):
        assert parse_french_date("samedi 14 mars 2026") == date(2026, 3, 14)

    def test_all_day_names_stripped(self):
        for day in ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"):
            result = parse_french_date(f"{day} 10 juillet 2026")
            assert result == date(2026, 7, 10), f"Failed for day name: {day}"

    def test_accented_month_aout(self):
        assert parse_french_date("5 août 2026") == date(2026, 8, 5)

    def test_unaccented_month_aout(self):
        assert parse_french_date("5 aout 2026") == date(2026, 8, 5)

    def test_month_fevrier_accented(self):
        assert parse_french_date("28 février 2027") == date(2027, 2, 28)

    def test_month_fevrier_unaccented(self):
        assert parse_french_date("28 fevrier 2027") == date(2027, 2, 28)

    def test_month_decembre(self):
        assert parse_french_date("25 décembre 2026") == date(2026, 12, 25)

    def test_month_abbreviated_juil(self):
        assert parse_french_date("14 juil. 2026") == date(2026, 7, 14)

    def test_all_full_month_names(self):
        months = [
            ("janvier", 1), ("février", 2), ("mars", 3), ("avril", 4),
            ("mai", 5), ("juin", 6), ("juillet", 7), ("août", 8),
            ("septembre", 9), ("octobre", 10), ("novembre", 11), ("décembre", 12),
        ]
        for name, num in months:
            result = parse_french_date(f"1 {name} 2027")
            assert result == date(2027, num, 1), f"Failed for month: {name}"


# ── Year-less format (date.today() dependency) ────────────────────────────────

@pytest.mark.unit
class TestYearLessFormat:
    def test_future_month_same_year(self):
        # Freeze today to Jan 15 → "20 mars" should resolve to Mar 20 same year
        frozen_today = date(2026, 1, 15)
        with patch("app.scraper.date") as mock_date:
            mock_date.today.return_value = frozen_today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = parse_french_date("20 mars")
        assert result == date(2026, 3, 20)

    def test_past_month_rolls_to_next_year(self):
        # Freeze today to Oct 1 → "15 juin" (already passed) → next year
        frozen_today = date(2026, 10, 1)
        with patch("app.scraper.date") as mock_date:
            mock_date.today.return_value = frozen_today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = parse_french_date("15 juin")
        assert result == date(2027, 6, 15)

    def test_result_is_never_in_past(self):
        result = parse_french_date("1 janvier")
        assert result is not None
        assert result >= date.today()


# ── Edge cases ────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestEdgeCases:
    def test_empty_string(self):
        assert parse_french_date("") is None

    def test_none_like_empty(self):
        assert parse_french_date("   ") is None

    def test_garbage_string(self):
        assert parse_french_date("pas une date") is None

    def test_invalid_day_31_april(self):
        # April has 30 days — should return None, not raise
        assert parse_french_date("31 avril 2026") is None

    def test_invalid_day_30_february(self):
        assert parse_french_date("30/02/2026") is None

    def test_whitespace_around_date(self):
        assert parse_french_date("  15/07/2026  ") == date(2026, 7, 15)

    def test_extra_text_around_date(self):
        assert parse_french_date("Venez le 15 juin 2026 dès 8h") == date(2026, 6, 15)
