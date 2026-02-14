# Prochains Departs

Real-time departure display for Ile-de-France public transport (IDFM), built for a Raspberry Pi 4 with a 7" touchscreen.

Shows upcoming departures for your saved stops with live countdowns, line badges, and direction filtering. Designed for kiosk use — runs fullscreen, auto-refreshes, and hides the cursor on ARM devices.

## Features

- **Multi-mode search**: Bus, Metro, Tramway, Train/RER with transport type icons
- **Direction filtering**: Choose which direction to monitor at each stop
- **Live countdowns**: Updated every second from SIRI Lite real-time data
- **Favourites**: Saved locally with atomic writes (crash-safe)
- **Auto-refresh**: Every 1 minute (pauses 2am-5am)
- **Touch-optimised**: 800x480 layout with kinetic scrolling and virtual AZERTY keyboard
- **Settings screen**: API token, WiFi configuration, theme, sleep delay
- **WiFi configuration**: Scan and connect to networks via nmcli
- **Dark/light theme**: Toggle between themes in settings
- **Sleep mode**: Turns screen off after configurable idle time (5/10/30 min), tap to wake
- **Kiosk mode**: Auto-fullscreen and hidden cursor on Raspberry Pi

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
- Restarts automatically if it crashes (`Restart=always`)
- App updates daily at 3:30am from GitHub (only restarts if code changed)
- OS security updates install daily, auto-reboot at 4am if needed
- Hardware watchdog reboots the Pi if it freezes

### Updating the app

The app automatically checks for updates daily at 3:30am (via a systemd timer). If new code is found on GitHub, it pulls and restarts the service. OS security patches are also automatic (via `unattended-upgrades`).

To update manually:

```bash
ssh pi@prochains-departs.local
cd /home/pi/app && ./update.sh
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
├── setup-pi.sh                   Pi setup script (packages, systemd, watchdog, timezone)
├── prepare-sd.ps1                Windows script to prepare SD card for autonomous setup
├── update.sh                     Created on the Pi by setup-pi.sh — pulls code and restarts
└── .env                          API token (not committed)
```

## License

Private project.
