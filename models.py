"""Data classes, shared helpers, and JSON persistence for favourites."""

import json
import os
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import List

FAVOURITES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favourites.json")


def normalize(text: str) -> str:
    """Strip accents, collapse dashes/apostrophes/brackets to spaces, lowercase."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[-''\s<>()]+", " ", text)
    return text.strip().lower()


def is_same_place(a: str, b: str) -> bool:
    """Check if two normalized place names refer to the same location."""
    if not a or not b:
        return False
    return a in b or b in a


@dataclass
class Favourite:
    stop_area_id: str
    stop_name: str
    line_id: str
    line_name: str
    line_color: str = "FFFFFF"
    line_text_color: str = "000000"
    direction: str = ""
    destination_name: str = ""


@dataclass
class Departure:
    line_name: str
    line_id: str
    destination: str
    expected_iso: str
    departure_status: str = ""
    vehicle_at_stop: bool = False
    direction_ref: str = ""
    fetch_timestamp: float = 0.0
    eta_seconds: float = 0.0


@dataclass
class LineAtStop:
    line_id: str
    line_name: str
    mode: str = ""
    line_color: str = "FFFFFF"
    line_text_color: str = "000000"
    route_id: str = ""


@dataclass
class StopOnLine:
    stop_name: str
    stop_id: str = ""


def load_favourites() -> List[Favourite]:
    """Load favourites from JSON file."""
    if not os.path.exists(FAVOURITES_PATH):
        return []
    try:
        with open(FAVOURITES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Favourite(**item) for item in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def save_favourites(favourites: List[Favourite]) -> None:
    """Save favourites to JSON file."""
    with open(FAVOURITES_PATH, "w", encoding="utf-8") as f:
        json.dump([asdict(fav) for fav in favourites], f, ensure_ascii=False, indent=2)


# ─── App Settings ─────────────────────────────────────────────────────────────

@dataclass
class AppSettings:
    theme: str = "dark"            # "dark" or "light"
    sleep_delay_minutes: int = 10  # 0 = disabled

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def load_settings() -> AppSettings:
    """Load app settings from JSON file, with graceful fallback to defaults."""
    if not os.path.exists(SETTINGS_PATH):
        return AppSettings()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppSettings(**{k: v for k, v in data.items() if k in AppSettings.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError, KeyError):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    """Save app settings to JSON file."""
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=2)


def save_api_token(token: str) -> None:
    """Write API token to .env file and update runtime variable."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"API_TOKEN={token}\n")
    # Update runtime variable (local import to avoid circular)
    import api
    api.API_TOKEN = token
