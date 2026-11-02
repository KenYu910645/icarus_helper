"""Verified static inputs stay versioned, exact and separate from current maxima."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "icarus_helper/data/animal_capacities.json"


def load():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_static_capacity_schema_and_explicit_species_growth_references():
    data = load()
    assert data["schema_version"] == 1 and data["game_build"] == "26313227"
    assert data["static_inputs_only"] is True
    assert data["coverage"] == {"survival_species": 72, "curves": 31}
    assert len(data["species"]) == 72 and len(data["curves"]) == 31
    for species, row in data["species"].items():
        assert isinstance(species, str) and row["source_survival_character"] is True
        assert row["growth_id"] and row["actor_class"].startswith("/Game/")
        assert set(row["base_capacities"]) <= {"health", "food", "water", "oxygen", "stamina"}
        assert all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in row["base_capacities"].values())
        for key in ("health_curve", "stamina_curve"):
            assert row[key] is None or row[key] in data["curves"]


def test_native_curves_preserve_linear_and_cubic_keys_and_source_hashes():
    data = load()
    sources = {row["path"]: row for row in data["sources"]}
    modes = set()
    for package, row in data["curves"].items():
        assert package.startswith("/Game/") and isinstance(row["properties"], dict)
        keys = row["keys"]
        assert keys and all(isinstance(key["Time"], (int, float)) and math.isfinite(key["Time"])
                            and isinstance(key["Value"], (int, float)) and math.isfinite(key["Value"]) for key in keys)
        assert all(a["Time"] < b["Time"] for a, b in zip(keys, keys[1:]))
        for key in keys:
            assert all(name in key for name in ("InterpMode", "TangentMode", "TangentWeightMode", "ArriveTangent", "LeaveTangent"))
            modes.add(key["InterpMode"])
        asset = "Icarus/Content/" + package.removeprefix("/Game/")
        for extension in ("uasset", "uexp"):
            source = sources[asset + "." + extension]
            assert source["pak"] == "pakchunk0_s20-WindowsNoEditor.pak"
            assert re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
    assert modes == {"RCIM_Cubic", "RCIM_Linear"}
    assert sources["AI/D_AISetup.json"]["sha256"] == "c20515be71939d39922baec4b8705c13e37a382a44f6077b5f3fcaf210d76b4d"
    assert sources["AI/D_AIGrowth.json"]["sha256"] == "061e13d2953a5b6689068f8eaaac547c03f20db6c654dcd54cb25bf7662798aa"


def test_moa_cow_and_juvenile_use_distinct_explicit_capacity_inputs():
    data = load()
    moa, cow, juvenile = (data["species"][key] for key in ("Mount_Moa", "Cow", "Juvenile_Moa"))
    assert moa["base_capacities"] == {"food": 300, "water": 300, "oxygen": 300}
    assert cow["base_capacities"] == {"food": 200, "water": 200}
    assert juvenile["base_capacities"] == {"health": 150, "food": 100, "water": 100}
    assert cow["stamina_curve"] is None and juvenile["health_curve"] is None
    assert cow["health_curve"] == data["species"]["Mount_Buffalo"]["health_curve"]
    curve = data["curves"][moa["health_curve"]]
    assert [(key["Time"], key["Value"]) for key in curve["keys"]] == [(0, 700), (50, 1200)]
    assert data["species"]["Mount_Arctic_Moa"]["growth_id"] == moa["growth_id"]


def test_rebuilder_rejects_other_game_build_before_reading_assets(tmp_path):
    manifest = tmp_path / "appmanifest_1149460.acf"
    manifest.write_text('"AppState" { "buildid" "1" }', encoding="utf-8")
    output = tmp_path / "not-created.json"
    proc = subprocess.run([sys.executable, str(ROOT / "tools/build_animal_capacity_catalog.py"),
                           "--content", str(tmp_path / "missing-game/Content"),
                           "--steam-manifest", str(manifest), "--output", str(output)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode != 0
    assert "Game build differs from validated 26313227" in proc.stderr
    assert not output.exists()


def test_shipped_capacity_inputs_have_no_private_paths_or_account_identifiers():
    data = load()
    assert data["executable_sha256"] == "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
    assert len(data["sources"]) == 64
    text = CATALOG.read_text(encoding="utf-8")
    for private in ("C:\\", "Users/", "Users\\", "PlayerData", "OwnerCharacterID", "PlayerID", "SteamID"):
        assert private not in text
    for source in data["sources"]:
        assert not Path(source["path"]).is_absolute()
        assert re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
