import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app.calendar_gen import generate_ics
from app.config import load_config, load_events, save_config, save_events
from app.scraper import scrape_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ──────────────────────────────────────────────
# Shared mutable state (single-process)
# ──────────────────────────────────────────────
_state: dict[str, Any] = {
    "last_refresh": None,
    "is_refreshing": False,
    "refresh_count": 0,
}
_scheduler = AsyncIOScheduler()


# ──────────────────────────────────────────────
# Config validation model
# ──────────────────────────────────────────────
class ConfigPayload(BaseModel):
    lat: float = Field(..., ge=41.0, le=51.5)
    lng: float = Field(..., ge=-5.5, le=9.5)
    city: str = Field(..., min_length=1)
    radius_km: int = Field(..., gt=0, le=500)
    refresh_hours: int = Field(..., ge=1, le=168)


async def _do_refresh() -> None:
    if _state["is_refreshing"]:
        logger.info("Refresh already running, skipping")
        return
    _state["is_refreshing"] = True
    try:
        config = load_config()
        events = await scrape_all(
            config["lat"], config["lng"], config["radius_km"]
        )
        save_events(events)
        _state["last_refresh"] = datetime.now().isoformat()
        _state["refresh_count"] += 1
        logger.info("Refresh complete: %d events", len(events))
    except Exception:
        logger.exception("Refresh failed")
    finally:
        _state["is_refreshing"] = False


def _reschedule(hours: int) -> None:
    if _scheduler.get_job("periodic_refresh"):
        _scheduler.reschedule_job(
            "periodic_refresh", trigger="interval", hours=hours
        )
    else:
        _scheduler.add_job(
            _do_refresh,
            "interval",
            hours=hours,
            id="periodic_refresh",
            replace_existing=True,
        )
    logger.info("Refresh scheduled every %dh", hours)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    _reschedule(config.get("refresh_hours", 12))
    _scheduler.start()
    asyncio.create_task(_do_refresh())
    yield
    _scheduler.shutdown(wait=False)


# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────
app = FastAPI(title="Brocantes App", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/feed.ics", response_class=Response)
async def ics_feed(request: Request):
    config = load_config()
    events = load_events()
    ics_bytes = generate_ics(events, config)
    etag = f'"{hashlib.md5(ics_bytes).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    return Response(
        content=ics_bytes,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="brocantes.ics"',
            # no-cache: revalidate every time, but use ETag for conditional GET
            "Cache-Control": "no-cache, must-revalidate",
            "ETag": etag,
        },
    )


@app.get("/api/config")
async def api_get_config():
    return load_config()


@app.post("/api/config")
async def api_post_config(body: ConfigPayload):
    current = load_config()
    location_changed = (
        abs(current.get("lat", 0) - body.lat) > 0.01
        or abs(current.get("lng", 0) - body.lng) > 0.01
        or current.get("radius_km", 0) != body.radius_km
    )
    save_config(body.model_dump())
    if location_changed:
        # Purge immediately so the UI doesn't show stale events from the old location
        save_events([])
        _state["last_refresh"] = None
    _reschedule(body.refresh_hours)
    asyncio.create_task(_do_refresh())
    return {"status": "ok", "message": "Config saved, refresh started", "purged": location_changed}


@app.get("/api/events")
async def api_events():
    events = load_events()
    return {
        "events": events,
        "count": len(events),
        "last_refresh": _state["last_refresh"],
    }


@app.delete("/api/events")
async def api_purge_events():
    """Purge all cached events. The next scheduled refresh will repopulate."""
    save_events([])
    _state["last_refresh"] = None
    return {"status": "ok", "message": "Events purged"}


@app.post("/api/refresh")
async def api_refresh():
    asyncio.create_task(_do_refresh())
    return {"status": "ok", "message": "Refresh started"}


@app.get("/api/status")
async def api_status():
    from app.scraper import _last_scrape_results
    config = load_config()
    events = load_events()
    return {
        "last_refresh": _state["last_refresh"],
        "is_refreshing": _state["is_refreshing"],
        "refresh_count": _state["refresh_count"],
        "event_count": len(events),
        "config": config,
        "sources": dict(_last_scrape_results),
    }
