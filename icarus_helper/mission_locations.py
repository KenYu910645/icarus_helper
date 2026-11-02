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
    "PRO_Story_Mo_Hideout1": "嫌疑人的洞穴基地", "PRO_Story_Mo_Hideout2": "嫌疑人的隱藏基地",
    "PRO_Story_3_Scan_1": "Alpha 掃描點", "PRO_Story_3_Scan_2": "Beta 掃描點", "PRO_Story_3_Scan_3": "Delta 掃描點",
    "PRO_Story_4_Body_1": "第一位失聯探員", "PRO_Story_4_Body_2": "第二位失聯探員", "PRO_Story_4_Body_3": "第三位失聯探員",
    "PRO_Story_4_Body_1_Search": "第一段訊號搜尋區", "PRO_Story_4_Body_2_Search": "第二段訊號搜尋區", "PRO_Story_4_Body_3_Search": "第三段訊號搜尋區",
    "PRO_Story_4_Sol_Cache": "Sol 的補給／交付區", "PRO_Story_5_Mo_Outpost": "前哨調查區",
    "PRO_Story_2_DynamicDrop": "Sinotai 任務補給／交付區", "PRO_Story_3_DynamicDrop": "Group 15 升級補給區",
    "PRO_Story_6_TraitorLocation": "叛徒最後出現位置", "PRO_Story_6_TraitorTrail1": "叛徒足跡第一段",
    "PRO_Story_6_TraitorTrail2": "叛徒足跡第二段", "PRO_Story_6_TraitorTrail3": "叛徒足跡第三段", "PRO_Story_6_TraitorTrail4": "足跡與洞穴出口",
    "PRO_Story_6_Group15_Camp": "Group 15 營地", "PRO_Story_6_Group15_Base": "Group 15 基地／駭入區",
    "PRO_Story_6_LavaCave_Entry": "證據洞穴入口", "PRO_Story_6_LavaCave_Inner": "證據洞穴內部",
    "PRO_Story_6_LavaCave_Tunnel": "洞穴第一段通道", "PRO_Story_6_LavaCave_Cavern": "洞穴大廳",
    "PRO_Story_6_LavaCave_LastTunnel": "洞穴最後通道", "PRO_Story_6_LavaCave_End": "洞穴聚落／終點",
    "PRO_Story_6_LavaCave_Outside": "洞穴外側調查區", "PRO_Story_6_LavaCave_OutsideLava": "熔岩區證據位置",
    "PRO_Story_6_LavaCave_CreatureSpawn": "大型熔岩生物區", "PRO_Story_1_Daisy": "Daisy 最後出現位置", "PRO_Story_1_Daisy_Base": "Daisy 飼主基地",
    "PRO_ArcticSteppe_Rear": "冰原補給／交付區", "PRO_Marshlands_Middle": "沼澤補給／交付區",
    "PRO_Treehouse": "樹頂住宅施工區", "PRO_MiasmicResearchStation": "沼澤研究站施工區", "PRO_ArcticExtraction": "超冷冰採掘區",
    "PRO_VolcanicFishing": "火山狩獵／釣魚區", "PRO_Ashlands_Outpost": "灰燼地前哨",
    "PRO_Ashlands_IcesheetTransition": "灰燼地至冰蓋過渡區", "PRO_Ashlands_Entrances": "灰燼地入口",
    "PRO_Icesheet_Outpost": "冰蓋前哨", "PRO_Icesheet_Entrance": "冰蓋入口", "PRO_Ashlands_Volcano": "灰燼地火山",
    "PRO_Marshlands_East": "物資儲備起點", "PRO_Ashlands_NorthPath": "灰燼地物資運送終點",
    "PRO_Ashlands_ValleyEntrance": "Beta 液體測試區", "PRO_Ashlands_ValleyEntryway": "Alpha 氣霧測試區",
    "PRO_Ashlands_ValleyDeep": "Chi 後續測試區", "PRO_Icesheet_UpperPillar": "冰蓋研究區",
    "PRO_ASH_Truck": "失事卡車／救援物資", "PRO_ASH_NPC1": "第一位任務探員", "PRO_ASH_NPC2": "第二位任務探員",
    "PRO_ASH_NPC3": "第三位任務探員", "PRO_ASH_NPC4": "第四位任務探員", "PRO_ASH_NPC5": "第五位任務探員",
    "PRO_ASH_Search": "失聯探員搜尋區", "PRO_ASH_Search2": "後續探員搜尋區", "PRO_ASH_Shed": "探員棚屋", "PRO_ASH_Camp": "第一處探員營地", "PRO_ASH_Camp2": "第二處探員營地",
    "PRO_ASH_NPC4_Rescue": "第一位待救援探員", "PRO_ASH_NPC5_Rescue": "第二位待救援探員", "PRO_ASH_Evac": "救援船撤離位置",
    "PRO_ASH_Entrance_Search": "灰燼地入口搜尋區", "PRO_NULL_AudioWarning": "灰燼地警告訊號區", "PRO_NULL_AudioEntered": "灰燼地訊號調查區",
    "PRO_ASH_BUNKER_NOTE1": "地堡第一份筆記", "PRO_ASH_BUNKER_NOTE2": "地堡第二份筆記", "PRO_ASH_BUNKER_NOTE3": "地堡第三份筆記",
    "PRO_ASH_E1": "第一處異質資源", "PRO_ASH_E2": "第二處異質資源", "PRO_ASH_E3": "第三處異質資源",
    "PRO_NUL_CAVE": "異常洞穴搜尋區", "PRO_NUL_EQU": "洞穴任務裝備", "PRO_NUL_CAVE_ENTRANCE": "異常洞穴入口",
    "PRO_NUL_FLASHLIGHT_PROP": "洞穴手電筒線索", "PRO_NUL_CAVE_RUNNING_PROP": "洞穴奔跑者線索", "PRO_NUL_CAVE_EVENT_RUNNING": "洞穴後續事件區",
    "PRO_NUL_CAVE_CHAMBER": "洞穴深處石室", "PRO_NUL_CAVE_CAMP": "洞穴營地", "PRO_NUL_CAVE_MAN_PROP": "洞穴人物線索", "PRO_NUL_CAVE_EXOTICS": "洞穴異質資源終點",
    "PRO_Ashlands_Blocker": "灰燼地通道阻擋處", "PRO_Icesheet_Blocker": "冰蓋通道阻擋處",
    "PRO_Icesheet_Cave_Note1": "洞穴第一份筆記", "PRO_Icesheet_Cave_Note2": "洞穴第二份筆記", "PRO_Icesheet_Cave_Note3": "洞穴第三份筆記",
    "SQ_NPC_FARM": "農夫基地", "SQ_NPC_FISHER": "漁夫基地", "SQ_NPC_FISHER_INJURED": "受傷漁夫位置",
    "SQ_NPC_HUNTER": "獵人基地", "SQ_NPC_NOREX_BOSS": "Norex 狩獵目標區", "SQ_NPC_NOREX_LAND": "Norex 到達／交付區",
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
            {"id": "collect", "label": "領取 Sinotai 鑽探設備", "quest_ids": ["PRO_A_Extraction_Collect"], "queries": []},
            {"id": "travel", "label": "前往超冷冰採掘區", "quest_ids": ["PRO_A_Extraction_Travel"], "queries": ["PRO_ArcticExtraction"]},
            {"id": "extract", "label": "放置 Ice Borer，開採超冷冰", "quest_ids": ["PRO_A_Extraction_Extract", "PRO_A_Extraction_Extract_MISSIONDISPLAY"], "queries": ["PRO_ArcticExtraction"]},
            {"id": "build", "label": "在冰區建立指定營地", "quest_ids": ["PRO_A_Extraction_Build"], "queries": ["PRO_ArcticExtraction"]},
            {"id": "deliver", "label": "到 Sinotai 回收艙交付物資", "quest_ids": ["PRO_A_Extraction_Deliver"], "queries": []},
        ]
    if mid == "PRO_Story_5":
        return [
            {"id": "investigate", "label": "調查並進入前哨", "quest_ids": ["PRO_Story_5_Investigate", "PRO_Story_5_Investigate_Enter"], "queries": ["PRO_Story_5_Mo_Outpost"]},
            {"id": "transmit", "label": "前哨通訊器供電與防守傳輸", "quest_ids": ["PRO_Story_5_Outpost_Power", "PRO_Story_5_Outpost_Trasmit", "PRO_Story_5_Outpost_Progress"], "queries": ["PRO_Story_5_Mo_Outpost"]},
            {"id": "craft", "label": "製作任務信標", "quest_ids": ["PRO_Story_5_Beacon_Craft"], "queries": []},
            {"id": "beacon", "label": "返回前哨放置信標", "quest_ids": ["PRO_Story_5_Beacon_Place"], "queries": ["PRO_Story_5_Mo_Outpost"]},
            {"id": "escape", "label": "離開即將摧毀的前哨", "quest_ids": ["PRO_Story_5_Run", "PRO_Story_5_Run_Timer"], "queries": ["PRO_Story_5_Mo_Outpost"]},
        ]
    if mid == "PRO_D_Rescue":
        return [
            {"id": "search", "label": "搜尋失聯隊員區域", "quest_ids": ["PRO_D_Rescue_Locate"], "queries": ["PRO_ASH_Search2"]},
            {"id": "simmons", "label": "找到並裝載 Simmons", "quest_ids": ["PRO_D_Rescue_Locate_NPC1", "PRO_D_Rescue_Locate_NPC1_Find", "PRO_D_Rescue_Locate_NPC1_Collect"], "queries": ["PRO_ASH_NPC4_Rescue"]},
            {"id": "deliver-simmons", "label": "將 Simmons 送到救援船", "quest_ids": ["PRO_D_Rescue_Locate_NPC1_Deliver"], "queries": ["PRO_ASH_Evac"]},
            {"id": "washington", "label": "找到並裝載 Washington", "quest_ids": ["PRO_D_Rescue_Locate_NPC2", "PRO_D_Rescue_Locate_NPC2_Find", "PRO_D_Rescue_Locate_NPC2_Collect"], "queries": ["PRO_ASH_NPC5_Rescue"]},
            {"id": "deliver-washington", "label": "將 Washington 送到救援船", "quest_ids": ["PRO_D_Rescue_Locate_NPC2_Deliver"], "queries": ["PRO_ASH_Evac"]},
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
        plans.append({"id": query, "label": QUERY_LABELS.get(query, "任務執行區域"), "quest_ids": stage.get("quest_ids", []), "queries": [query]})
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
        result["coverage"]["reason"] = "所選任務與目前世界地圖不符"
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
            plans.append({"id": name, "label": "追蹤巨獸區域" if name == "DYN_Kill" else "獵殺附近巨獸" if name == "DYN_Kill_Boss" else "目前任務執行位置", "quest_ids": [name], "queries": []})
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
                        evidence.append((xyz, f"uid:{ref.get(field)}", record, "任務物件的位置；生物或可搬運物件仍可能移動。", "known"))
            variables = _variables(record)
            location_set = any(variables.get(key, {}).get("bVariable") for key in ("LocationSet", "LocationReached", "bLocationSet", "bLocationReached"))
            xyz = _xyz(record)
            inherited = xyz is not None and parent_locations.get(record.get("IcarusActorGUID")) == xyz
            if not evidence and xyz and (location_set or inherited):
                evidence.append((xyz, "quest:" + qid, record, "任務搜尋／執行區域；目標生物或物件可能位於附近。", "known"))
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
                result["points"].append({"id": f"{mid}:{plan['id']}:anchor:{marker['name']}", "label": label, "stage": plan["id"], "order": order, "x": xyz[0], "y": xyz[1], "z": xyz[2], "status": "candidate", "confidence": "candidate", "description": "此階段可能在這個區域進行；開始後會更新選定地點。", "source": "installed_map_gameplay_tag", "query": query})
            if not found:
                reason = "推進任務後會標出後續地點。"
                if plan["id"] in ("collect", "deliver"):
                    reason = "任務投放／回收艙出現後會標出位置。"
                elif plan["id"] == "craft":
                    reason = "可在你選擇的工作台／基地製作。"
                elif not static_compatible and plan.get("queries"):
                    reason = "此任務的地點資料尚未更新。"
                result["pending_stages"].append({"id": f"{mid}:{plan['id']}:pending", "stage": plan["id"], "order": order, "label": label, "reason": reason, "description": reason, "status": "pending"})
    result["coverage"].update(static_compatible=static_compatible, stage_count=len(plans), known_point_count=sum(p["confidence"] == "known" for p in result["points"]), candidate_point_count=sum(p["confidence"] == "candidate" for p in result["points"]), pending_count=len(result["pending_stages"]))
    return result
