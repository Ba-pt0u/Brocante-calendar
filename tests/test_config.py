"""
Unit tests for config.py — load/save round-trips and resilience to bad data.
All file I/O is redirected to pytest's tmp_path via the isolated_data fixture.
"""
import json
import pytest

import app.config as cfg
from app.config import (
    load_config, save_config,
    load_events, save_events,
    DEFAULT_CONFIG,
)


@pytest.mark.unit
class TestLoadConfig:
    def test_returns_default_when_no_file(self, isolated_data):
        result = load_config()
        assert result == DEFAULT_CONFIG

    def test_default_has_required_keys(self, isolated_data):
        result = load_config()
        for key in ("lat", "lng", "city", "radius_km", "refresh_hours"):
            assert key in result

    def test_returns_copy_not_reference(self, isolated_data):
        a = load_config()
        b = load_config()
        a["city"] = "MODIFIED"
        assert b["city"] != "MODIFIED"

    def test_returns_empty_dict_on_corrupt_json(self, isolated_data):
        cfg.CONFIG_FILE.write_text("{ not json }", encoding="utf-8")
        result = load_config()
        assert result == DEFAULT_CONFIG

    def test_returns_empty_dict_on_empty_file(self, isolated_data):
        cfg.CONFIG_FILE.write_text("", encoding="utf-8")
        result = load_config()
        assert result == DEFAULT_CONFIG


@pytest.mark.unit
class TestSaveConfig:
    def test_round_trip(self, isolated_data):
        payload = {"lat": 45.76, "lng": 4.83, "city": "Lyon",
                   "radius_km": 50, "refresh_hours": 6}
        save_config(payload)
        assert load_config() == payload

    def test_file_created_in_data_dir(self, isolated_data):
        save_config({"city": "Bordeaux", "lat": 44.8, "lng": -0.57,
                     "radius_km": 30, "refresh_hours": 12})
        assert cfg.CONFIG_FILE.exists()

    def test_file_is_valid_json(self, isolated_data):
        save_config({"city": "Nantes", "lat": 47.2, "lng": -1.55,
                     "radius_km": 20, "refresh_hours": 24})
        data = json.loads(cfg.CONFIG_FILE.read_text(encoding="utf-8"))
        assert data["city"] == "Nantes"

    def test_unicode_city_name(self, isolated_data):
        save_config({"city": "Châteauroux", "lat": 46.8, "lng": 1.69,
                     "radius_km": 10, "refresh_hours": 12})
        loaded = load_config()
        assert loaded["city"] == "Châteauroux"

    def test_overwrites_previous(self, isolated_data):
        save_config({"city": "Paris", "lat": 48.8, "lng": 2.3,
                     "radius_km": 30, "refresh_hours": 12})
        save_config({"city": "Marseille", "lat": 43.3, "lng": 5.4,
                     "radius_km": 30, "refresh_hours": 12})
        assert load_config()["city"] == "Marseille"


@pytest.mark.unit
class TestLoadEvents:
    def test_returns_empty_list_when_no_file(self, isolated_data):
        assert load_events() == []

    def test_returns_empty_list_on_corrupt_json(self, isolated_data):
        cfg.EVENTS_FILE.write_text("[ broken", encoding="utf-8")
        assert load_events() == []


@pytest.mark.unit
class TestSaveEvents:
    def test_round_trip(self, isolated_data):
        events = [
            {"title": "Brocante Lyon", "date_parsed": "2026-07-15",
             "uid": "abc", "source": "brocabrac.fr"},
            {"title": "Vide-grenier Paris", "date_parsed": "2026-08-01",
             "uid": "def", "source": "vide-greniers.org"},
        ]
        save_events(events)
        loaded = load_events()
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Brocante Lyon"

    def test_saves_empty_list(self, isolated_data):
        save_events([])
        assert load_events() == []

    def test_file_is_valid_json(self, isolated_data):
        save_events([{"uid": "x", "title": "T"}])
        data = json.loads(cfg.EVENTS_FILE.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_overwrites_previous(self, isolated_data):
        save_events([{"uid": "a", "title": "Premier"}])
        save_events([{"uid": "b", "title": "Second"}])
        loaded = load_events()
        assert len(loaded) == 1
        assert loaded[0]["uid"] == "b"

    def test_unicode_in_events(self, isolated_data):
        save_events([{"title": "Braderie à Strasbourg", "uid": "u"}])
        loaded = load_events()
        assert loaded[0]["title"] == "Braderie à Strasbourg"


@pytest.mark.unit
class TestAtomicWrite:
    def test_save_config_produces_valid_json(self, isolated_data):
        """save_config() always writes a parseable JSON file."""
        payload = {"lat": 47.2, "lng": -1.55, "city": "Nantes",
                   "radius_km": 25, "refresh_hours": 8}
        save_config(payload)
        data = json.loads(cfg.CONFIG_FILE.read_text(encoding="utf-8"))
        assert data == payload

    def test_save_events_empty_then_load_returns_empty_list(self, isolated_data):
        """save_events([]) followed by load_events() returns []."""
        save_events([])
        assert load_events() == []

    def test_save_events_roundtrip(self, isolated_data):
        """save_events(data) then load_events() returns the exact same data."""
        events = [
            {"title": "Brocante Rennes", "date_parsed": "2026-09-12",
             "uid": "rennes-001", "source": "brocabrac.fr",
             "location": "Place de la Mairie, Rennes",
             "description": "Grande brocante annuelle", "url": "https://x.com/1"},
            {"title": "Vide-grenier Bordeaux", "date_parsed": "2026-10-03",
             "uid": "bx-002", "source": "vide-greniers.org",
             "location": "Quai des Chartrons, Bordeaux",
             "description": "", "url": "https://x.com/2"},
        ]
        save_events(events)
        loaded = load_events()
        assert loaded == events
