"""Rebuild compact animal capacity inputs from the installed read-only game.

Requires the validated game build, repak, PowerShell 7 and UAssetAPI. Full source
tables and cooked curves stay in the build cache; no save or game process is used.
This catalog records inputs and known gaps, not a claim that every runtime buff
can be reconstructed from a save.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from catalog_inventory import CACHE, GAME, REPAK, UASSET_LIBRARY, require_validated_build


ROOT = Path(__file__).resolve().parents[1]
NATIVE_EXE_SHA256 = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
MAX_STAT_KEYS = tuple(f"{prefix}Maximum{stat}_{operation}" for stat in ("Health", "Food", "Water", "Oxygen", "Stamina")
                      for prefix, operation in (("", "+"), ("Base", "+"), ("Base", "+%")))
TABLES = ("AI/D_GeneticValues.json", "AI/D_GeneticLineages.json", "Talents/D_Talents.json",
          "Modifiers/D_ModifierStates.json", "Traits/D_Equippable.json", "Items/D_ItemsStatic.json",
          "AI/D_TamedCreatureModifiers.json", "Stats/D_Stats.json")

CURVE_READER = r'''
param([string]$Repak,[string]$Content,[string]$Library,[string]$Config,[string]$Output)
$ErrorActionPreference='Stop'
Add-Type -Path $Library
$taskTargets=Get-Content -LiteralPath $Config -Raw -Encoding utf8 | ConvertFrom-Json
$taskResults=@()
foreach($taskTarget in $taskTargets) {
 $taskMemory=[System.IO.MemoryStream]::new()
 foreach($taskExtension in @('uasset','uexp')) {
  $taskInfo=[System.Diagnostics.ProcessStartInfo]::new()
  $taskInfo.FileName=$Repak
  $taskInfo.UseShellExecute=$false
  $taskInfo.RedirectStandardOutput=$true
  $taskInfo.CreateNoWindow=$true
  foreach($taskArgument in @('get',(Join-Path $Content ('Paks/'+$taskTarget.pak)),($taskTarget.asset+'.'+$taskExtension))) {$taskInfo.ArgumentList.Add($taskArgument)}
  $taskProcess=[System.Diagnostics.Process]::Start($taskInfo)
  $taskProcess.StandardOutput.BaseStream.CopyTo($taskMemory)
  $taskProcess.WaitForExit()
  if($taskProcess.ExitCode -ne 0){throw 'Curve extraction failed'}
 }
 $taskHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($taskMemory.ToArray())).ToLowerInvariant()
 $taskMemory.Position=0
 $taskReader=[UAssetAPI.AssetBinaryReader]::new($taskMemory,$null)
 $taskAsset=[UAssetAPI.UAsset]::new($taskReader,[UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27,$null,$false,[UAssetAPI.CustomSerializationFlags]::None)
 $taskJson=$taskAsset.SerializeJson($false) | ConvertFrom-Json -Depth 100
 $taskCurve=@($taskJson.Exports[0].Data | Where-Object Name -eq 'FloatCurve')
 if($taskCurve.Count -ne 1){throw 'Unrecognised curve export'}
 $taskKeys=@($taskCurve[0].Value | Where-Object Name -eq 'Keys')
 if($taskKeys.Count -ne 1){throw 'Unrecognised curve keys'}
 $taskFields=[ordered]@{}
 foreach($taskField in $taskCurve[0].Value){if($taskField.Name -ne 'Keys'){$taskFields[$taskField.Name]=$taskField.Value}}
 $taskResults+=@{id=$taskTarget.id;asset=$taskTarget.asset;pak=$taskTarget.pak;sha256=$taskHash;fields=$taskFields;keys=@($taskKeys[0].Value | ForEach-Object {$_.Value[0].Value})}
 $taskMemory.Dispose()
}
ConvertTo-Json -InputObject @($taskResults) -Depth 100 | Set-Content -LiteralPath $Output -Encoding utf8
'''


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def stat_name(raw: str) -> str:
    match = re.fullmatch(r'\(Value="([^"]+)"\)', str(raw))
    if not match:
        raise ValueError(f"Unrecognised stat key: {raw}")
    return match.group(1)


def stats(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Stat collection must be a mapping")
    output = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Invalid stat value")
        output[stat_name(key)] = value
    return output


def max_stats(raw: dict) -> dict:
    return {key: value for key, value in stats(raw).items() if key in MAX_STAT_KEYS}


def curve_reference(raw: str) -> str:
    match = re.fullmatch(r"CurveFloat'(/Game/[^']+)\.[^'.]+'", str(raw))
    if not match:
        raise ValueError(f"Unsupported curve reference: {raw}")
    return match.group(1)


def curve_keys(raw):
    numeric = ("Time", "Value", "ArriveTangent", "LeaveTangent", "ArriveTangentWeight", "LeaveTangentWeight")
    keys = []
    for row in raw:
        item = {key: float(row[key]) for key in numeric}
        if not all(math.isfinite(v) for v in item.values()):
            raise ValueError("Nonfinite curve key")
        item.update({key: row[key] for key in ("InterpMode", "TangentMode", "TangentWeightMode")})
        keys.append(item)
    if not keys or any(a["Time"] >= b["Time"] for a, b in zip(keys, keys[1:])):
        raise ValueError("Nonmonotonic curve times")
    return keys


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--repak", type=Path, default=REPAK)
    parser.add_argument("--content", type=Path, default=GAME)
    parser.add_argument("--uasset-library", type=Path, default=UASSET_LIBRARY)
    parser.add_argument("--powershell", default=shutil.which("pwsh") or "pwsh")
    parser.add_argument("--cache", type=Path, default=CACHE / "animal_capacity_effects_source")
    parser.add_argument("--output", type=Path, default=ROOT / "icarus_helper/data/animal_capacity_effects.json")
    args = parser.parse_args()
    # The normal environment contract validates the build; explicit content must
    # independently match the same installed manifest and verified executable.
    if args.content == GAME:
        require_validated_build()
    else:
        manifest = args.content.parents[3] / "appmanifest_1149460.acf"
        if not re.search(r'"buildid"\s+"26313227"', manifest.read_text(encoding="utf8")):
            raise ValueError("Unsupported game build")
    executable = args.content.parent / "Binaries/Win64/Icarus-Win64-Shipping.exe"
    if sha(executable.read_bytes()) != NATIVE_EXE_SHA256:
        raise ValueError("Unverified executable; revalidate native contracts first")
    args.cache.mkdir(parents=True, exist_ok=True)
    index = subprocess.check_output([str(args.repak), "list", str(args.content / "Data/data.pak"), "-s", ""], text=True).splitlines()
    tables, sources = {}, []
    for relative in TABLES:
        matches = [path for path in index if path.endswith("/" + relative)]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous source table: {relative}")
        raw = subprocess.check_output([str(args.repak), "get", str(args.content / "Data/data.pak"), matches[0], "-s", ""])
        (args.cache / Path(relative).name).write_bytes(raw)
        tables[relative] = json.loads(raw)
        sources.append({"path": relative, "sha256": sha(raw)})
    definitions = {r["Name"] for r in tables["Stats/D_Stats.json"]["Rows"]}
    if not set(MAX_STAT_KEYS).issubset(definitions):
        raise ValueError("Required capacity stat definitions differ")
    result = {"schema_version": 1, "game_build": "26313227", "verified_at": "2026-11-02",
              "executable_sha256": NATIVE_EXE_SHA256, "max_stat_keys": list(MAX_STAT_KEYS),
              "sources": sources, "genetics": {}, "lineages": {}, "curves": {},
              "creature_talents": {}, "modifiers": {}, "equippables": {}, "equippable_items": {},
              "owner_conversion": [], "known_ineffective": {"genetics": [], "lineages": [], "creature_talents": [], "modifiers": [], "equippables": []}}
    requested_curves = set()
    for row in tables["AI/D_GeneticValues.json"]["Rows"]:
        relevant = {stat_name(key): curve_reference(value) for key, value in row.get("Base", {}).items() if stat_name(key) in MAX_STAT_KEYS}
        result["genetics"][row["Name"]] = {"base": relevant}
        requested_curves.update(relevant.values())
        if not relevant:
            result["known_ineffective"]["genetics"].append(row["Name"])
    for row in tables["AI/D_GeneticLineages.json"]["Rows"]:
        relevant = {stat_name(key): curve_reference(value) for key, value in row.get("Growth", {}).items() if stat_name(key) in MAX_STAT_KEYS}
        fixed = max_stats(row.get("Stats", {}))
        result["lineages"][row["Name"]] = {"stats": fixed, "growth": relevant}
        requested_curves.update(relevant.values())
        if not relevant and not fixed:
            result["known_ineffective"]["lineages"].append(row["Name"])
    for row in tables["Talents/D_Talents.json"]["Rows"]:
        if not row["Name"].startswith("Creature_"):
            continue
        rewards = [stats(reward.get("GrantedStats", {})) for reward in row.get("Rewards", [])]
        result["creature_talents"][row["Name"]] = rewards
        if not any(set(reward).intersection(MAX_STAT_KEYS) for reward in rewards):
            result["known_ineffective"]["creature_talents"].append(row["Name"])
    modifier_table = tables["Modifiers/D_ModifierStates.json"]
    for raw in modifier_table["Rows"]:
        row = {**modifier_table["Defaults"], **raw}
        relevant = max_stats(row["GrantedStats"])
        result["modifiers"][row["Name"]] = {
            "max_stats": relevant, "npc": row["bEffectsNPCs"], "save_to_database": row["SaveToDatabase"],
            "behavior": row["Behaviour"], "effectiveness_affectors": [x["Value"] for x in row["ModifierEffectivenessAffectors"]],
            "merge": row["MergeType"], "max_stack": row["MaxStackNum"]}
        # This list means no direct capacity stats in the table, not that custom
        # behavior or indirect effectiveness changes can safely be ignored.
        if not relevant:
            result["known_ineffective"]["modifiers"].append(row["Name"])
    equipment = tables["Traits/D_Equippable.json"]
    for raw in equipment["Rows"]:
        row = {**equipment["Defaults"], **raw}
        granted = stats(row["GrantedStats"])
        result["equippables"][row["Name"]] = {"stats": granted, "behavior": row["EquippableModifier"],
            "all_inventories": row["bAppliesInAllInventories"], "diminishing_returns": row["bStackedModifiersGiveDiminishingReturns"],
            "global_actor_class": row["GlobalStat_ActorClass"], "global_stats": stats(row["GlobalStat_GrantedStats"])}
        if not set(granted).intersection(MAX_STAT_KEYS):
            result["known_ineffective"]["equippables"].append(row["Name"])
    for row in tables["Items/D_ItemsStatic.json"]["Rows"]:
        reference = row.get("Equippable", {}).get("RowName")
        if reference and reference != "None":
            result["equippable_items"][row["Name"]] = reference
    owner_table = tables["AI/D_TamedCreatureModifiers.json"]
    for raw in owner_table["Rows"]:
        row = {**owner_table["Defaults"], **raw}
        if row["GrantedStat"]["Value"] in MAX_STAT_KEYS:
            result["owner_conversion"].append({"row": row["Name"], "requirement": row["StatRequirement"]["Value"],
                                               "granted": row["GrantedStat"]["Value"], "effects": row["Effects"]})
    targets = []
    pak_indexes = {}
    for pak in sorted((args.content / "Paks").glob("*.pak")):
        paths = subprocess.check_output([str(args.repak), "list", str(pak)], text=True).splitlines()
        for curve in requested_curves:
            asset = "Icarus/Content/" + curve.removeprefix("/Game/")
            if asset + ".uasset" in paths:
                pak_indexes.setdefault(curve, []).append((pak.name, asset))
    for curve in sorted(requested_curves):
        matches = pak_indexes.get(curve, [])
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous native curve: {curve}")
        pak, asset = matches[0]
        targets.append({"id": curve, "asset": asset, "pak": pak})
    config = args.cache / "curve-inputs.json"
    config.write_text(json.dumps(targets), encoding="utf8")
    output = args.cache / "curve-exports.json"
    with tempfile.TemporaryDirectory(prefix="icarus-capacity-reader-") as temporary:
        script = Path(temporary) / "read_curves.ps1"
        script.write_text(CURVE_READER, encoding="utf-8-sig")
        subprocess.run([str(args.powershell), "-NoProfile", "-File", str(script), "-Repak", str(args.repak),
                        "-Content", str(args.content), "-Library", str(args.uasset_library), "-Config", str(config),
                        "-Output", str(output)], check=True)
    for curve in json.loads(output.read_text(encoding="utf-8-sig")):
        result["curves"][curve["id"]] = {"asset": curve["asset"], "pak": curve["pak"], "sha256": curve["sha256"],
                                          "fields": curve["fields"], "keys": curve_keys(curve["keys"])}
    result["contract"] = {
        "known_ineffective_meaning": "The named row is known and has no direct five-capacity stat; custom behavior, effectiveness changes, saved additional item stats and unknown identifiers must still be checked.",
        "genetic_curve_input": "Saved GeneticValue.Value; each curve result truncates toward zero to int32 before adding its stat.",
        "lineage_curve_input": "XP-derived native cached level, without +1; each Growth curve result truncates toward zero to int32 before adding its stat. Fixed Stats are integers.",
        "talent_rank_semantics": "Select only Rewards[rank-1] for a positive saved rank within the array. Do not accumulate lower-rank rewards. Missing rows and invalid ranks remain unknown.",
        "curve_extrapolation": "No explicit extrapolation fields are serialized in these four curves. Out-of-domain evaluation requires a separately verified FRichCurve default contract.",
        "modifier_completeness": "Mount recorder filters on SaveToDatabase. Unrecorded Animal_Feed, Animal_Feed_Bean, gruel, auras and other transient effects can change health/stamina; saved modifiers alone are incomplete.",
        "saddle_scope": "Resolve the saved item through equippable_items; inspect saved AdditionalStats, Alterations and LivingItemSlots. A saddle with no direct capacity grants is not proof of no rider/owner bonuses.",
        "owner_scope": "D_TamedCreatureModifiers transfers owner health/stamina percentage stats. Native PerformPlayerOwnerStatConversion resolves OwnerCharacterID to a valid runtime character; the animal record does not contain that character's full current stat container.",
        "food_water_oxygen": "No modifier grants food/water maxima; only Oxygen_Buff grants oxygen maximum and has bEffectsNPCs=false. No Creature talent, genetic/lineage input, saddle trait or owner conversion in these tables grants those three maxima.",
        "native_owner_conversion_rva": {"mount": "0x143a9e0", "juvenile": "0x143ac20", "mount_row_callback": "0x142abe0", "juvenile_row_callback": "0x1425b60"},
    }
    result["verified_rules"] = {
        "genetics": {"verified": True, "function_rva": "0x141c0f0", "input": "GeneticValue.Value",
                     "curve_call_rva": "0x141c3a2", "integer_conversion_rva": "0x141c3a7", "stat_add_rva": "0x141c3bc",
                     "integer_conversion": "truncate_toward_zero_per_curve"},
        "lineages": {"verified": True, "function_rva": "0x141c460", "input": "CharacterState.cached_level",
                     "level_offset": "0x29c", "curve_call_rva": "0x141c738", "integer_conversion_rva": "0x141c73d",
                     "fixed_stats_add_rva": "0x141c93d", "level_update_rva": "0x14834e0", "level_offset_adjustment": 0},
        "talents": {"verified": True, "function_rva": "0x1947520", "rank_read_rva": "0x194787a",
                    "index_minus_one_rva": "0x194787e", "reward_selection_rva": "0x1947895",
                    "rank_index_offset": -1, "cumulative": False},
        "health_stamina_finalization": {"verified": True, "formula": "max(1,trunc(base*(100+percent)/100))",
                    "health_getter_rva": "0x1468aa0", "health_update_rva": "0x147c220",
                    "stamina_getter_rva": "0x1469390", "stamina_update_rva": "0x147c390",
                    "scope": "Aggregation rule only; missing transient or owner inputs prevent a complete current capacity."},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    print(json.dumps({"output": str(args.output), "sha256": sha(args.output.read_bytes()),
                      "counts": {key: len(result[key]) for key in ("genetics", "lineages", "curves", "creature_talents", "modifiers", "equippables", "equippable_items")}}))


if __name__ == "__main__":
    main()
