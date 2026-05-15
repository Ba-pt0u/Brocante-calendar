import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
CONFIG_FILE = DATA_DIR / "config.json"
EVENTS_FILE = DATA_DIR / "events.json"

DEFAULT_CONFIG = {
    "lat": 48.8566,
    "lng": 2.3522,
    "city": "Paris",
    "radius_km": 30,
    "refresh_hours": 12,
}


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    _ensure_data_dir()
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    _ensure_data_dir()
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_events() -> list:
    _ensure_data_dir()
    if EVENTS_FILE.exists():
        try:
            return json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_events(events: list) -> None:
    _ensure_data_dir()
    EVENTS_FILE.write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
