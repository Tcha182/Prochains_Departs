# Prochains Departs

Real-time departure display for Ile-de-France public transport (IDFM), built for a Raspberry Pi 4 with a 7" touchscreen.

Shows upcoming departures for your saved stops with live countdowns, line badges, and direction filtering. Designed for kiosk use — runs fullscreen, auto-refreshes, and hides the cursor on ARM devices.

## Features

- **Multi-mode search**: Bus, Metro, Tramway, Train/RER with transport type icons
- **Direction filtering**: Choose which direction to monitor at each stop
- **Live countdowns**: Updated every second from SIRI Lite real-time data
- **Favourites**: Saved locally, persisted across restarts
- **Auto-refresh**: Every 1 minute (pauses 2am-5am)
- **Touch-optimised**: 800x480 layout with kinetic scrolling
- **Settings screen**: API token, WiFi configuration, theme, sleep mode
- **WiFi configuration**: Scan and connect to networks via nmcli
- **Dark/light theme**: Toggle between themes in settings
- **Sleep mode**: Screen dims during configurable overnight hours
- **Kiosk mode**: Auto-fullscreen and hidden cursor on Raspberry Pi

## Architecture

| File | Role |
|---|---|
| `main.py` | Entry point, MainWindow, timers, navigation |
| `widgets.py` | UI widgets (HomeScreen, SearchScreen, SettingsScreen, SleepOverlay) |
| `api.py` | QThread-based API workers (SIRI Lite, IDFM Open Data, WiFi) |
| `models.py` | Dataclasses, shared helpers, JSON persistence |
| `styles.py` | QSS theme stylesheets (dark and light) |

API workers run on background `QThread`s. The main thread handles UI updates and countdown interpolation.

## Requirements

- Python 3.10+
- PyQt5
- `requests`, `python-dotenv`
- An [IDFM PRIM API key](https://prim.iledefrance-mobilites.fr/)

## Local Setup

1. Clone the repository and create a `.env` file:

```
API_TOKEN=your-idfm-api-token
```

2. Install dependencies:

```bash
pip install PyQt5 requests python-dotenv
```

3. Run:

```bash
python main.py
```

## Testing

```bash
pip install pytest
pytest test_app.py -v -m "not live"
```

Live integration tests (require network + valid API key):

```bash
pytest test_app.py -v -m live
```

## Raspberry Pi Deployment

Target: Raspberry Pi 4 + official 7" touchscreen (800x480) running Raspberry Pi OS Lite (Bookworm, 64-bit).

### One-time setup

```bash
chmod +x setup-pi.sh
sudo ./setup-pi.sh
```

This installs Python dependencies natively, sets up a minimal X11 environment (Xorg + Openbox), configures auto-login, and creates a systemd service for the app.

### After setup

1. Edit `/home/pi/app/.env` with your real API token
2. Reboot

The Pi will:
- Auto-login on tty1
- Start X11 with Openbox
- Launch the app via systemd (`departure-display.service`)
- Restart the app automatically if it crashes

### Updating

SSH into the Pi and run the update script:

```bash
ssh pi@<ip>
cd /home/pi/app && ./update.sh
```

This pulls the latest code from git and restarts the systemd service.

## Project Structure

```
.
├── main.py                       App entry point
├── widgets.py                    PyQt5 UI widgets
├── api.py                        API workers (SIRI Lite, IDFM Open Data, WiFi)
├── models.py                     Dataclasses and persistence
├── styles.py                     QSS stylesheets + icon helpers
├── MaterialIcons-Regular.ttf     Material Icons font (Google)
├── test_app.py                   Test suite
├── setup-pi.sh                   One-time Pi setup script
└── .env                          API token (not committed)
```

## License

Private project.
