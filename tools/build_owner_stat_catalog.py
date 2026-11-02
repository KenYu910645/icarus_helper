"""Build owner tame-stat inputs from the verified installed game, read-only.

No player/profile file is accessed. Full tables remain in the build cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BUILD = "26313227"
EXE_SHA = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
TARGETS = {kind: {"base": f"BaseTamedCreatureMaximum{native}_+%", "derived": f"TamedCreatureMaximum{native}_+%"}
           for kind, native in (("health", "Health"), ("stamina", "Stamina"))}
STAT_KEYS = {name for row in TARGETS.values() for name in row.values()}
TABLES = ("Talents/D_Talents.json", "Stats/D_Stats.json", "Stats/D_CharacterStartingStats.json",
          "Items/D_ItemsStatic.json", "Traits/D_Equippable.json", "Traits/D_Armour.json",
          "Armour/D_ArmourSets.json", "Armour/D_ArmourSetBonus.json", "Modifiers/D_ModifierStates.json",
          "Bestiary/D_BestiaryData.json", "Inventory/D_InventoryID.json", "Alterations/D_Alterations.json")


def stat_values(raw):
    values = {}
    for key, value in raw.items():
        if not key.startswith('(Value="') or not key.endswith('")'):
            raise ValueError("Unsupported stat encoding")
        name = key[8:-2]
        if name in STAT_KEYS:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("Owner stat is not a native integer")
            values[name] = value
    return values


def reference(row, key):
    name = row.get(key, {}).get("RowName")
    return name if name and name != "None" else None


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--repak")
    parser.add_argument("--content")
    parser.add_argument("--steam-manifest")
    parser.add_argument("--output", type=Path, default=ROOT / "icarus_helper/data/owner_stat_catalog.json")
    args = parser.parse_args()
    for field, env in (("repak", "ICARUS_REPAK"), ("content", "ICARUS_CONTENT"), ("steam_manifest", "ICARUS_STEAM_MANIFEST")):
        if getattr(args, field):
            os.environ[env] = getattr(args, field)
    from catalog_inventory import CACHE, GAME, REPAK, require_validated_build
    require_validated_build()
    executable = GAME.parent / "Binaries/Win64/Icarus-Win64-Shipping.exe"
    if hashlib.sha256(executable.read_bytes()).hexdigest() != EXE_SHA:
        raise ValueError("Unverified executable")
    work = CACHE / "owner_stat_source"
    work.mkdir(parents=True, exist_ok=True)
    index = subprocess.check_output([str(REPAK), "list", str(GAME / "Data/data.pak"), "-s", ""], text=True).splitlines()
    tables, sources = {}, []
    for name in TABLES:
        matches = [row for row in index if row.endswith("/" + name)]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous table {name}")
        raw = subprocess.check_output([str(REPAK), "get", str(GAME / "Data/data.pak"), matches[0], "-s", ""])
        (work / Path(name).name).write_bytes(raw)
        tables[name] = json.loads(raw)
        sources.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    result = {"schema_version": 1, "game_build": BUILD, "executable_sha256": EXE_SHA,
              "verified_at": "2026-11-02", "sources": sources, "targets": TARGETS,
              "talents": {}, "starting_stats": {}, "items": {}, "equippables": {}, "armour": {},
              "armour_sets": {}, "armour_set_bonuses": {}, "modifiers": {}, "bestiary": {},
              "known_alterations": [], "stat_enum": [], "inventory_ids": {}}
    table = tables["Talents/D_Talents.json"]
    for row in table["Rows"]:
        rewards = row.get("Rewards", table["Defaults"]["Rewards"])
        result["talents"][row["Name"]] = [stat_values(reward.get("GrantedStats", {})) for reward in rewards]
    for row in tables["Stats/D_CharacterStartingStats.json"]["Rows"]:
        result["starting_stats"][row["Name"]] = stat_values(row.get("StatsGranted", {}))
    for row in tables["Items/D_ItemsStatic.json"]["Rows"]:
        result["items"][row["Name"]] = {key.lower(): name for key in ("Equippable", "Armour") if (name := reference(row, key))}
    table = tables["Traits/D_Equippable.json"]
    for raw in table["Rows"]:
        row = {**table["Defaults"], **raw}
        result["equippables"][row["Name"]] = {
            "stats": stat_values(row["GrantedStats"]), "global_stats": stat_values(row["GlobalStat_GrantedStats"]),
            "global_actor_class": row["GlobalStat_ActorClass"], "all_inventories": row["bAppliesInAllInventories"],
            "diminishing_returns": row["bStackedModifiersGiveDiminishingReturns"], "behavior": row["EquippableModifier"]}
    table = tables["Traits/D_Armour.json"]
    for raw in table["Rows"]:
        row = {**table["Defaults"], **raw}
        result["armour"][row["Name"]] = {"stats": stat_values(row["ArmourStats"]), "set": reference(row, "ArmourSet")}
    for row in tables["Armour/D_ArmourSets.json"]["Rows"]:
        result["armour_sets"][row["Name"]] = [entry["RowName"] for entry in row.get("SetBonus", [])]
    table = tables["Armour/D_ArmourSetBonus.json"]
    for raw in table["Rows"]:
        row = {**table["Defaults"], **raw}
        result["armour_set_bonuses"][row["Name"]] = {"max_stats": stat_values(row.get("GrantedStats", row.get("Stats", {}))),
                                                     "definition_has_target": any(key in json.dumps(raw) for key in STAT_KEYS)}
    table = tables["Modifiers/D_ModifierStates.json"]
    for raw in table["Rows"]:
        row = {**table["Defaults"], **raw}
        result["modifiers"][row["Name"]] = {"stats": stat_values(row["GrantedStats"]), "player": row["bEffectsPlayers"],
            "save_to_database": row["SaveToDatabase"], "behavior": row["Behaviour"], "merge": row["MergeType"],
            "max_stack": row["MaxStackNum"], "effectiveness_affectors": [entry["Value"] for entry in row["ModifierEffectivenessAffectors"]]}
    table = tables["Bestiary/D_BestiaryData.json"]
    for raw in table["Rows"]:
        row = {**table["Defaults"], **raw}
        result["bestiary"][row["Name"]] = {"total_points": row["TotalPointsRequired"],
            "unlock1": stat_values(row["StatsUnlock1"]), "unlock2": stat_values(row["StatsUnlock2"]),
            "progressive": [entry["Value"] for entry in row["ProgressiveStat"] if entry["Value"] in STAT_KEYS]}
    result["known_alterations"] = [row["Name"] for row in tables["Alterations/D_Alterations.json"]["Rows"]]
    result["stat_enum"] = [row["Name"] for row in tables["Stats/D_Stats.json"]["Rows"]]
    result["inventory_ids"] = {row["Name"]: index for index, row in enumerate(tables["Inventory/D_InventoryID.json"]["Rows"])}
    result["rules"] = {
        "talent_rank": {"verified": True, "index_offset": -1, "cumulative": False, "player_apply_rva": "0x1947b30"},
        "owner_derived": {"verified": True, "mode": "base_passthrough", "health_rva": "0x17df6c0", "stamina_rva": "0x17df720"},
        "bestiary": {"verified": True, "first_fraction": 0.4, "first_rule": "trunc(float32(float64(total_points)*0.4))",
            "second_rule": "points>=total_points", "absent_group_has_no_unlock": True, "nonpositive_has_no_unlock": True,
            "refresh_rva": "0x187d730", "first_threshold_rva": "0x187d9e6", "stat_add_rva": "0x1861d40"},
        "profile_scope": "Characters.json, Profile.json and BestiaryData.json are separate files, not a single world snapshot.",
        "equipment_scope": "Owner inventory is from PlayerStateRecorderComponent in the selected world. Positive grants in an uncertain active slot, diminishing returns or nested equipment require further application evidence.",
        "live_completeness": "Saved modifiers filter SaveToDatabase and cannot prove all current owner stats. Persistent known subtotal is not complete current owner percentage.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    print(json.dumps({"output": str(args.output), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      "talents": len(result["talents"]), "bestiary": len(result["bestiary"]), "items": len(result["items"])}))


if __name__ == "__main__":
    main()
