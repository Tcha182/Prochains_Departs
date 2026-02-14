"""Theme QSS stylesheets for 800x480 touchscreen display."""

import os

from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication

_current_theme = "dark"
_icon_font_family = ""


# ─── Material Icons ──────────────────────────────────────────────────────────

class Icons:
    """Material Icons codepoints (filled style)."""
    BUS = "\ue530"          # directions_bus
    METRO = "\ue56f"        # subway
    TRAM = "\ue571"         # tram
    TRAIN = "\ue570"        # train
    SETTINGS = "\ue8b8"     # settings
    REFRESH = "\ue5d5"      # refresh
    EDIT = "\ue3c9"         # edit
    ADD = "\ue145"          # add
    BACK = "\ue5c4"         # arrow_back
    CLOSE = "\ue5cd"        # close
    CHEVRON_RIGHT = "\ue5cc"  # chevron_right
    WIFI = "\ue63e"         # wifi
    LOCK = "\ue897"         # lock
    CHECK = "\ue5ca"        # check
    MOON = "\ue3a8"         # brightness_3


def load_icon_font():
    """Load the Material Icons font from the app directory."""
    global _icon_font_family
    font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MaterialIcons-Regular.ttf")
    if os.path.exists(font_path):
        font_id = QFontDatabase.addApplicationFont(font_path)
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                _icon_font_family = families[0]
    return _icon_font_family


def icon_font(size: int = 20) -> QFont:
    """Return a QFont for Material Icons at the given pixel size."""
    f = QFont(_icon_font_family, size)
    return f

# ─── Settings-screen selectors (shared across themes) ─────────────────────────

_SETTINGS_QSS_DARK = """
/* ── Settings screen ── */
#settingsRow {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 10px 14px;
    margin: 3px 8px;
    min-height: 48px;
}
#settingsLabel {
    color: #e6edf3;
    font-size: 15px;
    font-weight: bold;
}
#settingsValue {
    color: #8b949e;
    font-size: 14px;
}
#settingsInput {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #e6edf3;
    font-size: 15px;
    padding: 8px 12px;
    min-height: 36px;
}
#settingsInput:focus {
    border-color: #58a6ff;
}
#wifiItem {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 8px 14px;
    margin: 2px 8px;
    min-height: 40px;
}
#wifiSignal {
    color: #8b949e;
    font-size: 12px;
}
#saveBtn {
    background-color: #238636;
    border: none;
    border-radius: 8px;
    color: #ffffff;
    font-size: 15px;
    font-weight: bold;
    min-height: 44px;
    margin: 8px;
    padding: 0 20px;
}
#saveBtn:pressed {
    background-color: #2ea043;
}
"""

_SETTINGS_QSS_LIGHT = """
/* ── Settings screen ── */
#settingsRow {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 10px;
    padding: 10px 14px;
    margin: 3px 8px;
    min-height: 48px;
}
#settingsLabel {
    color: #1f2328;
    font-size: 15px;
    font-weight: bold;
}
#settingsValue {
    color: #656d76;
    font-size: 14px;
}
#settingsInput {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    color: #1f2328;
    font-size: 15px;
    padding: 8px 12px;
    min-height: 36px;
}
#settingsInput:focus {
    border-color: #0969da;
}
#wifiItem {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 10px;
    padding: 8px 14px;
    margin: 2px 8px;
    min-height: 40px;
}
#wifiSignal {
    color: #656d76;
    font-size: 12px;
}
#saveBtn {
    background-color: #1a7f37;
    border: none;
    border-radius: 8px;
    color: #ffffff;
    font-size: 15px;
    font-weight: bold;
    min-height: 44px;
    margin: 8px;
    padding: 0 20px;
}
#saveBtn:pressed {
    background-color: #218739;
}
"""

# ─── Dark theme ───────────────────────────────────────────────────────────────

DARK_THEME = """
QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: "Segoe UI", "Roboto", "Noto Sans", sans-serif;
    font-size: 14px;
}

/* ── Header bar ── */
#headerBar {
    background-color: transparent;
    border-bottom: 1px solid #30363d;
    min-height: 52px;
    max-height: 52px;
}
#headerTitle {
    color: #f0f6fc;
    font-size: 18px;
    font-weight: bold;
}
#headerBtn {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #8b949e;
    min-width: 44px;
    min-height: 44px;
    max-width: 44px;
    max-height: 44px;
    font-size: 20px;
    font-weight: bold;
}
#headerBtn:pressed {
    background-color: #30363d;
    color: #f0f6fc;
}

/* ── Status bar ── */
#statusBar {
    background-color: #0d1117;
    border-top: 1px solid #21262d;
    min-height: 24px;
    max-height: 24px;
    padding: 0 8px;
}
#statusLabel {
    color: #484f58;
    font-size: 11px;
}

/* ── Scroll area ── */
QScrollArea {
    border: none;
    background-color: transparent;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QScrollBar:vertical {
    background: #0d1117;
    width: 6px;
    margin: 0;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #30363d;
    min-height: 30px;
    border-radius: 3px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

/* ── Favourite group card ── */
#favouriteGroup {
    background-color: transparent;
    border: none;
    border-top: 1px solid #21262d;
    border-radius: 0;
    padding: 8px 8px 4px 8px;
    margin: 0 4px;
}
#groupHeader {
    color: #8b949e;
    font-size: 13px;
    font-weight: bold;
    padding: 0 0 4px 0;
}
#deleteBtn {
    background-color: #da3633;
    border: none;
    border-radius: 6px;
    color: white;
    min-width: 36px;
    min-height: 28px;
    max-width: 36px;
    max-height: 28px;
    font-size: 14px;
    font-weight: bold;
}
#deleteBtn:pressed {
    background-color: #b62324;
}

/* ── Departure card ── */
#departureCard {
    background-color: transparent;
    border-radius: 0;
    padding: 4px 8px;
    margin: 0;
}
#lineBadge {
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 15px;
    font-weight: bold;
    min-width: 48px;
    max-height: 28px;
    qproperty-alignment: AlignCenter;
}
#destinationLabel {
    color: #e6edf3;
    font-size: 13px;
    font-weight: bold;
}
#statusLabel2 {
    color: #484f58;
    font-size: 11px;
}
#countdownLabel {
    color: #e6edf3;
    font-size: 22px;
    font-weight: bold;
    qproperty-alignment: AlignRight;
}
#clockLabel {
    color: #484f58;
    font-size: 11px;
    qproperty-alignment: AlignRight;
}

/* ── Empty state ── */
#emptyLabel {
    color: #484f58;
    font-size: 16px;
    qproperty-alignment: AlignCenter;
}

/* ── No departure label ── */
#noDepartureLabel {
    color: #484f58;
    font-size: 12px;
    font-style: italic;
    padding: 4px 0;
}

/* ── Search screen ── */
#searchInput {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #e6edf3;
    font-size: 16px;
    padding: 10px 14px;
    min-height: 40px;
}
#searchInput:focus {
    border-color: #58a6ff;
}
#resultItem {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 10px 14px;
    margin: 3px 8px;
    min-height: 44px;
}
#resultTitle {
    color: #e6edf3;
    font-size: 14px;
    font-weight: bold;
}
#resultSubtitle {
    color: #8b949e;
    font-size: 12px;
}

/* ── Mode selection buttons ── */
#modeBtn {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 12px;
    color: #e6edf3;
    font-size: 16px;
    font-weight: bold;
    padding: 12px;
}
#modeBtn:pressed {
    background-color: #21262d;
}

/* ── Back / action buttons ── */
#backBtn {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #8b949e;
    min-width: 44px;
    min-height: 44px;
    max-width: 44px;
    max-height: 44px;
    font-size: 22px;
    font-weight: bold;
}
#backBtn:pressed {
    background-color: #30363d;
    color: #f0f6fc;
}
#addFavBtn {
    background-color: #238636;
    border: none;
    border-radius: 8px;
    color: #ffffff;
    font-size: 16px;
    font-weight: bold;
    min-height: 48px;
    margin: 8px;
    padding: 0 20px;
}
#addFavBtn:pressed {
    background-color: #2ea043;
}

/* ── Direction choice buttons ── */
#directionBtn {
    background-color: transparent;
    border: 1px solid #30363d;
    border-radius: 10px;
    color: #e6edf3;
    font-size: 16px;
    font-weight: bold;
    min-height: 56px;
    margin: 4px 8px;
    padding: 12px 18px;
    text-align: left;
}
#directionBtn:pressed {
    background-color: #161b22;
    border-color: #58a6ff;
}

/* ── Loading spinner label ── */
#loadingLabel {
    color: #8b949e;
    font-size: 14px;
    qproperty-alignment: AlignCenter;
}

/* ── Step title ── */
#stepTitle {
    color: #8b949e;
    font-size: 14px;
    font-weight: bold;
    padding: 4px 12px;
}
""" + _SETTINGS_QSS_DARK

# ─── Light theme ──────────────────────────────────────────────────────────────

LIGHT_THEME = """
QWidget {
    background-color: #ffffff;
    color: #1f2328;
    font-family: "Segoe UI", "Roboto", "Noto Sans", sans-serif;
    font-size: 14px;
}

/* ── Header bar ── */
#headerBar {
    background-color: transparent;
    border-bottom: 1px solid #d0d7de;
    min-height: 52px;
    max-height: 52px;
}
#headerTitle {
    color: #1f2328;
    font-size: 18px;
    font-weight: bold;
}
#headerBtn {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    color: #656d76;
    min-width: 44px;
    min-height: 44px;
    max-width: 44px;
    max-height: 44px;
    font-size: 20px;
    font-weight: bold;
}
#headerBtn:pressed {
    background-color: #d0d7de;
    color: #1f2328;
}

/* ── Status bar ── */
#statusBar {
    background-color: #ffffff;
    border-top: 1px solid #d0d7de;
    min-height: 24px;
    max-height: 24px;
    padding: 0 8px;
}
#statusLabel {
    color: #656d76;
    font-size: 11px;
}

/* ── Scroll area ── */
QScrollArea {
    border: none;
    background-color: transparent;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QScrollBar:vertical {
    background: #ffffff;
    width: 6px;
    margin: 0;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #d0d7de;
    min-height: 30px;
    border-radius: 3px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

/* ── Favourite group card ── */
#favouriteGroup {
    background-color: transparent;
    border: none;
    border-top: 1px solid #d0d7de;
    border-radius: 0;
    padding: 8px 8px 4px 8px;
    margin: 0 4px;
}
#groupHeader {
    color: #656d76;
    font-size: 13px;
    font-weight: bold;
    padding: 0 0 4px 0;
}
#deleteBtn {
    background-color: #cf222e;
    border: none;
    border-radius: 6px;
    color: white;
    min-width: 36px;
    min-height: 28px;
    max-width: 36px;
    max-height: 28px;
    font-size: 14px;
    font-weight: bold;
}
#deleteBtn:pressed {
    background-color: #a40e26;
}

/* ── Departure card ── */
#departureCard {
    background-color: transparent;
    border-radius: 0;
    padding: 4px 8px;
    margin: 0;
}
#lineBadge {
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 15px;
    font-weight: bold;
    min-width: 48px;
    max-height: 28px;
    qproperty-alignment: AlignCenter;
}
#destinationLabel {
    color: #1f2328;
    font-size: 13px;
    font-weight: bold;
}
#statusLabel2 {
    color: #656d76;
    font-size: 11px;
}
#countdownLabel {
    color: #1f2328;
    font-size: 22px;
    font-weight: bold;
    qproperty-alignment: AlignRight;
}
#clockLabel {
    color: #656d76;
    font-size: 11px;
    qproperty-alignment: AlignRight;
}

/* ── Empty state ── */
#emptyLabel {
    color: #656d76;
    font-size: 16px;
    qproperty-alignment: AlignCenter;
}

/* ── No departure label ── */
#noDepartureLabel {
    color: #656d76;
    font-size: 12px;
    font-style: italic;
    padding: 4px 0;
}

/* ── Search screen ── */
#searchInput {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    color: #1f2328;
    font-size: 16px;
    padding: 10px 14px;
    min-height: 40px;
}
#searchInput:focus {
    border-color: #0969da;
}
#resultItem {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 10px;
    padding: 10px 14px;
    margin: 3px 8px;
    min-height: 44px;
}
#resultTitle {
    color: #1f2328;
    font-size: 14px;
    font-weight: bold;
}
#resultSubtitle {
    color: #656d76;
    font-size: 12px;
}

/* ── Mode selection buttons ── */
#modeBtn {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 12px;
    color: #1f2328;
    font-size: 16px;
    font-weight: bold;
    padding: 12px;
}
#modeBtn:pressed {
    background-color: #f6f8fa;
}

/* ── Back / action buttons ── */
#backBtn {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    color: #656d76;
    min-width: 44px;
    min-height: 44px;
    max-width: 44px;
    max-height: 44px;
    font-size: 22px;
    font-weight: bold;
}
#backBtn:pressed {
    background-color: #d0d7de;
    color: #1f2328;
}
#addFavBtn {
    background-color: #1a7f37;
    border: none;
    border-radius: 8px;
    color: #ffffff;
    font-size: 16px;
    font-weight: bold;
    min-height: 48px;
    margin: 8px;
    padding: 0 20px;
}
#addFavBtn:pressed {
    background-color: #218739;
}

/* ── Direction choice buttons ── */
#directionBtn {
    background-color: transparent;
    border: 1px solid #d0d7de;
    border-radius: 10px;
    color: #1f2328;
    font-size: 16px;
    font-weight: bold;
    min-height: 56px;
    margin: 4px 8px;
    padding: 12px 18px;
    text-align: left;
}
#directionBtn:pressed {
    background-color: #f6f8fa;
    border-color: #0969da;
}

/* ── Loading spinner label ── */
#loadingLabel {
    color: #656d76;
    font-size: 14px;
    qproperty-alignment: AlignCenter;
}

/* ── Step title ── */
#stepTitle {
    color: #656d76;
    font-size: 14px;
    font-weight: bold;
    padding: 4px 12px;
}
""" + _SETTINGS_QSS_LIGHT

# ─── Theme colors for inline style overrides ─────────────────────────────────

THEME_COLORS = {
    "dark": {
        "countdown_normal": "#e6edf3",
        "countdown_imminent": "#d29922",
        "countdown_departed": "#f85149",
        "edit_active_bg": "#30363d",
        "edit_active_fg": "#f0f6fc",
    },
    "light": {
        "countdown_normal": "#1f2328",
        "countdown_imminent": "#9a6700",
        "countdown_departed": "#cf222e",
        "edit_active_bg": "#d0d7de",
        "edit_active_fg": "#1f2328",
    },
}


def get_theme() -> str:
    """Return the current theme name."""
    return _current_theme


def set_theme(name: str) -> None:
    """Apply a theme QSS to the application."""
    global _current_theme
    _current_theme = name
    app = QApplication.instance()
    if app:
        qss = DARK_THEME if name == "dark" else LIGHT_THEME
        app.setStyleSheet(qss)
