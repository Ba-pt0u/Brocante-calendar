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
        resp1 = client.get("/feed.ics")
        etag1 = resp1.headers["etag"]
        save_events([{"title": "Brocante", "date_parsed": "2026-08-01",
                      "location": "Paris", "uid": "u1", "source": "s"}])
        resp2 = client.get("/feed.ics")
        etag2 = resp2.headers["etag"]
        assert etag1 != etag2


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
