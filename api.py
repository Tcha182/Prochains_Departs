"""API workers using QThread/QObject pattern for SIRI Lite and IDFM open data."""

import logging
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from dotenv import load_dotenv

from models import (
    Favourite, Departure, LineAtStop, StopOnLine, StopAreaMatch,
    normalize, is_same_place,
)

log = logging.getLogger("departs.api")

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN", "")


def get_api_token() -> str:
    """Return the current API token."""
    return API_TOKEN


def set_api_token(token: str) -> None:
    """Update the runtime API token."""
    global API_TOKEN
    API_TOKEN = token


def _sanitize_odsql(value: str) -> str:
    """Remove double quotes from a value for safe use in ODSQL where clauses."""
    return value.replace('"', '')


SIRI_URL = "https://prim.iledefrance-mobilites.fr/marketplace/stop-monitoring"
OPEN_DATA_BASE = "https://data.iledefrance-mobilites.fr/api/explore/v2.1"
STOPS_DATASET = "arrets"
STOP_LINES_DATASET = "arrets-lignes"
LINES_DATASET = "referentiel-des-lignes"

REQUEST_TIMEOUT = 15


def _natural_sort_key(text: str):
    """Sort key for natural ordering: '1' < '2' < '10' < 'T1' < 'T3a'."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]


def _network_error_message(e: requests.RequestException) -> str:
    """Human-readable message, with a hint when the API key is the problem."""
    response = getattr(e, "response", None)
    if response is not None and response.status_code in (401, 403):
        return "Cle API invalide - voir Parametres"
    return f"Erreur réseau: {e}"


def _parse_iso_epoch(iso: str):
    """Parse an ISO timestamp to an epoch, or None."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def _text_search_records(url: str, field: str, query: str,
                         base_params: dict, extra_where: str = "") -> dict:
    """GET records whose `field` matches `query`, probing ODSQL text filters.

    Explore v2.1 has no search() function (that was v1's #search); a where
    clause using it is rejected with 400, which the UI showed as an
    eternally empty stop list. Valid v2.1 spellings differ in matching
    behaviour, so try them best-first and fall through on a 400:
    suggest() matches word prefixes (ideal while typing), like matches
    whole words in the field, and a bare quoted string is the last-resort
    full-text search across all fields.
    """
    q = _sanitize_odsql(query)
    variants = [
        f'suggest({field}, "{q}")',
        f'{field} like "{q}"',
        f'"{q}"',
    ]
    resp = None
    for where in variants:
        params = dict(base_params)
        params["where"] = f"{where} AND {extra_where}" if extra_where else where
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 400:
            log.warning("ODSQL filter rejected (%s): %s", where, resp.text[:200])
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()  # all variants rejected: surface the last 400
    return resp.json()


# ─── Departure Worker ───────────────────────────────────────────────────────

class DepartureWorker(QObject):
    """Fetches real-time departures for all favourites."""

    finished = pyqtSignal(dict)  # {fav_key: [Departure, ...]}
    error = pyqtSignal(str)

    def __init__(self, favourites: list):
        super().__init__()
        self.favourites = favourites

    def run(self):
        try:
            # Group favourites by unique (stop_area_id, line_id) to minimize API calls
            groups = {}
            for fav in self.favourites:
                key = (fav.stop_area_id, fav.line_id)
                if key not in groups:
                    groups[key] = []
                groups[key].append(fav)

            # Fetch groups in parallel so one slow stop doesn't serialize the
            # whole refresh (each request can take up to REQUEST_TIMEOUT).
            group_items = list(groups.items())
            if len(group_items) == 1:
                outcomes = [self._fetch_group(*group_items[0])]
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(group_items))) as pool:
                    outcomes = list(pool.map(
                        lambda item: self._fetch_group(*item), group_items))

            results = {}
            for partial, error in outcomes:
                results.update(partial)
                if error:
                    log.warning("departure fetch failed: %s", error)
                    self.error.emit(error)

            self.finished.emit(results)
        except Exception as e:
            log.exception("unexpected error in DepartureWorker")
            self.error.emit(f"Erreur inattendue: {e}")
            self.finished.emit({})

    def _fetch_group(self, key, favs):
        """Fetch one (stop_area, line) group. Returns (results_dict, error_or_None)."""
        stop_area_id, line_id = key
        try:
            headers = {"apikey": get_api_token()}
            params = {
                "MonitoringRef": f"STIF:StopArea:SP:{stop_area_id}:",
                "LineRef": f"STIF:Line::{line_id}:",
            }
            resp = requests.get(SIRI_URL, headers=headers, params=params,
                                timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            fetch_ts = time.time()
            data = resp.json()

            all_departures = self._parse_departures(data, fetch_ts)

            # Distribute departures to each favourite based on direction
            results = {}
            for fav in favs:
                fav_key = f"{fav.stop_area_id}_{fav.line_id}_{fav.direction}"
                stop_norm = normalize(fav.stop_name)
                matched = [
                    d for d in all_departures
                    if d.eta_seconds >= 0
                    and fav.destination_name.lower() in d.destination.lower()
                    and (not fav.direction or d.direction_ref == fav.direction)
                    and not is_same_place(stop_norm, normalize(d.destination))
                ]
                matched.sort(key=lambda d: d.expected_iso or "")
                results[fav_key] = matched[:5]
            return results, None

        except requests.RequestException as e:
            return {}, _network_error_message(e)
        except (KeyError, ValueError) as e:
            return {}, f"Erreur données: {e}"

    def _parse_departures(self, data, fetch_ts):
        departures = []
        try:
            service = data["Siri"]["ServiceDelivery"]
            delivery = service["StopMonitoringDelivery"][0]
            visits = delivery.get("MonitoredStopVisit", [])
        except (KeyError, IndexError):
            return departures

        # ETAs are computed against the server's clock: the Pi has no RTC, so
        # the local clock can be minutes off right after boot (before NTP).
        server_ts = _parse_iso_epoch(service.get("ResponseTimestamp", "")) or fetch_ts

        for visit in visits:
            journey = visit.get("MonitoredVehicleJourney", {})
            call = journey.get("MonitoredCall", {})

            destination = journey.get("DestinationName") or [{}]
            if isinstance(destination, list):
                destination = destination[0].get("value", "?") if destination else "?"
            elif not isinstance(destination, str):
                destination = "?"

            expected_time = (
                call.get("ExpectedDepartureTime")
                or call.get("ExpectedArrivalTime")
                or call.get("AimedDepartureTime")
            )

            line_name = journey.get("PublishedLineName") or [{}]
            if isinstance(line_name, list):
                line_name = line_name[0].get("value", "") if line_name else ""
            elif not isinstance(line_name, str):
                line_name = ""

            line_ref = journey.get("LineRef", {}).get("value", "")
            dep_status = call.get("DepartureStatus", "")
            vehicle_at_stop = call.get("VehicleAtStop", False)
            direction_ref = journey.get("DirectionRef", {}).get("value", "")

            # Compute eta_seconds relative to the server timestamp
            eta_seconds = 0.0
            if expected_time:
                expected_epoch = _parse_iso_epoch(expected_time)
                if expected_epoch is not None:
                    eta_seconds = expected_epoch - server_ts

            departures.append(Departure(
                line_name=line_name,
                line_id=line_ref,
                destination=destination,
                expected_iso=expected_time or "",
                departure_status=dep_status,
                vehicle_at_stop=vehicle_at_stop,
                direction_ref=direction_ref,
                fetch_timestamp=fetch_ts,
                eta_seconds=eta_seconds,
            ))

        return departures


# ─── Line Search Worker ──────────────────────────────────────────────────────

class LineSearchWorker(QObject):
    """Searches lines by number/name via IDFM referentiel-des-lignes."""

    finished = pyqtSignal(list, int)  # [LineAtStop, ...], search_id
    error = pyqtSignal(str)

    def __init__(self, query: str, mode: str = "", search_id: int = 0):
        super().__init__()
        self.query = query
        self.mode = mode
        self.search_id = search_id

    def run(self):
        try:
            url = f"{OPEN_DATA_BASE}/catalog/datasets/{LINES_DATASET}/records"
            mode_clause = f'transportmode="{_sanitize_odsql(self.mode)}"' if self.mode else ""
            params = {
                "select": "id_line,shortname_line,name_line,transportmode,colourweb_hexa,textcolourweb_hexa",
                "limit": 100,  # API maximum; 20 truncated the mode-wide line list
            }
            if self.query:
                data = _text_search_records(url, "shortname_line", self.query,
                                            params, extra_where=mode_clause)
            else:
                if mode_clause:
                    params["where"] = mode_clause
                resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for record in data.get("results", []):
                line_id = record.get("id_line", "")
                results.append(LineAtStop(
                    line_id=line_id,
                    line_name=record.get("shortname_line") or record.get("name_line") or "",
                    mode=record.get("transportmode") or "",
                    # `or` fallbacks: the API returns null for some lines
                    line_color=record.get("colourweb_hexa") or "FFFFFF",
                    line_text_color=record.get("textcolourweb_hexa") or "000000",
                    route_id=f"IDFM:{line_id}",
                ))

            results.sort(key=lambda l: _natural_sort_key(l.line_name))
            self.finished.emit(results, self.search_id)
        except requests.RequestException as e:
            log.warning("line search failed: %s", e)
            self.error.emit(f"Erreur recherche: {e}")
            self.finished.emit([], self.search_id)
        except Exception as e:
            log.exception("unexpected error in LineSearchWorker")
            self.error.emit(f"Erreur inattendue: {e}")
            self.finished.emit([], self.search_id)


# ─── Stops On Line Worker ───────────────────────────────────────────────────

class StopsOnLineWorker(QObject):
    """Gets all stops on a given line via IDFM arrets-lignes."""

    finished = pyqtSignal(list)  # [StopOnLine, ...]
    error = pyqtSignal(str)

    def __init__(self, route_id: str):
        super().__init__()
        self.route_id = route_id

    def run(self):
        try:
            url = f"{OPEN_DATA_BASE}/catalog/datasets/{STOP_LINES_DATASET}/records"
            params = {
                "where": f'id="{_sanitize_odsql(self.route_id)}"',
                "limit": 100,
                "select": "stop_name,stop_id",
            }
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            seen = set()
            results = []
            for record in data.get("results", []):
                name = record.get("stop_name", "")
                if not name or name in seen:
                    continue
                seen.add(name)
                results.append(StopOnLine(
                    stop_name=name,
                    stop_id=record.get("stop_id", ""),
                ))

            results.sort(key=lambda s: s.stop_name)
            self.finished.emit(results)
        except requests.RequestException as e:
            log.warning("stops-on-line fetch failed: %s", e)
            self.error.emit(f"Erreur arrets: {e}")
            self.finished.emit([])
        except Exception as e:
            log.exception("unexpected error in StopsOnLineWorker")
            self.error.emit(f"Erreur inattendue: {e}")
            self.finished.emit([])


# ─── Resolve + Direction Probe Worker ──────────────────────────────────────

class ResolveAndProbeWorker(QObject):
    """Resolves stop_id → stop_area_id, then probes SIRI for direction names.

    Handles both formats:
    - Bus:       IDFM:423181              → arrid lookup → zdaid
    - Train/RER: IDFM:monomodalStopPlace:43114 → numeric part IS the zdaid
    """

    finished = pyqtSignal(str, str, list)  # (stop_area_id, stop_name, [(dest, dir_ref)])
    error = pyqtSignal(str)

    def __init__(self, stop_id: str, line_id: str):
        super().__init__()
        self.stop_id = stop_id
        self.line_id = line_id

    def run(self):
        try:
            # Step 1: Resolve stop_id → stop_area_id
            try:
                stop_area_id, stop_name = self._resolve()
                if not stop_area_id:
                    self.finished.emit("", "", [])
                    return
            except requests.RequestException as e:
                log.warning("stop resolution failed: %s", e)
                self.error.emit(f"Erreur resolution: {e}")
                self.finished.emit("", "", [])
                return

            # Step 2: Probe SIRI for directions (may fail independently)
            try:
                directions = self._probe_directions(stop_area_id)
                self.finished.emit(stop_area_id, stop_name, directions)
            except requests.RequestException as e:
                log.warning("direction probe failed: %s", e)
                self.error.emit(_network_error_message(e))
                self.finished.emit(stop_area_id, stop_name, [])
        except Exception as e:
            log.exception("unexpected error in ResolveAndProbeWorker")
            self.error.emit(f"Erreur inattendue: {e}")
            self.finished.emit("", "", [])

    def _resolve(self):
        """Resolve stop_id to (stop_area_id, stop_name)."""
        if "monomodalStopPlace" in self.stop_id:
            # Train/RER: numeric part is already the zdaid
            return self.stop_id.split(":")[-1], ""

        # Bus: numeric part is arrid, need to look up zdaid
        arr_id = self.stop_id.split(":")[-1] if ":" in self.stop_id else self.stop_id
        url = f"{OPEN_DATA_BASE}/catalog/datasets/{STOPS_DATASET}/records"
        params = {
            "where": f'arrid="{_sanitize_odsql(arr_id)}"',
            "limit": 1,
            "select": "arrname,zdaid",
        }
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        records = data.get("results", [])
        if records:
            stop_name = records[0].get("arrname", "")
            zda_id = str(records[0].get("zdaid", ""))
            return zda_id, stop_name
        return "", ""

    def _probe_directions(self, stop_area_id):
        """Probe SIRI to discover destination names + direction refs."""
        headers = {"apikey": get_api_token()}
        params = {
            "MonitoringRef": f"STIF:StopArea:SP:{stop_area_id}:",
            "LineRef": f"STIF:Line::{self.line_id}:",
        }
        resp = requests.get(SIRI_URL, headers=headers, params=params,
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        destinations = {}
        try:
            delivery = data["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"][0]
            visits = delivery.get("MonitoredStopVisit", [])
        except (KeyError, IndexError):
            return []

        for visit in visits:
            journey = visit.get("MonitoredVehicleJourney", {})
            dest = journey.get("DestinationName") or [{}]
            if isinstance(dest, list):
                dest = dest[0].get("value", "?") if dest else "?"
            dir_ref = journey.get("DirectionRef", {}).get("value", "")
            if dest and dest != "?" and dest not in destinations:
                destinations[dest] = dir_ref

        return [(name, ref) for name, ref in destinations.items()]


# ─── Stop Area Search Worker ────────────────────────────────────────────────

class StopAreaSearchWorker(QObject):
    """Searches stops by name via arrets-lignes, grouped by (stop name, town)."""

    finished = pyqtSignal(list, int)  # [StopAreaMatch, ...], search_id
    error = pyqtSignal(str)

    def __init__(self, query: str, search_id: int = 0):
        super().__init__()
        self.query = query
        self.search_id = search_id

    def run(self):
        try:
            url = f"{OPEN_DATA_BASE}/catalog/datasets/{STOP_LINES_DATASET}/records"
            data = _text_search_records(url, "stop_name", self.query,
                                        {"limit": 100})

            matches = {}  # (stop_name, town) -> StopAreaMatch
            for record in data.get("results", []):
                name = record.get("stop_name") or ""
                route_id = record.get("id") or ""  # "IDFM:C01371"
                stop_id = record.get("stop_id") or ""
                if not name or not route_id:
                    continue
                town = record.get("nom_commune") or ""
                line_id = route_id.split(":")[-1]
                match = matches.setdefault((name, town),
                                           StopAreaMatch(stop_name=name, town=town))
                match.routes.setdefault(line_id, stop_id)

            results = sorted(matches.values(), key=lambda m: (m.stop_name, m.town))
            self.finished.emit(results, self.search_id)
        except requests.RequestException as e:
            log.warning("stop-name search failed: %s", e)
            self.error.emit(f"Erreur recherche: {e}")
            self.finished.emit([], self.search_id)
        except Exception as e:
            log.exception("unexpected error in StopAreaSearchWorker")
            self.error.emit(f"Erreur inattendue: {e}")
            self.finished.emit([], self.search_id)


# ─── Line Details Worker ────────────────────────────────────────────────────

class LineDetailsWorker(QObject):
    """Fetches full line info (name, colours) for a set of line ids."""

    finished = pyqtSignal(list)  # [LineAtStop, ...]
    error = pyqtSignal(str)

    MAX_LINES = 40  # keep the OR-joined where clause bounded

    def __init__(self, line_ids: list):
        super().__init__()
        self.line_ids = list(line_ids)[:self.MAX_LINES]

    def run(self):
        try:
            if not self.line_ids:
                self.finished.emit([])
                return
            url = f"{OPEN_DATA_BASE}/catalog/datasets/{LINES_DATASET}/records"
            where = " OR ".join(
                f'id_line="{_sanitize_odsql(lid)}"' for lid in self.line_ids
            )
            params = {
                "select": "id_line,shortname_line,name_line,transportmode,colourweb_hexa,textcolourweb_hexa",
                "where": where,
                "limit": 100,
            }
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            results = []
            for record in data.get("results", []):
                line_id = record.get("id_line", "")
                results.append(LineAtStop(
                    line_id=line_id,
                    line_name=record.get("shortname_line") or record.get("name_line") or "",
                    mode=record.get("transportmode") or "",
                    line_color=record.get("colourweb_hexa") or "FFFFFF",
                    line_text_color=record.get("textcolourweb_hexa") or "000000",
                    route_id=f"IDFM:{line_id}",
                ))

            results.sort(key=lambda l: _natural_sort_key(l.line_name))
            self.finished.emit(results)
        except requests.RequestException as e:
            log.warning("line details fetch failed: %s", e)
            self.error.emit(f"Erreur lignes: {e}")
            self.finished.emit([])
        except Exception as e:
            log.exception("unexpected error in LineDetailsWorker")
            self.error.emit(f"Erreur inattendue: {e}")
            self.finished.emit([])


# ─── WiFi Scan Worker ────────────────────────────────────────────────────────

class WiFiScanWorker(QObject):
    """Scans for available WiFi networks using nmcli."""

    finished = pyqtSignal(list)  # [{"ssid", "signal", "security", "in_use"}, ...]

    def run(self):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                 "device", "wifi", "list", "--rescan", "yes"],
                capture_output=True, text=True, timeout=15,
            )
            networks = []
            seen = set()
            for line in result.stdout.strip().splitlines():
                parts = line.split(":")
                if len(parts) < 4:
                    continue
                in_use = parts[0].strip() == "*"
                ssid = parts[1].strip()
                if not ssid or ssid in seen:
                    continue
                seen.add(ssid)
                signal = int(parts[2]) if parts[2].isdigit() else 0
                security = parts[3].strip()
                networks.append({
                    "ssid": ssid,
                    "signal": signal,
                    "security": security,
                    "in_use": in_use,
                })
            # Sort: connected first, then by signal strength descending
            networks.sort(key=lambda n: (-n["in_use"], -n["signal"]))
            self.finished.emit(networks)
        except FileNotFoundError:
            self.finished.emit([{"ssid": "WiFi non disponible", "signal": 0, "security": "", "in_use": False}])
        except (subprocess.TimeoutExpired, OSError):
            self.finished.emit([])
        except Exception:
            self.finished.emit([])


# ─── WiFi Connect Worker ────────────────────────────────────────────────────

class WiFiConnectWorker(QObject):
    """Connects to a WiFi network using nmcli."""

    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, ssid: str, password: str = ""):
        super().__init__()
        self.ssid = ssid
        self.password = password

    def run(self):
        try:
            cmd = ["nmcli", "device", "wifi", "connect", self.ssid]
            if self.password:
                cmd += ["password", self.password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                self.finished.emit(True, "Connecte")
            else:
                msg = result.stderr.strip() or result.stdout.strip() or "Echec de connexion"
                self.finished.emit(False, msg)
        except FileNotFoundError:
            self.finished.emit(False, "WiFi non disponible")
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "Delai d'attente depasse")
        except OSError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, f"Erreur inattendue: {e}")


# ─── Helper: create and start a worker on a thread ──────────────────────────

def start_worker(worker, parent=None):
    """Create a QThread, move worker to it, and start. Returns (thread, worker)."""
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.start()
    return thread, worker
