"""Bounded groups of nearby mission stages, preserving location uncertainty.

Groups describe horizontal proximity, not routes or permission to run operations
concurrently.  A nearby stage never stands in for the rest of a mission.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import heapq
import json
import math
from typing import Any

from .mission_locations import build_mission_locations


def _get(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _xyz(point: Any) -> tuple[float, float, float] | None:
    if not isinstance(point, dict):
        return None
    values = [point.get("x"), point.get("y"), point.get("z", 0)]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in values):
        return None
    return tuple(float(n) for n in values)


def _distance(first: dict, second: dict) -> float:
    return math.hypot(first["x"] - second["x"], first["y"] - second["y"]) / 100


def _point_key(point: dict) -> tuple:
    return (point["mission_id"], str(point.get("stage", "")), str(point.get("id", "")),
            point["x"], point["y"], point["z"])


def _order(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else math.inf


def _complete_link(points: list[dict], maximum: float) -> list[list[dict]]:
    """Agglomerate the nearest clusters; every retained pair stays in bounds.

    Complete-link distances update with max(old distances), so a long chain of
    individually close points cannot grow beyond the requested total span.
    """
    points = sorted(points, key=_point_key)
    active = {i: [point] for i, point in enumerate(points)}
    distances = {}
    queue = []
    for i, first in enumerate(points):
        for j in range(i + 1, len(points)):
            distance = _distance(first, points[j])
            distances[i, j] = distance
            if distance <= maximum:
                heapq.heappush(queue, (distance, i, j))
    next_id = len(points)
    while queue:
        _, first, second = heapq.heappop(queue)
        if first not in active or second not in active:
            continue
        merged = active.pop(first) + active.pop(second)
        for other in active:
            distance = max(distances[min(first, other), max(first, other)],
                           distances[min(second, other), max(second, other)])
            distances[other, next_id] = distance
            if distance <= maximum:
                heapq.heappush(queue, (distance, other, next_id))
        active[next_id] = merged
        next_id += 1
    return [sorted(group, key=_point_key) for group in active.values()]


def build_mission_clusters(snapshot: Any, missions: list[dict], base: dict | None = None,
                           max_span_m: float = 1200) -> dict:
    """Group eligible missions' stages without mutating them or their snapshot.

    Known and candidate locations are grouped independently. Candidates are
    alternatives, never a claim that all anchors must be visited. Completed
    stages and missions outside the selected world/map cannot enter a group.
    """
    result = {"groups": [], "unclustered": [], "summary": "", "max_span_m": max_span_m,
              "distance_type": "Horizontal straight-line distance", "eligible_mission_count": 0,
              "grouped_mission_count": 0, "base_status": "missing"}
    if (isinstance(max_span_m, bool) or not isinstance(max_span_m, (int, float))
            or not math.isfinite(max_span_m) or max_span_m <= 0):
        result["max_span_m"] = None
        result["summary"] = "Choose a nearby range greater than zero."
        return result
    result["max_span_m"] = float(max_span_m)
    if _get(snapshot, "is_shared_map", False):
        result["summary"] = "The shared map has no host save; mission availability and groups cannot be determined."
        return result
    map_name = str(_get(snapshot, "map_name", "") or "")
    world_id = str(_get(snapshot, "world_id", "") or "")
    if not map_name:
        result["summary"] = "The selected world's map is unavailable; mission groups cannot be planned."
        return result
    base_xyz = _xyz(base)
    if base_xyz:
        result["base_status"] = "known"
        if base.get("world_id") and str(base["world_id"]) != world_id:
            base_xyz = None
            result["base_status"] = "world_mismatch"
        elif base.get("map_name") and base["map_name"] != map_name:
            base_xyz = None
            result["base_status"] = "map_mismatch"

    eligible = {}
    for mission in sorted(missions, key=lambda m: (str(m.get("id", "")), str(m.get("title", "")))):
        mid = str(mission.get("id", "") or "")
        if (not mid or mission.get("kind") == "SMPL3" or mid.startswith("DYN_")
                or mission.get("status_key") not in {"available", "active"}
                or mission.get("map_name", map_name) not in {map_name, "All"}
                or mission.get("world_id") and str(mission["world_id"]) != world_id):
            continue
        eligible.setdefault(mid, mission)
    result["eligible_mission_count"] = len(eligible)
    locations = {}
    pools = {"known": [], "candidate": []}
    for mid, mission in eligible.items():
        route = build_mission_locations(snapshot, mission)
        points, seen = [], set()
        pending = {str(stage.get("stage") or stage.get("id")): dict(stage)
                   for stage in route.get("pending_stages", [])
                   if isinstance(stage, dict) and stage.get("status") != "completed"}
        for point in route.get("points", []):
            if not isinstance(point, dict) or point.get("status") == "completed":
                continue
            xyz = _xyz(point)
            stage = str(point.get("stage") or point.get("id") or "unknown-stage")
            confidence = point.get("confidence")
            if xyz is None or confidence not in pools:
                pending.setdefault(stage, {"stage": stage, "label": point.get("label", "Stage location unconfirmed"),
                                           "reason": "This stage has no verifiable coordinates or location confidence."})
                continue
            unique = (stage, confidence, xyz)
            if unique in seen:
                continue
            seen.add(unique)
            item = {**point, "x": xyz[0], "y": xyz[1], "z": xyz[2], "stage": stage,
                    "mission_id": mid, "mission_title": str(mission.get("title", mid))}
            points.append(item)
            pools[confidence].append(item)
        # A stage with a usable position need not also be counted as unlocated.
        for stage in {point["stage"] for point in points}:
            pending.pop(stage, None)
        locations[mid] = {"points": points, "pending": pending}

    for confidence, pool in pools.items():
        for points in _complete_link(pool, float(max_span_m)):
            mission_ids = sorted({point["mission_id"] for point in points})
            if len(mission_ids) < 2:
                continue
            span = max((_distance(a, b) for i, a in enumerate(points) for b in points[i + 1:]), default=0.0)
            center = {axis: sum(point[axis] / len(points) for point in points) for axis in ("x", "y", "z")}
            members = []
            inside_keys = {_point_key(point) for point in points}
            for mid in mission_ids:
                inside = defaultdict(list)
                for point in points:
                    if point["mission_id"] == mid:
                        inside[point["stage"]].append(point)
                outside = defaultdict(list)
                for point in locations[mid]["points"]:
                    # Several selected-world actors can belong to one stage;
                    # an outside confirmed actor still requires another place.
                    # Only unselected candidate anchors are alternatives.
                    if (_point_key(point) not in inside_keys
                            and (point["stage"] not in inside or point["confidence"] == "known")):
                        outside[point["stage"]].append(point)
                stages = [{"id": stage, "label": stage_points[0].get("label", stage),
                           "order": min((_order(p.get("order")) for p in stage_points), default=math.inf),
                           "confidence": confidence, "point_ids": [p.get("id") for p in stage_points]}
                          for stage, stage_points in inside.items()]
                stages.sort(key=lambda stage: (stage["order"], stage["id"]))
                # Avoid Infinity leaking into JSON when an unsupported source omits order.
                for stage in stages:
                    if not math.isfinite(stage["order"]):
                        stage["order"] = None
                members.append({"id": mid, "title": str(eligible[mid].get("title", mid)), "stages": stages,
                                "other_stage_count": len(outside),
                                "other_stages": [{"id": stage, "label": stage_points[0].get("label", stage)}
                                                 for stage, stage_points in sorted(outside.items())],
                                "pending_stage_count": len(locations[mid]["pending"]),
                                "pending_stages": list(locations[mid]["pending"].values()),
                                "alternative_point_count": sum(point["confidence"] == "candidate" and point["stage"] in inside and _point_key(point) not in inside_keys
                                                               for point in locations[mid]["points"])})
            limitations = ["Groups use horizontal straight-line distance only. Terrain, cave entrances and traversable routes have not been verified.",
                           "These are nearby-stage suggestions. Check acceptance order and concurrent-mission limits at the game device; one-trip completion is not guaranteed."]
            if confidence == "candidate":
                limitations.append("Candidate points are possible areas. Confirm the actual objective after accepting; multiple candidates for one stage are alternatives, not required stops.")
            if any(member["other_stage_count"] for member in members):
                limitations.append("Some missions have stages outside the group and may require travel between regions.")
            if any(member["pending_stage_count"] for member in members):
                limitations.append("Some stages remain unlocated. Groups will update when later locations appear.")
            token = json.dumps([world_id, map_name, confidence, [_point_key(point) for point in points]],
                               ensure_ascii=False, separators=(",", ":"))
            scores = [eligible[mid].get("recommendation_score", 0) for mid in mission_ids]
            scores = [float(score) if isinstance(score, (int, float)) and not isinstance(score, bool)
                      and math.isfinite(score) else 0.0 for score in scores]
            result["groups"].append({"id": "cluster-" + hashlib.sha256(token.encode()).hexdigest()[:16],
                                     "title": "Confirmed nearby area" if confidence == "known" else "Candidate nearby area",
                                     "confidence": confidence, "mission_ids": mission_ids, "missions": members,
                                     "points": points, "center": center, "span_m": span,
                                     "base_distance_m": math.hypot(center["x"] - base_xyz[0], center["y"] - base_xyz[1]) / 100 if base_xyz else None,
                                     "recommendation_score": sum(scores) / len(scores),
                                     "limitations": limitations, "complete_trip_confirmed": False})
    result["groups"].sort(key=lambda group: (0 if group["confidence"] == "known" else 1,
                                            -group["recommendation_score"],
                                            group["base_distance_m"] if group["base_distance_m"] is not None else math.inf,
                                            -len(group["mission_ids"]), group["span_m"], group["id"]))
    grouped = {mid for group in result["groups"] for mid in group["mission_ids"]}
    result["grouped_mission_count"] = len(grouped)
    for index, group in enumerate(result["groups"], 1):
        group["title"] += f" {index}"
    for mid, mission in eligible.items():
        if mid in grouped:
            continue
        result["unclustered"].append({"id": mid, "title": str(mission.get("title", mid)),
                                      "reason": "No unfinished stage has a usable location yet; update after accepting or progressing." if not locations[mid]["points"]
                                      else "No other available mission stage with the same location confidence is within this range."})
    if not eligible:
        result["summary"] = "No confirmed available or active missions can currently be grouped."
    elif not result["groups"]:
        result["summary"] = f"Checked {len(eligible)} available or active missions; no nearby-stage combinations are recommended within {max_span_m:g} m."
    else:
        result["summary"] = f"Of {len(eligible)} available or active missions, {len(grouped)} have nearby stages in {len(result['groups'])} groups. Grouping does not imply one-trip completion."
    return result
