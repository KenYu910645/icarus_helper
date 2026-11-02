"""Player-tamed animals from one validated world snapshot; no game writes."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .catalog import installed_game_build
from .inventory import _item_id, _quantity, load_item_catalog


ANIMAL_COMPONENTS = frozenset({
    "/Script/Icarus.IcarusMountCharacterRecorderComponent",
    "/Script/Icarus.IcarusCowCharacterRecorderComponent",
    "/Script/Icarus.IcarusJuvenileRecorderComponent",
})
INVENTORY_LABELS = {1: "一般物品欄", 17: "鞍具", 19: "動物背包", 20: "重型貨物"}
STAT_FIELDS = (
    ("health", "生命", "CurrentHealth"), ("food", "食物", "FoodLevel"),
    ("water", "水分", "WaterLevel"), ("oxygen", "氧氣", "OxygenLevel"),
    ("stamina", "耐力", "Stamina"), ("experience", "經驗值", "Experience"),
)


@lru_cache(maxsize=1)
def load_animal_catalog() -> dict:
    data = json.loads((Path(__file__).with_name("data") / "animal_catalog.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("動物目錄頂層格式錯誤")
    return data


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _name(value: Any, fallback: str = "未知") -> str:
    return value if isinstance(value, str) and value and value.casefold() != "none" else fallback


def _label(mapping: dict, key: Any, fallback: str | None = None) -> str:
    if not isinstance(key, (str, int, float)) or isinstance(key, bool):
        return fallback or "未知"
    value = mapping.get(str(key)) if isinstance(mapping, dict) else None
    if value is None and isinstance(mapping, dict) and isinstance(key, (str, int, float)):
        value = mapping.get(key)
    if isinstance(value, dict):
        value = value.get("name") or value.get("label")
    return _name(value, fallback or f"未知（{key}）")


def _enum(catalog: dict, group: str, value: Any) -> str:
    if not isinstance(value, str):
        return "未知"
    mapping = catalog.get("enums", {}).get(group, {})
    return _label(mapping, value, _label(mapping, str(value).split("::")[-1])) if value is not None else "未知"


def _variables(record: dict, key: str, value_key: str) -> dict:
    values = record.get(key)
    if not isinstance(values, list):
        return {}
    result = {}
    for row in values:
        if isinstance(row, dict) and isinstance(row.get("VariableName"), str):
            name = row["VariableName"]
            value = row.get(value_key)
            result[name] = value if name not in result or result[name] == value else None
    return result


def _identity(record: dict) -> str | None:
    value = record.get("IcarusActorGUID")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    if isinstance(value, str) and (re.fullmatch(r"[1-9][0-9]*", value) or re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value)):
        return value
    return None


def _ownership(record: dict) -> str:
    native_state = record.get("TamedState", "")
    state = {0: "NotTamed", 1: "Tamed", 2: "Domesticated"}.get(native_state, "") if isinstance(native_state, int) and not isinstance(native_state, bool) else str(native_state).split("::")[-1]
    wild = _variables(record, "BoolVariables", "bVariable").get("bIsWildTame")
    owner = record.get("OwnerCharacterID")
    owner = owner if isinstance(owner, dict) else {}
    player = owner.get("PlayerID")
    valid_owner = _owner_identity(record) is not None
    if state == "NotTamed" or wild is True:
        return "excluded"
    if record.get("_component") == "/Script/Icarus.IcarusJuvenileRecorderComponent" and state not in {"Tamed", "Domesticated"}:
        return "unknown" if player else "excluded"
    if valid_owner and wild is False:
        return "owned"
    # A leader is not an owner. Explicitly tamed juveniles may lack owner data.
    if state in {"Tamed", "Domesticated"} and wild is False and not player:
        return "explicit_tamed"
    return "unknown" if player or state in {"Tamed", "Domesticated"} else "excluded"


def _owner_identity(record: dict) -> tuple[str, int] | None:
    """Private source identity; never included in the public animal summary."""
    owner = record.get("OwnerCharacterID")
    if not isinstance(owner, dict):
        return None
    player, slot = owner.get("PlayerID"), owner.get("ChrSlot")
    if not isinstance(player, str) or player.strip().casefold() in {"", "0", "-1", "none", "null"}:
        return None
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        return None
    return player.strip(), slot


def _owner_key(record: dict) -> str:
    """Stable grouping key separates namesakes without exposing platform IDs."""
    identity = _owner_identity(record)
    if identity is None:
        return "unknown"
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "owner:" + hashlib.sha256(b"icarus-helper-owner-v1\0" + encoded).hexdigest()


def _position(record: dict) -> list | None:
    transform = record.get("ActorTransform")
    xyz = transform.get("Translation") if isinstance(transform, dict) else None
    if not isinstance(xyz, (list, tuple)) or len(xyz) != 3:
        return None
    return list(xyz) if all(_number(v) is not None for v in xyz) else None


def _level(record: dict, catalog: dict, species: dict, raw_level: Any) -> tuple[int | None, str]:
    note = "目前等級尚未驗證；最後等級記錄僅供核對。"
    rule = catalog.get("level_rule") or {}
    if not isinstance(rule, dict) or rule.get("verified") is not True:
        return None, note
    if rule.get("type") == "saved_integer" and rule.get("field") == "LastLevelAchieved" and isinstance(raw_level, int) and not isinstance(raw_level, bool):
        offset = rule.get("offset", 0)
        if isinstance(offset, int) and not isinstance(offset, bool) and raw_level + offset >= 0:
            return raw_level + offset, str(rule.get("note") or "依已驗證存檔等級規則。")
    if rule.get("type") != "curve_thresholds":
        return None, note
    curve = _verified_curve(catalog, species)
    if curve is None:
        return None, note
    thresholds, display_maximum = curve
    experience = _number(record.get("Experience"))
    if experience is None or experience < 0:
        return None, "經驗值缺失或不合法，無法核對目前等級。"
    # Native GetLevel compares integer-truncated curve thresholds; no +1.
    level = max(index for index, threshold in enumerate(thresholds) if experience >= threshold)
    return min(level, display_maximum), "依目前版本的動物經驗曲線計算。"


def _verified_curve(catalog: dict, species: dict) -> tuple[list[int], int] | None:
    """Return only the same validated native thresholds used for displayed level."""
    rule = catalog.get("level_rule")
    if not isinstance(rule, dict) or rule.get("verified") is not True or rule.get("type") != "curve_thresholds":
        return None
    curves = rule.get("curves")
    curve = curves.get(species.get("level_growth_id")) if isinstance(curves, dict) else None
    if not isinstance(curve, dict):
        return None
    thresholds = curve.get("thresholds")
    maximum = curve.get("max_level")
    display_maximum = curve.get("max_display_level")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0 or not isinstance(display_maximum, int) or isinstance(display_maximum, bool) or display_maximum < 0:
        return None
    if not isinstance(thresholds, list) or len(thresholds) != maximum + 1 or not thresholds or thresholds[0] != 0:
        return None
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in thresholds) or any(a >= b for a, b in zip(thresholds, thresholds[1:])):
        return None
    return thresholds, min(maximum, display_maximum)


def _experience_progress(record: dict, catalog: dict, species: dict) -> dict | None:
    """XP toward the next displayed level; unknown curves never imply a zero bar."""
    curve = _verified_curve(catalog, species)
    total = _number(record.get("Experience"))
    if curve is None or total is None or total < 0:
        return None
    thresholds, cap = curve
    level = min(max(index for index, threshold in enumerate(thresholds) if total >= threshold), cap)
    start = thresholds[level]
    capped = level >= cap
    end = None if capped else thresholds[level + 1]
    return {"value": 0 if capped else total - start,
            "maximum": None if capped else end - start,
            "total": total, "level_start": start,
            "next_level": None if capped else level + 1,
            "next_level_total": end, "capped": capped}


def _inventories(record: dict, items: dict, coverage: dict) -> tuple[list, str, list]:
    warnings = []
    saved = record.get("SavedInventories")
    if not isinstance(saved, list):
        coverage["unavailable_inventories"] += 1
        return [], "unavailable", ["背包資料缺失或未解碼；不能判定為空。"]
    canonical = {key.casefold(): key for key in items["items"]}
    dynamic_ids = items["dynamic_properties"]
    collected = {}
    for inv in saved:
        if not isinstance(inv, dict) or not isinstance(inv.get("InventoryID"), int) or isinstance(inv.get("InventoryID"), bool) or not isinstance(inv.get("Slots"), list):
            coverage["malformed_inventories"] += 1
            warnings.append("部分背包格式無法讀取。")
            continue
        inventory_id = inv["InventoryID"]
        group = collected.setdefault(inventory_id, {})
        for index, slot in enumerate(inv["Slots"]):
            if not isinstance(slot, dict):
                warnings.append("部分物品槽格式無法讀取。")
                coverage["malformed_inventories"] += 1
                continue
            location = slot.get("Location")
            valid_location = isinstance(location, int) and not isinstance(location, bool) and location >= 0
            key = location if valid_location else ("unlocated", index)
            candidates = group.setdefault(key, [])
            if slot not in candidates:
                candidates.append(slot)
            else:
                coverage["duplicate_slots"] += 1
            if not valid_location:
                warnings.append("部分物品槽沒有有效位置，保留為未知。")
    output = []
    for inventory_id, groups in sorted(collected.items()):
        label = INVENTORY_LABELS.get(inventory_id, f"未知背包（{inventory_id}）")
        inv_warnings = []
        if inventory_id not in INVENTORY_LABELS:
            inv_warnings.append("未驗證的背包類型；僅顯示可辨識物品。")
            coverage["unknown_inventory_ids"].append(inventory_id)
        rows = []
        for location, candidates in groups.items():
            conflict = len(candidates) > 1
            if conflict:
                coverage["conflicting_slots"] += 1
                inv_warnings.append("相同背包槽有衝突，物品數量保留未知。")
            seen = set()
            for slot in candidates:
                item_id = _item_id(slot)
                if not item_id:
                    continue
                item_id = canonical.get(item_id.casefold(), item_id)
                if item_id in seen:
                    continue
                seen.add(item_id)
                quantity = None if conflict else _quantity(slot, dynamic_ids["ItemableStack"])
                if quantity == 0:
                    continue
                definition = items["items"].get(item_id)
                row_warnings = []
                if definition is None:
                    coverage["unknown_items"] += 1
                    row_warnings.append("物品不在目前版本目錄中。")
                if quantity is None:
                    coverage["unknown_stacks"] += 1
                    row_warnings.append("數量資料缺失或衝突。")
                dynamic = slot.get("DynamicData", slot.get("ItemDynamicData", []))
                linked = isinstance(dynamic, list) and any(isinstance(e, dict) and e.get("Index", e.get("Type")) == dynamic_ids["InventoryContainer_LinkedInventoryId"] for e in dynamic)
                nested = bool(slot.get("LivingItemSlots"))
                if linked or nested:
                    coverage["unexpanded_item_inventories"] += 1
                    row_warnings.append("內嵌物品箱／物品槽未展開，內容不計入這份背包清單。")
                rows.append({"id": item_id, "name": (definition or {}).get("name", item_id),
                             "quantity": quantity, "slot": location if isinstance(location, int) else None,
                             "warnings": row_warnings, "nested_inventory_status": "unexpanded" if linked or nested else "none"})
                inv_warnings.extend(row_warnings)
        status = "partial" if inv_warnings else "ok" if rows else "empty"
        output.append({"id": inventory_id, "label": label, "items": rows,
                       "status": status, "warnings": list(dict.fromkeys(inv_warnings))})
        warnings.extend(inv_warnings)
    return output, "partial" if warnings else "ok" if any(i["items"] for i in output) else "empty", list(dict.fromkeys(warnings))


def _animal(record: dict, catalog: dict, item_catalog: dict, base: dict | None, radius: float, coverage: dict, owner_info=None) -> dict:
    species_id = _name(record.get("AISetupRowName"), "")
    species = catalog.get("species", {}).get(species_id, {})
    warnings = []
    if not species:
        warnings.append("此動物種類尚未與目前版本目錄對照。")
        coverage["unknown_species"] += 1
    details = []
    integers = _variables(record, "IntVariables", "iVariable")
    raw_level = integers.get("LastLevelAchieved")
    if _number(raw_level) is not None:
        details.append({"label": "最後等級記錄（原值）", "value": str(raw_level)})
    level, level_note = _level(record, catalog, species, raw_level)
    health_record = record.get("CharacterRecord")
    health = _number(health_record.get("CurrentHealth")) if isinstance(health_record, dict) else None
    capacities = {}
    if catalog.get("capacity_rule", {}).get("verified") is True:
        from .animal_capacities import reconstruct_capacities
        capacities = reconstruct_capacities(record, level, owner_info=owner_info)
    stats = []
    for stat_id, label, field in STAT_FIELDS:
        value = health if stat_id == "health" else _number(record.get(field))
        coverage["unknown_stats"] += value is None
        capacity = capacities.get(stat_id, {})
        capacity_status = capacity.get("maximum_status", "unknown")
        if stat_id != "experience":
            bucket = coverage.setdefault("capacity_status_counts", {})
            bucket[capacity_status] = bucket.get(capacity_status, 0) + 1
        stats.append({"id": stat_id, "label": label, "value": value, "maximum": capacity.get("maximum"),
                      "maximum_status": capacity_status,
                      "maximum_components": capacity.get("maximum_components"),
                      "maximum_sources": capacity.get("maximum_sources", []),
                      "unit": "經驗值" if stat_id == "experience" else "存檔數值",
                      "detail": "資料未知" if value is None else capacity.get("detail", "目前存檔值；未取得可驗證的目前上限。")})
    if "CurrentMilk" in record:
        value = _number(record.get("CurrentMilk"))
        stats.append({"id": "milk", "label": "目前乳量", "value": value, "maximum": None,
                      "unit": "存檔數值", "detail": "未換算為物品件數或容量單位。"})
    movement = _enum(catalog, "movement", record.get("MovementBehaviourState"))
    for group, field, label in (("movement", "MovementBehaviourState", "活動行為"),
                                ("combat", "CombatBehaviourState", "戰鬥行為"),
                                ("consumption", "ConsumptionBehaviourState", "進食設定"),
                                ("grazing", "GrazingBehaviourState", "覓食行為")):
        details.append({"label": label, "value": _enum(catalog, group, record.get(field))})
    modifiers = record.get("Modifiers")
    modifier_names = []
    if not isinstance(modifiers, list):
        warnings.append("狀態效果資料缺失，不能判定沒有異常。")
    else:
        for modifier in modifiers:
            if not isinstance(modifier, dict):
                warnings.append("部分狀態效果無法讀取。")
                continue
            row = modifier.get("RowName")
            modifier_names.append(_label(catalog.get("modifiers", {}), row))
    if modifier_names:
        details.append({"label": "狀態效果", "value": "、".join(modifier_names)})
    empty = [label for field, label in (("FoodLevel", "食物"), ("WaterLevel", "水分")) if _number(record.get(field)) == 0]
    if health == 0:
        status = "生命為 0（存檔）"
    elif empty:
        status = "需照料：" + "／".join(empty) + "為 0"
    elif modifier_names:
        status = "、".join(modifier_names)
    else:
        status = ("存活・" if health is not None and health > 0 else "生命狀態未知・") + movement
    for field, group, label in (("Sex", "sex", "性別"), ("Lineage", "lineages", "血統")):
        value = record.get(field)
        mapping = catalog.get("enums", {}).get(group, {}) if field == "Sex" else catalog.get(group, {})
        details.append({"label": label, "value": _label(mapping, value) if value is not None else "未知"})
    for field, label in (("Variation", "外觀變異（原值）"), ("GestationProgress", "妊娠進度（原值）"),
                         ("MotherName", "母系名稱"), ("FatherName", "父系名稱")):
        value = record.get(field)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and value != "":
            details.append({"label": label, "value": str(value)})
    for field, key, name_field, value_field, label in (("Genetics", "genetics", "GeneticValueName", "Value", "遺傳"),
                                                      ("Talents", "talents", "TalentRowName", "TalentRank", "天賦")):
        rows = record.get(field)
        if isinstance(rows, list):
            values = [f"{_label(catalog.get(key, {}), row.get(name_field))}：{row.get(value_field) if _number(row.get(value_field)) is not None else '未知'}" for row in rows if isinstance(row, dict)]
            if values:
                details.append({"label": label, "value": "；".join(values)})
    position = _position(record)
    distance = math.hypot(position[0] - base["x"], position[1] - base["y"]) / 100 if position is not None and base is not None else None
    inventories, inventory_status, inv_warnings = _inventories(record, item_catalog, coverage)
    warnings.extend(inv_warnings)
    unsupported = record.get("_unsupported_fields", [])
    if unsupported:
        warnings.append("部分動物欄位未解碼；顯示內容可能缺漏。")
    if _ownership(record) == "explicit_tamed":
        warnings.append("存檔明確記錄已馴服，但未記錄主人；主人顯示未知。")
    return {"id": _identity(record), "name": _name(record.get("MountName"), "未命名動物"),
            "species": _name(species.get("english"), species_id or "Unknown species"),
            "species_zh": _label(catalog.get("species", {}), species_id), "species_id": species_id,
            "owner_name": _name(record.get("OwnerName")) if _owner_identity(record) is not None else "未知",
            "owner_key": _owner_key(record), "level": level, "level_note": level_note,
            "owner_capacity": owner_info or {},
            "experience": _number(record.get("Experience")),
            "experience_progress": _experience_progress(record, catalog, species), "status_label": status,
            "distance_m": distance, "at_home": distance <= radius if distance is not None else None,
            "position": position, "stats": stats, "details": details, "inventories": inventories,
            "inventory_status": inventory_status, "warnings": list(dict.fromkeys(warnings))}


def summarize_animals(snapshot: Any, base: dict | None = None, radius_m: float = 100, catalog: dict | None = None, owner_profile_roots=None) -> dict:
    """List every proven player tame; distance is optional, never ownership proof."""
    coverage = {"status": "ok", "reason": "", "warnings": [], "snapshot_time": str(_get(snapshot, "saved_at", "")),
                "total_tamed": 0, "at_home": 0, "outside_home": 0, "unknown_location": 0,
                "home_unknown": 0, "excluded_animals": 0, "uncertain_ownership": 0,
                "duplicate_records": 0, "conflicting_records": 0, "missing_identity": 0,
                "unsupported_records": 0, "unknown_species": 0, "unknown_stacks": 0, "unknown_stats": 0,
                "unknown_items": 0, "unknown_inventory_ids": [], "duplicate_slots": 0,
                "conflicting_slots": 0, "malformed_inventories": 0, "unavailable_inventories": 0,
                "unexpanded_item_inventories": 0, "distance_type": "水平直線距離", "radius_m": radius_m}
    result = {"animals": [], "coverage": coverage}
    if _get(snapshot, "is_shared_map", False):
        coverage.update(status="unavailable", reason="尚未連接主機存檔，無法讀取馴服動物。")
        return result
    radius = _number(radius_m)
    if radius is None or radius <= 0:
        coverage.update(status="invalid_radius", reason="基地範圍必須大於零。")
        return result
    try:
        catalog = load_animal_catalog() if catalog is None else catalog
        item_catalog = load_item_catalog()
        if not isinstance(catalog, dict) or not isinstance(item_catalog, dict):
            raise ValueError("目錄格式錯誤")
    except (OSError, ValueError, TypeError):
        coverage.update(status="unavailable", reason="動物或物品目錄無法讀取。")
        return result
    actual_build = installed_game_build()
    coverage.update(game_build=catalog.get("game_build"), installed_game_build=actual_build)
    if not catalog.get("game_build") or (actual_build is not None and str(actual_build) != str(catalog["game_build"])) or str(item_catalog.get("game_build")) != str(catalog["game_build"]):
        coverage.update(status="unsupported_version", reason="遊戲或資料目錄版本不符；動物狀態暫不推算。")
        return result
    snapshot_coverage = _get(snapshot, "coverage", {})
    source_version = _get(snapshot, "save_format_version", snapshot_coverage.get("save_format_version") if isinstance(snapshot_coverage, dict) else None)
    source_warnings = _get(snapshot, "warnings", [])
    if (source_version is not None and source_version != 2) or (isinstance(source_warnings, list) and any("存檔世界格式版本" in str(w) and "尚未驗證" in str(w) for w in source_warnings)):
        coverage.update(status="unsupported_version", reason="存檔世界格式版本尚未驗證；動物狀態暫不推算。")
        return result
    records = _get(snapshot, "raw_records")
    if not isinstance(records, list):
        coverage.update(status="unavailable", reason="目前快照沒有可辨識的動物紀錄。")
        return result
    owner_summary = {}
    if catalog.get("owner_capacity_rule", {}).get("verified") is True:
        from .owner_stats import summarize_owner_stats
        owner_summary = summarize_owner_stats(snapshot, profile_roots=owner_profile_roots)
        result["owner_stats"] = owner_summary
        coverage["owner_stats"] = owner_summary.get("coverage", {})
    if not isinstance(base, dict) or _number(base.get("x")) is None or _number(base.get("y")) is None:
        base = None
    elif (base.get("world_id") and base["world_id"] != _get(snapshot, "world_id")) or (base.get("map_name") and base["map_name"] != _get(snapshot, "map_name")):
        coverage["warnings"].append("基地與目前世界不符，距離及在家狀態保留未知。")
        base = None
    versions = catalog.get("supported_recorder_versions", [3])
    unique, conflicts = {}, set()
    for record in records:
        if not isinstance(record, dict) or record.get("_component") not in ANIMAL_COMPONENTS:
            continue
        uid = _identity(record)
        if uid is None:
            if _ownership(record) not in {"excluded", "unknown"}:
                coverage["missing_identity"] += 1
            continue
        if uid in unique:
            if unique[uid] == record:
                coverage["duplicate_records"] += 1
            else:
                conflicts.add(uid)
            continue
        unique[uid] = record
    coverage["conflicting_records"] = len(conflicts)
    for uid, record in unique.items():
        if uid in conflicts:
            continue
        ownership = _ownership(record)
        if ownership == "excluded":
            coverage["excluded_animals"] += 1
            continue
        if ownership == "unknown":
            coverage["uncertain_ownership"] += 1
            continue
        if record.get("ActorStateRecorderVersion") not in versions:
            coverage["unsupported_records"] += 1
            continue
        animal = _animal(record, catalog, item_catalog, base, radius, coverage,
                         owner_summary.get("owners", {}).get(_owner_key(record)))
        result["animals"].append(animal)
        coverage["unknown_location"] += animal["position"] is None
        coverage["at_home"] += animal["at_home"] is True
        coverage["outside_home"] += animal["at_home"] is False
        coverage["home_unknown"] += animal["at_home"] is None
    result["animals"].sort(key=lambda a: (a["name"].casefold(), a["id"]))
    if catalog.get("live_capacity_rule", {}).get("verified") is True:
        from .live_animal_stats import read_live_animal_stats
        _apply_live_stats(result, read_live_animal_stats(snapshot))
    coverage["total_tamed"] = len(result["animals"])
    coverage["unknown_inventory_ids"] = sorted(set(coverage["unknown_inventory_ids"]))
    if coverage["warnings"] or any(coverage[k] for k in ("conflicting_records", "missing_identity", "unsupported_records", "uncertain_ownership", "unknown_species", "unknown_stacks", "unknown_stats", "unknown_location", "unknown_items", "unknown_inventory_ids", "conflicting_slots", "malformed_inventories", "unavailable_inventories", "unexpanded_item_inventories")) or any(a["warnings"] for a in result["animals"]):
        coverage.update(status="partial", reason="部分動物資料缺失、未驗證或有衝突，請查看明細警告。")
    return result


def _apply_live_stats(summary, live):
    """Never splice a live denominator with an old saved numerator."""
    summary["live_stats"] = live
    coverage = summary["coverage"]
    coverage["live_stats"] = {key: live.get(key) for key in ("status", "reason", "sample_time", "world_verified", "coverage")}
    if live.get("world_verified") is not True or live.get("status") not in ("ok", "partial"):
        return
    applied_animals, applied_pairs = set(), 0
    for animal in summary["animals"]:
        sampled = live.get("animals", {}).get(str(animal["id"]), {})
        for stat in animal["stats"]:
            key = stat.get("id")
            values = sampled.get(key, {})
            if key not in ("health", "stamina") or _number(values.get("value")) is None or _number(values.get("maximum")) is None or values["maximum"] <= 0 or values["value"] < 0:
                continue
            stat["saved_value"], stat["persistent_maximum"] = stat["value"], stat["maximum"]
            stat.update(value=values["value"], maximum=values["maximum"], maximum_status="live", unit="遊戲目前值",
                        sample_time=live.get("sample_time"),
                        detail="所選世界／動物識別已核對；目前值與上限一起唯讀取得自遊戲狀態，包含遊戲已套用的飼養者與臨時效果。地圖位置仍為存檔位置。",
                        maximum_sources=["遊戲原生 CharacterState 目前值／最大值快取"])
            applied_animals.add(str(animal["id"]))
            applied_pairs += 1
            if key == "health":
                animal["status_label"] = "生命為 0（遊戲）" if values["value"] == 0 else "存檔：" + animal.get("status_label", "")
    coverage["live_stats"].update(applied_animals=len(applied_animals), applied_pairs=applied_pairs)
    counts = {}
    for animal in summary["animals"]:
        for stat in animal["stats"]:
            if stat.get("id") in {"health", "food", "water", "oxygen", "stamina"}:
                status = stat.get("maximum_status", "unknown")
                counts[status] = counts.get(status, 0) + 1
    coverage["capacity_status_counts"] = counts
