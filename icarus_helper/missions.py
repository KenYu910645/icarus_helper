"""Versioned mission catalogue, conservative save-state joining, and offline guides."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from functools import lru_cache
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any

from .catalog import installed_game_build
from .mission_text import display_mission_text
from .operation_eligibility import operation_talent_gate, operation_retry_gate, operation_start_block

DATA_PATH = Path(__file__).resolve().parent / "data" / "missions.json"
STATUS = {"active": "In progress", "available": "Available", "locked": "Locked", "completed": "Completed", "unknown": "Unconfirmed"}
STATUS_ORDER = {"active": 0, "available": 1, "unknown": 2, "locked": 3, "completed": 4}
MAP_ALIASES = {"Terrain_016": "Olympus", "Terrain_017": "Styx", "Terrain_019": "Prometheus", "Terrain_021": "Elysium"}
# Verified from the installed executable's reflected EMissionState enumerator table.
HISTORY_STATES = {0: "In progress", 1: "Completed", 2: "Abandoned", 3: "Failed"}


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    return value if isinstance(value, dict) else {}


def _mission_state(world: dict) -> tuple[list | None, list, dict, list]:
    state = world.get("mission_state") or {}
    records = state.get("records") or world.get("mission_records") or []
    history = state.get("mission_history")
    manager = state.get("quest_manager") or {}
    quests = list(state.get("quests") or [])
    for record in records:
        if "MissionHistory" in record:
            history = record["MissionHistory"]
        if "FactionMissionName" in record:
            manager = record
        if record.get("QuestName"):
            quests.append(record)
    quests = list({q.get("QuestName", str(i)): q for i, q in enumerate(quests)}.values())
    return history, records, manager, quests


def _mission_name(value: Any) -> str | None:
    """Unreal's FName None sentinel is not a currently selected mission."""
    if not isinstance(value, str) or value.strip().casefold() in {"", "none", "null"}:
        return None
    return value.strip()


def _quest_number(variable: dict) -> float | int | None:
    """Read the numeric slot without erasing zero or accepting invalid values.

    Saved records may include the unused numeric slot with its default zero.
    Distinct nonzero slots conflict; missing slots do not default to zero.
    """
    values = [variable[key] for key in ("fVariable", "iVariable") if key in variable]
    if not values or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                         or not math.isfinite(value) for value in values):
        return None
    nonzero = set(value for value in values if value != 0)
    return next(iter(nonzero)) if len(nonzero) == 1 else 0 if not nonzero else None


def _progress_text(qid: str, catalog: dict, mission: dict | None) -> str:
    """Shared NPC quest IDs do not share a mission's audited requirements."""
    fallback = catalog.get("quest_texts", {}).get(qid, qid)
    if mission is None:
        return display_mission_text(fallback)
    for key in ("source_objectives", "objectives"):
        for row in mission.get(key, []):
            if row.get("id") == qid:
                source = row.get("quantity_source")
                if source and mission.get("id") not in source.get("missions", []):
                    return display_mission_text(row.get("source_text", qid))
                return display_mission_text(row.get("text", qid))
    for row in mission.get("catalog_only_objectives", []):
        if row.get("id") == qid:
            label = display_mission_text(row.get("source_text", row.get("text", qid)))
            return label + " (Catalog-only objective; not required in this verified mission flow)"
    if "Required:" in fallback:
        # A global label may have been filled for another map's NPC. Recover
        # its unfilled source only; absence from this mission is not approval
        # to reuse another mission's fixed/difficulty-specific target.
        for other in catalog.get("missions", []):
            for key in ("source_objectives", "objectives", "catalog_only_objectives"):
                for row in other.get(key, []):
                    if row.get("id") == qid and row.get("source_text"):
                        return display_mission_text(row["source_text"])
        return f"{qid} (Quantity unconfirmed for this mission)"
    return display_mission_text(fallback)


def _progress(quest: dict, catalog: dict, mission: dict | None = None) -> dict:
    """Expose evidence without treating a persisted actor as an active objective."""
    qid = quest.get("QuestName", "")
    variables = [var for var in (quest.get("VariableRecords") or []) if isinstance(var, dict)]
    by_name = {v["VariableName"]: v for v in variables if isinstance(v.get("VariableName"), str)}
    explicit_done = any(by_name.get(k, {}).get("bVariable") for k in ("Complete", "Completed", "bComplete", "bCompleted"))
    reached = bool(by_name.get("LocationReached", {}).get("bVariable"))
    state = "Completed (explicit completion flag)" if explicit_done else "Reached (location flag)" if reached else "Record exists; completion unconfirmed"
    details = []
    for var in by_name.values():
        name = var.get("VariableName", "")
        if not isinstance(name, str):
            continue
        records = [row for row in variables if row.get("VariableName") == name]
        numbers = [_quest_number(row) for row in records]
        number = numbers[0] if numbers and all(value == numbers[0] for value in numbers) else None
        if name in {"CurrentTime", "ElapsedTime", "TimeElapsed", "ProgressTime"}:
            if number is None or number < 0:
                continue
            current = float(number)
            if qid == "PRO_Story_5_Outpost_Progress":
                current = min(600.0, max(0.0, current))
                details.append(f"Transmission {current:.0f}/600 s ({current / 6:.1f}%); {600-current:.0f} s of effective transmission remaining")
                state = "Transmitting" if current < 600 else "Transmission target reached"
            else:
                details.append(f"{name}: {current:g} s (raw field value)")
        elif any(key in name.lower() for key in ("count", "amount", "health", "durability", "progress")):
            if number is not None:
                details.append(f"{name}: {number:g} (no percentage without a verified target)")
            else:
                details.append(f"{name}: Unknown (missing, invalid or conflicting numeric value)")
    if qid == "PRO_Story_5_Outpost_Progress" and not details:
        details.append("This snapshot has no readable transmission timer; a countdown is unavailable")
    return {"id": qid, "text": _progress_text(qid, catalog, mission), "state": state,
            "complete": bool(explicit_done), "reached": reached, "details": details, "variables": variables}


def _next_step(mission: dict) -> str:
    progress = {q["id"]: q for q in mission.get("progress", [])}
    if mission["status_key"] == "completed":
        return "This mission is complete. Choose another mission from the unlock list."
    if mission["status_key"] != "active":
        return "Verify availability, then prepare the items in the checklist."
    if mission["id"] == "PRO_Story_5":
        if "PRO_Story_5_Run" in progress or "PRO_Story_5_Run_Timer" in progress:
            return "The save contains evacuation-stage records. Check the destruction countdown in the game immediately and leave the outpost; the game displays the exact timer."
        if "PRO_Story_5_Beacon_Place" in progress:
            return "Verify that transmission is complete and place the mission Beacon inside the outpost. Evacuate when the destruction warning appears."
        if "PRO_Story_5_Beacon_Craft" in progress:
            return "Craft the mission Beacon at the Machining Bench, then return it to the outpost using the game marker."
        transmission = progress.get("PRO_Story_5_Outpost_Progress")
        if transmission and not transmission["complete"]:
            return "Check power and communicator durability. Defend within approximately 38 m to advance transmission; " + ("; ".join(transmission["details"]) or "This snapshot has no readable countdown.")
        if "PRO_Story_5_Outpost_Trasmit" in progress:
            return "Verify 10000 W of power and defense preparations in the game, then interact with the data communicator to start transmission."
        if "PRO_Story_5_Outpost_Power" in progress:
            return "The save contains a power-stage record. Check 10000 W at the communicator; the save cannot establish whether the requirement is currently met. Prepare for combat before starting transmission."
        if progress.get("PRO_Story_5_Investigate_Enter", {}).get("reached"):
            return "The outpost interior has been reached. Find the data communicator, watch for explosive mines and prepare 10000 W of power."
        return "Investigate the marked outpost. Check mines, power and defense supplies before entering."
    completed = {q["id"] for q in mission.get("progress", []) if q.get("complete")}
    remaining = [q.get("text", "") for q in mission.get("objectives", []) if q.get("id") not in completed]
    if remaining:
        return "Check the next objective in the game: " + display_mission_text(remaining[0]) + ". This snapshot cannot establish every stage's order or completion conditions."
    return "Follow the current in-game objective. Saved quest actors do not prove that every recorded stage is still active."


def _terminal_level(world: dict) -> tuple[int | None, str]:
    """Only recognise installed communication modules inside the actual terminal."""
    records = world.get("raw_records") or []
    devices = [r for r in records if r.get("StaticItemDataRowName") == "Mission_Device"]
    if not devices:
        devices = [r.get("raw", r) for r in world.get("deployables", []) if isinstance(r, dict) and (r.get("item_id") == "Mission_Device" or r.get("StaticItemDataRowName") == "Mission_Device")]
    if not devices:
        return None, "C0NT4CT and its upgrade modules have not been confirmed in this save"
    level = 1
    for device in devices:
        for inventory in device.get("SavedInventories", []):
            for slot in inventory.get("Slots", []):
                name = slot.get("ItemStaticData", "")
                if name == "Mission_Communication_Device_T3":
                    level = max(level, 2)
                elif name == "Mission_Communication_Device_T4":
                    level = max(level, 3)
    return level, ["", "C0NT4CT base device confirmed", "C0NT4CT T3 upgrade module confirmed", "C0NT4CT T4 upgrade module confirmed"][level]


def _map_name(world: dict) -> str:
    info = world.get("prospect_info") or world.get("info") or {}
    key = str(world.get("map_name") or world.get("map_key") or world.get("prospect_key") or info.get("ProspectDTKey") or "")
    for terrain, name in MAP_ALIASES.items():
        if terrain in key or name.lower() in key.lower():
            return name
    if "Olympus" in key or "Outpost006" in key:
        return "Olympus"
    return key


def _coordinates(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z", 0)]
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = tuple(float(n) for n in value[:3])
        if len(point) == 2:
            point += (0.0,)
        return point if all(math.isfinite(n) for n in point) else None
    except (TypeError, ValueError):
        return None


def _distance(mission: dict, world: dict, context: dict) -> float:
    """Distance is to a labelled point, with ranking reserved for verified goals."""
    base = context.get("active_base") or {}
    base_xyz, point = _coordinates(base), _coordinates(mission.get("position"))
    mission["horizontal_distance_m"] = None
    mission["distance_affects_ranking"] = False
    mission.pop("distance_position", None)
    mission.pop("distance_target_id", None)
    mission["base_known"] = bool(base_xyz)
    mission["base_name"] = str(base.get("name") or "Selected base") if base_xyz else None
    mission["distance_status"] = "base_missing"
    mission["distance_note"] = "No base selected. Set Home on the map first."
    if not base_xyz:
        return 0.0
    if base.get("world_id") and base["world_id"] != world.get("world_id"):
        mission["base_known"] = False
        mission["distance_status"] = "base_world_mismatch"
        mission["distance_note"] = "The base belongs to another world; distance was not calculated."
        return 0.0
    if base.get("map_name") and base["map_name"] != _map_name(world):
        mission["base_known"] = False
        mission["distance_status"] = "base_map_mismatch"
        mission["distance_note"] = "The base belongs to another map; distance was not calculated."
        return 0.0
    verified_goal = mission.get("position_reliable") is True and mission.get("position_scope") == "objective"
    label = "Mission objective" if verified_goal else "Mission record point"
    mission["distance_status"] = "known_point" if point else "target_pending"
    if point is None:
        # Newly generated operation targets are represented by the route model,
        # not necessarily by a top-level QuestActor transform. Reuse that same
        # world's verified/candidate points rather than calling the base unknown.
        from .mission_locations import build_mission_locations
        route = build_mission_locations(world, mission)
        known, candidates = [], []
        for item in route.get("points", []):
            xyz = _coordinates(item)
            if xyz is None:
                continue
            if item.get("confidence") == "known":
                known.append((item, xyz))
            elif item.get("confidence") in ("candidate", "inferred", "estimated", "Candidate", "Inferred", "Estimated", "\u5019\u9078", "\u63a8\u5b9a", "\u4f30\u8a08"):
                candidates.append((item, xyz))
        options = known or candidates
        if options:
            target, point = min(options, key=lambda entry: math.hypot(entry[1][0] - base_xyz[0], entry[1][1] - base_xyz[1]))
            label = "Nearest mission area" if known else "Nearest candidate site"
            mission["distance_status"] = "known_area" if known else "candidate_area"
            mission["distance_position"] = list(point)
            mission["distance_target_id"] = target.get("id")
            verified_goal = False
    if point is None:
        mission["distance_note"] = f"Base selected: {mission['base_name']}. The mission location is unresolved; distance will appear once located."
        return 0.0
    distance = math.hypot(point[0] - base_xyz[0], point[1] - base_xyz[1]) / 100.0
    mission["horizontal_distance_m"] = distance
    mission["distance_note"] = f"Horizontal straight-line distance from {base.get('name', 'Selected base')}: approximately {distance:,.0f} m to {label}; this is not a walking route."
    mission["distance_affects_ranking"] = verified_goal
    if not verified_goal:
        mission["distance_note"] += "These coordinates are not verified as an actual objective or entrance, so distance does not affect ranking."
        return 0.0
    # Maximum twenty points: only a small preference within a status/goal group.
    return max(0.0, 20.0 - distance / 500.0)


def _party_note(world: dict) -> tuple[int | None, str]:
    info = world.get("prospect_info") or world.get("info") or {}
    members = info.get("AssociatedMembers") or info.get("associatedMembers") or []
    if members:
        count = len({str(m.get("UserID") or m.get("CharacterName") or i) for i, m in enumerate(members) if isinstance(m, dict)})
    else:
        players = world.get("players") or []
        count = len({str(p.get("id") or p.get("name") or i) for i, p in enumerate(players) if isinstance(p, dict)})
    if not count:
        return None, "Party size is unconfirmed. Adjust preparation quantities for your actual party and difficulty."
    return count, f"The save roster records {count} players; this is not a live participant count. Adjust supplies for your actual party."


def build_missions(world: Any, goal: str = "story", context: dict | None = None) -> list[dict]:
    """Join only the selected world's history/quests to its map's catalogue.

    Account talent evidence and world history have independent scopes and times.
    Missing source-account evidence never becomes an unlocked operation.
    """
    world = _dict(world)
    context = context or {}
    catalog = load_catalog()
    current_build = installed_game_build()
    source_build = str(catalog.get("source_build") or "")
    if not source_build:
        match = re.search(r"\bbuild\s+(\d+)\b", catalog.get("source_version", ""), re.I)
        source_build = match.group(1) if match else ""
    version_compatible = (current_build == source_build) if current_build and source_build else None
    if version_compatible is True:
        version_note = f"Game build {current_build} matches the mission catalog."
    elif version_compatible is False:
        version_note = f"Version mismatch: game build {current_build}, mission catalog build {source_build}. Guide mechanics and unlock rules need verification."
    else:
        version_note = f"Version unverified: game build {current_build or 'Unknown'}, mission catalog build {source_build or 'Unknown'}. Compatibility cannot be assumed."
    map_name = _map_name(world)
    info = world.get("prospect_info") or world.get("info") or {}
    history, records, manager, quests = _mission_state(world)
    known_history = history if isinstance(history, list) else []
    current = _mission_name(manager.get("FactionMissionName")) or _mission_name(manager.get("DynamicMissionProspectName"))
    active_current = current if not manager.get("bMissionComplete") else None
    completed = {h.get("Mission") for h in known_history if h.get("Status") == 1}
    past = {h.get("Mission"): h for h in known_history}
    start_block = operation_start_block(world)
    if active_current and not start_block:
        start_block = "Complete or end the current Operation before accepting another mission"
    level, terminal_reason = _terminal_level(world)
    difficulty = info.get("Difficulty", world.get("difficulty", "Unknown"))
    party_count, party_note = _party_note(world)
    results = []
    all_by_id = {m["id"]: m for m in catalog["missions"]}
    for original in catalog["missions"]:
        if original["map_name"] not in (map_name, "All"):
            continue
        mission = deepcopy(original)
        mid = mission["id"]
        requirements = mission.get("prerequisite_ids", [])
        talent_state, talent_reason = operation_talent_gate(world, mid)
        required_level = mission.get("terminal_level", 0)
        state_key, reason = "unknown", "Verified world mission and device data is missing"
        if world.get("is_shared_map"):
            reason = "No host save connected; mission state and account unlocks cannot be confirmed"
        elif mid == active_current:
            state_key, reason = "active", "This world's QuestManager is running this mission"
        elif mission.get("kind") == "SMPL3":
            state_key = "unknown"
            reason = "SMPL3 random assignment type; the current selection and difficulty have not been recovered from the device, so availability is unconfirmed"
        elif mid in completed or (mid == current and manager.get("bMissionComplete")):
            state_key, reason = "completed", "This world's MissionHistory / QuestManager explicitly records completion"
        elif level is not None and level < required_level:
            state_key, reason = "locked", f"Requires C0NT4CT upgrade level {required_level - 1}; {terminal_reason}"
        elif talent_state != "available":
            state_key = "locked" if talent_state in {"locked", "unlisted"} else "unknown"
            reason = talent_reason
        elif level is None:
            reason = terminal_reason
        else:
            state_key, reason = operation_retry_gate(world, mid, history)
            reason = terminal_reason + "; " + talent_reason + "; " + reason
        # RequiredFeatureLevel is the shipped content stage. The validated
        # build's UFeatureLevelLibrary compares its row index with the current
        # release stage; it does not check Steam DLC ownership. In particular,
        # the cross-map NPC operations shipped in the free Kepler update.
        if (mission.get("required_flags") or mission.get("required_level")) and state_key == "available":
            state_key = "unknown"
            reason += "; character-level / account-flag requirements still need verification"
        if state_key == "available" and start_block:
            reason += "; " + start_block
        if version_compatible is not True:
            if state_key not in {"active", "completed"}:
                state_key = "unknown"
            reason += "; " + version_note
        mission.update(status=STATUS[state_key], status_key=state_key, status_reason=reason, difficulty=difficulty)
        mission.update(prerequisite_evidence_scope="source_account_talents", talent_gate=talent_state,
                       prerequisite_gate_reason=talent_reason,
                       can_start_now=state_key == "available" and not start_block,
                       start_block_reason=start_block if state_key == "available" else "")
        if state_key == "available" and start_block:
            mission["status"] = "Unlocked, waiting"
        elif talent_state == "unlisted" and state_key == "locked":
            mission["status"] = "Unavailable in this version"
        last_history_status = past.get(mid, {}).get("Status")
        mission.update(history_completion_count=sum(h.get("Mission") == mid and h.get("Status") == 1 for h in known_history),
                       history_last_status=last_history_status,
                       history_last_state=HISTORY_STATES.get(last_history_status, "Not recorded"))
        mission.update(version_compatible=version_compatible, version_note=version_note,
                       installed_build=current_build, source_build=source_build or None)
        mission["prerequisites"] = [all_by_id.get(pid, {}).get("title", pid) for pid in requirements]
        mission["progress"] = []
        for quest in quests:
            qid = quest.get("QuestName", "")
            known_ids = mission.get("quest_ids", [])
            if qid not in known_ids and qid != mid:
                continue
            mission["progress"].append(_progress(quest, catalog, mission))
            xyz = quest.get("ActorTransform", {}).get("Translation")
            if xyz and any(abs(float(n)) > 0 for n in xyz) and "position" not in mission:
                mission["position"] = xyz
                mission["position_scope"] = "record_point"
                mission["position_reliable"] = False
                mission["position_note"] = "Mission-record actor coordinates; objective entrance / route not guaranteed"
        mission["next_step"] = _next_step(mission)
        score = {"active": 1000, "available": 500, "unknown": 100, "locked": -100, "completed": -500}[state_key]
        if state_key == "active":
            rec = "Finish the current Operation first to avoid splitting preparation supplies."
        elif state_key == "available":
            rec = "Account prerequisites and device upgrades are satisfied. Verify character technology and inventory against the checklist before leaving."
        elif state_key == "locked":
            rec = "Complete the listed prerequisites or upgrade the device first."
        elif state_key == "completed":
            rec = "Completed in this world; keep the guide for replay reference."
        else:
            rec = "Check availability and missing requirements at the game device before committing supplies."
        story = "_Story_" in mid or "_Story" in mid
        if goal in ("story", "Story", "Advance the story", "\u4e3b\u7dda", "\u63a8\u9032\u4e3b\u7dda") and story:
            score += 70
            rec += " This mission advances the story or regional unlocks."
        elif goal in ("safe", "Low risk", "\u4f4e\u98a8\u96aa"):
            score -= mission.get("risk_rank", 2) * 30
            rec += " Ranking favors missions with fewer defense and boss encounters."
        elif goal in ("resources", "Resources", "Resources / unlocks", "\u8cc7\u6e90", "\u53d6\u5f97\u8cc7\u6e90\uff0f\u89e3\u9396"):
            score += 30 if mission.get("kind") in ("NPC", "SMPL3") else 0
            rec += " Ranking favors trading and supply benefits."
        score += _distance(mission, world, context)
        if mission.get("horizontal_distance_m") is not None:
            rec += " " + mission["distance_note"]
        else:
            rec += " A base is selected, but the mission location is unresolved; distance does not affect ranking." if mission.get("base_known") else " No base is selected for this world; distance does not affect ranking."
        mission.update(recommendation_score=score, recommendation_reason=rec, recommendation=rec)
        mission["inventory_note"] = "Checklist confirmation is manual; base and character inventory have not been checked."
        mission["technology_note"] = (f"Mission-table technology requirement / recommendation: {mission['tech']}; the current character's unlock is unconfirmed." if mission.get("tech") not in (None, "None", "") else "Character technology unlocks are unconfirmed. Verify them against the checklist.")
        mission["party_size_recorded"] = party_count
        mission["party_note"] = party_note
        mission["source_version"] = catalog["source_version"]
        mission["source_date"] = catalog["source_date"]
        results.append(mission)
    results.sort(key=lambda m: (STATUS_ORDER[m["status_key"]], -m["recommendation_score"], m.get("order", 999), m["title"]))
    for i, mission in enumerate(results):
        mission["rank"] = i + 1
    return results


def _initial_objective_text(row: Any, mission_id: str | None) -> tuple[str, bool]:
    """Show verified guide targets as starting counters, never saved progress."""
    if not isinstance(row, dict):
        return str(row), False
    text = str(row.get("text", ""))
    quantity = row.get("required_quantity")
    source = row.get("quantity_source")
    if (type(quantity) is not int or quantity <= 0 or not isinstance(source, dict)
            or mission_id not in source.get("missions", [])):
        return text, False
    label = f"(Required: {quantity})"
    if label in text:
        return text.replace(label, f"(0/{quantity})"), True
    prefix = f"Required: {quantity} "
    if text.startswith(prefix):
        return f"(0/{quantity}) " + text[len(prefix):], True
    return text, False


def mission_html(mission: dict) -> str:
    """Render the requested gameplay guide; keep diagnostics in the data model."""
    def esc(value):
        return escape(display_mission_text(value))
    def bullets(items):
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>"
    parts = ["<html><head><style>body{font-family:'Segoe UI',sans-serif;color:#e8ecf2;} h1{font-size:23px;color:#ffffff;}h2{font-size:16px;color:#f6c25b;}p,li{line-height:1.6;} .muted{color:#aeb7c5;}table{border-collapse:collapse;}td,th{padding:7px;border-bottom:1px solid #384452;text-align:left;}</style></head><body>",
             f"<h1>{esc(mission['title'])}</h1>",
             f"<p>{esc(mission.get('map_name'))} · {esc(mission.get('kind'))} · <b>{esc(mission.get('status','Unconfirmed'))}</b></p>",
             f"<p>{esc(mission.get('summary',''))}</p>"]
    parts += ["<h2>Preparation</h2>", "<table><tr><th>Type</th><th>Item / requirement</th><th>Quantity</th><th>Notes</th></tr>"]
    for item in mission.get("checklist", []):
        parts.append(f"<tr><td>{'Required' if item.get('required') else 'Recommended'}</td><td>{esc(item.get('item',''))}</td><td>{esc(item.get('quantity','Per stage'))}</td><td>{esc(item.get('note',''))}</td></tr>")
    parts += ["</table>", "<h2>Mission guide</h2>"]
    if "mission details are incomplete" in str(mission.get("guide_coverage", "")):
        parts.append("<p class='muted'>Mission-specific details are incomplete.</p>")
    parts.append("<ol>" + "".join(f"<li>{esc(x)}</li>" for x in mission.get("guide", [])) + "</ol>")
    if mission.get("mechanics"):
        parts += ["<h2>Mission mechanics</h2>", bullets(mission["mechanics"])]
    if mission.get("progress"):
        parts += ["<h2>Mission progress</h2>", bullets(f"{q['text']}: {q['state']}" + ("; " + "; ".join(q.get("details", [])) if q.get("details") else "") for q in mission["progress"])]
    parts += ["<h2>Risks and supplies</h2>", bullets(mission.get("risks", [])), "<h2>Rewards and unlocks</h2>", bullets(mission.get("rewards", []))]
    if mission.get("source_objectives") or mission.get("objectives"):
        objectives = [_initial_objective_text(row, mission.get("id"))
                      for row in (mission.get("source_objectives") or mission.get("objectives", []))]
        parts.append("<h2>Objectives</h2>")
        if any(initial for _, initial in objectives):
            parts.append("<p class='muted'>Initial objective counts; saved progress is listed separately.</p>")
        parts.append(bullets(text for text, _ in objectives))
    parts.append("</body></html>")
    return "".join(parts)
