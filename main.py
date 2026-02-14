"""PyQt5 Touchscreen Departure Display for Ile-de-France public transport.

Entry point: MainWindow with auto-refresh, countdown interpolation, and favourites.
"""

import glob
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
    ResolveAndProbeWorker, WiFiScanWorker, WiFiConnectWorker,
    start_worker,
)
from widgets import HomeScreen, SearchScreen, SettingsScreen, SleepOverlay, VirtualKeyboard
from styles import DARK_THEME, set_theme, get_theme, load_icon_font

WINDOW_WIDTH = 800
WINDOW_HEIGHT = 480
AUTO_REFRESH_MS = 1 * 60 * 1000  # 1 minute
COUNTDOWN_MS = 1000  # 1 second


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
        self._last_interaction_time = time.time()
        self._sleeping = False

        self._setup_ui()
        self._setup_timers()
        self._detect_kiosk()

        # Initial fetch
        if self.favourites:
            QTimer.singleShot(500, self._refresh_departures)
        else:
            self.home.populate(self.favourites, self.departure_map, self._delete_favourite)

    def _setup_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Home screen (index 0)
        self.home = HomeScreen()
        self.home.add_requested.connect(self._show_search)
        self.home.refresh_requested.connect(self._refresh_departures)
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
        self.stack.addWidget(self.settings_screen)

        # Virtual keyboard (child widget, overlays at bottom)
        self.keyboard = VirtualKeyboard(self)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        # Sleep overlay (child widget, overlays everything)
        self.sleep_overlay = SleepOverlay(self)
        self.sleep_overlay.tapped.connect(self._wake_up)

    def _setup_timers(self):
        # Auto-refresh timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(AUTO_REFRESH_MS)
        self.refresh_timer.timeout.connect(self._auto_refresh)
        self.refresh_timer.start()
        self._last_refresh_time = None
        self._next_refresh_epoch = None

        # Countdown timer (1 second)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(COUNTDOWN_MS)
        self.countdown_timer.timeout.connect(self._on_countdown_tick)
        self.countdown_timer.start()

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
        kb_h = 220
        self.keyboard.setGeometry(0, self.height() - kb_h, self.width(), kb_h)

    def _on_focus_changed(self, old, new):
        """Show/hide virtual keyboard when QLineEdit gains/loses focus."""
        if isinstance(new, QLineEdit):
            self.keyboard.set_target(new)
            kb_h = 220
            self.keyboard.setGeometry(0, self.height() - kb_h, self.width(), kb_h)
            self.keyboard.show()
            self.keyboard.raise_()
        else:
            self.keyboard.hide()

    # ── Event tracking for sleep mode ─────────────────────────────────────────

    def event(self, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.TouchBegin):
            self._last_interaction_time = time.time()
        return super().event(event)

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
        self._refresh_departures()

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

    def _launch_worker(self, worker, on_finished):
        """Wire up a worker's signals, move it to a thread, and start it."""
        worker.finished.connect(on_finished)
        if hasattr(worker, "error"):
            worker.error.connect(self._on_departure_error)
        thread, worker = start_worker(worker, self)
        self._active_threads.append(thread)
        self._active_workers.append(worker)
        thread.finished.connect(lambda t=thread, w=worker: self._cleanup_worker(t, w))

    # ── Departure fetching ───────────────────────────────────────────────────

    def _refresh_departures(self):
        if not self.favourites:
            self._rebuild_home()
            return

        self._launch_worker(DepartureWorker(list(self.favourites)), self._on_departures_received)
        self.home.set_updated_time("Mise a jour...")

    def _on_departures_received(self, dep_map: dict):
        self.departure_map.update(dep_map)
        self._last_refresh_time = datetime.now()
        self._next_refresh_epoch = self._last_refresh_time.timestamp() + AUTO_REFRESH_MS / 1000
        self.home.set_updated_time(
            f"Mis a jour a {self._last_refresh_time.strftime('%H:%M')}"
        )
        self._rebuild_home()

    def _on_departure_error(self, msg: str):
        self.home.set_updated_time(msg)

    def _auto_refresh(self):
        """Auto-refresh, but skip between 2am and 5am."""
        hour = datetime.now().hour
        if 2 <= hour < 5:
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

        # Sleep check
        sleep_delay = self._settings.sleep_delay_minutes
        if sleep_delay > 0 and not self._sleeping and self.stack.currentIndex() == 0:
            idle = time.time() - self._last_interaction_time
            if idle > sleep_delay * 60:
                self._enter_sleep()

    # ── Sleep mode ───────────────────────────────────────────────────────────

    def _enter_sleep(self):
        self._sleeping = True
        self.refresh_timer.stop()
        self.countdown_timer.stop()
        self.sleep_overlay.setGeometry(self.rect())
        self.sleep_overlay.show()
        self.sleep_overlay.raise_()
        self._set_backlight(False)

    def _wake_up(self):
        self._sleeping = False
        self._last_interaction_time = time.time()
        self.sleep_overlay.hide()
        self.refresh_timer.start()
        self.countdown_timer.start()
        self._set_backlight(True)
        self._refresh_departures()

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

    # ── Search API calls ─────────────────────────────────────────────────────

    def _on_line_search(self, query: str, mode: str):
        search_id = self.search._search_id
        self._launch_worker(LineSearchWorker(query, mode, search_id), self.search.on_line_results)

    def _on_stops_on_line(self, route_id: str):
        self._launch_worker(StopsOnLineWorker(route_id), self.search.on_stop_results)

    def _on_resolve_and_probe(self, stop_id: str, line_id: str):
        self._launch_worker(ResolveAndProbeWorker(stop_id, line_id), self.search.on_directions_results)

    # ── Thread/worker cleanup ────────────────────────────────────────────────

    def _cleanup_worker(self, thread, worker):
        if thread in self._active_threads:
            self._active_threads.remove(thread)
        if worker in self._active_workers:
            self._active_workers.remove(worker)


def main():
    app = QApplication(sys.argv)

    # Load icon font before creating any widgets
    load_icon_font()

    # Load and apply saved theme
    settings = load_settings()
    set_theme(settings.theme)

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
