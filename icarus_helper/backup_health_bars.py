"""Snapshot health bars with no invented maximum or percentage."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem


def health_bar_state(player):
    value = player.get("health")
    known = (isinstance(value, (int, float)) and not isinstance(value, bool)
             and math.isfinite(value) and value >= 0)
    text = (f"{value:,.0f}" if value == int(value) else f"{value:,.2f}".rstrip("0").rstrip(".")) if known else "?"
    return {"value": value if known else None, "text": text + " / ?",
            "state": "empty" if known and value == 0 else "saved" if known else "unknown",
            "ratio": None, "maximum": None}


def draw_health_bar(painter, rect, player):
    """A striped track is an unknown denominator, never a filled fraction."""
    state = health_bar_state(player)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    track = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    shape = QPainterPath()
    shape.addRoundedRect(track, 4, 4)
    painter.fillPath(shape, QColor("#131c27"))
    color = QColor("#ef7586") if state["state"] != "unknown" else QColor("#8192a3")
    if state["state"] != "empty":
        painter.save()
        painter.setClipPath(shape)
        stripes = QColor(color)
        stripes.setAlpha(80 if state["state"] == "saved" else 45)
        painter.setPen(QPen(stripes, 2))
        for x in range(int(track.left()) - 30, int(track.right()) + 30, 9):
            painter.drawLine(QPointF(x, track.bottom()), QPointF(x + track.height(), track.top()))
        painter.restore()
    painter.setPen(QPen(color, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(track, 4, 4)
    painter.setPen(QColor("#f6e7eb") if state["state"] != "unknown" else QColor("#c0cbd4"))
    font = painter.font()
    font.setBold(True)
    font.setPointSizeF(9)
    painter.setFont(font)
    painter.drawText(track, Qt.AlignmentFlag.AlignCenter, state["text"])
    painter.restore()


def health_bar_image(player, width=320, height=26):
    picture = QImage(max(80, int(width)), max(20, int(height)), QImage.Format.Format_ARGB32_Premultiplied)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    draw_health_bar(painter, QRectF(0, 0, picture.width(), picture.height()), player)
    painter.end()
    return picture


def player_rows_height(players):
    return max(58, len(players) * 38 + 8)


class PlayerHealthDelegate(QStyledItemDelegate):
    """Paint bars directly, without hundreds of per-cell child widgets."""

    def __init__(self, players_for_version, parent=None):
        super().__init__(parent)
        self.players_for_version = players_for_version

    def paint(self, painter, option, index):
        version = index.data(Qt.ItemDataRole.UserRole)
        players = self.players_for_version(version)
        if not players:
            return super().paint(painter, option, index)
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(option.palette.highlightedText().color() if selected else option.palette.text().color())
        width = max(70, min(420, option.rect.width() - 16))
        for offset, player in enumerate(players):
            y = option.rect.top() + 4 + offset * 38
            state = "Alive" if player.get("alive") is True else "Dead" if player.get("alive") is False else "Survival status unknown"
            title = f"{player.get('name') or 'Player'} · {state}"
            if player.get("is_currently_playing") is False:
                title += " · Last saved value"
            text_rect = QRectF(option.rect.left() + 8, y, option.rect.width() - 16, 15)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(text_rect.width())))
            draw_health_bar(painter, QRectF(option.rect.left() + 8, y + 16, width, 18), player)
        painter.restore()

    def sizeHint(self, option, index):
        players = self.players_for_version(index.data(Qt.ItemDataRole.UserRole))
        return QSize(300, player_rows_height(players))
