#!/usr/bin/env bash
# pi-network-setup.sh — keep the Pi's network alive without manual reboots.
# Idempotent; called by setup-pi.sh. On an already-deployed Pi, run once:
#   sudo /home/pi/app/pi-network-setup.sh
#
# 1. Disables WiFi power saving (the Pi's brcmfmac chip is known to drop
#    off the network after a while with it on, until a reboot).
# 2. Makes NetworkManager retry WiFi forever (by default it gives up after
#    4 failed attempts, e.g. during a router reboot).
# 3. Installs network-watchdog: every 2 min, checks the IDFM APIs are
#    reachable; restarts NetworkManager after 2 failed checks, and reboots
#    if the local network (gateway) itself stays unreachable for ~10 min.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

echo "==> Disabling WiFi power saving..."
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-wifi-powersave-off.conf << 'NMCONF'
[connection]
# 2 = disable
wifi.powersave = 2
NMCONF
if command -v iw >/dev/null 2>&1; then
    iw dev wlan0 set power_save off 2>/dev/null || true
fi

echo "==> Making WiFi reconnect forever..."
if command -v nmcli >/dev/null 2>&1; then
    nmcli -t -f NAME,TYPE connection show 2>/dev/null \
        | awk -F: '$2 == "802-11-wireless" {print $1}' \
        | while IFS= read -r name; do
            nmcli connection modify "$name" connection.autoconnect-retries 0 || true
        done
fi

echo "==> Installing network watchdog..."
cat > /usr/local/bin/network-watchdog.sh << 'WATCHDOG'
#!/usr/bin/env bash
# Run by network-watchdog.timer. Recovers the network when the IDFM APIs
# stay unreachable (see pi-network-setup.sh in the app repo).
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
WATCHDOG
chmod 755 /usr/local/bin/network-watchdog.sh

cat > /etc/systemd/system/network-watchdog.service << 'NWUNIT'
[Unit]
Description=Recover network connectivity for the departure display

[Service]
Type=oneshot
ExecStart=/usr/local/bin/network-watchdog.sh
NWUNIT

cat > /etc/systemd/system/network-watchdog.timer << 'NWTIMER'
[Unit]
Description=Check network connectivity every 2 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
NWTIMER

systemctl daemon-reload
systemctl enable --now network-watchdog.timer

if systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager || true
fi

echo "==> Network hardening done."
