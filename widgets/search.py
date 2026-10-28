"""Search screen: by stop name, or by mode then line, then direction."""

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QStackedWidget, QGridLayout,
)

from models import Favourite, LineAtStop, StopOnLine, normalize, is_same_place
from styles import Icons, icon_font
from widgets.common import TappableFrame, make_touch_scroll_area


TRANSPORT_MODES = [
    ("Bus", "bus", Icons.BUS),
    ("Metro", "metro", Icons.METRO),
    ("Tramway", "tram", Icons.TRAM),
    ("Train / RER", "rail", Icons.TRAIN),
]

TRANSPORT_MODE_LABELS = {mode: label for label, mode, _icon in TRANSPORT_MODES}

class SearchScreen(QWidget):
    """Two flows to add a favourite:

    line-first:  transport type (0) -> line (1) -> stop (2) -> direction (3)
    stop-first:  stop name search (0) -> stops (4) -> lines at stop (5) -> direction (3)
    """

    favourite_added = pyqtSignal(object)  # emits Favourite
    back_to_home = pyqtSignal()

    # Signals requesting API calls from main window
    line_search_requested = pyqtSignal(str, str)          # query, mode
    stops_on_line_requested = pyqtSignal(str)              # route_id
    resolve_and_probe_requested = pyqtSignal(str, str)     # stop_id, line_id
    stop_area_search_requested = pyqtSignal(str, int)      # query, search_id
    line_details_requested = pyqtSignal(list)              # [line_id, ...]

    # Fallback back-navigation when a page was reached without _navigate()
    _BACK_FALLBACK = {1: 0, 2: 1, 3: 2, 4: 0, 5: 4}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_mode = ""     # transportmode API value
        self.selected_line = None   # LineAtStop
        self.selected_stop = None   # StopOnLine
        self._all_stops = []        # full stop list for filtering
        self._selected_stop_area = None  # StopAreaMatch (stop-first flow)
        self._resolved_stop_area_id = ""
        self._resolved_stop_name = ""
        self._nav_history = []      # page indexes for the back button
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(600)
        self._debounce_timer.timeout.connect(self._do_search)
        self._search_id = 0  # incremented on each search to ignore stale results
        self._stop_debounce_timer = QTimer()
        self._stop_debounce_timer.setSingleShot(True)
        self._stop_debounce_timer.setInterval(600)
        self._stop_debounce_timer.timeout.connect(self._do_stop_search)
        self._stop_search_id = 0
        self._had_error = False  # set by show_error, consumed by the next results handler
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self._build_mode_page()             # page 0
        self._build_line_search_page()      # page 1
        self._build_stop_selection_page()   # page 2
        self._build_direction_page()        # page 3
        self._build_stop_search_page()      # page 4
        self._build_lines_at_stop_page()    # page 5

    def _navigate(self, index: int):
        if index == self.stack.currentIndex():
            return
        self._nav_history.append(self.stack.currentIndex())
        self.stack.setCurrentIndex(index)

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
        elif self._nav_history:
            self.stack.setCurrentIndex(self._nav_history.pop())
        else:
            self.stack.setCurrentIndex(self._BACK_FALLBACK.get(idx, 0))

    # ── Step 1: Transport type ──

    def _build_mode_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Ajouter un arret", layout)

        # Stop-first flow: search a stop by name
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(8, 8, 8, 0)
        self.stop_search_input = QLineEdit()
        self.stop_search_input.setObjectName("searchInput")
        self.stop_search_input.setPlaceholderText("Chercher un arret par nom...")
        self.stop_search_input.textChanged.connect(self._on_stop_search_text_changed)
        input_layout.addWidget(self.stop_search_input)
        layout.addWidget(input_container)

        # Line-first flow: pick a transport type
        mode_title = QLabel("ou choisir une ligne")
        mode_title.setObjectName("stepTitle")
        layout.addWidget(mode_title)

        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(16, 4, 16, 16)
        grid.setSpacing(12)

        for i, (label, mode_val, icon_char) in enumerate(TRANSPORT_MODES):
            card = TappableFrame()
            card.setObjectName("modeBtn")
            card.setMinimumHeight(90)
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

            card.tapped.connect(lambda m=mode_val: self._on_mode_selected(m))
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
        self._navigate(1)
        # Load all lines for this mode immediately (new id: any typed search
        # still in flight is now stale)
        self._search_id += 1
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

        scroll = make_touch_scroll_area()
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
        self._debounce_timer.start()

    def _do_search(self):
        self._search_id += 1
        query = self.search_input.text().strip()
        self.line_loading.setText("Recherche...")
        self.line_search_requested.emit(query, self.selected_mode)

    def on_line_results(self, lines: list, search_id: int = 0):
        """Called when line search results arrive."""
        # Ignore stale results from earlier searches
        if search_id and search_id != self._search_id:
            return
        if self._had_error:
            self._had_error = False
            return
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
            self.line_results_layout.addWidget(
                self._make_line_row(line, self._on_line_selected))

        self.line_results_layout.addStretch()

    def _make_line_row(self, line: LineAtStop, on_tap):
        """Result row with a coloured line badge."""
        row = TappableFrame()
        row.setObjectName("resultItem")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 6, 10, 6)

        badge = QLabel(line.line_name)
        badge.setFixedWidth(60)
        badge.setAlignment(Qt.AlignCenter)
        bg = line.line_color or "FFFFFF"
        fg = line.line_text_color or "000000"
        badge.setStyleSheet(
            f"background-color: #{bg}; color: #{fg}; "
            f"border-radius: 6px; padding: 4px 8px; font-weight: bold; font-size: 14px;"
        )
        row_layout.addWidget(badge)

        name_label = QLabel(f"{line.line_name} - {line.mode}" if line.mode else line.line_name)
        name_label.setObjectName("resultTitle")
        row_layout.addWidget(name_label, stretch=1)

        row.tapped.connect(lambda l=line: on_tap(l))
        return row

    def _on_line_selected(self, line: LineAtStop):
        self.selected_line = line
        self.stop_loading.setText("Chargement des arrets...")
        self._all_stops = []
        self.stop_filter_input.clear()
        self._clear_layout(self.stop_results_layout)
        self.stop_step_title.setText(f"Arrets - {line.line_name}")
        self.stops_on_line_requested.emit(line.route_id)
        self._navigate(2)

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

        scroll = make_touch_scroll_area()
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
        if self._had_error:
            self._had_error = False
            return
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
        self._start_direction_probe()

    def _start_direction_probe(self):
        """Shared final step of both flows: resolve the stop and probe directions."""
        stop = self.selected_stop
        self._resolved_stop_area_id = ""
        self._resolved_stop_name = ""
        self.dir_loading.setText("Chargement des directions...")
        self._clear_layout(self.dir_results_layout)
        self.dir_step_title.setText(
            f"{stop.stop_name} - {self.selected_line.line_name}"
            if self.selected_line else stop.stop_name
        )
        self.resolve_and_probe_requested.emit(stop.stop_id, self.selected_line.line_id)
        self._navigate(3)

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

        scroll = make_touch_scroll_area()
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
        if self._had_error:
            self._had_error = False
            return
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

    # ── Stop-first flow: pages 4 and 5 ──

    def _build_stop_search_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Choisir un arret", layout)

        self.stop_search_loading = QLabel("")
        self.stop_search_loading.setObjectName("loadingLabel")
        layout.addWidget(self.stop_search_loading)

        scroll = make_touch_scroll_area()
        self.stop_search_results_widget = QWidget()
        self.stop_search_results_layout = QVBoxLayout(self.stop_search_results_widget)
        self.stop_search_results_layout.setContentsMargins(0, 0, 0, 0)
        self.stop_search_results_layout.setSpacing(2)
        self.stop_search_results_layout.addStretch()
        scroll.setWidget(self.stop_search_results_widget)
        layout.addWidget(scroll, stretch=1)

        self.stack.addWidget(page)

    def _build_lines_at_stop_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._make_header("Choisir une ligne", layout)

        self.lines_at_stop_title = QLabel("")
        self.lines_at_stop_title.setObjectName("stepTitle")
        layout.addWidget(self.lines_at_stop_title)

        self.lines_at_stop_loading = QLabel("")
        self.lines_at_stop_loading.setObjectName("loadingLabel")
        layout.addWidget(self.lines_at_stop_loading)

        scroll = make_touch_scroll_area()
        self.lines_at_stop_widget = QWidget()
        self.lines_at_stop_layout = QVBoxLayout(self.lines_at_stop_widget)
        self.lines_at_stop_layout.setContentsMargins(0, 0, 0, 0)
        self.lines_at_stop_layout.setSpacing(2)
        self.lines_at_stop_layout.addStretch()
        scroll.setWidget(self.lines_at_stop_widget)
        layout.addWidget(scroll, stretch=1)

        self.stack.addWidget(page)

    def _on_stop_search_text_changed(self, text):
        self._stop_debounce_timer.stop()
        if len(text.strip()) >= 2:
            self._stop_debounce_timer.start()

    def _do_stop_search(self):
        query = self.stop_search_input.text().strip()
        if len(query) < 2:
            return
        self._stop_search_id += 1
        self.stop_search_loading.setText("Recherche...")
        self._clear_layout(self.stop_search_results_layout)
        self._navigate(4)
        self.stop_area_search_requested.emit(query, self._stop_search_id)

    def on_stop_area_results(self, matches: list, search_id: int = 0):
        """Called when stop-name search results arrive."""
        if search_id and search_id != self._stop_search_id:
            return
        if self._had_error:
            self._had_error = False
            return
        self.stop_search_loading.setText("")
        self._clear_layout(self.stop_search_results_layout)

        if not matches:
            lbl = QLabel("Aucun arret trouve")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.stop_search_results_layout.addWidget(lbl)
            self.stop_search_results_layout.addStretch()
            return

        for match in matches:
            item = self._make_result_item(
                match.stop_name, match.town,
                lambda checked, m=match: self._on_stop_area_selected(m),
            )
            self.stop_search_results_layout.addWidget(item)
        self.stop_search_results_layout.addStretch()

    def _on_stop_area_selected(self, match):
        self._selected_stop_area = match
        self.lines_at_stop_title.setText(
            f"{match.stop_name} ({match.town})" if match.town else match.stop_name)
        self.lines_at_stop_loading.setText("Chargement des lignes...")
        self._clear_layout(self.lines_at_stop_layout)
        self._navigate(5)
        self.line_details_requested.emit(list(match.routes.keys()))

    def on_lines_at_stop_results(self, lines: list):
        """Called when line details for the selected stop arrive."""
        if self._had_error:
            self._had_error = False
            return
        self.lines_at_stop_loading.setText("")
        self._clear_layout(self.lines_at_stop_layout)

        if not lines:
            lbl = QLabel("Aucune ligne trouvee")
            lbl.setObjectName("noDepartureLabel")
            lbl.setAlignment(Qt.AlignCenter)
            self.lines_at_stop_layout.addWidget(lbl)
            self.lines_at_stop_layout.addStretch()
            return

        for line in lines:
            self.lines_at_stop_layout.addWidget(
                self._make_line_row(line, self._on_line_at_stop_selected))
        self.lines_at_stop_layout.addStretch()

    def _on_line_at_stop_selected(self, line: LineAtStop):
        area = self._selected_stop_area
        if area is None:
            return
        self.selected_line = line
        self.selected_stop = StopOnLine(
            stop_name=area.stop_name,
            stop_id=area.routes.get(line.line_id, ""),
        )
        self._start_direction_probe()

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

    def show_error(self, msg: str):
        """Show a worker error on the loading label of the current step.

        The paired `finished` signal always follows (workers must emit it to
        let the QThread quit), which would otherwise immediately overwrite
        this message with an empty-results placeholder. `_had_error` tells
        the next results handler to leave the error message alone instead.
        """
        labels = {
            1: self.line_loading,
            2: self.stop_loading,
            3: self.dir_loading,
            4: self.stop_search_loading,
            5: self.lines_at_stop_loading,
        }
        label = labels.get(self.stack.currentIndex())
        if label:
            label.setText(msg)
        self._had_error = True

    def _make_result_item(self, title, subtitle, on_click):
        """Create a clickable result item frame."""
        frame = TappableFrame()
        frame.setObjectName("resultItem")
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

        frame.tapped.connect(lambda: on_click(True))
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
        self._selected_stop_area = None
        self._resolved_stop_area_id = ""
        self._resolved_stop_name = ""
        self._nav_history.clear()
        self._had_error = False
        self._debounce_timer.stop()
        self._stop_debounce_timer.stop()
        self.search_input.clear()
        self.stop_filter_input.clear()
        self.stop_search_input.clear()
        self._clear_layout(self.line_results_layout)
        self._clear_layout(self.stop_results_layout)
        self._clear_layout(self.dir_results_layout)
        self._clear_layout(self.stop_search_results_layout)
        self._clear_layout(self.lines_at_stop_layout)
        self.line_loading.setText("")
        self.stop_loading.setText("")
        self.dir_loading.setText("")
        self.stop_search_loading.setText("")
        self.lines_at_stop_loading.setText("")
        self.stack.setCurrentIndex(0)


# ─── Virtual Keyboard ──────────────────────────────────────────────────────
