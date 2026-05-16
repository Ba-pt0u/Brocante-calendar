"""
Shared fixtures used across the whole test suite.

Key concern: app.config and app.scraper use module-level Path objects
(CONFIG_FILE, EVENTS_FILE, _GEOCACHE_FILE) that are set at import time.
We patch them all to a pytest tmp_path so tests never touch the real ./data/.
"""
import asyncio
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


# ── Data-dir isolation (applies to every test automatically) ─────────────────

@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Redirect all file I/O to a throw-away temp directory."""
    monkeypatch.setattr("app.config.DATA_DIR",    tmp_path)
    monkeypatch.setattr("app.config.CONFIG_FILE",  tmp_path / "config.json")
    monkeypatch.setattr("app.config.EVENTS_FILE",  tmp_path / "events.json")
    monkeypatch.setattr("app.scraper._GEOCACHE_FILE", tmp_path / "geocache.json")
    return tmp_path


# ── In-memory state reset between tests ──────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_app_state():
    """Prevent _state leaking between API tests."""
    from app import main
    main._state["is_refreshing"] = False
    main._state["last_refresh"]  = None
    main._state["refresh_count"] = 0
    yield


# ── FastAPI TestClient with all network calls suppressed ─────────────────────

@pytest.fixture
def client(monkeypatch):
    """
    TestClient with scrape_all stubbed out so no real HTTP requests are made
    and the background refresh task completes instantly.
    """
    async def _noop_scrape(*args, **kwargs):
        return []

    monkeypatch.setattr("app.main.scrape_all", _noop_scrape)

    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Reusable event dict builder ───────────────────────────────────────────────

def make_event(
    title="Grande Brocante de Lyon",
    date_parsed="2026-07-15",
    location="Place Bellecour, Lyon",
    description="Brocante mensuelle",
    url="https://brocabrac.fr/event/123",
    source="brocabrac.fr",
    geo=None,
    uid=None,
):
    import hashlib
    ev = {
        "title": title,
        "date_parsed": date_parsed,
        "location": location,
        "description": description,
        "url": url,
        "source": source,
    }
    key = f"{title.lower()}|{date_parsed}|{location.lower()}"
    ev["uid"] = uid or hashlib.md5(key.encode()).hexdigest()
    if geo:
        ev["geo"] = geo
    return ev
