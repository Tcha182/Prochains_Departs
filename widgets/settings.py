"""Settings screen: WiFi, API key, theme, sleep, brightness, updates."""

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QStackedWidget,
)

from styles import Icons, icon_font
from widgets.common import TappableFrame, make_touch_scroll_area


SLEEP_OPTIONS = [5, 10, 30, 0]  # 0 = disabled
SLEEP_LABELS = {5: "5 min", 10: "10 min", 30: "30 min", 0: "Desactive"}
DIM_OPTIONS = [100, 50, 25]  # evening backlight, % of full
DIM_LABELS = {100: "Desactive", 50: "50 %", 25: "25 %"}


class SettingsScreen(QWidget):
    """Settings screen with WiFi, API key, theme, and sleep configuration."""

    back_to_home = pyqtSignal()
    theme_changed = pyqtSignal(str)
    sleep_delay_changed = pyqtSignal(int)
    wifi_scan_requested = pyqtSignal()
    wifi_connect_requested = pyqtSignal(str, str)  # ssid, password
    api_token_saved = pyqtSignal(str)
    update_check_requested = pyqtSignal()
    evening_brightness_changed = pyqtSignal(int)

    def __init__(self, current_theme: str = "dark", current_sleep: int = 10,
                 current_dim: int = 100, parent=None):
        super().__init__(parent)
        self._theme = current_theme
        self._sleep = current_sleep
        self._dim = current_dim
        self._update_in_progress = False
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
        wifi_row.tapped.connect(self._open_wifi)
        content_layout.addWidget(wifi_row)

        # API key row
        api_row = self._make_settings_row("Cle API", Icons.CHEVRON_RIGHT)
        api_row.tapped.connect(self._open_api)
        content_layout.addWidget(api_row)

        # Theme row
        theme_label = "Sombre" if self._theme == "dark" else "Clair"
        self.theme_row = self._make_settings_row("Theme", theme_label)
        self.theme_row.tapped.connect(self._toggle_theme)
        content_layout.addWidget(self.theme_row)

        # Sleep row
        self.sleep_row = self._make_settings_row("Mise en veille", SLEEP_LABELS.get(self._sleep, f"{self._sleep} min"))
        self.sleep_row.tapped.connect(self._cycle_sleep)
        content_layout.addWidget(self.sleep_row)

        # Evening dimming row
        self.dim_row = self._make_settings_row(
            "Luminosite le soir", DIM_LABELS.get(self._dim, f"{self._dim} %"))
        self.dim_row.tapped.connect(self._cycle_dim)
        content_layout.addWidget(self.dim_row)

        # Update row
        self.update_row = self._make_settings_row("Mise a jour", "Verifier")
        self.update_row.tapped.connect(self._on_check_update_pressed)
        content_layout.addWidget(self.update_row)

        content_layout.addStretch()
        layout.addWidget(content, stretch=1)

        self.stack.addWidget(page)

    def _make_settings_row(self, label_text, value_text, value_is_icon=False):
        row = TappableFrame()
        row.setObjectName("settingsRow")
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

    def _cycle_dim(self):
        try:
            idx = DIM_OPTIONS.index(self._dim)
        except ValueError:
            idx = -1
        self._dim = DIM_OPTIONS[(idx + 1) % len(DIM_OPTIONS)]
        self.dim_row._value_label.setText(DIM_LABELS[self._dim])
        self.evening_brightness_changed.emit(self._dim)

    def _on_check_update_pressed(self):
        if self._update_in_progress:
            return
        self._update_in_progress = True
        self.update_row._value_label.setText("Verification...")
        self.update_check_requested.emit()

    def on_update_result(self, updated: bool, message: str):
        """Called from main window when the update check finishes."""
        self.update_row._value_label.setText(message)
        if updated:
            return  # app is about to restart, leave the message as-is
        self._update_in_progress = False
        QTimer.singleShot(3000, lambda: self.update_row._value_label.setText("Verifier"))

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
        scroll = make_touch_scroll_area()
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
            item = TappableFrame()
            item.setObjectName("wifiItem")
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
                item.tapped.connect(lambda n=net: self._on_wifi_selected(n))

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
        from api import get_api_token
        token = get_api_token()
        if not token or token == "your-api-token-here":
            return "Non configure"
        return self._mask_token_str(token)

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
