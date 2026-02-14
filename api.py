"""API workers using QThread/QObject pattern for SIRI Lite and IDFM open data."""

import os
import re
import subprocess
import time
from datetime import datetime

import requests
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from dotenv import load_dotenv

from models import Favourite, Departure, LineAtStop, StopOnLine, normalize, is_same_place

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN", "")

SIRI_URL = "https://prim.iledefrance-mobilites.fr/marketplace/stop-monitoring"
OPEN_DATA_BASE = "https://data.iledefrance-mobilites.fr/api/explore/v2.1"
STOPS_DATASET = "arrets"
STOP_LINES_DATASET = "arrets-lignes"
LINES_DATASET = "referentiel-des-lignes"

REQUEST_TIMEOUT = 15


def _natural_sort_key(text: str):
    """Sort key for natural ordering: '1' < '2' < '10' < 'T1' < 'T3a'."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]


# ─── Departure Worker ───────────────────────────────────────────────────────

class DepartureWorker(QObject):
    """Fetches real-time departures for all favourites."""

    finished = pyqtSignal(dict)  # {fav_key: [Departure, ...]}
    error = pyqtSignal(str)

    def __init__(self, favourites: list):
        super().__init__()
        self.favourites = favourites

    def run(self):
        results = {}
        # Group favourites by unique (stop_area_id, line_id) to minimize API calls
        groups = {}
        for fav in self.favourites:
            key = (fav.stop_area_id, fav.line_id)
            if key not in groups:
                groups[key] = []
            groups[key].append(fav)

        for (stop_area_id, line_id), favs in groups.items():
            try:
                headers = {"apikey": API_TOKEN}
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

            except requests.RequestException as e:
                self.error.emit(f"Erreur réseau: {e}")
            except (KeyError, ValueError) as e:
                self.error.emit(f"Erreur données: {e}")

        self.finished.emit(results)

    def _parse_departures(self, data, fetch_ts):
        departures = []
        try:
            delivery = data["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"][0]
            visits = delivery.get("MonitoredStopVisit", [])
        except (KeyError, IndexError):
            return departures

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

            # Compute eta_seconds from fetch timestamp
            eta_seconds = 0.0
            if expected_time:
                try:
                    dt = datetime.fromisoformat(expected_time.replace("Z", "+00:00"))
                    expected_epoch = dt.timestamp()
                    eta_seconds = expected_epoch - fetch_ts
                except (ValueError, TypeError):
                    pass

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
            where_parts = []
            if self.query:
                where_parts.append(f'search(shortname_line, "{self.query}")')
            if self.mode:
                where_parts.append(f'transportmode="{self.mode}"')
            params = {
                "select": "id_line,shortname_line,name_line,transportmode,colourweb_hexa,textcolourweb_hexa",
                "limit": 20,
            }
            if where_parts:
                params["where"] = " AND ".join(where_parts)
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            results = []
            for record in data.get("results", []):
                line_id = record.get("id_line", "")
                results.append(LineAtStop(
                    line_id=line_id,
                    line_name=record.get("shortname_line") or record.get("name_line", ""),
                    mode=record.get("transportmode", ""),
                    line_color=record.get("colourweb_hexa", "FFFFFF"),
                    line_text_color=record.get("textcolourweb_hexa", "000000"),
                    route_id=f"IDFM:{line_id}",
                ))

            results.sort(key=lambda l: _natural_sort_key(l.line_name))
            self.finished.emit(results, self.search_id)
        except requests.RequestException as e:
            self.error.emit(f"Erreur recherche: {e}")
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
                "where": f'id="{self.route_id}"',
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
            self.error.emit(f"Erreur arrets: {e}")
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
        # Step 1: Resolve stop_id → stop_area_id
        try:
            stop_area_id, stop_name = self._resolve()
            if not stop_area_id:
                self.finished.emit("", "", [])
                return
        except requests.RequestException as e:
            self.error.emit(f"Erreur resolution: {e}")
            self.finished.emit("", "", [])
            return

        # Step 2: Probe SIRI for directions (may fail independently)
        try:
            directions = self._probe_directions(stop_area_id)
            self.finished.emit(stop_area_id, stop_name, directions)
        except requests.RequestException as e:
            self.error.emit(f"Erreur directions: {e}")
            self.finished.emit(stop_area_id, stop_name, [])

    def _resolve(self):
        """Resolve stop_id to (stop_area_id, stop_name)."""
        if "monomodalStopPlace" in self.stop_id:
            # Train/RER: numeric part is already the zdaid
            return self.stop_id.split(":")[-1], ""

        # Bus: numeric part is arrid, need to look up zdaid
        arr_id = self.stop_id.split(":")[-1] if ":" in self.stop_id else self.stop_id
        url = f"{OPEN_DATA_BASE}/catalog/datasets/{STOPS_DATASET}/records"
        params = {
            "where": f'arrid="{arr_id}"',
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
        headers = {"apikey": API_TOKEN}
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


# ─── Helper: create and start a worker on a thread ──────────────────────────

def start_worker(worker, parent=None):
    """Create a QThread, move worker to it, and start. Returns (thread, worker)."""
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.start()
    return thread, worker
