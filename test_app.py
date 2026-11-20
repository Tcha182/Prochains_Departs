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
    ResolveAndProbeWorker, StopAreaSearchWorker, LineDetailsWorker,
    start_worker, TrafficWorker,
)
from widgets import DepartureCard, FavouriteGroup, HomeScreen, SearchScreen
from styles import DARK_THEME


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def no_network(request):
    """Unit tests never reach the real APIs: a worker started in the
    background (e.g. by a MainWindow timer) fails fast instead of holding a
    thread open on a slow network. Tests patch api.requests.get themselves
    to provide responses; live tests are left alone."""
    if request.node.get_closest_marker("live"):
        yield
        return
    import requests as _requests
    # requests.request is what ha.py (Home Assistant) goes through.
    with patch("api.requests.get",
               side_effect=_requests.ConnectionError("network disabled in tests")), \
            patch("ha.requests.request",
                  side_effect=_requests.ConnectionError("network disabled in tests")):
        yield


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

    def test_corrupt_file_is_kept_aside(self, tmp_path):
        """A corrupt file must not be silently overwritten by the next save."""
        path = str(tmp_path / "favourites.json")
        with open(path, "w") as f:
            f.write("NOT JSON{{{")
        with patch("models.FAVOURITES_PATH", path):
            assert load_favourites() == []
            save_favourites([Favourite("1", "A", "L1", "1")])
        backups = [n for n in os.listdir(tmp_path) if ".corrupt-" in n]
        assert len(backups) == 1
        with open(tmp_path / backups[0]) as f:
            assert f.read() == "NOT JSON{{{"

    def test_unknown_fields_are_ignored(self, tmp_path):
        path = str(tmp_path / "favourites.json")
        with open(path, "w") as f:
            json.dump([{"stop_area_id": "1", "stop_name": "A", "line_id": "L",
                        "line_name": "1", "future_field": True}], f)
        with patch("models.FAVOURITES_PATH", path):
            loaded = load_favourites()
        assert loaded[0].stop_name == "A"

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
        key1 = fav1.key
        key2 = fav2.key
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

        key = sample_favourite.key
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

        key = fav.key
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

        key = fav.key
        assert len(results[key]) == 2

    @patch("api.requests.get")
    def test_branches_sharing_a_direction_stay_separate(self, mock_get):
        """RER B: Robinson and Saint-Remy share DirectionRef; each favourite
        must keep its own departures."""
        now = datetime.now(timezone.utc)
        t1 = (now + timedelta(minutes=3)).isoformat()
        t2 = (now + timedelta(minutes=6)).isoformat()
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_siri_response([
            {"destination": "Robinson", "expected_time": t1, "direction_ref": "A"},
            {"destination": "Saint-Remy-les-Chevreuse", "expected_time": t2,
             "direction_ref": "A"},
        ])
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        fav_r = Favourite("1", "Gare du Nord", "C01743", "B",
                          direction="A", destination_name="Robinson")
        fav_s = Favourite("1", "Gare du Nord", "C01743", "B",
                          direction="A", destination_name="Saint-Remy-les-Chevreuse")
        assert fav_r.key != fav_s.key

        worker = DepartureWorker([fav_r, fav_s])
        results = {}
        worker.finished.connect(lambda r: results.update(r))
        worker.run()

        assert [d.destination for d in results[fav_r.key]] == ["Robinson"]
        assert [d.destination for d in results[fav_s.key]] == [
            "Saint-Remy-les-Chevreuse"]

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

        key = fav.key
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


class TestStopsOnLinePagination:
    @patch("api.requests.get")
    def test_fetches_every_page(self, mock_get):
        """Lines with more than 100 stop records must not be cut off."""
        def page(params):
            offset = params["offset"]
            n = 100 if offset < 200 else 30
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"results": [
                {"stop_name": f"Arret {offset + i:03d}", "stop_id": str(offset + i)}
                for i in range(n)]}
            return resp
        mock_get.side_effect = lambda url, params, timeout: page(params)

        worker = StopsOnLineWorker("IDFM:C01234")
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()

        assert mock_get.call_count == 3
        assert len(results) == 230


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
        assert card.status_label.text() == "À l'heure"

    def test_status_vehicle_at_stop(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", vehicle_at_stop=True,
                        fetch_timestamp=time.time(), eta_seconds=60)
        card = DepartureCard(dep)
        assert card.status_label.text() == "À l'arrêt"

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
        # Shown once, in place of the countdown (a countdown would read as
        # a train to catch), with the destination struck through.
        assert card.countdown_label.text() == "Annulé"
        assert card.status_label.text() == ""
        assert card.dest_label.font().strikeOut()

    def test_cancelled_row_still_hides_once_gone(self):
        dep = Departure(line_name="259", line_id="X", destination="Test",
                        expected_iso="", departure_status="cancelled",
                        fetch_timestamp=time.time() - 400, eta_seconds=300)
        from PyQt5.QtWidgets import QWidget
        parent = QWidget()
        card = DepartureCard(dep, parent=parent)
        assert card.isHidden()
        dep.fetch_timestamp = time.time()
        assert not DepartureCard(dep, parent=parent).isHidden()

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
        # Should contain "Aucun départ" label
        found = False
        for i in range(group.layout().count()):
            w = group.layout().itemAt(i).widget()
            if w and hasattr(w, "text") and "Aucun départ" in w.text():
                found = True
        assert found, "Should show 'Aucun départ' when no departures"

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
        assert group.delete_btn is None

    def test_delete_button_in_edit_mode(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [], edit_mode=True)
        header_layout = group.layout().itemAt(0).layout()
        # Should have label + delete button (2 items)
        assert group.delete_btn is not None

    def test_delete_signal(self, sample_favourite):
        group = FavouriteGroup(sample_favourite, [], edit_mode=True)
        emitted = []
        group.delete_requested.connect(lambda fav: emitted.append(fav))
        # Click the delete button
        header_layout = group.layout().itemAt(0).layout()
        del_btn = group.delete_btn
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
        dep_map = {sample_favourite.key: [sample_departure]}
        home.populate([sample_favourite], dep_map)
        assert len(home.groups) == 1

    def test_populate_clears_old_groups(self, sample_favourite, sample_departure):
        home = HomeScreen()
        dep_map = {sample_favourite.key: [sample_departure]}
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
        dep_map = {sample_favourite.key: [sample_departure]}
        # Normal mode - no delete buttons
        home.populate([sample_favourite], dep_map)
        group = home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert group.delete_btn is None

        # Enable edit mode and repopulate
        home.edit_mode = True
        home.populate([sample_favourite], dep_map)
        group = home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert group.delete_btn is not None

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
        dep_map = {sample_favourite.key: [sample_departure]}
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
            if w and hasattr(w, "text") and "Aucun résultat" in w.text():
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
            if w and hasattr(w, "text") and "Aucun arrêt" in w.text():
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
        w.departure_map = {fav.key: [dep]}
        w._rebuild_home()

        # Normal mode: no delete buttons
        assert w.home.edit_mode is False
        group = w.home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert group.delete_btn is None

        # Toggle edit mode via button
        w.home.edit_btn.click()
        _app.processEvents()

        # Should have rebuilt with delete buttons
        assert w.home.edit_mode is True
        assert len(w.home.groups) == 1
        group = w.home.groups[0]
        header_layout = group.layout().itemAt(0).layout()
        assert group.delete_btn is not None
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
        key = fav.key
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
        home.populate([fav], {fav.key: [dep]})
        _app.processEvents()

        # Second: empty
        home.populate([], {})
        _app.processEvents()

        # Third: favourites again - should not crash
        home.populate([fav], {fav.key: [dep]})
        _app.processEvents()
        assert len(home.groups) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. NEW FEATURE TESTS (clock drift, stop-first search, keyboard, sleep)
# ═══════════════════════════════════════════════════════════════════════════════

class TestServerTimestampEta:
    """ETAs must be computed against the server clock, not the Pi's."""

    def test_eta_uses_response_timestamp(self):
        worker = DepartureWorker([])
        expected = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        data = make_siri_response([{"expected_time": expected}])
        # Server says "now" is 5 minutes in the future of local time
        server_now = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        data["Siri"]["ServiceDelivery"]["ResponseTimestamp"] = server_now
        deps = worker._parse_departures(data, time.time())
        # 10min - 5min = ~5min relative to the server, despite local clock
        assert 290 <= deps[0].eta_seconds <= 310

    def test_eta_falls_back_to_local_clock(self):
        worker = DepartureWorker([])
        expected = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        data = make_siri_response([{"expected_time": expected}])  # no ResponseTimestamp
        deps = worker._parse_departures(data, time.time())
        assert 290 <= deps[0].eta_seconds <= 310


class TestApiKeyError:
    @patch("api.requests.get")
    def test_401_shows_friendly_message(self, mock_get):
        import requests as _requests
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        error = _requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = error
        mock_get.return_value = mock_resp

        fav = Favourite("50980", "Test", "C02000", "259")
        worker = DepartureWorker([fav])
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        assert errors == ["Clé API invalide - voir Paramètres"]

    @patch("api.requests.get")
    def test_other_http_error_keeps_generic_message(self, mock_get):
        import requests as _requests
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = _requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        fav = Favourite("50980", "Test", "C02000", "259")
        worker = DepartureWorker([fav])
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        assert len(errors) == 1
        assert errors[0].startswith("Erreur")


class TestParallelDepartureFetch:
    @patch("api.requests.get")
    def test_multiple_groups_all_fetched(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = make_siri_response([])
        mock_get.return_value = mock_resp

        favs = [
            Favourite("50980", "Stop A", "C02000", "259", direction="1"),
            Favourite("50981", "Stop B", "C02001", "260", direction="1"),
            Favourite("50982", "Stop C", "C02002", "261", direction="1"),
        ]
        worker = DepartureWorker(favs)
        results = {}
        worker.finished.connect(results.update)
        worker.run()
        assert mock_get.call_count == 3
        assert len(results) == 3


class TestStopAreaSearchWorker:
    @patch("api.requests.get")
    def test_groups_by_stop_and_town(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"stop_name": "Mairie", "id": "IDFM:C01111", "stop_id": "IDFM:1", "nom_commune": "Pantin"},
                {"stop_name": "Mairie", "id": "IDFM:C02222", "stop_id": "IDFM:2", "nom_commune": "Pantin"},
                {"stop_name": "Mairie", "id": "IDFM:C01111", "stop_id": "IDFM:3", "nom_commune": "Clamart"},
            ]
        }
        mock_get.return_value = mock_resp

        worker = StopAreaSearchWorker("mairie", 7)
        results = []
        ids = []
        worker.finished.connect(lambda r, sid: (results.extend(r), ids.append(sid)))
        worker.run()

        assert ids == [7]
        assert len(results) == 2  # Pantin and Clamart grouped separately
        clamart = next(m for m in results if m.town == "Clamart")
        pantin = next(m for m in results if m.town == "Pantin")
        assert pantin.routes == {"C01111": "IDFM:1", "C02222": "IDFM:2"}
        assert clamart.routes == {"C01111": "IDFM:3"}

    @patch("api.requests.get")
    def test_missing_commune_field_tolerated(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"stop_name": "Gare", "id": "IDFM:C09999", "stop_id": "IDFM:9"}]
        }
        mock_get.return_value = mock_resp

        worker = StopAreaSearchWorker("gare")
        results = []
        worker.finished.connect(lambda r, sid: results.extend(r))
        worker.run()
        assert len(results) == 1
        assert results[0].town == ""

    @patch("api.requests.get")
    def test_http_error_emits_and_finishes_empty(self, mock_get):
        import requests as _requests
        mock_get.side_effect = _requests.ConnectionError("down")
        worker = StopAreaSearchWorker("x", 3)
        results, errors = [], []
        worker.finished.connect(lambda r, sid: results.append((list(r), sid)))
        worker.error.connect(errors.append)
        worker.run()
        assert results == [([], 3)]
        assert len(errors) == 1


def _ods_response(status_code=200, results=None):
    """Response stub honouring status_code the way _text_search_records reads it."""
    import requests as _requests
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "ODSQL parse error" if status_code == 400 else ""
    resp.json.return_value = {"results": results or []}
    if status_code >= 400:
        resp.raise_for_status.side_effect = _requests.HTTPError(response=resp)
    else:
        resp.raise_for_status = MagicMock()
    return resp


class TestOdsqlTextSearchFallback:
    """search() is not valid ODSQL v2.1 — the workers must use suggest()
    and degrade through like / bare full-text when a portal rejects it."""

    RECORD = {"stop_name": "Mairie", "id": "IDFM:C01111", "stop_id": "IDFM:1",
              "nom_commune": "Pantin"}

    @patch("api.requests.get")
    def test_stop_search_uses_suggest(self, mock_get):
        mock_get.return_value = _ods_response(results=[self.RECORD])
        worker = StopAreaSearchWorker("mairie")
        results = []
        worker.finished.connect(lambda r, sid: results.extend(r))
        worker.run()
        assert len(results) == 1
        assert mock_get.call_count == 1
        where = mock_get.call_args.kwargs["params"]["where"]
        assert where == 'suggest(stop_name, "mairie")'

    @patch("api.requests.get")
    def test_stop_search_falls_back_to_like_then_fulltext(self, mock_get):
        mock_get.side_effect = [
            _ods_response(400),
            _ods_response(400),
            _ods_response(results=[self.RECORD]),
        ]
        worker = StopAreaSearchWorker("mairie")
        results, errors = [], []
        worker.finished.connect(lambda r, sid: results.extend(r))
        worker.error.connect(errors.append)
        worker.run()
        assert len(results) == 1
        assert errors == []
        wheres = [c.kwargs["params"]["where"] for c in mock_get.call_args_list]
        assert wheres == [
            'suggest(stop_name, "mairie")',
            'stop_name like "mairie"',
            '"mairie"',
        ]

    @patch("api.requests.get")
    def test_stop_search_all_variants_rejected_emits_error(self, mock_get):
        mock_get.side_effect = [_ods_response(400)] * 3
        worker = StopAreaSearchWorker("mairie", 5)
        results, errors = [], []
        worker.finished.connect(lambda r, sid: results.append((list(r), sid)))
        worker.error.connect(errors.append)
        worker.run()
        assert results == [([], 5)]
        assert len(errors) == 1

    @patch("api.requests.get")
    def test_line_search_combines_suggest_and_mode(self, mock_get):
        mock_get.return_value = _ods_response()
        worker = LineSearchWorker("62", "bus")
        worker.finished.connect(lambda r, sid: None)
        worker.run()
        where = mock_get.call_args.kwargs["params"]["where"]
        assert where == 'suggest(shortname_line, "62") AND transportmode="bus"'

    @patch("api.requests.get")
    def test_mode_only_listing_needs_no_text_filter(self, mock_get):
        mock_get.return_value = _ods_response()
        worker = LineSearchWorker("", "bus")
        worker.finished.connect(lambda r, sid: None)
        worker.run()
        assert mock_get.call_count == 1
        where = mock_get.call_args.kwargs["params"]["where"]
        assert where == 'transportmode="bus"'


class TestLineDetailsWorker:
    @patch("api.requests.get")
    def test_builds_or_where_clause(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp

        worker = LineDetailsWorker(["C01111", "C02222"])
        worker.finished.connect(lambda r: None)
        worker.run()
        where = mock_get.call_args.kwargs.get("params", mock_get.call_args[1]["params"])["where"]
        assert where == 'id_line="C01111" OR id_line="C02222"'

    @patch("api.requests.get")
    def test_empty_ids_skip_network(self, mock_get):
        worker = LineDetailsWorker([])
        results = []
        worker.finished.connect(lambda r: results.append(list(r)))
        worker.run()
        assert results == [[]]
        mock_get.assert_not_called()

    @patch("api.requests.get")
    def test_parses_and_sorts_lines(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id_line": "C2", "shortname_line": "10", "transportmode": "bus",
                 "colourweb_hexa": None, "textcolourweb_hexa": None},
                {"id_line": "C1", "shortname_line": "2", "transportmode": "bus",
                 "colourweb_hexa": "FF0000", "textcolourweb_hexa": "FFFFFF"},
            ]
        }
        mock_get.return_value = mock_resp

        worker = LineDetailsWorker(["C1", "C2"])
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()
        assert [l.line_name for l in results] == ["2", "10"]  # natural sort
        assert results[1].line_color == "FFFFFF"  # None coalesced


class TestStopFirstSearchFlow:
    def _screen_with_matches(self):
        from models import StopAreaMatch
        screen = SearchScreen()
        matches = [
            StopAreaMatch("Mairie de Pantin", "Pantin", {"C01111": "IDFM:1", "C02222": "IDFM:2"}),
        ]
        screen.stack.setCurrentIndex(4)
        screen.on_stop_area_results(matches, 0)
        return screen, matches

    def test_stop_results_displayed(self):
        screen, _ = self._screen_with_matches()
        widgets = [screen.stop_search_results_layout.itemAt(i).widget()
                   for i in range(screen.stop_search_results_layout.count())]
        assert any(w is not None for w in widgets)

    def test_stop_selection_requests_line_details(self):
        screen, matches = self._screen_with_matches()
        requested = []
        screen.line_details_requested.connect(requested.append)
        screen._on_stop_area_selected(matches[0])
        assert screen.stack.currentIndex() == 5
        assert sorted(requested[0]) == ["C01111", "C02222"]

    def test_line_at_stop_selection_probes_directions(self):
        screen, matches = self._screen_with_matches()
        screen._on_stop_area_selected(matches[0])
        probes = []
        screen.resolve_and_probe_requested.connect(lambda s, l: probes.append((s, l)))
        line = LineAtStop("C01111", "170", "bus", "FF0000", "FFFFFF", "IDFM:C01111")
        screen._on_line_at_stop_selected(line)
        assert probes == [("IDFM:1", "C01111")]
        assert screen.stack.currentIndex() == 3
        assert screen.selected_stop.stop_name == "Mairie de Pantin"

    def test_back_follows_navigation_history(self):
        screen, matches = self._screen_with_matches()
        screen.stack.setCurrentIndex(0)
        screen._nav_history.clear()
        screen._navigate(4)
        screen._on_stop_area_selected(matches[0])   # -> 5
        line = LineAtStop("C01111", "170", "bus")
        screen._on_line_at_stop_selected(line)      # -> 3
        screen._go_back()
        assert screen.stack.currentIndex() == 5
        screen._go_back()
        assert screen.stack.currentIndex() == 4
        screen._go_back()
        assert screen.stack.currentIndex() == 0

    def test_stale_stop_results_ignored(self):
        from models import StopAreaMatch
        screen = SearchScreen()
        screen._stop_search_id = 5
        screen.on_stop_area_results([StopAreaMatch("Old", "X")], 4)  # stale
        widgets = [screen.stop_search_results_layout.itemAt(i).widget()
                   for i in range(screen.stop_search_results_layout.count())]
        assert all(w is None for w in widgets)  # only the stretch

    def test_reset_clears_stop_first_state(self):
        screen, matches = self._screen_with_matches()
        screen._on_stop_area_selected(matches[0])
        screen.stop_search_input.setText("mairie")
        screen.reset()
        assert screen.stack.currentIndex() == 0
        assert screen._selected_stop_area is None
        assert screen._nav_history == []
        assert screen.stop_search_input.text() == ""


class TestKeyboardAccents:
    def _kb_with_target(self):
        from widgets import VirtualKeyboard
        from PyQt5.QtWidgets import QLineEdit
        kb = VirtualKeyboard()
        target = QLineEdit()
        kb.set_target(target)
        return kb, target

    def test_accent_layer_toggle(self):
        kb, target = self._kb_with_target()
        kb._on_key(kb.KEY_ACCENTS)
        assert kb._accent is True
        keys = [k for k, b in kb._key_buttons]
        assert "é" in keys and "ç" in keys
        kb._on_key("é")
        assert target.text() == "é"
        kb._on_key(kb.KEY_LETTERS)
        assert kb._accent is False
        keys = [k for k, b in kb._key_buttons]
        assert "a" in keys

    def test_shift_in_accent_layer(self):
        kb, target = self._kb_with_target()
        kb._on_key(kb.KEY_ACCENTS)
        kb._on_key("⇧")  # shift
        keys = [k for k, b in kb._key_buttons]
        assert "É" in keys
        kb._on_key("É")
        assert target.text() == "É"
        assert kb._shifted is False  # auto-released after one char

    def test_set_target_resets_layer(self):
        kb, target = self._kb_with_target()
        kb._on_key(kb.KEY_ACCENTS)
        from PyQt5.QtWidgets import QLineEdit
        kb.set_target(QLineEdit())
        assert kb._accent is False

    def test_all_layouts_have_same_shape(self):
        from widgets import VirtualKeyboard
        shapes = {tuple(len(r) for r in rows) for rows in (
            VirtualKeyboard.ROWS_LOWER, VirtualKeyboard.ROWS_UPPER,
            VirtualKeyboard.ROWS_ACCENT, VirtualKeyboard.ROWS_ACCENT_UPPER)}
        assert len(shapes) == 1


class TestDepartedCardHides:
    def test_card_hides_when_long_departed(self):
        dep = Departure("259", "X", "Test", "",
                        fetch_timestamp=time.time() - 120, eta_seconds=1.0)
        card = DepartureCard(dep)
        card.show()
        card.update_countdown()
        assert card.isHidden()

    def test_card_shows_parti_within_grace_window(self):
        dep = Departure("259", "X", "Test", "",
                        fetch_timestamp=time.time() - 45, eta_seconds=1.0)
        card = DepartureCard(dep)
        card.show()
        card.update_countdown()
        assert not card.isHidden()
        assert card.countdown_label.text() == "Parti"


class TestNocturnalSleep:
    @patch("main.load_favourites", return_value=[])
    def test_forced_sleep_during_night(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        w._settings.sleep_delay_minutes = 0  # sleep disabled by user
        w._last_interaction_time = time.monotonic() - 10 * 60
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=3)
            with patch.object(w, "_enter_sleep") as mock_sleep:
                w._on_countdown_tick()
                mock_sleep.assert_called_once()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_no_forced_sleep_during_day(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        w._settings.sleep_delay_minutes = 0
        w._last_interaction_time = time.monotonic() - 10 * 60
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=14)
            with patch.object(w, "_enter_sleep") as mock_sleep:
                w._on_countdown_tick()
                mock_sleep.assert_not_called()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_heartbeat_wakes_after_nocturnal_pause(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        w._settings.sleep_delay_minutes = 0
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=3)
            w._enter_sleep()
        assert w._sleeping and w._nocturnal_sleep
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=5)
            with patch.object(w, "_refresh_departures"):
                w._on_heartbeat()
        assert not w._sleeping
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_tap_wake_not_reslept_by_heartbeat(self, mock_load):
        """A manual wake during the night must not be immediately re-slept."""
        from main import MainWindow
        w = MainWindow()
        w._settings.sleep_delay_minutes = 0
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=3)
            w._enter_sleep()
            with patch.object(w, "_refresh_departures"):
                w._wake_up()
            with patch.object(w, "_enter_sleep") as mock_sleep:
                w._on_countdown_tick()  # just woke: idle ~0
                mock_sleep.assert_not_called()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_heartbeat_ignores_normal_sleep(self, mock_load):
        """User-configured sleep still requires a tap to wake."""
        from main import MainWindow
        w = MainWindow()
        w._settings.sleep_delay_minutes = 10
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=14)
            w._enter_sleep()
        assert w._sleeping and not w._nocturnal_sleep
        w._on_heartbeat()
        assert w._sleeping
        w.close()


@pytest.mark.live
class TestLiveStopSearch:
    """Live checks for the new stop-first search (network required)."""

    def test_stop_area_search_live(self):
        worker = StopAreaSearchWorker("pavillon")
        results = []
        worker.finished.connect(lambda r, sid: results.extend(r))
        worker.run()
        assert len(results) > 0
        assert any(r.routes for r in results)

    def test_line_details_live(self):
        worker = LineDetailsWorker(["C02000"])
        results = []
        worker.finished.connect(lambda r: results.extend(r))
        worker.run()
        assert len(results) == 1
        assert results[0].line_name


class TestRefreshRobustness:
    """Regressions for 'updates once, then never again until a reboot'."""

    @patch("main.load_favourites")
    def test_single_fetch_in_flight(self, mock_load, sample_favourite):
        from main import MainWindow
        mock_load.return_value = [sample_favourite]
        w = MainWindow()
        with patch.object(w, "_launch_worker") as launch:
            w._refresh_departures()
            w._refresh_departures()  # auto-refresh while the first still runs
            assert launch.call_count == 1
            w._refresh_departures(force=True)  # user tap supersedes it
            assert launch.call_count == 2
        w.close()

    @patch("main.load_favourites")
    def test_stuck_fetch_is_abandoned(self, mock_load, sample_favourite):
        import main
        mock_load.return_value = [sample_favourite]
        w = main.MainWindow()
        with patch.object(w, "_launch_worker") as launch:
            w._refresh_departures()
            w._departure_started_at -= main.REFRESH_STALL_SECONDS + 1
            w._refresh_departures()
            assert launch.call_count == 2
        w.close()

    @patch("main.load_favourites")
    def test_late_results_from_superseded_fetch_ignored(self, mock_load,
                                                        sample_favourite, sample_departure):
        from main import MainWindow
        mock_load.return_value = [sample_favourite]
        w = MainWindow()
        with patch.object(w, "_launch_worker"):
            w._refresh_departures()
            old_gen = w._departure_generation
            w._refresh_departures(force=True)
        w._on_departures_received({"old": [sample_departure]}, old_gen)
        assert "old" not in w.departure_map
        w._on_departures_received({"new": [sample_departure]}, w._departure_generation)
        assert "new" in w.departure_map
        assert w._departure_started_at is None
        w.close()

    @patch("main.load_favourites")
    def test_self_restart_when_refreshes_keep_failing(self, mock_load, sample_favourite):
        import main
        mock_load.return_value = [sample_favourite]
        w = main.MainWindow()
        w._last_departure_success -= main.SELF_RESTART_AFTER_SECONDS + 1
        with patch("main.datetime") as mock_dt, \
                patch.object(main.QApplication, "instance") as inst:
            mock_dt.now.return_value = MagicMock(hour=14)
            w._on_heartbeat()
            inst.return_value.exit.assert_called_once_with(1)
        w.close()

    @patch("main.load_favourites")
    def test_no_self_restart_while_sleeping(self, mock_load, sample_favourite):
        import main
        mock_load.return_value = [sample_favourite]
        w = main.MainWindow()
        w._sleeping = True
        w._last_departure_success -= main.SELF_RESTART_AFTER_SECONDS + 1
        with patch("main.datetime") as mock_dt, \
                patch.object(main.QApplication, "instance") as inst:
            mock_dt.now.return_value = MagicMock(hour=14)
            w._check_refresh_health()
            inst.return_value.exit.assert_not_called()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_idle_uses_monotonic_clock(self, mock_load):
        """An NTP jump of the wall clock after boot must not trigger sleep."""
        import main
        w = main.MainWindow()
        w._settings.sleep_delay_minutes = 10
        with patch("main.datetime") as mock_dt, \
                patch.object(w, "_enter_sleep") as mock_sleep, \
                patch("time.time", return_value=time.time() + 3 * 86400):
            mock_dt.now.return_value = MagicMock(hour=14)
            w._on_countdown_tick()
            mock_sleep.assert_not_called()
        w.close()

    def test_populate_not_deferred_forever_by_stuck_scroller(
            self, sample_favourite, sample_departure):
        from PyQt5.QtWidgets import QScroller
        home = HomeScreen()
        dep_map = {sample_favourite.key: [sample_departure]}
        fake = MagicMock()
        fake.state.return_value = QScroller.Pressed  # lost touch release
        with patch("widgets.home.QScroller.scroller", return_value=fake):
            home.populate([sample_favourite], dep_map)
            home._apply_pending_populate()  # first retry: starts the clock
            assert home.groups == []  # deferred while "pressed"
            home._populate_deferred_since -= HomeScreen.POPULATE_MAX_DEFER_S + 1
            home._apply_pending_populate()
        assert len(home.groups) == 1
        fake.stop.assert_called()


class TestSearchErrorFlag:
    @patch("main.load_favourites", return_value=[])
    def test_stale_search_error_does_not_swallow_results(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        s = w.search
        s._search_id = 2
        s.stack.setCurrentIndex(1)
        w._on_search_error("Erreur recherche: timeout", 1, s._search_id)  # stale
        s.on_line_results([], 1)  # stale finished
        assert not s._had_error
        line = LineAtStop("C02000", "259", "bus")
        s.on_line_results([line], 2)
        assert s.line_loading.text() == ""
        w.close()

    def test_reset_clears_error_flag(self):
        s = SearchScreen()
        s._had_error = True
        s.reset()
        assert not s._had_error


class TestHomeImprovements:
    @patch("main.load_favourites")
    @patch("main.save_favourites")
    def test_failed_group_is_flagged_stale(self, mock_save, mock_load):
        from main import MainWindow
        ok = Favourite("1", "A", "L1", "1", direction="x", destination_name="D")
        ko = Favourite("2", "B", "L2", "2", direction="x", destination_name="D")
        mock_load.return_value = [ok, ko]
        w = MainWindow()
        dep = Departure("1", "L1", "D", "", fetch_timestamp=time.time(),
                        eta_seconds=300)
        w.departure_map = {ko.key: [dep]}  # from an earlier, good fetch
        w._departure_error_msg = "Erreur réseau"
        w._on_departures_received({ok.key: [dep]})
        _app.processEvents()
        by_key = {g.favourite.key: g for g in w.home.groups}
        assert by_key[ok.key].stale_label is None
        assert by_key[ko.key].stale_label is not None
        # Next fetch succeeds for both: flag goes away
        w._departure_error_msg = None
        w._on_departures_received({ok.key: [dep], ko.key: [dep]})
        by_key = {g.favourite.key: g for g in w.home.groups}
        assert by_key[ko.key].stale_label is None
        w.close()

    @patch("main.load_favourites")
    @patch("main.save_favourites")
    def test_move_favourite(self, mock_save, mock_load):
        from main import MainWindow
        a = Favourite("1", "A", "L1", "1")
        b = Favourite("2", "B", "L2", "2")
        c = Favourite("3", "C", "L3", "3")
        mock_load.return_value = [a, b, c]
        w = MainWindow()
        w._move_favourite(c, -1)
        assert [f.stop_name for f in w.favourites] == ["A", "C", "B"]
        w._move_favourite(a, -1)  # already first: no-op
        assert [f.stop_name for f in w.favourites] == ["A", "C", "B"]
        assert mock_save.call_count == 1
        w.close()

    def test_move_buttons_only_in_edit_mode_and_bounded(self):
        home = HomeScreen()
        favs = [Favourite("1", "A", "L1", "1"), Favourite("2", "B", "L2", "2")]
        moves = []
        home.populate(favs, {}, move_callback=lambda f, d: moves.append((f.stop_name, d)))
        assert home.groups[0].up_btn is None
        home.edit_mode = True
        home.populate(favs, {}, move_callback=lambda f, d: moves.append((f.stop_name, d)))
        first, second = home.groups
        assert not first.up_btn.isEnabled() and first.down_btn.isEnabled()
        assert second.up_btn.isEnabled() and not second.down_btn.isEnabled()
        first.down_btn.click()
        assert moves == [("A", 1)]

    def test_traffic_message_shown_for_line(self):
        home = HomeScreen()
        fav = Favourite("1", "A", "C01742", "A")
        other = Favourite("2", "B", "C00000", "B")
        home.populate([fav, other], {},
                      traffic={"C01742": ["Trafic interrompu entre X et Y"]})
        assert len(home.groups[0].traffic_labels) == 1
        assert "interrompu" in home.groups[0].traffic_labels[0].text()
        assert home.groups[1].traffic_labels == []

    def test_header_clock_in_french(self):
        home = HomeScreen()
        home.update_clock(datetime(2026, 10, 25, 8, 5))
        assert home.header_clock.text() == "08:05"
        assert home.header_date.text() == "dimanche 25 octobre"


def _general_message(messages, ts="2026-10-25T08:00:00Z"):
    return {"Siri": {"ServiceDelivery": {
        "ResponseTimestamp": ts,
        "GeneralMessageDelivery": [{"InfoMessage": messages}]}}}


def _info(text, channel="Perturbation", until="2026-10-25T20:00:00Z",
          msg_type="SHORT_MESSAGE"):
    return {"InfoChannelRef": {"value": channel}, "ValidUntilTime": until,
            "Content": {"Message": [{"MessageType": msg_type,
                                     "MessageText": {"value": text}}]}}


class TestTrafficWorker:
    def test_keeps_current_perturbations_only(self):
        data = _general_message([
            _info("Trafic interrompu entre A et B"),
            _info("Nouveau: abonnement", channel="Commercial"),
            _info("Travaux termines", until="2026-10-24T20:00:00Z"),
            _info("Trafic interrompu entre A et B"),  # duplicate
        ])
        assert TrafficWorker.parse(data, 0) == ["Trafic interrompu entre A et B"]

    def test_prefers_short_message_and_strips_html(self):
        info = _info("<p>Long <b>texte</b></p>", msg_type="LONG_MESSAGE")
        info["Content"]["Message"].append(
            {"MessageType": "SHORT_MESSAGE", "MessageText": {"value": "Court"}})
        assert TrafficWorker.parse(_general_message([info]), 0) == ["Court"]
        only_long = _info("<p>Long  <b>texte</b></p>", msg_type="LONG_MESSAGE")
        assert TrafficWorker.parse(_general_message([only_long]), 0) == ["Long texte"]

    def test_truncates_and_caps_count(self):
        data = _general_message([_info("x" * 500), _info("b"), _info("c")])
        msgs = TrafficWorker.parse(data, 0)
        assert len(msgs) == 2
        assert len(msgs[0]) == TrafficWorker.MAX_CHARS

    def test_empty_or_odd_payloads(self):
        assert TrafficWorker.parse({}, 0) == []
        assert TrafficWorker.parse({"Siri": {"ServiceDelivery": {
            "GeneralMessageDelivery": [{}]}}}, 0) == []

    @patch("api.requests.get")
    def test_failed_line_is_left_out(self, mock_get):
        import requests as _requests
        ok = MagicMock()
        ok.raise_for_status = MagicMock()
        ok.json.return_value = _general_message([_info("Panne")])
        bad = MagicMock()
        bad.raise_for_status.side_effect = _requests.HTTPError("403")
        mock_get.side_effect = [ok, bad]
        worker = TrafficWorker(["L1", "L2", "L1"])
        out = {}
        worker.finished.connect(out.update)
        worker.run()
        assert out == {"L1": ["Panne"]}
        assert mock_get.call_count == 2  # duplicates fetched once

    @patch("main.load_favourites")
    @patch("main.save_favourites")
    def test_main_window_shows_traffic(self, mock_save, mock_load):
        from main import MainWindow
        fav = Favourite("1", "A", "C01742", "A")
        mock_load.return_value = [fav]
        w = MainWindow()
        w._on_traffic_received({"C01742": ["Trafic perturbe"], "X": []})
        assert w._traffic == {"C01742": ["Trafic perturbe"]}
        _app.processEvents()
        assert len(w.home.groups[0].traffic_labels) == 1
        w.close()


class TestEveningDimming:
    def _fake_backlight(self, tmp_path, max_level=255):
        dev = tmp_path / "rpi_backlight"
        dev.mkdir()
        (dev / "max_brightness").write_text(f"{max_level}\n")
        (dev / "brightness").write_text(f"{max_level}\n")
        return dev

    @patch("main.load_favourites", return_value=[])
    def test_dims_in_evening_and_restores_by_day(self, mock_load, tmp_path):
        from main import MainWindow
        dev = self._fake_backlight(tmp_path)
        with patch("main.glob.glob", return_value=[str(dev)]), \
                patch("main.datetime") as mock_dt, \
                patch("main.save_settings"):
            mock_dt.now.return_value = MagicMock(hour=22)
            w = MainWindow()
            w._settings.evening_brightness = 50
            w._brightness_pct = None
            w._apply_brightness()
            assert (dev / "brightness").read_text() == "128"

            mock_dt.now.return_value = MagicMock(hour=10)
            w._apply_brightness()
            assert (dev / "brightness").read_text() == "255"

            # Setting change applies straight away in the evening
            mock_dt.now.return_value = MagicMock(hour=6)
            w._on_evening_brightness_changed(25)
            assert (dev / "brightness").read_text() == "64"
            w.close()

    @patch("main.load_favourites", return_value=[])
    def test_writes_only_on_change(self, mock_load, tmp_path):
        from main import MainWindow
        dev = self._fake_backlight(tmp_path)
        with patch("main.glob.glob", return_value=[str(dev)]), \
                patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=12)
            w = MainWindow()
            w._brightness_pct = None
            w._apply_brightness()
            (dev / "brightness").write_text("7")  # changed behind our back
            w._apply_brightness()  # same level: no write
            assert (dev / "brightness").read_text() == "7"
            w.close()

    def test_settings_row_cycles(self):
        from widgets import SettingsScreen
        screen = SettingsScreen(current_dim=100)
        got = []
        screen.evening_brightness_changed.connect(got.append)
        screen._cycle_dim()
        screen._cycle_dim()
        screen._cycle_dim()
        assert got == [50, 25, 100]
        assert screen.dim_row._value_label.text() == "Désactivé"


class TestWakeUpShowsDepartures:
    """Regressions for 'woke the screen, no departures until a reboot'."""

    @staticmethod
    def _window(sample_favourite):
        import main
        with patch("main.load_favourites", return_value=[sample_favourite]):
            return main.MainWindow()

    def test_sleep_keeps_refreshing_slowly(self, sample_favourite):
        import main
        w = self._window(sample_favourite)
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=14)
            w._enter_sleep()
        assert w.refresh_timer.isActive()
        assert w.refresh_timer.interval() == main.SLEEP_REFRESH_MS
        assert not w.countdown_timer.isActive()
        with patch.object(w, "_launch_worker"):
            w._wake_up()
        assert w.refresh_timer.interval() == main.AUTO_REFRESH_MS
        assert w.countdown_timer.isActive()
        w.close()

    def test_departures_survive_sleep(self, sample_favourite, sample_departure):
        """Known departures are shown immediately on wake, even if the
        wake-up fetch then fails."""
        w = self._window(sample_favourite)
        w.departure_map = {sample_favourite.key: [sample_departure]}
        with patch("main.datetime") as mock_dt, patch.object(w, "_launch_worker"):
            mock_dt.now.return_value = MagicMock(hour=14)
            w._enter_sleep()
            w._wake_up()
            w._on_departure_error("Pas de connexion réseau", w._departure_generation)
            w._on_departures_received({}, w._departure_generation)
        assert len(w.home.groups[0].cards) == 1
        assert w.home.updated_label.text() == "Pas de connexion réseau"
        w.close()

    def test_fetch_during_sleep_updates_data(self, sample_favourite, sample_departure):
        w = self._window(sample_favourite)
        with patch("main.datetime") as mock_dt, patch.object(w, "_launch_worker"):
            mock_dt.now.return_value = MagicMock(hour=14)
            w._enter_sleep()
            w._refresh_departures()
            w._on_departures_received({sample_favourite.key: [sample_departure]},
                                      w._departure_generation)
        assert sample_favourite.key in w.departure_map
        w.close()

    def test_failed_fetch_retries_quickly_then_backs_off(self, sample_favourite):
        import main
        w = self._window(sample_favourite)
        with patch("main.datetime") as mock_dt, patch.object(w, "_launch_worker"):
            mock_dt.now.return_value = MagicMock(hour=14)
            for delay in main.RETRY_DELAYS_S:
                w._refresh_departures(force=True)
                assert not w.retry_timer.isActive()
                w._on_departure_error("Pas de connexion réseau", w._departure_generation)
                w._on_departures_received({}, w._departure_generation)
                assert w.retry_timer.isActive()
                assert w.retry_timer.interval() == delay * 1000
            # Quick attempts exhausted: the regular refresh timer takes over
            w._refresh_departures(force=True)
            w._on_departure_error("Pas de connexion réseau", w._departure_generation)
            w._on_departures_received({}, w._departure_generation)
            assert not w.retry_timer.isActive()
            assert w.refresh_timer.isActive()
        w.close()

    def test_success_resets_retry_backoff(self, sample_favourite, sample_departure):
        w = self._window(sample_favourite)
        with patch("main.datetime") as mock_dt, patch.object(w, "_launch_worker"):
            mock_dt.now.return_value = MagicMock(hour=14)
            w._refresh_departures(force=True)
            w._on_departure_error("x", w._departure_generation)
            w._on_departures_received({}, w._departure_generation)
            w._refresh_departures(force=True)
            w._on_departures_received({sample_favourite.key: [sample_departure]},
                                      w._departure_generation)
        assert w._departure_failures == 0
        assert not w.retry_timer.isActive()
        assert w.home.updated_label.text().startswith("Mis à jour")
        w.close()

    def test_unfetched_favourite_does_not_claim_no_departures(self, sample_favourite):
        texts = lambda g: [g.layout().itemAt(i).widget().text()
                           for i in range(g.layout().count())
                           if g.layout().itemAt(i).widget()]
        assert "En attente des horaires..." in texts(FavouriteGroup(sample_favourite, None))
        assert "Aucun départ prévu" in texts(FavouriteGroup(sample_favourite, []))


class TestErrorMessages:
    def test_messages_are_short_and_specific(self):
        import requests as req
        from api import _network_error_message
        long_detail = "HTTPSConnectionPool(host='prim.iledefrance-mobilites.fr') " * 5
        assert _network_error_message(req.ConnectionError(long_detail)) == "Pas de connexion réseau"
        assert "délai" in _network_error_message(req.ConnectTimeout(long_detail))
        assert "délai" in _network_error_message(req.ReadTimeout(long_detail))
        resp = MagicMock(status_code=429)
        assert "quota" in _network_error_message(req.HTTPError(response=resp))

    def test_odsql_sanitize_strips_quotes_and_backslashes(self):
        from api import _sanitize_odsql
        assert _sanitize_odsql('gare "du" nord\\') == "gare du nord"


class TestWiFiScanParsing:
    @patch("api.subprocess.run")
    def test_ssid_with_escaped_colon(self, mock_run):
        from api import WiFiScanWorker
        mock_run.return_value = MagicMock(
            stdout="*:Box\\:Salon:72:WPA2\n :Voisin:40:WPA1 WPA2\n", returncode=0)
        worker = WiFiScanWorker()
        got = []
        worker.finished.connect(got.append)
        worker.run()
        assert [(n["ssid"], n["signal"], n["in_use"]) for n in got[0]] == [
            ("Box:Salon", 72, True), ("Voisin", 40, False)]


class TestFavouriteModel:
    def test_key(self, sample_favourite):
        f = sample_favourite
        assert f.key == f"{f.stop_area_id}_{f.line_id}_{f.direction}_{f.destination_name}"

    def test_load_ignores_unknown_fields(self, tmp_path):
        path = tmp_path / "favs.json"
        path.write_text(json.dumps([{
            "stop_area_id": "1", "stop_name": "A", "line_id": "L", "line_name": "1",
            "added_by_future_version": True}]), encoding="utf-8")
        with patch("models.FAVOURITES_PATH", str(path)):
            assert len(load_favourites()) == 1


class TestWiFiStatus:
    @patch("api.subprocess.run")
    def test_status_worker_reports_ssid(self, mock_run):
        from api import WiFiStatusWorker
        mock_run.return_value = MagicMock(stdout=" :Voisin\n*:Box\\:Salon\n", returncode=0)
        worker = WiFiStatusWorker()
        got = []
        worker.finished.connect(lambda ok, ssid: got.append((ok, ssid)))
        worker.run()
        assert got == [(True, "Box:Salon")]

    @patch("api.subprocess.run", side_effect=FileNotFoundError)
    def test_status_worker_without_nmcli(self, _):
        from api import WiFiStatusWorker
        worker = WiFiStatusWorker()
        got = []
        worker.finished.connect(lambda ok, ssid: got.append((ok, ssid)))
        worker.run()
        assert got == [(False, "")]

    def test_settings_row_shows_connection(self):
        from widgets import SettingsScreen
        s = SettingsScreen()
        s.set_wifi_status(True, "Box")
        assert s.wifi_row_status.text() == "Connecté · Box"
        s.set_wifi_status(True, "")
        assert s.wifi_row_status.text() == "Non connecté"
        s.on_wifi_scan_results([
            {"ssid": "Box", "signal": 70, "security": "WPA2", "in_use": True}])
        assert s.wifi_row_status.text() == "Connecté · Box"
        assert s.wifi_status.text() == "Connecté à Box"


# ═══════════════════════════════════════════════════════════════════════════════
# HEATING (Home Assistant)
# ═══════════════════════════════════════════════════════════════════════════════

import ha
from models import Radiator
from widgets import HeatingScreen, RadiatorCard


def climate_state(entity_id="climate.salon", state="heat", name="Salon",
                  current=19.5, target=20.0, action="idle",
                  modes=("heat", "auto", "off")):
    return {
        "entity_id": entity_id, "state": state,
        "attributes": {
            "friendly_name": name, "hvac_modes": list(modes),
            "min_temp": 4.0, "max_temp": 35.0, "target_temp_step": 0.5,
            "current_temperature": current, "temperature": target,
            "hvac_action": action,
        },
    }


def make_radiator(**kwargs):
    defaults = dict(entity_id="climate.salon", name="Salon", mode="heat",
                    modes=["heat", "auto", "off"], current_temp=19.5,
                    target_temp=20.0, min_temp=4.0, max_temp=35.0, step=0.5)
    defaults.update(kwargs)
    return Radiator(**defaults)


class FakeHomeAssistant:
    """Stands in for requests.request: just enough of Home Assistant's
    auth and REST API, behaving as the real one was observed to."""

    def __init__(self, users=("user-1",), bypass=True, provider=True):
        self.users = list(users)
        self.bypass = bypass
        self.provider = provider
        self.refresh_tokens = set()
        self.access_tokens = set()
        self.states = [climate_state()]
        self.calls = []  # (method, path)
        self.services = []  # (path, json)
        self._n = 0

    def _resp(self, status, payload=None):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        return resp

    def _issue(self, with_refresh):
        self._n += 1
        access = f"access-{self._n}"
        self.access_tokens.add(access)
        payload = {"access_token": access, "expires_in": 1800}
        if with_refresh:
            payload["refresh_token"] = f"refresh-{self._n}"
            self.refresh_tokens.add(payload["refresh_token"])
        return self._resp(200, payload)

    def __call__(self, method, url, headers=None, timeout=None, json=None,
                 data=None):
        path = url.replace(ha.HA_URL, "")
        self.calls.append((method, path))
        if path == "/auth/login_flow":
            if not self.provider:
                return self._resp(500)
            if self.bypass and len(self.users) == 1:
                return self._resp(200, {"type": "create_entry", "result": "code"})
            return self._resp(200, {
                "type": "form", "flow_id": "flow-1", "step_id": "init",
                "data_schema": [{"type": "select", "name": "user",
                                 "options": [[u, u] for u in self.users]}]})
        if path == "/auth/login_flow/flow-1":
            assert json["user"] in self.users
            return self._resp(200, {"type": "create_entry", "result": "code"})
        if path == "/auth/token":
            if data["grant_type"] == "authorization_code":
                return self._issue(with_refresh=True)
            if data["refresh_token"] in self.refresh_tokens:
                return self._issue(with_refresh=False)
            return self._resp(400, {"error": "invalid_grant"})
        token = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        if token not in self.access_tokens:
            return self._resp(401)
        if path == "/api/states":
            return self._resp(200, self.states)
        if path.startswith("/api/services/"):
            self.services.append((path, json))
            return self._resp(200, [])
        return self._resp(404)

    def count(self, path):
        return sum(1 for _, p in self.calls if p == path)


@pytest.fixture
def fake_ha(tmp_path):
    """A fake Home Assistant, with the app's sign-in state starting empty."""
    server = FakeHomeAssistant()
    ha._forget_access_token()
    with patch("ha.requests.request", side_effect=server), \
            patch("ha.AUTH_PATH", str(tmp_path / "ha_auth.json")), \
            patch("ha.HA_TOKEN", ""):
        yield server
    ha._forget_access_token()


class TestRadiatorParsing:
    def test_keeps_only_thermostats_sorted_by_name(self):
        states = [
            climate_state("climate.salon", name="Salon"),
            {"entity_id": "sensor.salon_battery", "state": "87", "attributes": {}},
            climate_state("climate.chambre", name="Chambre"),
        ]
        radiators = ha.parse_radiators(states)
        assert [r.name for r in radiators] == ["Chambre", "Salon"]

    def test_fields(self):
        r = ha.parse_radiators([climate_state(action="heating")])[0]
        assert r.entity_id == "climate.salon"
        assert (r.mode, r.current_temp, r.target_temp) == ("heat", 19.5, 20.0)
        assert (r.min_temp, r.max_temp, r.step) == (4.0, 35.0, 0.5)
        assert r.modes == ["heat", "auto", "off"]
        assert r.available and r.heating

    def test_unavailable_valve(self):
        state = climate_state(state="unavailable", current=None, target=None)
        r = ha.parse_radiators([state])[0]
        assert not r.available
        assert r.mode == "" and r.target_temp is None

    def test_thermostat_suffix_is_dropped_from_the_name(self):
        states = [climate_state(name="Salon Thermostat"),
                  climate_state("climate.t", name="Thermostat")]
        assert sorted(r.name for r in ha.parse_radiators(states)) == [
            "Salon", "Thermostat"]

    def test_missing_attributes_fall_back(self):
        r = ha.parse_radiators([{"entity_id": "climate.bureau", "state": "heat"}])[0]
        assert r.name == "bureau"
        assert (r.min_temp, r.max_temp, r.step) == (5.0, 30.0, 0.5)


class TestHomeAssistantSignIn:
    def test_signs_in_without_password_and_keeps_refresh_token(self, fake_ha):
        radiators = ha.fetch_radiators()
        assert [r.name for r in radiators] == ["Salon"]
        with open(ha.AUTH_PATH) as f:
            assert json.load(f)["refresh_token"] == "refresh-1"
        if os.name == "posix":
            assert os.stat(ha.AUTH_PATH).st_mode & 0o777 == 0o600

    def test_access_token_is_reused(self, fake_ha):
        ha.fetch_radiators()
        ha.fetch_radiators()
        assert fake_ha.count("/auth/login_flow") == 1
        assert fake_ha.count("/auth/token") == 1

    def test_picks_a_user_when_asked(self, fake_ha):
        fake_ha.users = ["owner", "guest"]
        assert ha.fetch_radiators()
        assert fake_ha.count("/auth/login_flow/flow-1") == 1

    def test_no_account_yet_means_onboarding(self, fake_ha):
        fake_ha.users = []
        with pytest.raises(ha.HAError) as err:
            ha.fetch_radiators()
        assert err.value.status == ha.ONBOARDING

    def test_provider_not_enabled(self, fake_ha):
        fake_ha.provider = False
        with pytest.raises(ha.HAError) as err:
            ha.fetch_radiators()
        assert err.value.status == ha.AUTH

    def test_restart_uses_stored_refresh_token(self, fake_ha):
        ha.fetch_radiators()
        ha._forget_access_token()  # what a new process starts with
        ha.fetch_radiators()
        assert fake_ha.count("/auth/login_flow") == 1  # no second sign-in

    def test_rejected_refresh_token_signs_in_again(self, fake_ha):
        ha.fetch_radiators()
        ha._forget_access_token()
        fake_ha.refresh_tokens.clear()  # e.g. Home Assistant reinstalled
        assert ha.fetch_radiators()
        assert fake_ha.count("/auth/login_flow") == 2

    def test_refused_access_token_is_renewed_once(self, fake_ha):
        ha.fetch_radiators()
        fake_ha.access_tokens.clear()
        assert ha.fetch_radiators()

    def test_refresh_token_of_another_instance_is_ignored(self, fake_ha):
        with open(ha.AUTH_PATH, "w") as f:
            json.dump({"url": "http://elsewhere:8123", "refresh_token": "x"}, f)
        ha.fetch_radiators()
        assert fake_ha.count("/auth/login_flow") == 1

    def test_long_lived_token_skips_sign_in(self, fake_ha):
        fake_ha.access_tokens.add("long-lived")
        with patch("ha.HA_TOKEN", "long-lived"):
            assert ha.fetch_radiators()
        assert fake_ha.count("/auth/login_flow") == 0

    def test_unreachable(self):
        ha._forget_access_token()
        with patch("ha.HA_TOKEN", "token"), pytest.raises(ha.HAError) as err:
            ha.fetch_radiators()  # network is disabled by the autouse fixture
        assert err.value.status == ha.UNREACHABLE


class TestHeatingWorkers:
    def test_fetch_reports_radiators(self, fake_ha):
        got = []
        worker = ha.HeatingWorker()
        worker.finished.connect(lambda *args: got.append(args))
        worker.run()
        status, radiators, _ = got[0]
        assert status == ha.OK and radiators[0].name == "Salon"

    def test_fetch_reports_status_on_failure(self, fake_ha):
        fake_ha.users = []
        got = []
        worker = ha.HeatingWorker()
        worker.finished.connect(lambda *args: got.append(args))
        worker.run()
        assert got[0][0] == ha.ONBOARDING and got[0][1] == []

    def test_command_sends_mode_then_temperature(self, fake_ha):
        got = []
        worker = ha.HeatingCommandWorker("climate.salon", temperature=21.5,
                                         mode="heat")
        worker.finished.connect(lambda *args: got.append(args))
        worker.run()
        assert got == [("climate.salon", True)]
        assert fake_ha.services == [
            ("/api/services/climate/set_hvac_mode",
             {"entity_id": "climate.salon", "hvac_mode": "heat"}),
            ("/api/services/climate/set_temperature",
             {"entity_id": "climate.salon", "temperature": 21.5}),
        ]

    def test_command_failure_is_reported(self):
        ha._forget_access_token()
        got = []
        worker = ha.HeatingCommandWorker("climate.salon", temperature=21.0)
        worker.finished.connect(lambda *args: got.append(args))
        with patch("ha.HA_TOKEN", "token"):
            worker.run()
        assert got == [("climate.salon", False)]


class TestRadiatorCard:
    def _signals(self, card):
        temps, modes = [], []
        card.temperature_requested.connect(lambda e, t: temps.append((e, t)))
        card.mode_requested.connect(lambda e, m: modes.append((e, m)))
        return temps, modes

    def test_shows_state(self):
        card = RadiatorCard(make_radiator(heating=True))
        assert card.name_label.text() == "Salon"
        assert card.target_label.text() == "20,0°"
        assert "19,5 °C" in card.info_label.text()
        assert "chauffe" in card.info_label.text()
        assert card.mode_btn.text() == "Manuel"

    def test_taps_are_sent_as_one_command(self):
        card = RadiatorCard(make_radiator())
        temps, _ = self._signals(card)
        card.plus_btn.click()
        card.plus_btn.click()
        card.plus_btn.click()
        assert card.target_label.text() == "21,5°"  # display follows at once
        assert temps == [] and card._temp_timer.isActive()
        card._temp_timer.timeout.emit()
        assert temps == [("climate.salon", 21.5)]

    def test_back_to_the_current_value_sends_nothing(self):
        card = RadiatorCard(make_radiator())
        temps, _ = self._signals(card)
        card.plus_btn.click()
        card.minus_btn.click()
        card._temp_timer.timeout.emit()
        assert temps == []

    def test_stays_within_range(self):
        card = RadiatorCard(make_radiator(target_temp=34.5))
        card.plus_btn.click()
        assert card.target_label.text() == "35,0°"
        assert not card.plus_btn.isEnabled()
        card.plus_btn.click()
        assert card._target == 35.0

    def test_lands_on_a_multiple_of_the_step(self):
        card = RadiatorCard(make_radiator(target_temp=19.3))
        card.plus_btn.click()
        assert card._target == 20.0

    def test_refresh_does_not_undo_a_pending_change(self):
        card = RadiatorCard(make_radiator())
        card.plus_btn.click()
        # The valve hasn't picked the command up yet: still 20.0 there.
        card.update_from(make_radiator(target_temp=20.0))
        assert card.target_label.text() == "20,5°"
        # Once it reports the new value, the hold is over...
        card.update_from(make_radiator(target_temp=20.5))
        assert card._hold_until == 0.0
        # ...and a change made elsewhere (phone, schedule) shows up.
        card.update_from(make_radiator(target_temp=17.0))
        assert card.target_label.text() == "17,0°"

    def test_hold_expires(self):
        card = RadiatorCard(make_radiator())
        card.plus_btn.click()
        card._hold_until = time.monotonic() - 1
        card.update_from(make_radiator(target_temp=20.0))
        assert card.target_label.text() == "20,0°"

    def test_mode_cycles_through_what_the_valve_supports(self):
        card = RadiatorCard(make_radiator())
        _, modes = self._signals(card)
        card.mode_btn.click()
        assert card.mode_btn.text() == "Programme"
        card.mode_btn.click()
        assert card.mode_btn.text() == "Arrêt"
        card._mode_timer.timeout.emit()
        assert modes == [("climate.salon", "off")]

    def test_full_mode_cycle_sends_nothing(self):
        card = RadiatorCard(make_radiator())
        _, modes = self._signals(card)
        for _ in range(3):
            card.mode_btn.click()
        card._mode_timer.timeout.emit()
        assert modes == []

    def test_unsupported_modes_are_skipped(self):
        card = RadiatorCard(make_radiator(modes=["heat", "off", "cool"]))
        card.mode_btn.click()
        assert card.mode_btn.text() == "Arrêt"
        card.mode_btn.click()
        assert card.mode_btn.text() == "Manuel"

    def test_off_hides_target_and_disables_buttons(self):
        card = RadiatorCard(make_radiator(mode="off"))
        assert card.target_label.text() == "—"
        assert not card.plus_btn.isEnabled() and not card.minus_btn.isEnabled()
        assert card.mode_btn.isEnabled()  # the way back on

    def test_unreachable_valve_is_not_adjustable(self):
        card = RadiatorCard(make_radiator(available=False, mode="",
                                          target_temp=None, current_temp=None))
        assert card.info_label.text() == "Injoignable"
        assert not card.plus_btn.isEnabled()
        assert not card.mode_btn.isEnabled()


class TestHeatingScreen:
    def test_radiators_replace_the_message(self):
        screen = HeatingScreen()
        screen.show_loading()
        assert screen.message_label.text() == "Chargement..."
        screen.set_state(ha.OK, [make_radiator()])
        assert list(screen.cards) == ["climate.salon"]
        assert screen.message_label.isHidden() and not screen.scroll.isHidden()
        assert "Mis à jour" in screen.status_label.text()

    def test_refresh_updates_cards_in_place(self):
        screen = HeatingScreen()
        screen.set_state(ha.OK, [make_radiator()])
        card = screen.cards["climate.salon"]
        screen.set_state(ha.OK, [make_radiator(target_temp=22.0)])
        assert screen.cards["climate.salon"] is card
        assert card.target_label.text() == "22,0°"

    def test_new_radiator_rebuilds_the_list(self):
        screen = HeatingScreen()
        screen.set_state(ha.OK, [make_radiator()])
        screen.set_state(ha.OK, [
            make_radiator(entity_id="climate.chambre", name="Chambre"),
            make_radiator()])
        assert list(screen.cards) == ["climate.chambre", "climate.salon"]

    def test_card_requests_are_relayed(self):
        screen = HeatingScreen()
        got = []
        screen.temperature_requested.connect(lambda e, t: got.append((e, t)))
        screen.set_state(ha.OK, [make_radiator()])
        card = screen.cards["climate.salon"]
        card.minus_btn.click()
        card._temp_timer.timeout.emit()
        assert got == [("climate.salon", 19.5)]

    def test_account_not_created_yet(self):
        screen = HeatingScreen()
        with patch("ha.setup_url", return_value="http://192.168.1.20:8123"):
            screen.set_state(ha.ONBOARDING, [])
        assert "Créez votre compte" in screen.message_label.text()
        assert "http://192.168.1.20:8123" in screen.message_label.text()

    def test_no_radiator_paired_yet(self):
        screen = HeatingScreen()
        with patch("ha.setup_url", return_value="http://192.168.1.20:8123"):
            screen.set_state(ha.OK, [])
        assert "Aucun radiateur" in screen.message_label.text()
        assert "http://192.168.1.20:8123" in screen.message_label.text()

    def test_not_answering_yet(self):
        screen = HeatingScreen()
        screen.set_state(ha.UNREACHABLE, [])
        assert "ne répond pas encore" in screen.message_label.text()

    def test_hiccup_keeps_radiators_on_screen(self):
        screen = HeatingScreen()
        screen.set_state(ha.OK, [make_radiator()])
        screen.set_state(ha.UNREACHABLE, [])
        assert list(screen.cards) == ["climate.salon"]
        assert screen.status_label.text() == "Home Assistant ne répond pas"

    def test_failed_command_is_flagged_and_truth_restored(self):
        screen = HeatingScreen()
        screen.set_state(ha.OK, [make_radiator()])
        card = screen.cards["climate.salon"]
        card.plus_btn.click()
        screen.on_command_result("climate.salon", False)
        assert "non transmise" in screen.status_label.text()
        screen.set_state(ha.OK, [make_radiator(target_temp=20.0)])
        assert card.target_label.text() == "20,0°"


class TestMainWindowHeating:
    @patch("main.load_favourites", return_value=[])
    def test_no_heating_button_without_home_assistant(self, mock_load):
        from main import MainWindow
        with patch("main.ha.is_configured", return_value=False):
            w = MainWindow()
        assert w.home.heating_btn.isHidden()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_heating_button_opens_the_screen_and_polls(self, mock_load):
        from main import MainWindow, HEATING_PAGE
        with patch("main.ha.is_configured", return_value=True):
            w = MainWindow()
        assert not w.home.heating_btn.isHidden()
        with patch.object(w, "_launch_worker") as launch:
            w.home.heating_btn.click()
            assert w.stack.currentIndex() == HEATING_PAGE
            assert w.heating_timer.isActive()
            assert isinstance(launch.call_args[0][0], ha.HeatingWorker)
            # One fetch at a time
            w._refresh_heating()
            assert launch.call_count == 1
        w._on_heating_state(ha.OK, [make_radiator()], "")
        assert list(w.heating.cards) == ["climate.salon"]
        w._show_home()
        assert w.stack.currentIndex() == 0 and not w.heating_timer.isActive()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_no_polling_away_from_the_heating_screen(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        with patch.object(w, "_launch_worker") as launch:
            w._refresh_heating()
            launch.assert_not_called()
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_card_change_sends_a_command(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        w.heating.set_state(ha.OK, [make_radiator()])
        card = w.heating.cards["climate.salon"]
        with patch.object(w, "_launch_worker") as launch:
            card.plus_btn.click()
            card._temp_timer.timeout.emit()
            worker = launch.call_args[0][0]
        assert isinstance(worker, ha.HeatingCommandWorker)
        assert (worker.entity_id, worker.temperature, worker.mode) == (
            "climate.salon", 20.5, None)
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_left_alone_returns_to_departures(self, mock_load):
        from main import MainWindow, HEATING_PAGE, HEATING_IDLE_RETURN_S
        w = MainWindow()
        with patch.object(w, "_launch_worker"), \
                patch.object(MainWindow, "_is_nocturnal", return_value=False):
            w._show_heating()
            w._on_countdown_tick()
            assert w.stack.currentIndex() == HEATING_PAGE
            w._last_interaction_time = time.monotonic() - HEATING_IDLE_RETURN_S - 1
            w._on_countdown_tick()
            assert w.stack.currentIndex() == 0
        w.close()

    @patch("main.load_favourites", return_value=[])
    def test_sleep_from_heating_screen_wakes_on_departures(self, mock_load):
        from main import MainWindow
        w = MainWindow()
        with patch.object(w, "_launch_worker"), \
                patch.object(w, "_set_backlight"):
            w._show_heating()
            w._enter_sleep()
            assert w.stack.currentIndex() == 0
            assert not w.heating_timer.isActive()
            w._wake_up()
        w.close()
