"""Version-bound game capacities; separate exact supplies from partial individuals.

The recorder deliberately excludes SaveToDatabase=false effects. Owner character
talents also live outside this world snapshot. Health/stamina reconstruction must
therefore never be advertised as the complete live maximum.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import struct


BUILD = "26313227"
STAT_NAMES = {"health": "Health", "food": "Food", "water": "Water", "oxygen": "Oxygen", "stamina": "Stamina"}
PARTIAL_SCOPE = "個體基準重建；未含飼養者加成、寒冷／食物／光環等未保存的臨時效果。並非完整目前上限。"


@lru_cache(maxsize=1)
def load_capacity_catalog():
    folder = Path(__file__).with_name("data")
    return tuple(json.loads((folder / name).read_text(encoding="utf-8")) for name in ("animal_capacities.json", "animal_capacity_effects.json"))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _int(value):
    if not isinstance(value, int) or isinstance(value, bool) or not -(2**31) <= value < 2**31:
        raise ValueError("屬性整數缺失或超出原生格式")
    return value


def _f(value):
    result = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if not math.isfinite(result):
        raise ValueError("曲線含不合法數值")
    return result


def evaluate_curve(curve, time):
    """UE4 unweighted rich curve with single-precision intermediate operations."""
    keys = curve.get("keys", [])
    if not keys or not _finite(time):
        raise ValueError("缺少可驗證曲線")
    times = [_f(k["Time"]) for k in keys]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("曲線時間順序錯誤")
    time = _f(time)
    if time < times[0] or time > times[-1]:
        raise ValueError("超出已驗證曲線範圍")
    if time == times[-1]:
        return _f(keys[-1]["Value"])
    for index, (left, right) in enumerate(zip(keys, keys[1:])):
        if not times[index] <= time < times[index + 1]:
            continue
        p0, p3 = _f(left["Value"]), _f(right["Value"])
        mode = left.get("InterpMode")
        if mode == "RCIM_Constant":
            return p0
        delta = _f(times[index + 1] - times[index])
        alpha = _f(_f(time - times[index]) / delta)
        if mode == "RCIM_Linear":
            return _f(p0 + _f(_f(p3 - p0) * alpha))
        if mode != "RCIM_Cubic" or any(k.get("TangentWeightMode") != "RCTWM_WeightedNone" for k in (left, right)):
            raise ValueError("未支援的曲線插值")
        # Native unweighted cubic, same operation sequence as the XP rebuild.
        m0 = _f(_f(delta * _f(left["LeaveTangent"])) * _f(1 / 3))
        m1 = _f(_f(delta * _f(right["ArriveTangent"])) * _f(1 / 3))
        p1, p2 = _f(p0 + m0), _f(p3 - m1)
        a01, a23 = _f(p0 + _f(m0 * alpha)), _f(p2 + _f(m1 * alpha))
        difference = _f(p2 - p1)
        a12 = _f(p1 + _f(difference * alpha))
        q, p = _f(_f(a12 - a01) + m0), _f(_f(a23 - a12) + difference)
        b12, b01 = _f(p1 + _f(p * alpha)), _f(p0 + _f(q * alpha))
        return _f(p0 + _f(_f(_f(b12 - b01) + q) * alpha))
    if len(keys) == 1 and time == times[0]:
        return _f(keys[0]["Value"])
    raise ValueError("曲線區間缺失")


def native_capacity(base, percent, minimum=0):
    """Signed int32 arithmetic and truncation toward zero; health/stamina min1."""
    base, percent = _int(base), _int(percent)
    total_percent = _int(100 + percent)
    product = _int(base * total_percent)
    value = abs(product) // 100 * (-1 if product < 0 else 1)
    return max(minimum, value) if minimum else value


def _check_supply_sources(record, effects, items):
    """Known ineffective sources differ from unrecognised or modified gear."""
    supply_keys = {prefix + name + suffix for name in ("Food", "Water", "Oxygen") for prefix, suffix in (("Maximum", "_+"), ("BaseMaximum", "_+"), ("BaseMaximum", "_+%"))}
    modifiers = record.get("Modifiers")
    if not isinstance(modifiers, list):
        raise ValueError("狀態效果資料缺失")
    for row in modifiers:
        known = effects.get("modifiers", {}).get(row.get("RowName"))
        if known is None or supply_keys.intersection(known.get("max_stats", {})):
            raise ValueError("未辨識或未支援的補給容量效果")
    for row in record.get("Genetics", []) or []:
        if row.get("GeneticValueName") not in effects.get("genetics", {}):
            raise ValueError("未辨識的遺傳資料")
    for row in record.get("Talents", []) or []:
        if row.get("TalentRowName") not in effects.get("creature_talents", {}):
            raise ValueError("未辨識的天賦資料")
    lineage = record.get("Lineage")
    if lineage and lineage != "None" and lineage not in effects.get("lineages", {}):
        raise ValueError("未辨識的血統資料")
    inventories = record.get("SavedInventories")
    if not isinstance(inventories, list):
        raise ValueError("裝備資料缺失")
    from .inventory import _item_id
    for inv in inventories:
        if not isinstance(inv, dict) or not isinstance(inv.get("Slots"), list):
            raise ValueError("裝備槽格式未知")
        ident = inv.get("InventoryID")
        if not isinstance(ident, int) or isinstance(ident, bool) or ident not in (1, 17, 19, 20):
            raise ValueError("未辨識的物品欄，不能確認裝備作用範圍")
        if ident != 17:
            continue
        for slot in inv["Slots"]:
            if _item_id(slot) not in items or any(slot.get(key) for key in ("AdditionalStats", "Alterations", "LivingItemSlots")):
                raise ValueError("未知或含未展開加成的裝備")


def reconstruct_capacities(record, level, static=None, effects=None, owner_info=None):
    result = {key: {"maximum": None, "maximum_status": "unknown", "detail": "缺少可驗證的物種容量。"} for key in STAT_NAMES}
    try:
        if static is None or effects is None:
            static, effects = load_capacity_catalog()
        if any(c.get("game_build") != BUILD or c.get("schema_version") != 1 for c in (static, effects)):
            return result
        species = static.get("species", {}).get(record.get("AISetupRowName"))
        if not species or species.get("source_survival_character") is not True:
            return result
        from .inventory import load_item_catalog
        items = load_item_catalog().get("items", {})
        _check_supply_sources(record, effects, items)
        bases = species.get("base_capacities", {})
        # Current NPC tables contain no food/water maximum modifiers or owner
        # conversions; the only oxygen modifier explicitly excludes NPCs.
        for key in ("food", "water", "oxygen"):
            value = bases.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[key] = {"maximum": value, "maximum_status": "verified",
                               "detail": f"目前版本 D_AISetup → D_AIGrowth 的 {key} 容量；已核對 NPC 天賦、遺傳、裝備與效果表。"}
        stat_values = {}
        references = []

        def add(values):
            for stat, value in values.items():
                stat_values[stat] = _int(stat_values.get(stat, 0) + _int(value))

        def add_curves(values, x):
            for stat, curve_id in values.items():
                curve = effects.get("curves", {}).get(curve_id)
                if curve is None:
                    raise ValueError("缺少遺傳／血統曲線")
                add({stat: math.trunc(evaluate_curve(curve, x))})

        if not isinstance(level, int) or isinstance(level, bool) or level < 0:
            raise ValueError("未驗證的動物等級")
        species_sources = set()
        for key in ("health", "stamina"):
            field = "BaseMaximum" + STAT_NAMES[key] + "_+"
            if key in bases:
                add({field: bases[key]})
                species_sources.add(key)
            curve_id = species.get(key + "_curve")
            if curve_id:
                curve = static.get("curves", {}).get(curve_id)
                if not curve:
                    raise ValueError("缺少生長曲線")
                # Native InitGrowthStats writes the truncated curve to its base
                # contribution; no +1 is added to CreatureLevel.
                add({field: math.trunc(evaluate_curve(curve, level))})
                species_sources.add(key)
        references.append("物種生長曲線／等級")
        if record.get("bHasGeneratedGenetics") is not True:
            raise ValueError("遺傳尚未生成或未保存")
        genes = record.get("Genetics")
        if not isinstance(genes, list):
            raise ValueError("遺傳資料缺失")
        seen = set()
        for gene in genes:
            ident, value = gene.get("GeneticValueName"), gene.get("Value")
            if ident in seen or ident not in effects.get("genetics", {}):
                raise ValueError("遺傳資料未知或衝突")
            seen.add(ident)
            add_curves(effects["genetics"][ident].get("base", {}), _int(value))
        required = {key for key, row in effects.get("genetics", {}).items() if row.get("base")}
        if not required <= seen:
            raise ValueError("影響容量的遺傳資料不完整")
        references.append("遺傳")
        lineage = effects.get("lineages", {}).get(record.get("Lineage"))
        if lineage is None:
            raise ValueError("血統資料未知")
        add(lineage.get("stats", {}))
        add_curves(lineage.get("growth", {}), level)
        references.append("血統")
        talents = record.get("Talents")
        if not isinstance(talents, list):
            raise ValueError("天賦資料缺失")
        seen.clear()
        for talent in talents:
            ident, rank = talent.get("TalentRowName"), talent.get("TalentRank")
            ranks = effects.get("creature_talents", {}).get(ident)
            if ident in seen or not isinstance(rank, int) or isinstance(rank, bool) or ranks is None or not 0 <= rank <= len(ranks):
                raise ValueError("未辨識天賦或階級")
            seen.add(ident)
            if rank:
                add(ranks[rank - 1])
        references.append("天賦階級")
        modifiers = record.get("Modifiers")
        if not isinstance(modifiers, list):
            raise ValueError("狀態效果資料缺失")
        for modifier in modifiers:
            definition = effects.get("modifiers", {}).get(modifier.get("RowName"))
            if definition is None:
                raise ValueError("未辨識狀態效果")
            if definition.get("max_stats"):
                raise ValueError("狀態效果的疊加／有效性尚未完整還原")
        if record.get("StomachContents") != []:
            raise ValueError("食物效果尚未完整還原")
        inventories = record.get("SavedInventories")
        if not isinstance(inventories, list):
            raise ValueError("裝備資料缺失")
        from .inventory import _item_id
        for inv in inventories:
            if not isinstance(inv, dict) or not isinstance(inv.get("Slots"), list):
                raise ValueError("裝備槽格式未知")
            for slot in inv["Slots"]:
                ident = _item_id(slot)
                equipped = inv.get("InventoryID") == 17
                if equipped and ident not in items:
                    raise ValueError("未辨識的鞍具／裝備")
                trait = effects.get("equippable_items", {}).get(ident)
                if trait:
                    equip = effects.get("equippables", {}).get(trait)
                    relevant = {prefix + name + suffix for name in STAT_NAMES.values() for prefix, suffix in (("Maximum", "_+"), ("BaseMaximum", "_+"), ("BaseMaximum", "_+%"))}
                    if equip is None or ((equipped or equip.get("all_inventories")) and (relevant.intersection(equip.get("stats", {})) or relevant.intersection(equip.get("global_stats", {})))):
                        raise ValueError("裝備上限加成尚未完整還原")
        for key in ("health", "stamina"):
            field = "BaseMaximum" + STAT_NAMES[key]
            if key not in species_sources:
                result[key]["detail"] = "來源未定義此物種的可驗證容量，不以初始化存檔值猜上限。"
                continue
            individual_percent = stat_values.get(field + "_+%", 0)
            owner_percent = None
            if isinstance(owner_info, dict) and owner_info.get("persistent_complete", {}).get(key) is True:
                owner_percent = owner_info.get("percent", {}).get(key)
                if not isinstance(owner_percent, int) or isinstance(owner_percent, bool):
                    owner_percent = None
            # Native owner conversion transfers only a positive owner stat and
            # adds it BEFORE the final signed integer division, never after it.
            transferred = max(0, owner_percent) if owner_percent is not None else 0
            total_percent = _int(individual_percent + transferred)
            maximum = native_capacity(stat_values[field + "_+"], total_percent, minimum=1)
            detail = (f"已按原生規則計入飼養者 +{transferred}% 與個體加成；角色／圖鑑為獨立檔案時間。"
                      "此為已保存的持續上限，未保存的寒冷、食物、光環等臨時效果仍需遊戲執行時核對。"
                      if owner_percent is not None else PARTIAL_SCOPE + " 飼養者資料缺失或未完整核對，不以零加成代替。")
            result[key] = {"maximum": maximum, "maximum_status": "persistent" if owner_percent is not None else "reconstructed", "detail": detail,
                           "maximum_components": {"base": stat_values[field + "_+"], "percent": total_percent,
                                                  **({"individual_percent": individual_percent, "owner_percent": owner_percent} if owner_percent is not None else {})},
                           "maximum_sources": references + (["飼養者角色／帳號圖鑑／世界裝備效果"] if owner_percent is not None else [])}
    except (OSError, KeyError, ValueError, TypeError, OverflowError, AttributeError) as error:
        for key in ("food", "water", "oxygen"):
            if result[key]["maximum"] is None:
                result[key]["detail"] = f"容量資料未能可靠核對：{error}。"
        for key in ("health", "stamina"):
            result[key] = {"maximum": None, "maximum_status": "unknown", "detail": f"目前上限未能可靠重建：{error}。"}
    return result
