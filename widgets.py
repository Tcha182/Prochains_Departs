"""UI widgets: DepartureCard, FavouriteGroup, HomeScreen, SearchScreen, SettingsScreen, SleepOverlay."""

import os
import time
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLineEdit, QStackedWidget, QScroller, QGridLayout,
)

from models import Favourite, Departure, LineAtStop, StopOnLine, normalize, is_same_place
from styles import THEME_COLORS, get_theme, Icons, icon_font

TRANSPORT_MODES = [
    ("Bus", "bus", Icons.BUS),
    ("Metro", "metro", Icons.METRO),
    ("Tramway", "tram", Icons.TRAM),
    ("Train / RER", "rail", Icons.TRAIN),
]

TRANSPORT_MODE_LABELS = {mode: label for label, mode, _icon in TRANSPORT_MODES}


# ─── DepartureCard ───────────────────────────────────────────────────────────

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
            "cancelled": "Annule",
            "noReport": "",
            "arrived": "Arrive",
        }
        return status_map.get(dep.departure_status, dep.departure_status)

    def update_countdown(self):
        """Recompute countdown from fetch_timestamp + eta_seconds."""
        if self.eta_seconds <= 0:
            self.countdown_label.setText("--")
            return
        elapsed = time.time() - self.fetch_timestamp
        remaining = self.eta_seconds - elapsed
        colors = THEME_COLORS[get_theme()]
        if remaining < -30:
            self.countdown_label.setText("Parti")
            self.countdown_label.setStyleSheet(
                f"color: {colors['countdown_departed']}; font-size: 22px; font-weight: bold;"
            )
        elif remaining < 60:
            self.countdown_label.setText("< 1 min")
            self.countdown_label.setStyleSheet(
                f"color: {colors['countdown_imminent']}; font-size: 22px; font-weight: bold;"
            )
        else:
            minutes = int(remaining / 60)
            self.countdown_label.setText(f"{minutes} min")
            self.countdown_label.setStyleSheet(
                f"color: {colors['countdown_normal']}; font-size: 22px; font-weight: bold;"
            )


# ─── FavouriteGroup ──────────────────────────────────────────────────────────

class FavouriteGroup(QFrame):
    """Groups departure cards for one favourite, with optional delete button."""

    delete_requested = pyqtSignal(object)  # emits the Favourite

    def __init__(self, favourite: Favourite, departures: list,
                 edit_mode: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("favouriteGroup")
        self.favourite = favourite
        self.cards = []

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
            del_btn = QPushButton(Icons.CLOSE)
            del_btn.setObjectName("deleteBtn")
            del_btn.setFont(icon_font(14))
            del_btn.clicked.connect(lambda: self.delete_requested.emit(self.favourite))
            header_row.addWidget(del_btn)

        layout.addLayout(header_row)

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit_mode = False
        self.groups = []
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

        title = QLabel("Prochains departs")
        title.setObjectName("headerTitle")
        header_layout.addWidget(title, stretch=1)

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
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Enable touch scrolling
        QScroller.grabGesture(self.scroll.viewport(), QScroller.LeftMouseButtonGesture)

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

    def populate(self, favourites, departure_map, delete_callback=None):
        """Rebuild the scroll area with favourite groups."""
        # Clear old groups
        self.groups.clear()
        while self.scroll_layout.count() > 0:
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not favourites:
            self.scroll_layout.addWidget(self.empty_label)
            self.scroll_layout.addStretch()
            return

        for fav in favourites:
            fav_key = f"{fav.stop_area_id}_{fav.line_id}_{fav.direction}"
            departures = departure_map.get(fav_key, [])
            group = FavouriteGroup(fav, departures, edit_mode=self.edit_mode)
            if delete_callback:
                group.delete_requested.connect(delete_callback)
            self.groups.append(group)
            self.scroll_layout.addWidget(group)

        self.scroll_layout.addStretch()

    def update_countdowns(self):
        """Called every second to update all departure countdowns."""
        for group in self.groups:
            group.update_countdowns()

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
        self.setStyleSheet("background-color: black;")
        self.setCursor(Qt.BlankCursor)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        moon = QLabel(Icons.MOON)
        moon.setAlignment(Qt.AlignCenter)
        moon.setFont(icon_font(64))
        moon.setStyleSheet("color: #484f58; background-color: black;")
        layout.addWidget(moon)

        hint = QLabel("Appuyez pour reactiver")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #484f58; font-size: 16px; background-color: black;")
        layout.addWidget(hint)

    def mousePressEvent(self, event):
        self.tapped.emit()


# ─── SettingsScreen ──────────────────────────────────────────────────────────

SLEEP_OPTIONS = [5, 10, 30, 0]  # 0 = disabled
SLEEP_LABELS = {5: "5 min", 10: "10 min", 30: "30 min", 0: "Desactive"}


class SettingsScreen(QWidget):
    """Settings screen with WiFi, API key, theme, and sleep configuration."""

    back_to_home = pyqtSignal()
    theme_changed = pyqtSignal(str)
    sleep_delay_changed = pyqtSignal(int)
    wifi_scan_requested = pyqtSignal()
    wifi_connect_requested = pyqtSignal(str, str)  # ssid, password
    api_token_saved = pyqtSignal(str)

    def __init__(self, current_theme: str = "dark", current_sleep: int = 10, parent=None):
        super().__init__(parent)
        self._theme = current_theme
        self._sleep = current_sleep
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self._build_main_page()     # page 0
        self._build_wifi_page()     # page 1
        self._build_api_page()      # page 2

    # ── Page 0: Main settings ──

    def _build_main_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("headerBar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 4, 12, 4)

        back_btn = QPushButton(Icons.BACK)
        back_btn.setObjectName("backBtn")
        back_btn.setFont(icon_font(22))
        back_btn.clicked.connect(self.back_to_home.emit)
        h_layout.addWidget(back_btn)

        title = QLabel("Parametres")
        title.setObjectName("headerTitle")
        h_layout.addWidget(title, stretch=1)

        layout.addWidget(header)

        # Settings rows
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 12, 0, 12)
        content_layout.setSpacing(6)

        # WiFi row
        wifi_row = self._make_settings_row("WiFi", Icons.CHEVRON_RIGHT)
        wifi_row.mousePressEvent = lambda e: self._open_wifi()
        content_layout.addWidget(wifi_row)

        # API key row
        api_row = self._make_settings_row("Cle API", Icons.CHEVRON_RIGHT)
        api_row.mousePressEvent = lambda e: self._open_api()
        content_layout.addWidget(api_row)

        # Theme row
        theme_label = "Sombre" if self._theme == "dark" else "Clair"
        self.theme_row = self._make_settings_row("Theme", theme_label)
        self.theme_row.setCursor(Qt.PointingHandCursor)
        self.theme_row.mousePressEvent = lambda e: self._toggle_theme()
        content_layout.addWidget(self.theme_row)

        # Sleep row
        self.sleep_row = self._make_settings_row("Mise en veille", SLEEP_LABELS.get(self._sleep, f"{self._sleep} min"))
        self.sleep_row.setCursor(Qt.PointingHandCursor)
        self.sleep_row.mousePressEvent = lambda e: self._cycle_sleep()
        content_layout.addWidget(self.sleep_row)

        content_layout.addStretch()
        layout.addWidget(content, stretch=1)

        self.stack.addWidget(page)

    def _make_settings_row(self, label_text, value_text, value_is_icon=False):
        row = QFrame()
        row.setObjectName("settingsRow")
        row.setCursor(Qt.PointingHandCursor)
        r_layout = QHBoxLayout(row)
        r_layout.setContentsMargins(14, 8, 14, 8)

        label = QLabel(label_text)
        label.setObjectName("settingsLabel")
        r_layout.addWidget(label, stretch=1)

        value = QLabel(value_text)
        value.setObjectName("settingsValue")
        if value_is_icon or value_text in (Icons.CHEVRON_RIGHT,):
            value.setFont(icon_font(18))
        r_layout.addWidget(value)

        row._value_label = value
        return row

    def _toggle_theme(self):
        self._theme = "light" if self._theme == "dark" else "dark"
        label = "Sombre" if self._theme == "dark" else "Clair"
        self.theme_row._value_label.setText(label)
        self.theme_changed.emit(self._theme)

    def _cycle_sleep(self):
        try:
            idx = SLEEP_OPTIONS.index(self._sleep)
        except ValueError:
            idx = -1
        self._sleep = SLEEP_OPTIONS[(idx + 1) % len(SLEEP_OPTIONS)]
        self.sleep_row._value_label.setText(SLEEP_LABELS.get(self._sleep, f"{self._sleep} min"))
        self.sleep_delay_changed.emit(self._sleep)

    def _open_wifi(self):
        self.wifi_status.setText("Recherche...")
        self.wifi_scan_requested.emit()
        self.stack.setCurrentIndex(1)

    def _open_api(self):
        self.api_input.clear()
        self.stack.setCurrentIndex(2)

    # ── Page 1: WiFi ──

    def _build_wifi_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("headerBar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 4, 12, 4)

        back_btn = QPushButton(Icons.BACK)
        back_btn.setObjectName("backBtn")
        back_btn.setFont(icon_font(22))
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        h_layout.addWidget(back_btn)

        title = QLabel("WiFi")
        title.setObjectName("headerTitle")
        h_layout.addWidget(title, stretch=1)

        scan_btn = QPushButton(Icons.REFRESH)
        scan_btn.setObjectName("headerBtn")
        scan_btn.setFont(icon_font(20))
        scan_btn.clicked.connect(self._on_scan_pressed)
        h_layout.addWidget(scan_btn)

        layout.addWidget(header)

        # Status
        self.wifi_status = QLabel("")
        self.wifi_status.setObjectName("loadingLabel")
        layout.addWidget(self.wifi_status)

        # Password bar (hidden by default)
        self.wifi_pw_bar = QWidget()
        pw_layout = QHBoxLayout(self.wifi_pw_bar)
        pw_layout.setContentsMargins(8, 4, 8, 4)
        self.wifi_pw_input = QLineEdit()
        self.wifi_pw_input.setObjectName("settingsInput")
        self.wifi_pw_input.setPlaceholderText("Mot de passe...")
        self.wifi_pw_input.setEchoMode(QLineEdit.Password)
        pw_layout.addWidget(self.wifi_pw_input)
        self.wifi_connect_btn = QPushButton("Connecter")
        self.wifi_connect_btn.setObjectName("saveBtn")
        self.wifi_connect_btn.clicked.connect(self._on_connect_pressed)
        pw_layout.addWidget(self.wifi_connect_btn)
        self.wifi_pw_bar.hide()
        layout.addWidget(self.wifi_pw_bar)

        # Scroll area for networks
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        self.wifi_list_widget = QWidget()
        self.wifi_list_layout = QVBoxLayout(self.wifi_list_widget)
        self.wifi_list_layout.setContentsMargins(0, 4, 0, 4)
        self.wifi_list_layout.setSpacing(2)
        self.wifi_list_layout.addStretch()
        scroll.setWidget(self.wifi_list_widget)
        layout.addWidget(scroll, stretch=1)

        self._selected_ssid = ""
        self.stack.addWidget(page)

    def _on_scan_pressed(self):
        self.wifi_status.setText("Recherche...")
        self.wifi_pw_bar.hide()
        self._selected_ssid = ""
        self.wifi_scan_requested.emit()

    def _on_connect_pressed(self):
        if self._selected_ssid:
            self.wifi_status.setText("Connexion...")
            self.wifi_connect_requested.emit(self._selected_ssid, self.wifi_pw_input.text())

    def on_wifi_scan_results(self, networks: list):
        """Called from main window when scan results arrive."""
        self.wifi_status.setText("")
        self._clear_layout(self.wifi_list_layout)

        if not networks:
            lbl = QLabel("Aucun reseau trouve")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.wifi_list_layout.addWidget(lbl)
            self.wifi_list_layout.addStretch()
            return

        for net in networks:
            item = QFrame()
            item.setObjectName("wifiItem")
            item.setCursor(Qt.PointingHandCursor)
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(10, 6, 10, 6)

            # Connected indicator
            if net["in_use"]:
                check = QLabel(Icons.CHECK)
                check.setFont(icon_font(16))
                check.setStyleSheet("color: #238636;")
                item_layout.addWidget(check)

            ssid_label = QLabel(net["ssid"])
            ssid_label.setObjectName("settingsLabel")
            item_layout.addWidget(ssid_label, stretch=1)

            # Lock icon for secured networks
            if net["security"] and net["security"] != "--":
                lock = QLabel(Icons.LOCK)
                lock.setFont(icon_font(14))
                lock.setObjectName("wifiSignal")
                item_layout.addWidget(lock)

            signal_label = QLabel(f"{net['signal']}%")
            signal_label.setObjectName("wifiSignal")
            item_layout.addWidget(signal_label)

            # Only make tappable if it's a real network (not the "non disponible" placeholder)
            if net["signal"] > 0 or net["in_use"]:
                item.mousePressEvent = lambda e, n=net: self._on_wifi_selected(n)

            self.wifi_list_layout.addWidget(item)

        self.wifi_list_layout.addStretch()

    def _on_wifi_selected(self, network):
        self._selected_ssid = network["ssid"]
        if network["security"] and network["security"] != "--" and not network["in_use"]:
            self.wifi_pw_bar.show()
            self.wifi_pw_input.clear()
            self.wifi_pw_input.setFocus()
        else:
            # Open network or already connected — connect directly
            self.wifi_pw_bar.hide()
            self.wifi_status.setText("Connexion...")
            self.wifi_connect_requested.emit(self._selected_ssid, "")

    def on_wifi_connect_result(self, success: bool, message: str):
        """Called from main window when connect result arrives."""
        self.wifi_status.setText(message)
        if success:
            self.wifi_pw_bar.hide()
            # Re-scan to update the list
            QTimer.singleShot(1000, self._on_scan_pressed)

    # ── Page 2: API key ──

    def _build_api_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("headerBar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 4, 12, 4)

        back_btn = QPushButton(Icons.BACK)
        back_btn.setObjectName("backBtn")
        back_btn.setFont(icon_font(22))
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        h_layout.addWidget(back_btn)

        title = QLabel("Cle API")
        title.setObjectName("headerTitle")
        h_layout.addWidget(title, stretch=1)

        layout.addWidget(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 24, 16, 16)
        content_layout.setSpacing(12)

        # Current key (masked)
        current_label = QLabel("Cle actuelle")
        current_label.setObjectName("settingsLabel")
        content_layout.addWidget(current_label)

        self.api_current = QLabel(self._mask_token())
        self.api_current.setObjectName("settingsValue")
        content_layout.addWidget(self.api_current)

        # New key input
        new_label = QLabel("Nouvelle cle")
        new_label.setObjectName("settingsLabel")
        content_layout.addWidget(new_label)

        self.api_input = QLineEdit()
        self.api_input.setObjectName("settingsInput")
        self.api_input.setPlaceholderText("Coller la cle API...")
        content_layout.addWidget(self.api_input)

        save_btn = QPushButton("Enregistrer")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._on_save_api)
        content_layout.addWidget(save_btn)

        content_layout.addStretch()
        layout.addWidget(content, stretch=1)

        self.stack.addWidget(page)

    def _mask_token(self):
        """Mask the current API token for display."""
        import api
        token = api.API_TOKEN
        if not token or token == "your-api-token-here":
            return "Non configure"
        if len(token) <= 8:
            return "\u2022" * len(token)
        return token[:4] + "\u2022\u2022\u2022\u2022" + token[-4:]

    def _on_save_api(self):
        token = self.api_input.text().strip()
        if token:
            self.api_token_saved.emit(token)
            self.api_current.setText(self._mask_token_str(token))
            self.api_input.clear()

    def _mask_token_str(self, token: str) -> str:
        if len(token) <= 8:
            return "\u2022" * len(token)
        return token[:4] + "\u2022\u2022\u2022\u2022" + token[-4:]

    def update_theme(self, theme: str):
        """Update internal theme state for the toggle label."""
        self._theme = theme
        label = "Sombre" if theme == "dark" else "Clair"
        self.theme_row._value_label.setText(label)

    def update_sleep(self, minutes: int):
        """Update internal sleep state."""
        self._sleep = minutes
        self.sleep_row._value_label.setText(SLEEP_LABELS.get(minutes, f"{minutes} min"))

    # ── Helpers ──

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()


# ─── SearchScreen ────────────────────────────────────────────────────────────

class SearchScreen(QWidget):
    """4-step flow: transport type -> line -> stop -> direction."""

    favourite_added = pyqtSignal(object)  # emits Favourite
    back_to_home = pyqtSignal()

    # Signals requesting API calls from main window
    line_search_requested = pyqtSignal(str, str)          # query, mode
    stops_on_line_requested = pyqtSignal(str)              # route_id
    resolve_and_probe_requested = pyqtSignal(str, str)     # stop_id, line_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_mode = ""     # transportmode API value
        self.selected_line = None   # LineAtStop
        self.selected_stop = None   # StopOnLine
        self._all_stops = []        # full stop list for filtering
        self._resolved_stop_area_id = ""
        self._resolved_stop_name = ""
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(400)
        self._debounce_timer.timeout.connect(self._do_search)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self._build_mode_page()           # page 0
        self._build_line_search_page()    # page 1
        self._build_stop_selection_page() # page 2
        self._build_direction_page()      # page 3

    def _make_header(self, title_text, parent_layout):
        header = QFrame()
        header.setObjectName("headerBar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 4, 12, 4)

        back_btn = QPushButton(Icons.BACK)
        back_btn.setObjectName("backBtn")
        back_btn.setFont(icon_font(22))
        back_btn.clicked.connect(self._go_back)
        h_layout.addWidget(back_btn)

        title = QLabel(title_text)
        title.setObjectName("headerTitle")
        h_layout.addWidget(title, stretch=1)

        parent_layout.addWidget(header)
        return header

    def _go_back(self):
        idx = self.stack.currentIndex()
        if idx == 0:
            self.back_to_home.emit()
        else:
            self.stack.setCurrentIndex(idx - 1)

    # ── Step 1: Transport type ──

    def _build_mode_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Type de transport", layout)

        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(16, 24, 16, 16)
        grid.setSpacing(12)

        for i, (label, mode_val, icon_char) in enumerate(TRANSPORT_MODES):
            card = QFrame()
            card.setObjectName("modeBtn")
            card.setCursor(Qt.PointingHandCursor)
            card.setMinimumHeight(100)
            card_layout = QVBoxLayout(card)
            card_layout.setAlignment(Qt.AlignCenter)
            card_layout.setSpacing(4)

            icon_lbl = QLabel(icon_char)
            icon_lbl.setFont(icon_font(32))
            icon_lbl.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(icon_lbl)

            text_lbl = QLabel(label)
            text_lbl.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(text_lbl)

            card.mousePressEvent = lambda e, m=mode_val: self._on_mode_selected(m)
            grid.addWidget(card, i // 2, i % 2)

        layout.addWidget(grid_container)
        layout.addStretch()
        self.stack.addWidget(page)

    def _on_mode_selected(self, mode: str):
        self.selected_mode = mode
        self.line_loading.setText("Chargement...")
        self.search_input.clear()
        self._clear_layout(self.line_results_layout)
        self.line_step_title.setText(TRANSPORT_MODE_LABELS.get(mode, mode))
        self.stack.setCurrentIndex(1)
        # Load all lines for this mode immediately
        self.line_search_requested.emit("", mode)

    # ── Step 2: Line search ──

    def _build_line_search_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Chercher une ligne", layout)

        self.line_step_title = QLabel("")
        self.line_step_title.setObjectName("stepTitle")
        layout.addWidget(self.line_step_title)

        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(8, 4, 8, 4)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Numero de ligne...")
        self.search_input.textChanged.connect(self._on_search_text_changed)
        input_layout.addWidget(self.search_input)
        layout.addWidget(input_container)

        self.line_loading = QLabel("")
        self.line_loading.setObjectName("loadingLabel")
        layout.addWidget(self.line_loading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        self.line_results_widget = QWidget()
        self.line_results_layout = QVBoxLayout(self.line_results_widget)
        self.line_results_layout.setContentsMargins(0, 0, 0, 0)
        self.line_results_layout.setSpacing(2)
        self.line_results_layout.addStretch()
        scroll.setWidget(self.line_results_widget)
        layout.addWidget(scroll, stretch=1)

        self.stack.addWidget(page)

    def _on_search_text_changed(self, text):
        self._debounce_timer.stop()
        if len(text.strip()) >= 1:
            self._debounce_timer.start()
        else:
            # Empty input: reload all lines for the mode
            self._do_search()

    def _do_search(self):
        query = self.search_input.text().strip()
        self.line_loading.setText("Recherche...")
        self.line_search_requested.emit(query, self.selected_mode)

    def on_line_results(self, lines: list):
        """Called when line search results arrive."""
        self.line_loading.setText("")
        self._clear_layout(self.line_results_layout)

        if not lines:
            lbl = QLabel("Aucun resultat")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.line_results_layout.addWidget(lbl)
            self.line_results_layout.addStretch()
            return

        for line in lines:
            btn_widget = QFrame()
            btn_widget.setObjectName("resultItem")
            btn_widget.setCursor(Qt.PointingHandCursor)
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(10, 6, 10, 6)

            badge = QLabel(line.line_name)
            badge.setFixedWidth(60)
            badge.setAlignment(Qt.AlignCenter)
            bg = line.line_color or "FFFFFF"
            fg = line.line_text_color or "000000"
            badge.setStyleSheet(
                f"background-color: #{bg}; color: #{fg}; "
                f"border-radius: 6px; padding: 4px 8px; font-weight: bold; font-size: 14px;"
            )
            btn_layout.addWidget(badge)

            name_label = QLabel(f"{line.line_name} - {line.mode}" if line.mode else line.line_name)
            name_label.setObjectName("resultTitle")
            btn_layout.addWidget(name_label, stretch=1)

            btn_widget.mousePressEvent = lambda e, l=line: self._on_line_selected(l)
            self.line_results_layout.addWidget(btn_widget)

        self.line_results_layout.addStretch()

    def _on_line_selected(self, line: LineAtStop):
        self.selected_line = line
        self.stop_loading.setText("Chargement des arrets...")
        self._all_stops = []
        self.stop_filter_input.clear()
        self._clear_layout(self.stop_results_layout)
        self.stop_step_title.setText(f"Arrets - {line.line_name}")
        self.stops_on_line_requested.emit(line.route_id)
        self.stack.setCurrentIndex(2)

    # ── Step 3: Stop selection ──

    def _build_stop_selection_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Choisir un arret", layout)

        step_title = QLabel("")
        step_title.setObjectName("stepTitle")
        self.stop_step_title = step_title
        layout.addWidget(step_title)

        filter_container = QWidget()
        filter_layout = QHBoxLayout(filter_container)
        filter_layout.setContentsMargins(8, 4, 8, 4)
        self.stop_filter_input = QLineEdit()
        self.stop_filter_input.setObjectName("searchInput")
        self.stop_filter_input.setPlaceholderText("Filtrer les arrets...")
        self.stop_filter_input.textChanged.connect(self._on_stop_filter_changed)
        filter_layout.addWidget(self.stop_filter_input)
        layout.addWidget(filter_container)

        self.stop_loading = QLabel("")
        self.stop_loading.setObjectName("loadingLabel")
        layout.addWidget(self.stop_loading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        self.stop_results_widget = QWidget()
        self.stop_results_layout = QVBoxLayout(self.stop_results_widget)
        self.stop_results_layout.setContentsMargins(0, 0, 0, 0)
        self.stop_results_layout.setSpacing(2)
        self.stop_results_layout.addStretch()
        scroll.setWidget(self.stop_results_widget)
        layout.addWidget(scroll, stretch=1)

        self.stack.addWidget(page)

    def on_stop_results(self, stops: list):
        """Called when stops-on-line results arrive."""
        self.stop_loading.setText("")
        self._all_stops = list(stops)
        self._display_filtered_stops()

    def _on_stop_filter_changed(self, text):
        self._display_filtered_stops()

    def _display_filtered_stops(self):
        self._clear_layout(self.stop_results_layout)
        query = normalize(self.stop_filter_input.text())
        filtered = [s for s in self._all_stops if query in normalize(s.stop_name)] if query else self._all_stops

        if not filtered:
            lbl = QLabel("Aucun arret trouve")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.stop_results_layout.addWidget(lbl)
            self.stop_results_layout.addStretch()
            return

        for stop in filtered:
            btn = self._make_result_item(
                stop.stop_name, "",
                lambda checked, s=stop: self._on_stop_selected(s),
            )
            self.stop_results_layout.addWidget(btn)
        self.stop_results_layout.addStretch()

    def _on_stop_selected(self, stop: StopOnLine):
        self.selected_stop = stop
        self._resolved_stop_area_id = ""
        self._resolved_stop_name = ""
        self.dir_loading.setText("Chargement des directions...")
        self._clear_layout(self.dir_results_layout)
        self.dir_step_title.setText(
            f"{stop.stop_name} - {self.selected_line.line_name}"
            if self.selected_line else stop.stop_name
        )
        self.resolve_and_probe_requested.emit(stop.stop_id, self.selected_line.line_id)
        self.stack.setCurrentIndex(3)

    # ── Step 4: Direction selection ──

    def _build_direction_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Choisir une direction", layout)

        self.dir_step_title = QLabel("")
        self.dir_step_title.setObjectName("stepTitle")
        layout.addWidget(self.dir_step_title)

        self.dir_loading = QLabel("")
        self.dir_loading.setObjectName("loadingLabel")
        layout.addWidget(self.dir_loading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        self.dir_results_widget = QWidget()
        self.dir_results_layout = QVBoxLayout(self.dir_results_widget)
        self.dir_results_layout.setContentsMargins(0, 0, 0, 0)
        self.dir_results_layout.setSpacing(2)
        self.dir_results_layout.addStretch()
        scroll.setWidget(self.dir_results_widget)
        layout.addWidget(scroll, stretch=1)

        self.stack.addWidget(page)

    def on_directions_results(self, stop_area_id: str, stop_name: str,
                              directions: list):
        """Called when resolve+probe results arrive.

        directions = [(destination_name, direction_ref), ...]
        """
        self.dir_loading.setText("")
        self._resolved_stop_area_id = stop_area_id
        self._resolved_stop_name = stop_name
        self._clear_layout(self.dir_results_layout)

        if not stop_area_id:
            lbl = QLabel("Erreur de resolution")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.dir_results_layout.addWidget(lbl)
            self.dir_results_layout.addStretch()
            return

        # Filter out destinations matching the stop itself (terminus arrivals)
        if self.selected_stop and directions:
            stop_norm = normalize(self.selected_stop.stop_name)
            directions = [
                (dest, ref) for dest, ref in directions
                if not is_same_place(stop_norm, normalize(dest))
            ]

        if not directions:
            lbl = QLabel("Aucune direction disponible")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.dir_results_layout.addWidget(lbl)

            btn = QPushButton("Ajouter sans filtrer la direction")
            btn.setObjectName("addFavBtn")
            btn.clicked.connect(lambda: self._on_direction_selected("", ""))
            self.dir_results_layout.addWidget(btn)
            self.dir_results_layout.addStretch()
            return

        # Show each destination individually
        for dest, ref in directions:
            btn = QPushButton(f"\u2192  {dest}")
            btn.setObjectName("directionBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda checked, r=ref, d=dest: self._on_direction_selected(r, d),
            )
            self.dir_results_layout.addWidget(btn)

        self.dir_results_layout.addStretch()

    def _on_direction_selected(self, direction_ref: str, destination_name: str = ""):
        if not self.selected_line or not self._resolved_stop_area_id:
            return

        fav = Favourite(
            stop_area_id=self._resolved_stop_area_id,
            stop_name=self._resolved_stop_name or self.selected_stop.stop_name,
            line_id=self.selected_line.line_id,
            line_name=self.selected_line.line_name,
            line_color=self.selected_line.line_color,
            line_text_color=self.selected_line.line_text_color,
            direction=direction_ref,
            destination_name=destination_name,
        )
        self.favourite_added.emit(fav)

    # ── Helpers ──

    def _make_result_item(self, title, subtitle, on_click):
        """Create a clickable result item frame."""
        frame = QFrame()
        frame.setObjectName("resultItem")
        frame.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        t = QLabel(title)
        t.setObjectName("resultTitle")
        layout.addWidget(t)

        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("resultSubtitle")
            layout.addWidget(s)

        frame.mousePressEvent = lambda e: on_click(True)
        return frame

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def reset(self):
        """Reset the search screen back to step 1."""
        self.selected_mode = ""
        self.selected_line = None
        self.selected_stop = None
        self._all_stops = []
        self._resolved_stop_area_id = ""
        self._resolved_stop_name = ""
        self.search_input.clear()
        self.stop_filter_input.clear()
        self._clear_layout(self.line_results_layout)
        self._clear_layout(self.stop_results_layout)
        self._clear_layout(self.dir_results_layout)
        self.line_loading.setText("")
        self.stop_loading.setText("")
        self.dir_loading.setText("")
        self.stack.setCurrentIndex(0)
