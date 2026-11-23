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
- **Clear status**: Cancelled trains show "Annulé" instead of a countdown; a stop whose last refresh failed is flagged "Données non actualisées"
- **Auto-refresh**: Every 1 minute, stops fetched in parallel (pauses 2am-5am). A failed refresh is retried after 5, 10, 20 and 40 s instead of waiting a full minute, and the last known departures stay on screen meanwhile
- **Touch-optimised**: 800x480 layout with kinetic scrolling, tap feedback, and virtual AZERTY keyboard with an accent/symbol layer
- **Settings screen**: API token, WiFi configuration, theme, sleep delay, evening brightness
- **Evening dimming**: Backlight lowered to 50 % (or 25 %, or off) between 21h and 7h
- **WiFi configuration**: Scan and connect to networks via nmcli
- **Dark/light theme**: Toggle between themes in settings
- **Sleep mode**: Turns screen off after configurable idle time (5/10/30 min), tap to wake. While asleep the app keeps refreshing every 5 min, so departures show the instant it wakes and the WiFi link never sits idle
- **Night screen**: During the 2am-5am refresh pause the display sleeps instead of showing stale data, and wakes automatically at 5am
- **Kiosk mode**: Auto-fullscreen and hidden cursor on Raspberry Pi
- **Heating**: Radiator valves driven from the touchscreen (room temperature, target −/+, mode), through a Home Assistant running on the same Pi — see [Radiators](#radiators-home-assistant)
- **Field debugging**: Rotating log file (`app.log`) next to the app

## Architecture

| File | Role |
|---|---|
| `main.py` | Entry point, MainWindow, timers, navigation, sleep mode |
| `widgets/` | UI widgets, one module per screen: `home.py`, `search.py`, `settings.py`, `heating.py`, `keyboard.py`, plus shared touch helpers in `common.py` |
| `api.py` | QThread-based API workers (SIRI Lite departures and traffic info, IDFM Open Data, WiFi) |
| `ha.py` | Home Assistant client and workers (sign-in, radiator state, commands) |
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

## Radiators (Home Assistant)

The Pi can also drive Zigbee thermostatic radiator valves. Built for SONOFF TRVZB valves with a SONOFF ZBDongle-P (CC2652P) Zigbee stick; any thermostat Home Assistant exposes as a `climate` entity shows up the same way.

### What gets installed

On a Pi 4 with at least 2 GB of RAM and 9 GB free on the SD card, `pi-system.sh` installs Docker and runs [Home Assistant Container](https://www.home-assistant.io/installation/) next to the display (`home-assistant.service`). Nothing to do by hand: it arrives with the next app update, and the first start downloads about 700 MB (a few GB once unpacked). On a smaller Pi nothing is installed and the heating button never appears; `journalctl -u app-update` says why.

- Configuration and data live in `/home/pi/homeassistant`. `configuration.yaml` is written once and is then yours to edit.
- Home Assistant updates itself every Monday around 4:30am (`home-assistant-update.timer`).
- It runs with half the CPU priority of the display, and keeps one week of history to spare the SD card.
- The touchscreen app signs in by itself: Home Assistant trusts connections coming from the Pi itself (`trusted_networks` limited to `127.0.0.1`), so there is no token to type. Other devices still need the account password.

### First-time setup

1. Once Home Assistant is installed, a thermometer button appears in the home screen header. Until your account exists, the heating screen shows the address to open.
2. From a phone or a computer on the same network, open `http://<pi-ip>:8123` and create your account.
3. Plug the Zigbee stick into a **USB 2 (black) port, on its extension cable**: the Pi 4's USB 3 ports jam the 2.4 GHz band. Home Assistant restarts by itself to see the stick, then offers the Zigbee integration under *Settings > Devices & services*.
4. Add each valve from the Zigbee integration (*Add device*, with the valve in pairing mode), and name the device after its room.
5. The radiators now show on the touchscreen.

### On the touchscreen

The screen follows Home Assistant's areas: first the rooms, then the radiators of the chosen room (devices without an area are under *Autres*, and a home with a single room opens on its radiators directly). Each radiator has its room temperature, a −/+ target, and a mode button: *Manuel* holds the target you set, *Programme* follows the valve's own schedule, *Arrêt* closes it. Taps are sent as one command once you stop tapping. Left alone for two minutes, the screen returns to the departures.

Schedules, automations and anything beyond these three controls are done in Home Assistant's own interface.

### History and statistics

`ha_sync.py`, run at each system update (after boot and nightly), gives every thermostat its history sensors (`packages/chauffage_vannes.yaml` in the Home Assistant config) and rebuilds the *Chauffage* dashboard: an overview, then one view per room. To add a radiator, pair it and put it in an area; it is picked up at the next update. A dashboard you edited by hand is left alone.

### Home Assistant running elsewhere

To control a Home Assistant on another machine, set its address and a long-lived access token in `.env`:

```
HA_URL=http://192.168.1.20:8123
HA_TOKEN=your-long-lived-token
```

## Project Structure

```
.
├── main.py                       App entry point
├── widgets/                      PyQt5 UI widgets (home, search, settings, heating, keyboard)
├── api.py                        API workers (SIRI Lite, IDFM Open Data, WiFi)
├── ha.py                         Home Assistant client (radiators)
├── models.py                     Dataclasses and persistence
├── styles.py                     QSS stylesheets + icon helpers
├── MaterialIcons-Regular.ttf     Material Icons font (Google)
├── test_app.py                   Test suite
├── requirements.txt              Python dependencies
├── .github/workflows/tests.yml   CI: lint + unit tests
├── setup-pi.sh                   One-time Pi setup (installs git, clones, runs pi-system.sh)
├── pi-system.sh                  Pi system config, re-applied on every update (idempotent)
├── update.sh                     Pulls code, applies pi-system.sh, restarts the app
├── prepare-sd.ps1                Windows script to prepare SD card for autonomous setup
└── .env                          API token (not committed)
```

## License

Private project.
