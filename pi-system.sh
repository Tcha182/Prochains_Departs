#!/usr/bin/env bash
# pi-system.sh — the Pi's whole system configuration, kept in the repo.
#
# Idempotent: run by update.sh (as root, via sudo) after every update
# check, and once by setup-pi.sh. A system-level change made here
# reaches the Pi like any code change — no SSH needed. Files are only
# rewritten (and services only reloaded) when their content differs.
#
#   sudo /home/pi/app/pi-system.sh
set -uo pipefail

APP_DIR="/home/pi/app"
PI_USER="pi"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

UNITS_CHANGED=0
DISPLAY_UNIT_CHANGED=0
NM_CHANGED=0

# put <path> <mode> [owner] < content — install only if different.
put() {
    local path=$1 mode=$2 owner=${3:-root:root} tmp
    tmp=$(mktemp)
    cat > "$tmp"
    if [ -f "$path" ] && cmp -s "$tmp" "$path"; then
        rm -f "$tmp"
        return 1
    fi
    mkdir -p "$(dirname "$path")"
    install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$path"
    rm -f "$tmp"
    echo "updated $path"
    return 0
}

# ─── Packages (only installs what is missing) ───────────────────────────────

PACKAGES=(
    xserver-xorg xinit openbox unclutter x11-xserver-utils
    network-manager iw curl git
    python3-pyqt5 python3-requests python3-dotenv
    unattended-upgrades watchdog
)
missing=()
for pkg in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" \
        || missing+=("$pkg")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "installing: ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    timeout 15m apt-get update -qq \
        && timeout 30m apt-get install -y -qq --no-install-recommends "${missing[@]}" \
        || echo "package install failed, will retry next run" >&2
fi

# ─── Time zone ───────────────────────────────────────────────────────────────

if [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "Europe/Paris" ]; then
    timedatectl set-timezone Europe/Paris && echo "timezone set to Europe/Paris"
fi

# ─── Automatic OS updates ────────────────────────────────────────────────────

# Origins must match `apt-cache policy`: security lives in the
# "<codename>-security" suite, and the kernel/firmware come from the
# "Raspberry Pi Foundation" repo (64-bit OS).
put /etc/apt/apt.conf.d/50unattended-upgrades 644 << 'EOF'
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=${distro_codename},label=Debian";
    "origin=Debian,codename=${distro_codename}-updates,label=Debian";
    "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
    "origin=Raspberry Pi Foundation,codename=${distro_codename}";
    "origin=Raspbian,codename=${distro_codename},label=Raspbian";
};
// Never stop on a config-file prompt
Dpkg::Options { "--force-confdef"; "--force-confold"; };
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF

put /etc/apt/apt.conf.d/20auto-upgrades 644 << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# ─── Hardware watchdog (reboots the Pi if the system freezes) ───────────────

BOOT_CONFIG=/boot/firmware/config.txt
if [ -f "$BOOT_CONFIG" ] && ! grep -q '^dtparam=watchdog=on' "$BOOT_CONFIG"; then
    echo 'dtparam=watchdog=on' >> "$BOOT_CONFIG"
    echo "enabled hardware watchdog (active after next reboot)"
fi
put /etc/watchdog.conf 644 << 'EOF'
watchdog-device = /dev/watchdog
watchdog-timeout = 15
max-load-1 = 24
EOF
systemctl enable --quiet watchdog 2>/dev/null || true

# ─── Kiosk session: autologin on tty1 → startx → openbox ────────────────────

put /etc/systemd/system/getty@tty1.service.d/autologin.conf 644 << 'EOF' && UNITS_CHANGED=1
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin pi --noclear %I $TERM
EOF

put "/home/$PI_USER/.xinitrc" 755 "$PI_USER:$PI_USER" << 'EOF'
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
EOF

BASH_PROFILE="/home/$PI_USER/.bash_profile"
if ! grep -q 'startx' "$BASH_PROFILE" 2>/dev/null; then
    cat >> "$BASH_PROFILE" << 'EOF'

# Auto-start X on tty1 login
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx
fi
EOF
    chown "$PI_USER:$PI_USER" "$BASH_PROFILE"
    echo "updated $BASH_PROFILE"
fi

# ─── App service ─────────────────────────────────────────────────────────────

put /etc/systemd/system/departure-display.service 644 << 'EOF' && { UNITS_CHANGED=1; DISPLAY_UNIT_CHANGED=1; }
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
EOF

# ─── App updates: at boot and nightly ────────────────────────────────────────

put /etc/systemd/system/app-update.service 644 << 'EOF' && UNITS_CHANGED=1
[Unit]
Description=Update Prochains Departs from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/app
ExecStart=/bin/bash /home/pi/app/update.sh
# Hard cap so a stuck update can never linger
TimeoutStartSec=30min
Nice=10
EOF

# Started by the timer only (never WantedBy a boot target), so boot and
# the display never wait on it.
put /etc/systemd/system/app-update.timer 644 << 'EOF' && UNITS_CHANGED=1
[Unit]
Description=App update check at boot and nightly

[Timer]
OnBootSec=1min
OnCalendar=*-*-* 03:30
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

# ─── Network hardening ───────────────────────────────────────────────────────

# The Pi's brcmfmac WiFi chip is known to drop off the network with power
# saving on, until a reboot.
put /etc/NetworkManager/conf.d/99-wifi-powersave-off.conf 644 << 'EOF' && NM_CHANGED=1
[connection]
# 2 = disable
wifi.powersave = 2
EOF
iw dev wlan0 set power_save off 2>/dev/null || true

# Retry WiFi forever (NetworkManager gives up after 4 attempts by default,
# e.g. during a router reboot).
if command -v nmcli >/dev/null 2>&1; then
    nmcli -t -f NAME,TYPE connection show 2>/dev/null \
        | awk -F: '$2 == "802-11-wireless" {print $1}' \
        | while IFS= read -r name; do
            if [ "$(nmcli -g connection.autoconnect-retries connection show "$name" 2>/dev/null)" != "0" ]; then
                nmcli connection modify "$name" connection.autoconnect-retries 0 \
                    && echo "WiFi '$name' now retries forever"
            fi
        done
fi

# Every 2 min: restart NetworkManager when the IDFM APIs stay unreachable,
# reboot if the local network itself stays down ~10 min.
put /usr/local/bin/network-watchdog.sh 755 << 'EOF'
#!/usr/bin/env bash
# Installed by pi-system.sh (app repo). Run by network-watchdog.timer.
STATE=/run/network-watchdog.failures
URLS=(
    "https://prim.iledefrance-mobilites.fr/"
    "https://data.iledefrance-mobilites.fr/"
)
RESTART_NM_AT=2   # consecutive failed checks (~4 min)
REBOOT_AT=5       # ~10 min
MIN_UPTIME_FOR_REBOOT=1800

log() { logger -t network-watchdog "$*"; }

online() {
    local url
    for url in "${URLS[@]}"; do
        # Any HTTP answer (even 4xx) means the network path works.
        if curl -sS -o /dev/null -m 15 "$url" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

gateway_reachable() {
    local gw
    gw=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
    [ -n "$gw" ] && ping -c 2 -W 3 "$gw" >/dev/null 2>&1
}

failures=$(cat "$STATE" 2>/dev/null || echo 0)

if online; then
    if [ "$failures" -gt 0 ]; then
        log "network back after $failures failed check(s)"
    fi
    echo 0 > "$STATE"
    exit 0
fi

failures=$((failures + 1))
echo "$failures" > "$STATE"
log "IDFM APIs unreachable (check $failures)"

if [ "$failures" -eq "$RESTART_NM_AT" ]; then
    log "restarting NetworkManager"
    iw dev wlan0 set power_save off 2>/dev/null || true
    systemctl restart NetworkManager
elif [ "$failures" -ge "$REBOOT_AT" ]; then
    uptime_s=$(cut -d. -f1 /proc/uptime)
    if gateway_reachable; then
        # Local network is fine: the internet or IDFM is down, and a
        # reboot won't help. Nudge NetworkManager now and then.
        if [ $((failures % 5)) -eq 0 ]; then
            log "gateway reachable, internet/IDFM down: restarting NetworkManager"
            systemctl restart NetworkManager
        fi
    elif [ "$uptime_s" -ge "$MIN_UPTIME_FOR_REBOOT" ]; then
        log "gateway unreachable for $failures checks, rebooting"
        systemctl reboot
    fi
fi
EOF

put /etc/systemd/system/network-watchdog.service 644 << 'EOF' && UNITS_CHANGED=1
[Unit]
Description=Recover network connectivity for the departure display

[Service]
Type=oneshot
ExecStart=/usr/local/bin/network-watchdog.sh
EOF

put /etc/systemd/system/network-watchdog.timer 644 << 'EOF' && UNITS_CHANGED=1
[Unit]
Description=Check network connectivity every 2 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

# ─── Apply ───────────────────────────────────────────────────────────────────

if [ "$UNITS_CHANGED" -eq 1 ]; then
    systemctl daemon-reload
fi
systemctl enable --quiet departure-display.service
systemctl enable --quiet --now app-update.timer network-watchdog.timer

if [ "$NM_CHANGED" -eq 1 ] && systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager || true
fi
if [ "$DISPLAY_UNIT_CHANGED" -eq 1 ] && systemctl is-active --quiet departure-display; then
    systemctl restart departure-display && echo "restarted departure-display (unit changed)"
fi

echo "system configuration up to date"
