# Prochains Departs

Real-time departure display for Ile-de-France public transport (IDFM), built for a Raspberry Pi 4 with a 7" touchscreen.

Shows upcoming departures for your saved stops with live countdowns, line badges, and direction filtering. Designed for kiosk use — runs fullscreen, auto-refreshes, and hides the cursor on ARM devices.

## Features

- **Two search flows**: by stop name, or by transport type (Bus, Metro, Tramway, Train/RER) then line
- **Direction filtering**: Choose which direction to monitor at each stop
- **Live countdowns**: Updated every second from SIRI Lite real-time data, computed against the server clock (immune to Pi clock drift)
- **Favourites**: Saved locally with atomic writes (crash-safe)
- **Auto-refresh**: Every 1 minute, stops fetched in parallel (pauses 2am-5am). A failed refresh is retried after 5, 10, 20 and 40 s instead of waiting a full minute, and the last known departures stay on screen meanwhile
- **Touch-optimised**: 800x480 layout with kinetic scrolling, tap feedback, and virtual AZERTY keyboard with an accent/symbol layer
- **Settings screen**: API token, WiFi configuration, theme, sleep delay
- **WiFi configuration**: Scan and connect to networks via nmcli
- **Dark/light theme**: Toggle between themes in settings
- **Sleep mode**: Turns screen off after configurable idle time (5/10/30 min), tap to wake. While asleep the app keeps refreshing every 5 min, so departures show the instant it wakes and the WiFi link never sits idle
- **Night screen**: During the 2am-5am refresh pause the display sleeps instead of showing stale data, and wakes automatically at 5am
- **Kiosk mode**: Auto-fullscreen and hidden cursor on Raspberry Pi
- **Field debugging**: Rotating log file (`app.log`) next to the app

## Architecture

| File | Role |
|---|---|
| `main.py` | Entry point, MainWindow, timers, navigation, sleep mode |
| `widgets.py` | UI widgets (HomeScreen, SearchScreen, SettingsScreen, SleepOverlay, VirtualKeyboard) |
| `api.py` | QThread-based API workers (SIRI Lite, IDFM Open Data, WiFi) |
| `models.py` | Dataclasses, shared helpers, JSON persistence (atomic writes) |
| `styles.py` | QSS theme stylesheets and Material Icons helpers |

API workers run on background `QThread`s with catch-all error handling to prevent thread leaks. The main thread handles UI updates and countdown interpolation.

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

### Autonomous setup (no keyboard needed)

1. **Flash the SD card** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Choose *Raspberry Pi OS Lite (64-bit)* and click the settings icon to configure:
   - Username: `pi`, set a password
   - Enable SSH (for remote access later)
   - Configure WiFi (SSID + password), or use Ethernet

2. **Prepare the SD card** — with the SD card still in the reader, run:

```powershell
powershell -ExecutionPolicy Bypass -File prepare-sd.ps1
```

3. **Eject, insert, power on.** The Pi handles everything automatically:
   - First boot: creates user, connects to WiFi, reboots
   - Second boot: installs packages, clones the app from GitHub, reboots
   - Third boot: the app starts on the touchscreen

4. **Enter your API token** via the Settings screen on the touchscreen (or via SSH: `nano /home/pi/app/.env`).

### Manual setup (via SSH)

If you prefer to set up manually, flash with Raspberry Pi Imager (configure user + SSH + WiFi), boot the Pi, then:

```bash
ssh pi@raspberrypi.local
curl -sSL https://raw.githubusercontent.com/Tcha182/Prochains_Departs/main/setup-pi.sh | sudo bash
nano /home/pi/app/.env   # set your API token
sudo reboot
```

### After setup

The Pi is fully autonomous:
- Timezone set to `Europe/Paris`
- Auto-login on tty1, starts X11 with Openbox
- App launches via systemd (`departure-display.service`, `After=multi-user.target`)
- Restarts automatically if it crashes (`Restart=always`) or hangs (systemd watchdog, `WatchdogSec=120` — the app pings every 30s)
- App **and system config** update from GitHub about a minute after boot and daily at 3:30am (app only restarts if code changed; never delays boot)
- OS updates (Debian security + Raspberry Pi kernel/firmware) install daily, auto-reboot at 4am if needed
- Hardware watchdog reboots the Pi if it freezes
- Network hardening: WiFi power saving off, WiFi reconnects forever, and a network watchdog (every 2 min) that restarts NetworkManager when the IDFM APIs are unreachable, or reboots if the local network stays down ~10 min
- The app restarts itself if departures haven't refreshed for 15 min while awake
- **Paramètres** shows the running version (commit + date), to check an update actually landed

### Updating the app and the system

Everything the Pi needs lives in the repo:

- **`update.sh`** — run by `app-update.timer` about a minute after each boot and nightly at 3:30am. Fast-forwards to GitHub's `main`, then runs `pi-system.sh`, then restarts the app if the code changed. Offline or diverged checkout: keeps the current version and retries next time.
- **`pi-system.sh`** — the whole system configuration (packages, systemd units, watchdogs, WiFi hardening, unattended-upgrades). Idempotent: files are only rewritten, and services only reloaded, when their content changes.

So a change to the system config is deployed exactly like a code change: commit, push, and the Pi applies it at the next update check. No SSH needed.

You can also trigger an app update from **Paramètres > Mise à jour** on the touchscreen (code only; the system config follows at the next scheduled run), or by SSH:

```bash
ssh pi@<pi-ip>
/home/pi/app/update.sh
```

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
├── setup-pi.sh                   One-time Pi setup (installs git, clones, runs pi-system.sh)
├── pi-system.sh                  Pi system config, re-applied on every update (idempotent)
├── update.sh                     Pulls code, applies pi-system.sh, restarts the app
├── prepare-sd.ps1                Windows script to prepare SD card for autonomous setup
└── .env                          API token (not committed)
```

## License

Private project.
