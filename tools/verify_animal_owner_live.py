"""Verify owner inputs and live/fallback animal pairs using isolated Qt settings.

This is read-only snapshot/widget verification, not Windows desktop interaction.
The game may be running and writing its save; a changed source hash is recorded
as external activity, never used alone to accuse the helper of writing a save.
Private screenshots/reports stay in ignored artifacts; raw profiles are omitted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def sha_file(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def source_metadata(source):
    allowed = {"kind", "sha256", "size", "mtime", "mtime_ns", "same_snapshot", "status", "snapshot_time", "world_id", "reason"}
    return {key: value for key, value in source.items() if key in allowed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/animal-owner-live-20261102"))
    parser.add_argument("--reference-settings", type=Path)
    parser.add_argument("--expected-animals", type=int, default=12, help="Case-specific expectation, not a fixed game rule")
    parser.add_argument("--timeout", type=float, default=75)
    args = parser.parse_args()

    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from icarus_helper.animals import load_animal_catalog
    from icarus_helper.animals_view import AnimalPanel
    from icarus_helper.catalog import load_catalog
    from icarus_helper.saves import SaveChangedError, load_snapshot
    from icarus_helper.settings import Settings, settings_path
    from icarus_helper.ui import STYLESHEET

    if load_animal_catalog().get("live_capacity_rule", {}).get("verified") is not True:
        raise RuntimeError("Live source marker is not enabled; wait for the validated activation before running this tool")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference_path = args.reference_settings or settings_path()
    reference_bytes = reference_path.read_bytes()
    reference = json.loads(reference_bytes)
    source = Path(reference.get("selected_source", ""))
    if not source.is_file():
        raise ValueError("The selected source is not a readable world save")
    source_before = sha_file(source)
    checks = {}
    report = {"success": False, "test_type": "Real read-only stable world snapshot and isolated Qt widgets; no desktop/game input",
              "checks": checks, "raw_profiles_included": False}

    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    app = QApplication.instance() or QApplication([])
    for font in ("msjh.ttc", "segoeui.ttf"):
        path = Path("C:/Windows/Fonts") / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft JhengHei", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    panel = None
    try:
        with tempfile.TemporaryDirectory(prefix="icarus-owner-live-check-") as temporary:
            private = Path(temporary)
            isolated_path = private / "settings.json"
            isolated_path.write_bytes(reference_bytes)
            settings = Settings(isolated_path)
            snapshot = None
            for attempt in range(5):
                try:
                    snapshot = load_snapshot(source, cache_dir=private / "snapshots")
                    break
                except SaveChangedError:
                    if attempt == 4:
                        raise
                    app.processEvents()
                    time.sleep(.2)
            check("stable_snapshot_loaded", snapshot is not None)
            config = settings.world(snapshot.world_id)
            bases = config.get("bases", [])
            base = next((row for row in bases if row.get("id") == config.get("active_base")), None)
            if base is None and not config.get("active_base") and len(bases) == 1:
                base = bases[0]
            base = dict(base, world_id=snapshot.world_id, map_name=snapshot.map_name) if base else None
            catalog = load_catalog(snapshot.map_name, world_seed=snapshot.game_state_seed)
            panel = AnimalPanel()
            configured = config.get("animal_owner_sources", [])
            if configured:
                local = [str(source.parent.parent.parent)] if source.parent.name.casefold() == "prospects" else []
                panel.owner_profile_roots = local + [{"world_id": snapshot.world_id, "path": path} for path in configured if isinstance(path, str)]
            panel.resize(1700, 1050)
            panel.show()
            panel.set_snapshot(snapshot, base, catalog)
            deadline = time.monotonic() + args.timeout
            while (panel.summary is None or panel.busy) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            check("summary_loaded", panel.summary is not None and not panel.busy)
            # Keep one sample fixed during the visual checks; the app's normal
            # visible refresh behavior is exercised separately in Qt tests.
            panel.live_refresh.stop()
            summary = deepcopy(panel.summary)
            animals = summary["animals"]
            owners = summary.get("owner_stats", {}).get("owners", {})
            check("case_expected_animal_count", len(animals) == args.expected_animals)
            check("one_card_per_animal", len(panel.animal_cards) == len(animals))
            check("same_snapshot_time", summary["coverage"].get("snapshot_time") == str(snapshot.saved_at))
            local_owners = [owner for owner in owners.values() if owner.get("coverage", {}).get("profile_match") is True]
            remote_owners = [owner for owner in owners.values() if owner.get("coverage", {}).get("profile_match") is not True]
            check("case_one_local_owner", len(local_owners) == 1)
            check("case_one_missing_remote_profile", len(remote_owners) == 1)
            local = local_owners[0]
            check("local_owner_known_persistent_sources_are_30_10", local.get("known_percent") == {"health": 30, "stamina": 10})
            components = local.get("components", [])
            check("owner_percent_comes_from_character_and_bestiary", any(row.get("row") == "Husbandry_TameHealth" and row.get("stats", {}).get("health") == 30 for row in components)
                  and any(row.get("row") == "Husbandry_Stamina" and row.get("stats", {}).get("stamina") == 5 for row in components)
                  and any(row.get("source") == "account_bestiary" and row.get("row") == "Horse" and row.get("stats", {}).get("stamina") == 5 for row in components))
            check("remote_profile_is_unknown_not_zero", all(owner.get("percent") == {"health": None, "stamina": None}
                  and owner.get("known_percent") == {"health": None, "stamina": None} for owner in remote_owners))
            check("persistent_sources_are_not_marked_live_complete", all(owner.get("live_complete") is False for owner in owners.values()))
            check("profile_times_stay_independent", all(source.get("same_snapshot") is False for owner in owners.values()
                  for source in owner.get("sources", []) if source.get("kind") in ("Characters.json", "Profile.json", "BestiaryData.json")))
            live = summary.get("live_stats", {})
            applied = []
            for animal in animals:
                for stat in animal["stats"]:
                    if stat.get("maximum_status") != "live":
                        continue
                    pair = live.get("animals", {}).get(str(animal["id"]), {}).get(stat["id"], {})
                    check(f"live_pair_{animal['id']}_{stat['id']}", stat["value"] == pair.get("value") and stat["maximum"] == pair.get("maximum")
                          and "saved_value" in stat and "persistent_maximum" in stat and stat.get("sample_time") == live.get("sample_time"))
                    applied.append({"animal_id": animal["id"], "stat": stat["id"], "value": stat["value"], "maximum": stat["maximum"]})
            check("applied_live_pairs_require_verified_world", not applied or live.get("world_verified") is True)
            panel.animal_map.map.fit_animals()
            app.processEvents()
            panel.grab().save(str(output / "animals-wide.png"))
            local_animal = next(row for row in animals if row.get("owner_key") == local["owner_key"])
            panel._select_by_id(str(local_animal["id"]))
            panel.stat_toggle.setChecked(True)
            app.processEvents()
            panel.grab().save(str(output / "local-owner-detail.png"))
            panel.detail_content.grab().save(str(output / "local-owner-detail-content.png"))
            remote_animal = next(row for row in animals if row.get("owner_key") == remote_owners[0]["owner_key"])
            panel._select_by_id(str(remote_animal["id"]))
            app.processEvents()
            panel.grab().save(str(output / "remote-owner-detail.png"))
            panel.resize(1100, 760)
            app.processEvents()
            panel.grab().save(str(output / "animals-small.png"))
            report.update(world_id=snapshot.world_id, snapshot_time=snapshot.saved_at.isoformat(), snapshot_sha256=snapshot.sha256,
                animal_count=len(animals), owner_count=len(owners), live_status={key: live.get(key) for key in ("status", "reason", "sample_time", "world_verified", "coverage")},
                live_pair_count=len(applied), live_applied_pairs=applied,
                owner_inputs=[{"owner_name": owner.get("owner_name"), "known_percent": owner.get("known_percent"), "percent": owner.get("percent"),
                    "persistent_complete": owner.get("persistent_complete"), "live_complete": owner.get("live_complete"),
                    "sources": [source_metadata(row) for row in owner.get("sources", [])],
                    "components": owner.get("components"), "coverage": owner.get("coverage"), "unresolved": owner.get("unresolved")} for owner in owners.values()],
                animal_statuses=[{"name": row["name"], "id": row["id"], "owner_name": row["owner_name"],
                    "stats": [{key: stat.get(key) for key in ("id", "value", "maximum", "maximum_status", "sample_time", "maximum_components")} for stat in row["stats"]]} for row in animals])
            check("pre_clear_summary_present", bool(panel.summary))
            panel.clear()
            check("clear_discards_cards_maps_and_roots", panel.summary is None and panel.owner_profile_roots is None
                  and not panel.animal_cards and not panel.animal_map.map.points)
            report["success"] = True
    except Exception as error:
        report.update(error_type=type(error).__name__, failed_check=str(error) if isinstance(error, AssertionError) else "Verification could not finish")
        raise
    finally:
        if panel is not None:
            panel.close()
            app.processEvents()
        source_after = sha_file(source)
        settings_unchanged = reference_path.read_bytes() == reference_bytes
        report.update(source_before_sha256=source_before, source_after_sha256=source_after,
                      source_unchanged=source_before is not None and source_before == source_after,
                      source_change_note="The game or another external writer may update the original world while this read-only check runs; a hash change alone is not evidence of helper writes.",
                      settings_unchanged=settings_unchanged)
        if not settings_unchanged:
            report["success"] = False
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        assert settings_unchanged, "Reference settings changed during verification"
    print(json.dumps({"success": report["success"], "animals": report.get("animal_count"), "live_pairs": report.get("live_pair_count"),
                      "live_status": report.get("live_status", {}).get("status"), "source_unchanged": report["source_unchanged"],
                      "settings_unchanged": report["settings_unchanged"], "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
