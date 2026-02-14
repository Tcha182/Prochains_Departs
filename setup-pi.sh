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

echo "==> Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

echo "==> Installing Python dependencies..."
uv pip install --system requests python-dotenv

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
After=graphical.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
WorkingDirectory=/home/pi/app
ExecStart=/usr/bin/python3 /home/pi/app/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
UNIT
systemctl daemon-reload
systemctl enable departure-display

echo "==> Creating update script..."
cat > "$APP_DIR/update.sh" << 'UPDATE'
#!/usr/bin/env bash
set -euo pipefail
cd /home/pi/app
git pull
sudo systemctl restart departure-display
echo "Update complete."
UPDATE
chmod +x "$APP_DIR/update.sh"

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
echo "  - OS security updates install daily, auto-reboot at 4am if needed"
echo "  - Hardware watchdog reboots the Pi if it ever freezes"
