"""Launch ICARUS Helper or its independent world backup worker."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import tempfile
import traceback

from icarus_helper.settings import Settings, app_dir


def main():
    parser = argparse.ArgumentParser(description="ICARUS Helper desktop companion and world backup manager")
    parser.add_argument("--save", help="World save source (read-only)")
    parser.add_argument("--settings", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--backup-daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backup-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--backup-once", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--world-publisher", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--publisher-state", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--publisher-once", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--publisher-keep-versions", type=int, default=3, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-shared", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-remote", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-reference-settings", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-timeout", type=int, default=60, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.world_publisher:
        if not args.save:
            parser.error("World sharing requires an explicit --save source")
        from tools.publish_world_sync import main as publish_world
        publish_args = ["--source", args.save, "--keep-versions", str(args.publisher_keep_versions)]
        if args.publisher_state:
            publish_args += ["--state-dir", str(args.publisher_state)]
        if args.publisher_once:
            publish_args.append("--once")
        return publish_world(publish_args)
    if args.backup_daemon:
        from icarus_helper.backup_daemon import run
        return run(args.backup_root, once=args.backup_once)
    logging.basicConfig(filename=app_dir() / "app.log", encoding="utf-8", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    def report_exception(kind, value, tb):
        logging.error("Unhandled exception", exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report_exception
    # Initialise diagnostics before native imports: a broken Qt dependency used
    # to fail before anything was written to app.log.
    try:
        from PySide6.QtCore import QLocale, QTimer
        from PySide6.QtGui import QIcon, QImage
        from PySide6.QtWidgets import QApplication
        from icarus_helper.ui import MainWindow, STYLESHEET
    except Exception:
        logging.exception("Desktop runtime could not load")
        if args.self_test_report:
            args.self_test_report.parent.mkdir(parents=True, exist_ok=True)
            args.self_test_report.write_text(json.dumps({"success": False, "stage": "native_import", "error": traceback.format_exc()}, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1
        raise
    QLocale.setDefault(QLocale("en_US"))
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ICARUS Helper")
    app.setOrganizationName("IcarusHelper")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "icarus_helper" / "assets" / "app.ico")))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    isolated = tempfile.TemporaryDirectory(prefix="icarus-helper-release-") if args.self_test_report else None
    isolated_settings = Settings(Path(isolated.name) / "settings.json") if isolated else Settings(args.settings) if args.settings else None
    if isolated_settings is not None and args.self_test_reference_settings and not args.self_test_shared:
        reference = Settings(args.self_test_reference_settings)
        # Read home/source preferences, but keep every verification write isolated
        # and use default layers so a user's hidden layers do not fail the gate.
        isolated_settings.data = {key: reference.data[key] for key in ("worlds", "selected_source") if key in reference.data}
    initial_source = args.save
    if args.self_test_remote:
        from icarus_helper.release_sync import REMOTE_SOURCE
        initial_source = REMOTE_SOURCE
    window = MainWindow(initial_source, isolated_settings)
    window.show()
    release_result = {"success": False, "stage": "waiting_for_snapshot", "frozen": bool(getattr(sys, "frozen", False)), "ui_language": "en", "checked_at": datetime.now(timezone.utc).isoformat()}
    release_finished = False
    if args.self_test_report:
        def finish_release(success, error=""):
            nonlocal release_finished
            if release_finished:
                return
            release_finished = True
            release_result.update(success=success, error=error)
            args.self_test_report.parent.mkdir(parents=True, exist_ok=True)
            args.self_test_report.write_text(json.dumps(release_result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()

        def check_release(_payload):
            if release_finished:
                return
            try:
                snapshot = window.snapshot
                assert snapshot is not None, "World snapshot not loaded"
                release_result["tab_titles"] = [window.tabs.tabText(index).replace("&&", "&") for index in range(window.tabs.count())]
                assert window.points and window.visible_points, "Map resources not loaded"
                from icarus_helper.drill_inventory import inventory_for_point
                from icarus_helper.map_widget import drill_stock_label, value
                drills = [point for point in snapshot.deployables if point.kind == "drill"]
                stocks = [inventory_for_point(point) for point in drills]
                assert all(stock is not None and stock.get("source_sha256") == snapshot.sha256 for stock in stocks), "Drill inventory does not match the snapshot"
                linked = [point for point in window.points if value(point, "kind") == "deep_deposit" and inventory_for_point(point) is not None]
                assert all(drill_stock_label(point) for point in linked), "Drill inventory map labels missing"
                if linked:
                    from icarus_helper.ui import drill_inventory_html
                    assert all("Drill inventory (saved)" in drill_inventory_html(point) for point in linked), "Drill inventory details missing"
                release_result["drill_inventory"] = {"drill_count": len(drills), "linked_count": len(linked),
                                                     "complete_count": sum(stock.get("quantity") is not None for stock in stocks),
                                                     "labels": [drill_stock_label(point) for point in linked]}
                background = window.catalog.get("coverage", {}).get("background", {})
                if window.catalog.get("map_key") == "prometheus":
                    assert background.get("includes_null_sector") and background.get("hash_verified"), "Verified full Null Sector background not loaded"
                    assert window.map.terrain.width() == window.map.terrain.height() == 2048, "Incorrect full background dimensions"
                release_result["map_background"] = {key: background.get(key) for key in (
                    "path", "tileset", "sha256", "hash_verified", "includes_null_sector", "fetched_at")}
                assert window.tabs.indexOf(window.animal_panel) >= 0, "Tamed animals tab not loaded"
                assert window.tabs.tabText(window.tabs.indexOf(window.world_backups_panel)) == "World saves", "World saves tab not loaded"
                assert not window._production_backups, "Verification must not write production backups or install scheduled tasks"
                release_result["world_backups_tab_loaded"] = True
                release_result["backup_store_isolated"] = True
                from icarus_helper.animal_icons import animal_icon_path, load_animal_icon_catalog
                animal_icons = load_animal_icon_catalog().get("species", {})
                animal_icon_files = {animal_icon_path(species) for species in animal_icons}
                animal_icon_files.discard(None)
                assert animal_icon_files and all(not QImage(str(path)).isNull() for path in animal_icon_files), "Native animal icons missing or could not be decoded"
                from icarus_helper.animal_capacities import load_capacity_catalog
                capacity_catalog, capacity_effects = load_capacity_catalog()
                assert capacity_catalog.get("species") and capacity_effects.get("genetics"), "Animal capacity sources missing from bundle"
                from PySide6.QtCore import Qt
                shared_missions = getattr(snapshot, "is_shared_map", False)
                eligible_ids = {m["id"] for m in window.missions if window.mission_active(m)}
                catalogue_ids = {m["id"] for m in window.missions}
                listed_ids = {window.mission_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] for i in range(window.mission_list.count())}
                assert listed_ids == eligible_ids, "Unlocked operation list is incomplete or includes completed / SMPL3 missions"
                overview_ids = {m["id"] for m in window.operation_overview.overview.get("missions", [])}
                assert overview_ids == eligible_ids, "Unlocked mission overview and list are out of sync"
                assert all(p.get("mission_id") in eligible_ids for p in window.operation_overview.map.points), "Overview includes ineligible missions"
                groups = window.mission_clusters.get("groups", [])
                assert not shared_missions or not groups, "Shared map must not infer unlocked mission groups"
                if groups:
                    window.mission_left_tabs.setCurrentIndex(1)
                    assert {p["mission_id"] for p in window.mission_route.map.points} == set(groups[0]["mission_ids"]), "Group map does not include all members"
                    window.mission_left_tabs.setCurrentIndex(0)
                release_result["mission_planner"] = {"summary": window.mission_summary.text(),
                                                     "eligible_ids": sorted(eligible_ids), "listed_ids": sorted(listed_ids),
                                                     "catalogue_ids": sorted(catalogue_ids),
                                                     "overview_ids": sorted(overview_ids),
                                                     "overview_point_count": len(window.operation_overview.map.points),
                                                     "group_count": len(groups),
                                                     "groups": [{key: group[key] for key in ("confidence", "mission_ids", "span_m", "complete_trip_confirmed")} for group in groups]}
                if args.self_test_shared:
                    assert getattr(snapshot, "is_shared_map", False), "First launch did not select the shared map"
                    assert snapshot.source_path is None and snapshot.saved_at is None, "Shared map must not fabricate save source or timestamp"
                    assert window.current_base(), "Shared base not loaded"
                    assert not window.map.terrain.isNull(), "Shared background not loaded"
                    assert "no host save connected" in window.world_label.text(), "Host save connection status missing"
                    assert window.animal_panel.snapshot is None and window.animal_panel.summary is None, "Shared map must not contain private animal data"
                    assert not window.animal_panel.animal_cards and not window.animal_panel.animal_map.map.points, "Shared map must not retain animal cards or positions"
                    from icarus_helper.animals import load_animal_catalog
                    animal_catalog = load_animal_catalog()
                    assert animal_catalog.get("species") and animal_catalog.get("level_rule", {}).get("verified"), "Animal catalog or level rules missing from bundle"
                    from icarus_helper.owner_stats import load_owner_stat_catalog
                    assert load_owner_stat_catalog().get("bestiary"), "Owner bestiary / bonus catalog missing from bundle"
                    assert not window.windowIcon().pixmap(32, 32).isNull(), "Application icon cannot be rendered"
                    from icarus_helper.item_icons import item_icon_path
                    assert all(not QImage(str(item_icon_path(ident))).isNull() for ident in ("Gold_Ore", "Metal_Ore", "Copper_Ore")), "Native mineral icons cannot be decoded"
                    release_result.update(stage="shared_map_and_base_loaded", map_name=snapshot.map_name,
                                          point_count=len(window.points), visible_map_points=len(window.visible_points),
                                          shared_map=True, host_save_connected=False, base_name=window.current_base()["name"],
                                          private_snapshot_included=False, animal_tab_loaded=True,
                                          animal_catalog_species=len(animal_catalog["species"]),
                                          animal_native_icon_files=len(animal_icon_files),
                                          animal_capacity_catalog_loaded=True,
                                          animal_requires_host_save=True, window_title=window.windowTitle())
                    finish_release(True)
                    return
                assert window.missions, "Mission catalog not loaded"
                assert window.mission_detail.toPlainText().strip(), "Mission content not loaded"
                assert not window.windowIcon().isNull(), "Application icon not loaded"
                assert not window.windowIcon().pixmap(32, 32).isNull(), "Application icon cannot be rendered"
                assert not eligible_ids or window.mission_route.stages.count() > 0, "Mission map stages not loaded"
                assert window.tabs.indexOf(window.inventory_panel) >= 0, "Base inventory tab not loaded"
                from icarus_helper.inventory import load_item_catalog
                inventory_catalog = load_item_catalog()
                assert inventory_catalog["items"], "Item catalog missing from bundle"
                if window.current_base() and window.inventory_panel.summary is None and (window.inventory_panel.busy or window.inventory_panel.debounce.isActive()):
                    QTimer.singleShot(100, lambda: check_release(None))
                    return
                assert not window.current_base() or window.inventory_panel.summary, window.inventory_panel.status.text()
                stock = window.inventory_panel.summary or {}
                if window.animal_panel.summary is None and (window.animal_panel.busy or window.animal_panel.debounce.isActive()):
                    QTimer.singleShot(100, lambda: check_release(None))
                    return
                assert window.animal_panel.summary is not None, window.animal_panel.status.text()
                animals = window.animal_panel.summary
                entries = animals.get("animals", [])
                assert len(window.animal_panel.animal_cards) == len(entries), "Animal cards do not match the saved list"
                assert len(window.animal_panel.owner_columns) == len({a.get("owner_key") or a.get("owner_name") or "unknown" for a in entries}), "Owner columns do not match the save"
                assert len(window.animal_panel.animal_map.map.points) == sum(a.get("position") is not None for a in entries), "Animal map positions do not match the snapshot"
                from icarus_helper.item_icons import has_item_icon, item_icon_path
                native_items = [item["id"] for item in stock.get("items", []) if has_item_icon(item["id"])]
                missing_icons = [item["id"] for item in stock.get("items", []) if not has_item_icon(item["id"])]
                from icarus_helper.map_icons import mineral_item_id
                map_mineral_items = {str(point.resource_type): mineral_item_id(point.resource_type) for point in window.points if point.kind in ("ore", "deep_deposit") and mineral_item_id(point.resource_type)}
                assert not stock.get("items") or native_items, "Item icons missing or unreadable"
                icon_probes = set(native_items) | set(map_mineral_items.values()) | {"Gold_Ore", "Refined_Gold"}
                assert all(has_item_icon(ident) and not QImage(str(item_icon_path(ident))).isNull() for ident in icon_probes), "Item icons missing or cannot be decoded"
                release_result.update(stage="snapshot_and_widgets_loaded", map_name=snapshot.map_name,
                                      snapshot_sha256=snapshot.sha256, point_count=len(window.points),
                                      mission_count=len(window.missions), visible_map_points=len(window.visible_points),
                                      drill_count=sum(p.kind == "drill" for p in window.points),
                                      mission_map_points=len(window.mission_route.map.points),
                                      mission_map_stages=window.mission_route.stages.count(), app_icon_loaded=True,
                                      inventory_tab_loaded=True, inventory_requires_base=window.current_base() is None,
                                      inventory_catalog_items=len(inventory_catalog["items"]),
                                      inventory_containers=stock.get("coverage", {}).get("counted_containers"),
                                      inventory_item_types=stock.get("coverage", {}).get("item_types"),
                                      inventory_native_icons=len(native_items), inventory_missing_icons=missing_icons,
                                      animal_tab_loaded=True, tamed_animal_count=len(animals.get("animals", [])),
                                      animal_owner_columns=len(window.animal_panel.owner_columns),
                                      animal_map_positions=len(window.animal_panel.animal_map.map.points),
                                      animal_native_icon_files=len(animal_icon_files),
                                      animal_capacity_status_counts=animals.get("coverage", {}).get("capacity_status_counts", {}),
                                      animal_live_status=animals.get("live_stats", {}).get("status"),
                                      animal_live_sample_time=animals.get("live_stats", {}).get("sample_time"),
                                      live_animal_positions=animals.get("coverage", {}).get("live_stats", {}).get("applied_animal_positions", 0),
                                      live_player_positions=animals.get("coverage", {}).get("live_stats", {}).get("applied_player_positions", 0),
                                      live_cache_mode=animals.get("live_stats", {}).get("coverage", {}).get("cache_mode"),
                                      animal_map_player_positions=len(window.animal_panel.animal_map.map.players),
                                      animal_coverage=animals.get("coverage", {}),
                                      map_mineral_icons=map_mineral_items, window_title=window.windowTitle(),
                                      world_selector_width=window.world_combo.width())
                if args.self_test_remote:
                    assert getattr(snapshot, "is_remote", False), "Public sync source not used"
                    assert not window.world_backups_panel._connected(), "Remote copies cannot be registered for backup or restore"
                    assert not window.world_backups_panel.restore_button.isEnabled(), "Remote restore button must be disabled"
                    assert not animals.get("coverage", {}).get("live_stats", {}).get("applied_pairs"), "Remote world must not use local game readings"
                    release_result.update(remote_world=True, remote_stale=getattr(snapshot, "remote_stale", False),
                                          remote_read_only=True, remote_poll_ms=window.controller.remote_poll.interval(),
                                          remote_header=window.world_label.text())
                finish_release(True)
            except Exception:
                finish_release(False, traceback.format_exc())

        window.controller.loaded.connect(check_release)
        if window.snapshot is not None:
            QTimer.singleShot(0, lambda: check_release(None))
        QTimer.singleShot(args.self_test_timeout * 1000, lambda: finish_release(False, "Startup verification timed out; " + getattr(window, "last_error", "No valid snapshot")))
    if args.smoke_seconds:
        QTimer.singleShot(args.smoke_seconds * 1000, window.close)
    result = app.exec()
    if isolated:
        isolated.cleanup()
    return (0 if release_result["success"] else 1) if args.self_test_report else result


if __name__ == "__main__":
    raise SystemExit(main())
