"""One map for unlocked and active operations, with exact mission selection.

Eligibility belongs to the mission model. This projection excludes completed,
unverified and SMPL3 entries and keeps every retained location in place.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .map_icons import marker_pixmap
from .mission_locations import build_mission_locations
from .mission_map import MissionMapView, candidate_point


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _xyz(point):
    if not isinstance(point, dict):
        return None
    xyz = [point.get("x"), point.get("y"), point.get("z", 0)]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in xyz):
        return None
    return tuple(float(n) for n in xyz)


def _mission_order(mission):
    order = mission.get("order")
    return (float(order) if isinstance(order, (int, float)) and not isinstance(order, bool)
            and math.isfinite(order) else math.inf, str(mission.get("id", "")))


def _mission_color(ident):
    # Stable across refreshes and unrelated missions appearing/disappearing.
    hue = int.from_bytes(sha256(ident.encode("utf-8")).digest()[:2], "little") % 360
    return QColor.fromHsv(hue, 125, 238).name()


def build_operation_overview(snapshot: Any, missions: list[dict]) -> dict:
    """Project the selected snapshot's unlocked and active Operation/NPC sites.

    No file, process or network access occurs here. A missing location stays in
    the selectable mission list; no origin or base coordinate is substituted.
    """
    coverage = {"mission_count": 0, "located_mission_count": 0,
                "unlocated_mission_count": 0, "point_count": 0,
                "known_point_count": 0, "candidate_point_count": 0,
                "pending_stage_count": 0, "ready_mission_count": 0,
                "waiting_mission_count": 0, "active_mission_count": 0}
    result = {"missions": [], "points": [], "unlocated": [], "coverage": coverage,
              "summary": "", "world_id": _get(snapshot, "world_id"),
              "snapshot_sha256": _get(snapshot, "sha256", "")}
    if snapshot is None or not _get(snapshot, "source_path") or _get(snapshot, "is_shared_map", False):
        result["summary"] = "No valid host save connected; current mission state is unavailable."
        return result
    map_name = _get(snapshot, "map_name", "")
    if not map_name or not result["world_id"]:
        result["summary"] = "The current world identity or map is unconfirmed; mission locations cannot be shown."
        return result
    seen_missions = set()
    for mission in sorted(missions, key=_mission_order):
        mid = str(mission.get("id") or "")
        if (not mid or mid in seen_missions or mission.get("kind") not in {"Operation", "NPC"}
                or mid.startswith("DYN_") or mission.get("status_key") not in {"available", "active"}
                or mission.get("map_name", map_name) not in {map_name, "All"}
                or mission.get("world_id") and mission["world_id"] != result["world_id"]):
            continue
        seen_missions.add(mid)
        # Older callers omit can_start_now. Explicit false/unknown evidence
        # must never turn an unlocked operation into an immediately startable one.
        availability = ("active" if mission["status_key"] == "active" else
                        "ready" if mission.get("can_start_now", True) is True else "waiting")
        display_status = {"ready": "Ready to accept", "waiting": "Unlocked, waiting", "active": "In progress"}[availability]
        route = build_mission_locations(snapshot, mission)
        pending = {str(p.get("stage") or p.get("id") or i): dict(p)
                   for i, p in enumerate(route.get("pending_stages", []))
                   if isinstance(p, dict) and p.get("status") != "completed"}
        points, seen = [], set()
        for index, raw in enumerate(route.get("points", [])):
            if not isinstance(raw, dict) or raw.get("status") == "completed":
                continue
            stage = str(raw.get("stage") or raw.get("id") or f"stage-{index}")
            xyz = _xyz(raw)
            confidence = raw.get("confidence")
            if xyz is None or confidence not in {"known", "candidate"}:
                pending.setdefault(stage, {"stage": stage, "label": raw.get("label", "Mission stage"),
                                           "reason": "No mission location is confirmed yet."})
                continue
            identity = (stage, xyz, confidence)
            if identity in seen:
                continue
            seen.add(identity)
            point = dict(raw)
            point.update(id=f"overview:{mid}:{raw.get('id') or index}", x=xyz[0], y=xyz[1], z=xyz[2],
                         mission_id=mid, mission_title=str(mission.get("title", mid)),
                         mission_kind=mission["kind"], mission_status=mission["status_key"],
                         mission_display_status=display_status,
                         start_block_reason=str(mission.get("start_block_reason") or ""),
                         mission_color=_mission_color(mid), mission_number=len(result["missions"]) + 1,
                         name=str(mission.get("title", mid)), stage=stage)
            points.append(point)
        for stage in {point["stage"] for point in points}:
            pending.pop(stage, None)
        entry = dict(mission)
        entry.update(overview_point_count=len(points), overview_pending_count=len(pending),
                     overview_candidate_count=sum(candidate_point(p) for p in points),
                     overview_availability=availability, overview_display_status=display_status,
                     overview_color=_mission_color(mid), overview_pending_stages=list(pending.values()))
        result["missions"].append(entry)
        coverage[f"{availability}_mission_count"] += 1
        result["points"].extend(points)
        coverage["pending_stage_count"] += len(pending)
        if not points:
            result["unlocated"].append({"id": mid, "title": str(mission.get("title", mid)),
                                        "reason": "Mission selectable; location unresolved. Updates after accepting or progressing.",
                                        "pending_stage_count": len(pending)})
    coverage.update(mission_count=len(result["missions"]),
                    unlocated_mission_count=len(result["unlocated"]), point_count=len(result["points"]),
                    known_point_count=sum(p["confidence"] == "known" for p in result["points"]),
                    candidate_point_count=sum(p["confidence"] == "candidate" for p in result["points"]))
    coverage["located_mission_count"] = coverage["mission_count"] - coverage["unlocated_mission_count"]
    result["summary"] = (f"Ready: {coverage['ready_mission_count']} · "
                         f"Unlocked, waiting: {coverage['waiting_mission_count']} · "
                         f"In progress: {coverage['active_mission_count']} · Total: {coverage['mission_count']} missions · "
                         f"Located: {coverage['located_mission_count']} · "
                         f"Unlocated: {coverage['unlocated_mission_count']} · "
                         f"Stage points: {coverage['known_point_count']} confirmed / {coverage['candidate_point_count']} candidate")
    return result


class OperationOverviewMap(MissionMapView):
    """Fixed-size operation badges, exact locations and overlap choice menus."""

    def __init__(self):
        super().__init__()
        self.setAccessibleName("Overview map of unlocked and active missions; select a mission icon to open its guide")
        self.selected_mission_id = None
        self._projection_generation = 0

    def set_points(self, points):
        self._projection_generation = getattr(self, "_projection_generation", 0) + 1
        super().set_points(points)

    def drawForeground(self, painter, rect):
        transform = self.viewportTransform()
        painter.save()
        painter.resetTransform()
        painter.setClipRect(transform.mapRect(self.map_bounds).intersected(QRectF(self.viewport().rect())))
        self._hit_targets = []
        groups = defaultdict(list)
        for point in self.points:
            groups[(point["x"], point["y"], point.get("z", 0))].append(point)
        grouped = list(groups.values())
        grouped.sort(key=lambda members: any(p["mission_id"] == self.selected_mission_id for p in members))
        for members in grouped:
            point = next((p for p in members if p["mission_id"] == self.selected_mission_id), members[-1])
            pos = transform.map(QPointF(point["x"] / 100, point["y"] / 100))
            if not QRectF(self.viewport().rect()).adjusted(-300, -60, 300, 60).contains(pos):
                continue
            color = QColor(point["mission_color"])
            painter.setPen(QPen(QColor("#07141e"), 5))
            painter.setBrush(QColor("#142737"))
            painter.drawEllipse(pos, 16, 16)
            painter.setPen(QPen(color, 2.5, Qt.PenStyle.DashLine if candidate_point(point) else Qt.PenStyle.SolidLine))
            painter.drawEllipse(pos, 16, 16)
            font = QFont("Segoe UI")
            font.setPixelSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(pos.x() - 15, pos.y() - 15, 30, 30), Qt.AlignmentFlag.AlignCenter,
                             str(point["mission_number"]))
            if candidate_point(point):
                painter.setBrush(QColor("#ffd06a"))
                painter.setPen(QPen(QColor("#18222b"), 1))
                badge = QRectF(pos.x() + 8, pos.y() - 23, 16, 16)
                painter.drawEllipse(badge)
                font.setPixelSize(10)
                painter.setFont(font)
                painter.setPen(QColor("#17202a"))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "?")
            other_count = len({p["mission_id"] for p in members}) - 1
            label = point["mission_title"] + (f" (+{other_count} more)" if other_count else "")
            font = QFont("Segoe UI UI")
            font.setPixelSize(11)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(label) + 12
            label_rect = QRectF(pos.x() - width / 2, pos.y() + 20, width, 23)
            painter.setPen(QPen(color, 0.8))
            painter.setBrush(QColor(9, 21, 31, 240))
            painter.drawRoundedRect(label_rect, 3, 3)
            painter.setPen(QColor("#f3f8fc"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
            self._hit_targets.append({"rect": QRectF(pos.x() - 19, pos.y() - 19, 38, 38).united(label_rect),
                                      "members": members, "scene_position": QPointF(point["x"] / 100, point["y"] / 100)})
            if point["mission_id"] == self.selected_mission_id:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.drawEllipse(pos, 21, 21)
        # Bases stay above operation badges, matching the resource-map priority.
        for base in self.all_bases:
            xyz = _xyz(base)
            if xyz is None:
                continue
            pos = transform.map(QPointF(xyz[0] / 100, xyz[1] / 100))
            icon = marker_pixmap("base", size=30)
            painter.drawPixmap(QRectF(pos.x() - 15, pos.y() - 15, 30, 30), icon, QRectF(icon.rect()))
            self._hit_targets.append({"rect": QRectF(pos.x() - 16, pos.y() - 16, 32, 32),
                                      "members": [base], "scene_position": QPointF(xyz[0] / 100, xyz[1] / 100)})
        painter.restore()

    def choose_overlap(self, members, global_position):
        menu = QMenu(self)
        for point in sorted(members, key=lambda p: (p["mission_title"], str(p.get("label", "")))):
            confidence = "Candidate" if candidate_point(point) else "Confirmed"
            action = menu.addAction(f"{point['mission_title']} · {point['mission_display_status']} · "
                                    f"{point.get('label', 'Mission location')} ({confidence})")
            action.setToolTip(point.get("start_block_reason", ""))
            action.setData(point)
        selected = menu.exec(global_position)
        return selected.data() if selected is not None else None

    def mouseReleaseEvent(self, event):
        # The base map zooms overlapping markers. Here the user must be able to
        # choose the exact operation without moving or replacing its marker.
        QGraphicsView.mouseReleaseEvent(self, event)
        if (event.position().toPoint() - self._press).manhattanLength() > 6:
            return
        if event.button() == Qt.MouseButton.RightButton:
            pos = self.mapToScene(event.position().toPoint())
            self.positionPicked.emit(pos.x() * 100, pos.y() * 100)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self.hit_target(event.position())
        if target is None:
            return
        members = target["members"]
        generation = self._projection_generation
        if len({p.get("mission_id") for p in members}) > 1:
            point = self.choose_overlap(members, event.globalPosition().toPoint())
        else:
            point = members[-1]
        # A menu runs a nested Qt event loop. The world or valid snapshot can
        # change while it is open; its old action data must then be discarded.
        if generation != self._projection_generation:
            return
        if point is not None:
            self.chosen = point
            if point.get("mission_id"):
                self.selected_mission_id = point["mission_id"]
            self.selected.emit(point)
            self.viewport().update()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        target = self.hit_target(event.position())
        if target and any(p.get("mission_id") for p in target["members"]):
            names = [f"{p['mission_title']} · {p['mission_display_status']} · {p.get('label', 'Mission location')}" +
                     (" (candidate location; confirm after accepting)" if candidate_point(p) else " (confirmed by save)") +
                     (f"\n{p['start_block_reason']}" if p.get("start_block_reason") else "")
                     for p in target["members"]]
            prefix = "Overlapping markers; select a mission:\n" if len({p["mission_id"] for p in target["members"]}) > 1 else "Select to open the mission guide\n"
            self.viewport().setToolTip(prefix + "\n".join(names))


class OperationOverviewPanel(QWidget):
    missionSelected = Signal(str)

    def __init__(self):
        super().__init__()
        self.overview = {}
        self.world_id = None
        self.map_name = None
        self.selected_mission_id = None
        self.active_base = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        heading = QLabel("Mission overview map")
        heading.setStyleSheet("font-size:16px;font-weight:600;color:#f6c45f")
        toolbar.addWidget(heading)
        toolbar.addStretch()
        self.map = OperationOverviewMap()
        for label, action in [("＋", lambda: self.map.zoom(1.4)), ("－", lambda: self.map.zoom(1 / 1.4)),
                              ("All missions", self.map.fit_targets), ("Full map", self.map.fit_map), ("Home", self.focus_home)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.summary = QLabel("Load a world save to show unlocked and active missions")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.map)
        self.listing = QListWidget()
        self.listing.setAccessibleName("All unlocked and active Operations and NPC missions")
        self.listing.setWordWrap(True)
        self.listing.currentItemChanged.connect(self._list_selected)
        splitter.addWidget(self.listing)
        splitter.setSizes([520, 145])
        layout.addWidget(splitter, 1)
        self.note = QLabel("Solid outlines show confirmed sites; dashed question marks show candidates. Select an icon or name to open the guide.")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.map.selected.connect(self._point_selected)

    def clear(self):
        self.world_id = None
        self.map_name = None
        self.selected_mission_id = None
        self.active_base = None
        self.overview = {}
        self.map.selected_mission_id = None
        self.map.chosen = None
        self.map.set_points([])
        self.map.set_bases([])
        self.map.set_catalog({"map": "", "calibrated": False})
        self.map.regions = []
        self.map._targets_fit = False
        self.listing.blockSignals(True)
        self.listing.clear()
        self.listing.blockSignals(False)
        self.summary.setText("No valid host save connected; current mission state is unavailable.")
        self.note.setText("")

    def set_missions(self, snapshot, missions, catalog, bases, active_base=None):
        overview = build_operation_overview(snapshot, missions)
        if (snapshot is None or not _get(snapshot, "source_path") or _get(snapshot, "is_shared_map", False)
                or not _get(snapshot, "world_id") or not _get(snapshot, "map_name")):
            self.clear()
            self.summary.setText(overview["summary"])
            return
        world_id = str(_get(snapshot, "world_id", ""))
        same_world = self.world_id == world_id and self.map_name == _get(snapshot, "map_name")
        previous = self.selected_mission_id if same_world else None
        self.overview, self.world_id = overview, world_id
        self.map_name = _get(snapshot, "map_name")
        valid_bases = [base for base in bases if isinstance(base, dict) and _xyz(base) is not None
                       and (not base.get("world_id") or base["world_id"] == world_id)
                       and (not base.get("map_name") or base["map_name"] == _get(snapshot, "map_name"))]
        self.active_base = (active_base if isinstance(active_base, dict) and _xyz(active_base) is not None
                            and (not active_base.get("world_id") or active_base["world_id"] == world_id)
                            and (not active_base.get("map_name") or active_base["map_name"] == self.map_name) else None)
        self.map.set_catalog(catalog)
        self.map.set_bases(valid_bases, selected=self.active_base)
        self.map.set_points(overview["points"])
        self.listing.blockSignals(True)
        self.listing.clear()
        for index, mission in enumerate(overview["missions"], 1):
            confidence = f"{mission['overview_point_count']} stage points" if mission["overview_point_count"] else "Location unresolved"
            if mission["overview_candidate_count"]:
                confidence += f" (including {mission['overview_candidate_count']} candidates)"
            status = mission["overview_display_status"]
            item = QListWidgetItem(f"{index}. {mission.get('title', mission['id'])}\n{status} · {confidence}")
            item.setData(Qt.ItemDataRole.UserRole, mission)
            pixmap = QPixmap(14, 14)
            pixmap.fill(QColor(mission["overview_color"]))
            item.setIcon(QIcon(pixmap))
            pending = "\n".join(f"{p.get('label', 'Stage')}: {p.get('reason', 'Location unconfirmed')}" for p in mission["overview_pending_stages"])
            item.setToolTip("\n".join(str(text) for text in
                                     (mission.get("start_block_reason"), mission.get("status_reason"), pending)
                                     if text))
            self.listing.addItem(item)
        self.listing.blockSignals(False)
        self.summary.setText(overview["summary"])
        self.note.setText("Solid outlines show confirmed sites; dashed question marks show candidates. Select an icon or name to open the guide. Overlapping missions can be selected; distances are horizontal straight-line references only.")
        self.selected_mission_id = None
        self.map.selected_mission_id = None
        self.map.chosen = None
        if previous is not None:
            self.select_mission(previous)
        if not same_world:
            self.map.fit_targets()

    def select_mission(self, ident, focus=False):
        """Select without emitting, so the main window can synchronise safely."""
        item = next((self.listing.item(i) for i in range(self.listing.count())
                     if self.listing.item(i).data(Qt.ItemDataRole.UserRole)["id"] == ident), None)
        if item is None:
            self.listing.blockSignals(True)
            self.listing.setCurrentRow(-1)
            self.listing.blockSignals(False)
            self.selected_mission_id = None
            self.map.selected_mission_id = None
            self.map.chosen = None
            self.map.viewport().update()
            return False
        self.listing.blockSignals(True)
        self.listing.setCurrentItem(item)
        self.listing.blockSignals(False)
        self.selected_mission_id = ident
        self.map.selected_mission_id = ident
        points = [p for p in self.map.points if p["mission_id"] == ident]
        self.map.chosen = points[0] if points else None
        if focus and points:
            self.map.focus_point(points[0])
        self.map.viewport().update()
        return True

    def _list_selected(self, item, previous=None):
        if item is not None:
            ident = item.data(Qt.ItemDataRole.UserRole)["id"]
            self.select_mission(ident)
            self.missionSelected.emit(ident)

    def _point_selected(self, point):
        ident = point.get("mission_id")
        if ident:
            self.select_mission(ident)
            self.map.chosen = point
            self.missionSelected.emit(ident)

    def focus_home(self):
        if self.active_base is not None:
            self.map.focus_point(self.active_base)
