"""Compact animal cards and honest, icon-led snapshot gauges."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .animal_icons import animal_pixmap


STAT_STYLE = {
    "health": ("生命", "#f1848e"), "food": ("食物", "#dfba68"),
    "water": ("水分", "#66c5ef"), "oxygen": ("氧氣", "#99cfe0"),
    "stamina": ("耐力", "#a6db84"), "experience": ("經驗", "#b6a0f4"),
    "milk": ("乳量", "#e7ded0"),
}


def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def number_text(value):
    if not valid_number(value):
        return "?"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def paint_stat_icon(painter, key, rect, color):
    """Small vector symbols, independent of fonts and emoji availability."""
    painter.save()
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 20, rect.height() / 20)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(color))
    path = QPainterPath()
    if key == "health":
        path.moveTo(10, 17)
        path.cubicTo(1, 11, 1, 5, 5, 4)
        path.cubicTo(8, 3, 10, 6, 10, 6)
        path.cubicTo(14, 1, 19, 4, 18, 8)
        path.cubicTo(18, 12, 13, 15, 10, 17)
        painter.drawPath(path)
    elif key in ("water", "milk"):
        path.moveTo(10, 2)
        path.cubicTo(7, 7, 3, 10, 5, 15)
        path.cubicTo(7, 20, 16, 18, 16, 13)
        path.cubicTo(16, 9, 12, 5, 10, 2)
        painter.drawPath(path)
    elif key == "food":
        path.moveTo(4, 16)
        path.cubicTo(1, 7, 8, 3, 17, 3)
        path.cubicTo(18, 12, 12, 17, 4, 16)
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#28322b"), 1.5))
        painter.drawLine(QPointF(4, 16), QPointF(13, 7))
    elif key == "stamina":
        for index, (x, y) in enumerate([(12, 1), (4, 11), (10, 11), (8, 19), (17, 8), (11, 8)]):
            path.lineTo(x, y) if index else path.moveTo(x, y)
        path.closeSubpath()
        painter.drawPath(path)
    elif key == "experience":
        for index in range(10):
            angle = math.pi * index / 5 - math.pi / 2
            radius = 8 if index % 2 == 0 else 3.6
            point = QPointF(10 + radius * math.cos(angle), 10 + radius * math.sin(angle))
            path.lineTo(point) if index else path.moveTo(point)
        path.closeSubpath()
        painter.drawPath(path)
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(2, 3, 12, 12))
        painter.drawEllipse(QRectF(13, 12, 5, 5))
    painter.restore()


def symbol_icon(kind, color="#a9c9da", size=20):
    picture = QPixmap(size, size)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 20, size / 20)
    painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if kind == "home":
        painter.drawLine(2, 9, 10, 2)
        painter.drawLine(10, 2, 18, 9)
        painter.drawLine(4, 8, 4, 18)
        painter.drawLine(4, 18, 16, 18)
        painter.drawLine(16, 18, 16, 8)
        painter.drawRect(8, 12, 4, 6)
    elif kind == "away":
        painter.drawLine(4, 16, 16, 4)
        painter.drawLine(8, 4, 16, 4)
        painter.drawLine(16, 4, 16, 12)
    else:
        painter.drawEllipse(QRectF(4, 4, 12, 12))
        painter.drawEllipse(QRectF(8, 8, 4, 4))
        for a, b, c, d in [(10, 0, 10, 5), (10, 15, 10, 20), (0, 10, 5, 10), (15, 10, 20, 10)]:
            painter.drawLine(a, b, c, d)
    painter.end()
    return QIcon(picture)


class AnimalGauge(QWidget):
    """A ratio only when a verified maximum exists; hatching means unknown."""

    def __init__(self, stat, progress=None, parent=None):
        super().__init__(parent)
        self.stat = dict(stat)
        self.progress = progress
        self.key = str(stat.get("id", ""))
        self.label, self.color = STAT_STYLE.get(self.key, (stat.get("label", "狀態"), "#a8b7c4"))
        self.value, self.maximum = stat.get("value"), stat.get("maximum")
        self.reconstructed = stat.get("maximum_status") == "reconstructed"
        self.persistent = stat.get("maximum_status") == "persistent"
        self.capped = False
        if self.key == "experience" and progress:
            self.value, self.maximum = progress.get("value"), progress.get("maximum")
            self.capped = progress.get("capped") is True
        self.has_capacity = valid_number(self.value) and valid_number(self.maximum) and self.maximum > 0
        self.over_capacity = self.has_capacity and self.value > self.maximum
        self.ratio_known = self.has_capacity and not self.over_capacity
        self.setFixedHeight(27)
        self.setMinimumWidth(100)
        self.setAccessibleName(f"{self.label}：{self.display_text()}")
        detail = str(stat.get("detail") or "")
        if stat.get("maximum_status") == "live":
            detail += "\n遊戲讀取時間：" + str(stat.get("sample_time") or "未知")
        if self.key == "experience" and progress:
            detail = ("已達目前可顯示的最高等級" if self.capped else f"本級經驗 {number_text(self.value)} / {number_text(self.maximum)}；下一級 {progress.get('next_level')}") + f"；累積經驗 {number_text(progress.get('total'))}"
        elif self.over_capacity:
            detail += "\n目前值高於此個體基準，代表仍有未涵蓋的加成；不顯示為 100% 或更改分母。"
        elif not self.ratio_known:
            detail += "\n上限未知，斜紋不代表百分比。" if valid_number(self.value) else "\n存檔數值未知，不能當作零。"
        self.setToolTip(f"{self.label} · {self.display_text()}\n{detail}")

    def display_text(self):
        if self.capped:
            return "MAX"
        if self.has_capacity:
            mark = "≈" if self.reconstructed else ""
            return f"{number_text(self.value)} / {mark}{number_text(self.maximum)}" + ("*" if self.persistent else "") + (" ↑" if self.over_capacity else "")
        return f"{number_text(self.value)} / ?"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_stat_icon(painter, self.key, QRectF(0, 4, 18, 18), self.color)
        track = QRectF(25, 4, self.width() - 26, 19)
        path = QPainterPath()
        path.addRoundedRect(track, 5, 5)
        painter.fillPath(path, QColor("#0c1721"))
        painter.save()
        painter.setClipPath(path)
        if self.ratio_known or self.capped:
            ratio = 1 if self.capped else min(1, self.value / self.maximum)
            color = QColor(self.color)
            color.setAlpha(115)
            painter.fillRect(QRectF(track.x(), track.y(), track.width() * ratio, track.height()), color)
        elif not self.over_capacity:
            color = QColor(self.color)
            color.setAlpha(45)
            painter.setPen(QPen(color, 2))
            for x in range(int(track.x()) - 25, int(track.right()) + 25, 9):
                painter.drawLine(QPointF(x, track.bottom()), QPointF(x + 19, track.top()))
        painter.restore()
        if self.reconstructed or self.persistent:
            painter.setPen(QPen(QColor(self.color), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track, 5, 5)
        painter.setPen(QColor("#dce6ef"))
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        painter.setFont(font)
        while painter.fontMetrics().horizontalAdvance(self.display_text()) > track.width() - 6 and font.pointSizeF() > 7:
            font.setPointSizeF(font.pointSizeF() - .5)
            painter.setFont(font)
        painter.setFont(font)
        painter.drawText(track, Qt.AlignmentFlag.AlignCenter, self.display_text())


class AnimalCard(QFrame):
    selected = Signal(str)
    locate = Signal(str)

    def __init__(self, animal, color, home_state, parent=None):
        super().__init__(parent)
        self.animal = animal
        self.ident = str(animal.get("id", ""))
        self.color = color
        self.setObjectName("animalCard")
        self.setMinimumWidth(245)
        self.setMaximumWidth(360)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{animal.get('name')}，{animal.get('species')}，飼養者 {animal.get('owner_name')}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        portrait = QLabel()
        portrait.setObjectName("animalPortrait")
        portrait.setPixmap(animal_pixmap(animal.get("species_id", ""), 62))
        portrait.setFixedSize(64, 64)
        portrait.setToolTip(str(animal.get("species") or "Unknown species"))
        portrait.setStyleSheet("background:transparent")
        header.addWidget(portrait)
        titles = QVBoxLayout()
        name = QLabel(str(animal.get("name") or "未命名動物"))
        name.setWordWrap(True)
        name.setStyleSheet("font-size:16px;font-weight:600;background:transparent")
        titles.addWidget(name)
        species = QLabel(f"{animal.get('species') or 'Unknown species'}  ·  Lv {number_text(animal.get('level'))}")
        species.setStyleSheet("color:#a6b8c8;font-size:12px;background:transparent")
        species.setToolTip(str(animal.get("level_note") or ""))
        titles.addWidget(species)
        header.addLayout(titles, 1)
        layout.addLayout(header)
        grid = QGridLayout()
        grid.setSpacing(6)
        stats = {s.get("id"): s for s in animal.get("stats", []) if isinstance(s, dict)}
        self.gauges = {}
        for i, key in enumerate(("health", "food", "water", "oxygen", "stamina", "experience")):
            stat = stats.get(key, {"id": key, "value": None, "maximum": None})
            gauge = AnimalGauge(stat, animal.get("experience_progress") if key == "experience" else None)
            self.gauges[key] = gauge
            grid.addWidget(gauge, i // 2, i % 2)
        layout.addLayout(grid)
        footer = QHBoxLayout()
        distance = animal.get("distance_m")
        state_icon = QLabel()
        state_icon.setPixmap(symbol_icon("home" if home_state == "home" else "away" if valid_number(distance) else "locate", size=16).pixmap(16, 16))
        state_icon.setFixedSize(16, 16)
        state_icon.setStyleSheet("background:transparent")
        footer.addWidget(state_icon)
        label = number_text(distance) + " m" if valid_number(distance) else "距離未知"
        location = QLabel(label)
        location.setStyleSheet("color:#8faabd;background:transparent;font-size:12px")
        location.setToolTip("距離所選基地的水平直線距離；不是步行路線。")
        footer.addWidget(location, 1)
        locate = QPushButton()
        locate.setIcon(symbol_icon("locate", color))
        locate.setAccessibleName(f"地圖定位 {animal.get('name')}")
        locate.setToolTip("在動物地圖定位")
        locate.setFixedSize(32, 26)
        locate.setEnabled(isinstance(animal.get("position"), (list, tuple)) and len(animal["position"]) == 3)
        locate.clicked.connect(lambda: self.locate.emit(self.ident))
        footer.addWidget(locate)
        layout.addLayout(footer)
        status = str(animal.get("status_label") or "狀態未知")
        self.setToolTip(status)
        self.set_selected(False)

    def set_selected(self, selected):
        edge = self.color if selected else "#304352"
        self.setStyleSheet(f"QFrame#animalCard {{background:{'#203443' if selected else '#172632'};border:1px solid {edge};border-left:3px solid {self.color};border-radius:9px;}}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.ident)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and isinstance(self.animal.get("position"), (list, tuple)) and len(self.animal["position"]) == 3:
            self.locate.emit(self.ident)
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected.emit(self.ident)
            event.accept()
            return
        super().keyPressEvent(event)
