"""Batched QGraphicsView map: pan/zoom, calibrated grid and selectable markers."""
from __future__ import annotations

import math
from pathlib import Path
from collections import defaultdict

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .map_icons import marker_pixmap, mineral_style


COLORS = {
    "cave": "#c9a0ff", "deep": "#f0b65a", "ore": "#75cee0",
    "exotic": "#f084d8", "geyser": "#ff7c68", "oil": "#b7b386",
    "water": "#66b5ff", "plant": "#86c98b", "tree": "#74a67c",
    "deployable": "#c9d3df", "drill": "#72e3ae", "player": "#ffffff",
    "base": "#72e3ae", "resource": "#75cee0",
}


def value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def kind_group(item) -> str:
    kind = str(value(item, "kind", "resource")).lower()
    if "cave" in kind or "entrance" in kind:
        return "cave"
    if "deep" in kind:
        return "deep"
    if "drill" in kind:
        return "drill"
    if "voxel" in kind or "ore" in kind or "mineral" in kind:
        return "ore"
    if "exotic" in kind or "meta" in kind:
        return "exotic"
    return kind if kind in COLORS else "resource"


def region_of(item):
    raw = value(item, "raw", {}) or {}
    bounds = raw.get("region_bounds", value(item, "region_bounds"))
    if isinstance(bounds, dict) and all(k in bounds for k in ("min_x", "min_y", "max_x", "max_y")):
        return QRectF(bounds["min_x"] / 100, bounds["min_y"] / 100, (bounds["max_x"] - bounds["min_x"]) / 100, (bounds["max_y"] - bounds["min_y"]) / 100)
    return None


class WorldMap(QGraphicsView):
    selected = Signal(object)
    positionPicked = Signal(float, float)
    cursorPosition = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setObjectName("worldMap")
        self.setAccessibleName("互動地圖；滾輪縮放、拖曳平移、點選標記")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor("#101e29"))
        self.points = []
        self.base = None
        self.all_bases = []
        self.chosen = None
        self.terrain = QPixmap()
        self.map_bounds = QRectF(-4032, -4032, 8064, 8064)
        self.grid_verified = False
        self.map_name = ""
        self._press = QPoint()
        self._fit_pending = True
        self._fit_mode = True
        self._hit_targets = []
        self.setSceneRect(self.map_bounds.adjusted(-180, -180, 180, 180))

    def set_catalog(self, catalog):
        self._hit_targets = []
        self.map_name = str(catalog.get("map", ""))
        bounds = catalog.get("bounds", {})
        if isinstance(bounds, dict):
            xmin = bounds.get("min_x", bounds.get("xmin", -403200))
            ymin = bounds.get("min_y", bounds.get("ymin", -403200))
            xmax = bounds.get("max_x", bounds.get("xmax", 403200))
            ymax = bounds.get("max_y", bounds.get("ymax", 403200))
        elif isinstance(bounds, (list, tuple)) and len(bounds) == 4:
            xmin, ymin, xmax, ymax = bounds
        else:
            xmin, ymin, xmax, ymax = -403200, -403200, 403200, 403200
        self.map_bounds = QRectF(xmin / 100, ymin / 100, (xmax - xmin) / 100, (ymax - ymin) / 100)
        self.grid_verified = bool(catalog.get("calibrated", catalog.get("grid_verified", False)))
        self.terrain = QPixmap(str(catalog.get("background_path", ""))) if catalog.get("background_path") else QPixmap()
        self.setSceneRect(self.map_bounds.adjusted(-180, -180, 180, 180))
        self.viewport().update()

    def set_points(self, points):
        priority = {"cave": 3, "deep": 4, "drill": 5, "player": 6}
        self.points = sorted(points, key=lambda p: 8 if kind_group(p) == "deep" and str(value(p, "drill_status", "")).startswith("已放置") else priority.get(kind_group(p), 0))
        self._hit_targets = []
        self.viewport().update()

    def set_bases(self, bases, selected=None):
        self.all_bases, self.base = bases, selected
        self.viewport().update()

    def fit_map(self):
        self._fit_mode = True
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_pending = False

    def zoom(self, factor):
        self._fit_mode = False
        scale = self.transform().m11()
        if 0.025 < scale * factor < 35:
            self.scale(factor, factor)

    def focus_point(self, point):
        self._fit_mode = False
        self.chosen = point
        region = region_of(point)
        if region is not None and region.width() > 0 and region.height() > 0:
            self.fitInView(region.adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)
            self.viewport().update()
            return
        scale = self.transform().m11()
        if scale < 1.0:
            self.scale(1.0 / max(scale, 1e-6), 1.0 / max(scale, 1e-6))
        self.centerOn(float(value(point, "x", 0)) / 100, float(value(point, "y", 0)) / 100)
        self.viewport().update()

    def wheelEvent(self, event):
        self.zoom(1.22 ** (event.angleDelta().y() / 120))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_pending or self._fit_mode:
            self.fit_map()

    def mousePressEvent(self, event):
        self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and (event.position().toPoint() - self._press).manhattanLength() > 6:
            self._fit_mode = False
        pos = self.mapToScene(event.position().toPoint())
        self.cursorPosition.emit(pos.x() * 100, pos.y() * 100)
        target = self.hit_target(event.position())
        if target:
            members = target["members"]
            names = [str(value(p, "name", "位置")) for p in members[:6]]
            tooltip = (f"{len(members)} 個重疊點位；點選放大\n" if len(members) > 1 else "") + "\n".join(names)
        else:
            tooltip = ""
        if self.viewport().toolTip() != tooltip:
            self.viewport().setToolTip(tooltip)
        super().mouseMoveEvent(event)

    def hit_target(self, viewport_position):
        for target in reversed(self._hit_targets):
            if target["rect"].contains(QPointF(viewport_position)):
                return target
        return None

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if (event.position().toPoint() - self._press).manhattanLength() > 6:
            return
        pos = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.MouseButton.RightButton:
            self.positionPicked.emit(pos.x() * 100, pos.y() * 100)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self.hit_target(event.position())
        if target is not None:
            if len(target["members"]) > 1:
                self.zoom(2.2)
                self.centerOn(target["scene_position"])
                return
            point = target["members"][0]
            self.chosen = point
            self.selected.emit(point)
            self.viewport().update()

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#101b26"))
        if not self.terrain.isNull():
            painter.drawPixmap(self.map_bounds, self.terrain, QRectF(self.terrain.rect()))
            painter.fillRect(self.map_bounds, QColor(7, 15, 25, 55))
        else:
            painter.fillRect(self.map_bounds, QColor("#1e3541"))
        scale = max(self.transform().m11(), 0.001)
        painter.setPen(QPen(QColor(211, 225, 236, 75), 0))
        for i in range(17):
            x = self.map_bounds.left() + self.map_bounds.width() * i / 16
            y = self.map_bounds.top() + self.map_bounds.height() * i / 16
            painter.drawLine(QPointF(x, self.map_bounds.top()), QPointF(x, self.map_bounds.bottom()))
            painter.drawLine(QPointF(self.map_bounds.left(), y), QPointF(self.map_bounds.right(), y))
        painter.setFont(QFont("Segoe UI", max(8, int(11 / scale))))
        if self.grid_verified:
            for i in range(16):
                x = self.map_bounds.left() + self.map_bounds.width() * (i + 0.5) / 16
                y = self.map_bounds.top() + self.map_bounds.height() * (i + 0.5) / 16
                painter.setPen(QColor("#edf5ff"))
                painter.drawText(QPointF(x, self.map_bounds.top() - 20 / scale), chr(65 + i))
                painter.drawText(QPointF(self.map_bounds.left() - 30 / scale, y), str(i + 1))

    def drawForeground(self, painter, rect):
        scale = max(self.transform().m11(), 0.001)
        viewport = self.mapToScene(self.viewport().rect()).boundingRect().adjusted(-20 / scale, -20 / scale, 20 / scale, 20 / scale)
        painter.save()
        transform = self.viewportTransform()
        markers = []
        areas = []
        paired_drills = {value(p, "raw", {}).get("_drill", {}).get("id") for p in self.points if kind_group(p) == "deep" and str(value(p, "drill_status", "")).startswith("已放置")}
        for point in self.points:
            p = QPointF(float(value(point, "x", 0)) / 100, float(value(point, "y", 0)) / 100)
            region = region_of(point)
            if not viewport.contains(p) and (region is None or not viewport.intersects(region)):
                continue
            group = kind_group(point)
            if region is not None:
                areas.append((point, transform.mapRect(region)))
                continue
            if group == "drill" and value(point, "id") in paired_drills:
                # The deposit's drill badge represents this exact linked machine.
                continue
            markers.append((point, p, transform.map(p)))
        painter.resetTransform()
        # Fixed-size icons near the world edge must not spill into the outside
        # margin; their geographic centres remain unchanged.
        painter.setClipRect(transform.mapRect(self.map_bounds), Qt.ClipOperation.IntersectClip)
        self._hit_targets = []
        for point, region in areas:
            color = QColor(COLORS.get(kind_group(point), "#75cee0"))
            color.setAlpha(28)
            painter.fillRect(region, color)
            color.setAlpha(85)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(region)
            self._hit_targets.append({"rect": region, "members": [point], "scene_position": QPointF(value(point, "x", 0) / 100, value(point, "y", 0) / 100)})
        # User preference: every icon stays exactly at its geographic position,
        # including when icons overlap. No displacement, leader lines or clusters.
        display_markers = [(point, position) for point, _, position in markers]
        for base in self.all_bases:
            p = transform.map(QPointF(base["x"] / 100, base["y"] / 100))
            display_markers.append((base, p))
        for point, position in display_markers:
            self._draw_marker(painter, point, position, scale)
        if self.chosen is not None:
            p = transform.map(QPointF(float(value(self.chosen, "x", 0)) / 100, float(value(self.chosen, "y", 0)) / 100))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(p, 21, 21)
        # Mouse targets follow the same clipping as the visible markers.
        clip = transform.mapRect(self.map_bounds)
        for target in self._hit_targets:
            target["rect"] = target["rect"].intersected(clip)
        self._hit_targets = [target for target in self._hit_targets if not target["rect"].isEmpty()]
        painter.restore()

    @staticmethod
    def _marker_label(point, scale):
        group = kind_group(point)
        if group == "deep" and scale >= 0.32:
            style = mineral_style(str(value(point, "resource_type", "")))
            return style.label + ("礦" if not style.label.endswith("礦") and style.label not in ("未知", "超冷冰", "凍木", "石材", "黏土", "石灰石") else "")
        if group == "base":
            return str(value(point, "name", "基地"))
        if group == "cave" and scale >= 0.6:
            name = str(value(point, "name", "地穴"))
            return name if name.startswith("地穴 ") and len(name) < 14 else "地穴"
        if group == "player" and scale >= 0.6:
            return str(value(point, "name", "玩家"))
        return ""

    @staticmethod
    def _marker_size(point, scale):
        drilled = str(value(point, "drill_status", "")).startswith("已放置") or value(point, "drill_status") in ("installed", "inferred_installed")
        return 32 if scale >= 0.32 or kind_group(point) in ("player", "base", "drill") or drilled else 26

    def _draw_marker(self, painter, point, position, scale):
        group = kind_group(point)
        candidate = str(value(point, "confidence", "")) in ("candidate", "estimated", "inferred", "推定", "候選")
        drilled = str(value(point, "drill_status", "")).startswith("已放置") or value(point, "drill_status") in ("installed", "inferred_installed")
        size = self._marker_size(point, scale)
        pixmap = marker_pixmap(group, str(value(point, "resource_type", "")), size=size, candidate=candidate, drilled=drilled)
        icon_rect = QRectF(position.x() - size / 2, position.y() - size / 2, size, size)
        highlighted = group == "deep" and drilled
        if highlighted:
            painter.setPen(QPen(QColor("#ff4e59"), 3))
            painter.setBrush(QColor(255, 52, 67, 65))
            painter.drawEllipse(position, size / 2 + 6, size / 2 + 6)
        painter.drawPixmap(icon_rect, pixmap, QRectF(pixmap.rect()))
        hit_rect = icon_rect.adjusted(-7 if highlighted else -3, -7 if highlighted else -3, 7 if highlighted else 3, 7 if highlighted else 3)
        label = self._marker_label(point, scale)
        if label:
            font = QFont("Microsoft JhengHei UI")
            font.setPixelSize(12)
            font.setBold(True)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(label) + 12
            text_rect = QRectF(position.x() - width / 2, position.y() + size / 2 + 2, width, 21)
            painter.setBrush(QColor(76, 15, 26, 245) if highlighted else QColor(7, 17, 25, 235))
            painter.setPen(QPen(QColor("#ff6470") if highlighted else QColor("#9cc5d7"), 1.4 if highlighted else 0.7))
            painter.drawRoundedRect(text_rect, 4, 4)
            painter.setPen(QColor("#f4faff"))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
            hit_rect = hit_rect.united(text_rect)
        self._hit_targets.append({"rect": hit_rect, "members": [point], "scene_position": QPointF(value(point, "x", 0) / 100, value(point, "y", 0) / 100)})

    def grid(self, x, y):
        if not self.grid_verified:
            return "格位未校準"
        col = int((x / 100 - self.map_bounds.left()) / self.map_bounds.width() * 16)
        row = int((y / 100 - self.map_bounds.top()) / self.map_bounds.height() * 16)
        return f"{chr(65 + col)}{row + 1}" if 0 <= col < 16 and 0 <= row < 16 else "地圖範圍外"
