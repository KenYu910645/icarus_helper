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
PARTIAL_SCOPE = "Reconstructed individual baseline; excludes owner bonuses and unsaved temporary effects such as cold, food, and auras. This is not the complete current maximum."


@lru_cache(maxsize=1)
def load_capacity_catalog():
    folder = Path(__file__).with_name("data")
    return tuple(json.loads((folder / name).read_text(encoding="utf-8")) for name in ("animal_capacities.json", "animal_capacity_effects.json"))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _int(value):
    if not isinstance(value, int) or isinstance(value, bool) or not -(2**31) <= value < 2**31:
        raise ValueError("Stat integer is missing or outside the native format")
    return value


def _f(value):
    result = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if not math.isfinite(result):
        raise ValueError("Curve contains an invalid value")
    return result


def evaluate_curve(curve, time):
    """UE4 unweighted rich curve with single-precision intermediate operations."""
    keys = curve.get("keys", [])
    if not keys or not _finite(time):
        raise ValueError("A verifiable curve is missing")
    times = [_f(k["Time"]) for k in keys]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Curve times are out of order")
    time = _f(time)
    if time < times[0] or time > times[-1]:
        raise ValueError("Outside the verified curve range")
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
            raise ValueError("Unsupported curve interpolation")
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
    raise ValueError("Curve interval is missing")


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
        raise ValueError("Status effect data is missing")
    for row in modifiers:
        known = effects.get("modifiers", {}).get(row.get("RowName"))
        if known is None or supply_keys.intersection(known.get("max_stats", {})):
            raise ValueError("Unrecognized or unsupported supply capacity effect")
    for row in record.get("Genetics", []) or []:
        if row.get("GeneticValueName") not in effects.get("genetics", {}):
            raise ValueError("Unrecognized genetic data")
    for row in record.get("Talents", []) or []:
        if row.get("TalentRowName") not in effects.get("creature_talents", {}):
            raise ValueError("Unrecognized talent data")
    lineage = record.get("Lineage")
    if lineage and lineage != "None" and lineage not in effects.get("lineages", {}):
        raise ValueError("Unrecognized lineage data")
    inventories = record.get("SavedInventories")
    if not isinstance(inventories, list):
        raise ValueError("Equipment data is missing")
    from .inventory import _item_id
    for inv in inventories:
        if not isinstance(inv, dict) or not isinstance(inv.get("Slots"), list):
            raise ValueError("Equipment slot format is unknown")
        ident = inv.get("InventoryID")
        if not isinstance(ident, int) or isinstance(ident, bool) or ident not in (1, 17, 19, 20):
            raise ValueError("Unrecognized inventory; equipment scope cannot be confirmed")
        if ident != 17:
            continue
        for slot in inv["Slots"]:
            if _item_id(slot) not in items or any(slot.get(key) for key in ("AdditionalStats", "Alterations", "LivingItemSlots")):
                raise ValueError("Unknown equipment or equipment with unexpanded bonuses")


def reconstruct_capacities(record, level, static=None, effects=None, owner_info=None):
    result = {key: {"maximum": None, "maximum_status": "unknown", "detail": "A verifiable species capacity is missing."} for key in STAT_NAMES}
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
                               "detail": f"Current-version D_AISetup → D_AIGrowth {key} capacity; NPC talents, genetics, equipment, and effect tables have been checked."}
        stat_values = {}
        references = []

        def add(values):
            for stat, value in values.items():
                stat_values[stat] = _int(stat_values.get(stat, 0) + _int(value))

        def add_curves(values, x):
            for stat, curve_id in values.items():
                curve = effects.get("curves", {}).get(curve_id)
                if curve is None:
                    raise ValueError("Genetic/lineage curve is missing")
                add({stat: math.trunc(evaluate_curve(curve, x))})

        if not isinstance(level, int) or isinstance(level, bool) or level < 0:
            raise ValueError("Animal level is unverified")
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
                    raise ValueError("Growth curve is missing")
                # Native InitGrowthStats writes the truncated curve to its base
                # contribution; no +1 is added to CreatureLevel.
                add({field: math.trunc(evaluate_curve(curve, level))})
                species_sources.add(key)
        references.append("Species growth curve/level")
        if record.get("bHasGeneratedGenetics") is not True:
            raise ValueError("Genetics have not been generated or saved")
        genes = record.get("Genetics")
        if not isinstance(genes, list):
            raise ValueError("Genetic data is missing")
        seen = set()
        for gene in genes:
            ident, value = gene.get("GeneticValueName"), gene.get("Value")
            if ident in seen or ident not in effects.get("genetics", {}):
                raise ValueError("Genetic data is unknown or conflicting")
            seen.add(ident)
            add_curves(effects["genetics"][ident].get("base", {}), _int(value))
        required = {key for key, row in effects.get("genetics", {}).items() if row.get("base")}
        if not required <= seen:
            raise ValueError("Genetic data affecting capacity is incomplete")
        references.append("Genetics")
        lineage = effects.get("lineages", {}).get(record.get("Lineage"))
        if lineage is None:
            raise ValueError("Lineage data is unknown")
        add(lineage.get("stats", {}))
        add_curves(lineage.get("growth", {}), level)
        references.append("Lineage")
        talents = record.get("Talents")
        if not isinstance(talents, list):
            raise ValueError("Talent data is missing")
        seen.clear()
        for talent in talents:
            ident, rank = talent.get("TalentRowName"), talent.get("TalentRank")
            ranks = effects.get("creature_talents", {}).get(ident)
            if ident in seen or not isinstance(rank, int) or isinstance(rank, bool) or ranks is None or not 0 <= rank <= len(ranks):
                raise ValueError("Unrecognized talent or rank")
            seen.add(ident)
            if rank:
                add(ranks[rank - 1])
        references.append("Talent ranks")
        modifiers = record.get("Modifiers")
        if not isinstance(modifiers, list):
            raise ValueError("Status effect data is missing")
        for modifier in modifiers:
            definition = effects.get("modifiers", {}).get(modifier.get("RowName"))
            if definition is None:
                raise ValueError("Unrecognized status effect")
            if definition.get("max_stats"):
                raise ValueError("Status effect stacking/validity has not been fully reconstructed")
        if record.get("StomachContents") != []:
            raise ValueError("Food effects have not been fully reconstructed")
        inventories = record.get("SavedInventories")
        if not isinstance(inventories, list):
            raise ValueError("Equipment data is missing")
        from .inventory import _item_id
        for inv in inventories:
            if not isinstance(inv, dict) or not isinstance(inv.get("Slots"), list):
                raise ValueError("Equipment slot format is unknown")
            for slot in inv["Slots"]:
                ident = _item_id(slot)
                equipped = inv.get("InventoryID") == 17
                if equipped and ident not in items:
                    raise ValueError("Unrecognized saddle/equipment")
                trait = effects.get("equippable_items", {}).get(ident)
                if trait:
                    equip = effects.get("equippables", {}).get(trait)
                    relevant = {prefix + name + suffix for name in STAT_NAMES.values() for prefix, suffix in (("Maximum", "_+"), ("BaseMaximum", "_+"), ("BaseMaximum", "_+%"))}
                    if equip is None or ((equipped or equip.get("all_inventories")) and (relevant.intersection(equip.get("stats", {})) or relevant.intersection(equip.get("global_stats", {})))):
                        raise ValueError("Equipment maximum bonuses have not been fully reconstructed")
        for key in ("health", "stamina"):
            field = "BaseMaximum" + STAT_NAMES[key]
            if key not in species_sources:
                result[key]["detail"] = "The source does not define a verifiable capacity for this species; initial save values are not used to guess a maximum."
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
            detail = (f"Native rules include owner +{transferred}% and individual bonuses; character/Bestiary timestamps come from separate files."
                      " This is the saved persistent maximum; unsaved temporary effects such as cold, food, and auras still require an in-game check."
                      if owner_percent is not None else PARTIAL_SCOPE + " Owner data is missing or incomplete; bonuses are not assumed to be zero.")
            result[key] = {"maximum": maximum, "maximum_status": "persistent" if owner_percent is not None else "reconstructed", "detail": detail,
                           "maximum_components": {"base": stat_values[field + "_+"], "percent": total_percent,
                                                  **({"individual_percent": individual_percent, "owner_percent": owner_percent} if owner_percent is not None else {})},
                           "maximum_sources": references + (["Owner character/account Bestiary/world equipment effects"] if owner_percent is not None else [])}
    except (OSError, KeyError, ValueError, TypeError, OverflowError, AttributeError) as error:
        for key in ("food", "water", "oxygen"):
            if result[key]["maximum"] is None:
                result[key]["detail"] = f"Capacity data could not be reliably verified: {error}."
        for key in ("health", "stamina"):
            result[key] = {"maximum": None, "maximum_status": "unknown", "detail": f"Current maximum could not be reliably reconstructed: {error}."}
    return result
