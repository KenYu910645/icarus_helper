"""Read-only real-save capacity checks and isolated Qt rendering.

This runs the animal page against the selected world's stable save snapshot.
Only the output directory and a temporary copy of settings/snapshots are written.
It neither starts nor controls the game and is not desktop-input verification.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def status_of(stat):
    return stat.get("maximum_status")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/animal-capacities-20261102"))
    parser.add_argument("--reference-settings", type=Path, help="Defaults to the fixed user settings; read only")
    parser.add_argument("--expected-animals", type=int, default=12)
    parser.add_argument("--expected-oxygen", type=int, default=11)
    parser.add_argument("--expected-reconstructed", type=int, default=23)
    parser.add_argument("--expected-unknown", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from icarus_helper.animal_widgets import AnimalGauge
    from icarus_helper.animals_view import AnimalPanel
    from icarus_helper.catalog import load_catalog
    from icarus_helper.saves import load_snapshot
    from icarus_helper.settings import Settings, settings_path
    from icarus_helper.ui import STYLESHEET

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference_path = (args.reference_settings or settings_path()).resolve()
    reference_bytes = reference_path.read_bytes()
    reference = json.loads(reference_bytes)
    source = Path(reference.get("selected_source", ""))
    if not source.is_file():
        raise ValueError("The fixed settings do not select a readable real-world save")
    source_before = sha(source.read_bytes())
    checks = {}
    result = {"test_type": "Qt widget verification with a real read-only save snapshot; no game or desktop input",
              "success": False, "checks": checks}

    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    app = QApplication.instance() or QApplication([])
    for font in ("msjh.ttc", "segoeui.ttf"):
        filename = Path("C:/Windows/Fonts") / font
        if filename.exists():
            QFontDatabase.addApplicationFont(str(filename))
    app.setFont(QFont("Microsoft JhengHei", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    panel = None
    try:
        with tempfile.TemporaryDirectory(prefix="icarus-capacity-check-") as temporary:
            private = Path(temporary)
            isolated_path = private / "settings.json"
            isolated_path.write_bytes(reference_bytes)
            settings = Settings(isolated_path)
            snapshot = load_snapshot(source, cache_dir=private / "snapshots")
            check("stable_source_hash_matches_snapshot", snapshot.sha256 == source_before)
            config = settings.world(snapshot.world_id)
            bases = config.get("bases", [])
            base = next((row for row in bases if row.get("id") == config.get("active_base")), None)
            if base is None and not config.get("active_base") and len(bases) == 1:
                base = bases[0]
            if base:
                base = dict(base, world_id=snapshot.world_id, map_name=snapshot.map_name)
            catalog = load_catalog(snapshot.map_name, world_seed=snapshot.game_state_seed)
            panel = AnimalPanel()
            panel.resize(1540, 1000)
            panel.show()
            panel.set_snapshot(snapshot, base, catalog)
            deadline = time.monotonic() + args.timeout
            while (panel.summary is None or panel.busy) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            check("animal_summary_loaded", panel.summary is not None and not panel.busy)
            summary = panel.summary
            animals = summary["animals"]
            check("expected_real_animal_count", len(animals) == args.expected_animals)
            check("all_animals_have_cards", len(panel.animal_cards) == len(animals))
            stats = {animal["id"]: {s["id"]: s for s in animal["stats"]} for animal in animals}
            food_water = [row[key] for row in stats.values() for key in ("food", "water")]
            check("food_water_have_exact_maxima", all(finite(row.get("maximum")) and row["maximum"] > 0
                  and status_of(row) == "verified" for row in food_water))
            oxygen = [row["oxygen"] for row in stats.values() if finite(row["oxygen"].get("maximum"))]
            check("mounts_have_verified_oxygen_maxima", len(oxygen) == args.expected_oxygen
                  and all(status_of(row) == "verified" and row["maximum"] > 0 for row in oxygen))
            reconstructed = [row[key] for row in stats.values() for key in ("health", "stamina")
                             if finite(row[key].get("maximum"))]
            check("health_stamina_have_explicit_source_scope", len(reconstructed) == args.expected_reconstructed
                  and all(status_of(row) in ("reconstructed", "persistent", "live") for row in reconstructed))
            unknown = [row[key] for row in stats.values() for key in ("health", "food", "water", "oxygen", "stamina")
                       if status_of(row[key]) == "unknown"]
            check("only_expected_capacity_unknowns", len(unknown) == args.expected_unknown)
            for animal in animals:
                card = panel.animal_cards[str(animal["id"])]
                for key in ("food", "water"):
                    row, gauge = stats[animal["id"]][key], card.gauges[key]
                    check(f"{animal['id']}_{key}_same_denominator", gauge.maximum == row["maximum"])
                    check(f"{animal['id']}_{key}_honest_ratio", gauge.ratio_known is (finite(row.get("value"))
                          and 0 <= row["value"] <= row["maximum"]))
                for key in ("health", "stamina"):
                    gauge = card.gauges[key]
                    if finite(stats[animal["id"]][key].get("maximum")):
                        status = status_of(stats[animal["id"]][key])
                        check(f"{animal['id']}_{key}_scope_label", ("≈" in gauge.display_text()) if status == "reconstructed"
                              else ("*" in gauge.display_text()) if status == "persistent" else "≈" not in gauge.display_text() and "*" not in gauge.display_text())
            cows = [animal for animal in animals if animal.get("species_id") == "Cow"]
            check("cow_is_present", bool(cows))
            check("cow_oxygen_stamina_stay_unknown", all(stats[animal["id"]][key].get("maximum") is None
                  for animal in cows for key in ("oxygen", "stamina")))
            limits = json.dumps({"coverage": summary.get("coverage"),
                                "capacity_details": [stat.get("detail", "") for animal in animals for stat in animal["stats"]]},
                               ensure_ascii=False)
            check("cold_limit_disclosed", any(token in limits for token in ("寒冷", "低溫", "冷卻", "cold", "Cold")))
            check("owner_bonus_limit_disclosed", any(token in limits for token in ("主人", "飼養者", "owner", "Owner")))
            check("transient_limit_disclosed", any(token in limits for token in ("暫態", "暫時", "臨時", "transient", "Transient")))
            for _ in range(5):
                app.processEvents()
            check("wide_image_saved", panel.grab().save(str(output / "animal-capacities-wide.png")))
            cow = cows[0]
            panel._locate_animal(cow["id"])
            app.processEvents()
            check("cow_image_saved", panel.grab().save(str(output / "animal-capacities-cow.png")))
            panel.resize(1100, 760)
            for _ in range(5):
                app.processEvents()
            check("small_image_saved", panel.grab().save(str(output / "animal-capacities-small.png")))
            probe_stat = deepcopy(next(row for row in reconstructed if row.get("id") == "health"))
            probe_stat.update(value=120, maximum=100, maximum_status="reconstructed")
            probe = AnimalGauge(probe_stat)
            probe.resize(260, 27)
            probe.show()
            app.processEvents()
            check("smaller_maximum_not_rewritten", probe.maximum == 100 and probe.value == 120)
            check("smaller_maximum_not_claimed_as_full", not probe.ratio_known and "100%" not in probe.display_text())
            check("overflow_probe_saved", probe.grab().save(str(output / "animal-capacity-overflow.png")))
            result.update(world=snapshot.world_id, map=snapshot.map_name, saved_at=snapshot.saved_at.isoformat(),
                          source_sha256=source_before, settings_sha256=sha(reference_bytes), animals=len(animals),
                          exact_food_water=len(food_water), exact_oxygen=len(oxygen), reconstructed_stats=len(reconstructed),
                          unknown_capacities=len(unknown),
                          stats=[{"id": animal["id"], "species_id": animal["species_id"], "stats": animal["stats"]}
                                 for animal in animals], overflow_display=probe.display_text())
            probe.close()
            check("isolated_settings_not_saved", isolated_path.read_bytes() == reference_bytes)
            panel.close()
            app.processEvents()
            panel = None
        result["success"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if panel is not None:
            panel.close()
            app.processEvents()
        result["source_unchanged"] = sha(source.read_bytes()) == source_before
        result["settings_unchanged"] = reference_path.read_bytes() == reference_bytes
        result["success"] = result["success"] and result["source_unchanged"] and result["settings_unchanged"]
        (output / "capacity-widget-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows console code pages may not contain the approximation glyph.
        print(json.dumps({key: value for key, value in result.items() if key not in ("stats", "checks")}, ensure_ascii=True))
    if not result["success"]:
        raise AssertionError("Original source/settings changed during verification")


if __name__ == "__main__":
    main()
