---
name: verify
description: Run and drive the PyQt5 kiosk app headlessly to verify changes end-to-end (touch scrolling, taps, keyboard, sleep mode).
---

# Verifying Prochains Departs

## Setup

```bash
pip install PyQt5 pytest python-dotenv
# xcb platform needs these on a bare container:
apt-get install -y libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0 libxcb-shape0 \
  libxcb-xinerama0 xdotool
Xvfb :99 -screen 0 800x480x24 -dpi 96 &
```

## Unit tests (fast sanity, not verification)

```bash
QT_QPA_PLATFORM=offscreen python -m pytest test_app.py -m "not live" -q
```

## Driving the real app

Gotchas learned the hard way:

- **QScroller ignores synthetic events.** `QTest.mousePress`/`sendEvent(QMouseEvent)`
  and `QTest.touchEvent` are all non-spontaneous — the flick gesture recognizer
  never engages, drags scroll 0px. Only real X input works: run under
  `DISPLAY=:99 QT_QPA_PLATFORM=xcb` and inject with `xdotool mousemove/mousedown/
  mousemove.../mouseup`, pumping the Qt loop (`QTest.qWait`) between steps.
- **Stub the network before importing `main`:** replace `api.requests.get` with a
  fake returning `{"Siri": {"ServiceDelivery": {"StopMonitoringDelivery":
  [{"MonitoredStopVisit": []}]}}}`, and `patch("main.load_favourites")`.
- **`patch.object(w, "_on_line_search")` does NOT stop workers** — signal
  connections hold the real bound method. Disconnect all five SearchScreen
  request signals (`line_search_requested`, `stops_on_line_requested`,
  `resolve_and_probe_requested`, `stop_area_search_requested`,
  `line_details_requested`) instead, or async empty results will wipe
  fixture rows mid-test.
- Taps must wait ~450ms after release: QScroller withholds the press and replays
  press+release on release.
- Before asserting scroller state, wait until `QScroller.scroller(vp).state()`
  is Inactive (prior flick may still be decelerating).
- Screenshot with `w.grab().save(path)`.

Flows worth driving: kinetic scroll on home (drag viewport), drag starting on a
result row (must scroll, never select — including after a 400ms finger rest),
tap a row (must select), populate/refresh during a drag (must defer), virtual
keyboard typing with shift, sleep overlay tap-to-wake.

## Heating screen (Home Assistant)

Verify against a real Home Assistant, not only the `FakeHomeAssistant` in the tests.

- **Get one running.** Either `pip install homeassistant` in a venv and `hass -c <dir>`,
  or start `dockerd` and run the container with the exact `ExecStart` from
  `pi-system.sh`. In a cloud sandbox `ghcr.io` blobs are blocked: pull
  `homeassistant/home-assistant:stable` from Docker Hub and `docker tag` it.
- **Config:** take the seed `configuration.yaml` from `pi-system.sh` (the
  `trusted_networks` block is what lets the app sign in) and append test
  thermostats: `generic_thermostat` on an `input_boolean` heater and an
  `input_number` target sensor.
- **Create the account** without a browser:
  `POST /api/onboarding/users` with `client_id`, `name`, `username`, `password`, `language`.
  Before that, the app must report `ha.ONBOARDING`.
- **Show the button:** run the app with `HA_URL=http://127.0.0.1:8123` (off the Pi,
  `ha.is_configured()` is otherwise false). `ha.py` uses `requests.request`, so
  stubbing `requests.get` for IDFM leaves it working.
- Delete `ha_auth.json` between instances: it holds a refresh token for one of them.
- Icons: a glyph can render in `offscreen` and not under `xcb` (U+E1FF did). Check
  new icons in an `xcb` screenshot.

Flows worth driving: tap + several times (display follows at once, one command
after ~0.8 s), mode button through to Arrêt and back, a refresh while a change is
pending (must not flip back), back button, idle return to home.
