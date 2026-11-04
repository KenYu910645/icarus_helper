"""Presentation helpers for the mission list and nearby-stage plans."""
from __future__ import annotations

from collections import Counter
from html import escape
from urllib.parse import quote


def availability_summary(missions, shared=False):
    if shared:
        return "No host save connected. Select a world to show available missions and locations."
    operations = [m for m in missions if m.get("kind") != "SMPL3" and not str(m.get("id", "")).startswith("DYN_")]
    counts = Counter(m.get("status_key") for m in operations)
    ready = sum(m.get("status_key") == "available" and m.get("can_start_now", True) for m in operations)
    waiting = counts['available'] - ready
    text = f"Ready to accept: {ready} · Unlocked, waiting: {waiting} · In progress: {counts['active']}"
    blocks = list(dict.fromkeys(m.get("start_block_reason") for m in operations
                               if m.get("status_key") == "available" and m.get("start_block_reason")))
    if blocks:
        text += ". " + "; ".join(blocks) + ". "
    if counts["unknown"]:
        text += f" · {counts['unknown']} excluded from availability because their sources are unconfirmed"
    return text


def mission_catalog_order(mission):
    order = mission.get("order")
    order = order if isinstance(order, (int, float)) else float("inf")
    return order, str(mission.get("id", ""))


def cluster_html(group):
    esc = lambda value: escape(str(value))
    confidence = "Confirmed location" if group["confidence"] == "known" else "Candidate location; verify again after accepting"
    parts = ['<html><body style="color:#e8ecf2;font-family:Segoe UI;line-height:1.6">',
             f"<h2>{esc(group['title'])}</h2><p>{confidence}</p>",
             f"<p>Maximum horizontal straight-line distance within this group: {group['span_m']:,.0f} m. This is not a walking route.</p>"]
    if group.get("base_distance_m") is not None:
        parts.append(f"<p>Selected base to group center: approximately {group['base_distance_m']:,.0f} m (horizontal straight-line distance; center is a planning reference).</p>")
    parts.append("<p><b>Plan nearby stages together. A group does not mean every mission can be accepted together or completed in one trip.</b></p>")
    for mission in group["missions"]:
        target = "mission:/" + quote(mission["id"], safe="")
        parts.append(f'<h3><a style="color:#f6c25b" href="{target}">{esc(mission["title"])}</a></h3><ul>')
        for stage in mission["stages"]:
            parts.append(f"<li>{esc(stage['label'])}</li>")
        parts.append("</ul>")
        parts.append(f"<p>Stages outside group (including candidates): {mission['other_stage_count']}; unlocated stages: {mission['pending_stage_count']}. Select the mission name for the full guide and locations.</p>")
        for stage in mission.get("other_stages", []):
            parts.append(f"<p>Outside group: {esc(stage['label'])}</p>")
    if group.get("limitations"):
        parts.append("<ul>" + "".join(f"<li>{esc(note)}</li>" for note in group["limitations"]) + "</ul>")
    parts.append("</body></html>")
    return "".join(parts)


def cluster_map_route(group):
    points = []
    for index, raw in enumerate(group["points"], 1):
        point = dict(raw)
        point.update(number=index, order=index,
                     label=f"{raw['mission_title']} · {raw['label']}",
                     description=f"{raw['mission_title']} · {raw['label']}. " + ("Candidate location; verify again after accepting." if group["confidence"] != "known" else "Mission location in the current save."))
        points.append(point)
    pending = []
    for index, mission in enumerate(group["missions"], len(points) + 1):
        if mission["other_stage_count"] or mission["pending_stage_count"]:
            pending.append({"id": f"{group['id']}:{mission['id']}:outside", "order": index,
                            "label": mission["title"],
                            "reason": f"Also includes {mission['other_stage_count']} stages outside the group (including candidates) and {mission['pending_stage_count']} unlocated stages. Open the mission guide for all locations."})
    return {"points": points, "regions": [], "pending_stages": pending}
