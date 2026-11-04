"""Verified live/save animal and player positions on the calibrated terrain."""
from __future__ import annotations

import hashlib
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .map_icons import marker_pixmap
from .map_widget import WorldMap, value
from .animal_widgets import position_source_text, sample_time_text, symbol_icon


OWNER_COLORS = ("#74c9ef", "#d2a3fb", "#f0bc74", "#83d5ad", "#f49ab0", "#a5b9f7")


def owner_color(owner_name: str) -> str:
    """Stable across refreshes, sorting and processes; never expose owner IDs."""
    owner = str(owner_name or "").strip()
    if owner in ("", "Unknown", "Unknown owner", "unknown", "未知", "主人未知"):
        return "#99a9b7"
    index = int.from_bytes(hashlib.sha256(owner.encode("utf-8")).digest()[:4], "big")
    return OWNER_COLORS[index % len(OWNER_COLORS)]


def _valid_position(position):
    return (isinstance(position, (list, tuple)) and len(position) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in position))


def _animal_picture(species_id, size):
    # Keep an unrecognised species visibly unknown, including a damaged catalog.
    try:
        from .animal_icons import animal_pixmap
        return animal_pixmap(species_id, size=size)
    except (ImportError, OSError, ValueError):
        return marker_pixmap("resource", "", size=size)


class AnimalMapView(WorldMap):
    """Fixed-pixel portraits stay at their unmodified source coordinates."""

    def __init__(self):
        super().__init__()
        self.setAccessibleName("Animal and player map; scroll to zoom, drag to pan, click markers")
        self._animals_fit = False
        self.players = []

    def set_players(self, players):
        self.players = list(players)
        self._hit_targets = []
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_animals_fit", False):
            self.fit_animals()

    def zoom(self, factor):
        self._animals_fit = False
        super().zoom(factor)

    def focus_point(self, point):
        self._animals_fit = False
        super().focus_point(point)

    def fit_map(self):
        self._animals_fit = False
        super().fit_map()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._animals_fit = False
        super().mouseMoveEvent(event)
        target = self.hit_target(event.position())
        if target and target.get("members"):
            point = target["members"][0]
            prefix = "Player " if point.get("kind") == "player" else ""
            self.viewport().setToolTip(prefix + str(point.get("name") or "Unnamed") + "\n" + position_source_text(point))
        else:
            self.viewport().setToolTip("")

    def fit_animals(self):
        locations = [(point["x"] / 100, point["y"] / 100) for point in [*self.points, *self.players]]
        if not locations:
            if self.base:
                self.focus_point(self.base)
                self.chosen = None
            else:
                self.fit_map()
            return
        xmin, xmax = min(p[0] for p in locations), max(p[0] for p in locations)
        ymin, ymax = min(p[1] for p in locations), max(p[1] for p in locations)
        pad = max(80, max(xmax - xmin, ymax - ymin) * 0.18)
        self._animals_fit = True
        self._fit_mode = False
        self._fit_pending = False
        self.fitInView(QRectF(xmin - pad, ymin - pad, xmax - xmin + pad * 2,
                             ymax - ymin + pad * 2), Qt.AspectRatioMode.KeepAspectRatio)
        self.viewport().update()

    def drawForeground(self, painter, rect):
        transform = self.viewportTransform()
        clip = transform.mapRect(self.map_bounds)
        painter.save()
        painter.resetTransform()
        painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
        self._hit_targets = []
        for base in self.all_bases:
            pos = transform.map(QPointF(base["x"] / 100, base["y"] / 100))
            picture = marker_pixmap("base", size=30)
            painter.drawPixmap(QRectF(pos.x() - 15, pos.y() - 15, 30, 30), picture,
                               QRectF(picture.rect()))
        chosen_id = value(self.chosen, "id") if self.chosen else None
        # Selected portrait draws last and is the top mouse target at overlaps.
        ordered = sorted(self.points, key=lambda p: p["id"] == chosen_id)
        for point in ordered:
            pos = transform.map(QPointF(point["x"] / 100, point["y"] / 100))
            if not QRectF(self.viewport().rect()).adjusted(-120, -60, 120, 60).contains(pos):
                continue
            selected = point["id"] == chosen_id
            color = QColor(owner_color(point.get("owner_key") or point.get("owner_name") or "unknown"))
            source_style = Qt.PenStyle.SolidLine if point.get("position_source") == "live" else Qt.PenStyle.DashLine
            painter.setPen(QPen(QColor("#ffffff") if selected else color, 2.6 if selected else 1.8, source_style))
            painter.setBrush(QColor(9, 21, 31, 242))
            painter.drawEllipse(pos, 21, 21)
            picture = _animal_picture(point.get("species_id", ""), 36)
            icon_rect = QRectF(pos.x() - 18, pos.y() - 18, 36, 36)
            painter.drawPixmap(icon_rect, picture, QRectF(picture.rect()))
            font = QFont("Microsoft JhengHei UI")
            font.setPixelSize(11)
            font.setBold(selected)
            painter.setFont(font)
            label = str(point.get("name", "Unnamed animal"))
            short = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 132)
            width = painter.fontMetrics().horizontalAdvance(short) + 14
            label_rect = QRectF(pos.x() - width / 2, pos.y() + 24, width, 21)
            painter.setPen(QPen(color, 0.9))
            painter.setBrush(QColor(8, 20, 30, 242))
            painter.drawRoundedRect(label_rect, 4, 4)
            painter.setPen(QColor("#f1f6fb"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, short)
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(pos, 25, 25)
            hit = QRectF(pos.x() - 24, pos.y() - 24, 48, 48).united(label_rect).intersected(clip)
            if not hit.isEmpty():
                self._hit_targets.append({"rect": hit, "members": [point],
                                          "scene_position": QPointF(point["x"] / 100, point["y"] / 100)})
        for point in sorted(self.players, key=lambda player: player["id"] == chosen_id):
            pos = transform.map(QPointF(point["x"] / 100, point["y"] / 100))
            if not QRectF(self.viewport().rect()).adjusted(-120, -60, 120, 60).contains(pos):
                continue
            selected = point["id"] == chosen_id
            color = QColor("#70e5f5" if point.get("local") is True else "#f4f7fb")
            source_style = Qt.PenStyle.SolidLine if point.get("position_source") == "live" else Qt.PenStyle.DashLine
            box = QRectF(pos.x() - 19, pos.y() - 19, 38, 38)
            painter.setPen(QPen(color, 2.6 if selected else 1.8, source_style))
            painter.setBrush(QColor(12, 26, 38, 245))
            painter.drawRoundedRect(box, 6, 6)
            picture = marker_pixmap("player", size=30)
            painter.drawPixmap(QRectF(pos.x() - 15, pos.y() - 15, 30, 30), picture, QRectF(picture.rect()))
            font = QFont("Microsoft JhengHei UI")
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            label = ("You · " if point.get("local") is True else "Player ") + str(point.get("name") or "Unnamed")
            short = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 150)
            width = painter.fontMetrics().horizontalAdvance(short) + 14
            label_rect = QRectF(pos.x() - width / 2, pos.y() + 23, width, 21)
            painter.setBrush(QColor(9, 23, 35, 245))
            painter.drawRoundedRect(label_rect, 4, 4)
            painter.setPen(color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, short)
            hit = box.adjusted(-4, -4, 4, 4).united(label_rect).intersected(clip)
            if not hit.isEmpty():
                self._hit_targets.append({"rect": hit, "members": [point],
                                          "scene_position": QPointF(point["x"] / 100, point["y"] / 100)})
        painter.restore()


class AnimalMapPanel(QWidget):
    animal_selected = Signal(str)
    player_selected = Signal(object)

    def __init__(self):
        super().__init__()
        self.snapshot = None
        self.summary = None
        self.base = None
        self.world_id = None
        self.animals = []
        self.players = []
        self._selected_id = None
        self.map = AnimalMapView()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        title = QLabel("Animal and player positions")
        title.setStyleSheet("font-size:15px;font-weight:600;color:#e6d5ad")
        toolbar.addWidget(title)
        toolbar.addStretch()
        for text, tooltip, action in (("＋", "Zoom in", lambda: self.map.zoom(1.4)),
                                      ("－", "Zoom out", lambda: self.map.zoom(1 / 1.4)),
                                      ("All positions", "Show filtered animals and all players", self.map.fit_animals),
                                      ("⌂", "Go to the selected base", self.go_home),
                                      ("Full map", "Fit full map", self.map.fit_map)):
            button = QPushButton(text)
            if text == "⌂":
                button.setText("")
                button.setIcon(symbol_icon("home"))
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.status = QLabel("Select a host save to view animal positions")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        legend = QHBoxLayout()
        player_icon = QLabel()
        player_icon.setPixmap(marker_pixmap("player", size=18))
        legend.addWidget(player_icon)
        legend.addWidget(QLabel("Squares: players"))
        self.legend = QLabel("Circles: animals in owner colors · Solid: game · Dashed: save")
        self.legend.setObjectName("muted")
        self.legend.setWordWrap(True)
        legend.addWidget(self.legend, 1)
        layout.addLayout(legend)
        layout.addWidget(self.map, 1)
        self.position_note = QLabel("")
        self.position_note.setObjectName("muted")
        self.position_note.setWordWrap(True)
        layout.addWidget(self.position_note)
        self.map.selected.connect(self._point_selected)

    def clear(self):
        self.snapshot = self.summary = self.base = self.world_id = None
        self.animals = []
        self.players = []
        self._selected_id = None
        self.map.chosen = None
        self.map.set_points([])
        self.map.set_players([])
        self.map.viewport().setToolTip("")
        self.map.set_bases([])
        self.map.set_catalog({"map": "", "calibrated": False})
        self.map.fit_map()
        self.status.setText("Select a host save to view animal positions")
        self.position_note.setText("")
        self.position_note.setToolTip("")
        self.status.setToolTip("")

    def set_context(self, snapshot, summary, base=None, catalog=None):
        if snapshot is None or value(snapshot, "is_shared_map", False):
            self.clear()
            if snapshot is not None:
                self.status.setText("No host save connected; the shared map has no private animal positions")
            return
        new_world = value(snapshot, "world_id")
        changed_world = self.snapshot is None or self.world_id != new_world
        self.snapshot, self.summary, self.world_id = snapshot, summary, new_world
        same_world = base and value(base, "world_id", new_world) == new_world
        same_map = base and (not value(base, "map_name") or value(base, "map_name") == value(snapshot, "map_name"))
        self.base = base if same_world and same_map else None
        if changed_world:
            self._selected_id = None
            self.map.chosen = None
            self.position_note.setText("")
            self.position_note.setToolTip("")
        self.map.set_catalog(catalog or {"map": value(snapshot, "map_name", ""), "calibrated": False})
        bases = [self.base] if self.base and _valid_position([self.base.get("x"), self.base.get("y"), self.base.get("z", 0)]) else []
        self.map.set_bases(bases, bases[0] if bases else None)
        self.set_players((summary or {}).get("players", []))
        self.set_animals((summary or {}).get("animals", []), fit=changed_world)

    def set_players(self, players):
        if self.snapshot is None or value(self.snapshot, "is_shared_map", False):
            self.players = []
            self.map.set_players([])
            return
        parsed, duplicate = {}, set()
        for player in players if isinstance(players, list) else []:
            if not isinstance(player, dict):
                continue
            ident, pos = player.get("id"), player.get("position")
            if not isinstance(ident, str) or not ident.startswith("player:") or not _valid_position(pos):
                continue
            if ident in parsed:
                duplicate.add(ident)
            parsed[ident] = {**player, "kind": "player", "x": pos[0], "y": pos[1], "z": pos[2]}
        self.players = [point for ident, point in parsed.items() if ident not in duplicate]
        self.map.set_players(self.players)

    def set_animals(self, animals, fit=False):
        if self.snapshot is None:
            return
        self.animals = list(animals)
        old_selected = self.map.chosen
        points = []
        for animal in self.animals:
            pos = animal.get("position")
            if not _valid_position(pos) or not animal.get("id"):
                continue
            sampled = animal.get("position_sample_time")
            if not sampled and animal.get("position_source") != "live":
                sampled = value(self.snapshot, "saved_at")
            points.append({**animal, "id": str(animal["id"]), "kind": "animal", "position_sample_time": sampled,
                           "x": pos[0], "y": pos[1], "z": pos[2]})
        self.map.set_points(points)
        self.map.chosen = next((p for p in points if p["id"] == self._selected_id), None)
        if self._selected_id is not None:
            self.select_animal(self._selected_id, focus=False)
        if self.map.chosen is not None and old_selected is not None and self.map.chosen["id"] == old_selected.get("id"):
            if any(self.map.chosen[k] != old_selected[k] for k in ("x", "y", "z")):
                self.map.focus_point(self.map.chosen)
        if fit:
            self.map.fit_animals()
        saved_at = value(self.snapshot, "saved_at", "")
        saved_text = sample_time_text(saved_at or (self.summary or {}).get("coverage", {}).get("snapshot_time"))
        missing = len(self.animals) - len(points)
        name = str(value(self.snapshot, "map_name", ""))
        locations = [*points, *self.players]
        live = [point for point in locations if point.get("position_source") == "live"]
        times = sorted({position_source_text(point).split(" · ", 1)[-1] for point in live})
        live_note = f" · Game positions {len(live)} ({', '.join(times)})" if live else ""
        saved_count = len(locations) - len(live)
        self.status.setText(f"{name} · Animals located: {len(points)}" + (f" · Unknown positions: {missing}" if missing else "")
                            + f" · Players {len(self.players)}" + live_note
                            + (f" · Saved positions {saved_count}" if saved_count else "") + f" · Save baseline: {saved_text}")
        self.status.setToolTip("Solid outlines show verified game position samples; dashed outlines show saved positions. Samples are read every 30 seconds, not continuously.\n"
                               "Animal circle colors identify owners; players use human icons and squares. Missing positions are not plotted."
                               + ("\nThis map grid has not been calibrated." if not self.map.grid_verified else ""))

    def select_animal(self, animal_id, focus=True):
        self._selected_id = str(animal_id) if animal_id is not None else None
        self.position_note.setToolTip("")
        point = next((p for p in self.map.points if p["id"] == self._selected_id), None)
        self.map.chosen = point
        if point is None:
            animal = next((a for a in self.animals if str(a.get("id")) == self._selected_id), None)
            self.position_note.setText(f"{animal.get('name', 'This animal')} · Position unknown" if animal else "")
        else:
            self._show_position_note(point)
            if focus:
                self.map.focus_point(point)
        self.map.viewport().update()
        return point is not None

    def _point_selected(self, point):
        if point.get("kind") == "player":
            self._show_position_note(point)
            self.player_selected.emit(dict(point))
            return
        self.select_animal(point["id"], focus=False)
        self.animal_selected.emit(str(point["id"]))

    def _show_position_note(self, point):
        prefix = "Player " if point.get("kind") == "player" else ""
        text = prefix + str(point.get("name") or "Unnamed") + " · " + self.map.grid(point["x"], point["y"])
        text += " · " + position_source_text(point, value(self.snapshot, "saved_at"))
        distance = point.get("distance_m")
        if isinstance(distance, (int, float)) and not isinstance(distance, bool) and math.isfinite(distance):
            text += f" · From base: {distance:,.0f} m (horizontal straight-line distance)"
        self.position_note.setText(text)
        tooltip = f"X {point['x']:,.0f} · Y {point['y']:,.0f} · Z {point['z']:,.0f} cm\n" + position_source_text(point, value(self.snapshot, "saved_at"))
        saved = point.get("saved_position")
        if point.get("position_source") == "live" and _valid_position(saved):
            tooltip += f"\nLast saved X {saved[0]:,.0f} · Y {saved[1]:,.0f} · Z {saved[2]:,.0f} cm"
        self.position_note.setToolTip(tooltip + "\nHorizontal straight-line distance is not a walking route.")

    def go_home(self):
        if self.map.base:
            chosen = self.map.chosen
            self.map.focus_point(self.map.base)
            self.map.chosen = chosen
            self.map.viewport().update()
