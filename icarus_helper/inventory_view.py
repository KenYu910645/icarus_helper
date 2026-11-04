"""Native Qt inventory charts, with asynchronous world/base-isolated summaries."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QScrollArea,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .item_icons import has_item_icon, item_icon, item_pixmap


FORM_LABELS = {"ore": "Ore", "raw": "Ore", "ingot": "Ingot", "processed": "Processed", "other": "Other"}
FORM_COLORS = {"ore": "#f3b651", "raw": "#f3b651", "ingot": "#59cebd", "processed": "#59cebd", "other": "#f3b651"}
FORM_ORDER = {"ore": 0, "other": 1, "ingot": 2, "processed": 3}


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _form(key):
    return "ore" if key == "raw" else str(key)


def _item_id(item):
    return str(item.get("id", item.get("item_id", "")) or "")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def number_text(value):
    if not _number(value):
        return "Unknown"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")


def _quantity_order(value):
    known = _number(value)
    return (not known, -value if known else 0)


def _row_order(row):
    values = tuple(row["values"].values())
    total = sum(values) if values and all(_number(value) for value in values) else None
    return (*_quantity_order(total), str(row["label"]), str(row["key"]))


def _chart_rows(category):
    result = []
    for row in category.get("rows", category.get("materials", [])):
        values = row.get("values", row.get("series", {}))
        values = values if isinstance(values, dict) else {}
        result.append({"key": row.get("key", row.get("id", "")), "label": row.get("label", row.get("name", "Uncategorized")),
                       "values": {_form(key): value for key, value in values.items()}, "items": row.get("items", [])})
    return sorted(result, key=_row_order)


def _series(category, rows):
    result = []
    for item in category.get("series", []):
        if isinstance(item, str):
            key, label = _form(item), FORM_LABELS.get(item, item)
        else:
            key = _form(item.get("id", item.get("key", "other")))
            label = item.get("label", FORM_LABELS.get(key, key))
        if key not in {entry["id"] for entry in result}:
            result.append({"id": key, "label": label, "color": FORM_COLORS.get(key, "#9db9ce")})
    if not result:
        available = {key for row in rows for key in row["values"]}
        keys = sorted(available, key=lambda key: (["ore", "ingot", "processed", "other"].index(key) if key in ("ore", "ingot", "processed", "other") else 99, key))
        result = [{"id": key, "label": FORM_LABELS.get(key, key), "color": FORM_COLORS.get(key, "#9db9ce")} for key in keys]
    return sorted(result, key=lambda entry: (FORM_ORDER.get(entry["id"], 1), entry["id"]))


def _search_category(category, text):
    """Filter a display copy, keeping unknown quantities and the source intact."""
    query = text.strip().casefold().replace("_", " ")
    if not query:
        return category
    rows = []
    for row in _chart_rows(category):
        if any(query in str(row[field]).casefold().replace("_", " ") for field in ("key", "label")):
            rows.append(row)
            continue
        items = [item for item in row["items"] if any(
            query in str(item.get(field, "")).casefold().replace("_", " ")
            for field in ("id", "item_id", "name", "english", "material", "material_label"))]
        if not items:
            continue
        values = {}
        for item in items:
            form, quantity = _form(item.get("form", "other")), item.get("quantity")
            previous = values.get(form, 0)
            values[form] = previous + quantity if _number(previous) and _number(quantity) else None
        rows.append({**row, "items": items, "values": values})
    return {**category, "rows": rows}


class InventoryChart(QWidget):
    """One stacked bar per material, using actual counts without conversion."""
    def __init__(self):
        super().__init__()
        self.setAccessibleName("Base inventory Category Bar Chart")
        self.setMinimumHeight(340)
        self.setMouseTracking(True)
        self.rows = []
        self.series = []
        self.title = "Inventory Quantities"
        self.message = "Set your home location on the map first."
        self.bar_targets = []
        self.icon_targets = []
        self.group_width = 104

    def set_category(self, category):
        self.title = str(category.get("label", "Inventory Quantities"))
        self.rows = _chart_rows(category)
        self.series = _series(category, self.rows)
        self.bar_targets, self.icon_targets = [], []
        self.message = "No inventory is available in this category." if not self.rows else ""
        self.group_width = max(98, max((len(row["values"]) for row in self.rows), default=1) * 40 + 20)
        self.setMinimumWidth(max(360, len(self.rows) * self.group_width + 100))
        self.update()

    def clear(self, message):
        self.rows, self.series, self.bar_targets, self.icon_targets = [], [], [], []
        self.message = message
        self.setMinimumWidth(360)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#14232f"))
        font = QFont("Microsoft JhengHei UI")
        font.setPixelSize(13)
        painter.setFont(font)
        self.bar_targets = []
        self.icon_targets = []
        if self.message:
            painter.setPen(QColor("#9eb7c8"))
            painter.drawText(QRectF(self.rect()).adjusted(30, 20, -30, -20), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        painter.setPen(QColor("#e6eff6"))
        font.setPixelSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QPointF(20, 28), self.title)
        font.setPixelSize(12)
        font.setBold(False)
        painter.setFont(font)
        # Reserve visual space for unknown caps, without assigning them a count.
        unknown_slots = max((sum(not _number(value) for value in row["values"].values()) for row in self.rows), default=0)
        chart = QRectF(65, 64 + unknown_slots * 24, max(80, self.width() - 89), max(60, self.height() - 188 - unknown_slots * 24))
        known = [sum(value for value in row["values"].values() if _number(value)) for row in self.rows]
        peak = max(known, default=1)
        raw_step = max(peak / 5, 1)
        magnitude = 10 ** math.floor(math.log10(raw_step))
        normalized = raw_step / magnitude
        step = next((factor * magnitude for factor in (1, 2, 5, 10) if normalized <= factor), 10 * magnitude)
        maximum = max(step, math.ceil(peak / step) * step)
        ticks = int(maximum / step)
        painter.setPen(QColor("#8fa9bd"))
        painter.drawText(QPointF(15, chart.top() - 12), "Item count")
        for index in range(ticks + 1):
            amount = step * index
            y = chart.bottom() - amount / maximum * chart.height()
            painter.setPen(QPen(QColor("#2b414f"), 1))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor("#9db5c8"))
            painter.drawText(QRectF(0, y - 9, chart.left() - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, number_text(amount))
        group_width = chart.width() / max(1, len(self.rows))
        bar_width = min(40.0, group_width - 22)
        for index, row in enumerate(self.rows):
            center_x = chart.left() + group_width * (index + 0.5)
            present = [series for series in self.series if series["id"] in row["values"]]
            known_total = sum(value for value in row["values"].values() if _number(value))
            stacked = 0
            unknown_index = 0
            x, width = center_x - bar_width / 2, bar_width
            for column, series in enumerate(present):
                amount = row["values"][series["id"]]
                if _number(amount):
                    height = amount / maximum * chart.height()
                    top = chart.bottom() - (stacked + amount) / maximum * chart.height()
                    bar = QRectF(x, top, width, height)
                    stacked += amount
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(series["color"]))
                    painter.drawRect(bar)
                    if amount == 0:
                        painter.setPen(QPen(QColor(series["color"]), 1))
                        painter.drawLine(bar.topLeft(), bar.topRight())
                    if len(present) > 1 and height >= 20:
                        painter.setPen(QColor("#10222b"))
                        painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, number_text(amount))
                else:
                    top = chart.bottom() - known_total / maximum * chart.height() - (unknown_index + 1) * 24
                    bar = QRectF(x, top, width, 22)
                    unknown_index += 1
                    painter.setPen(QPen(QColor(series["color"]), 1.2, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(bar)
                    painter.setPen(QColor("#e9f3fa"))
                    painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, "?")
                tooltip = f"{row['label']} · {series['label']}\nQuantity: {number_text(amount)}"
                details = []
                for item in row.get("items", []):
                    if _form(item.get("form", "other")) != series["id"]:
                        continue
                    count = number_text(item.get("quantity"))
                    if item.get("quantity") is None and _number(item.get("known_quantity")) and item["known_quantity"]:
                        count += f" (confirmed: {number_text(item['known_quantity'])})"
                    details.append(f"{item.get('name', item.get('id', ''))}: {count}")
                if details:
                    tooltip += "\n" + "\n".join(details[:8])
                target = bar if bar.height() else bar.adjusted(0, -2, 0, 2)
                self.bar_targets.append((target.adjusted(-4, 0, 4, 0), tooltip, row, series))
                matching = [item for item in row.get("items", []) if _form(item.get("form", "other")) == series["id"]]
                representative = matching[0] if matching else {}
                ident = _item_id(representative)
                icon_x = center_x + (column - (len(present) - 1) / 2) * 40
                icon_rect = QRectF(icon_x - 16, chart.bottom() + 12, 32, 32)
                painter.setPen(QPen(QColor(series["color"]), 1))
                painter.setBrush(QColor("#0c1822"))
                painter.drawRoundedRect(icon_rect.adjusted(-2, -2, 2, 2), 4, 4)
                pixmap = item_pixmap(ident, 32)
                image_rect = QRectF(icon_rect.center().x() - pixmap.width() / 2, icon_rect.center().y() - pixmap.height() / 2, pixmap.width(), pixmap.height())
                painter.drawPixmap(image_rect, pixmap, QRectF(pixmap.rect()))
                if len(matching) > 1:
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(QRectF(icon_rect.right() - 7, icon_rect.bottom() - 11, 20, 15), Qt.AlignmentFlag.AlignRight, f"+{len(matching) - 1}")
                self.icon_targets.append({"rect": icon_rect, "item_id": ident,
                                          "name": representative.get("name", row["label"]),
                                          "form": series["id"], "has_icon": has_item_icon(ident), "tooltip": tooltip})
            total_label = number_text(known_total)
            if unknown_index:
                total_label = f"{total_label} + ?" if known_total else "?"
            label_y = chart.bottom() - known_total / maximum * chart.height() - unknown_index * 24 - 21
            painter.setPen(QColor("#e9f3fa"))
            painter.drawText(QRectF(center_x - group_width / 2, label_y, group_width, 18), Qt.AlignmentFlag.AlignHCenter, total_label)
            painter.setPen(QColor("#d2e2ed"))
            painter.drawText(QRectF(center_x - group_width / 2 + 3, chart.bottom() + 51, group_width - 6, 56), Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, str(row["label"]))
        painter.end()

    def mouseMoveEvent(self, event):
        tooltip = next((target["tooltip"] for target in reversed(self.icon_targets) if target["rect"].contains(event.position())), "")
        if not tooltip:
            tooltip = next((text for rect, text, *_ in reversed(self.bar_targets) if rect.contains(event.position())), "")
        if self.toolTip() != tooltip:
            self.setToolTip(tooltip)
        super().mouseMoveEvent(event)


class _SummarySignals(QObject):
    success = Signal(int, object)
    failure = Signal(int, str)


class _SummaryWorker(QRunnable):
    def __init__(self, generation, snapshot, base, radius, function=None):
        super().__init__()
        self.generation, self.snapshot, self.base, self.radius, self.function = generation, snapshot, base, radius, function
        self.signals = _SummarySignals()

    def run(self):
        try:
            function = self.function
            if function is None:
                from .inventory import summarize_inventory
                function = summarize_inventory
            result = function(self.snapshot, self.base, radius_m=self.radius)
            self.signals.success.emit(self.generation, {"summary": result, "saved_at": _value(self.snapshot, "saved_at")})
        except Exception as exc:
            self.signals.failure.emit(self.generation, f"{type(exc).__name__}: {exc}")


class InventoryPanel(QWidget):
    loaded = Signal(object)
    failed = Signal(str)
    radiusChanged = Signal(int)

    def __init__(self, summary_function: Callable | None = None):
        super().__init__()
        self.summary = None
        self.snapshot = None
        self.base = None
        self.busy = False
        self._closed = False
        self._generation = 0
        self._context = None
        self._pending = False
        self._worker = None
        self._summary_function = summary_function
        self.last_error = ""
        self._summary_status = ""
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(120)
        self.debounce.timeout.connect(self._start)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        controls = QHBoxLayout()
        self.base_label = QLabel("Base inventory · No base selected")
        self.base_label.setStyleSheet("font-size:18px;font-weight:600;color:#f2c771")
        controls.addWidget(self.base_label, 1)
        controls.addWidget(QLabel("Horizontal radius"))
        self.radius_spin = QSpinBox()
        self.radius_spin.setAccessibleName("Base inventory radius")
        self.radius_spin.setRange(10, 1000)
        self.radius_spin.setSingleStep(10)
        self.radius_spin.setSuffix(" m")
        self.radius_spin.setValue(100)
        self.radius_spin.setMinimumSize(105, 34)
        self.radius_spin.setStyleSheet("QSpinBox {background:#192834;border:1px solid #344857;border-radius:4px;padding:5px 7px;}")
        self.radius_spin.valueChanged.connect(self._radius_changed)
        controls.addWidget(self.radius_spin)
        controls.addWidget(QLabel("Category"))
        self.category_combo = QComboBox()
        self.category_combo.setAccessibleName("Inventory category")
        self.category_combo.setMinimumWidth(170)
        self.category_combo.currentIndexChanged.connect(self._category_changed)
        controls.addWidget(self.category_combo)
        layout.addLayout(controls)
        self.search = QLineEdit()
        self.search.setAccessibleName("Search base inventory")
        self.search.setPlaceholderText("Search items or materials in this category…")
        self.search.setToolTip("Filter the selected category by item or material name.")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(34)
        self.search.textChanged.connect(self._category_changed)
        layout.addWidget(self.search)
        self.status = QLabel("Right-click the map to set your home location first.")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        split = QSplitter(Qt.Orientation.Vertical)
        self.chart = InventoryChart()
        self.chart_scroll = QScrollArea()
        self.chart_scroll.setWidgetResizable(True)
        self.chart_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chart_scroll.setWidget(self.chart)
        split.addWidget(self.chart_scroll)
        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("Base inventory Details")
        self.table.setHorizontalHeaderLabels(["Item", "Category", "Form", "Quantity", "Containers"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(32, 32))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        split.addWidget(self.table)
        split.setSizes([420, 210])
        layout.addWidget(split, 1)
        self.note = QLabel("Saved inventory in containers and equipment within the horizontal base radius. Ore and ingots are counted separately, without smelting conversion.")
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        layout.addWidget(self.note)

    def set_snapshot(self, snapshot, base):
        if self._closed:
            return
        self.snapshot, self.base = snapshot, dict(base) if base else None
        context = (_value(snapshot, "world_id"), tuple(self.base.get(axis) for axis in ("id", "x", "y", "z")) if self.base else None, self.radius_spin.value())
        different_context = context != self._context
        self._context = context
        self._generation += 1
        if not snapshot or not base:
            self._clear_contents("Right-click the map to set your home location first.")
            self.base_label.setText("Base inventory · No base selected")
            self._pending = False
            self.debounce.stop()
            return
        self.base_label.setText(f"Base inventory · {self.base.get('name', 'Selected Base')}")
        if different_context:
            self._clear_contents("Loading inventory for this base…")
        self.status.setText("Updating home inventory…")
        self.debounce.start()

    def _radius_changed(self, radius):
        self.radiusChanged.emit(radius)
        if self.snapshot is not None:
            self.set_snapshot(self.snapshot, self.base)

    def _start(self):
        if self._closed or self.snapshot is None or not self.base:
            return
        if self.busy:
            self._pending = True
            return
        self.busy, self._pending = True, False
        worker = _SummaryWorker(self._generation, self.snapshot, dict(self.base), self.radius_spin.value(), self._summary_function)
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
                self.loaded.emit(self.summary)
            else:
                self.failed.emit(self.last_error)
        if self.snapshot is not None and self.base and (self._pending or generation != self._generation):
            self.debounce.start(0)

    def _failure(self, generation, error):
        self.busy = False
        self._worker = None
        if self._closed:
            return
        if generation == self._generation:
            self.last_error = error
            self.status.setText("Inventory update failed; showing the last successful totals. " + self._summary_status
                                if self.summary is not None else "Inventory is temporarily unavailable. Refresh the save again later.")
            self.status.setToolTip(error)
            self.failed.emit(error)
        if self.snapshot is not None and self.base and (self._pending or generation != self._generation):
            self.debounce.start(0)

    def _clear_contents(self, message):
        self.summary = None
        self._summary_status = ""
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.blockSignals(False)
        self.chart.clear(message)
        self.table.setRowCount(0)
        self.status.setText(message)

    def clear(self):
        self._generation += 1
        self._pending = False
        self.snapshot, self.base, self._context = None, None, None
        self.debounce.stop()
        self.base_label.setText("Base inventory · No base selected")
        self._clear_contents("Right-click the map to set your home location first.")

    def set_missing_source(self):
        """Invalidate older inventory jobs without presenting unknown stock as zero."""
        self.clear()
        self.base_label.setText("Base inventory · Host save not connected")
        self._clear_contents("Connect a host save to view base inventory. Select “Choose host save”.")

    def set_summary(self, summary, saved_at=None):
        """Apply valid totals, retaining the last good result for this context."""
        if self._closed:
            return False
        coverage = summary.get("coverage", {}) if isinstance(summary, dict) else {}
        coverage = coverage if isinstance(coverage, dict) else {}
        status = str(coverage.get("status", ""))
        if not isinstance(summary, dict) or status not in ("", "ok", "partial"):
            reason = str(coverage.get("reason") or "The current save has no readable container inventory data.")
            self.last_error = reason
            if self.summary is not None:
                self.status.setText("Inventory update failed; showing the last successful totals. " + self._summary_status)
            else:
                self._clear_contents(reason)
            self.status.setToolTip(reason)
            return False
        self.summary = summary
        self.last_error = ""
        previous = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for category in summary.get("categories", []):
            self.category_combo.addItem(str(category.get("label", category.get("id", "Uncategorized"))), category.get("id"))
        preferred = next((i for i, category in enumerate(summary.get("categories", [])) if category.get("id") in ("resources", "metal", "metals")), 0)
        index = self.category_combo.findData(previous)
        self.category_combo.setCurrentIndex(index if index >= 0 else preferred)
        self.category_combo.blockSignals(False)
        containers = coverage.get("counted_containers", len(summary.get("containers", [])))
        count = len(summary.get("items", []))
        timestamp = saved_at
        if isinstance(timestamp, datetime):
            timestamp = timestamp.astimezone().strftime("%m/%d %H:%M:%S")
        text = f"{containers} containers / devices · {count} item types"
        if timestamp:
            text += f" · Saved {timestamp}"
        unknown = coverage.get("unknown_stacks", 0)
        if unknown:
            text += f" · {unknown} stacks with unknown quantities"
        if coverage.get("unknown_items"):
            text += f" · {coverage['unknown_items']} unrecognized item types"
        if status == "partial":
            text += " · Some data needs verification"
        self._summary_status = text
        self.status.setText(text)
        self.status.setToolTip("\n".join(str(warning) for warning in coverage.get("warnings", [])))
        self._category_changed()
        return True

    def _category_changed(self, *_):
        if not self.summary:
            return
        key = self.category_combo.currentData()
        category = next((item for item in self.summary.get("categories", []) if item.get("id") == key), None)
        if category is None:
            self.chart.clear("No readable inventory within the selected radius.")
            self.table.setRowCount(0)
            return
        displayed_category = _search_category(category, self.search.text())
        self.chart.set_category(displayed_category)
        if self.search.text().strip() and not self.chart.rows:
            self.chart.clear("No items match your search in this category.")
        items = [item for item in self.summary.get("items", []) if item.get("category") in (key, category.get("label"))]
        if not items:
            items = [item for row in _chart_rows(category) for item in row.get("items", [])]
        if self.search.text().strip():
            matching_ids = {_item_id(item) for row in _chart_rows(displayed_category) for item in row["items"]}
            items = [item for item in items if _item_id(item) in matching_ids]
        items.sort(key=lambda item: (*_quantity_order(item.get("quantity")), str(item.get("name", "")), _item_id(item)))
        container_names = {str(container.get("id")): str(container.get("name", container.get("id", "Container"))) for container in self.summary.get("containers", [])}
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            quantity = item.get("quantity")
            quantity_label = number_text(quantity)
            if quantity is None and _number(item.get("known_quantity")) and item["known_quantity"]:
                quantity_label += f" (confirmed: {number_text(item['known_quantity'])})"
            container_ids = item.get("containers", [item["container_id"]] if item.get("container_id") is not None else [])
            containers = ", ".join(container_names.get(str(ident), str(ident)) for ident in container_ids)
            values = [item.get("name", item.get("id", item.get("item_id", "Unrecognized Item"))), category.get("label", key), FORM_LABELS.get(item.get("form"), item.get("form", "Other")), quantity_label, containers]
            for column, text in enumerate(values):
                entry = QTableWidgetItem(str(text))
                entry.setToolTip(str(text))
                if column == 0:
                    ident = _item_id(item)
                    entry.setIcon(item_icon(ident))
                    entry.setData(Qt.ItemDataRole.UserRole, ident)
                    if not has_item_icon(ident):
                        entry.setToolTip(f"{text}\nNo icon is available for this item.")
                if column == 3:
                    entry.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, entry)

    def shutdown(self, wait_ms=5000):
        self._closed = True
        self._generation += 1
        self.debounce.stop()
        self.pool.waitForDone(wait_ms)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
