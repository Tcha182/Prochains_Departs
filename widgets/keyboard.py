"""On-screen AZERTY keyboard."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton


class VirtualKeyboard(QFrame):
    """On-screen AZERTY keyboard for touchscreen input.

    Two layers (letters / accents+symbols), each with a shift variant.
    All layouts share the same shape so buttons are relabelled in place.
    """

    KEY_ACCENTS = "\u00e9\u00e0"  # layer-toggle label: "\u00e9\u00e0"
    KEY_LETTERS = "abc"

    ROWS_LOWER = [
        list("1234567890"),
        list("azertyuiop"),
        list("qsdfghjklm"),
        ["\u21e7", "w", "x", "c", "v", "b", "n", "\u232b"],
        ["\u2423", "@", "-", ".", "#", KEY_ACCENTS, "OK"],
    ]
    ROWS_UPPER = [
        list("!\"#$%&*()_"),
        list("AZERTYUIOP"),
        list("QSDFGHJKLM"),
        ["\u21e7", "W", "X", "C", "V", "B", "N", "\u232b"],
        ["\u2423", "@", "-", ".", "#", KEY_ACCENTS, "OK"],
    ]
    ROWS_ACCENT = [
        list("\u00e9\u00e8\u00ea\u00eb\u00e0\u00e2\u00e7\u00f9\u00fb\u00fc"),  # \u00e9 \u00e8 \u00ea \u00eb \u00e0 \u00e2 \u00e7 \u00f9 \u00fb \u00fc
        list("\u00ee\u00ef\u00f4\u00f6\u0153!?'\"&"),                          # \u00ee \u00ef \u00f4 \u00f6 \u0153 ! ? ' " &
        list("()[]{}<>%="),
        ["\u21e7", "_", "/", ":", ";", "*", "~", "\u232b"],
        ["\u2423", "@", "-", ".", "#", KEY_LETTERS, "OK"],
    ]
    ROWS_ACCENT_UPPER = [
        list("\u00c9\u00c8\u00ca\u00cb\u00c0\u00c2\u00c7\u00d9\u00db\u00dc"),  # \u00c9 \u00c8 \u00ca \u00cb \u00c0 \u00c2 \u00c7 \u00d9 \u00db \u00dc
        list("\u00ce\u00cf\u00d4\u00d6\u0152!?'\"&"),                          # \u00ce \u00cf \u00d4 \u00d6 \u0152 ! ? ' " &
        list("()[]{}<>%="),
        ["\u21e7", "_", "/", ":", ";", "*", "~", "\u232b"],
        ["\u2423", "@", "-", ".", "#", KEY_LETTERS, "OK"],
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("virtualKeyboard")
        self._target = None
        self._shifted = False
        self._accent = False
        self._key_buttons = []
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        for row_keys in self.ROWS_LOWER:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(3)
            for key in row_keys:
                btn = QPushButton(self._display_text(key))
                btn.setObjectName("kbKey")
                btn.setFocusPolicy(Qt.NoFocus)
                btn.setMinimumHeight(38)

                if key == "\u2423":  # space
                    btn.setMinimumWidth(180)
                elif key == "\u21e7":  # shift
                    btn.setMinimumWidth(56)
                elif key == "\u232b":  # backspace
                    btn.setMinimumWidth(56)
                elif key == "OK":
                    btn.setMinimumWidth(72)
                    btn.setObjectName("kbKeyOk")

                # Look up the key at click time so shift can swap layouts
                # without rewiring signal connections.
                index = len(self._key_buttons)
                btn.clicked.connect(
                    lambda checked, i=index: self._on_key(self._key_buttons[i][0])
                )
                row_layout.addWidget(btn)
                self._key_buttons.append((key, btn))

            layout.addLayout(row_layout)

    def _display_text(self, key):
        if key == "\u2423":
            return "espace"
        if key == "\u21e7":
            return "\u21e7"
        if key == "\u232b":
            return "\u232b"
        return key

    def set_target(self, widget):
        self._target = widget
        # Fresh field, fresh layer
        if self._accent or self._shifted:
            self._accent = False
            self._shifted = False
            self._update_layout()

    def _on_key(self, key):
        if not self._target:
            return

        if key == "\u21e7":  # shift
            self._shifted = not self._shifted
            self._update_layout()
        elif key == "\u232b":  # backspace
            self._target.backspace()
        elif key == "OK":
            self.hide()
        elif key == "\u2423":  # space
            self._target.insert(" ")
        elif key == self.KEY_ACCENTS:
            self._accent = True
            self._shifted = False
            self._update_layout()
        elif key == self.KEY_LETTERS:
            self._accent = False
            self._shifted = False
            self._update_layout()
        else:
            char = key.upper() if self._shifted and key.isalpha() else key
            self._target.insert(char)
            if self._shifted:
                self._shifted = False
                self._update_layout()

    def _current_rows(self):
        if self._accent:
            return self.ROWS_ACCENT_UPPER if self._shifted else self.ROWS_ACCENT
        return self.ROWS_UPPER if self._shifted else self.ROWS_LOWER

    def _update_layout(self):
        flat = [k for row in self._current_rows() for k in row]
        for i, (_orig_key, btn) in enumerate(self._key_buttons):
            if i < len(flat):
                new_key = flat[i]
                btn.setText(self._display_text(new_key))
                self._key_buttons[i] = (new_key, btn)
