#!/usr/bin/env bash
# setup-pi.sh — One-time setup for Raspberry Pi 4 + 7" touchscreen
# Run on a fresh Raspberry Pi OS Lite (Bookworm, 64-bit):
#   chmod +x setup-pi.sh && sudo ./setup-pi.sh
set -euo pipefail

APP_DIR="/home/pi/app"
PI_USER="pi"
REPO_URL="https://github.com/Tcha182/Prochains_Departs.git"

echo "==> Installing system packages..."
apt-get update && apt-get install -y --no-install-recommends \
    xserver-xorg \
    xinit \
    openbox \
    unclutter \
    x11-xserver-utils \
    network-manager \
    python3-pyqt5 \
    git

echo "==> Setting timezone..."
timedatectl set-timezone Europe/Paris

echo "==> Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

echo "==> Installing Python dependencies..."
uv pip install --system --break-system-packages requests python-dotenv

echo "==> Setting up automatic OS updates..."
apt-get install -y --no-install-recommends unattended-upgrades
cat > /etc/apt/apt.conf.d/50unattended-upgrades << 'UUCONF'
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=${distro_codename},label=Debian";
    "origin=Debian,codename=${distro_codename},label=Debian-Security";
    "origin=Raspbian,codename=${distro_codename},label=Raspbian";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
UUCONF
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'AUTOCONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
AUTOCONF

echo "==> Enabling hardware watchdog..."
# Pi 4 has a built-in BCM2835 watchdog — reboots if system freezes
if ! grep -q 'dtparam=watchdog=on' /boot/firmware/config.txt 2>/dev/null; then
    echo 'dtparam=watchdog=on' >> /boot/firmware/config.txt
fi
apt-get install -y --no-install-recommends watchdog
cat > /etc/watchdog.conf << 'WDCONF'
watchdog-device = /dev/watchdog
watchdog-timeout = 15
max-load-1 = 24
WDCONF
systemctl enable watchdog

echo "==> Configuring auto-login on tty1..."
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'AUTOLOGIN'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin pi --noclear %I $TERM
AUTOLOGIN

echo "==> Cloning application..."
if [ -d "$APP_DIR/.git" ]; then
    echo "    App directory already exists, pulling latest..."
    sudo -u "$PI_USER" git -C "$APP_DIR" pull
else
    sudo -u "$PI_USER" git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> Hardening network (WiFi power save, reconnects, watchdog)..."
bash "$APP_DIR/pi-network-setup.sh"

echo "==> Creating placeholder .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" << 'DOTENV'
# Replace with your real API token
API_TOKEN=your-api-token-here
DOTENV
fi

echo "==> Creating placeholder favourites.json..."
if [ ! -f "$APP_DIR/favourites.json" ]; then
    echo '[]' > "$APP_DIR/favourites.json"
fi

echo "==> Creating placeholder settings.json..."
if [ ! -f "$APP_DIR/settings.json" ]; then
    echo '{}' > "$APP_DIR/settings.json"
fi

echo "==> Installing systemd service..."
cat > /etc/systemd/system/departure-display.service << 'UNIT'
[Unit]
Description=Departure Display
After=multi-user.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
WorkingDirectory=/home/pi/app
ExecStart=/usr/bin/python3 /home/pi/app/main.py
Restart=always
RestartSec=5
# App pings WATCHDOG=1 every 30s; restart it if it hangs (frozen UI)
WatchdogSec=120
NotifyAccess=main

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable departure-display

echo "==> Creating update script..."
cat > "$APP_DIR/update.sh" << 'UPDATE'
#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the app if it changed.
# Runs at boot and nightly (app-update.timer), and by hand over SSH.
# Every step is time-bounded and failures just mean "try again next
# time": the display keeps running whatever code is already on disk.
set -uo pipefail
cd /home/pi/app

# Never wait for a credentials prompt or a stalled connection.
export GIT_TERMINAL_PROMPT=0
export GIT_HTTP_LOW_SPEED_LIMIT=1000
export GIT_HTTP_LOW_SPEED_TIME=30

# At boot WiFi may still be connecting: give GitHub a few minutes.
for _ in $(seq 1 18); do
    timeout 10 git ls-remote --exit-code origin HEAD >/dev/null 2>&1 && break
    sleep 10
done

before=$(git rev-parse HEAD)
if ! timeout 120 git fetch --quiet origin; then
    echo "Fetch failed (offline?), keeping current version."
    exit 0
fi
# Fast-forward only: a diverged or locally edited checkout is left alone
# rather than half-merged.
if ! git merge --ff-only --quiet '@{u}'; then
    echo "Cannot fast-forward, keeping current version."
    exit 0
fi
after=$(git rev-parse HEAD)
if [ "$before" != "$after" ]; then
    sudo -n systemctl restart departure-display
    echo "Updated ${before:0:8} -> ${after:0:8} and restarted."
else
    echo "Already up to date."
fi
UPDATE
chmod +x "$APP_DIR/update.sh"

echo "==> Scheduling app updates (at boot and nightly)..."
cat > /etc/systemd/system/app-update.service << 'AUUNIT'
[Unit]
Description=Update Prochains Departs from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/app
ExecStart=/home/pi/app/update.sh
# Hard cap so a stuck update can never linger
TimeoutStartSec=10min
Nice=10
AUUNIT

# Started by the timer only (never WantedBy a boot target), so boot and
# the display never wait on it: the app starts with the code on disk and
# is restarted once, a minute later, only if new code was pulled.
cat > /etc/systemd/system/app-update.timer << 'AUTIMER'
[Unit]
Description=App update check at boot and nightly

[Timer]
OnBootSec=1min
OnCalendar=*-*-* 03:30
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
AUTIMER
systemctl daemon-reload
systemctl enable app-update.timer

echo "==> Writing .xinitrc..."
cat > "/home/$PI_USER/.xinitrc" << 'XINITRC'
#!/bin/sh
# Disable screen blanking
xset s off
xset s noblank
xset -dpms

# Hide cursor after 3 seconds of inactivity
unclutter -idle 3 -root &

# Start window manager
openbox &

# Keep X running (systemd service starts the app)
wait
XINITRC
chown "$PI_USER:$PI_USER" "/home/$PI_USER/.xinitrc"
chmod +x "/home/$PI_USER/.xinitrc"

echo "==> Writing .bash_profile (auto-startx on tty1)..."
BASH_PROFILE="/home/$PI_USER/.bash_profile"
if ! grep -q 'startx' "$BASH_PROFILE" 2>/dev/null; then
    cat >> "$BASH_PROFILE" << 'BASHPROFILE'

# Auto-start X on tty1 login
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx
fi
BASHPROFILE
    chown "$PI_USER:$PI_USER" "$BASH_PROFILE"
fi

chown -R "$PI_USER:$PI_USER" "$APP_DIR"

echo ""
echo "==> Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit $APP_DIR/.env with your real API token"
echo "  2. Reboot: sudo reboot"
echo ""
echo "After reboot the Pi is fully autonomous:"
echo "  - App starts automatically on the touchscreen"
echo "  - Update with: ssh pi@<ip> 'cd /home/pi/app && ./update.sh'"
echo "  - App updates from GitHub at boot and nightly (3:30am)"
echo "  - OS security updates install daily, auto-reboot at 4am if needed"
echo "  - Hardware watchdog reboots the Pi if it ever freezes"
echo "  - Network watchdog restarts WiFi (or reboots) if the network drops"
