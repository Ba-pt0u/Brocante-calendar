"""
Integration tests for the FastAPI endpoints.
scrape_all is stubbed so no real HTTP requests are made.
The 'client' fixture (from conftest.py) handles setup.
"""
import json
import pytest

import app.config as cfg
from app.config import save_events, save_config, load_events


# ── GET / ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestIndexRoute:
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_content_type_is_html(self, client):
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_contains_app_title(self, client):
        resp = client.get("/")
        assert "Brocantes" in resp.text


# ── GET /feed.ics ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestIcsFeed:
    def test_returns_200(self, client):
        resp = client.get("/feed.ics")
        assert resp.status_code == 200

    def test_content_type_is_calendar(self, client):
        resp = client.get("/feed.ics")
        assert "text/calendar" in resp.headers["content-type"]

    def test_starts_with_vcalendar(self, client):
        resp = client.get("/feed.ics")
        assert resp.content.startswith(b"BEGIN:VCALENDAR")

    def test_no_cache_headers(self, client):
        resp = client.get("/feed.ics")
        assert "no-cache" in resp.headers.get("cache-control", "")

    def test_includes_saved_events(self, client, isolated_data):
        save_events([{
            "title": "Brocante de Test", "date_parsed": "2026-07-15",
            "location": "Lyon", "uid": "test-uid",
            "source": "brocabrac.fr", "url": "https://x.com",
            "description": "",
        }])
        resp = client.get("/feed.ics")
        assert b"Brocante de Test" in resp.content

    def test_ics_with_no_events_is_valid(self, client):
        resp = client.get("/feed.ics")
        assert b"BEGIN:VCALENDAR" in resp.content
        assert b"END:VCALENDAR" in resp.content

    def test_etag_header_present(self, client):
        resp = client.get("/feed.ics")
        assert "etag" in resp.headers

    def test_conditional_get_returns_304(self, client):
        resp1 = client.get("/feed.ics")
        etag = resp1.headers["etag"]
        resp2 = client.get("/feed.ics", headers={"if-none-match": etag})
        assert resp2.status_code == 304

    def test_different_content_gives_different_etag(self, client, isolated_data):
        import app.main as m
        resp1 = client.get("/feed.ics")
        etag1 = resp1.headers["etag"]
        save_events([{"title": "Brocante", "date_parsed": "2026-08-01",
                      "location": "Paris", "uid": "u1", "source": "s"}])
        m._state["ics_cache"] = {}  # direct save_events bypasses the API → invalidate manually
        resp2 = client.get("/feed.ics")
        etag2 = resp2.headers["etag"]
        assert etag1 != etag2


@pytest.mark.integration
class TestIcsFeedTypeFilter:
    _EVENTS = [
        {"title": "Grande Brocante", "date_parsed": "2026-08-01", "location": "Lyon",
         "uid": "u1", "source": "s", "ev_type": "brocante", "description": "", "url": ""},
        {"title": "Vide-grenier Caluire", "date_parsed": "2026-08-02", "location": "Caluire",
         "uid": "u2", "source": "s", "ev_type": "vide-grenier", "description": "", "url": ""},
        {"title": "Braderie Villeurbanne", "date_parsed": "2026-08-03", "location": "Villeurbanne",
         "uid": "u3", "source": "s", "ev_type": "braderie", "description": "", "url": ""},
    ]

    def _save(self, isolated_data):
        save_events(self._EVENTS)

    def test_no_filter_returns_all(self, client, isolated_data):
        self._save(isolated_data)
        resp = client.get("/feed.ics")
        assert resp.status_code == 200
        body = resp.content
        assert b"Grande Brocante" in body
        assert b"Vide-grenier Caluire" in body
        assert b"Braderie Villeurbanne" in body

    def test_single_type_filter(self, client, isolated_data):
        self._save(isolated_data)
        resp = client.get("/feed.ics?types=brocante")
        assert b"Grande Brocante" in resp.content
        assert b"Vide-grenier" not in resp.content
        assert b"Braderie" not in resp.content

    def test_multi_type_filter(self, client, isolated_data):
        self._save(isolated_data)
        resp = client.get("/feed.ics?types=brocante,vide-grenier")
        assert b"Grande Brocante" in resp.content
        assert b"Vide-grenier Caluire" in resp.content
        assert b"Braderie" not in resp.content

    def test_invalid_type_ignored(self, client, isolated_data):
        self._save(isolated_data)
        resp = client.get("/feed.ics?types=invalid_type")
        assert resp.status_code == 200
        # unknown type filtered → no events match (valid ICS but empty)
        assert b"BEGIN:VCALENDAR" in resp.content

    def test_empty_types_param_returns_all(self, client, isolated_data):
        self._save(isolated_data)
        resp = client.get("/feed.ics?types=")
        assert b"Grande Brocante" in resp.content

    def test_filter_changes_etag(self, client, isolated_data):
        self._save(isolated_data)
        etag_all = client.get("/feed.ics").headers["etag"]
        etag_brocante = client.get("/feed.ics?types=brocante").headers["etag"]
        assert etag_all != etag_brocante

    def test_calname_includes_type_label_when_filtered(self, client, isolated_data):
        self._save(isolated_data)
        resp = client.get("/feed.ics?types=brocante")
        assert b"Brocante" in resp.content

    def test_304_still_works_with_type_filter(self, client, isolated_data):
        self._save(isolated_data)
        resp1 = client.get("/feed.ics?types=brocante")
        etag = resp1.headers["etag"]
        resp2 = client.get("/feed.ics?types=brocante", headers={"if-none-match": etag})
        assert resp2.status_code == 304


# ── ICS ETag cache ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestIcsEtagCache:
    """Tests for the in-memory ICS cache (feature D)."""

    _EVENTS = [
        {"title": "Grande Brocante", "date_parsed": "2026-08-01", "location": "Lyon",
         "uid": "u1", "source": "s", "ev_type": "brocante", "description": "", "url": ""},
        {"title": "Vide-grenier Caluire", "date_parsed": "2026-08-02", "location": "Caluire",
         "uid": "u2", "source": "s", "ev_type": "vide-grenier", "description": "", "url": ""},
    ]

    def test_cache_is_populated_after_first_request(self, client, isolated_data):
        """After the first /feed.ics call the cache entry for '' should exist."""
        import app.main as m
        save_events(self._EVENTS)
        assert m._state["ics_cache"] == {}
        client.get("/feed.ics")
        assert "" in m._state["ics_cache"]

    def test_cache_hit_returns_same_etag(self, client, isolated_data):
        """Two consecutive requests must return the same ETag (cache hit)."""
        save_events(self._EVENTS)
        etag1 = client.get("/feed.ics").headers["etag"]
        etag2 = client.get("/feed.ics").headers["etag"]
        assert etag1 == etag2

    def test_cache_invalidated_after_purge(self, client, isolated_data):
        """DELETE /api/events must clear the ICS cache."""
        import app.main as m
        save_events(self._EVENTS)
        client.get("/feed.ics")          # populate cache
        assert "" in m._state["ics_cache"]
        client.delete("/api/events")
        assert m._state["ics_cache"] == {}

    def test_cache_invalidated_after_refresh(self, isolated_data, monkeypatch):
        """After _do_refresh runs, the ICS cache must be empty."""
        import app.main as m
        import asyncio
        from fastapi.testclient import TestClient

        async def _fake_scrape(*a, **kw):
            return self._EVENTS

        monkeypatch.setattr("app.main.scrape_all", _fake_scrape)

        with TestClient(m.app, raise_server_exceptions=True) as c:
            m._state["ics_cache"] = {}
            save_events(self._EVENTS)
            c.get("/feed.ics")          # populates cache
            assert "" in m._state["ics_cache"]

            asyncio.get_event_loop().run_until_complete(m._do_refresh())
            assert m._state["ics_cache"] == {}

    def test_separate_cache_per_type_filter(self, client, isolated_data):
        """Requests with different ?types= must use separate cache keys."""
        import app.main as m
        save_events(self._EVENTS)
        client.get("/feed.ics")
        client.get("/feed.ics?types=brocante")
        assert "" in m._state["ics_cache"]
        assert "brocante" in m._state["ics_cache"]
        assert m._state["ics_cache"][""]["etag"] != m._state["ics_cache"]["brocante"]["etag"]

    def test_cache_key_is_sorted_types(self, client, isolated_data):
        """?types=vide-grenier,brocante and ?types=brocante,vide-grenier share the same cache."""
        import app.main as m
        save_events(self._EVENTS)
        r1 = client.get("/feed.ics?types=vide-grenier,brocante")
        r2 = client.get("/feed.ics?types=brocante,vide-grenier")
        assert r1.headers["etag"] == r2.headers["etag"]
        assert "brocante,vide-grenier" in m._state["ics_cache"]
        assert len(m._state["ics_cache"]) == 1


# ── ICS caching behaviour (TestIcsCaching) ────────────────────────────────────

@pytest.mark.integration
class TestIcsCaching:
    _EVENTS = [
        {"title": "Brocante Metz", "date_parsed": "2026-09-05",
         "location": "Place Saint-Louis, Metz", "uid": "metz-001",
         "source": "brocabrac.fr", "ev_type": "brocante",
         "description": "", "url": "https://brocabrac.fr/1"},
        {"title": "Vide-grenier Nancy", "date_parsed": "2026-09-12",
         "location": "Place Stanislas, Nancy", "uid": "nancy-002",
         "source": "vide-greniers.org", "ev_type": "vide-grenier",
         "description": "", "url": "https://vide-greniers.org/2"},
    ]

    def test_two_successive_requests_return_same_etag(self, client, isolated_data):
        """When events have not changed, two calls must return the same ETag."""
        save_events(self._EVENTS)
        etag1 = client.get("/feed.ics").headers["etag"]
        etag2 = client.get("/feed.ics").headers["etag"]
        assert etag1 == etag2

    def test_etag_changes_after_delete_events(self, client, isolated_data):
        """After DELETE /api/events the ICS content changes → ETag must differ."""
        save_events(self._EVENTS)
        etag_before = client.get("/feed.ics").headers["etag"]
        client.delete("/api/events")
        etag_after = client.get("/feed.ics").headers["etag"]
        assert etag_before != etag_after

    def test_type_filter_produces_different_etag_than_no_filter(self, client, isolated_data):
        """?types=brocante returns a different subset than no filter → different ETag."""
        save_events(self._EVENTS)
        etag_all = client.get("/feed.ics").headers["etag"]
        etag_filtered = client.get("/feed.ics?types=brocante").headers["etag"]
        assert etag_all != etag_filtered


# ── GET /api/config ───────────────────────────────────────────────────────────

@pytest.mark.integration
class TestGetConfig:
    def test_returns_200(self, client):
        assert client.get("/api/config").status_code == 200

    def test_returns_default_when_no_config_file(self, client):
        data = client.get("/api/config").json()
        assert "lat" in data
        assert "lng" in data
        assert "city" in data
        assert "radius_km" in data
        assert "refresh_hours" in data

    def test_returns_saved_config(self, client, isolated_data):
        payload = {"lat": 45.76, "lng": 4.83, "city": "Lyon",
                   "radius_km": 50, "refresh_hours": 6}
        save_config(payload)
        data = client.get("/api/config").json()
        assert data["city"] == "Lyon"
        assert data["radius_km"] == 50


# ── POST /api/config ──────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPostConfig:
    VALID_PAYLOAD = {
        "lat": 45.764, "lng": 4.836, "city": "Lyon",
        "radius_km": 30, "refresh_hours": 12,
    }

    def test_returns_200_with_valid_payload(self, client):
        resp = client.post("/api/config", json=self.VALID_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_ok_status(self, client):
        resp = client.post("/api/config", json=self.VALID_PAYLOAD)
        assert resp.json()["status"] == "ok"

    def test_config_is_persisted(self, client, isolated_data):
        client.post("/api/config", json=self.VALID_PAYLOAD)
        loaded = cfg.load_config()
        assert loaded["city"] == "Lyon"
        assert loaded["radius_km"] == 30

    def test_missing_lat_returns_422(self, client):
        payload = {k: v for k, v in self.VALID_PAYLOAD.items() if k != "lat"}
        resp = client.post("/api/config", json=payload)
        assert resp.status_code == 422

    def test_missing_city_returns_422(self, client):
        payload = {k: v for k, v in self.VALID_PAYLOAD.items() if k != "city"}
        resp = client.post("/api/config", json=payload)
        assert resp.status_code == 422

    def test_all_required_fields_validated(self, client):
        for field in ("lat", "lng", "city", "radius_km", "refresh_hours"):
            payload = {k: v for k, v in self.VALID_PAYLOAD.items() if k != field}
            resp = client.post("/api/config", json=payload)
            assert resp.status_code == 422, f"Expected 422 when '{field}' is missing"

    def test_lat_below_france_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "lat": 39.0})
        assert resp.status_code == 422

    def test_lat_above_france_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "lat": 52.0})
        assert resp.status_code == 422

    def test_lng_east_of_france_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "lng": 15.0})
        assert resp.status_code == 422

    def test_lng_west_of_france_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "lng": -10.0})
        assert resp.status_code == 422

    def test_radius_zero_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "radius_km": 0})
        assert resp.status_code == 422

    def test_radius_too_large_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "radius_km": 501})
        assert resp.status_code == 422

    def test_refresh_hours_zero_returns_422(self, client):
        resp = client.post("/api/config", json={**self.VALID_PAYLOAD, "refresh_hours": 0})
        assert resp.status_code == 422

    def test_location_change_purges_events(self, client, isolated_data):
        # Save Lyon config first, then switch to Paris — should purge
        save_config(self.VALID_PAYLOAD)
        save_events([{"uid": "old", "title": "Vieux événement", "date_parsed": "2026-07-01"}])
        paris = {**self.VALID_PAYLOAD, "lat": 48.856, "lng": 2.352, "city": "Paris"}
        resp = client.post("/api/config", json=paris)
        assert resp.json()["purged"] is True
        assert load_events() == []

    def test_same_location_does_not_purge(self, client, isolated_data):
        # Save same Lyon config, then repost it — should NOT purge
        save_config(self.VALID_PAYLOAD)
        save_events([{"uid": "keep", "title": "À garder", "date_parsed": "2026-07-01"}])
        resp = client.post("/api/config", json=self.VALID_PAYLOAD)
        assert resp.json()["purged"] is False
        assert len(load_events()) == 1

    def test_watch_keywords_accepted(self, client):
        payload = {**self.VALID_PAYLOAD, "watch_keywords": ["Saint-Arnoult"]}
        resp = client.post("/api/config", json=payload)
        assert resp.status_code == 200

    def test_watch_keywords_persisted(self, client, isolated_data):
        payload = {**self.VALID_PAYLOAD, "watch_keywords": ["Limours", "Rambouillet"]}
        client.post("/api/config", json=payload)
        loaded = cfg.load_config()
        assert loaded.get("watch_keywords") == ["Limours", "Rambouillet"]

    def test_watch_keywords_absent_defaults_to_empty(self, client, isolated_data):
        client.post("/api/config", json=self.VALID_PAYLOAD)
        loaded = cfg.load_config()
        assert loaded.get("watch_keywords") == []

    def test_keyword_change_invalidates_ics_cache(self, client, isolated_data):
        import app.main as m
        client.post("/api/config", json=self.VALID_PAYLOAD)
        client.get("/feed.ics")  # populate cache
        assert m._state["ics_cache"] != {}
        client.post("/api/config", json={**self.VALID_PAYLOAD, "watch_keywords": ["Lyon"]})
        assert m._state["ics_cache"] == {}


# ── DELETE /api/events ────────────────────────────────────────────────────────

@pytest.mark.integration
class TestDeleteEvents:
    def test_returns_200(self, client):
        assert client.delete("/api/events").status_code == 200

    def test_response_has_ok_status(self, client):
        assert client.delete("/api/events").json()["status"] == "ok"

    def test_clears_all_events(self, client, isolated_data):
        save_events([{"uid": "a"}, {"uid": "b"}, {"uid": "c"}])
        client.delete("/api/events")
        assert load_events() == []

    def test_resets_last_refresh(self, client, isolated_data):
        import app.main as m
        m._state["last_refresh"] = "2026-05-16T10:00:00"
        client.delete("/api/events")
        assert client.get("/api/status").json()["last_refresh"] is None

    def test_idempotent_on_empty_list(self, client, isolated_data):
        client.delete("/api/events")
        client.delete("/api/events")
        assert load_events() == []


# ── GET /api/events ───────────────────────────────────────────────────────────

@pytest.mark.integration
class TestGetEvents:
    def test_returns_200(self, client):
        assert client.get("/api/events").status_code == 200

    def test_response_structure(self, client):
        data = client.get("/api/events").json()
        assert "events" in data
        assert "count" in data
        assert "last_refresh" in data

    def test_events_is_list(self, client):
        assert isinstance(client.get("/api/events").json()["events"], list)

    def test_count_matches_events_length(self, client, isolated_data):
        save_events([
            {"title": "A", "uid": "1", "date_parsed": "2026-07-01"},
            {"title": "B", "uid": "2", "date_parsed": "2026-07-02"},
        ])
        data = client.get("/api/events").json()
        assert data["count"] == len(data["events"])

    def test_returns_saved_events(self, client, isolated_data):
        save_events([{"title": "Brocante Lyon", "uid": "u1", "date_parsed": "2026-07-15"}])
        data = client.get("/api/events").json()
        assert data["events"][0]["title"] == "Brocante Lyon"


# ── GET /api/events — starred field ──────────────────────────────────────────

@pytest.mark.integration
class TestGetEventsStarred:
    _EVENTS = [
        {"title": "Brocante de Saint-Arnoult", "uid": "sa-01",
         "date_parsed": "2026-07-15", "location": "Saint-Arnoult-en-Yvelines",
         "ev_type": "brocante"},
        {"title": "Vide-grenier Versailles", "uid": "vs-01",
         "date_parsed": "2026-07-22", "location": "Versailles",
         "ev_type": "vide-grenier"},
    ]

    def test_starred_field_present_on_all_events(self, client, isolated_data):
        save_events(self._EVENTS)
        data = client.get("/api/events").json()
        for ev in data["events"]:
            assert "starred" in ev

    def test_starred_true_when_keyword_matches(self, client, isolated_data):
        save_config({**cfg.DEFAULT_CONFIG, "watch_keywords": ["Saint-Arnoult"]})
        save_events(self._EVENTS)
        data = client.get("/api/events").json()
        sa = next(e for e in data["events"] if "Saint-Arnoult" in e["title"])
        assert sa["starred"] is True

    def test_starred_false_when_no_match(self, client, isolated_data):
        save_config({**cfg.DEFAULT_CONFIG, "watch_keywords": ["Saint-Arnoult"]})
        save_events(self._EVENTS)
        data = client.get("/api/events").json()
        vs = next(e for e in data["events"] if "Versailles" in e["title"])
        assert vs["starred"] is False

    def test_all_unstarred_when_no_keywords(self, client, isolated_data):
        save_config({**cfg.DEFAULT_CONFIG, "watch_keywords": []})
        save_events(self._EVENTS)
        data = client.get("/api/events").json()
        assert all(e["starred"] is False for e in data["events"])


# ── POST /api/refresh ─────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPostRefresh:
    def test_returns_200(self, client):
        assert client.post("/api/refresh").status_code == 200

    def test_response_has_ok_status(self, client):
        assert client.post("/api/refresh").json()["status"] == "ok"


# ── GET /api/status ───────────────────────────────────────────────────────────

@pytest.mark.integration
class TestGetStatus:
    def test_returns_200(self, client):
        assert client.get("/api/status").status_code == 200

    def test_response_has_required_keys(self, client):
        data = client.get("/api/status").json()
        for key in ("last_refresh", "is_refreshing", "refresh_count",
                    "event_count", "config"):
            assert key in data, f"Missing key: {key}"

    def test_is_refreshing_is_bool(self, client):
        data = client.get("/api/status").json()
        assert isinstance(data["is_refreshing"], bool)

    def test_event_count_reflects_saved_events(self, client, isolated_data):
        save_events([{"uid": "a"}, {"uid": "b"}, {"uid": "c"}])
        data = client.get("/api/status").json()
        assert data["event_count"] == 3

    def test_config_embedded_in_status(self, client):
        data = client.get("/api/status").json()
        assert isinstance(data["config"], dict)
        assert "city" in data["config"]

    def test_sources_key_in_status(self, client):
        data = client.get("/api/status").json()
        assert "sources" in data
        assert isinstance(data["sources"], dict)


# ── Full pipeline : save → /feed.ics → parse → /api/events filters ───────────

@pytest.mark.integration
class TestFullPipeline:
    """End-to-end: save events → GET /feed.ics → parse ICS → verify VEVENTs."""

    _EVENTS = [
        {
            "title": "Grande Brocante de Lyon",
            "date_parsed": "2026-07-15",
            "location": "Place Bellecour, Lyon",
            "description": "Brocante mensuelle",
            "url": "https://brocabrac.fr/event/123",
            "source": "brocabrac.fr",
            "uid": "brocante-lyon-001",
            "ev_type": "brocante",
            "geo": {"lat": 45.764, "lng": 4.836, "city": "Lyon", "postcode": "69002"},
        },
        {
            "title": "Vide-grenier Villeurbanne",
            "date_parsed": "2026-07-22",
            "location": "Place de la Mairie, Villeurbanne",
            "description": "",
            "url": "https://brocabrac.fr/event/456",
            "source": "brocabrac.fr",
            "uid": "vide-grenier-villeurb-002",
            "ev_type": "vide-grenier",
            "geo": {"lat": 45.772, "lng": 4.891, "city": "Villeurbanne", "postcode": "69100"},
        },
    ]

    def _ics_vevents(self, raw: bytes) -> list[str]:
        return [l.strip() for l in raw.decode("utf-8").splitlines() if l.strip() == "BEGIN:VEVENT"]

    def test_ics_contains_all_saved_events(self, client, isolated_data):
        save_events(self._EVENTS)
        resp = client.get("/feed.ics")
        assert resp.status_code == 200
        assert resp.content.startswith(b"BEGIN:VCALENDAR")
        assert len(self._ics_vevents(resp.content)) == 2

    def test_ics_geo_present_for_geocoded_events(self, client, isolated_data):
        save_events(self._EVENTS)
        ics = client.get("/feed.ics").content.decode("utf-8")
        assert "GEO:" in ics
        assert "X-APPLE-STRUCTURED-LOCATION" in ics

    def test_ics_valarm_present_for_dated_events(self, client, isolated_data):
        save_events(self._EVENTS)
        ics = client.get("/feed.ics").content.decode("utf-8")
        assert "BEGIN:VALARM" in ics

    def test_ics_type_filter_reduces_event_count(self, client, isolated_data):
        save_events(self._EVENTS)
        import app.main as m; m._state["ics_cache"] = {}
        ics = client.get("/feed.ics?types=brocante").content.decode("utf-8")
        assert len(self._ics_vevents(ics.encode())) == 1
        assert "Grande Brocante de Lyon" in ics
        assert "Vide-grenier Villeurbanne" not in ics

    def test_api_events_type_filter(self, client, isolated_data):
        save_events(self._EVENTS)
        data = client.get("/api/events?types=vide-grenier").json()
        assert data["count"] == 1
        assert data["events"][0]["title"] == "Vide-grenier Villeurbanne"

    def test_api_events_dept_filter(self, client, isolated_data):
        save_events(self._EVENTS)
        data = client.get("/api/events?dept=69").json()
        assert data["count"] == 2  # both events are in dept 69

    def test_api_events_within_days_filter(self, client, isolated_data):
        from datetime import date, timedelta
        today = date.today()
        near = {**self._EVENTS[0], "uid": "near-01",
                "date_parsed": (today + timedelta(days=3)).isoformat()}
        far = {**self._EVENTS[1], "uid": "far-01",
               "date_parsed": (today + timedelta(days=30)).isoformat()}
        save_events([near, far])
        data = client.get("/api/events?within_days=7").json()
        assert data["count"] == 1
        assert data["events"][0]["uid"] == "near-01"

    def test_purge_then_ics_returns_empty_calendar(self, client, isolated_data):
        save_events(self._EVENTS)
        client.delete("/api/events")
        resp = client.get("/feed.ics")
        assert resp.content.startswith(b"BEGIN:VCALENDAR")
        assert len(self._ics_vevents(resp.content)) == 0

    def test_events_in_memory_cache_updated_after_purge(self, client, isolated_data):
        import app.main as m
        save_events(self._EVENTS)
        m._state["events"] = self._EVENTS  # seed cache as if refresh ran
        client.delete("/api/events")
        assert m._state["events"] == []

    def test_ics_utf8_event_titles_round_trip(self, client, isolated_data):
        ev = {**self._EVENTS[0], "title": "Braderie à Strasbourg", "uid": "str-001"}
        save_events([ev])
        raw = client.get("/feed.ics").content
        assert "Braderie" in raw.decode("utf-8")
        assert raw.decode("utf-8")  # must not raise on decode
