"""Read-only tamed-animal browser using background world/base-isolated summaries."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGridLayout, QHeaderView, QLabel, QLineEdit,
    QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QHBoxLayout, QToolButton, QFrame,
)

from .item_icons import has_item_icon, item_icon
from .animal_widgets import AnimalCard
from .animal_map import AnimalMapPanel, owner_color


STANDARD_STATS = (
    ("health", "Health"), ("food", "Food"), ("water", "Water"),
    ("oxygen", "Oxygen"), ("stamina", "Stamina"), ("experience", "XP"),
)


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _text(value):
    if value is None or value == "":
        return "Unknown"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "Unknown"
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value) or "Unknown"
    return str(value)


def _quantity_text(value):
    return _text(value) if _number(value) else "Unknown"


def _time_text(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value.astimezone().strftime("%m/%d %H:%M:%S") if isinstance(value, datetime) else str(value or "")


def _table(columns, name):
    table = QTableWidget(0, len(columns))
    table.setAccessibleName(name)
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(38)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _put(table, row, column, value):
    entry = QTableWidgetItem(str(value))
    entry.setToolTip(str(value))
    table.setItem(row, column, entry)
    return entry


def _fit_detail_table(table, row_height=38):
    # The outer detail area scrolls, keeping every row reachable in a small window.
    table.resizeRowsToContents()
    for row in range(table.rowCount()):
        table.setRowHeight(row, max(row_height, table.rowHeight(row)))
    rows_height = sum(table.rowHeight(row) for row in range(table.rowCount())) or row_height
    height = table.horizontalHeader().height() + rows_height + table.horizontalScrollBar().sizeHint().height() + 4
    table.setMinimumHeight(height)
    table.setMaximumHeight(height)


class _SummarySignals(QObject):
    success = Signal(int, object)
    failure = Signal(int, str)


class _SummaryWorker(QRunnable):
    def __init__(self, generation, snapshot, base, radius, function, owner_profile_roots=None):
        super().__init__()
        self.generation, self.snapshot, self.base = generation, snapshot, base
        self.radius, self.function = radius, function
        self.owner_profile_roots = owner_profile_roots
        self.signals = _SummarySignals()

    def run(self):
        try:
            function = self.function
            if function is None:
                from .animals import summarize_animals
                summary = summarize_animals(self.snapshot, self.base, radius_m=self.radius, owner_profile_roots=self.owner_profile_roots)
            else:
                summary = function(self.snapshot, self.base, radius_m=self.radius)
            self.signals.success.emit(self.generation, {"summary": summary, "saved_at": _value(self.snapshot, "saved_at")})
        except Exception as exc:
            self.signals.failure.emit(self.generation, f"{type(exc).__name__}: {exc}")


class AnimalPanel(QWidget):
    """All owned animals by default; distance filters never imply ownership."""

    loaded = Signal(object)
    failed = Signal(str)
    radiusChanged = Signal(int)
    ownerSourcesRequested = Signal()

    def __init__(self, summary_function: Callable | None = None):
        super().__init__()
        self.summary = None
        self.snapshot = None
        self.base = None
        self.busy = False
        self.last_error = ""
        self._closed = False
        self._generation = 0
        self._context = None
        self._pending = False
        self._worker = None
        self._summary_function = summary_function
        self._selected_id = None
        self._row_animals = []
        self._map_catalog = None
        self.owner_profile_roots = None
        self.owner_columns = {}
        self.animal_cards = {}
        self.inventory_tables = []
        self.inventory_tiles = []
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(120)
        self.debounce.timeout.connect(self._start)
        self.live_refresh = QTimer(self)
        self.live_refresh.setInterval(30000)
        self.live_refresh.timeout.connect(self._refresh_visible)
        self.live_refresh.start()
        self._build_ui()

    def _refresh_visible(self):
        # The main resource map also consumes loaded; a hidden animal tab must
        # keep sampling the selected world without opening or controlling it.
        if (self.snapshot is not None and not _value(self.snapshot, "is_shared_map", False)
                and not _value(self.snapshot, "is_remote", False)
                and not self._closed and not self.busy and not self.debounce.isActive()):
            self._generation += 1
            self._start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        controls = QGridLayout()
        self.base_label = QLabel("Tamed animals · No save connected")
        self.base_label.setWordWrap(True)
        self.base_label.setStyleSheet("font-size:16px;font-weight:600;color:#f2c771")
        controls.addWidget(self.base_label, 0, 0, 1, 5)
        self.scope_combo = QComboBox()
        self.scope_combo.setAccessibleName("Tamed animal scope")
        for label, key in (("All tamed animals", "all"), ("Near home", "home"), ("Away", "away"), ("Position unknown", "unknown")):
            self.scope_combo.addItem(label, key)
        self.scope_combo.currentIndexChanged.connect(self._filter_requested)
        controls.addWidget(self.scope_combo, 1, 0)
        controls.addWidget(QLabel("Home radius (horizontal)"), 1, 1)
        self.radius_spin = QSpinBox()
        self.radius_spin.setAccessibleName("Tamed animal home radius")
        self.radius_spin.setRange(10, 5000)
        self.radius_spin.setSingleStep(10)
        self.radius_spin.setSuffix(" m")
        self.radius_spin.setValue(100)
        self.radius_spin.setMinimumWidth(96)
        self.radius_spin.valueChanged.connect(self._radius_changed)
        controls.addWidget(self.radius_spin, 1, 2)
        self.search_edit = QLineEdit()
        self.search_edit.setAccessibleName("Search tamed animals")
        self.search_edit.setPlaceholderText("Search name, species, or owner")
        self.search_edit.textChanged.connect(self._filter_requested)
        controls.addWidget(self.search_edit, 1, 3)
        controls.setColumnStretch(3, 1)
        self.owner_sources_button = QToolButton()
        self.owner_sources_button.setText("Owner data…")
        self.owner_sources_button.setToolTip("Connect a teammate backup folder containing Characters, Profile, and BestiaryData; read-only and verified by character identity.")
        self.owner_sources_button.clicked.connect(self.ownerSourcesRequested.emit)
        controls.addWidget(self.owner_sources_button, 1, 4)
        layout.addLayout(controls)
        self.status = QLabel("No host save connected; tamed animal status is unknown.")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.board_host = QWidget()
        board_layout = QVBoxLayout(self.board_host)
        board_layout.setContentsMargins(0, 0, 0, 0)
        self.board_scroll = QScrollArea()
        self.board_scroll.setAccessibleName("Animal cards grouped by owner")
        self.board_scroll.setWidgetResizable(True)
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_content = QWidget()
        self.board_layout = QHBoxLayout(self.board_content)
        self.board_layout.setContentsMargins(0, 0, 0, 0)
        self.board_layout.setSpacing(10)
        self.board_scroll.setWidget(self.board_content)
        board_layout.addWidget(self.board_scroll, 1)
        self.list_toggle = QToolButton()
        self.list_toggle.setText("Detailed list")
        self.list_toggle.setCheckable(True)
        self.list_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.list_toggle.setArrowType(Qt.ArrowType.RightArrow)
        board_layout.addWidget(self.list_toggle)
        self.table = _table(["Name", "Species", "Level", "Status", "Home / horizontal distance", "Owner"], "Tamed animal list")
        self.animal_table = self.table
        for column, width in enumerate((150, 130, 75, 150, 160, 120)):
            self.table.setColumnWidth(column, width)
        self.table.setMinimumHeight(115)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setMaximumHeight(220)
        self.table.hide()
        self.list_toggle.toggled.connect(lambda checked: self._toggle_section(self.list_toggle, self.table, checked))
        board_layout.addWidget(self.table)
        self.splitter.addWidget(self.board_host)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.animal_map = AnimalMapPanel()
        self.animal_map.setMinimumHeight(230)
        self.animal_map.animal_selected.connect(self._select_by_id)
        self.right_splitter.addWidget(self.animal_map)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setMinimumHeight(160)
        self.detail_content = QWidget()
        detail_layout = QVBoxLayout(self.detail_content)
        self.detail_title = QLabel("Select an animal to view its status, inventory, and equipment.")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size:16px;font-weight:600")
        detail_layout.addWidget(self.detail_title)
        self.stat_toggle = QToolButton()
        self.stat_toggle.setText("Full stat values")
        self.stat_toggle.setCheckable(True)
        self.stat_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.stat_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        detail_layout.addWidget(self.stat_toggle)
        self.stats_table = _table(["Status", "Current value / source", "Maximum / known baseline", "Notes"], "Animal stat details")
        for column, width in enumerate((100, 110, 110, 260)):
            self.stats_table.setColumnWidth(column, width)
        detail_layout.addWidget(self.stats_table)
        self.stats_table.hide()
        self.stat_toggle.toggled.connect(lambda checked: self._toggle_section(self.stat_toggle, self.stats_table, checked))
        self.inventory_host = QWidget()
        self.inventory_layout = QVBoxLayout(self.inventory_host)
        self.inventory_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.inventory_host)
        self.details_label = QLabel("Lineage, talents, and other details")
        self.details_label.setStyleSheet("font-size:15px;font-weight:600")
        self.details_toggle = QToolButton()
        self.details_toggle.setText("Lineage, talents, and other details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        detail_layout.addWidget(self.details_toggle)
        self.details_label.hide()
        self.details_table = _table(["Item", "Saved data"], "Animal lineage, talents, and other saved details")
        self.details_table.setColumnWidth(0, 140)
        self.details_table.setWordWrap(True)
        self.details_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.details_table.horizontalHeader().sectionResized.connect(self._resize_details)
        detail_layout.addWidget(self.details_table)
        self.details_toggle.toggled.connect(lambda checked: self._toggle_section(self.details_toggle, self.details_table, checked))
        self.animal_warnings = QLabel()
        self.animal_warnings.setWordWrap(True)
        self.animal_warnings.setObjectName("muted")
        detail_layout.addWidget(self.animal_warnings)
        detail_layout.addStretch(1)
        self.detail_scroll.setWidget(self.detail_content)
        self.right_splitter.addWidget(self.detail_scroll)
        self.right_splitter.setSizes([460, 300])
        self.right_splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.right_splitter)
        self.splitter.setSizes([720, 520])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        layout.addWidget(self.splitter, 1)
        self.note = QLabel("Game state and positions update every 30s · Hover for source/time · * Persistent maximum · ≈ Individual baseline · ↑ Above denominator · Hatched bars: unknown maximum")
        self.note.setToolTip("Health, food, water, oxygen, stamina, and positions may use verified game samples; each field shows its actual source and time.\n"
                             "Unreadable fields retain saved values. XP, inventory, and other data still come from the save.\n"
                             "Solid bars have a usable denominator; their appearance alone does not indicate a game sample. * means a known persistent maximum with temporary effects unverified; ≈ means an individual baseline.")
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        self._local_note = self.note.text()
        self._local_note_tooltip = self.note.toolTip()
        layout.addWidget(self.note)
        self._clear_detail()

    @staticmethod
    def _toggle_section(toggle, widget, checked):
        widget.setVisible(checked)
        toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def set_snapshot(self, snapshot, base, catalog=None):
        if self._closed:
            return
        if snapshot is None:
            self.clear()
            return
        if _value(snapshot, "is_shared_map", False):
            self.set_missing_source()
            return
        self.snapshot, self.base = snapshot, dict(base) if base else None
        self.owner_sources_button.setEnabled(not _value(snapshot, "is_remote", False))
        if _value(snapshot, "is_remote", False):
            self.note.setText("Public sync checks every 2 min · State, positions, and inventory come from the host save · ≈ Individual baseline · ↑ Above denominator · Hatched bars: unknown maximum")
            self.note.setToolTip("Only validated public host world saves are downloaded; local game processes and character files are not read. Save times and unknown states are retained.")
        else:
            self.note.setText(self._local_note)
            self.note.setToolTip(self._local_note_tooltip)
        if catalog is not None:
            self._map_catalog = catalog
        context = (_value(snapshot, "world_id"), tuple(self.base.get(axis) for axis in ("id", "x", "y", "z")) if self.base else None, self.radius_spin.value())
        changed = context != self._context
        self._context = context
        self._generation += 1
        self.last_error = ""
        if changed:
            self._clear_contents("Loading tamed animals for this world…")
        home = self.base.get("name", "Selected base") if self.base else "No base selected; distance unknown"
        self.base_label.setText(f"Tamed animals · {home}")
        self.status.setText("Updating tamed animals…")
        self.debounce.start()

    def _radius_changed(self, radius):
        self.radiusChanged.emit(radius)
        if self.snapshot is not None:
            self.set_snapshot(self.snapshot, self.base)
        else:
            self._filter_changed()

    def _start(self):
        if self._closed or self.snapshot is None or _value(self.snapshot, "is_shared_map", False):
            return
        if self.busy:
            self._pending = True
            return
        self.busy, self._pending = True, False
        base = dict(self.base) if self.base else None
        worker = _SummaryWorker(self._generation, self.snapshot, base, self.radius_spin.value(), self._summary_function, self.owner_profile_roots)
        self._worker = worker
        worker.signals.success.connect(self._success)
        worker.signals.failure.connect(self._failure)
        self.pool.start(worker)

    def _success(self, generation, payload):
        self.busy = False
        self._worker = None
        if self._closed:
            return
        if generation == self._generation:
            if self.set_summary(payload["summary"], saved_at=payload.get("saved_at")):
                self.last_error = ""
                self.loaded.emit(self.summary)
            else:
                self.failed.emit(self.last_error)
        self._restart_pending(generation)

    def _failure(self, generation, error):
        self.busy = False
        self._worker = None
        if self._closed:
            return
        if generation == self._generation:
            self.last_error = error
            self.status.setText("Animal update incomplete; displaying the last valid result for this world and base." if self.summary is not None else "Animals could not be read; refresh the save again later.")
            self.status.setToolTip(error)
            self.failed.emit(error)
        self._restart_pending(generation)

    def _restart_pending(self, generation):
        if (not self._closed and self.snapshot is not None and not _value(self.snapshot, "is_shared_map", False)
                and (self._pending or generation != self._generation)):
            self.debounce.start(0)

    def clear(self):
        self._generation += 1
        self._pending = False
        self.snapshot, self.base, self._context = None, None, None
        self.owner_profile_roots = None
        self.last_error = ""
        self.debounce.stop()
        self.base_label.setText("Tamed animals · No save connected")
        self._clear_contents("No host save connected; tamed animal status is unknown.")

    def set_missing_source(self):
        """Clear dynamic state and invalidate jobs when displaying the shared map."""
        self.clear()
        self.base_label.setText("Tamed animals · No host save connected")
        self.status.setText("No host save connected; tamed animals are unavailable. Click “Choose host save”.")

    def _clear_contents(self, message):
        self.summary = None
        self._selected_id = None
        self._row_animals = []
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        self._clear_board()
        self.animal_map.clear()
        self.status.setText(message)
        self.status.setToolTip("")
        self._clear_detail()

    def set_summary(self, summary, saved_at=None):
        """Apply a validated summary, retaining the last good same-context result."""
        if self._closed:
            return False
        coverage = summary.get("coverage", {}) if isinstance(summary, dict) else {}
        status = str(coverage.get("status", ""))
        if not isinstance(summary, dict) or not isinstance(summary.get("animals"), list) or status not in ("", "ok", "partial", "empty"):
            reason = str(coverage.get("reason") or "This save has no readable tamed animal data.")
            self.last_error = reason
            self.status.setText(f"{reason} Showing the last valid result." if self.summary is not None else reason)
            return False
        self.summary = summary
        self.last_error = ""
        animals = summary["animals"]
        total = coverage.get("total_tamed")
        text = f"Loaded {len(animals)} tamed animals"
        if _number(total) and total != len(animals):
            text += f" / source total: {_quantity_text(total)} animals"
        timestamp = _time_text(saved_at or coverage.get("snapshot_time"))
        if timestamp:
            text += f" · Save {timestamp}"
        if status == "partial":
            text += " · Some data needs verification"
        live = summary.get("live_stats", {})
        live_keys = {"health", "food", "water", "oxygen", "stamina"}
        live_fields = [stat for animal in animals for stat in animal.get("stats", [])
                       if stat.get("id") in live_keys and stat.get("maximum_status") == "live"]
        applied = sum(any(stat.get("id") in live_keys and stat.get("maximum_status") == "live"
                          for stat in animal.get("stats", [])) for animal in animals)
        if live.get("world_verified") is True and applied:
            text += f" · Game state {applied}/{len(animals)} animals, {len(live_fields)} fields {_time_text(live.get('sample_time'))}"
        elif live:
            text += " · Stats use saved data"
        live_animals = sum(animal.get("position_source") == "live" for animal in animals)
        live_players = sum(player.get("position_source") == "live" for player in summary.get("players", []))
        if live_animals or live_players:
            text += f" · Game positions: animals {live_animals} / players {live_players}"
        self._summary_status = text
        refresh_note = "Public sync checks every two minutes; all fields and positions come from the host save. The local game process is not read.\n" if _value(self.snapshot, "is_remote", False) else "The connected world updates every 30 seconds, including while this tab is hidden. Fields and positions may come from different samples; hover for sources and times.\n"
        self.status.setToolTip(refresh_note
                               + "Fields without game samples retain saved values; XP, inventory, talents, and lineage remain save data.\n"
                               + "\n".join(str(warning) for warning in coverage.get("warnings", []))
                               + ("\n" + str(live.get("reason")) if live.get("reason") else ""))
        self.animal_map.set_context(self.snapshot, summary, self.base, catalog=self._map_catalog)
        self._filter_changed()
        return True

    def _home_state(self, animal):
        distance = animal.get("distance_m")
        if _number(distance):
            return "home" if distance <= self.radius_spin.value() else "away"
        return "unknown"

    def _filter_requested(self, *_):
        self._filter_changed(fit=True)

    def _filter_changed(self, *_, fit=False):
        if self.summary is None:
            return
        scope = self.scope_combo.currentData()
        query = self.search_edit.text().strip().casefold()
        animals = []
        for animal in self.summary.get("animals", []):
            if scope != "all" and self._home_state(animal) != scope:
                continue
            haystack = " ".join(str(animal.get(key) or "") for key in ("name", "species", "species_zh", "species_id", "owner_name")).casefold()
            if query and query not in haystack:
                continue
            animals.append(animal)
        self._row_animals = animals
        self._rebuild_board(animals)
        self.animal_map.set_animals(animals, fit=fit)
        self.table.blockSignals(True)
        self.table.setRowCount(len(animals))
        for row, animal in enumerate(animals):
            distance = animal.get("distance_m")
            state = self._home_state(animal)
            home = f"{'Near home' if state == 'home' else 'Away'} · {_quantity_text(distance)} m" if state != "unknown" else "Position / base unknown"
            level = _quantity_text(animal.get("level"))
            values = (animal.get("name") or "Unnamed", animal.get("species") or "Unknown species", level,
                      animal.get("status_label") or "Unknown", home, animal.get("owner_name") or "Unknown")
            for column, value in enumerate(values):
                entry = _put(self.table, row, column, value)
                if column == 0:
                    entry.setData(Qt.ItemDataRole.UserRole, str(animal.get("id", "")))
                if column == 2 and animal.get("level_note"):
                    entry.setToolTip(str(animal["level_note"]))
        index = next((i for i, animal in enumerate(animals) if str(animal.get("id", "")) == self._selected_id), 0)
        self.table.clearSelection()
        if animals:
            self.table.selectRow(index)
        self.table.blockSignals(False)
        self._selection_changed()
        suffix = f" · Showing {len(animals)} animals" if len(animals) != len(self.summary["animals"]) else ""
        self.status.setText(self._summary_status + suffix)

    def _clear_board(self):
        self.owner_columns = {}
        self.animal_cards = {}
        while self.board_layout.count():
            item = self.board_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _rebuild_board(self, animals):
        previous_scroll = {key: column.findChild(QScrollArea).verticalScrollBar().value() for key, column in self.owner_columns.items()}
        self._clear_board()
        groups = {}
        for animal in animals:
            key = animal.get("owner_key") or animal.get("owner_name") or "unknown"
            groups.setdefault(key, []).append(animal)
        if not groups:
            empty = QLabel("No animals match the current filters" if self.summary else "No host save connected")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.board_layout.addWidget(empty)
        for key, members in groups.items():
            name = str(members[0].get("owner_name") or "Unknown owner")
            color = owner_color(key)
            column = QWidget()
            column.setMinimumWidth(265)
            column.setMaximumWidth(380)
            column.setAccessibleName(f"Owner {name}")
            lane = QVBoxLayout(column)
            lane.setContentsMargins(0, 0, 0, 0)
            lane.setSpacing(8)
            title = QLabel(f"●  {name}    {len(members)}")
            owner = members[0].get("owner_capacity", {})
            bonuses = owner.get("percent", {})
            known = owner.get("known_percent", {})
            lines = []
            for stat, label in (("health", "Health"), ("stamina", "Stamina")):
                value = bonuses.get(stat)
                lines.append(f"{label} +{max(0, value)}%" if isinstance(value, int) else f"{label} bonus unverified" + (f" (known {known[stat]}%)" if isinstance(known.get(stat), int) else ""))
            title.setToolTip(" · ".join(lines) + "\n" + str(owner.get("scope", "Owner character/account data is missing.")))
            title.setStyleSheet(f"color:{color};font-size:16px;font-weight:600;padding:10px;background:#1b2b38;border-radius:6px")
            lane.addWidget(title)
            live_count = sum(any(stat.get("id") == "health" and stat.get("maximum_status") == "live" for stat in animal.get("stats", [])) for animal in members)
            owner_note = QLabel("Maximum from game · Includes applied bonuses" if live_count == len(members) else " · ".join(lines))
            owner_note.setWordWrap(True)
            owner_note.setStyleSheet("color:#a7bdcf;font-size:11px")
            lane.addWidget(owner_note)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            host = QWidget()
            cards = QVBoxLayout(host)
            cards.setContentsMargins(0, 0, 2, 0)
            cards.setSpacing(9)
            for animal in members:
                card = AnimalCard(animal, color, self._home_state(animal))
                card.selected.connect(self._select_by_id)
                card.locate.connect(self._locate_animal)
                self.animal_cards[card.ident] = card
                cards.addWidget(card)
            cards.addStretch(1)
            scroll.setWidget(host)
            lane.addWidget(scroll, 1)
            self.owner_columns[key] = column
            self.board_layout.addWidget(column, 1)
            if key in previous_scroll:
                QTimer.singleShot(0, scroll, lambda s=scroll, v=previous_scroll[key]: s.verticalScrollBar().setValue(v))

    def _select_by_id(self, ident):
        row = next((i for i, animal in enumerate(self._row_animals) if str(animal.get("id", "")) == str(ident)), -1)
        if row >= 0:
            self.table.selectRow(row)
            self._selection_changed()
            card = self.animal_cards.get(str(ident))
            if card:
                column = card.parentWidget().parentWidget()
                scroll = column.parentWidget()
                if isinstance(scroll, QScrollArea):
                    scroll.ensureWidgetVisible(card, 0, 10)
                self.board_scroll.ensureWidgetVisible(card, 12, 0)

    def _locate_animal(self, ident):
        self._select_by_id(ident)
        self.animal_map.select_animal(ident, focus=True)

    def selected_animal(self):
        rows = self.table.selectionModel().selectedRows()
        row = rows[0].row() if rows else -1
        return self._row_animals[row] if 0 <= row < len(self._row_animals) else None

    def _selection_changed(self):
        animal = self.selected_animal()
        if animal is None:
            self._clear_detail("No tamed animals match the current filters.")
            return
        self._selected_id = str(animal.get("id", ""))
        for ident, card in self.animal_cards.items():
            card.set_selected(ident == self._selected_id)
        self.animal_map.select_animal(self._selected_id, focus=False)
        self.detail_title.setText(f"{animal.get('name') or 'Unnamed'} · {animal.get('species') or 'Unknown species'}")
        stats = [stat for stat in animal.get("stats", []) if isinstance(stat, dict)]
        present = {stat.get("id") for stat in stats}
        stats = stats + [{"id": key, "label": label, "value": None, "maximum": None, "detail": "No verifiable value in the save"} for key, label in STANDARD_STATS if key not in present]
        self.stats_table.setRowCount(len(stats))
        for row, stat in enumerate(stats):
            value = _text(stat.get("value"))
            unit = str(stat.get("unit") or "")
            if value != "Unknown" and unit:
                value += f" {unit}"
            maximum = _quantity_text(stat.get("maximum")) if _number(stat.get("maximum")) else "Not provided"
            if maximum != "Not provided" and stat.get("maximum_status") == "reconstructed":
                maximum = "≈ " + maximum
            elif maximum != "Not provided" and stat.get("maximum_status") == "persistent":
                maximum += "*"
            values = (stat.get("label") or stat.get("id") or "Unrecognized stat", value, maximum, stat.get("detail") or "")
            for column, text in enumerate(values):
                entry = _put(self.stats_table, row, column, text)
                if column == 0:
                    entry.setData(Qt.ItemDataRole.UserRole, stat.get("id"))
        _fit_detail_table(self.stats_table)
        details = [item for item in animal.get("details", []) if isinstance(item, dict)]
        self.details_table.setRowCount(len(details))
        for row, detail in enumerate(details):
            _put(self.details_table, row, 0, detail.get("label") or "Other details")
            _put(self.details_table, row, 1, _text(detail.get("value")))
        self.details_table.setVisible(bool(details) and self.details_toggle.isChecked())
        self.details_toggle.setVisible(bool(details))
        _fit_detail_table(self.details_table)
        self._show_inventories(animal)
        self.animal_warnings.setText("\n".join(str(warning) for warning in animal.get("warnings", [])))
        self.detail_scroll.verticalScrollBar().setValue(0)

    def _clear_inventories(self):
        self.inventory_tables = []
        self.inventory_tiles = []
        while self.inventory_layout.count():
            item = self.inventory_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def _show_inventories(self, animal):
        self._clear_inventories()
        title = QLabel("Inventory and equipment")
        title.setStyleSheet("font-size:15px;font-weight:600")
        self.inventory_layout.addWidget(title)
        inventories = animal.get("inventories", [])
        if not inventories:
            empty = animal.get("inventory_status") == "empty"
            message = "The save confirms the inventory/equipment is empty." if empty else "The save has no readable inventory/equipment; contents are unknown."
            self.inventory_layout.addWidget(QLabel(message))
            return
        for inventory in inventories:
            items = inventory.get("items")
            items = items if isinstance(items, list) else None
            state = inventory.get("status", "unavailable")
            label = str(inventory.get("label") or "Unrecognized storage slot")
            if items:
                label += f" · {len(items)} item stacks"
                if state not in ("ok", "empty"):
                    label += " · Some contents need verification"
            elif items == [] and state in ("ok", "empty"):
                label += " · Confirmed empty"
            else:
                label += " · Contents unknown; empty state unconfirmed"
            heading = QLabel(label)
            heading.setWordWrap(True)
            self.inventory_layout.addWidget(heading)
            if not items:
                continue
            tiles_host = QWidget()
            tiles = QGridLayout(tiles_host)
            tiles.setContentsMargins(0, 0, 0, 0)
            tiles.setSpacing(6)
            for index, item in enumerate(items):
                ident = str(item.get("id", item.get("item_id", "")) or "")
                tile = QToolButton()
                tile.setIcon(item_icon(ident))
                tile.setIconSize(QSize(44, 44))
                quantity = _quantity_text(item.get("quantity"))
                tile.setText("× " + (quantity if quantity != "Unknown" else "?"))
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                tile.setFixedSize(72, 72)
                name = str(item.get("name") or ident or "Unrecognized item")
                tooltip = f"{name}\nQuantity {quantity} · Slot {_text(item.get('slot'))}"
                if item.get("warnings"):
                    tooltip += "\n" + "\n".join(map(str, item["warnings"]))
                tile.setToolTip(tooltip)
                tile.setAccessibleName(tooltip)
                tile.setStyleSheet("QToolButton {background:#1b2f3e;border:1px solid #355164;border-radius:6px;color:#d9e6f0} QToolButton:hover {border-color:#87cfb4;background:#243c4b}")
                tiles.addWidget(tile, index // 4, index % 4)
                self.inventory_tiles.append(tile)
            tiles.setColumnStretch(4, 1)
            self.inventory_layout.addWidget(tiles_host)
            toggle = QToolButton()
            toggle.setText("Item names, quantities, and slots")
            toggle.setCheckable(True)
            toggle.setArrowType(Qt.ArrowType.RightArrow)
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.inventory_layout.addWidget(toggle)
            table = _table(["Item", "Quantity", "Saved slot"], label)
            table.setIconSize(QSize(32, 32))
            table.verticalHeader().setDefaultSectionSize(42)
            table.setColumnWidth(0, 260)
            table.setColumnWidth(1, 100)
            table.setColumnWidth(2, 100)
            table.setRowCount(len(items))
            for row, item in enumerate(items):
                ident = str(item.get("id", item.get("item_id", "")) or "")
                entry = _put(table, row, 0, item.get("name") or ident or "Unrecognized item")
                entry.setIcon(item_icon(ident))
                entry.setData(Qt.ItemDataRole.UserRole, ident)
                warnings = [str(warning) for warning in item.get("warnings", [])]
                if not has_item_icon(ident):
                    warnings.append("No icon is available for this item.")
                if warnings:
                    entry.setToolTip(entry.text() + "\n" + "\n".join(warnings))
                quantity = _put(table, row, 1, _quantity_text(item.get("quantity")))
                quantity.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                _put(table, row, 2, _text(item.get("slot")))
            _fit_detail_table(table, row_height=42)
            self.inventory_tables.append(table)
            self.inventory_layout.addWidget(table)
            table.hide()
            toggle.toggled.connect(lambda checked, t=table, b=toggle: self._toggle_section(b, t, checked))

    def _clear_detail(self, message="Select an animal to view its status, inventory, and equipment."):
        self.detail_title.setText(message)
        self.stats_table.setRowCount(0)
        _fit_detail_table(self.stats_table)
        self.details_table.setRowCount(0)
        self.details_table.hide()
        self.details_label.hide()
        self.details_toggle.hide()
        self.animal_warnings.clear()
        self._clear_inventories()

    def _resize_details(self, *_):
        if not self._closed and self.details_table.rowCount():
            _fit_detail_table(self.details_table)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._resize_details)

    def shutdown(self, wait_ms=16000):
        self._closed = True
        self._generation += 1
        self.debounce.stop()
        self.live_refresh.stop()
        self.pool.waitForDone(wait_ms)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
