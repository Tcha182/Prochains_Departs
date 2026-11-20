"""Heating screen: one card per radiator (Home Assistant thermostat)."""

import time
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
)

import ha
from models import Radiator
from styles import THEME_COLORS, get_theme, Icons, icon_font
from widgets.common import make_touch_scroll_area


# Modes offered on the card, in tap order. "auto" is the valve following
# its own weekly programme; "heat" holds the temperature set here.
MODE_CYCLE = ("heat", "auto", "off")
MODE_LABELS = {"heat": "Manuel", "auto": "Programme", "off": "Arrêt"}


def format_temp(value: float) -> str:
    """21.5 -> "21,5" (French decimal comma)."""
    return f"{value:.1f}".replace(".", ",")


class RadiatorCard(QFrame):
    """Name, measured temperature, mode and −/+ target for one radiator.

    Taps change the display at once; the command itself is only sent once
    the taps stop (SEND_DELAY_MS), so going from 19 to 21 is one command,
    not four. Until Home Assistant reports the new value (a battery valve
    takes a few seconds to pick it up), a refresh must not put the old one
    back on screen: local changes are held for HOLD_SECONDS.
    """

    temperature_requested = pyqtSignal(str, float)  # entity_id, target
    # entity_id, hvac mode, target to set along with it (float) or None
    mode_requested = pyqtSignal(str, str, object)

    SEND_DELAY_MS = 800
    HOLD_SECONDS = 20
    # Target offered when switching on a valve that never had one here.
    DEFAULT_TARGET = 19.0

    def __init__(self, radiator: Radiator, parent=None):
        super().__init__(parent)
        self.setObjectName("radiatorCard")
        self.radiator = radiator
        self._target = radiator.target_temp
        self._mode = radiator.mode
        # A valve switched off reports no target, and comes back from "off"
        # at its minimum (4 °C): keep the last real target to restore it.
        self._last_target = radiator.target_temp if radiator.mode != "off" else None
        self._hold_until = 0.0  # time.monotonic()

        self._temp_timer = QTimer(self)
        self._temp_timer.setSingleShot(True)
        self._temp_timer.setInterval(self.SEND_DELAY_MS)
        self._temp_timer.timeout.connect(self._send_temperature)
        self._mode_timer = QTimer(self)
        self._mode_timer.setSingleShot(True)
        self._mode_timer.setInterval(self.SEND_DELAY_MS)
        self._mode_timer.timeout.connect(self._send_mode)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.name_label = QLabel("")
        self.name_label.setObjectName("radiatorName")
        left.addWidget(self.name_label)
        self.info_label = QLabel("")
        self.info_label.setObjectName("radiatorInfo")
        left.addWidget(self.info_label)
        layout.addLayout(left, stretch=1)

        self.mode_btn = QPushButton("")
        self.mode_btn.setObjectName("radiatorModeBtn")
        self.mode_btn.clicked.connect(self._cycle_mode)
        layout.addWidget(self.mode_btn)

        self.minus_btn = QPushButton("−")
        self.minus_btn.setObjectName("tempBtn")
        self.minus_btn.clicked.connect(lambda: self._step(-1))
        layout.addWidget(self.minus_btn)

        self.target_label = QLabel("")
        self.target_label.setObjectName("radiatorTarget")
        self.target_label.setAlignment(Qt.AlignCenter)
        self.target_label.setFixedWidth(104)
        layout.addWidget(self.target_label)

        self.plus_btn = QPushButton("+")
        self.plus_btn.setObjectName("tempBtn")
        self.plus_btn.clicked.connect(lambda: self._step(1))
        layout.addWidget(self.plus_btn)

        self._render()

    # ── State from Home Assistant ──

    def update_from(self, radiator: Radiator):
        self.radiator = radiator
        holding = time.monotonic() < self._hold_until
        caught_up = (radiator.target_temp == self._target
                     and radiator.mode == self._mode)
        if not holding or caught_up:
            self._hold_until = 0.0
            self._target = radiator.target_temp
            self._mode = radiator.mode
            if self._mode != "off" and self._target is not None:
                self._last_target = self._target
        self._render()

    def release_hold(self):
        """A command failed: show Home Assistant's value at the next refresh."""
        self._hold_until = 0.0

    def _modes(self) -> list:
        return [m for m in MODE_CYCLE if m in self.radiator.modes]

    def _render(self):
        r = self.radiator
        self.name_label.setText(r.name)

        if not r.available:
            info = "Injoignable"
        else:
            parts = []
            if r.current_temp is not None:
                parts.append(f"{format_temp(r.current_temp)} °C dans la pièce")
            if r.heating and self._mode != "off":
                parts.append("chauffe")
            info = "  ·  ".join(parts)
        self.info_label.setText(info)
        heating_now = r.available and r.heating and self._mode != "off"
        self.info_label.setStyleSheet(
            f"color: {THEME_COLORS[get_theme()]['heating_active']};"
            if heating_now else "")

        self.mode_btn.setText(MODE_LABELS.get(self._mode, self._mode or "—"))
        self.mode_btn.setEnabled(r.available and len(self._modes()) > 1)

        adjustable = (r.available and self._mode != "off"
                      and self._target is not None)
        self.target_label.setText(
            f"{format_temp(self._target)}°" if adjustable else "—")
        self.target_label.setEnabled(adjustable)
        self.minus_btn.setEnabled(adjustable and self._target > r.min_temp)
        self.plus_btn.setEnabled(adjustable and self._target < r.max_temp)

    # ── Touch actions ──

    def _step(self, direction: int):
        r = self.radiator
        if self._target is None:
            return
        step = r.step or 0.5
        # Land on a multiple of the step (19.3 + 0.5 -> 20.0, not 19.8).
        target = round((self._target + direction * step) / step) * step
        target = round(min(max(target, r.min_temp), r.max_temp), 2)
        if target == self._target:
            return
        self._target = target
        self._hold_until = time.monotonic() + self.HOLD_SECONDS
        self._render()
        self._temp_timer.start()

    def _cycle_mode(self):
        modes = self._modes()
        if len(modes) < 2:
            return
        i = modes.index(self._mode) if self._mode in modes else -1
        self._mode = modes[(i + 1) % len(modes)]
        if self._mode != "off" and self._target is None:
            # Show the target (and let it be adjusted) right away, instead
            # of waiting for the valve to report its own.
            r = self.radiator
            wanted = self._last_target if self._last_target is not None \
                else self.DEFAULT_TARGET
            self._target = min(max(wanted, r.min_temp), r.max_temp)
        self._hold_until = time.monotonic() + self.HOLD_SECONDS
        self._render()
        self._mode_timer.start()

    def _send_temperature(self):
        if self._target is not None and self._target != self.radiator.target_temp:
            self.temperature_requested.emit(self.radiator.entity_id, self._target)

    def _send_mode(self):
        # Cycling all the way round to the current mode sends nothing.
        if self._mode and self._mode != self.radiator.mode:
            # Coming back from "off" the valve picks its minimum: send the
            # target in the same command, after the mode.
            temperature = None
            if (self._mode == "heat" and self._target is not None
                    and self._target != self.radiator.target_temp):
                temperature = self._target
            self.mode_requested.emit(self.radiator.entity_id, self._mode,
                                     temperature)


class HeatingScreen(QWidget):
    """Radiator list, or what is still to be set up in Home Assistant."""

    back_to_home = pyqtSignal()
    refresh_requested = pyqtSignal()
    temperature_requested = pyqtSignal(str, float)
    mode_requested = pyqtSignal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = {}  # {entity_id: RadiatorCard}, in display order
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("headerBar")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 4, 12, 4)

        back_btn = QPushButton(Icons.BACK)
        back_btn.setObjectName("backBtn")
        back_btn.setFont(icon_font(22))
        back_btn.clicked.connect(self.back_to_home.emit)
        h_layout.addWidget(back_btn)

        title = QLabel("Chauffage")
        title.setObjectName("headerTitle")
        h_layout.addWidget(title, stretch=1)

        refresh_btn = QPushButton(Icons.REFRESH)
        refresh_btn.setObjectName("headerBtn")
        refresh_btn.setFont(icon_font(20))
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        h_layout.addWidget(refresh_btn)
        layout.addWidget(header)

        # Shown instead of the cards while there is nothing to control yet.
        self.message_label = QLabel("")
        self.message_label.setObjectName("heatingMessage")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label, stretch=1)

        self.scroll = make_touch_scroll_area()
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 6, 0, 6)
        self.scroll_layout.setSpacing(6)
        self.scroll_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, stretch=1)
        self.scroll.hide()

        status_bar = QFrame()
        status_bar.setObjectName("statusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(8, 0, 8, 0)
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        status_layout.addWidget(self.status_label, stretch=1)
        layout.addWidget(status_bar)

    # ── Display ──

    def show_loading(self):
        """Called when the screen opens, before the first answer."""
        if not self.cards:
            self._show_message("Chargement...")

    def set_state(self, status: str, radiators: list, detail: str = ""):
        """Apply the result of a HeatingWorker."""
        if status == ha.OK and radiators:
            self._show_radiators(radiators)
            self._set_status(f"Mis à jour à {datetime.now().strftime('%H:%M')}")
        elif status == ha.OK:
            self._show_message(
                "Aucun radiateur pour l'instant\n\n"
                "Ajoutez vos têtes thermostatiques dans Home Assistant,\n"
                "depuis un téléphone ou un ordinateur :\n"
                f"{ha.setup_url()}")
        elif status == ha.ONBOARDING:
            self._show_message(
                "Home Assistant est installé\n\n"
                "Créez votre compte depuis un téléphone\n"
                "ou un ordinateur connecté au même réseau :\n"
                f"{ha.setup_url()}")
        elif status == ha.AUTH:
            self._show_message(
                "Home Assistant refuse la connexion de l'écran\n\n"
                "Vérifiez dans son fichier configuration.yaml que les\n"
                "connexions locales (trusted_networks) sont autorisées.")
        elif self.cards:
            # A passing hiccup: keep the radiators on screen, flag it.
            self._set_status("Home Assistant ne répond pas", error=True)
        elif status == ha.UNREACHABLE:
            self._show_message(
                "Home Assistant ne répond pas encore\n\n"
                "Il lui faut quelques minutes après un redémarrage,\n"
                "et davantage juste après son installation.")
        else:
            self._show_message(f"Erreur Home Assistant\n\n{detail}".strip())

    def on_command_result(self, entity_id: str, success: bool):
        if success:
            return
        card = self.cards.get(entity_id)
        if card:
            card.release_hold()
        self._set_status("Commande non transmise au radiateur", error=True)

    def _show_message(self, text: str):
        self._clear_cards()
        self.message_label.setText(text)
        self.scroll.hide()
        self.message_label.show()
        self._set_status("")

    def _show_radiators(self, radiators: list):
        ids = [r.entity_id for r in radiators]
        if ids == list(self.cards):
            # Same radiators: update in place, so a finger on a button
            # (and a change not sent yet) survives the refresh.
            for radiator in radiators:
                self.cards[radiator.entity_id].update_from(radiator)
        else:
            self._clear_cards()
            for radiator in radiators:
                card = RadiatorCard(radiator)
                card.temperature_requested.connect(self.temperature_requested)
                card.mode_requested.connect(self.mode_requested)
                self.cards[radiator.entity_id] = card
                self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, card)
        self.message_label.hide()
        self.scroll.show()

    def _clear_cards(self):
        for card in self.cards.values():
            self.scroll_layout.removeWidget(card)
            card.deleteLater()
        self.cards = {}

    def _set_status(self, text: str, error: bool = False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {THEME_COLORS[get_theme()]['countdown_imminent']};"
            if error else "")
