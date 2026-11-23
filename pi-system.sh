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
# ─── Boot command line ───────────────────────────────────────────────────────

# A first-boot hook (`init=/bin/sh -c "...; exec /sbin/init"`, left by the
# SD card imager) must not stay in cmdline.txt: it ran on every boot,
# rewrote the WiFi connection (undoing the settings below), kept the WiFi
# password on the FAT partition, and is the prime suspect for the kernel
# panic after the 6.18 kernel update (2026-11). The connection it wrote
# already lives in /etc/NetworkManager, so WiFi doesn't need it.
CMDLINE=/boot/firmware/cmdline.txt
if [ -f "$CMDLINE" ] && grep -q ' init=' "$CMDLINE"; then
    sed -i -E 's/ init=.*$//' "$CMDLINE"
    echo "removed init= hook from $CMDLINE"
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

# ─── Home Assistant (radiators, driven from the touchscreen) ────────────────

# Runs as a container next to the display: the one supported install that
# leaves the OS, and so the kiosk, alone. The app talks to it on localhost
# (ha.py); pairing the Zigbee valves and their schedules are done in its
# own web UI, http://<pi>:8123.

HA_CONFIG_DIR="/home/$PI_USER/homeassistant"
HA_UNIT=/etc/systemd/system/home-assistant.service
HA_UNIT_CHANGED=0

# Once installed it stays managed. A first install needs a Pi that can
# carry it without starving the display.
ha_wanted() {
    [ -f "$HA_UNIT" ] && return 0
    local mem_kb free_kb
    if [ "$(dpkg --print-architecture)" != "arm64" ]; then
        echo "Home Assistant not installed: needs the 64-bit OS"
        return 1
    fi
    mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
    if [ "${mem_kb:-0}" -lt 1500000 ]; then
        echo "Home Assistant not installed: needs 2 GB of RAM"
        return 1
    fi
    # The image is 2-4 GB unpacked, and an update holds the old and the
    # new one side by side until the old one is pruned.
    free_kb=$(df -k --output=avail / | tail -1 | tr -d ' ')
    if [ "${free_kb:-0}" -lt 9000000 ]; then
        echo "Home Assistant not installed: needs 9 GB free on the SD card"
        return 1
    fi
}

HA_WANTED=0
ha_wanted && HA_WANTED=1

if [ "$HA_WANTED" -eq 1 ]; then
    if ! command -v docker >/dev/null 2>&1; then
        # docker-cli apart: on Debian 13 the `docker` command is no longer
        # part of docker.io, and --no-install-recommends skips it.
        echo "installing: docker.io docker-cli"
        export DEBIAN_FRONTEND=noninteractive
        timeout 15m apt-get update -qq \
            && timeout 30m apt-get install -y -qq --no-install-recommends docker.io docker-cli \
            || echo "docker install failed, will retry next run" >&2
    fi
fi

if [ "$HA_WANTED" -eq 1 ] && command -v docker >/dev/null 2>&1; then
    # Starting configuration, written once: from then on the file belongs
    # to Home Assistant and its owner.
    if [ ! -f "$HA_CONFIG_DIR/configuration.yaml" ]; then
        mkdir -p "$HA_CONFIG_DIR/packages"
        printf '[]\n' > "$HA_CONFIG_DIR/automations.yaml"
        : > "$HA_CONFIG_DIR/scripts.yaml"
        : > "$HA_CONFIG_DIR/scenes.yaml"
        cat > "$HA_CONFIG_DIR/configuration.yaml" << 'EOF'
# Written once by the departure display's pi-system.sh, never overwritten.

default_config:

homeassistant:
  # Generated by ha_sync.py: the history sensors of each radiator.
  packages: !include_dir_named packages
  auth_providers:
    - type: homeassistant
    # Lets the touchscreen app, running on this same Pi, sign in without a
    # password. Only connections from the Pi itself are trusted.
    - type: trusted_networks
      trusted_networks:
        - 127.0.0.1
        - ::1
      allow_bypass_login: true

# Keep a week of history and write it in batches: easier on the SD card.
recorder:
  purge_keep_days: 7
  commit_interval: 30

automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
EOF
        echo "created $HA_CONFIG_DIR/configuration.yaml"
    fi

    # --privileged and /run/dbus are the documented way to give it the
    # Zigbee stick and Bluetooth. Half the CPU weight of everything else,
    # so the display stays smooth while it starts. Its log goes to the
    # journal through this unit, so Docker keeps no copy (which is never
    # rotated).
    put "$HA_UNIT" 644 << 'EOF' && { UNITS_CHANGED=1; HA_UNIT_CHANGED=1; }
[Unit]
Description=Home Assistant (container)
After=docker.service network-online.target
Wants=docker.service network-online.target

[Service]
Type=simple
# A container left behind by a crash would block the name
ExecStartPre=-/usr/bin/docker rm -f homeassistant
ExecStart=/usr/bin/docker run --rm --name homeassistant \
    --privileged --network=host --cpu-shares=512 --log-driver=none \
    -e TZ=Europe/Paris \
    -v /home/pi/homeassistant:/config \
    -v /run/dbus:/run/dbus:ro \
    ghcr.io/home-assistant/home-assistant:stable
ExecStop=/usr/bin/docker stop -t 120 homeassistant
Restart=always
RestartSec=30
# The very first start downloads the image (700 MB, several minutes)
TimeoutStartSec=0
TimeoutStopSec=150

[Install]
WantedBy=multi-user.target
EOF

    # A USB stick plugged in after the container started doesn't exist
    # inside it. try-restart: does nothing while Home Assistant is stopped
    # (so nothing at boot, when every device is "added").
    put /etc/udev/rules.d/99-home-assistant-usb.rules 644 << 'EOF' && udevadm control --reload
ACTION=="add", SUBSYSTEM=="tty", ENV{ID_BUS}=="usb", RUN+="/usr/bin/systemctl --no-block try-restart home-assistant.service"
EOF

    put /usr/local/bin/home-assistant-update.sh 755 << 'EOF'
#!/usr/bin/env bash
# Installed by pi-system.sh (app repo). Run by home-assistant-update.timer.
IMAGE=ghcr.io/home-assistant/home-assistant:stable

log() { logger -t home-assistant-update "$*"; }

if ! timeout 30m docker pull -q "$IMAGE" >/dev/null; then
    log "pull failed, keeping the current version"
    exit 0
fi
latest=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null)
running=$(docker inspect --format '{{.Image}}' homeassistant 2>/dev/null)
if [ -n "$latest" ] && [ -n "$running" ] && [ "$latest" != "$running" ]; then
    log "new Home Assistant version, restarting it"
    systemctl restart home-assistant.service
    docker image prune -f >/dev/null
fi
EOF

    put /etc/systemd/system/home-assistant-update.service 644 << 'EOF' && UNITS_CHANGED=1
[Unit]
Description=Update the Home Assistant container
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/home-assistant-update.sh
TimeoutStartSec=45min
Nice=10
EOF

    # Monday night, after the app update (3:30) and the OS reboot slot (4:00)
    put /etc/systemd/system/home-assistant-update.timer 644 << 'EOF' && UNITS_CHANGED=1
[Unit]
Description=Weekly Home Assistant update

[Timer]
OnCalendar=Mon *-*-* 04:30
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
EOF
fi

# ─── Apply ───────────────────────────────────────────────────────────────────

if [ "$UNITS_CHANGED" -eq 1 ]; then
    systemctl daemon-reload
fi
systemctl enable --quiet departure-display.service
systemctl enable --quiet --now app-update.timer network-watchdog.timer

if [ -f "$HA_UNIT" ]; then
    systemctl enable --quiet home-assistant.service
    systemctl enable --quiet --now home-assistant-update.timer
    # --no-block: never make an app update wait for the image download.
    if [ "$HA_UNIT_CHANGED" -eq 1 ] && systemctl is-active --quiet home-assistant; then
        systemctl restart --no-block home-assistant && echo "restarting home-assistant (unit changed)"
    else
        systemctl start --no-block home-assistant
    fi

    # History sensors and dashboard for any radiator added since the last
    # run. Home Assistant may still be starting: then the next run does it.
    if [ -f /home/pi/app/ha_sync.py ] \
        && [ "$(docker inspect -f '{{.State.Running}}' homeassistant 2>/dev/null)" = "true" ]; then
        ( cd /home/pi/app && sudo -u pi env QT_QPA_PLATFORM=offscreen timeout 30 \
            python3 -c 'import ha; print(ha._get_token())' 2>/dev/null ) \
            | timeout 90 docker exec -i homeassistant python3 -c "$(cat /home/pi/app/ha_sync.py)" \
            || echo "heating sync skipped, will retry next run" >&2
    fi
fi

if [ "$NM_CHANGED" -eq 1 ] && systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager || true
fi
if [ "$DISPLAY_UNIT_CHANGED" -eq 1 ] && systemctl is-active --quiet departure-display; then
    systemctl restart departure-display && echo "restarted departure-display (unit changed)"
fi

echo "system configuration up to date"
