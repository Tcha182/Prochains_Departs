"""Touch helpers shared by every screen."""

from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QEvent
from PyQt5.QtWidgets import (
    QFrame, QScrollArea, QScroller, QScrollerProperties, QApplication,
)


# ─── Touch helpers ───────────────────────────────────────────────────────────

def make_touch_scroll_area() -> QScrollArea:
    """Vertical-only scroll area with kinetic touch scrolling enabled."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    viewport = scroll.viewport()
    QScroller.grabGesture(viewport, QScroller.LeftMouseButtonGesture)
    scroller = QScroller.scroller(viewport)
    props = scroller.scrollerProperties()
    # Overshoot animations can leave the view stuck offset on slow devices,
    # and horizontal wobble fights the vertical-only layout.
    props.setScrollMetric(QScrollerProperties.HorizontalOvershootPolicy,
                          QScrollerProperties.OvershootAlwaysOff)
    props.setScrollMetric(QScrollerProperties.VerticalOvershootPolicy,
                          QScrollerProperties.OvershootAlwaysOff)
    # By default QScroller replays a withheld press into the child widget
    # after 0.25s, so resting a finger briefly before swiping presses the
    # item under it. Items in these scroll areas all activate on release,
    # so withhold the press for the whole gesture: a tap is replayed as
    # press+release on release, and a drag only ever scrolls.
    props.setScrollMetric(QScrollerProperties.MousePressEventDelay, 3600.0)
    scroller.setScrollerProperties(props)
    return scroll


class TappableFrame(QFrame):
    """Clickable frame safe to use inside a QScroller scroll area.

    Emits `tapped` on release only if the press didn't turn into a scroll
    drag — activating on press would fire while the user is trying to scroll.
    """

    tapped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._press_pos = None

    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        press_pos, self._press_pos = self._press_pos, None
        if press_pos is not None:
            moved = (event.pos() - press_pos).manhattanLength()
            if moved <= QApplication.startDragDistance() and self.rect().contains(event.pos()):
                self._flash()
                self.tapped.emit()
        event.accept()

    def event(self, event):
        # QScroller cancels a stolen press by ungrabbing the mouse.
        if event.type() == QEvent.UngrabMouse:
            self._press_pos = None
        return super().event(event)

    def _flash(self):
        """Brief highlight so a tap visibly registered (presses are withheld
        by the scroller, so there is no :pressed state to rely on)."""
        self.setProperty("tapFlash", True)
        self.style().unpolish(self)
        self.style().polish(self)
        QTimer.singleShot(150, self._unflash)

    def _unflash(self):
        try:
            self.setProperty("tapFlash", False)
            self.style().unpolish(self)
            self.style().polish(self)
        except RuntimeError:
            pass  # widget was deleted (page rebuilt) before the timer fired


# ─── DepartureCard ───────────────────────────────────────────────────────────
