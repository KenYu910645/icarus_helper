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

DATA_PATH = Path(__file__).resolve().parent / "data" / "missions.json"
STATUS = {"active": "進行中", "available": "可接取", "locked": "尚未解鎖", "completed": "已完成", "unknown": "待確認"}
STATUS_ORDER = {"active": 0, "available": 1, "unknown": 2, "locked": 3, "completed": 4}
MAP_ALIASES = {"Terrain_016": "Olympus", "Terrain_017": "Styx", "Terrain_019": "Prometheus", "Terrain_021": "Elysium"}
# Verified from the installed executable's reflected EMissionState enumerator table.
HISTORY_STATES = {0: "進行中", 1: "已完成", 2: "已放棄", 3: "失敗"}


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    return value if isinstance(value, dict) else {}


def _mission_state(world: dict) -> tuple[list, list, dict, list]:
    state = world.get("mission_state") or {}
    records = state.get("records") or world.get("mission_records") or []
    history = state.get("mission_history") or []
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


def _progress(quest: dict, catalog: dict) -> dict:
    """Expose evidence without treating a persisted actor as an active objective."""
    qid = quest.get("QuestName", "")
    variables = quest.get("VariableRecords", [])
    by_name = {v.get("VariableName"): v for v in variables}
    explicit_done = any(by_name.get(k, {}).get("bVariable") for k in ("Complete", "Completed", "bComplete", "bCompleted"))
    reached = bool(by_name.get("LocationReached", {}).get("bVariable"))
    state = "已完成（明確完成旗標）" if explicit_done else "已到達（位置旗標）" if reached else "記錄存在，完成狀態未確認"
    details = []
    for var in variables:
        name = var.get("VariableName", "")
        if name in {"CurrentTime", "ElapsedTime", "TimeElapsed", "ProgressTime"}:
            current = float(var.get("fVariable", 0) or var.get("iVariable", 0))
            if qid == "PRO_Story_5_Outpost_Progress":
                current = min(600.0, max(0.0, current))
                details.append(f"傳輸進度 {current:.0f}／600 秒（{current / 6:.1f}%），剩餘有效傳輸 {600-current:.0f} 秒")
                state = "傳輸中" if current < 600 else "傳輸進度達目標"
            else:
                details.append(f"{name}：{current:g} 秒（欄位原值）")
        elif any(key in name.lower() for key in ("count", "amount", "health", "durability", "progress")):
            number = var.get("fVariable") or var.get("iVariable")
            if number is not None:
                details.append(f"{name}：{number:g}（未驗證目標值時不換算百分比）")
    if qid == "PRO_Story_5_Outpost_Progress" and not details:
        details.append("目前快照未保存可讀的傳輸計時，無法顯示倒數")
    return {"id": qid, "text": catalog.get("quest_texts", {}).get(qid, qid), "state": state,
            "complete": bool(explicit_done), "reached": reached, "details": details, "variables": variables}


def _next_step(mission: dict) -> str:
    progress = {q["id"]: q for q in mission.get("progress", [])}
    if mission["status_key"] == "completed":
        return "此任務已完成，可依解鎖清單選擇下一個任務。"
    if mission["status_key"] != "active":
        return "先核對可接條件，再按準備清單備妥物品。"
    if mission["id"] == "PRO_Story_5":
        if "PRO_Story_5_Run" in progress or "PRO_Story_5_Run_Timer" in progress:
            return "存檔已有撤離階段記錄：立即在遊戲內確認摧毀倒數，優先遠離前哨；精確倒數以遊戲畫面為準。"
        if "PRO_Story_5_Beacon_Place" in progress:
            return "核對傳輸已完成，將任務信標放在前哨內；基地摧毀警告出現後優先撤離。"
        if "PRO_Story_5_Beacon_Craft" in progress:
            return "在 Machining Bench 製作任務 Beacon，再依遊戲標記帶回前哨。"
        transmission = progress.get("PRO_Story_5_Outpost_Progress")
        if transmission and not transmission["complete"]:
            return "確認供電與通訊器耐久，在約 38 m 內防守以推進傳輸；" + ("；".join(transmission["details"]) or "目前快照未提供可讀倒數。")
        if "PRO_Story_5_Outpost_Trasmit" in progress:
            return "在遊戲確認 10000 W 供電與防守準備完成後，與資料通訊器互動開始傳輸。"
        if "PRO_Story_5_Outpost_Power" in progress:
            return "已保存供電階段記錄：到通訊器檢查 10000 W 供電；存檔未提供足夠資料判斷此刻是否達標。完成戰備後依遊戲目標啟動傳輸。"
        if progress.get("PRO_Story_5_Investigate_Enter", {}).get("reached"):
            return "已到達前哨內部；前往資料通訊器，注意爆炸地雷並準備 10000 W 電力。"
        return "依遊戲標記調查前哨位置，進入前先檢查地雷、供電與防守補給。"
    completed = {q["id"] for q in mission.get("progress", []) if q.get("complete")}
    remaining = [q.get("text", "") for q in mission.get("objectives", []) if q.get("id") not in completed]
    if remaining:
        return "依遊戲任務清單確認下一項：" + remaining[0] + "。目前快照不足以判斷所有階段先後／完成條件。"
    return "查看遊戲目前標示的目標；已保存的任務物件不代表所有階段仍在進行。"


def _terminal_level(world: dict) -> tuple[int | None, str]:
    """Only recognise installed communication modules inside the actual terminal."""
    records = world.get("raw_records") or []
    devices = [r for r in records if r.get("StaticItemDataRowName") == "Mission_Device"]
    if not devices:
        devices = [r.get("raw", r) for r in world.get("deployables", []) if isinstance(r, dict) and (r.get("item_id") == "Mission_Device" or r.get("StaticItemDataRowName") == "Mission_Device")]
    if not devices:
        return None, "尚未從存檔確認 C0NT4CT 裝置及其升級模組"
    level = 1
    for device in devices:
        for inventory in device.get("SavedInventories", []):
            for slot in inventory.get("Slots", []):
                name = slot.get("ItemStaticData", "")
                if name == "Mission_Communication_Device_T3":
                    level = max(level, 2)
                elif name == "Mission_Communication_Device_T4":
                    level = max(level, 3)
    return level, ["", "已確認 C0NT4CT 基礎裝置", "已確認 C0NT4CT 的 T3 升級模組", "已確認 C0NT4CT 的 T4 升級模組"][level]


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
    mission["base_name"] = str(base.get("name") or "所選基地") if base_xyz else None
    mission["distance_status"] = "base_missing"
    mission["distance_note"] = "尚未指定基地，請先在地圖設定我們家。"
    if not base_xyz:
        return 0.0
    if base.get("world_id") and base["world_id"] != world.get("world_id"):
        mission["base_known"] = False
        mission["distance_status"] = "base_world_mismatch"
        mission["distance_note"] = "基地與目前世界不符，未計算距離。"
        return 0.0
    if base.get("map_name") and base["map_name"] != _map_name(world):
        mission["base_known"] = False
        mission["distance_status"] = "base_map_mismatch"
        mission["distance_note"] = "基地與目前地圖不符，未計算距離。"
        return 0.0
    verified_goal = mission.get("position_reliable") is True and mission.get("position_scope") == "objective"
    label = "任務目標" if verified_goal else "任務記錄點"
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
            elif item.get("confidence") in ("candidate", "inferred", "estimated", "候選", "推定", "估計"):
                candidates.append((item, xyz))
        options = known or candidates
        if options:
            target, point = min(options, key=lambda entry: math.hypot(entry[1][0] - base_xyz[0], entry[1][1] - base_xyz[1]))
            label = "最近任務區域" if known else "最近候選地點"
            mission["distance_status"] = "known_area" if known else "candidate_area"
            mission["distance_position"] = list(point)
            mission["distance_target_id"] = target.get("id")
            verified_goal = False
    if point is None:
        mission["distance_note"] = f"已指定基地「{mission['base_name']}」；此任務位置尚未確定，定位後顯示距離。"
        return 0.0
    distance = math.hypot(point[0] - base_xyz[0], point[1] - base_xyz[1]) / 100.0
    mission["horizontal_distance_m"] = distance
    mission["distance_note"] = f"距離{base.get('name', '所選基地')}的水平直線距離：約 {distance:,.0f} m（至{label}，非步行路線）。"
    mission["distance_affects_ranking"] = verified_goal
    if not verified_goal:
        mission["distance_note"] += "此座標未證實為實際目標／入口，因此不套用距離排序。"
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
        return None, "尚未確認隊伍人數；準備數量請按實際參與人數與難度調整。"
    return count, f"存檔名單記錄 {count} 人，不代表即時在場人數；補給數量請按本次實際隊伍調整。"


def build_missions(world: Any, goal: str = "story", context: dict | None = None) -> list[dict]:
    """Join only the selected world's history/quests to its map's catalogue.

    Missing history is unknown, not a completed or unlocked mission. Account/DLC
    conditions remain visible when no authoritative profile data is available.
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
        version_note = f"目前遊戲 build {current_build} 與任務資料版本相符。"
    elif version_compatible is False:
        version_note = f"版本不符：目前遊戲 build {current_build}，任務資料 build {source_build}。攻略機制與解鎖規則需重新查核。"
    else:
        version_note = f"版本未驗證：目前遊戲 build {current_build or '未知'}，任務資料 build {source_build or '未知'}；不能假設相同。"
    map_name = _map_name(world)
    info = world.get("prospect_info") or world.get("info") or {}
    history, records, manager, quests = _mission_state(world)
    current = manager.get("FactionMissionName") or manager.get("DynamicMissionProspectName")
    completed = {h.get("Mission") for h in history if h.get("Status") == 1}
    past = {h.get("Mission"): h for h in history}
    level, terminal_reason = _terminal_level(world)
    difficulty = info.get("Difficulty", world.get("difficulty", "未知"))
    party_count, party_note = _party_note(world)
    results = []
    all_by_id = {m["id"]: m for m in catalog["missions"]}
    for original in catalog["missions"]:
        if original["map_name"] not in (map_name, "All"):
            continue
        mission = deepcopy(original)
        mid = mission["id"]
        requirements = mission.get("prerequisite_ids", [])
        unmet = [pid for pid in requirements if pid not in completed]
        required_level = mission.get("terminal_level", 0)
        state_key, reason = "unknown", "缺少可確認的世界任務與裝置資料"
        if mid == current and not manager.get("bMissionComplete"):
            state_key, reason = "active", "目前世界的 QuestManager 正在執行此任務"
        elif mid in completed or (mid == current and manager.get("bMissionComplete")):
            state_key, reason = "completed", "此世界 MissionHistory／QuestManager 明確記錄完成"
        elif mission.get("kind") == "SMPL3":
            state_key = "unknown"
            reason = "SMPL3 隨機委派類型；目前抽選名單與難度尚未從裝置還原，不能保證此刻可接"
        elif unmet:
            if history:
                state_key, reason = "locked", "尚未確認完成前置：" + "、".join(all_by_id.get(pid, {}).get("title", pid) for pid in unmet)
            else:
                reason = "需確認前置：" + "、".join(requirements)
        elif level is None:
            reason = terminal_reason
        elif level < required_level:
            state_key, reason = "locked", f"需要 C0NT4CT 升級階段 {required_level - 1}；{terminal_reason}"
        else:
            state_key, reason = "available", terminal_reason + "，世界前置條件符合"
        # Entitlements are not present in a world snapshot; never imply verified DLC ownership.
        if mission.get("feature") == "DangerousHorizons" and state_key == "available":
            state_key = "unknown"
            reason += "；另需確認 Dangerous Horizons 使用權限"
        if (mission.get("required_flags") or mission.get("required_level")) and state_key == "available":
            state_key = "unknown"
            reason += "；角色等級／帳號標記需求仍待核對"
        if mid in past and past[mid].get("Status") in (2, 3):
            reason += "；上次紀錄：" + HISTORY_STATES[past[mid]["Status"]] + "，重試冷卻需在裝置確認"
            if state_key == "available":
                state_key, reason = "unknown", reason
        if current and state_key == "available":
            reason += "；須先完成或在遊戲內結束目前操作，再接下一個"
        if version_compatible is not True:
            if state_key not in {"active", "completed"}:
                state_key = "unknown"
            reason += "；" + version_note
        mission.update(status=STATUS[state_key], status_key=state_key, status_reason=reason, difficulty=difficulty)
        mission.update(version_compatible=version_compatible, version_note=version_note,
                       installed_build=current_build, source_build=source_build or None)
        mission["prerequisites"] = [all_by_id.get(pid, {}).get("title", pid) for pid in requirements]
        mission["progress"] = []
        for quest in quests:
            qid = quest.get("QuestName", "")
            known_ids = mission.get("quest_ids", [])
            if qid not in known_ids and qid != mid:
                continue
            mission["progress"].append(_progress(quest, catalog))
            xyz = quest.get("ActorTransform", {}).get("Translation")
            if xyz and any(abs(float(n)) > 0 for n in xyz) and "position" not in mission:
                mission["position"] = xyz
                mission["position_scope"] = "record_point"
                mission["position_reliable"] = False
                mission["position_note"] = "任務記錄物件座標，非保證目標入口／路線"
        mission["next_step"] = _next_step(mission)
        score = {"active": 1000, "available": 500, "unknown": 100, "locked": -100, "completed": -500}[state_key]
        if state_key == "active":
            rec = "優先完成目前操作，避免另起準備造成物資分散。"
        elif state_key == "available":
            rec = "世界前置與裝置升級已符合；出發前仍須依清單核對角色科技與庫存。"
        elif state_key == "locked":
            rec = "先完成列出的前置或升級裝置。"
        elif state_key == "completed":
            rec = "此世界已完成，可保留攻略供重玩參考。"
        else:
            rec = "先在遊戲裝置確認可接狀態與缺少條件，再投入物資。"
        story = "_Story_" in mid or "_Story" in mid
        if goal in ("story", "主線", "推進主線") and story:
            score += 70
            rec += "此任務會推進故事／區域解鎖。"
        elif goal in ("safe", "低風險"):
            score -= mission.get("risk_rank", 2) * 30
            rec += "排序偏好較少防守與首領戰的任務。"
        elif goal in ("resources", "資源", "取得資源／解鎖"):
            score += 30 if mission.get("kind") in ("NPC", "SMPL3") else 0
            rec += "排序偏好交易與補給用途。"
        score += _distance(mission, world, context)
        if mission.get("horizontal_distance_m") is not None:
            rec += mission["distance_note"]
        else:
            rec += "已指定基地；任務位置尚未確定，距離未參與排序。" if mission.get("base_known") else "尚未指定目前世界的基地，距離未參與排序。"
        mission.update(recommendation_score=score, recommendation_reason=rec, recommendation=rec)
        mission["inventory_note"] = "清單由使用者手動確認；尚未核對基地或角色庫存。"
        mission["technology_note"] = (f"任務表的科技需求／建議：{mission['tech']}；尚未確認目前角色已解鎖。" if mission.get("tech") not in (None, "None", "") else "角色科技解鎖狀態尚未確認，請依清單核對。")
        mission["party_size_recorded"] = party_count
        mission["party_note"] = party_note
        mission["source_version"] = catalog["source_version"]
        mission["source_date"] = catalog["source_date"]
        results.append(mission)
    results.sort(key=lambda m: (STATUS_ORDER[m["status_key"]], -m["recommendation_score"], m.get("order", 999), m["title"]))
    for i, mission in enumerate(results):
        mission["rank"] = i + 1
    return results


def mission_html(mission: dict) -> str:
    """Render the requested gameplay guide; keep diagnostics in the data model."""
    def esc(value):
        return escape(str(value))
    def bullets(items):
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>"
    parts = ["<html><head><style>body{font-family:'Microsoft JhengHei',sans-serif;color:#e8ecf2;} h1{font-size:23px;color:#ffffff;}h2{font-size:16px;color:#f6c25b;}p,li{line-height:1.6;} .muted{color:#aeb7c5;}table{border-collapse:collapse;}td,th{padding:7px;border-bottom:1px solid #384452;text-align:left;}</style></head><body>",
             f"<h1>{esc(mission['title'])}</h1>",
             f"<p>{esc(mission.get('map_name'))} · {esc(mission.get('kind'))} · <b>{esc(mission.get('status','待確認'))}</b></p>",
             f"<p>{esc(mission.get('summary',''))}</p>"]
    parts += ["<h2>出發前準備</h2>", "<table><tr><th>性質</th><th>物品／條件</th><th>數量</th><th>說明</th></tr>"]
    for item in mission.get("checklist", []):
        parts.append(f"<tr><td>{'必備' if item.get('required') else '建議'}</td><td>{esc(item.get('item',''))}</td><td>{esc(item.get('quantity','依階段'))}</td><td>{esc(item.get('note',''))}</td></tr>")
    parts += ["</table>", "<h2>任務攻略</h2>"]
    if "個別細節待補" in str(mission.get("guide_coverage", "")):
        parts.append("<p class='muted'>個別細節待補。</p>")
    parts.append("<ol>" + "".join(f"<li>{esc(x)}</li>" for x in mission.get("guide", [])) + "</ol>")
    if mission.get("mechanics"):
        parts += ["<h2>任務機制</h2>", bullets(mission["mechanics"])]
    if mission.get("progress"):
        parts += ["<h2>任務進度</h2>", bullets(f"{q['text']}：{q['state']}" + ("；" + "；".join(q.get("details", [])) if q.get("details") else "") for q in mission["progress"])]
    parts += ["<h2>風險與補給</h2>", bullets(mission.get("risks", [])), "<h2>獎勵與解鎖</h2>", bullets(mission.get("rewards", []))]
    if mission.get("source_objectives") or mission.get("objectives"):
        parts += ["<h2>任務目標</h2>", bullets(x.get("text", "") if isinstance(x, dict) else x for x in (mission.get("source_objectives") or mission.get("objectives", [])))]
    parts.append("</body></html>")
    return "".join(parts)
