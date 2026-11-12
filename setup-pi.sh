#!/usr/bin/env bash
# setup-pi.sh — One-time setup for Raspberry Pi 4 + 7" touchscreen
# Run on a fresh Raspberry Pi OS Lite (64-bit):
#   chmod +x setup-pi.sh && sudo ./setup-pi.sh
#
# Only does what can't live in the repo yet (getting git and the code).
# Everything else — packages, services, watchdogs, OS updates — is in
# pi-system.sh, which update.sh re-applies after every update check.
set -euo pipefail

APP_DIR="/home/pi/app"
PI_USER="pi"
REPO_URL="https://github.com/Tcha182/Prochains_Departs.git"

echo "==> Installing git..."
apt-get update && apt-get install -y --no-install-recommends git ca-certificates

echo "==> Cloning application..."
if [ -d "$APP_DIR/.git" ]; then
    echo "    App directory already exists, pulling latest..."
    sudo -u "$PI_USER" git -C "$APP_DIR" pull --ff-only
else
    sudo -u "$PI_USER" git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> Applying system configuration (pi-system.sh)..."
bash "$APP_DIR/pi-system.sh"

echo "==> Creating placeholder data files..."
if [ ! -f "$APP_DIR/.env" ]; then
    printf '# Replace with your real API token\nAPI_TOKEN=your-api-token-here\n' > "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
fi
[ -f "$APP_DIR/favourites.json" ] || echo '[]' > "$APP_DIR/favourites.json"
[ -f "$APP_DIR/settings.json" ] || echo '{}' > "$APP_DIR/settings.json"

chown -R "$PI_USER:$PI_USER" "$APP_DIR"

echo ""
echo "==> Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Set your API token (Settings screen, or edit $APP_DIR/.env)"
echo "  2. Reboot: sudo reboot"
echo ""
echo "After reboot the Pi is fully autonomous:"
echo "  - App starts automatically on the touchscreen"
echo "  - App + system config update from GitHub at boot and nightly (3:30am)"
echo "  - OS security updates install daily, auto-reboot at 4am if needed"
echo "  - Hardware watchdog reboots the Pi if it ever freezes"
echo "  - Network watchdog restarts WiFi (or reboots) if the network drops"
