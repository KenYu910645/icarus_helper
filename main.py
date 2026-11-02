"""Launch ICARUS Helper without touching the game process or save files."""
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
    parser = argparse.ArgumentParser(description="ICARUS Helper read-only desktop companion")
    parser.add_argument("--save", help="World save source (read-only)")
    parser.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-shared", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-reference-settings", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-timeout", type=int, default=60, help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(filename=app_dir() / "app.log", encoding="utf-8", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    def report_exception(kind, value, tb):
        logging.error("Unhandled exception", exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report_exception
    # Initialise diagnostics before native imports: a broken Qt dependency used
    # to fail before anything was written to app.log.
    try:
        from PySide6.QtCore import QTimer
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
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ICARUS Helper")
    app.setOrganizationName("IcarusHelper")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "icarus_helper" / "assets" / "app.ico")))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    isolated = tempfile.TemporaryDirectory(prefix="icarus-helper-release-") if args.self_test_report else None
    isolated_settings = Settings(Path(isolated.name) / "settings.json") if isolated else None
    if isolated_settings is not None and args.self_test_reference_settings and not args.self_test_shared:
        reference = Settings(args.self_test_reference_settings)
        # Read home/source preferences, but keep every verification write isolated
        # and use default layers so a user's hidden layers do not fail the gate.
        isolated_settings.data = {key: reference.data[key] for key in ("worlds", "selected_source") if key in reference.data}
    window = MainWindow(args.save, isolated_settings)
    window.show()
    release_result = {"success": False, "stage": "waiting_for_snapshot", "frozen": bool(getattr(sys, "frozen", False)), "checked_at": datetime.now(timezone.utc).isoformat()}
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
                assert snapshot is not None, "未載入世界快照"
                assert window.points and window.visible_points, "地圖資源未載入"
                if args.self_test_shared:
                    assert getattr(snapshot, "is_shared_map", False), "首次啟動沒有選擇共用地圖"
                    assert snapshot.source_path is None and snapshot.saved_at is None, "共用地圖不得偽造存檔來源／時間"
                    assert window.current_base(), "共用基地未載入"
                    assert not window.map.terrain.isNull(), "共用底圖未載入"
                    assert "未連接" in window.world_label.text(), "未顯示主機存檔連線狀態"
                    assert not window.windowIcon().pixmap(32, 32).isNull(), "APP 圖示無法繪製"
                    from icarus_helper.item_icons import item_icon_path
                    assert all(not QImage(str(item_icon_path(ident))).isNull() for ident in ("Gold_Ore", "Metal_Ore", "Copper_Ore")), "原生礦物圖示無法解碼"
                    release_result.update(stage="shared_map_and_base_loaded", map_name=snapshot.map_name,
                                          point_count=len(window.points), visible_map_points=len(window.visible_points),
                                          shared_map=True, host_save_connected=False, base_name=window.current_base()["name"],
                                          private_snapshot_included=False, window_title=window.windowTitle())
                    finish_release(True)
                    return
                assert window.missions and window.mission_list.count(), "任務目錄未載入"
                assert window.mission_detail.toPlainText().strip(), "任務內容未載入"
                assert not window.windowIcon().isNull(), "APP 圖示未載入"
                assert not window.windowIcon().pixmap(32, 32).isNull(), "APP 圖示無法繪製"
                assert window.mission_route.stages.count() > 0, "任務地圖階段未載入"
                assert window.tabs.indexOf(window.inventory_panel) >= 0, "家中物資頁未載入"
                from icarus_helper.inventory import load_item_catalog
                inventory_catalog = load_item_catalog()
                assert inventory_catalog["items"], "物品目錄未封裝"
                if window.current_base() and window.inventory_panel.summary is None and (window.inventory_panel.busy or window.inventory_panel.debounce.isActive()):
                    QTimer.singleShot(100, lambda: check_release(None))
                    return
                assert not window.current_base() or window.inventory_panel.summary, window.inventory_panel.status.text()
                stock = window.inventory_panel.summary or {}
                from icarus_helper.item_icons import has_item_icon, item_icon_path
                native_items = [item["id"] for item in stock.get("items", []) if has_item_icon(item["id"])]
                missing_icons = [item["id"] for item in stock.get("items", []) if not has_item_icon(item["id"])]
                from icarus_helper.map_icons import mineral_item_id
                map_mineral_items = {str(point.resource_type): mineral_item_id(point.resource_type) for point in window.points if point.kind in ("ore", "deep_deposit") and mineral_item_id(point.resource_type)}
                assert not stock.get("items") or native_items, "物品圖示未封裝或無法讀取"
                icon_probes = set(native_items) | set(map_mineral_items.values()) | {"Gold_Ore", "Refined_Gold"}
                assert all(has_item_icon(ident) and not QImage(str(item_icon_path(ident))).isNull() for ident in icon_probes), "物品圖示未封裝或無法解碼"
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
                                      map_mineral_icons=map_mineral_items, window_title=window.windowTitle(),
                                      world_selector_width=window.world_combo.width())
                finish_release(True)
            except Exception:
                finish_release(False, traceback.format_exc())

        window.controller.loaded.connect(check_release)
        if window.snapshot is not None:
            QTimer.singleShot(0, lambda: check_release(None))
        QTimer.singleShot(args.self_test_timeout * 1000, lambda: finish_release(False, "啟動驗收逾時；" + getattr(window, "last_error", "沒有成功快照")))
    if args.smoke_seconds:
        QTimer.singleShot(args.smoke_seconds * 1000, window.close)
    result = app.exec()
    if isolated:
        isolated.cleanup()
    return (0 if release_result["success"] else 1) if args.self_test_report else result


if __name__ == "__main__":
    raise SystemExit(main())
