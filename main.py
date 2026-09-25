"""PyQt5 Touchscreen Departure Display for Ile-de-France public transport.

Entry point: MainWindow with auto-refresh, countdown interpolation, and favourites.
"""

import glob
import logging
import logging.handlers
import os
import socket
import sys
import time
import platform
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QEvent
from PyQt5.QtWidgets import QApplication, QMainWindow, QStackedWidget, QLineEdit

from models import (
    Favourite, load_favourites, save_favourites,
    AppSettings, load_settings, save_settings, save_api_token,
)
from api import (
    DepartureWorker, LineSearchWorker, StopsOnLineWorker,
    ResolveAndProbeWorker, StopAreaSearchWorker, LineDetailsWorker,
    WiFiScanWorker, WiFiConnectWorker, UpdateWorker,
    start_worker,
)
from widgets import HomeScreen, SearchScreen, SettingsScreen, SleepOverlay, VirtualKeyboard
from styles import DARK_THEME, set_theme, get_theme, load_icon_font

WINDOW_WIDTH = 800
WINDOW_HEIGHT = 480
AUTO_REFRESH_MS = 1 * 60 * 1000  # 1 minute
COUNTDOWN_MS = 1000  # 1 second
KEYBOARD_HEIGHT = 220
NOCTURNAL_START_HOUR = 2
NOCTURNAL_END_HOUR = 5
NOCTURNAL_SLEEP_MINUTES = 2  # idle time before the night screen kicks in
HEARTBEAT_MS = 30 * 1000  # watchdog ping + nocturnal auto-wake check
# A departure fetch still running after this long is considered stuck: a new
# refresh may start and the stuck one's late results are ignored.
REFRESH_STALL_SECONDS = 90
# No successful departure fetch for this long while awake: restart the app
# (systemd's Restart=always relaunches it) to shed any bad in-process state.
SELF_RESTART_AFTER_SECONDS = 15 * 60

log = logging.getLogger("departs.main")


def sd_notify(message: str) -> None:
    """Send a message to the systemd notify socket, if one exists."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        if addr.startswith("@"):  # abstract namespace socket
            addr = "\0" + addr[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), addr)
    except OSError:
        pass


def setup_logging() -> None:
    """Log to a small rotating file next to the app, plus stderr."""
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
    root = logging.getLogger("departs")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass  # read-only filesystem etc. — stderr still works
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Prochains Departs")
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)

        self.favourites = load_favourites()
        self.departure_map = {}  # {fav_key: [Departure, ...]}
        self._active_threads = []  # prevent GC of running threads
        self._active_workers = []  # prevent GC of running workers
        self._settings = load_settings()
        # Monotonic, not wall-clock: the Pi has no RTC, so NTP can jump the
        # clock by hours/days after boot, which made idle time look huge
        # (instant sleep) or negative (never sleeping).
        self._last_interaction_time = time.monotonic()
        self._sleeping = False
        self._nocturnal_sleep = False

        self._setup_ui()
        self._setup_timers()
        self._detect_kiosk()

        # Show current state immediately, then fetch data
        self.home.populate(self.favourites, self.departure_map, self._delete_favourite)
        if self.favourites:
            self.home.set_updated_time("Chargement...")
            QTimer.singleShot(500, self._refresh_departures)

    def _setup_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Home screen (index 0)
        self.home = HomeScreen()
        self.home.add_requested.connect(self._show_search)
        self.home.refresh_requested.connect(lambda: self._refresh_departures(force=True))
        self.home.edit_toggled.connect(self._rebuild_home)
        self.home.settings_requested.connect(self._show_settings)
        self.stack.addWidget(self.home)

        # Search screen (index 1)
        self.search = SearchScreen()
        self.search.back_to_home.connect(self._show_home)
        self.search.favourite_added.connect(self._on_favourite_added)
        self.search.line_search_requested.connect(self._on_line_search)
        self.search.stops_on_line_requested.connect(self._on_stops_on_line)
        self.search.resolve_and_probe_requested.connect(self._on_resolve_and_probe)
        self.search.stop_area_search_requested.connect(self._on_stop_area_search)
        self.search.line_details_requested.connect(self._on_line_details)
        self.stack.addWidget(self.search)

        # Settings screen (index 2)
        self.settings_screen = SettingsScreen(
            current_theme=self._settings.theme,
            current_sleep=self._settings.sleep_delay_minutes,
        )
        self.settings_screen.back_to_home.connect(self._show_home)
        self.settings_screen.theme_changed.connect(self._on_theme_changed)
        self.settings_screen.sleep_delay_changed.connect(self._on_sleep_delay_changed)
        self.settings_screen.wifi_scan_requested.connect(self._on_wifi_scan)
        self.settings_screen.wifi_connect_requested.connect(self._on_wifi_connect)
        self.settings_screen.api_token_saved.connect(self._on_api_token_saved)
        self.settings_screen.update_check_requested.connect(self._on_check_update)
        self.stack.addWidget(self.settings_screen)

        # Virtual keyboard (child widget, overlays at bottom)
        self.keyboard = VirtualKeyboard(self)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        # Sleep overlay (child widget, overlays everything)
        self.sleep_overlay = SleepOverlay(self)
        self.sleep_overlay.tapped.connect(self._wake_up)

        # Track touch activity app-wide for the sleep timer. Events accepted
        # by child widgets (buttons, list rows) never propagate up to this
        # window, so a plain event() override misses most interactions.
        QApplication.instance().installEventFilter(self)

    def _setup_timers(self):
        # Auto-refresh timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(AUTO_REFRESH_MS)
        self.refresh_timer.timeout.connect(self._auto_refresh)
        self.refresh_timer.start()
        self._last_refresh_time = None
        self._next_refresh_epoch = None
        self._departure_error_msg = None
        # Only the latest departure fetch may update the display; an older
        # one finishing late (slow network) must not overwrite it.
        self._departure_generation = 0
        self._departure_started_at = None  # monotonic; None when idle
        self._last_departure_success = time.monotonic()
        self._departure_failures = 0

        # Countdown timer (1 second)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(COUNTDOWN_MS)
        self.countdown_timer.timeout.connect(self._on_countdown_tick)
        self.countdown_timer.start()

        # Heartbeat: never stopped (unlike the timers above, which pause
        # during sleep). Pings the systemd watchdog and ends a forced
        # nocturnal sleep once the pause window is over.
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(HEARTBEAT_MS)
        self.heartbeat_timer.timeout.connect(self._on_heartbeat)
        self.heartbeat_timer.start()

    def _detect_kiosk(self):
        """Go fullscreen and hide cursor on Raspberry Pi."""
        arch = platform.machine().lower()
        if "arm" in arch or "aarch" in arch:
            self.showFullScreen()
            self.setCursor(Qt.BlankCursor)

    def resizeEvent(self, event):
        """Keep overlays sized to the full window."""
        super().resizeEvent(event)
        self.sleep_overlay.setGeometry(self.rect())
        # Position keyboard at the bottom
        self.keyboard.setGeometry(0, self.height() - KEYBOARD_HEIGHT,
                                  self.width(), KEYBOARD_HEIGHT)

    def _on_focus_changed(self, old, new):
        """Show/hide virtual keyboard when QLineEdit gains/loses focus."""
        if isinstance(new, QLineEdit):
            self.keyboard.set_target(new)
            self.keyboard.setGeometry(0, self.height() - KEYBOARD_HEIGHT,
                                      self.width(), KEYBOARD_HEIGHT)
            self.keyboard.show()
            self.keyboard.raise_()
        else:
            self.keyboard.hide()

    # ── Event tracking for sleep mode ─────────────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseMove,
                            QEvent.TouchBegin, QEvent.TouchUpdate):
            self._last_interaction_time = time.monotonic()
        return super().eventFilter(obj, event)

    # ── Navigation ───────────────────────────────────────────────────────────

    def _show_search(self):
        self.search.reset()
        self.stack.setCurrentIndex(1)

    def _show_home(self):
        self.stack.setCurrentIndex(0)

    def _show_settings(self):
        self.settings_screen.stack.setCurrentIndex(0)
        self.stack.setCurrentIndex(2)

    # ── Favourites management ────────────────────────────────────────────────

    def _on_favourite_added(self, fav: Favourite):
        # Avoid duplicates
        for existing in self.favourites:
            if (existing.stop_area_id == fav.stop_area_id
                    and existing.line_id == fav.line_id
                    and existing.destination_name == fav.destination_name):
                self._show_home()
                return

        self.favourites.append(fav)
        save_favourites(self.favourites)
        self._show_home()
        self._refresh_departures(force=True)

    def _delete_favourite(self, fav: Favourite):
        self.favourites = [
            f for f in self.favourites
            if not (f.stop_area_id == fav.stop_area_id
                    and f.line_id == fav.line_id
                    and f.destination_name == fav.destination_name)
        ]
        save_favourites(self.favourites)
        # Remove from departure map
        fav_key = f"{fav.stop_area_id}_{fav.line_id}_{fav.direction}"
        self.departure_map.pop(fav_key, None)
        self._rebuild_home()

    def _rebuild_home(self):
        self.home.populate(self.favourites, self.departure_map, self._delete_favourite)

    # ── Worker lifecycle ─────────────────────────────────────────────────────

    def _launch_worker(self, worker, on_finished, on_error=None):
        """Wire up a worker's signals, move it to a thread, and start it."""
        worker.finished.connect(on_finished)
        if on_error is not None and hasattr(worker, "error"):
            worker.error.connect(on_error)
        thread, worker = start_worker(worker, self)
        self._active_threads.append(thread)
        self._active_workers.append(worker)
        thread.finished.connect(lambda t=thread, w=worker: self._cleanup_worker(t, w))

    # ── Departure fetching ───────────────────────────────────────────────────

    def _refresh_departures(self, force: bool = False):
        """Start a departure fetch. `force` supersedes one already running
        (user-initiated refreshes, new favourite, wake-up)."""
        if not self.favourites:
            self._rebuild_home()
            return

        # One fetch at a time: on a slow network, stacking a new fetch every
        # minute only piles up threads. A fetch stuck past the stall limit
        # is abandoned (its late results are dropped by the generation check).
        if self._departure_started_at is not None and not force:
            running = time.monotonic() - self._departure_started_at
            if running < REFRESH_STALL_SECONDS:
                return
            log.warning("departure fetch stuck for %.0fs, starting a new one", running)

        self._departure_generation += 1
        generation = self._departure_generation
        self._departure_started_at = time.monotonic()
        self._departure_error_msg = None
        self._launch_worker(
            DepartureWorker(list(self.favourites)),
            lambda dep_map, g=generation: self._on_departures_received(dep_map, g),
            on_error=lambda msg, g=generation: self._on_departure_error(msg, g))
        self.home.set_updated_time("Mise a jour...")

    def _on_departures_received(self, dep_map: dict, generation: int = None):
        if generation is not None and generation != self._departure_generation:
            return  # superseded by a newer fetch
        self._departure_started_at = None
        if dep_map or not self._departure_error_msg:
            if self._departure_failures:
                log.info("departures back after %d failed refresh(es)",
                         self._departure_failures)
            self._departure_failures = 0
            self._last_departure_success = time.monotonic()
        else:
            self._departure_failures += 1
        if self._sleeping:
            return  # fetch finished after we went to sleep: keep the screen clear
        self.departure_map.update(dep_map)
        self._last_refresh_time = datetime.now()
        self._next_refresh_epoch = self._last_refresh_time.timestamp() + AUTO_REFRESH_MS / 1000
        if not dep_map and self._departure_error_msg:
            # Every group failed: keep the real error visible instead of
            # claiming a successful update that fetched nothing.
            self.home.set_updated_time(self._departure_error_msg)
        else:
            self.home.set_updated_time(
                f"Mis a jour a {self._last_refresh_time.strftime('%H:%M')}"
            )
        self._rebuild_home()

    def _on_departure_error(self, msg: str, generation: int = None):
        if generation is not None and generation != self._departure_generation:
            return
        self._departure_error_msg = msg
        if not self._sleeping:
            self.home.set_updated_time(msg)

    @staticmethod
    def _is_nocturnal() -> bool:
        return NOCTURNAL_START_HOUR <= datetime.now().hour < NOCTURNAL_END_HOUR

    def _auto_refresh(self):
        """Auto-refresh, but skip between 2am and 5am."""
        if self._is_nocturnal():
            self.home.set_next_refresh("Pause nocturne")
            return
        self._refresh_departures()

    # ── Countdown tick + sleep check ─────────────────────────────────────────

    def _on_countdown_tick(self):
        self.home.update_countdowns()

        # Update "next refresh" display
        if self._next_refresh_epoch:
            remaining = self._next_refresh_epoch - time.time()
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                self.home.set_next_refresh(f"MaJ dans {mins}:{secs:02d}")
            else:
                self.home.set_next_refresh("")

        # Sleep check. During the nocturnal pause (no refreshes, stale data)
        # the sleep screen always kicks in — even if sleep is disabled, and
        # regardless of which screen is showing.
        sleep_delay = self._settings.sleep_delay_minutes
        if self._is_nocturnal():
            effective_delay = (min(sleep_delay, NOCTURNAL_SLEEP_MINUTES)
                               if sleep_delay > 0 else NOCTURNAL_SLEEP_MINUTES)
            home_only = False
        else:
            effective_delay = sleep_delay
            home_only = True
        if (effective_delay > 0 and not self._sleeping
                and (not home_only or self.stack.currentIndex() == 0)):
            idle = time.monotonic() - self._last_interaction_time
            if idle > effective_delay * 60:
                self._enter_sleep()

    def _on_heartbeat(self):
        """Runs every 30s, including during sleep."""
        sd_notify("WATCHDOG=1")
        self._check_refresh_health()
        # Auto-wake from a forced nocturnal sleep: users who disabled sleep
        # expect an always-on display, so don't require a tap at 5am.
        if (self._sleeping and self._nocturnal_sleep
                and not self._is_nocturnal()):
            log.info("nocturnal pause over, waking display")
            self._wake_up()

    def _check_refresh_health(self):
        """Self-heal when departures haven't refreshed for a long time.

        Rebooting the Pi used to be the only way out of a state where
        refreshes silently stopped. Exiting lets systemd (Restart=always)
        relaunch a fresh process; the network itself is looked after by
        network-watchdog.sh (see pi-network-setup.sh).
        """
        if (not self.favourites or self._sleeping or self._is_nocturnal()):
            # No refresh is expected: don't count this time as a failure.
            self._last_departure_success = time.monotonic()
            return
        stale = time.monotonic() - self._last_departure_success
        if stale > SELF_RESTART_AFTER_SECONDS:
            log.error("no successful departure refresh for %.0f min "
                      "(%d failed attempts), restarting app",
                      stale / 60, self._departure_failures)
            QApplication.instance().exit(1)

    # ── Sleep mode ───────────────────────────────────────────────────────────

    def _enter_sleep(self):
        log.info("entering sleep (nocturnal=%s)", self._is_nocturnal())
        self._sleeping = True
        self._nocturnal_sleep = (self._is_nocturnal()
                                 and self._settings.sleep_delay_minutes == 0)
        self.refresh_timer.stop()
        self.countdown_timer.stop()
        # Clear stale departures so they aren't visible under the overlay
        self.departure_map.clear()
        self._rebuild_home()
        self.home.set_updated_time("")
        self.sleep_overlay.setGeometry(self.rect())
        self.sleep_overlay.show()
        self.sleep_overlay.raise_()
        self._set_backlight(False)

    def _wake_up(self):
        log.info("waking up")
        self._sleeping = False
        self._nocturnal_sleep = False
        self._last_interaction_time = time.monotonic()
        self.home.set_updated_time("Chargement...")
        self.sleep_overlay.hide()
        self.refresh_timer.start()
        self.countdown_timer.start()
        self._set_backlight(True)
        self._refresh_departures(force=True)

    def _set_backlight(self, on: bool):
        """Control Raspberry Pi backlight via sysfs. Silently fails on non-Pi."""
        try:
            paths = glob.glob("/sys/class/backlight/*/bl_power")
            for path in paths:
                with open(path, "w") as f:
                    f.write("0" if on else "1")  # 0=on, 1=off in Linux sysfs
        except (OSError, PermissionError):
            pass

    # ── Settings handlers ────────────────────────────────────────────────────

    def _on_theme_changed(self, name: str):
        set_theme(name)
        self._settings.theme = name
        save_settings(self._settings)

    def _on_sleep_delay_changed(self, minutes: int):
        self._settings.sleep_delay_minutes = minutes
        save_settings(self._settings)

    def _on_api_token_saved(self, token: str):
        save_api_token(token)

    # ── WiFi workers ─────────────────────────────────────────────────────────

    def _on_wifi_scan(self):
        self._launch_worker(WiFiScanWorker(), self.settings_screen.on_wifi_scan_results)

    def _on_wifi_connect(self, ssid: str, password: str):
        self._launch_worker(WiFiConnectWorker(ssid, password), self.settings_screen.on_wifi_connect_result)

    # ── Update worker ────────────────────────────────────────────────────────

    def _on_check_update(self):
        self._launch_worker(UpdateWorker(), self._on_update_result)

    def _on_update_result(self, updated: bool, message: str):
        self.settings_screen.on_update_result(updated, message)
        if updated:
            # New code is already on disk; exit and let systemd's
            # Restart=always (departure-display.service) relaunch us with it.
            QTimer.singleShot(1500, QApplication.instance().quit)

    # ── Search API calls ─────────────────────────────────────────────────────

    def _on_line_search(self, query: str, mode: str):
        search_id = self.search._search_id
        self._launch_worker(LineSearchWorker(query, mode, search_id),
                            self.search.on_line_results,
                            on_error=lambda msg, sid=search_id: self._on_search_error(
                                msg, sid, self.search._search_id))

    def _on_stops_on_line(self, route_id: str):
        self._launch_worker(StopsOnLineWorker(route_id),
                            self.search.on_stop_results,
                            on_error=self.search.show_error)

    def _on_resolve_and_probe(self, stop_id: str, line_id: str):
        self._launch_worker(ResolveAndProbeWorker(stop_id, line_id),
                            self.search.on_directions_results,
                            on_error=self.search.show_error)

    def _on_stop_area_search(self, query: str, search_id: int):
        self._launch_worker(StopAreaSearchWorker(query, search_id),
                            self.search.on_stop_area_results,
                            on_error=lambda msg, sid=search_id: self._on_search_error(
                                msg, sid, self.search._stop_search_id))

    def _on_search_error(self, msg: str, search_id: int, current_id: int):
        """Drop errors from superseded searches: their stale `finished` is
        ignored too, so the error flag would otherwise swallow the results
        of the current search (typing fast on a flaky network)."""
        if search_id == current_id:
            self.search.show_error(msg)

    def _on_line_details(self, line_ids: list):
        self._launch_worker(LineDetailsWorker(line_ids),
                            self.search.on_lines_at_stop_results,
                            on_error=self.search.show_error)

    # ── Thread/worker cleanup ────────────────────────────────────────────────

    def _cleanup_worker(self, thread, worker):
        if thread in self._active_threads:
            self._active_threads.remove(thread)
        if worker in self._active_workers:
            self._active_workers.remove(worker)


def main():
    setup_logging()
    log.info("app starting")
    app = QApplication(sys.argv)

    # Load icon font before creating any widgets
    load_icon_font()

    # Load and apply saved theme
    settings = load_settings()
    set_theme(settings.theme)

    window = MainWindow()
    window.show()

    sd_notify("READY=1")
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
