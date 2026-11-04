"""Mission stage locations from versioned query anchors and selected-world UIDs.

No live file I/O or asset decoding is performed while selecting a mission. The
bundled catalogue contains derived public game coordinates, never private saves.
Unselected query anchors remain candidates, not fabricated spawned objectives.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

from .catalog import installed_game_build

DATA_PATH = Path(__file__).resolve().parent / "data" / "mission_locations.json"

QUERY_LABELS = {
    "PRO_Story_Mo_Hideout1": "Suspect's cave base", "PRO_Story_Mo_Hideout2": "Suspect's hidden base",
    "PRO_Story_3_Scan_1": "Alpha scan site", "PRO_Story_3_Scan_2": "Beta scan site", "PRO_Story_3_Scan_3": "Delta scan site",
    "PRO_Story_4_Body_1": "First missing agent", "PRO_Story_4_Body_2": "Second missing agent", "PRO_Story_4_Body_3": "Third missing agent",
    "PRO_Story_4_Body_1_Search": "First signal search area", "PRO_Story_4_Body_2_Search": "Second signal search area", "PRO_Story_4_Body_3_Search": "Third signal search area",
    "PRO_Story_4_Sol_Cache": "Sol's supply / delivery area", "PRO_Story_5_Mo_Outpost": "Outpost investigation area",
    "PRO_Story_2_DynamicDrop": "Sinotai mission supply / delivery area", "PRO_Story_3_DynamicDrop": "Group 15 upgrade supply area",
    "PRO_Story_6_TraitorLocation": "Traitor's last known location", "PRO_Story_6_TraitorTrail1": "First section of the traitor's trail",
    "PRO_Story_6_TraitorTrail2": "Second section of the traitor's trail", "PRO_Story_6_TraitorTrail3": "Third section of the traitor's trail", "PRO_Story_6_TraitorTrail4": "Trail and cave exit",
    "PRO_Story_6_Group15_Camp": "Group 15 camp", "PRO_Story_6_Group15_Base": "Group 15 base / hacking area",
    "PRO_Story_6_LavaCave_Entry": "Evidence cave entrance", "PRO_Story_6_LavaCave_Inner": "Evidence cave interior",
    "PRO_Story_6_LavaCave_Tunnel": "First cave tunnel", "PRO_Story_6_LavaCave_Cavern": "Cave cavern",
    "PRO_Story_6_LavaCave_LastTunnel": "Final cave tunnel", "PRO_Story_6_LavaCave_End": "Cave settlement / end",
    "PRO_Story_6_LavaCave_Outside": "Outside-cave investigation area", "PRO_Story_6_LavaCave_OutsideLava": "Lava-area evidence site",
    "PRO_Story_6_LavaCave_CreatureSpawn": "Giant Lava Creature area", "PRO_Story_1_Daisy": "Daisy's last known location", "PRO_Story_1_Daisy_Base": "Daisy's owner's base",
    "PRO_ArcticSteppe_Rear": "Icesheet supply / delivery area", "PRO_Marshlands_Middle": "Swamp supply / delivery area",
    "PRO_Treehouse": "Treetop housing site", "PRO_MiasmicResearchStation": "Swamp research-station site", "PRO_ArcticExtraction": "Super Cooled Ice extraction area",
    "PRO_VolcanicFishing": "Volcanic hunting / fishing area", "PRO_Ashlands_Outpost": "Ashlands outpost",
    "PRO_Ashlands_IcesheetTransition": "Ashlands-to-Icesheet transition", "PRO_Ashlands_Entrances": "Ashlands entrance",
    "PRO_Icesheet_Outpost": "Icesheet outpost", "PRO_Icesheet_Entrance": "Icesheet entrance", "PRO_Ashlands_Volcano": "Ashlands volcano",
    "PRO_Marshlands_East": "Supply stockpile origin", "PRO_Ashlands_NorthPath": "Ashlands supply destination",
    "PRO_Ashlands_ValleyEntrance": "Beta liquid-test area", "PRO_Ashlands_ValleyEntryway": "Alpha aerosol-test area",
    "PRO_Ashlands_ValleyDeep": "Chi follow-up test area", "PRO_Icesheet_UpperPillar": "Icesheet research area",
    "PRO_ASH_Truck": "Crashed truck / rescue supplies", "PRO_ASH_NPC1": "First mission agent", "PRO_ASH_NPC2": "Second mission agent",
    "PRO_ASH_NPC3": "Third mission agent", "PRO_ASH_NPC4": "Fourth mission agent", "PRO_ASH_NPC5": "Fifth mission agent",
    "PRO_ASH_Search": "Missing-agent search area", "PRO_ASH_Search2": "Follow-up agent search area", "PRO_ASH_Shed": "Agents' shed", "PRO_ASH_Camp": "First agent camp", "PRO_ASH_Camp2": "Second agent camp",
    "PRO_ASH_NPC4_Rescue": "First rescue agent", "PRO_ASH_NPC5_Rescue": "Second rescue agent", "PRO_ASH_Evac": "Rescue ship evacuation site",
    "PRO_ASH_Entrance_Search": "Ashlands entrance search area", "PRO_NULL_AudioWarning": "Ashlands warning-signal area", "PRO_NULL_AudioEntered": "Ashlands signal investigation area",
    "PRO_ASH_BUNKER_NOTE1": "First bunker note", "PRO_ASH_BUNKER_NOTE2": "Second bunker note", "PRO_ASH_BUNKER_NOTE3": "Third bunker note",
    "PRO_ASH_E1": "First Exotics site", "PRO_ASH_E2": "Second Exotics site", "PRO_ASH_E3": "Third Exotics site",
    "PRO_NUL_CAVE": "Anomalous cave search area", "PRO_NUL_EQU": "Cave mission equipment", "PRO_NUL_CAVE_ENTRANCE": "Anomalous cave entrance",
    "PRO_NUL_FLASHLIGHT_PROP": "Cave flashlight clue", "PRO_NUL_CAVE_RUNNING_PROP": "Cave runner clue", "PRO_NUL_CAVE_EVENT_RUNNING": "Later cave event area",
    "PRO_NUL_CAVE_CHAMBER": "Deep cave chamber", "PRO_NUL_CAVE_CAMP": "Cave camp", "PRO_NUL_CAVE_MAN_PROP": "Cave figure clue", "PRO_NUL_CAVE_EXOTICS": "Cave Exotics endpoint",
    "PRO_Ashlands_Blocker": "Ashlands passage barrier", "PRO_Icesheet_Blocker": "Icesheet passage barrier",
    "PRO_Icesheet_Cave_Note1": "First cave note", "PRO_Icesheet_Cave_Note2": "Second cave note", "PRO_Icesheet_Cave_Note3": "Third cave note",
    "SQ_NPC_FARM": "Farmer's base", "SQ_NPC_FISHER": "Fisher's base", "SQ_NPC_FISHER_INJURED": "Injured fisher location",
    "SQ_NPC_HUNTER": "Hunter's base", "SQ_NPC_NOREX_BOSS": "Norex hunting target area", "SQ_NPC_NOREX_LAND": "Norex arrival / delivery area",
}


@lru_cache(maxsize=1)
def load_location_catalog() -> dict:
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source_build": "", "markers": [], "queries": {}, "missions": {}}


def _get(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _xyz(record: dict) -> tuple[float, float, float] | None:
    position = (record.get("ActorTransform") or {}).get("Translation")
    if position is None and all(k in record for k in ("x", "y")):
        position = [record["x"], record["y"], record.get("z", 0)]
    if not isinstance(position, (list, tuple)) or len(position) < 2:
        return None
    if len(position) == 2:
        position = [*position, 0]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in position[:3]):
        return None
    xyz = tuple(float(v) for v in position[:3])
    return xyz if any(abs(v) > 0.01 for v in xyz) else None


def _variables(record: dict) -> dict:
    return {v.get("VariableName"): v for collection in ("VariableRecords", "BoolVariables", "IntVariables") for v in record.get(collection, []) if isinstance(v, dict)}


def _status(record: dict | None, mission: dict) -> str:
    if record:
        variables = _variables(record)
        if any(variables.get(k, {}).get("bVariable") for k in ("Complete", "Completed", "bComplete", "bCompleted")):
            return "completed"
        return "active" if mission.get("status_key") == "active" else "known"
    return "future"


def _plans(mid: str, info: dict, mission: dict) -> list[dict]:
    if mid == "PRO_A_Extraction":
        return [
            {"id": "collect", "label": "Collect Sinotai drilling equipment", "quest_ids": ["PRO_A_Extraction_Collect"], "queries": []},
            {"id": "travel", "label": "Travel to the Super Cooled Ice extraction area", "quest_ids": ["PRO_A_Extraction_Travel"], "queries": ["PRO_ArcticExtraction"]},
            {"id": "extract", "label": "Place the Ice Borer and extract Super Cooled Ice", "quest_ids": ["PRO_A_Extraction_Extract", "PRO_A_Extraction_Extract_MISSIONDISPLAY"], "queries": ["PRO_ArcticExtraction"]},
            {"id": "build", "label": "Build the required arctic camp", "quest_ids": ["PRO_A_Extraction_Build"], "queries": ["PRO_ArcticExtraction"]},
            {"id": "deliver", "label": "Deliver supplies to the Sinotai Retrieval Pod", "quest_ids": ["PRO_A_Extraction_Deliver"], "queries": []},
        ]
    if mid == "PRO_Story_5":
        return [
            {"id": "investigate", "label": "Investigate and enter the outpost", "quest_ids": ["PRO_Story_5_Investigate", "PRO_Story_5_Investigate_Enter"], "queries": ["PRO_Story_5_Mo_Outpost"]},
            {"id": "transmit", "label": "Power the outpost communicator and defend transmission", "quest_ids": ["PRO_Story_5_Outpost_Power", "PRO_Story_5_Outpost_Trasmit", "PRO_Story_5_Outpost_Progress"], "queries": ["PRO_Story_5_Mo_Outpost"]},
            {"id": "craft", "label": "Craft the mission Beacon", "quest_ids": ["PRO_Story_5_Beacon_Craft"], "queries": []},
            {"id": "beacon", "label": "Return to the outpost and place the Beacon", "quest_ids": ["PRO_Story_5_Beacon_Place"], "queries": ["PRO_Story_5_Mo_Outpost"]},
            {"id": "escape", "label": "Evacuate the outpost before destruction", "quest_ids": ["PRO_Story_5_Run", "PRO_Story_5_Run_Timer"], "queries": ["PRO_Story_5_Mo_Outpost"]},
        ]
    if mid == "PRO_D_Rescue":
        return [
            {"id": "search", "label": "Search for the missing team members", "quest_ids": ["PRO_D_Rescue_Locate"], "queries": ["PRO_ASH_Search2"]},
            {"id": "simmons", "label": "Find and bag Simmons", "quest_ids": ["PRO_D_Rescue_Locate_NPC1", "PRO_D_Rescue_Locate_NPC1_Find", "PRO_D_Rescue_Locate_NPC1_Collect"], "queries": ["PRO_ASH_NPC4_Rescue"]},
            {"id": "deliver-simmons", "label": "Deliver Simmons to the rescue ship", "quest_ids": ["PRO_D_Rescue_Locate_NPC1_Deliver"], "queries": ["PRO_ASH_Evac"]},
            {"id": "washington", "label": "Find and bag Washington", "quest_ids": ["PRO_D_Rescue_Locate_NPC2", "PRO_D_Rescue_Locate_NPC2_Find", "PRO_D_Rescue_Locate_NPC2_Collect"], "queries": ["PRO_ASH_NPC5_Rescue"]},
            {"id": "deliver-washington", "label": "Deliver Washington to the rescue ship", "quest_ids": ["PRO_D_Rescue_Locate_NPC2_Deliver"], "queries": ["PRO_ASH_Evac"]},
        ]
    plans = []
    stages = list(info.get("query_stages", []))
    ordered_queries = {
        "PRO_Story_4": ["PRO_Story_4_Body_1_Search", "PRO_Story_4_Body_1", "PRO_Story_4_Body_2_Search", "PRO_Story_4_Body_2", "PRO_Story_4_Body_3_Search", "PRO_Story_4_Body_3", "PRO_Story_4_Sol_Cache"],
        "PRO_Story_6": ["PRO_Story_6_TraitorLocation", "PRO_Story_6_TraitorTrail1", "PRO_Story_6_TraitorTrail4", "PRO_Story_6_Group15_Camp", "PRO_Story_6_Group15_Base", "PRO_Story_6_LavaCave_OutsideLava", "PRO_Story_6_LavaCave_CreatureSpawn", "PRO_Story_6_LavaCave_Entry", "PRO_Story_6_LavaCave_Tunnel", "PRO_Story_6_LavaCave_Cavern", "PRO_Story_6_LavaCave_LastTunnel", "PRO_Story_6_LavaCave_End", "PRO_Story_6_LavaCave_Outside"],
    }.get(mid)
    if ordered_queries:
        rank = {query: i for i, query in enumerate(ordered_queries)}
        stages.sort(key=lambda stage: rank.get(stage["query"], 1000))
    for index, stage in enumerate(stages, 1):
        query = stage["query"]
        plans.append({"id": query, "label": QUERY_LABELS.get(query, "Mission area"), "quest_ids": stage.get("quest_ids", []), "queries": [query]})
    return plans


def build_mission_locations(snapshot: Any, mission: dict) -> dict:
    """Return ordered known/candidate locations and still-unlocated stages.

    Runtime work is in-memory joining plus a cached compact catalogue read. No
    backups, network requests, game controls or asset extraction are performed.
    """
    catalog = load_location_catalog()
    mid = str(mission.get("id", ""))
    map_name = str(_get(snapshot, "map_name", ""))
    mission_map = str(mission.get("map_name", map_name))
    result = {"points": [], "regions": [], "pending_stages": [], "coverage": {"map": map_name, "mission": mid, "static_build": catalog.get("source_build"), "sources": catalog.get("source_files", {})}}
    if mission_map not in (map_name, "All", "", "None"):
        result["coverage"]["reason"] = "The selected mission does not match the current world's map"
        return result
    info = catalog.get("missions", {}).get(mid, {})
    records = _get(snapshot, "raw_records", []) or []
    by_uid = defaultdict(list)
    quests = {}
    manager = {}
    for record in records:
        uid = record.get("IcarusActorGUID")
        if isinstance(uid, int) and not isinstance(uid, bool) and uid > 0:
            by_uid[uid].append(record)
        if record.get("QuestName"):
            quests[record["QuestName"]] = record
        if str(record.get("_component", "")).endswith("IcarusQuestManagerRecorderComponent"):
            manager = record
    if not manager:
        manager = (_get(snapshot, "mission_state", {}) or {}).get("quest_manager", {})
    selected_current = mid in (manager.get("FactionMissionName"), manager.get("DynamicMissionProspectName"))
    qids = set(info.get("quest_ids", [])) | set(mission.get("quest_ids", []))
    if selected_current:
        initial = manager.get("InitialQuestRecord", {}).get("RelevantActorIcarusUID")
        queue = [initial] if initial else []
        seen = set()
        while queue:
            uid = queue.pop()
            if uid in seen:
                continue
            seen.add(uid)
            for record in by_uid.get(uid, []):
                if record.get("QuestName"):
                    qids.add(record["QuestName"])
                queue.extend(q.get("RelevantActorIcarusUID") for q in record.get("SubQuestRecords", []) if q.get("RelevantActorIcarusUID"))
    relevant = {name: record for name, record in quests.items() if name in qids}
    parent_locations = {}
    for record in relevant.values():
        variables = _variables(record)
        if not any(variables.get(key, {}).get("bVariable") for key in ("LocationSet", "LocationReached", "bLocationSet", "bLocationReached")):
            continue
        xyz = _xyz(record)
        if xyz is not None:
            for child in record.get("SubQuestRecords", []):
                if child.get("RelevantActorIcarusUID"):
                    parent_locations[child["RelevantActorIcarusUID"]] = xyz
    plans = _plans(mid, info, mission)
    planned_qids = {name for plan in plans for name in plan.get("quest_ids", [])}
    for name in relevant:
        if name not in planned_qids and (not plans or selected_current and name.startswith("DYN_")):
            plans.append({"id": name, "label": "Track the creature's area" if name == "DYN_Kill" else "Hunt the nearby creature" if name == "DYN_Kill_Boss" else "Current mission location", "quest_ids": [name], "queries": []})
    if not plans:
        for index, step in enumerate(mission.get("guide", []), 1):
            plans.append({"id": f"guide-{index}", "label": str(step), "quest_ids": [], "queries": []})
    current_build = installed_game_build()
    static_compatible = bool(current_build and current_build == catalog.get("source_build") and map_name == catalog.get("map_name"))
    marker_tags = defaultdict(list)
    if static_compatible:
        for marker in catalog.get("markers", []):
            for tag in marker.get("tags", []):
                marker_tags[tag].append(marker)

    def resolve(uid):
        candidates = [(record, _xyz(record)) for record in by_uid.get(uid, [])]
        candidates = [(record, xyz) for record, xyz in candidates if xyz is not None]
        unique = {xyz for _, xyz in candidates}
        return candidates[0] if len(unique) == 1 else None

    for order, plan in enumerate(plans, 1):
        label = plan["label"]
        evidence = []
        for qid in plan.get("quest_ids", []):
            record = relevant.get(qid)
            if record is None:
                continue
            for collection, field in (("RelevantActorRecords", "RelevantActorIcarusUID"), ("RelevantCharacterRecords", "RelevantCharacterIcarusUID")):
                for ref in record.get(collection, []):
                    resolved = resolve(ref.get(field))
                    if resolved:
                        target, xyz = resolved
                        evidence.append((xyz, f"uid:{ref.get(field)}", record, "Mission actor location; creatures and movable objects may move.", "known"))
            variables = _variables(record)
            location_set = any(variables.get(key, {}).get("bVariable") for key in ("LocationSet", "LocationReached", "bLocationSet", "bLocationReached"))
            xyz = _xyz(record)
            inherited = xyz is not None and parent_locations.get(record.get("IcarusActorGUID")) == xyz
            if not evidence and xyz and (location_set or inherited):
                evidence.append((xyz, "quest:" + qid, record, "Mission search / activity area; the target creature or object may be nearby.", "known"))
        seen_coordinates = set()
        for xyz, ident, record, description, confidence in evidence:
            if xyz in seen_coordinates:
                continue
            seen_coordinates.add(xyz)
            result["points"].append({"id": f"{mid}:{plan['id']}:{ident}", "label": label, "stage": plan["id"], "order": order, "x": xyz[0], "y": xyz[1], "z": xyz[2], "status": _status(record, mission), "confidence": confidence, "description": description, "source": "selected_world_uid_or_location_flag"})
        if not evidence:
            found = []
            for query in plan.get("queries", []):
                for tag in catalog.get("queries", {}).get(query, []):
                    for marker in marker_tags.get(tag, []):
                        xyz = _xyz(marker)
                        if xyz and xyz not in seen_coordinates:
                            seen_coordinates.add(xyz)
                            found.append((query, marker, xyz))
            for query, marker, xyz in found:
                result["points"].append({"id": f"{mid}:{plan['id']}:anchor:{marker['name']}", "label": label, "stage": plan["id"], "order": order, "x": xyz[0], "y": xyz[1], "z": xyz[2], "status": "candidate", "confidence": "candidate", "description": "This stage may take place in this area. The selected site updates after starting.", "source": "installed_map_gameplay_tag", "query": query})
            if not found:
                reason = "Later locations will appear as the mission progresses."
                if plan["id"] in ("collect", "deliver"):
                    reason = "The location will appear when the mission drop / retrieval pod spawns."
                elif plan["id"] == "craft":
                    reason = "Craft at a bench / base of your choice."
                elif not static_compatible and plan.get("queries"):
                    reason = "Location data for this mission has not yet been updated."
                result["pending_stages"].append({"id": f"{mid}:{plan['id']}:pending", "stage": plan["id"], "order": order, "label": label, "reason": reason, "description": reason, "status": "pending"})
    result["coverage"].update(static_compatible=static_compatible, stage_count=len(plans), known_point_count=sum(p["confidence"] == "known" for p in result["points"]), candidate_point_count=sum(p["confidence"] == "candidate" for p in result["points"]), pending_count=len(result["pending_stages"]))
    return result
