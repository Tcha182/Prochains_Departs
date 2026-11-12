#!/usr/bin/env bash
# update.sh — pull the latest code from GitHub, apply the system config
# (pi-system.sh), and restart the app if the code changed.
#
# Run by app-update.timer (at boot and nightly), or by hand:
#   /home/pi/app/update.sh
#
# Every step is time-bounded and failures just mean "try again next
# time": the display keeps running whatever code is already on disk.
set -uo pipefail

# Everything lives in main(), which bash parses completely before running
# it — so the `git merge` below can safely replace this very file.
main() {
    local app_dir before after
    app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$app_dir" || exit 1

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
    # Fast-forward only: a diverged or locally edited checkout is left
    # alone rather than half-merged.
    elif ! git merge --ff-only --quiet '@{u}'; then
        echo "Cannot fast-forward, keeping current version." >&2
    fi
    after=$(git rev-parse HEAD)

    # System config from the (possibly new) checkout. Checked for syntax
    # first so a broken push can't half-apply.
    if bash -n "$app_dir/pi-system.sh"; then
        sudo -n bash "$app_dir/pi-system.sh" || echo "pi-system.sh failed" >&2
    else
        echo "pi-system.sh has a syntax error, skipped" >&2
    fi

    if [ "$before" != "$after" ]; then
        sudo -n systemctl restart departure-display
        echo "Updated ${before:0:8} -> ${after:0:8} and restarted."
    else
        echo "Already up to date."
    fi
}

main "$@"
exit
