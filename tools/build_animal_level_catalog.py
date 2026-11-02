"""Rebuild a compact verified animal level rule from local read-only sources.

Run read_animal_assets.ps1 first to obtain the two small curve summaries.
This tool never reads a player save and does not run the game executable.
The native RVAs below apply only to the exact verified executable SHA256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess

EXE_HASH = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"


def f32(value):
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def add(a, b): return f32(f32(a) + f32(b))
def sub(a, b): return f32(f32(a) - f32(b))
def mul(a, b): return f32(f32(a) * f32(b))
def div(a, b): return f32(f32(a) / f32(b))


def evaluate(keys, time):
    """Match unweighted cubic SSE operations at native RVA 0x37cc9b0.

    Both accepted curves end at their level cap, so extrapolation is excluded.
    """
    if time <= float(keys[0]["Time"]): return float(keys[0]["Value"])
    if time >= float(keys[-1]["Time"]): return float(keys[-1]["Value"])
    for left, right in zip(keys, keys[1:]):
        if float(left["Time"]) <= time < float(right["Time"]):
            if left["InterpMode"] != "RCIM_Cubic": raise ValueError("Unsupported curve interpolation")
            if left["TangentWeightMode"] != "RCTWM_WeightedNone" or right["TangentWeightMode"] != "RCTWM_WeightedNone":
                raise ValueError("Unsupported weighted curve")
            dt = sub(right["Time"], left["Time"])
            alpha = div(sub(time, left["Time"]), dt)
            p0, p3 = float(left["Value"]), float(right["Value"])
            m0 = mul(mul(dt, left["LeaveTangent"]), f32(1 / 3))
            m1 = mul(mul(dt, right["ArriveTangent"]), f32(1 / 3))
            p1, p2 = add(m0, p0), sub(p3, m1)
            a01, a23 = add(mul(m0, alpha), p0), add(mul(m1, alpha), p2)
            delta = sub(p2, p1)
            a12 = add(mul(delta, alpha), p1)
            q, p = add(sub(a12, a01), m0), add(sub(a23, a12), delta)
            b12, b01 = add(mul(p, alpha), p1), add(mul(q, alpha), p0)
            return add(mul(add(sub(b12, b01), q), alpha), p0)
    raise ValueError("Curve contains an uncovered interval")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--repak", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--curve-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    executable = args.content.parent / "Binaries/Win64/Icarus-Win64-Shipping.exe"
    if hashlib.sha256(executable.read_bytes()).hexdigest() != EXE_HASH:
        raise ValueError("Unverified executable; revalidate native level and UI contracts first")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if catalog.get("game_build") != "26313227": raise ValueError("Unsupported game build")
    index = subprocess.check_output([str(args.repak), "list", str(args.content / "Data/data.pak"), "-s", ""], text=True).splitlines()
    prefix = next(p for p in index if p.endswith("AI/D_Mounts.json")).removesuffix("AI/D_Mounts.json")
    def table(path):
        raw = subprocess.check_output([str(args.repak), "get", str(args.content / "Data/data.pak"), prefix + path, "-s", ""])
        if not any(s["path"] == path for s in catalog["sources"]):
            catalog["sources"].append({"path": path, "sha256": hashlib.sha256(raw).hexdigest()})
        return json.loads(raw)
    mounts, growth = table("AI/D_Mounts.json"), table("Character/D_CharacterGrowth.json")
    curves = {}
    for growth_id, name in [("AI_Mounts", "C_MountExperienceGrowth"), ("AI_Pets", "C_PetExperienceGrowth")]:
        row = next(x for x in growth["Rows"] if x["Name"] == growth_id)
        maximum = row.get("MaxLevel", growth["Defaults"]["MaxLevel"])
        display = row.get("MaxDisplayLevel", growth["Defaults"]["MaxDisplayLevel"])
        parsed = json.loads((args.curve_dir / (name + ".json")).read_text(encoding="utf-8-sig"))
        keys = parsed["keys"]
        if float(keys[0]["Time"]) != 0 or float(keys[-1]["Time"]) != maximum:
            raise ValueError("Unverified curve range")
        thresholds = [math.trunc(evaluate(keys, i)) for i in range(maximum + 1)]
        if thresholds != sorted(set(thresholds)): raise ValueError("Nonmonotonic level thresholds")
        curves[growth_id] = {"thresholds": thresholds, "max_level": maximum, "max_display_level": display, "asset": parsed["asset"]}
        for ext in ["uasset", "uexp"]:
            asset_path = parsed["asset"] + "." + ext
            raw = subprocess.check_output([str(args.repak), "get", str(args.content / "Paks" / parsed["pak"]), asset_path])
            if not any(s["path"] == asset_path for s in catalog["sources"]):
                catalog["sources"].append({"path": asset_path, "pak": parsed["pak"], "sha256": hashlib.sha256(raw).hexdigest()})
    catalog["mounts"] = {}
    for row in mounts["Rows"]:
        growth_id = row.get("GrowthCurve", mounts["Defaults"]["GrowthCurve"])["RowName"]
        ai = row.get("AISetup", {}).get("RowName")
        catalog["mounts"][row["Name"]] = {"ai_setup": ai, "level_growth_id": growth_id}
        if ai in catalog["species"] and growth_id in curves:
            catalog["species"][ai]["level_growth_id"] = growth_id
    catalog["level_rule"] = {
        "type": "curve_thresholds", "verified": True, "curves": curves,
        "native": {"function": "UCharacterState::GetLevel", "rva": "0x1466ff0", "size": 206,
                   "interpolation_rva": "0x37cc9b0", "integer_conversion": "cvttss2si: truncate toward zero"},
        "ui": {"asset": "Icarus/Content/UI/Components/UMG_CharacterInfo", "export": "ExecuteUbergraph_UMG_CharacterInfo",
               "statements": [48, 49, 50, 51, 52, 53], "rule": "Clamp(GetLevel(), 0, MaxDisplayLevel); no +1"},
        "meaning": "thresholds[i] is the minimum total XP for native integer level i; use the highest satisfied i, clamp to max_display_level",
    }
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "curves": {k: len(v["thresholds"]) for k, v in curves.items()}, "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == "__main__": main()
