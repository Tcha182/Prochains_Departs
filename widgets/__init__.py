"""UI widgets, one module per screen.

Re-exported here so callers keep using `from widgets import HomeScreen`.
"""

from widgets.common import TappableFrame, make_touch_scroll_area
from widgets.home import DepartureCard, FavouriteGroup, HomeScreen, SleepOverlay
from widgets.keyboard import VirtualKeyboard
from widgets.search import SearchScreen, TRANSPORT_MODES, TRANSPORT_MODE_LABELS
from widgets.settings import SettingsScreen

__all__ = [
    "TappableFrame", "make_touch_scroll_area",
    "DepartureCard", "FavouriteGroup", "HomeScreen", "SleepOverlay",
    "VirtualKeyboard", "SearchScreen", "TRANSPORT_MODES",
    "TRANSPORT_MODE_LABELS", "SettingsScreen",
]
