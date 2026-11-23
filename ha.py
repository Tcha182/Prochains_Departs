"""Home Assistant client and workers for the heating screen.

The radiators (Zigbee thermostatic valves) are driven by a Home Assistant
instance running on the same Pi, installed as a container by pi-system.sh.
This module only uses its HTTP API on localhost: list the `climate`
entities, set a target temperature, change a mode.

No password or token to type on the touchscreen: Home Assistant is set up
(see pi-system.sh) to trust logins coming from the Pi itself, so the app
signs in on its own and keeps the resulting refresh token in ha_auth.json.
For a Home Assistant running elsewhere, set HA_URL and HA_TOKEN (a
long-lived access token) in .env instead.
"""

import json
import logging
import os
import socket
import threading
import time

import requests
from PyQt5.QtCore import QObject, pyqtSignal
from dotenv import load_dotenv

from models import Radiator

log = logging.getLogger("departs.ha")

load_dotenv()
DEFAULT_URL = "http://127.0.0.1:8123"
HA_URL = (os.getenv("HA_URL", "") or DEFAULT_URL).rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
# Written by pi-system.sh when it installs Home Assistant on this Pi.
HA_UNIT_PATH = "/etc/systemd/system/home-assistant.service"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_PATH = os.path.join(APP_DIR, "ha_auth.json")

REQUEST_TIMEOUT = (3, 10)  # (connect, read) seconds

# Outcome of a request, as reported to the heating screen.
OK = "ok"
UNREACHABLE = "unreachable"  # not answering: still starting, or not installed
ONBOARDING = "onboarding"    # running, but its owner account isn't created yet
AUTH = "auth"                # it won't let the app sign in
ERROR = "error"              # anything else


def is_configured() -> bool:
    """Whether this device is meant to control a Home Assistant at all
    (decides if the heating button is shown)."""
    return bool(os.getenv("HA_URL")) or os.path.exists(HA_UNIT_PATH)


def setup_url() -> str:
    """Address of the Home Assistant web UI as seen from a phone or a PC,
    shown on screen when something has to be set up there."""
    if HA_URL != DEFAULT_URL:
        return HA_URL
    host = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # Connecting a UDP socket sends nothing: it only makes the
            # kernel pick the outgoing interface, whose address we read.
            sock.connect(("192.0.2.1", 9))
            host = sock.getsockname()[0]
    except OSError:
        pass
    if not host or host.startswith("127."):
        host = f"{socket.gethostname()}.local"
    return f"http://{host}:8123"


class HAError(Exception):
    def __init__(self, status: str, detail: str = ""):
        super().__init__(detail or status)
        self.status = status
        self.detail = detail


# ─── HTTP + sign-in ──────────────────────────────────────────────────────────

def _http(method: str, path: str, token: str = "", **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        return requests.request(method, HA_URL + path, headers=headers,
                                timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise HAError(UNREACHABLE, type(e).__name__) from e


def _json(resp):
    try:
        return resp.json()
    except ValueError as e:
        raise HAError(ERROR, f"réponse illisible ({resp.status_code})") from e


# Access tokens last 30 min; workers run on short-lived threads, so the
# current one is shared here.
_token_lock = threading.Lock()
_access_token = ""
_access_expiry = 0.0  # time.monotonic()


def _client_id() -> str:
    # Home Assistant only checks that this is a URL; the instance's own
    # address is what its web UI uses too.
    return HA_URL + "/"


def _load_refresh_token() -> str:
    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("url") == HA_URL:  # a token is only good for its instance
            return data.get("refresh_token", "")
    except (OSError, ValueError, AttributeError):
        pass
    return ""


def _save_refresh_token(token: str) -> None:
    tmp_path = AUTH_PATH + ".tmp"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"url": HA_URL, "refresh_token": token}, f)
        os.replace(tmp_path, AUTH_PATH)
    except OSError as e:
        # Not fatal: the app just signs in again after a restart.
        log.warning("could not save Home Assistant token: %s", e)


def _forget_refresh_token() -> None:
    try:
        os.remove(AUTH_PATH)
    except OSError:
        pass


def _store_access_token(data: dict) -> str:
    global _access_token, _access_expiry
    _access_token = data["access_token"]
    # Renew a minute early rather than have a request rejected.
    _access_expiry = time.monotonic() + max(60, int(data.get("expires_in", 1800)) - 60)
    return _access_token


def _forget_access_token() -> None:
    global _access_token, _access_expiry
    with _token_lock:
        _access_token = ""
        _access_expiry = 0.0


def _refresh(refresh_token: str) -> str:
    """Trade the stored refresh token for an access token. Returns "" when
    Home Assistant no longer knows it (revoked, or a reinstalled instance)."""
    resp = _http("POST", "/auth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _client_id(),
    })
    if resp.status_code in (400, 401, 403):
        log.info("stored Home Assistant token rejected, signing in again")
        _forget_refresh_token()
        return ""
    if resp.status_code != 200:
        raise HAError(ERROR, f"HTTP {resp.status_code}")
    return _store_access_token(_json(resp))


def _login() -> str:
    """Sign in through the `trusted_networks` provider (no password: the
    request comes from the Pi itself) and keep the refresh token."""
    client_id = _client_id()
    resp = _http("POST", "/auth/login_flow", json={
        "client_id": client_id,
        "handler": ["trusted_networks", None],
        "redirect_uri": client_id,
    })
    if resp.status_code != 200:
        # The provider isn't enabled in configuration.yaml.
        raise HAError(AUTH, f"HTTP {resp.status_code}")
    flow = _json(resp)

    if flow.get("type") == "form":
        # Asked to pick a user (several accounts, or none yet).
        users = [opt[0] for field in flow.get("data_schema", [])
                 if field.get("name") == "user"
                 for opt in field.get("options", [])]
        if not users:
            raise HAError(ONBOARDING)
        resp = _http("POST", f"/auth/login_flow/{flow.get('flow_id')}",
                     json={"client_id": client_id, "user": users[0]})
        if resp.status_code != 200:
            raise HAError(AUTH, f"HTTP {resp.status_code}")
        flow = _json(resp)

    if flow.get("type") != "create_entry" or not flow.get("result"):
        raise HAError(AUTH, str(flow.get("reason") or flow.get("type") or ""))

    resp = _http("POST", "/auth/token", data={
        "grant_type": "authorization_code",
        "code": flow["result"],
        "client_id": client_id,
    })
    if resp.status_code != 200:
        raise HAError(AUTH, f"HTTP {resp.status_code}")
    data = _json(resp)
    token = _store_access_token(data)
    if data.get("refresh_token"):
        _save_refresh_token(data["refresh_token"])
    log.info("signed in to Home Assistant")
    return token


def _get_token() -> str:
    if HA_TOKEN:
        return HA_TOKEN
    with _token_lock:
        if _access_token and time.monotonic() < _access_expiry:
            return _access_token
        refresh_token = _load_refresh_token()
        if refresh_token:
            token = _refresh(refresh_token)
            if token:
                return token
        return _login()


def _api(method: str, path: str, **kwargs):
    """Authenticated API call. Signs in again once if the token is refused."""
    resp = _http(method, path, token=_get_token(), **kwargs)
    if resp.status_code == 401 and not HA_TOKEN:
        _forget_access_token()
        resp = _http(method, path, token=_get_token(), **kwargs)
    if resp.status_code == 401:
        raise HAError(AUTH, "HTTP 401")
    if resp.status_code >= 400:
        raise HAError(ERROR, f"HTTP {resp.status_code}")
    return resp


# ─── Radiators ───────────────────────────────────────────────────────────────

def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_radiators(states: list, rooms: dict = None) -> list:
    """Build Radiators from Home Assistant's state list (`climate` entities).
    `rooms` is {entity_id: area name}, see fetch_rooms()."""
    rooms = rooms or {}
    radiators = []
    for state in states:
        entity_id = state.get("entity_id", "")
        if not entity_id.startswith("climate."):
            continue
        attrs = state.get("attributes") or {}
        mode = state.get("state", "")
        available = mode not in ("unavailable", "unknown", "")
        name = attrs.get("friendly_name") or entity_id.split(".", 1)[1]
        # Zigbee valves are named "<device name> Thermostat": on a screen
        # that only lists thermostats, the room is the useful part.
        if name.lower().endswith(" thermostat"):
            name = name[:-len(" thermostat")].strip() or name
        radiators.append(Radiator(
            entity_id=entity_id,
            name=name,
            available=available,
            mode=mode if available else "",
            modes=list(attrs.get("hvac_modes") or []),
            current_temp=_number(attrs.get("current_temperature")),
            target_temp=_number(attrs.get("temperature")),
            min_temp=_number(attrs.get("min_temp"), 5.0),
            max_temp=_number(attrs.get("max_temp"), 30.0),
            step=_number(attrs.get("target_temp_step")) or 0.5,
            heating=attrs.get("hvac_action") == "heating",
            room=rooms.get(entity_id) or "",
        ))
    radiators.sort(key=lambda r: r.name.lower())
    return radiators


# The state list doesn't say which room a device is in; a template does.
_ROOMS_TEMPLATE = (
    "{% set ns = namespace(rooms={}) %}"
    "{% for s in states.climate %}"
    "{% set ns.rooms = dict(ns.rooms, **{s.entity_id: area_name(s.entity_id)}) %}"
    "{% endfor %}{{ ns.rooms | to_json }}"
)
_last_rooms = {}  # kept so a failed lookup doesn't reshuffle the screen


def fetch_rooms() -> dict:
    """{entity_id: area name} for the thermostats. Rooms only organise the
    screen: if they can't be read, the last known ones are used."""
    global _last_rooms
    try:
        resp = _api("POST", "/api/template", json={"template": _ROOMS_TEMPLATE})
        rooms = json.loads(resp.text)
        if isinstance(rooms, dict):
            _last_rooms = rooms
    except (HAError, ValueError, TypeError) as e:
        log.warning("could not read Home Assistant rooms: %s", e)
    return _last_rooms


def fetch_radiators() -> list:
    states = _json(_api("GET", "/api/states"))
    return parse_radiators(states, fetch_rooms())


def set_temperature(entity_id: str, temperature: float) -> None:
    _api("POST", "/api/services/climate/set_temperature",
         json={"entity_id": entity_id, "temperature": temperature})


def set_hvac_mode(entity_id: str, mode: str) -> None:
    _api("POST", "/api/services/climate/set_hvac_mode",
         json={"entity_id": entity_id, "hvac_mode": mode})


# ─── Workers ─────────────────────────────────────────────────────────────────

class HeatingWorker(QObject):
    """Fetches the radiators' current state."""

    finished = pyqtSignal(str, list, str)  # status, [Radiator], detail

    def run(self):
        try:
            self.finished.emit(OK, fetch_radiators(), "")
        except HAError as e:
            if e.status != UNREACHABLE:
                log.warning("Home Assistant: %s (%s)", e.status, e.detail)
            self.finished.emit(e.status, [], e.detail)
        except Exception as e:
            log.exception("unexpected error in HeatingWorker")
            self.finished.emit(ERROR, [], str(e))


class HeatingCommandWorker(QObject):
    """Sends a new mode and/or target temperature to one radiator."""

    finished = pyqtSignal(str, bool)  # entity_id, success

    def __init__(self, entity_id: str, temperature: float = None,
                 mode: str = None):
        super().__init__()
        self.entity_id = entity_id
        self.temperature = temperature
        self.mode = mode

    def run(self):
        try:
            if self.mode is not None:
                set_hvac_mode(self.entity_id, self.mode)
            if self.temperature is not None:
                set_temperature(self.entity_id, self.temperature)
            self.finished.emit(self.entity_id, True)
        except HAError as e:
            log.warning("command to %s failed: %s (%s)",
                        self.entity_id, e.status, e.detail)
            self.finished.emit(self.entity_id, False)
        except Exception:
            log.exception("unexpected error in HeatingCommandWorker")
            self.finished.emit(self.entity_id, False)
