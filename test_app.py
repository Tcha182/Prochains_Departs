"""Comprehensive tests for the departure display app.

Run:  pytest test_app.py -v
Live: pytest test_app.py -v -m live  (requires network + API key)
"""

import json
import os
import sys
import time
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import pytest

# ── Need a QApplication before any widget imports ────────────────────────────
from PyQt5.QtWidgets import QApplication

# Singleton QApplication for all tests
_app = QApplication.instance() or QApplication(sys.argv)

from models import (
    Favourite, Departure, LineAtStop, StopOnLine,
    load_favourites, save_favourites, FAVOURITES_PATH,
)
from api import (
    DepartureWorker, LineSearchWorker, StopsOnLineWorker,
    ResolveAndProbeWorker, start_worker,
)
from widgets import DepartureCard, FavouriteGroup, HomeScreen, SearchScreen
from styles import DARK_THEME


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_fav_path(tmp_path):
    """Temporarily redirect FAVOURITES_PATH to a temp file."""
    path = str(tmp_path / "favourites.json")
    with patch("models.FAVOURITES_PATH", path):
        yield path


@pytest.fixture
def sample_favourite():
    return Favourite(
        stop_area_id="50980",
        stop_name="Pavillon Halevy",
        line_id="C02000",
        line_name="259",
        line_color="3C91DC",
        line_text_color="FFFFFF",
        direction="1",
        destination_name="Saint-Germain-En-Laye",
    )


@pytest.fixture
def sample_departure():
    ts = time.time()
    return Departure(
        line_name="259",
        line_id="STIF:Line::C02000:",
        destination="Saint-Germain-En-Laye <RER>",
        expected_iso=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        departure_status="onTime",
        vehicle_at_stop=False,
        direction_ref="1",
        fetch_timestamp=ts,
        eta_seconds=300.0,
    )


def make_siri_response(departures_data):
    """Build a fake SIRI API response with given departure data."""
    visits = []
    for d in departures_data:
        visits.append({
            "MonitoredVehicleJourney": {
                "PublishedLineName": [{"value": d.get("line_name", "259")}],
                "DestinationName": [{"value": d.get("destination", "?")}],
                "LineRef": {"value": d.get("line_ref", "STIF:Line::C02000:")},
                "DirectionRef": {"value": d.get("direction_ref", "1")},
                "MonitoredCall": {
                    "ExpectedDepartureTime": d.get("expected_time"),
                    "DepartureStatus": d.get("status", "onTime"),
                    "VehicleAtStop": d.get("vehicle_at_stop", False),
                },
            }
        })
    return {
        "Siri": {
            "ServiceDelivery": {
                "StopMonitoringDelivery": [{
                    "MonitoredStopVisit": visits,
                }]
            }
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MODELS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestFavourite:
    def test_creation_with_defaults(self):
        fav = Favourite(stop_area_id="123", stop_name="Test", line_id="A", line_name="Bus A")
        assert fav.line_color == "FFFFFF"
        assert fav.line_text_color == "000000"
        assert fav.direction == ""
        assert fav.destination_name == ""

    def test_creation_full(self, sample_favourite):
        assert sample_favourite.stop_area_id == "50980"
        assert sample_favourite.line_color == "3C91DC"


class TestDeparture:
    def test_creation(self, sample_departure):
        assert sample_departure.line_name == "259"
        assert sample_departure.eta_seconds == 300.0
        assert sample_departure.fetch_timestamp > 0

    def test_defaults(self):
        dep = Departure(line_name="X", line_id="Y", destination="Z", expected_iso="")
        assert dep.departure_status == ""
        assert dep.vehicle_at_stop is False
        assert dep.fetch_timestamp == 0.0
        assert dep.eta_seconds == 0.0


class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_fav_path, sample_favourite):
        favs = [sample_favourite]
        with patch("models.FAVOURITES_PATH", tmp_fav_path):
            save_favourites(favs)
            loaded = load_favourites()
        assert len(loaded) == 1
        assert loaded[0].stop_area_id == "50980"
        assert loaded[0].line_color == "3C91DC"
        assert loaded[0].destination_name == "Saint-Germain-En-Laye"

    def test_load_nonexistent_file(self, tmp_path):
        path = str(tmp_path / "does_not_exist.json")
        with patch("models.FAVOURITES_PATH", path):
            assert load_favourites() == []

    def test_load_corrupt_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("NOT JSON{{{")
        with patch("models.FAVOURITES_PATH", path):
            assert load_favourites() == []

    def test_load_empty_list(self, tmp_path):
        path = str(tmp_path / "empty.json")
        with open(path, "w") as f:
            json.dump([], f)
        with patch("models.FAVOURITES_PATH", path):
            assert load_favourites() == []

    def test_save_multiple(self, tmp_fav_path):
        favs = [
            Favourite("1", "Stop A", "L1", "Bus 1"),
            Favourite("2", "Stop B", "L2", "Bus 2", line_color="FF0000"),
        ]
        with patch("models.FAVOURITES_PATH", tmp_fav_path):
            save_favourites(favs)
            loaded = load_favourites()
        assert len(loaded) == 2
        assert loaded[1].line_color == "FF0000"

    def test_unicode_persistence(self, tmp_fav_path):
        fav = Favourite("1", "Pavillon Halévy", "L1", "259", destination_name="Saint-Germain-en-Laye <RER>")
        with patch("models.FAVOURITES_PATH", tmp_fav_path):
            save_favourites([fav])
            loaded = load_favourites()
        assert loaded[0].stop_name == "Pavillon Halévy"
        assert loaded[0].destination_name == "Saint-Germain-en-Laye <RER>"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. API WORKER TESTS (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDepartureWorkerParsing:
    """Test the _parse_departures method directly (no HTTP needed)."""

    def test_parse_basic_departure(self):
        worker = DepartureWorker([])
        now_iso = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        data = make_siri_response([{
            "line_name": "259",
            "destination": "Saint-Germain-En-Laye <RER>",
            "expected_time": now_iso,
            "status": "onTime",
        }])
        fetch_ts = time.time()
        departures = worker._parse_departures(data, fetch_ts)
        assert len(departures) == 1
        d = departures[0]
        assert d.line_name == "259"
        assert d.destination == "Saint-Germain-En-Laye <RER>"
        assert d.departure_status == "onTime"
        assert d.fetch_timestamp == fetch_ts
        # eta_seconds should be roughly 600 (10 min)
        assert 550 < d.eta_seconds < 650

    def test_parse_empty_response(self):
        worker = DepartureWorker([])
        data = {"Siri": {"ServiceDelivery": {"StopMonitoringDelivery": [{}]}}}
        assert worker._parse_departures(data, time.time()) == []

    def test_parse_missing_keys(self):
        worker = DepartureWorker([])
        assert worker._parse_departures({}, time.time()) == []
        assert worker._parse_departures({"Siri": {}}, time.time()) == []

    def test_parse_multiple_departures(self):
        worker = DepartureWorker([])
        t1 = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        data = make_siri_response([
            {"destination": "Saint-Germain", "expected_time": t1},
            {"destination": "Nanterre", "expected_time": t2},
        ])
        deps = worker._parse_departures(data, time.time())
        assert len(deps) == 2
        assert deps[0].destination == "Saint-Germain"
        assert deps[1].destination == "Nanterre"

    def test_parse_vehicle_at_stop(self):
        worker = DepartureWorker([])
        data = make_siri_response([{
            "destination": "Test",
            "expected_time": datetime.now(timezone.utc).isoformat(),
            "vehicle_at_stop": True,
        }])
        deps = worker._parse_departures(data, time.time())
        assert deps[0].vehicle_at_stop is True

    def test_eta_seconds_computation(self):
        worker = DepartureWorker([])
        future = datetime.now(timezone.utc) + timedelta(minutes=7)
        data = make_siri_response([{
            "destination": "Test",
            "expected_time": future.isoformat(),
        }])
        fetch_ts = time.time()
        deps = worker._parse_departures(data, fetch_ts)
        assert deps[0].fetch_timestamp == fetch_ts
        # Should be ~420 seconds (7 min)
        assert 380 < deps[0].eta_seconds < 460


class TestDepartureWorkerRun:
    """Test the full run() method with mocked HTTP."""

    @patch("api.requests.get")
    def test_run_groups_by_stop_and_line(self, mock_get, sample_favourite):
        """Two favourites with same stop+line should make only 1 API call."""
        fav1 = sample_favourite
        fav2 = Favourite(
            stop_area_id="50980", stop_name="Pavillon Halevy",
            line_id="C02000", line_name="259",
            direction="2", destination_name="Nanterre",
        )
        t1 = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_siri_response([
            {"destination": "Saint-Germain-En-Laye <RER>", "expected_time": t1},
            {"destination": "Nanterre - Papeteries", "expected_time": t2},
        ])
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        worker = DepartureWorker([fav1, fav2])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()

        # Only 1 API call for same (stop_area_id, line_id)
        assert mock_get.call_count == 1

        # Each favourite gets its own filtered departures
        key1 = f"50980_C02000_{fav1.direction}"
        key2 = f"50980_C02000_{fav2.direction}"
        assert key1 in results
        assert key2 in results
        # Saint-Germain favourite gets Saint-Germain departure
        assert len(results[key1]) >= 1
        assert "Saint-Germain" in results[key1][0].destination

    @patch("api.requests.get")
    def test_run_handles_http_error(self, mock_get, sample_favourite):
        import requests as req
        mock_get.side_effect = req.ConnectionError("Network down")
        worker = DepartureWorker([sample_favourite])
        errors = []
        results = {}
        worker.error.connect(lambda msg: errors.append(msg))
        worker.finished.connect(lambda r: results.update(r))
        worker.run()
        assert len(errors) == 1
        assert "réseau" in errors[0].lower() or "network" in errors[0].lower()

    @patch("api.requests.get")
    def test_run_limits_to_5_departures(self, mock_get, sample_favourite):
        deps_data = [
            {"destination": "Saint-Germain-En-Laye <RER>",
             "expected_time": (datetime.now(timezone.utc) + timedelta(minutes=i)).isoformat()}
            for i in range(1, 9)
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_siri_response(deps_data)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        worker = DepartureWorker([sample_favourite])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()

        key = f"50980_C02000_{sample_favourite.direction}"
        assert len(results[key]) <= 5

    @patch("api.requests.get")
    def test_run_filters_by_direction(self, mock_get):
        """Favourite with direction set should only get departures for that direction."""
        fav = Favourite(
            stop_area_id="50980", stop_name="Test",
            line_id="C02000", line_name="259",
            direction="1", destination_name="",
        )
        t1 = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_siri_response([
            {"destination": "Saint-Germain", "expected_time": t1, "direction_ref": "1"},
            {"destination": "Nanterre", "expected_time": t2, "direction_ref": "2"},
        ])
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        worker = DepartureWorker([fav])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()

        key = "50980_C02000_1"
        assert len(results[key]) == 1
        assert results[key][0].destination == "Saint-Germain"

    @patch("api.requests.get")
    def test_run_empty_direction_shows_all(self, mock_get):
        """Favourite with direction="" should get all departures."""
        fav = Favourite(
            stop_area_id="50980", stop_name="Test",
            line_id="C02000", line_name="259",
            direction="", destination_name="",
        )
        t1 = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_siri_response([
            {"destination": "Saint-Germain", "expected_time": t1, "direction_ref": "1"},
            {"destination": "Nanterre", "expected_time": t2, "direction_ref": "2"},
        ])
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        worker = DepartureWorker([fav])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()

        key = "50980_C02000_"
        assert len(results[key]) == 2

    @patch("api.requests.get")
    def test_run_filters_terminus_arrivals(self, mock_get):
        """Departures whose destination matches the stop name (terminus) are filtered out."""
        fav = Favourite(
            stop_area_id="43114", stop_name="Saint-Germain-en-Laye",
            line_id="C01742", line_name="A",
            direction="1", destination_name="",
        )
        t1 = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        t3 = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_siri_response([
            {"destination": "Saint-Germain-en-Laye", "expected_time": t1, "direction_ref": "1"},
            {"destination": "Marne-la-Vallee Chessy", "expected_time": t2, "direction_ref": "1"},
            {"destination": "Saint-Germain-En-Laye <RER>", "expected_time": t3, "direction_ref": "1"},
        ])
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        worker = DepartureWorker([fav])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()

        key = "43114_C01742_1"
        # Only "Marne-la-Vallee Chessy" should remain; both Saint-Germain variants filtered
        assert len(results[key]) == 1
        assert "Marne" in results[key][0].destination


class TestLineSearchWorker:
    @patch("api.requests.get")
    def test_search_returns_results(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id_line": "C02000",
                    "shortname_line": "259",
                    "name_line": "Bus 259",
                    "transportmode": "bus",
                    "colourweb_hexa": "3C91DC",
                    "textcolourweb_hexa": "FFFFFF",
                },
                {
                    "id_line": "C02001",
                    "shortname_line": "259A",
                    "name_line": "Bus 259A",
                    "transportmode": "bus",
                    "colourweb_hexa": "FF0000",
                    "textcolourweb_hexa": "FFFFFF",
                },
            ]
        }
        mock_get.return_value = mock_resp

        worker = LineSearchWorker("259")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()

        assert len(results) == 2
        assert results[0].line_id == "C02000"
        assert results[0].line_name == "259"
        assert results[0].line_color == "3C91DC"
        assert results[0].mode == "bus"
        assert results[0].route_id == "IDFM:C02000"

    @patch("api.requests.get")
    def test_search_uses_shortname_line(self, mock_get):
        """Verify the API call searches shortname_line."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp

        worker = LineSearchWorker("259")
        worker.finished.connect(lambda r: None)
        worker.run()

        call_args = mock_get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert "shortname_line" in params["where"]
        assert "id_line" in params["select"]

    @patch("api.requests.get")
    def test_search_http_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError("fail")
        worker = LineSearchWorker("259")
        results = []
        errors = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.error.connect(lambda e: errors.append(e))
        worker.run()
        assert results == []
        assert len(errors) == 1


class TestStopsOnLineWorker:
    @patch("api.requests.get")
    def test_returns_stops(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"stop_name": "Pavillon Halevy", "stop_id": "IDFM:423181"},
                {"stop_name": "Nanterre - Papeteries", "stop_id": "IDFM:423200"},
                {"stop_name": "Bas Prunay", "stop_id": "IDFM:423100"},
            ]
        }
        mock_get.return_value = mock_resp

        worker = StopsOnLineWorker("IDFM:C02000")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()

        assert len(results) == 3
        # Should be sorted alphabetically
        assert results[0].stop_name == "Bas Prunay"
        assert results[1].stop_name == "Nanterre - Papeteries"
        assert results[2].stop_name == "Pavillon Halevy"
        assert results[2].stop_id == "IDFM:423181"

    @patch("api.requests.get")
    def test_deduplicates_by_stop_name(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"stop_name": "Pavillon Halevy", "stop_id": "IDFM:423181"},
                {"stop_name": "Pavillon Halevy", "stop_id": "IDFM:423182"},
            ]
        }
        mock_get.return_value = mock_resp

        worker = StopsOnLineWorker("IDFM:C02000")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()
        assert len(results) == 1

    @patch("api.requests.get")
    def test_uses_correct_query(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp

        worker = StopsOnLineWorker("IDFM:C02000")
        worker.finished.connect(lambda r: None)
        worker.run()

        call_args = mock_get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert params["where"] == 'id="IDFM:C02000"'
        assert "stop_name" in params["select"]
        assert "stop_id" in params["select"]

    @patch("api.requests.get")
    def test_http_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError("fail")
        worker = StopsOnLineWorker("IDFM:C02000")
        results = []
        errors = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.error.connect(lambda e: errors.append(e))
        worker.run()
        assert results == []
        assert len(errors) == 1


class TestResolveAndProbeWorker:
    @patch("api.requests.get")
    def test_resolves_bus_and_probes(self, mock_get):
        """Bus stop: arrid lookup → zdaid, then SIRI probe."""
        arrets_resp = MagicMock()
        arrets_resp.raise_for_status = MagicMock()
        arrets_resp.json.return_value = {
            "results": [{"arrname": "Pavillon Halevy", "zdaid": "50980"}]
        }
        siri_resp = MagicMock()
        siri_resp.raise_for_status = MagicMock()
        siri_resp.json.return_value = make_siri_response([
            {"destination": "Saint-Germain-En-Laye <RER>", "expected_time": "2025-01-01T12:00:00+01:00", "direction_ref": "1"},
            {"destination": "Nanterre - Papeteries", "expected_time": "2025-01-01T12:05:00+01:00", "direction_ref": "2"},
        ])
        mock_get.side_effect = [arrets_resp, siri_resp]

        worker = ResolveAndProbeWorker("IDFM:423181", "C02000")
        results = []
        worker.finished.connect(lambda sa, sn, dirs: results.append((sa, sn, dirs)))
        worker.run()

        assert len(results) == 1
        stop_area_id, stop_name, directions = results[0]
        assert stop_area_id == "50980"
        assert stop_name == "Pavillon Halevy"
        assert len(directions) == 2

        # First call should be arrets lookup by arrid
        first_call = mock_get.call_args_list[0]
        first_params = first_call.kwargs.get("params") or first_call[1].get("params")
        assert 'arrid="423181"' in first_params["where"]

    @patch("api.requests.get")
    def test_resolves_train_directly(self, mock_get):
        """Train/RER: monomodalStopPlace numeric part IS the zdaid - no arrets lookup."""
        siri_resp = MagicMock()
        siri_resp.raise_for_status = MagicMock()
        siri_resp.json.return_value = make_siri_response([
            {"destination": "Cergy-Le Haut", "expected_time": "2025-01-01T12:00:00+01:00", "direction_ref": "1"},
            {"destination": "Boissy-Saint-Leger", "expected_time": "2025-01-01T12:05:00+01:00", "direction_ref": "2"},
        ])
        mock_get.return_value = siri_resp

        worker = ResolveAndProbeWorker("IDFM:monomodalStopPlace:470195", "C01742")
        results = []
        worker.finished.connect(lambda sa, sn, dirs: results.append((sa, sn, dirs)))
        worker.run()

        assert len(results) == 1
        stop_area_id, stop_name, directions = results[0]
        assert stop_area_id == "470195"
        assert stop_name == ""  # not resolved for train, use stop_name from arrets-lignes
        assert len(directions) == 2

        # Only 1 API call (SIRI probe), no arrets lookup
        assert mock_get.call_count == 1

    @patch("api.requests.get")
    def test_no_arrets_result(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp

        worker = ResolveAndProbeWorker("IDFM:999999", "C02000")
        results = []
        worker.finished.connect(lambda sa, sn, dirs: results.append((sa, sn, dirs)))
        worker.run()

        assert len(results) == 1
        assert results[0] == ("", "", [])

    @patch("api.requests.get")
    def test_http_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError("fail")
        worker = ResolveAndProbeWorker("IDFM:423181", "C02000")
        results = []
        errors = []
        worker.finished.connect(lambda sa, sn, dirs: results.append((sa, sn, dirs)))
        worker.error.connect(lambda e: errors.append(e))
        worker.run()
        assert len(results) == 1
        assert results[0] == ("", "", [])
        assert len(errors) == 1

    @patch("api.requests.get")
    def test_probe_failure_still_returns_stop_area(self, mock_get):
        """If SIRI probe fails after successful resolution, stop_area_id is still returned."""
        import requests as req
        arrets_resp = MagicMock()
        arrets_resp.raise_for_status = MagicMock()
        arrets_resp.json.return_value = {
            "results": [{"arrname": "La Defense", "zdaid": "71517"}]
        }
        mock_get.side_effect = [arrets_resp, req.ConnectionError("SIRI down")]

        worker = ResolveAndProbeWorker("IDFM:470549", "C01740")
        results = []
        errors = []
        worker.finished.connect(lambda sa, sn, dirs: results.append((sa, sn, dirs)))
        worker.error.connect(lambda e: errors.append(e))
        worker.run()

        assert len(results) == 1
        stop_area_id, stop_name, directions = results[0]
        assert stop_area_id == "71517"
        assert stop_name == "La Defense"
        assert directions == []
        assert len(errors) == 1  # error emitted but stop_area_id preserved


# ═══════════════════════════════════════════════════════════════════════════════
# 3. WIDGET TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestDepartureCard:
    def test_countdown_future_departure(self):
        dep = Departure(
            line_name="259", line_id="X", destination="Test", expected_iso="",
            fetch_timestamp=time.time(), eta_seconds=660.0,  # 11 min - avoids rounding edge
        )
        card = DepartureCard(dep, "3C91DC", "FFFFFF")
        # Should show ~10-11 min depending on exact timing
        text = card.countdown_label.text()
        assert text in ("10 min", "11 min"), f"Expected 10 or 11 min, got '{text}'"

    def test_countdown_imminent(self):
        dep = Departure(
            line_name="259", line_id="X", destination="Test", expected_iso="",
            fetch_timestamp=time.time(), eta_seconds=30.0,
        )
        card = DepartureCard(dep, "3C91DC", "FFFFFF")
        assert card.countdown_label.text() == "< 1 min"

    def test_countdown_departed(self):
        dep = Departure(
            line_name="259", line_id="X", destination="Test", expected_iso="",
            fetch_timestamp=time.time() - 60, eta_seconds=0.5,
        )
        card = DepartureCard(dep, "3C91DC", "FFFFFF")
        assert card.countdown_label.text() == "Parti"

    def test_countdown_zero_eta(self):
        dep = Departure(
            line_name="259", line_id="X", destination="Test", expected_iso="",
            fetch_timestamp=time.time(), eta_seconds=0.0,
        )
        card = DepartureCard(dep, "3C91DC", "FFFFFF")
        assert card.countdown_label.text() == "--"

    def test_countdown_updates_over_time(self):
        """Simulate time passing: countdown should decrease."""
        dep = Departure(
            line_name="259", line_id="X", destination="Test", expected_iso="",
            fetch_timestamp=time.time() - 120,  # fetched 2 min ago
            eta_seconds=600.0,  # was 10 min away at fetch time
        )
        card = DepartureCard(dep)
        # Should now be ~8 min (600 - 120 = 480s = 8min)
        assert card.countdown_label.text() == "8 min"

    def test_badge_color(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep, "3C91DC", "FFFFFF")
        style = card.badge.styleSheet()
        assert "#3C91DC" in style
        assert "#FFFFFF" in style

    def test_status_on_time(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", departure_status="onTime",
                        fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep)
        assert card.status_label.text() == "A l'heure"

    def test_status_vehicle_at_stop(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", vehicle_at_stop=True,
                        fetch_timestamp=time.time(), eta_seconds=60)
        card = DepartureCard(dep)
        assert card.status_label.text() == "A l'arret"

    def test_status_delayed(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", departure_status="delayed",
                        fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep)
        assert card.status_label.text() == "En retard"

    def test_status_cancelled(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", departure_status="cancelled",
                        fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep)
        assert card.status_label.text() == "Annule"

    def test_clock_time_display(self):
        iso = "2025-06-15T14:35:00+02:00"
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso=iso,
                        fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep)
        # Widget converts to local timezone, so compute expected local time
        expected_local = datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
        assert card.clock_label.text() == expected_local


class TestFavouriteGroup:
    def test_with_departures(self, sample_favourite, sample_departure):
        group = FavouriteGroup(sample_favourite, [sample_departure])
        assert len(group.cards) == 1

    def test_no_departures_message(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [])
        assert len(group.cards) == 0
        # Should contain "Aucun depart" label
        found = False
        for i in range(group.layout().count()):
            w = group.layout().itemAt(i).widget()
            if w and hasattr(w, "text") and "Aucun depart" in w.text():
                found = True
        assert found, "Should show 'Aucun depart' when no departures"

    def test_max_5_cards(self, sample_favourite):
        deps = [
            Departure("259", "X", "Test", "", fetch_timestamp=time.time(), eta_seconds=60 * i)
            for i in range(1, 9)
        ]
        group = FavouriteGroup(sample_favourite, deps)
        assert len(group.cards) == 5  # max 5 from departures[:5]

    def test_header_text(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [])
        # Find the header label
        header_layout = group.layout().itemAt(0).layout()
        header_label = header_layout.itemAt(0).widget()
        assert "Pavillon Halevy" in header_label.text()
        assert "Saint-Germain" in header_label.text()

    def test_no_delete_button_in_normal_mode(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [], edit_mode=False)
        header_layout = group.layout().itemAt(0).layout()
        # Should only have the label (1 item)
        assert header_layout.count() == 1

    def test_delete_button_in_edit_mode(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [], edit_mode=True)
        header_layout = group.layout().itemAt(0).layout()
        # Should have label + delete button (2 items)
        assert header_layout.count() == 2

    def test_delete_signal(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [], edit_mode=True)
        emitted = []
        group.delete_requested.connect(lambda fav: emitted.append(fav))
        # Click the delete button
        header_layout = group.layout().itemAt(0).layout()
        del_btn = header_layout.itemAt(1).widget()
        del_btn.click()
        assert len(emitted) == 1
        assert emitted[0].stop_area_id == "50980"

    def test_update_countdowns_propagates(self, sample_favourite, sample_departure):
        group = FavouriteGroup(sample_favourite, [sample_departure])
        # Should not crash
        group.update_countdowns()


class TestHomeScreen:
    def test_empty_state(self):
        home = HomeScreen()
        home.populate([], {})
        # Should show the empty label
        found_empty = False
        for i in range(home.scroll_layout.count()):
            w = home.scroll_layout.itemAt(i).widget()
            if w and hasattr(w, "text") and "Aucun favori" in w.text():
                found_empty = True
        assert found_empty

    def test_populate_with_favourites(self, sample_favourite, sample_departure):
        home = HomeScreen()
        dep_map = {f"50980_C02000_{sample_favourite.direction}": [sample_departure]}
        home.populate([sample_favourite], dep_map)
        assert len(home.groups) == 1

    def test_populate_clears_old_groups(self, sample_favourite, sample_departure):
        home = HomeScreen()
        dep_map = {f"50980_C02000_{sample_favourite.direction}": [sample_departure]}
        home.populate([sample_favourite], dep_map)
        assert len(home.groups) == 1
        # Populate again with empty
        home.populate([], {})
        assert len(home.groups) == 0

    def test_edit_mode_toggle(self):
        home = HomeScreen()
        assert home.edit_mode is False
        home._toggle_edit_mode()
        assert home.edit_mode is True
        home._toggle_edit_mode()
        assert home.edit_mode is False

    def test_edit_mode_shows_delete_buttons(self, sample_favourite, sample_departure):
        home = HomeScreen()
        dep_map = {f"50980_C02000_{sample_favourite.direction}": [sample_departure]}
        # Normal mode - no delete buttons
        home.populate([sample_favourite], dep_map)
        group = home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert header_layout.count() == 1  # just label

        # Enable edit mode and repopulate
        home.edit_mode = True
        home.populate([sample_favourite], dep_map)
        group = home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert header_layout.count() == 2  # label + delete btn

    def test_add_signal(self):
        home = HomeScreen()
        emitted = []
        home.add_requested.connect(lambda: emitted.append(True))
        home.add_btn.click()
        assert len(emitted) == 1

    def test_refresh_signal(self):
        home = HomeScreen()
        emitted = []
        home.refresh_requested.connect(lambda: emitted.append(True))
        home.refresh_btn.click()
        assert len(emitted) == 1

    def test_status_bar_text(self):
        home = HomeScreen()
        home.set_updated_time("Mis a jour a 14:32")
        assert home.updated_label.text() == "Mis a jour a 14:32"
        home.set_next_refresh("MaJ dans 2:45")
        assert home.next_refresh_label.text() == "MaJ dans 2:45"

    def test_update_countdowns_no_crash_empty(self):
        home = HomeScreen()
        home.populate([], {})
        home.update_countdowns()  # should not crash

    def test_repopulate_empty_after_populated(self, sample_favourite, sample_departure):
        """Verify we can go from populated -> empty -> populated without crash."""
        home = HomeScreen()
        dep_map = {f"50980_C02000_{sample_favourite.direction}": [sample_departure]}
        home.populate([sample_favourite], dep_map)
        assert len(home.groups) == 1

        # Process pending deleteLater
        _app.processEvents()

        home.populate([], {})
        assert len(home.groups) == 0

        _app.processEvents()

        # Repopulate again
        home.populate([sample_favourite], dep_map)
        assert len(home.groups) == 1


class TestSearchScreen:
    def test_initial_state(self):
        screen = SearchScreen()
        assert screen.stack.currentIndex() == 0  # mode selection page
        assert screen.selected_mode == ""
        assert screen.selected_line is None
        assert screen.selected_stop is None

    def test_mode_selection_advances_to_line_search(self):
        screen = SearchScreen()
        screen._on_mode_selected("bus")
        assert screen.selected_mode == "bus"
        assert screen.stack.currentIndex() == 1  # line search page

    def test_search_signal_emitted_with_mode(self):
        screen = SearchScreen()
        screen.selected_mode = "bus"
        emitted = []
        screen.line_search_requested.connect(lambda q, m: emitted.append((q, m)))
        screen.search_input.setText("259")
        screen._do_search()
        assert emitted == [("259", "bus")]

    def test_debounce_allows_single_char(self):
        """Line search allows queries as short as 1 character."""
        screen = SearchScreen()
        screen.selected_mode = "metro"
        emitted = []
        screen.line_search_requested.connect(lambda q, m: emitted.append((q, m)))
        screen.search_input.setText("2")
        screen._do_search()
        assert emitted == [("2", "metro")]

    def test_line_results_displayed(self):
        screen = SearchScreen()
        lines = [
            LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF", route_id="IDFM:C02000"),
            LineAtStop("C02001", "259A", "bus", "FF0000", "FFFFFF", route_id="IDFM:C02001"),
        ]
        screen.on_line_results(lines)
        count = 0
        for i in range(screen.line_results_layout.count()):
            w = screen.line_results_layout.itemAt(i).widget()
            if w:
                count += 1
        assert count == 2

    def test_line_results_empty(self):
        screen = SearchScreen()
        screen.on_line_results([])
        found = False
        for i in range(screen.line_results_layout.count()):
            w = screen.line_results_layout.itemAt(i).widget()
            if w and hasattr(w, "text") and "Aucun resultat" in w.text():
                found = True
        assert found

    def test_line_selection_advances_to_stop_page(self):
        screen = SearchScreen()
        emitted_signals = []
        screen.stops_on_line_requested.connect(
            lambda route_id: emitted_signals.append(route_id)
        )
        line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF", route_id="IDFM:C02000")
        screen._on_line_selected(line)
        assert screen.stack.currentIndex() == 2  # stop selection page
        assert screen.selected_line == line
        assert emitted_signals == ["IDFM:C02000"]

    def test_stop_results_displayed(self):
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF", route_id="IDFM:C02000")
        stops = [
            StopOnLine("Pavillon Halevy", "IDFM:423181"),
            StopOnLine("Nanterre - Papeteries", "IDFM:423200"),
        ]
        screen.on_stop_results(stops)
        count = 0
        for i in range(screen.stop_results_layout.count()):
            w = screen.stop_results_layout.itemAt(i).widget()
            if w:
                count += 1
        assert count == 2

    def test_stop_results_empty(self):
        screen = SearchScreen()
        screen.on_stop_results([])
        found = False
        for i in range(screen.stop_results_layout.count()):
            w = screen.stop_results_layout.itemAt(i).widget()
            if w and hasattr(w, "text") and "Aucun arret" in w.text():
                found = True
        assert found

    def test_stop_filter(self):
        screen = SearchScreen()
        stops = [
            StopOnLine("Pavillon Halevy", "IDFM:423181"),
            StopOnLine("Nanterre - Papeteries", "IDFM:423200"),
            StopOnLine("Bas Prunay", "IDFM:423100"),
        ]
        screen.on_stop_results(stops)
        # All 3 visible
        count = sum(1 for i in range(screen.stop_results_layout.count())
                    if screen.stop_results_layout.itemAt(i).widget())
        assert count == 3

        # Filter to "pav"
        screen.stop_filter_input.setText("pav")
        count = sum(1 for i in range(screen.stop_results_layout.count())
                    if screen.stop_results_layout.itemAt(i).widget())
        assert count == 1

        # Clear filter shows all again
        screen.stop_filter_input.setText("")
        count = sum(1 for i in range(screen.stop_results_layout.count())
                    if screen.stop_results_layout.itemAt(i).widget())
        assert count == 3

    def test_stop_filter_ignores_accents_and_dashes(self):
        screen = SearchScreen()
        stops = [
            StopOnLine("Pavillon Halévy", "IDFM:423181"),
            StopOnLine("Nanterre - Papeteries", "IDFM:423200"),
            StopOnLine("Bois-d'Arcy", "IDFM:423100"),
        ]
        screen.on_stop_results(stops)

        # "halevy" without accent matches "Halévy"
        screen.stop_filter_input.setText("halevy")
        count = sum(1 for i in range(screen.stop_results_layout.count())
                    if screen.stop_results_layout.itemAt(i).widget())
        assert count == 1

        # "nanterre papeteries" without dash matches "Nanterre - Papeteries"
        screen.stop_filter_input.setText("nanterre papeteries")
        count = sum(1 for i in range(screen.stop_results_layout.count())
                    if screen.stop_results_layout.itemAt(i).widget())
        assert count == 1

        # "bois darcy" without dash/apostrophe matches "Bois-d'Arcy"
        screen.stop_filter_input.setText("bois darcy")
        count = sum(1 for i in range(screen.stop_results_layout.count())
                    if screen.stop_results_layout.itemAt(i).widget())
        assert count == 1

    def test_stop_selection_advances_to_direction_page(self):
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF", route_id="IDFM:C02000")
        emitted = []
        screen.resolve_and_probe_requested.connect(
            lambda sid, lid: emitted.append((sid, lid))
        )
        stop = StopOnLine("Pavillon Halevy", "IDFM:423181")
        screen._on_stop_selected(stop)
        assert screen.stack.currentIndex() == 3  # direction page
        assert screen.selected_stop == stop
        assert emitted == [("IDFM:423181", "C02000")]

    def test_directions_displayed_individually(self):
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF")
        screen.selected_stop = StopOnLine("Test", "IDFM:123")
        directions = [
            ("Saint-Germain-En-Laye <RER>", "1"),
            ("Poissy", "1"),
            ("Nanterre - Papeteries", "2"),
        ]
        screen.on_directions_results("50980", "Pavillon Halevy", directions)
        count = 0
        for i in range(screen.dir_results_layout.count()):
            w = screen.dir_results_layout.itemAt(i).widget()
            if w:
                count += 1
        # Each destination shown individually
        assert count == 3

    def test_direction_selection_emits_favourite(self):
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF")
        screen.selected_stop = StopOnLine("Pavillon Halevy", "IDFM:423181")
        screen._resolved_stop_area_id = "50980"
        screen._resolved_stop_name = "Pavillon Halevy"
        emitted = []
        screen.favourite_added.connect(lambda fav: emitted.append(fav))

        screen._on_direction_selected("1", "Saint-Germain-En-Laye")

        assert len(emitted) == 1
        fav = emitted[0]
        assert fav.stop_area_id == "50980"
        assert fav.stop_name == "Pavillon Halevy"
        assert fav.line_id == "C02000"
        assert fav.line_name == "259"
        assert fav.line_color == "3C91DC"
        assert fav.direction == "1"
        assert fav.destination_name == "Saint-Germain-En-Laye"

    def test_terminus_directions_filtered_out(self):
        """Arriving vehicles whose destination matches stop name are filtered."""
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C01742", "A", "rail", "FF0000", "FFFFFF")
        screen.selected_stop = StopOnLine("Saint-Germain-en-Laye RER", "IDFM:monomodalStopPlace:43114")
        directions = [
            ("Cergy-Le Haut", "1"),
            ("Poissy", "1"),
            ("Saint-Germain-en-Laye", "1"),  # arriving -> should be filtered
            ("Boissy-Saint-Leger", "2"),
        ]
        screen.on_directions_results("43114", "Saint-Germain-en-Laye", directions)
        # 3 individual destinations (Cergy, Poissy, Boissy; Saint-Germain filtered as terminus)
        count = 0
        for i in range(screen.dir_results_layout.count()):
            w = screen.dir_results_layout.itemAt(i).widget()
            if w:
                count += 1
        assert count == 3

    def test_no_directions_shows_fallback_button(self):
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF")
        screen.selected_stop = StopOnLine("Test", "IDFM:123")
        screen.on_directions_results("50980", "Test", [])
        # Should show "Aucune direction" label + fallback button
        found_label = False
        found_btn = False
        for i in range(screen.dir_results_layout.count()):
            w = screen.dir_results_layout.itemAt(i).widget()
            if w and hasattr(w, "text"):
                if "Aucune direction" in w.text():
                    found_label = True
                if "Ajouter" in w.text():
                    found_btn = True
        assert found_label
        assert found_btn

    def test_resolution_error_shows_message(self):
        screen = SearchScreen()
        screen.selected_line = LineAtStop("C02000", "259", "bus", "3C91DC", "FFFFFF")
        screen.selected_stop = StopOnLine("Test", "IDFM:123")
        screen.on_directions_results("", "", [])
        found = False
        for i in range(screen.dir_results_layout.count()):
            w = screen.dir_results_layout.itemAt(i).widget()
            if w and hasattr(w, "text") and "Erreur" in w.text():
                found = True
        assert found

    def test_back_from_mode_page_emits_home(self):
        screen = SearchScreen()
        emitted = []
        screen.back_to_home.connect(lambda: emitted.append(True))
        screen.stack.setCurrentIndex(0)
        screen._go_back()
        assert len(emitted) == 1

    def test_back_from_line_search_goes_to_mode(self):
        screen = SearchScreen()
        screen.stack.setCurrentIndex(1)
        screen._go_back()
        assert screen.stack.currentIndex() == 0

    def test_back_from_stop_page_goes_to_line_search(self):
        screen = SearchScreen()
        screen.stack.setCurrentIndex(2)
        screen._go_back()
        assert screen.stack.currentIndex() == 1

    def test_back_from_direction_page_goes_to_stop(self):
        screen = SearchScreen()
        screen.stack.setCurrentIndex(3)
        screen._go_back()
        assert screen.stack.currentIndex() == 2

    def test_reset(self):
        screen = SearchScreen()
        screen.selected_mode = "bus"
        screen.selected_line = LineAtStop("L1", "Bus 1")
        screen.selected_stop = StopOnLine("Test Stop", "IDFM:123")
        screen._resolved_stop_area_id = "50980"
        screen.stack.setCurrentIndex(3)
        screen.reset()
        assert screen.stack.currentIndex() == 0
        assert screen.selected_mode == ""
        assert screen.selected_line is None
        assert screen.selected_stop is None
        assert screen._resolved_stop_area_id == ""
        assert screen.search_input.text() == ""


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MAIN WINDOW TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMainWindow:
    @patch("main.load_favourites", return_value=[])
    def test_starts_on_home_screen(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        assert w.stack.currentIndex() == 0
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_navigate_to_search(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        w._show_search()
        assert w.stack.currentIndex() == 1
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_navigate_back_home(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        w._show_search()
        w._show_home()
        assert w.stack.currentIndex() == 0
        w.close()

    @patch("main.load_favourites", return_value=[])
    @patch("main.save_favourites")
    def test_add_favourite(self, mock_save, mock_load):
        from main import MainWindow
        w = MainWindow()
        fav = Favourite("50980", "Pavillon Halevy", "C02000", "259",
                        destination_name="Saint-Germain")
        with patch.object(w, "_refresh_departures"):
            w._on_favourite_added(fav)
        assert len(w.favourites) == 1
        mock_save.assert_called_once()
        w.close()

    @patch("main.load_favourites", return_value=[])
    @patch("main.save_favourites")
    def test_duplicate_favourite_ignored(self, mock_save, mock_load):
        from main import MainWindow
        w = MainWindow()
        fav = Favourite("50980", "Pavillon Halevy", "C02000", "259",
                        destination_name="Saint-Germain")
        with patch.object(w, "_refresh_departures"):
            w._on_favourite_added(fav)
            w._on_favourite_added(fav)  # duplicate
        assert len(w.favourites) == 1
        w.close()

    @patch("main.load_favourites", return_value=[])
    @patch("main.save_favourites")
    def test_delete_favourite(self, mock_save, mock_load):
        from main import MainWindow
        w = MainWindow()
        fav = Favourite("50980", "Pavillon Halevy", "C02000", "259",
                        direction="1", destination_name="Saint-Germain")
        w.favourites = [fav]
        w._delete_favourite(fav)
        assert len(w.favourites) == 0
        mock_save.assert_called()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_auto_refresh_nocturnal_pause(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        with patch("main.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 3  # 3am
            mock_dt.now.return_value = mock_now
            with patch.object(w, "_refresh_departures") as mock_refresh:
                w._auto_refresh()
                mock_refresh.assert_not_called()
        assert w.home.next_refresh_label.text() == "Pause nocturne"
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_timers_are_running(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        assert w.refresh_timer.isActive()
        assert w.countdown_timer.isActive()
        assert w.refresh_timer.interval() == 60000  # 1 min
        assert w.countdown_timer.interval() == 1000
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_window_size(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        assert w.width() == 800
        assert w.height() == 480
        w.close()

    @patch("main.load_favourites")
    @patch("main.save_favourites")
    def test_edit_mode_rebuilds_with_delete_buttons(self, mock_save, mock_load):
        """Toggling edit mode should rebuild the home screen with delete buttons."""
        from main import MainWindow
        fav = Favourite("50980", "Pavillon Halevy", "C02000", "259",
                        direction="1", destination_name="Saint-Germain")
        mock_load.return_value = [fav]
        w = MainWindow()
        dep = Departure("259", "X", "Saint-Germain", "",
                        fetch_timestamp=time.time(), eta_seconds=300)
        w.departure_map = {"50980_C02000_1": [dep]}
        w._rebuild_home()

        # Normal mode: no delete buttons
        assert w.home.edit_mode is False
        group = w.home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert header_layout.count() == 1

        # Toggle edit mode via button
        w.home.edit_btn.click()
        _app.processEvents()

        # Should have rebuilt with delete buttons
        assert w.home.edit_mode is True
        assert len(w.home.groups) == 1
        group = w.home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert header_layout.count() == 2  # label + delete btn
        w.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LIVE INTEGRATION TESTS (network required, skip by default)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.live
class TestLiveAPIs:
    """These tests hit real APIs. Run with: pytest -m live"""

    def test_line_search_259(self):
        worker = LineSearchWorker("259")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()
        assert len(results) > 0
        names = [r.line_name for r in results]
        assert any("259" in n for n in names), f"Expected 259 in {names}"

    def test_stops_on_line_259(self):
        worker = StopsOnLineWorker("IDFM:C02000")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()
        assert len(results) > 0
        names = [r.stop_name for r in results]
        assert any("Pavillon" in n or "Halevy" in n for n in names), f"Expected Pavillon Halevy in {names}"

    def test_resolve_and_probe_259(self):
        # First get a stop_id from stops-on-line
        worker = StopsOnLineWorker("IDFM:C02000")
        stops = []
        worker.finished.connect(lambda r: stops.extend(r))
        worker.run()
        assert len(stops) > 0

        # Use the first stop's stop_id to resolve + probe
        stop = stops[0]
        probe_worker = ResolveAndProbeWorker(stop.stop_id, "C02000")
        results = []
        probe_worker.finished.connect(lambda sa, sn, dirs: results.append((sa, sn, dirs)))
        probe_worker.run()
        assert len(results) == 1
        stop_area_id, stop_name, directions = results[0]
        assert stop_area_id != ""

    def test_departure_fetch(self):
        fav = Favourite(
            stop_area_id="50980", stop_name="Pavillon Halevy",
            line_id="C02000", line_name="259",
            direction="1", destination_name="Saint-Germain",
        )
        worker = DepartureWorker([fav])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()
        key = "50980_C02000_1"
        assert key in results
        # May be empty during night, but key should exist


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EDGE CASE / REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestThreadedWorkerDelivery:
    """Regression tests: workers must not be garbage collected before signals arrive."""

    @patch("api.requests.get")
    def test_line_search_worker_delivers_via_thread(self, mock_get):
        """The actual bug: worker on QThread must survive to deliver results."""
        from PyQt5.QtCore import QEventLoop, QTimer

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id_line": "C02000",
                    "shortname_line": "259",
                    "name_line": "Bus 259",
                    "transportmode": "bus",
                    "colourweb_hexa": "3C91DC",
                    "textcolourweb_hexa": "FFFFFF",
                },
            ]
        }
        mock_get.return_value = mock_resp

        results = []
        worker = LineSearchWorker("259")
        worker.finished.connect(lambda r: results.extend(r))
        thread, worker_ref = start_worker(worker)

        # Wait for thread to finish (up to 5s)
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        QTimer.singleShot(5000, loop.quit)
        loop.exec_()
        _app.processEvents()

        assert len(results) == 1, f"Expected 1 result via thread, got {len(results)}"
        assert results[0].line_name == "259"

    @patch("api.requests.get")
    def test_worker_gc_without_ref_loses_results(self, mock_get):
        """Demonstrate that without storing worker ref, results may be lost."""
        import gc
        from PyQt5.QtCore import QEventLoop, QTimer

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id_line": "C02000",
                    "shortname_line": "259",
                    "name_line": "Bus 259",
                    "transportmode": "bus",
                    "colourweb_hexa": "3C91DC",
                    "textcolourweb_hexa": "FFFFFF",
                },
            ]
        }
        mock_get.return_value = mock_resp

        results = []
        worker = LineSearchWorker("259")
        worker.finished.connect(lambda r: results.extend(r))
        thread, _ = start_worker(worker)
        # Delete the only Python reference to worker
        del worker
        gc.collect()

        # Wait for thread
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec_()
        _app.processEvents()

        # Results may or may not arrive - this test documents the GC risk
        # If it passes with results, Qt kept the C++ object alive (platform-dependent)
        # The important thing is it doesn't crash


class TestEdgeCases:
    def test_empty_line_color_doesnt_crash(self):
        dep = Departure("259", "X", "Test", "", fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep, "", "")
        assert card.badge is not None

    def test_none_line_color_doesnt_crash(self):
        dep = Departure("259", "X", "Test", "", fetch_timestamp=time.time(), eta_seconds=300)
        card = DepartureCard(dep, None, None)
        assert card.badge is not None

    def test_special_characters_in_stop_name(self):
        fav = Favourite("1", "Gare de Bois-d'Arcy <RER>", "L1", "Bus")
        group = FavouriteGroup(fav, [])
        header_layout = group.layout().itemAt(0).layout()
        header_label = header_layout.itemAt(0).widget()
        assert "Bois-d'Arcy" in header_label.text()

    def test_countdown_negative_eta(self):
        dep = Departure("259", "X", "Test", "", fetch_timestamp=time.time(), eta_seconds=-100)
        card = DepartureCard(dep)
        assert card.countdown_label.text() == "--"

    def test_departure_worker_empty_favourites(self):
        worker = DepartureWorker([])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()
        assert results == {}

    @patch("api.requests.get")
    def test_line_search_worker_empty_query(self, mock_get):
        """A search with an empty string should still not crash."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp

        worker = LineSearchWorker("")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()
        assert results == []

    def test_home_screen_populate_then_empty_then_populate(self):
        """Regression: empty_label must survive multiple populate cycles."""
        home = HomeScreen()
        fav = Favourite("1", "Test", "L1", "Bus")
        dep = Departure("Bus", "L1", "Dest", "",
                        fetch_timestamp=time.time(), eta_seconds=300)

        # First: show favourites
        home.populate([fav], {"1_L1_": [dep]})
        _app.processEvents()

        # Second: empty
        home.populate([], {})
        _app.processEvents()

        # Third: favourites again - should not crash
        home.populate([fav], {"1_L1_": [dep]})
        _app.processEvents()
        assert len(home.groups) == 1
