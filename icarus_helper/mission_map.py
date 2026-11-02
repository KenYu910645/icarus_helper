"""An ordered mission map: verified targets, later locations and pending stages."""
from __future__ import annotations

import math
from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter, QVBoxLayout, QWidget

from .map_icons import marker_pixmap
from .map_widget import WorldMap, value


def stage_number(point, fallback):
    return point.get("order", point.get("stage_order", point.get("stage", fallback)))


def candidate_point(point):
    return point.get("status") == "candidate" or point.get("confidence") in ("candidate", "inferred", "estimated", "候選", "推定", "估計") or bool(point.get("is_candidate"))


class MissionMapView(WorldMap):
    def __init__(self):
        super().__init__()
        self.setAccessibleName("任務地圖；數字對應任務階段，滾輪縮放、拖曳平移")
        self.regions = []
        self._targets_fit = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_targets_fit", False):
            self.fit_targets()

    def zoom(self, factor):
        self._targets_fit = False
        super().zoom(factor)

    def focus_point(self, point):
        self._targets_fit = False
        super().focus_point(point)

    def fit_map(self):
        self._targets_fit = False
        super().fit_map()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._targets_fit = False
        super().mouseMoveEvent(event)

    def drawForeground(self, painter, rect):
        transform = self.viewportTransform()
        painter.save()
        painter.resetTransform()
        self._hit_targets = []
        for region in self.regions:
            bounds = region.get("bounds", {})
            if not all(key in bounds for key in ("min_x", "min_y", "max_x", "max_y")):
                continue
            area = transform.mapRect(QRectF(bounds["min_x"] / 100, bounds["min_y"] / 100, (bounds["max_x"] - bounds["min_x"]) / 100, (bounds["max_y"] - bounds["min_y"]) / 100))
            painter.setPen(QPen(QColor("#f6c45f"), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(246, 196, 95, 35))
            painter.drawRect(area)
        for base in self.all_bases:
            pos = transform.map(QPointF(base["x"] / 100, base["y"] / 100))
            icon = marker_pixmap("base", size=28)
            painter.drawPixmap(QRectF(pos.x() - 14, pos.y() - 14, 28, 28), icon, QRectF(icon.rect()))
        groups = defaultdict(list)
        for point in self.points:
            groups[(point["x"], point["y"], point.get("z", 0))].append(point)
        points = list(groups.values())
        if self.chosen is not None:
            points.sort(key=lambda members: any(point.get("id") == value(self.chosen, "id") for point in members))
        for fallback, members in enumerate(points, 1):
            point = next((p for p in members if p.get("id") == value(self.chosen, "id")), members[0]) if self.chosen else members[0]
            pos = transform.map(QPointF(point["x"] / 100, point["y"] / 100))
            if not QRectF(self.viewport().rect()).adjusted(-100, -60, 100, 60).contains(pos):
                continue
            status = str(point.get("status", ""))
            color = QColor("#77dfad" if status in ("completed", "已完成", "historical") else "#ffca66" if status in ("active", "current", "進行中", "目前") else "#a2cbe7")
            painter.setPen(QPen(QColor("#07141e"), 5))
            painter.setBrush(QColor("#142737"))
            painter.drawEllipse(pos, 15, 15)
            candidate = any(candidate_point(p) for p in members)
            painter.setPen(QPen(color, 2.2, Qt.PenStyle.DashLine if candidate else Qt.PenStyle.SolidLine))
            painter.drawEllipse(pos, 15, 15)
            font = QFont("Segoe UI")
            font.setPixelSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            number = str(point.get("number", stage_number(point, fallback)))
            numbers = sorted({int(p.get("number", stage_number(p, fallback))) for p in members})
            if len(numbers) > 1:
                number = f"{numbers[0]}–{numbers[-1]}" if numbers == list(range(numbers[0], numbers[-1] + 1)) else f"{numbers[0]}+"
            painter.drawText(QRectF(pos.x() - 16, pos.y() - 15, 32, 30), Qt.AlignmentFlag.AlignCenter, number)
            if candidate:
                painter.setPen(QPen(QColor("#18222b"), 1))
                painter.setBrush(QColor("#ffd06a"))
                badge = QRectF(pos.x() + 8, pos.y() - 22, 15, 15)
                painter.drawEllipse(badge)
                font.setPixelSize(10)
                painter.setFont(font)
                painter.setPen(QColor("#17202a"))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "?")
            font = QFont("Microsoft JhengHei UI")
            font.setPixelSize(11)
            painter.setFont(font)
            label = str(point.get("label", point.get("name", "任務地點")))
            if len(numbers) > 1:
                label = "階段 " + "、".join(map(str, numbers)) + "（同一地點）"
            short_label = label[:16] + ("…" if len(label) > 16 else "")
            width = painter.fontMetrics().horizontalAdvance(short_label) + 12
            label_rect = QRectF(pos.x() - width / 2, pos.y() + 19, width, 21)
            painter.setPen(QPen(color, 0.7))
            painter.setBrush(QColor(9, 21, 31, 238))
            painter.drawRoundedRect(label_rect, 3, 3)
            painter.setPen(QColor("#f3f8fc"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, short_label)
            self._hit_targets.append({"rect": QRectF(pos.x() - 18, pos.y() - 18, 36, 36).united(label_rect), "members": [point], "scene_position": QPointF(point["x"] / 100, point["y"] / 100)})
            if self.chosen is not None and any(p.get("id") == value(self.chosen, "id") for p in members):
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(pos, 21, 21)
        painter.restore()

    def fit_targets(self):
        locations = [(p["x"] / 100, p["y"] / 100) for p in self.points]
        for region in self.regions:
            bounds = region.get("bounds", {})
            if all(key in bounds for key in ("min_x", "min_y", "max_x", "max_y")):
                locations.extend([(bounds["min_x"] / 100, bounds["min_y"] / 100), (bounds["max_x"] / 100, bounds["max_y"] / 100)])
        if not locations:
            self.fit_map()
            return
        xmin, xmax = min(p[0] for p in locations), max(p[0] for p in locations)
        ymin, ymax = min(p[1] for p in locations), max(p[1] for p in locations)
        pad = max(200, max(xmax - xmin, ymax - ymin) * 0.15)
        self._fit_mode = False
        self._fit_pending = False
        self._targets_fit = True
        self.fitInView(QRectF(xmin - pad, ymin - pad, xmax - xmin + pad * 2, ymax - ymin + pad * 2), Qt.AspectRatioMode.KeepAspectRatio)


class MissionMapPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.route = {}
        self.mission_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        heading = QLabel("任務地圖")
        heading.setStyleSheet("font-size:16px;font-weight:600;color:#f6c45f")
        toolbar.addWidget(heading)
        toolbar.addStretch()
        self.map = MissionMapView()
        for text, action in [("＋", lambda: self.map.zoom(1.4)), ("－", lambda: self.map.zoom(1 / 1.4)), ("全部地點", self.map.fit_targets), ("全圖", self.map.fit_map)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.summary = QLabel("選擇任務查看各階段地點")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.map)
        self.stages = QListWidget()
        self.stages.setAccessibleName("任務階段與後續地點")
        self.stages.setWordWrap(True)
        self.stages.currentItemChanged.connect(self.select_stage)
        split.addWidget(self.stages)
        split.setSizes([410, 170])
        layout.addWidget(split, 1)
        self.point_note = QLabel("數字對應下方階段；點選地圖或階段可定位。")
        self.point_note.setWordWrap(True)
        self.point_note.setObjectName("muted")
        layout.addWidget(self.point_note)
        self.map.selected.connect(self.select_point)

    def clear(self):
        self.route = {}
        self.mission_id = None
        self.map.set_points([])
        self.map.set_bases([])
        self.map.set_catalog({"map": "", "calibrated": False})
        self.map.regions = []
        self.map.chosen = None
        self.stages.clear()
        self.summary.setText("選擇任務查看各階段地點")
        self.point_note.setText("")

    def set_mission(self, snapshot, mission, catalog, bases, route=None, active_base=None):
        if route is None:
            from .mission_locations import build_mission_locations
            route = build_mission_locations(snapshot, mission)
        old_positions = {(p.get("id"), p["x"], p["y"], p.get("z", 0)) for p in self.map.points}
        chosen_id = value(self.map.chosen, "id") if self.map.chosen is not None else None
        old_chosen_position = (value(self.map.chosen, "x"), value(self.map.chosen, "y"), value(self.map.chosen, "z")) if chosen_id else None
        previous = self.mission_id
        self.mission_id = mission.get("id")
        self.route = route
        self.map.set_catalog(catalog)
        self.map.set_bases(bases)
        self.map.regions = route.get("regions", [])
        points = []
        for index, raw in enumerate(route.get("points", []), 1):
            point = dict(raw)
            if not all(isinstance(point.get(axis), (float, int)) and math.isfinite(point[axis]) for axis in ("x", "y")):
                continue
            point.setdefault("z", 0)
            point.setdefault("id", f"{self.mission_id}:location:{index}")
            point.setdefault("name", point.get("label", "任務地點"))
            point.setdefault("number", point.get("order", index))
            point["distance_m"] = math.hypot(point["x"] - active_base["x"], point["y"] - active_base["y"]) / 100 if active_base else None
            points.append(point)
        self.map.set_points(points)
        self.map.chosen = next((point for point in points if point["id"] == chosen_id), None)
        self.stages.blockSignals(True)
        self.stages.clear()
        entries = [(float(point.get("order", index)), 0, point) for index, point in enumerate(points, 1)]
        entries += [(float(stage.get("order", stage.get("stage_order", len(points) + index))), 1, stage) for index, stage in enumerate(route.get("pending_stages", []), 1)]
        for _, pending, stage in sorted(entries, key=lambda entry: (entry[0], entry[1])):
            label = stage.get("label", stage.get("title", stage.get("stage", "任務階段")))
            if pending:
                reason = stage.get("description", stage.get("reason", "位置將在任務進行時決定"))
                text = f"○ {label}\n{reason}"
            else:
                status = "候選地點" if candidate_point(stage) else stage.get("status_label", {"active": "目前", "current": "目前", "completed": "已完成", "future": "後續", "historical": "已知地點"}.get(stage.get("status"), "執行地點"))
                text = f"{stage['number']}. {label}\n{status} · {self.map.grid(stage['x'], stage['y'])}"
                if stage.get("distance_m") is not None:
                    text += f" · 距{active_base['name']} {stage['distance_m']:,.0f} m（水平直線）"
            item = QListWidgetItem(str(text))
            item.setData(Qt.ItemDataRole.UserRole, {"pending": bool(pending), **stage})
            self.stages.addItem(item)
            if not pending and stage.get("id") == chosen_id:
                self.stages.setCurrentItem(item)
        self.stages.blockSignals(False)
        pending_count = len(route.get("pending_stages", []))
        known_locations = {(p["x"], p["y"], p.get("z", 0)) for p in points if not candidate_point(p)}
        candidate_locations = {(p["x"], p["y"], p.get("z", 0)) for p in points if candidate_point(p)}
        self.summary.setText(f"已定位 {len(known_locations)} 處 · 候選 {len(candidate_locations)} 處" + (f" · {pending_count} 階段位置未定" if pending_count else "") if points or pending_count else "此任務尚未取得執行位置")
        self.point_note.setText("數字對應下方階段；點選地圖或階段可定位。")
        new_positions = {(p.get("id"), p["x"], p["y"], p.get("z", 0)) for p in points}
        if previous != self.mission_id:
            self.map.chosen = None
            self.map.fit_targets()
        elif self.map.chosen is not None:
            point = self.map.chosen
            if old_chosen_position != (point["x"], point["y"], point.get("z", 0)):
                self.map.focus_point(point)
        elif old_positions != new_positions:
            self.map.fit_targets()

    def select_stage(self, item, previous=None):
        if item is None:
            return
        stage = item.data(Qt.ItemDataRole.UserRole)
        self.point_note.setText(str(stage.get("description", stage.get("reason", ""))))
        if not stage.get("pending"):
            self.map.focus_point(stage)

    def select_point(self, point):
        for index in range(self.stages.count()):
            item = self.stages.item(index)
            stage = item.data(Qt.ItemDataRole.UserRole)
            if stage.get("id") == point.get("id"):
                self.stages.setCurrentItem(item)
                break
