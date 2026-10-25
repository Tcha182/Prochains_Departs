# Prochains Departs

Real-time departure display for Ile-de-France public transport (IDFM), built for a Raspberry Pi 4 with a 7" touchscreen.

Shows upcoming departures for your saved stops with live countdowns, line badges, and direction filtering. Designed for kiosk use — runs fullscreen, auto-refreshes, and hides the cursor on ARM devices.

## Features

- **Two search flows**: by stop name, or by transport type (Bus, Metro, Tramway, Train/RER) then line
- **Direction filtering**: Choose which direction to monitor at each stop (branches of the same line, e.g. RER B to Robinson / Saint-Remy, are kept apart)
- **Traffic info**: Current disruptions for each favourite's line, refreshed every 5 minutes
- **Clock**: Time and date in the home screen header
- **Live countdowns**: Updated every second from SIRI Lite real-time data, computed against the server clock (immune to Pi clock drift)
- **Favourites**: Saved locally with atomic writes (crash-safe); reorder them with the arrows in edit mode. An unreadable `favourites.json` is kept aside as `favourites.json.corrupt-<date>` instead of being overwritten
- **Clear status**: Cancelled trains show "Annule" instead of a countdown; a stop whose last refresh failed is flagged "Donnees non actualisees"
- **Auto-refresh**: Every 1 minute, stops fetched in parallel (pauses 2am-5am)
- **Touch-optimised**: 800x480 layout with kinetic scrolling, tap feedback, and virtual AZERTY keyboard with an accent/symbol layer
- **Settings screen**: API token, WiFi configuration, theme, sleep delay, evening brightness
- **Evening dimming**: Backlight lowered to 50 % (or 25 %, or off) between 21h and 7h
- **WiFi configuration**: Scan and connect to networks via nmcli
- **Dark/light theme**: Toggle between themes in settings
- **Sleep mode**: Turns screen off after configurable idle time (5/10/30 min), tap to wake
- **Night screen**: During the 2am-5am refresh pause the display sleeps instead of showing stale data, and wakes automatically at 5am
- **Kiosk mode**: Auto-fullscreen and hidden cursor on Raspberry Pi
- **Field debugging**: Rotating log file (`app.log`) next to the app

## Architecture

| File | Role |
|---|---|
| `main.py` | Entry point, MainWindow, timers, navigation, sleep mode |
| `widgets/` | UI widgets, one module per screen: `home.py`, `search.py`, `settings.py`, `keyboard.py`, plus shared touch helpers in `common.py` |
| `api.py` | QThread-based API workers (SIRI Lite departures and traffic info, IDFM Open Data, WiFi) |
| `models.py` | Dataclasses, shared helpers, JSON persistence (atomic writes) |
| `styles.py` | QSS theme stylesheets and Material Icons helpers |

API workers run on background `QThread`s with catch-all error handling to prevent thread leaks. The main thread handles UI updates and countdown interpolation.

## Requirements

- Python 3.10+
- PyQt5
- `requests`, `python-dotenv`
- An [IDFM PRIM API key](https://prim.iledefrance-mobilites.fr/). For traffic info, also subscribe the key to PRIM's *Messages Info Trafic* (SIRI Lite general-message) API; without it the app works the same, just without disruption messages

## Local Setup

1. Clone the repository and create a `.env` file:

```
API_TOKEN=your-idfm-api-token
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run:

```bash
python main.py
```

## Testing

```bash
QT_QPA_PLATFORM=offscreen pytest test_app.py -v -m "not live"
```

These run on GitHub Actions for every push to `main` and every pull request (`.github/workflows/tests.yml`).

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
- App updates from GitHub about a minute after boot and daily at 3:30am (only restarts if code changed; never delays boot)
- OS security updates install daily, auto-reboot at 4am if needed
- Hardware watchdog reboots the Pi if it freezes
- Network hardening (`pi-network-setup.sh`): WiFi power saving off, WiFi reconnects forever, and a network watchdog (every 2 min) that restarts NetworkManager when the IDFM APIs are unreachable, or reboots if the local network stays down ~10 min
- The app restarts itself if departures haven't refreshed for 15 min while awake

### Updating the app

The app automatically checks for updates about a minute after each boot and daily at 3:30am (via a systemd timer, so boot never waits on it). If new code is found on GitHub, it fast-forwards and restarts the service; if the network is down or the fetch fails, it keeps running the current version and retries next time. OS security patches are also automatic (via `unattended-upgrades`).

You can also trigger a check on demand from **Parametres > Mise a jour** on the touchscreen: it pulls from GitHub and, if there's new code, exits so `Restart=always` relaunches the app with it.

To update manually via SSH:

```bash
ssh pi@prochains-departs.local
cd /home/pi/app && ./update.sh
```

**Pi set up before the network hardening?** Run it once (idempotent):

```bash
ssh pi@prochains-departs.local
cd /home/pi/app && git pull && sudo ./pi-network-setup.sh
```

Note: the systemd unit is written once at setup time. To pick up unit changes
(e.g. the watchdog) on an already-deployed Pi, re-run `setup-pi.sh` or edit
`/etc/systemd/system/departure-display.service` by hand, then
`sudo systemctl daemon-reload && sudo systemctl restart departure-display`.

## Project Structure

```
.
├── main.py                       App entry point
├── widgets/                      PyQt5 UI widgets (home, search, settings, keyboard)
├── api.py                        API workers (SIRI Lite, IDFM Open Data, WiFi)
├── models.py                     Dataclasses and persistence
├── styles.py                     QSS stylesheets + icon helpers
├── MaterialIcons-Regular.ttf     Material Icons font (Google)
├── test_app.py                   Test suite
├── requirements.txt              Python dependencies
├── .github/workflows/tests.yml   CI: lint + unit tests
├── setup-pi.sh                   Pi setup script (packages, systemd, watchdog, timezone)
├── pi-network-setup.sh           Network hardening (WiFi power save off, network watchdog)
├── prepare-sd.ps1                Windows script to prepare SD card for autonomous setup
├── update.sh                     Created on the Pi by setup-pi.sh — pulls code and restarts
└── .env                          API token (not committed)
```

## License

Private project.
