"""Home screen: departure rows, favourite groups, sleep overlay."""

import time
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QScroller,
)

from models import Favourite, Departure
from styles import THEME_COLORS, get_theme, Icons, icon_font
from widgets.common import make_touch_scroll_area


FRENCH_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi",
               "samedi", "dimanche"]
FRENCH_MONTHS = ["janvier", "fevrier", "mars", "avril", "mai", "juin",
                 "juillet", "aout", "septembre", "octobre", "novembre",
                 "decembre"]


class DepartureCard(QFrame):
    """Single departure row with line badge, destination, countdown."""

    def __init__(self, departure: Departure, line_color: str = "FFFFFF",
                 line_text_color: str = "000000", badge_name: str = "",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("departureCard")
        self.departure = departure
        self.fetch_timestamp = departure.fetch_timestamp
        self.eta_seconds = departure.eta_seconds
        self._countdown_shown = None  # (text, state, theme) of last render

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        # Line badge — prefer badge_name (from favourite) over SIRI line_name
        display_name = badge_name or departure.line_name or ""
        self.badge = QLabel(display_name)
        self.badge.setObjectName("lineBadge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedWidth(54)
        bg = line_color if line_color else "FFFFFF"
        fg = line_text_color if line_text_color else "000000"
        self.badge.setStyleSheet(
            f"background-color: #{bg}; color: #{fg}; "
            f"border-radius: 6px; padding: 2px 8px; "
            f"font-size: 15px; font-weight: bold;"
        )

        # Middle: destination + status
        mid = QVBoxLayout()
        mid.setSpacing(1)
        self.dest_label = QLabel(departure.destination)
        self.dest_label.setObjectName("destinationLabel")
        self.dest_label.setWordWrap(True)
        mid.addWidget(self.dest_label)
        if departure.departure_status == "cancelled":
            font = self.dest_label.font()
            font.setStrikeOut(True)
            self.dest_label.setFont(font)

        status_text = self._format_status(departure)
        self.status_label = QLabel(status_text)
        self.status_label.setObjectName("statusLabel2")
        mid.addWidget(self.status_label)

        # Right: countdown + clock
        right = QVBoxLayout()
        right.setSpacing(1)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.countdown_label = QLabel("")
        self.countdown_label.setObjectName("countdownLabel")
        self.countdown_label.setMinimumWidth(80)
        right.addWidget(self.countdown_label)

        self.clock_label = QLabel("")
        self.clock_label.setObjectName("clockLabel")
        right.addWidget(self.clock_label)

        # Format clock time — convert to local timezone
        if departure.expected_iso:
            try:
                dt = datetime.fromisoformat(departure.expected_iso.replace("Z", "+00:00"))
                local_dt = dt.astimezone()
                self.clock_label.setText(local_dt.strftime("%H:%M"))
            except (ValueError, TypeError):
                self.clock_label.setText("")

        layout.addWidget(self.badge)
        layout.addLayout(mid, stretch=1)
        layout.addLayout(right)

        self.update_countdown()

    def _format_status(self, dep: Departure) -> str:
        if dep.vehicle_at_stop:
            return "A l'arret"
        status_map = {
            "onTime": "A l'heure",
            "delayed": "En retard",
            "early": "En avance",
            "cancelled": "",  # shown in place of the countdown instead
            "noReport": "",
            "arrived": "Arrive",
        }
        return status_map.get(dep.departure_status, dep.departure_status)

    @property
    def cancelled(self) -> bool:
        return self.departure.departure_status == "cancelled"

    def update_countdown(self):
        """Recompute countdown from fetch_timestamp + eta_seconds."""
        if self.eta_seconds <= 0 and not self.cancelled:
            self._render_countdown("--", None)
            return
        elapsed = time.time() - self.fetch_timestamp
        remaining = self.eta_seconds - elapsed
        if remaining < -60:
            # Long gone — drop the row instead of keeping "Parti" around
            # until the next refresh rebuilds the list.
            self.hide()
        elif self.cancelled:
            # No countdown for a train that won't come: it would read as
            # one to catch.
            self._render_countdown("Annule", "countdown_departed")
        elif remaining < -30:
            self._render_countdown("Parti", "countdown_departed")
        elif remaining < 60:
            self._render_countdown("< 1 min", "countdown_imminent")
        else:
            # Round to whole seconds first so timer jitter can't flip
            # e.g. 480.0s into "7 min" a tick early.
            minutes = int(round(remaining)) // 60
            self._render_countdown(f"{minutes} min", "countdown_normal")

    def _render_countdown(self, text: str, color_key):
        """Apply text/style only when changed — a stylesheet update every
        second forces a relayout, which stutters scrolling on the Pi."""
        theme = get_theme()
        if self._countdown_shown == (text, color_key, theme):
            return
        self._countdown_shown = (text, color_key, theme)
        self.countdown_label.setText(text)
        if color_key:
            self.countdown_label.setStyleSheet(
                f"color: {THEME_COLORS[theme][color_key]}; font-size: 22px; font-weight: bold;"
            )


# ─── FavouriteGroup ──────────────────────────────────────────────────────────

class FavouriteGroup(QFrame):
    """Groups departure cards for one favourite, with edit controls."""

    delete_requested = pyqtSignal(object)  # emits the Favourite
    move_requested = pyqtSignal(object, int)  # Favourite, -1 (up) / +1 (down)

    def __init__(self, favourite: Favourite, departures: list,
                 edit_mode: bool = False, stale: bool = False,
                 traffic: tuple = (), can_move_up: bool = False,
                 can_move_down: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("favouriteGroup")
        self.favourite = favourite
        self.cards = []
        self.delete_btn = self.up_btn = self.down_btn = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header row
        header_row = QHBoxLayout()
        if favourite.destination_name:
            header_text = f"{favourite.stop_name}  \u2192  {favourite.destination_name}"
        else:
            header_text = favourite.stop_name
        header_label = QLabel(header_text)
        header_label.setObjectName("groupHeader")
        header_row.addWidget(header_label, stretch=1)

        if edit_mode:
            self.up_btn = self._make_move_btn(Icons.ARROW_UP, -1, can_move_up)
            self.down_btn = self._make_move_btn(Icons.ARROW_DOWN, 1, can_move_down)
            header_row.addWidget(self.up_btn)
            header_row.addWidget(self.down_btn)
            self.delete_btn = QPushButton(Icons.CLOSE)
            self.delete_btn.setObjectName("deleteBtn")
            self.delete_btn.setFont(icon_font(14))
            self.delete_btn.clicked.connect(
                lambda: self.delete_requested.emit(self.favourite))
            header_row.addWidget(self.delete_btn)

        layout.addLayout(header_row)

        # Last refresh of this stop failed: the rows below are from an
        # earlier fetch and may miss newly announced trains or delays.
        self.stale_label = None
        if stale and departures:
            self.stale_label = QLabel("Donnees non actualisees")
            self.stale_label.setObjectName("staleLabel")
            layout.addWidget(self.stale_label)

        self.traffic_labels = []
        for message in traffic:
            lbl = QLabel(f"\u26a0  {message}")
            lbl.setObjectName("trafficLabel")
            lbl.setWordWrap(True)
            self.traffic_labels.append(lbl)
            layout.addWidget(lbl)

        # Departure cards
        if departures:
            for dep in departures[:5]:
                card = DepartureCard(
                    dep,
                    line_color=favourite.line_color,
                    line_text_color=favourite.line_text_color,
                    badge_name=favourite.line_name,
                )
                self.cards.append(card)
                layout.addWidget(card)
        else:
            no_dep = QLabel("Aucun depart prevu")
            no_dep.setObjectName("noDepartureLabel")
            layout.addWidget(no_dep)

    def _make_move_btn(self, icon: str, delta: int, enabled: bool) -> QPushButton:
        btn = QPushButton(icon)
        btn.setObjectName("moveBtn")
        btn.setFont(icon_font(18))
        btn.setEnabled(enabled)
        btn.clicked.connect(lambda: self.move_requested.emit(self.favourite, delta))
        return btn

    def update_countdowns(self):
        for card in self.cards:
            card.update_countdown()


# ─── HomeScreen ──────────────────────────────────────────────────────────────

class HomeScreen(QWidget):
    """Main display showing favourites and their departures."""

    add_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    edit_toggled = pyqtSignal()
    settings_requested = pyqtSignal()

    POPULATE_MAX_DEFER_S = 5.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit_mode = False
        self.groups = []
        self._pending_populate = None
        self._populate_deferred_since = None  # monotonic time of first deferral
        self._populate_retry = QTimer(self)
        self._populate_retry.setSingleShot(True)
        self._populate_retry.setInterval(250)
        self._populate_retry.timeout.connect(self._apply_pending_populate)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header
        header = QFrame()
        header.setObjectName("headerBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 4, 12, 4)

        # Clock in place of a title: on a wall display the time is what
        # you check alongside the departures.
        self.header_clock = QLabel("")
        self.header_clock.setObjectName("headerClock")
        header_layout.addWidget(self.header_clock)
        self.header_date = QLabel("")
        self.header_date.setObjectName("headerDate")
        self.header_date.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header_layout.addSpacing(10)
        header_layout.addWidget(self.header_date, stretch=1)
        self.update_clock()

        self.refresh_btn = QPushButton(Icons.REFRESH)
        self.refresh_btn.setObjectName("headerBtn")
        self.refresh_btn.setFont(icon_font(20))
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        header_layout.addWidget(self.refresh_btn)

        self.edit_btn = QPushButton(Icons.EDIT)
        self.edit_btn.setObjectName("headerBtn")
        self.edit_btn.setFont(icon_font(20))
        self.edit_btn.clicked.connect(self._toggle_edit_mode)
        header_layout.addWidget(self.edit_btn)

        self.add_btn = QPushButton("+")
        self.add_btn.setObjectName("headerBtn")
        self.add_btn.clicked.connect(self.add_requested.emit)
        header_layout.addWidget(self.add_btn)

        self.settings_btn = QPushButton(Icons.SETTINGS)
        self.settings_btn.setObjectName("headerBtn")
        self.settings_btn.setFont(icon_font(20))
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        header_layout.addWidget(self.settings_btn)

        layout.addWidget(header)

        # ── Scroll area
        self.scroll = make_touch_scroll_area()
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 4, 0, 4)
        self.scroll_layout.setSpacing(4)
        self.scroll_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, stretch=1)

        # ── Empty state
        self.empty_label = QLabel("Aucun favori\nAppuyez sur + pour ajouter un arret")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)

        # ── Status bar
        status_bar = QFrame()
        status_bar.setObjectName("statusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(8, 0, 8, 0)

        self.updated_label = QLabel("")
        self.updated_label.setObjectName("statusLabel")
        status_layout.addWidget(self.updated_label, stretch=1)

        self.next_refresh_label = QLabel("")
        self.next_refresh_label.setObjectName("statusLabel")
        status_layout.addWidget(self.next_refresh_label)

        layout.addWidget(status_bar)

    def _toggle_edit_mode(self):
        self.edit_mode = not self.edit_mode
        colors = THEME_COLORS[get_theme()]
        self.edit_btn.setStyleSheet(
            f"background-color: {colors['edit_active_bg']}; color: {colors['edit_active_fg']};"
            if self.edit_mode else ""
        )
        self.edit_toggled.emit()

    def populate(self, favourites, departure_map, delete_callback=None,
                 stale_keys=(), traffic=None, move_callback=None):
        """Rebuild the scroll area with favourite groups.

        Deferred while a touch scroll gesture is in flight: tearing the
        widgets out from under the finger kills the gesture mid-scroll.
        The deferral is capped (POPULATE_MAX_DEFER_S): if the touchscreen
        loses a release, QScroller stays "pressed" forever, and the list
        used to never refresh again until a reboot.
        """
        self._pending_populate = (list(favourites), dict(departure_map),
                                  delete_callback, set(stale_keys),
                                  dict(traffic or {}), move_callback)
        scroller = QScroller.scroller(self.scroll.viewport())
        if scroller.state() != QScroller.Inactive:
            self._populate_retry.start()
            return
        self._apply_pending_populate()

    def _apply_pending_populate(self):
        if self._pending_populate is None:
            return
        scroller = QScroller.scroller(self.scroll.viewport())
        if scroller.state() != QScroller.Inactive:
            now = time.monotonic()
            if self._populate_deferred_since is None:
                self._populate_deferred_since = now
            if now - self._populate_deferred_since < self.POPULATE_MAX_DEFER_S:
                self._populate_retry.start()
                return
            # Gesture stuck (lost touch release): reset it and rebuild anyway.
            scroller.stop()
        self._populate_deferred_since = None
        self._populate_retry.stop()

        (favourites, departure_map, delete_callback, stale_keys,
         traffic, move_callback) = self._pending_populate
        self._pending_populate = None

        scroll_pos = self.scroll.verticalScrollBar().value()

        # Clear old groups (keep the reusable empty-state label alive)
        self.groups.clear()
        while self.scroll_layout.count() > 0:
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is self.empty_label:
                widget.setParent(None)
            elif widget:
                widget.deleteLater()

        if not favourites:
            self.scroll_layout.addWidget(self.empty_label)
            self.empty_label.show()
            self.scroll_layout.addStretch()
            return

        last = len(favourites) - 1
        for i, fav in enumerate(favourites):
            group = FavouriteGroup(
                fav, departure_map.get(fav.key, []),
                edit_mode=self.edit_mode,
                stale=fav.key in stale_keys,
                traffic=tuple(traffic.get(fav.line_id, ())),
                can_move_up=i > 0, can_move_down=i < last)
            if delete_callback:
                group.delete_requested.connect(delete_callback)
            if move_callback:
                group.move_requested.connect(move_callback)
            self.groups.append(group)
            self.scroll_layout.addWidget(group)

        self.scroll_layout.addStretch()
        # Restore scroll position once the new content has been laid out
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_pos))

    def update_countdowns(self):
        """Called every second to update all departure countdowns."""
        self.update_clock()
        for group in self.groups:
            group.update_countdowns()

    def update_clock(self, now: datetime = None):
        now = now or datetime.now()
        self.header_clock.setText(now.strftime("%H:%M"))
        # Spelled out by hand: the Pi image has no French locale installed.
        self.header_date.setText(
            f"{FRENCH_DAYS[now.weekday()]} {now.day} {FRENCH_MONTHS[now.month - 1]}")

    def set_updated_time(self, text: str):
        self.updated_label.setText(text)

    def set_next_refresh(self, text: str):
        self.next_refresh_label.setText(text)


# ─── SleepOverlay ────────────────────────────────────────────────────────────

class SleepOverlay(QWidget):
    """Full-screen overlay shown when the kiosk is asleep."""

    tapped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Plain QWidgets don't paint stylesheet backgrounds without this —
        # the screen underneath would show through around the labels.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: black;")
        self.setCursor(Qt.BlankCursor)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        moon = QLabel(Icons.MOON)
        moon.setFont(icon_font(56))
        moon.setAlignment(Qt.AlignCenter)
        moon.setStyleSheet("color: #30363d; background-color: black;")
        layout.addWidget(moon)

        hint = QLabel("Appuyez pour activer")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #484f58; font-size: 28px; background-color: black;")
        layout.addWidget(hint)

    def mousePressEvent(self, event):
        self.tapped.emit()


# ─── SettingsScreen ──────────────────────────────────────────────────────────
