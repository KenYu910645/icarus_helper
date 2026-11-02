"""Rebuild static animal capacity inputs from the verified installed game.

These are calculation inputs, not verified current maxima for individual animals.
Talents, genetics, modifiers and native application/rounding must be resolved by
the consumer. The game installation and player saves are never written or loaded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BUILD = "26313227"
EXE_SHA256 = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
CURVE_PAK = "pakchunk0_s20-WindowsNoEditor.pak"
CAPACITY_STATS = {key: f"BaseMaximum{native}_+" for key, native in (
    ("health", "Health"), ("food", "Food"), ("water", "Water"),
    ("oxygen", "Oxygen"), ("stamina", "Stamina"))}

READ_CURVES = r'''
param([string]$Repak,[string]$Content,[string]$UAssetLibrary,[string]$Targets,[string]$OutputRoot)
$ErrorActionPreference = 'Stop'
Add-Type -Path $UAssetLibrary
foreach ($target in (Get-Content -LiteralPath $Targets -Raw -Encoding utf8 | ConvertFrom-Json)) {
    $memory = [IO.MemoryStream]::new()
    foreach ($extension in @('uasset','uexp')) {
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $Repak
        $info.UseShellExecute = $false
        $info.RedirectStandardOutput = $true
        $info.CreateNoWindow = $true
        foreach ($argument in @('get',(Join-Path $Content "Paks/$($target.pak)"),"$($target.asset).$extension")) {
            $info.ArgumentList.Add($argument)
        }
        $raw = [IO.MemoryStream]::new()
        $process = [Diagnostics.Process]::Start($info)
        $process.StandardOutput.BaseStream.CopyTo($raw)
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "repak failed: $($target.asset).$extension" }
        $bytes = $raw.ToArray()
        [IO.File]::WriteAllBytes((Join-Path $OutputRoot "$($target.key).$extension"), $bytes)
        $memory.Write($bytes, 0, $bytes.Length)
        $raw.Dispose()
        $process.Dispose()
    }
    $memory.Position = 0
    $reader = [UAssetAPI.AssetBinaryReader]::new($memory,$null)
    $asset = [UAssetAPI.UAsset]::new($reader,[UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27,$null,$false,[UAssetAPI.CustomSerializationFlags]::None)
    [IO.File]::WriteAllText((Join-Path $OutputRoot "$($target.key).json"),$asset.SerializeJson($false),[Text.UTF8Encoding]::new($false))
    $memory.Dispose()
}
'''


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def curve_package(reference) -> str | None:
    if reference in (None, "None", ""):
        return None
    if not isinstance(reference, str):
        raise ValueError("Unsupported non-string curve reference")
    match = re.fullmatch(r"CurveFloat'(/Game/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+)'", reference)
    if match is None or match[1].rsplit("/", 1)[-1] != match[2]:
        raise ValueError(f"Unsupported curve reference: {reference}")
    return match[1]


def strip_types(value):
    if isinstance(value, dict):
        return {key: strip_types(item) for key, item in value.items() if key != "$type"}
    if isinstance(value, list):
        return [strip_types(item) for item in value]
    # UAssetAPI uses the string +0 for positive floating zero.
    return 0.0 if value == "+0" else value


def curve_data(asset: dict) -> dict:
    curves = [prop for export in asset.get("Exports", []) for prop in export.get("Data", [])
              if prop.get("StructType") == "RichCurve" and prop.get("Name") == "FloatCurve"]
    if len(curves) != 1:
        raise ValueError("Expected one FloatCurve/RichCurve property")
    properties = curves[0].get("Value", [])
    rows = [prop for prop in properties if prop.get("Name") == "Keys"]
    if len(rows) != 1 or not rows[0].get("Value"):
        raise ValueError("Curve has no validated key list")
    keys = []
    for row in rows[0]["Value"]:
        members = row.get("Value")
        if not isinstance(members, list) or len(members) != 1:
            raise ValueError("Unsupported RichCurveKey layout")
        key = strip_types(members[0].get("Value"))
        if not isinstance(key, dict) or not all(field in key for field in ("Time", "Value", "InterpMode", "TangentWeightMode")):
            raise ValueError("Incomplete RichCurveKey")
        keys.append(key)
    # Preserve interpolation, tangents and all serialized extrapolation/default
    # properties. No sample approximation or forced linear interpolation.
    return {"keys": keys, "properties": {prop["Name"]: strip_types(prop.get("Value"))
                                           for prop in properties if prop.get("Name") != "Keys"}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "icarus_helper/data/animal_capacities.json")
    parser.add_argument("--repak", help="Override ICARUS_REPAK")
    parser.add_argument("--content", help="Override ICARUS_CONTENT")
    parser.add_argument("--uasset-library", help="Override ICARUS_UASSET_LIBRARY")
    parser.add_argument("--steam-manifest", help="Override ICARUS_STEAM_MANIFEST")
    parser.add_argument("--pwsh", help="PowerShell 7 executable; defaults to ICARUS_PWSH or PATH")
    parser.add_argument("--work-dir", type=Path, help="Build-only extraction directory")
    args = parser.parse_args()
    for field, variable in (("repak", "ICARUS_REPAK"), ("content", "ICARUS_CONTENT"),
                            ("uasset_library", "ICARUS_UASSET_LIBRARY"), ("steam_manifest", "ICARUS_STEAM_MANIFEST")):
        if getattr(args, field):
            os.environ[variable] = getattr(args, field)
    from catalog_inventory import CACHE, GAME, REPAK, UASSET_LIBRARY, require_validated_build
    require_validated_build()
    executable = GAME.parent / "Binaries/Win64/Icarus-Win64-Shipping.exe"
    if sha256(executable.read_bytes()) != EXE_SHA256:
        raise ValueError("Unverified game executable; revalidate native capacity contracts before rebuilding")
    powershell = args.pwsh or os.environ.get("ICARUS_PWSH") or shutil.which("pwsh")
    if not powershell or not UASSET_LIBRARY.is_file():
        raise FileNotFoundError("PowerShell 7 and UAssetAPI.dll are required for curve extraction")
    work = (args.work_dir or CACHE / "animal_capacity_source").resolve()
    work.mkdir(parents=True, exist_ok=True)
    sources = []
    index = subprocess.check_output([str(REPAK), "list", str(GAME / "Data/data.pak"), "-s", ""], text=True).splitlines()

    def table(relative):
        matches = [path for path in index if path.endswith("/" + relative)]
        if len(matches) != 1:
            raise ValueError(f"Game table missing or ambiguous: {relative}")
        raw = subprocess.check_output([str(REPAK), "get", str(GAME / "Data/data.pak"), matches[0], "-s", ""])
        sources.append({"path": relative, "sha256": sha256(raw)})
        (work / Path(relative).name).write_bytes(raw)
        return json.loads(raw)

    setups = table("AI/D_AISetup.json")
    growth = table("AI/D_AIGrowth.json")
    growth_rows = {row["Name"]: row for row in growth["Rows"]}
    species, packages = {}, set()
    for setup in setups["Rows"]:
        survival = setup.get("bUseSurvivalCharacterState", setups["Defaults"]["bUseSurvivalCharacterState"])
        if survival is not True:
            continue
        growth_id = setup.get("AIGrowth", setups["Defaults"]["AIGrowth"])["RowName"]
        row = growth_rows.get(growth_id)
        if row is None:
            raise ValueError(f"Missing AIGrowth for survival species {setup['Name']}")
        base = {}
        for identifier, amount in row.get("Base", growth["Defaults"]["Base"]).items():
            match = re.fullmatch(r'\(Value="([^"]+)"\)', identifier)
            if match is None:
                raise ValueError(f"Unsupported stat identifier: {identifier}")
            base[match[1]] = amount
        customs = row.get("CustomStats", growth["Defaults"]["CustomStats"])
        stamina = [entry for entry in customs if entry.get("Stat", {}).get("Value") == CAPACITY_STATS["stamina"]]
        if len(stamina) > 1:
            raise ValueError("Multiple stamina curves require native precedence verification")
        health_curve = curve_package(row.get("Health", growth["Defaults"]["Health"]))
        stamina_curve = curve_package(stamina[0].get("Curve")) if stamina else None
        species[setup["Name"]] = {
            "source_survival_character": True, "growth_id": growth_id,
            "actor_class": setup.get("ActorClass", setups["Defaults"]["ActorClass"]),
            "base_capacities": {key: base[identifier] for key, identifier in CAPACITY_STATS.items() if identifier in base},
            "health_curve": health_curve, "stamina_curve": stamina_curve}
        packages.update(package for package in (health_curve, stamina_curve) if package)
    locations = set(subprocess.check_output([str(REPAK), "list", str(GAME / "Paks" / CURVE_PAK)], text=True).splitlines())
    targets = []
    for package in sorted(packages):
        asset = "Icarus/Content/" + package.removeprefix("/Game/")
        if not all(asset + "." + extension in locations for extension in ("uasset", "uexp")):
            raise ValueError(f"Missing verified curve package in {CURVE_PAK}: {asset}")
        targets.append({"key": sha256(package.encode())[:24], "package": package, "asset": asset, "pak": CURVE_PAK})
    target_path, script_path = work / "curve-targets.json", work / "read-curves.ps1"
    target_path.write_text(json.dumps(targets), encoding="utf-8")
    script_path.write_text(READ_CURVES, encoding="utf-8-sig")
    subprocess.run([str(powershell), "-NoProfile", "-File", str(script_path), "-Repak", str(REPAK.resolve()),
                    "-Content", str(GAME.resolve()), "-UAssetLibrary", str(UASSET_LIBRARY.resolve()),
                    "-Targets", str(target_path), "-OutputRoot", str(work)], check=True)
    curves = {}
    for target in targets:
        asset = json.loads((work / (target["key"] + ".json")).read_text(encoding="utf-8"))
        curves[target["package"]] = curve_data(asset)
        for extension in ("uasset", "uexp"):
            raw = (work / (target["key"] + "." + extension)).read_bytes()
            sources.append({"path": target["asset"] + "." + extension,
                            "pak": target["pak"], "sha256": sha256(raw)})
    result = {"schema_version": 1, "game_build": BUILD, "executable_sha256": EXE_SHA256,
              "verified_at": datetime.now(timezone.utc).isoformat(), "static_inputs_only": True,
              "source_chain": "D_AISetup.AIGrowth -> D_AIGrowth.Base/Health/CustomStats -> exact native RichCurve keys",
              "base_stat_ids": CAPACITY_STATS, "species": species, "curves": curves, "sources": sources,
              "limitations": ["Static inputs are not verified current maxima for individual animals.",
                              "Native growth application, modifiers, talents, genetics, equipment and integer rounding must be resolved by the consumer.",
                              "Missing base capacities stay absent; player starting stats are not inherited.",
                              "Unserialized RichCurve properties stay absent; interpolation and tangents are preserved without approximation."],
              "coverage": {"survival_species": len(species), "curves": len(curves)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["coverage"], "sha256": sha256(args.output.read_bytes())}))


if __name__ == "__main__":
    main()
