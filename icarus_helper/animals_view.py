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
    ("health", "生命"), ("food", "食物"), ("water", "水分"),
    ("oxygen", "氧氣"), ("stamina", "耐力"), ("experience", "經驗"),
)


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _text(value):
    if value is None or value == "":
        return "未知"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "未知"
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return "、".join(_text(item) for item in value) or "未知"
    return str(value)


def _quantity_text(value):
    return _text(value) if _number(value) else "未知"


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
        if self.isVisible() and self.snapshot is not None and not self._closed and not self.busy and not self.debounce.isActive():
            self._generation += 1
            self._start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        controls = QGridLayout()
        self.base_label = QLabel("馴養動物 · 尚未連接存檔")
        self.base_label.setWordWrap(True)
        self.base_label.setStyleSheet("font-size:16px;font-weight:600;color:#f2c771")
        controls.addWidget(self.base_label, 0, 0, 1, 5)
        self.scope_combo = QComboBox()
        self.scope_combo.setAccessibleName("馴養動物範圍")
        for label, key in (("全部馴養動物", "all"), ("家附近", "home"), ("離家", "away"), ("位置未知", "unknown")):
            self.scope_combo.addItem(label, key)
        self.scope_combo.currentIndexChanged.connect(self._filter_requested)
        controls.addWidget(self.scope_combo, 1, 0)
        controls.addWidget(QLabel("家附近水平半徑"), 1, 1)
        self.radius_spin = QSpinBox()
        self.radius_spin.setAccessibleName("馴養動物家附近半徑")
        self.radius_spin.setRange(10, 5000)
        self.radius_spin.setSingleStep(10)
        self.radius_spin.setSuffix(" m")
        self.radius_spin.setValue(100)
        self.radius_spin.setMinimumWidth(96)
        self.radius_spin.valueChanged.connect(self._radius_changed)
        controls.addWidget(self.radius_spin, 1, 2)
        self.search_edit = QLineEdit()
        self.search_edit.setAccessibleName("搜尋馴養動物")
        self.search_edit.setPlaceholderText("搜尋名稱、物種或主人")
        self.search_edit.textChanged.connect(self._filter_requested)
        controls.addWidget(self.search_edit, 1, 3)
        controls.setColumnStretch(3, 1)
        self.owner_sources_button = QToolButton()
        self.owner_sources_button.setText("飼養者資料…")
        self.owner_sources_button.setToolTip("連接隊友的 Characters、Profile 與 BestiaryData 備份資料夾；僅讀取，依角色識別核對。")
        self.owner_sources_button.clicked.connect(self.ownerSourcesRequested.emit)
        controls.addWidget(self.owner_sources_button, 1, 4)
        layout.addLayout(controls)
        self.status = QLabel("尚未連接主機存檔，馴養動物狀態未知。")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.board_host = QWidget()
        board_layout = QVBoxLayout(self.board_host)
        board_layout.setContentsMargins(0, 0, 0, 0)
        self.board_scroll = QScrollArea()
        self.board_scroll.setAccessibleName("依飼養者分欄的動物卡片")
        self.board_scroll.setWidgetResizable(True)
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_content = QWidget()
        self.board_layout = QHBoxLayout(self.board_content)
        self.board_layout.setContentsMargins(0, 0, 0, 0)
        self.board_layout.setSpacing(10)
        self.board_scroll.setWidget(self.board_content)
        board_layout.addWidget(self.board_scroll, 1)
        self.list_toggle = QToolButton()
        self.list_toggle.setText("清單數值明細")
        self.list_toggle.setCheckable(True)
        self.list_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.list_toggle.setArrowType(Qt.ArrowType.RightArrow)
        board_layout.addWidget(self.list_toggle)
        self.table = _table(["名稱", "物種", "等級", "狀態", "家／水平距離", "主人"], "馴養動物清單")
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
        self.detail_title = QLabel("選取一隻動物查看狀態與背包／裝備。")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size:16px;font-weight:600")
        detail_layout.addWidget(self.detail_title)
        self.stat_toggle = QToolButton()
        self.stat_toggle.setText("完整狀態數值")
        self.stat_toggle.setCheckable(True)
        self.stat_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.stat_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        detail_layout.addWidget(self.stat_toggle)
        self.stats_table = _table(["狀態", "目前值／來源", "上限／已知基準", "說明"], "動物詳細狀態")
        for column, width in enumerate((100, 110, 110, 260)):
            self.stats_table.setColumnWidth(column, width)
        detail_layout.addWidget(self.stats_table)
        self.stats_table.hide()
        self.stat_toggle.toggled.connect(lambda checked: self._toggle_section(self.stat_toggle, self.stats_table, checked))
        self.inventory_host = QWidget()
        self.inventory_layout = QVBoxLayout(self.inventory_host)
        self.inventory_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.inventory_host)
        self.details_label = QLabel("血統、天賦與其他資訊")
        self.details_label.setStyleSheet("font-size:15px;font-weight:600")
        self.details_toggle = QToolButton()
        self.details_toggle.setText("血統、天賦與其他資訊")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        detail_layout.addWidget(self.details_toggle)
        self.details_label.hide()
        self.details_table = _table(["項目", "存檔內容"], "動物血統、天賦與其他存檔資訊")
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
        self.note = QLabel("生命／耐力實心條＝遊戲實際值 · *＝已計飼養者的持續上限，臨時效果待核對 · ≈＝未含完整飼養者加成 · ↑＝超出已知上限 · 斜紋＝資料不足")
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
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
        self.snapshot, self.base = snapshot, dict(base) if base else None
        if catalog is not None:
            self._map_catalog = catalog
        context = (_value(snapshot, "world_id"), tuple(self.base.get(axis) for axis in ("id", "x", "y", "z")) if self.base else None, self.radius_spin.value())
        changed = context != self._context
        self._context = context
        self._generation += 1
        self.last_error = ""
        if changed:
            self._clear_contents("正在讀取這個世界的馴養動物…")
        home = self.base.get("name", "所選基地") if self.base else "尚未指定基地，距離未知"
        self.base_label.setText(f"馴養動物 · {home}")
        self.status.setText("正在更新馴養動物…")
        self.debounce.start()

    def _radius_changed(self, radius):
        self.radiusChanged.emit(radius)
        if self.snapshot is not None:
            self.set_snapshot(self.snapshot, self.base)
        else:
            self._filter_changed()

    def _start(self):
        if self._closed or self.snapshot is None:
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
            self.status.setText("動物更新未完成，顯示同一世界與基地上次成功資料。" if self.summary is not None else "動物暫時無法讀取，請稍後重新整理存檔。")
            self.status.setToolTip(error)
            self.failed.emit(error)
        self._restart_pending(generation)

    def _restart_pending(self, generation):
        if self.snapshot is not None and (self._pending or generation != self._generation):
            self.debounce.start(0)

    def clear(self):
        self._generation += 1
        self._pending = False
        self.snapshot, self.base, self._context = None, None, None
        self.owner_profile_roots = None
        self.last_error = ""
        self.debounce.stop()
        self.base_label.setText("馴養動物 · 尚未連接存檔")
        self._clear_contents("尚未連接主機存檔，馴養動物狀態未知。")

    def set_missing_source(self):
        """Clear dynamic state and invalidate jobs when displaying the shared map."""
        self.clear()
        self.base_label.setText("馴養動物 · 未連接主機存檔")
        self.status.setText("尚未連接主機存檔，無法取得馴養動物；請按「選擇主機存檔」。")

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
            reason = str(coverage.get("reason") or "目前存檔沒有可讀取的馴養動物資料。")
            self.last_error = reason
            self.status.setText(f"{reason} 顯示上次成功資料。" if self.summary is not None else reason)
            return False
        self.summary = summary
        self.last_error = ""
        animals = summary["animals"]
        total = coverage.get("total_tamed")
        text = f"已讀取 {len(animals)} 隻馴養動物"
        if _number(total) and total != len(animals):
            text += f"／來源共 {_quantity_text(total)} 隻"
        timestamp = _time_text(saved_at or coverage.get("snapshot_time"))
        if timestamp:
            text += f" · 存檔 {timestamp}"
        if status == "partial":
            text += " · 部分資料待確認"
        live = summary.get("live_stats", {})
        applied = coverage.get("live_stats", {}).get("applied_animals", 0)
        if live.get("world_verified") is True and applied:
            text += f" · 遊戲狀態 {applied}/{len(animals)} 隻 {_time_text(live.get('sample_time'))}"
            if applied < len(animals):
                text += "（其餘保留存檔）"
        elif live:
            text += " · 生命／耐力目前使用存檔來源"
        self._summary_status = text
        self.status.setToolTip("\n".join(str(warning) for warning in coverage.get("warnings", [])) + ("\n" + str(live.get("reason")) if live else ""))
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
            home = f"{'家附近' if state == 'home' else '離家'} · {_quantity_text(distance)} m" if state != "unknown" else "位置／基地未知"
            level = _quantity_text(animal.get("level"))
            values = (animal.get("name") or "未命名", animal.get("species") or "Unknown species", level,
                      animal.get("status_label") or "未知", home, animal.get("owner_name") or "未知")
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
        suffix = f" · 顯示 {len(animals)} 隻" if len(animals) != len(self.summary["animals"]) else ""
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
            empty = QLabel("目前沒有符合的動物" if self.summary else "尚未連接主機存檔")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.board_layout.addWidget(empty)
        for key, members in groups.items():
            name = str(members[0].get("owner_name") or "飼養者未知")
            color = owner_color(key)
            column = QWidget()
            column.setMinimumWidth(265)
            column.setMaximumWidth(380)
            column.setAccessibleName(f"飼養者 {name}")
            lane = QVBoxLayout(column)
            lane.setContentsMargins(0, 0, 0, 0)
            lane.setSpacing(8)
            title = QLabel(f"●  {name}    {len(members)}")
            owner = members[0].get("owner_capacity", {})
            bonuses = owner.get("percent", {})
            known = owner.get("known_percent", {})
            lines = []
            for stat, label in (("health", "生命"), ("stamina", "耐力")):
                value = bonuses.get(stat)
                lines.append(f"{label} +{max(0, value)}%" if isinstance(value, int) else f"{label}加成待核對" + (f"（已知 {known[stat]}%）" if isinstance(known.get(stat), int) else ""))
            title.setToolTip(" · ".join(lines) + "\n" + str(owner.get("scope", "缺少飼養者角色／帳號資料。")))
            title.setStyleSheet(f"color:{color};font-size:16px;font-weight:600;padding:10px;background:#1b2b38;border-radius:6px")
            lane.addWidget(title)
            live_count = sum(any(stat.get("id") == "health" and stat.get("maximum_status") == "live" for stat in animal.get("stats", [])) for animal in members)
            owner_note = QLabel("上限取自遊戲 · 包含已套用加成" if live_count == len(members) else " · ".join(lines))
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
            self._clear_detail("目前篩選沒有符合的馴養動物。")
            return
        self._selected_id = str(animal.get("id", ""))
        for ident, card in self.animal_cards.items():
            card.set_selected(ident == self._selected_id)
        self.animal_map.select_animal(self._selected_id, focus=False)
        self.detail_title.setText(f"{animal.get('name') or '未命名'} · {animal.get('species') or 'Unknown species'}")
        stats = [stat for stat in animal.get("stats", []) if isinstance(stat, dict)]
        present = {stat.get("id") for stat in stats}
        stats = stats + [{"id": key, "label": label, "value": None, "maximum": None, "detail": "存檔未提供可驗證數值"} for key, label in STANDARD_STATS if key not in present]
        self.stats_table.setRowCount(len(stats))
        for row, stat in enumerate(stats):
            value = _text(stat.get("value"))
            unit = str(stat.get("unit") or "")
            if value != "未知" and unit:
                value += f" {unit}"
            maximum = _quantity_text(stat.get("maximum")) if _number(stat.get("maximum")) else "未提供"
            if maximum != "未提供" and stat.get("maximum_status") == "reconstructed":
                maximum = "≈ " + maximum
            elif maximum != "未提供" and stat.get("maximum_status") == "persistent":
                maximum += "*"
            values = (stat.get("label") or stat.get("id") or "未辨識狀態", value, maximum, stat.get("detail") or "")
            for column, text in enumerate(values):
                entry = _put(self.stats_table, row, column, text)
                if column == 0:
                    entry.setData(Qt.ItemDataRole.UserRole, stat.get("id"))
        _fit_detail_table(self.stats_table)
        details = [item for item in animal.get("details", []) if isinstance(item, dict)]
        self.details_table.setRowCount(len(details))
        for row, detail in enumerate(details):
            _put(self.details_table, row, 0, detail.get("label") or "其他資訊")
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
        title = QLabel("背包與裝備")
        title.setStyleSheet("font-size:15px;font-weight:600")
        self.inventory_layout.addWidget(title)
        inventories = animal.get("inventories", [])
        if not inventories:
            empty = animal.get("inventory_status") == "empty"
            message = "存檔已確認背包／裝備沒有物品。" if empty else "存檔未提供可讀取的背包／裝備，內容未知。"
            self.inventory_layout.addWidget(QLabel(message))
            return
        for inventory in inventories:
            items = inventory.get("items")
            items = items if isinstance(items, list) else None
            state = inventory.get("status", "unavailable")
            label = str(inventory.get("label") or "未辨識儲物槽")
            if items:
                label += f" · {len(items)} 個物品堆疊"
                if state not in ("ok", "empty"):
                    label += " · 部分內容待確認"
            elif items == [] and state in ("ok", "empty"):
                label += " · 已確認空置"
            else:
                label += " · 內容未知，不能確認是否為空"
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
                tile.setText("× " + (quantity if quantity != "未知" else "?"))
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                tile.setFixedSize(72, 72)
                name = str(item.get("name") or ident or "未辨識物品")
                tooltip = f"{name}\n數量 {quantity} · 槽位 {_text(item.get('slot'))}"
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
            toggle.setText("物品名稱、數量與槽位")
            toggle.setCheckable(True)
            toggle.setArrowType(Qt.ArrowType.RightArrow)
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.inventory_layout.addWidget(toggle)
            table = _table(["物品", "數量", "存檔槽位"], label)
            table.setIconSize(QSize(32, 32))
            table.verticalHeader().setDefaultSectionSize(42)
            table.setColumnWidth(0, 260)
            table.setColumnWidth(1, 100)
            table.setColumnWidth(2, 100)
            table.setRowCount(len(items))
            for row, item in enumerate(items):
                ident = str(item.get("id", item.get("item_id", "")) or "")
                entry = _put(table, row, 0, item.get("name") or ident or "未辨識物品")
                entry.setIcon(item_icon(ident))
                entry.setData(Qt.ItemDataRole.UserRole, ident)
                warnings = [str(warning) for warning in item.get("warnings", [])]
                if not has_item_icon(ident):
                    warnings.append("此物品尚無可用圖示。")
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

    def _clear_detail(self, message="選取一隻動物查看狀態與背包／裝備。"):
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
