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
  connections hold the real bound method. Disconnect the SearchScreen signals
  (`w.search.line_search_requested.disconnect()`, etc.) instead, or async empty
  results will wipe fixture rows mid-test.
- Taps must wait ~450ms after release: QScroller withholds the press and replays
  press+release on release.
- Before asserting scroller state, wait until `QScroller.scroller(vp).state()`
  is Inactive (prior flick may still be decelerating).
- Screenshot with `w.grab().save(path)`.

Flows worth driving: kinetic scroll on home (drag viewport), drag starting on a
result row (must scroll, never select — including after a 400ms finger rest),
tap a row (must select), populate/refresh during a drag (must defer), virtual
keyboard typing with shift, sleep overlay tap-to-wake.
