"""Render/interaction checks for our Qt UI, isolated settings and read-only saves.

These are widget tests, not a claim that in-game actions were performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper.settings import Settings
from icarus_helper.ui import MainWindow, STYLESHEET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/ui")
    parser.add_argument("--save")
    parser.add_argument("--reference-settings", type=Path, help="Read existing settings into an isolated temporary copy")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    temp = tempfile.TemporaryDirectory(prefix="icarus-helper-ui-")
    app = QApplication([])
    # The Windows offscreen platform does not enumerate system fonts itself.
    for font in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/segoeui.ttf")):
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft JhengHei", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    isolated_settings = Settings(Path(temp.name) / "settings.json")
    if args.reference_settings:
        isolated_settings.data = json.loads(args.reference_settings.read_text(encoding="utf-8"))
    window = MainWindow(args.save, isolated_settings)
    window.resize(1540, 1000)
    window.show()
    results = {"test_type": "Qt widget rendering and interaction; no game control", "checks": {}}
    started = time.perf_counter()
    completed = False

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        results["checks"][name] = True

    def perform():
        nonlocal completed
        if completed:
            return
        completed = True
        try:
            results["load_seconds"] = round(time.perf_counter() - started, 3)
            results["world"] = {"map": window.snapshot.map_name, "save_sha256": window.snapshot.sha256, "point_count": len(window.points)}
            results["header"] = {"title": window.windowTitle(), "world_info": window.world_label.text(), "world_info_x": window.world_label.x(), "world_selector_x": window.world_combo.x(), "world_selector_width": window.world_combo.width()}
            from icarus_helper.map_icons import mineral_item_id
            results["map_mineral_icons"] = {point.resource_type: mineral_item_id(point.resource_type) for point in window.points if point.kind in ("ore", "deep_deposit") and mineral_item_id(point.resource_type)}
            check("snapshot_loaded", bool(window.snapshot))
            app.processEvents()
            window.grab().save(str(output / "startup.png"))
            results["default_layers"] = {key: box.isChecked() for key, box in window.layer_boxes.items()}
            check("crossed_out_widgets_removed", all(not hasattr(window, attr) for attr in ("stats", "banner", "table", "base_combo", "saved_label", "status")))
            check("header_has_snapshot_time", window.snapshot.saved_at.astimezone().strftime("%m/%d %H:%M") in window.world_label.text())
            if window.current_base():
                deadline = time.perf_counter() + 15
                while window.inventory_panel.summary is None and time.perf_counter() < deadline:
                    QTest.qWait(50)
                check("inventory_loaded_with_configured_home", bool(window.inventory_panel.summary))
                results["inventory"] = window.inventory_panel.summary.get("coverage", {})
                results["inventory_categories"] = [{"id": cat["id"], "label": cat["label"], "materials": len(cat["rows"]), "items": sum(len(row["items"]) for row in cat["rows"])} for cat in window.inventory_panel.summary["categories"]]
                check("inventory_categories_are_broad_groups", all(cat["id"] in ("resources", "construction", "supplies", "gear", "other", "unknown") for cat in window.inventory_panel.summary["categories"]))
                window.tabs.setCurrentWidget(window.inventory_panel)
                app.processEvents()
                from icarus_helper.item_icons import has_item_icon
                stock_items = window.inventory_panel.summary["items"]
                results["inventory_icons"] = {"native": sum(has_item_icon(item["id"]) for item in stock_items), "total": len(stock_items), "missing": [item["id"] for item in stock_items if not has_item_icon(item["id"])]}
                check("inventory_item_table_has_icons", all(not window.inventory_panel.table.item(row, 0).icon().isNull() for row in range(window.inventory_panel.table.rowCount())))
                check("inventory_chart_has_item_icons", bool(window.inventory_panel.chart.icon_targets))
                window.grab().save(str(output / "inventory.png"))
                for index in range(window.inventory_panel.category_combo.count()):
                    window.inventory_panel.category_combo.setCurrentIndex(index)
                    app.processEvents()
                    window.grab().save(str(output / f"inventory-{window.inventory_panel.category_combo.currentData()}.png"))
                window.inventory_panel.category_combo.setCurrentIndex(0)
                window.tabs.setCurrentIndex(0)
            if window.snapshot.map_name == "Prometheus":
                check("full_map_ordinary_nodes", len([p for p in window.points if p.kind == "ore"]) > 24000)
            window.map.fit_map()
            app.processEvents()
            window.grab().save(str(output / "map.png"))
            scale = window.map.transform().m11()
            window.map.zoom(1.4)
            check("zoom_changes_scale", window.map.transform().m11() > scale)
            before = window.map.mapToScene(window.map.viewport().rect().center())
            viewport = window.map.viewport()
            p = viewport.rect().center()
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=p)
            QTest.mouseMove(viewport, p + QPoint(90, 35), 100)
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=p + QPoint(90, 35))
            after = window.map.mapToScene(viewport.rect().center())
            check("pan_changes_center", (after - before).manhattanLength() > 1)
            all_count = len(window.visible_points)
            window.search.setText("Gold")
            check("mineral_search_filters", 0 < len(window.visible_points) < all_count)
            window.search.clear()
            # Persist user changes and verify they survive reopening settings.
            original_ore = window.layer_boxes["ore"].isChecked()
            window.layer_boxes["ore"].setChecked(not original_ore)
            saved_display = Settings(window.settings.path).map_display()
            check("layer_preferences_persist", saved_display["layers"]["ore"] == (not original_ore))
            window.layer_boxes["ore"].setChecked(original_ore)
            window.drill_combo.setCurrentIndex(1)
            installed = [p for p in window.points if p.kind == "deep_deposit" and p.drill_status.startswith("已放置")]
            check("installed_drill_filter", len(window.visible_points) == len(installed))
            check("installed_drills_in_bounds", all(window.map.map_bounds.contains(p.x / 100, p.y / 100) for p in installed))
            if installed:
                window.map.focus_point(installed[0])
                app.processEvents()
                window.grab().save(str(output / "installed-drill.png"))
            window.drill_combo.setCurrentIndex(0)
            candidate = window.snapshot.base_candidates[0] if window.snapshot.base_candidates else window.snapshot.deployables[0] if window.snapshot.deployables else window.points[0]
            base = {"id": "test-base", "kind": "base", "name": "測試基地（獨立設定）", "x": candidate.x, "y": candidate.y, "z": candidate.z}
            config = window.settings.world(window.snapshot.world_id)
            config.update(bases=[base], active_base=base["id"])
            window.save_settings()
            window.update_bases()
            window.change_base()
            window.go_home()
            check("base_position_and_world_binding", window.current_base()["id"] == "test-base" and not window.settings.world("other-test-world")["bases"])
            reloaded = Settings(window.settings.path)
            check("base_persists", reloaded.world(window.snapshot.world_id)["active_base"] == "test-base")
            window.tabs.setCurrentIndex(1)
            check("missions_list_loaded", window.mission_list.count() > 0)
            check("guide_has_preparation", "出發前準備" in window.mission_detail.toPlainText())
            check("guide_has_steps", "任務攻略" in window.mission_detail.toPlainText())
            check("duplicate_checklist_removed", not hasattr(window, "checklist"))
            check("mission_map_present", window.mission_route.map.isVisible())
            check("mission_stages_listed", window.mission_route.stages.count() > 0)
            text = window.mission_detail.toPlainText()
            check("crossed_out_sections_removed", all(section not in text for section in ("判定依據", "推薦理由", "基地距離", "前置與接取", "來源與適用範圍", "目前遊戲 build")))
            results["mission_map"] = {"mission_id": window.mission_list.currentItem().data(Qt.ItemDataRole.UserRole)["id"], "points": len(window.mission_route.map.points), "pending": len(window.mission_route.route.get("pending_stages", []))}
            app.processEvents()
            window.grab().save(str(output / "missions.png"))
            window.all_missions_check.setChecked(True)
            results["multi_stage_routes"] = {}
            for mission_id, minimum_stages in (("PRO_A_Extraction", 5), ("PRO_Story_3", 5), ("PRO_Story_6", 10)):
                row = next((i for i in range(window.mission_list.count()) if window.mission_list.item(i).data(Qt.ItemDataRole.UserRole).get("id") == mission_id), None)
                if row is None or window.snapshot.map_name != "Prometheus":
                    continue
                window.mission_list.setCurrentRow(row)
                app.processEvents()
                check(f"{mission_id}_later_stages", window.mission_route.stages.count() >= minimum_stages and len(window.mission_route.map.points) > 0)
                results["multi_stage_routes"][mission_id] = {"points": len(window.mission_route.map.points), "stages": window.mission_route.stages.count(), "pending": len(window.mission_route.route.get("pending_stages", []))}
                window.grab().save(str(output / f"mission-{mission_id}.png"))
            # Test small-window layout after the full-size capture.
            window.tabs.setCurrentIndex(0)
            window.resize(1100, 760)
            window.map.fit_map()
            app.processEvents()
            window.grab().save(str(output / "small-window.png"))
            check("small_window_scrollable_filters", window.layer_boxes["deep"].isVisible() and window.map.viewport().height() > 100)
            results["success"] = True
        except Exception as exc:
            results["success"] = False
            results["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            (output / "verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()

    window.controller.loaded.connect(lambda *_: QTimer.singleShot(200, perform))
    QTimer.singleShot(30000, lambda: (results.update(success=False, error="UI load timeout"), (output / "verification.json").write_text(json.dumps(results), encoding="utf-8"), window.close()) if not completed else None)
    app.exec()
    temp.cleanup()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
