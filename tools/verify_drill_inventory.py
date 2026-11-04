"""唯讀核對鑽機庫存及隔離 Qt 地圖；不代表 Windows 或遊戲內操作驗收。"""
from __future__ import annotations

import argparse
from collections import Counter
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

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper.saves import load_snapshot
from icarus_helper.settings import Settings, settings_path
from icarus_helper.ui import MainWindow, STYLESHEET
from icarus_helper.map_widget import drill_stock_label
from icarus_helper.drill_inventory import inventory_for_point


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def independent_output(record):
    """直接核對原始槽，不使用正式摘要的數量解析或加總函式。"""
    inventories = record.get("SavedInventories")
    assert isinstance(inventories, list), "缺少 SavedInventories"
    output = [inv for inv in inventories if inv.get("InventoryID") == 1]
    assert output, "未找到 General 輸出槽，不能冒稱空置"
    totals, seen = Counter(), {}
    excluded = Counter()
    slots = []
    for inv in inventories:
        for slot in inv.get("Slots", []):
            item = slot.get("ItemStaticData")
            if isinstance(item, dict):
                item = item.get("RowName")
            if not item or str(item).lower() in {"none", "null"}:
                continue
            values = [p["Value"] for p in slot.get("DynamicData", []) if p.get("Index") == 7]
            assert values and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)
            assert len(set(values)) == 1, "物品數量欄位衝突"
            quantity = values[0]
            if inv.get("InventoryID") != 1:
                excluded[item] += quantity
                continue
            location = slot["Location"]
            signature = json.dumps(slot, ensure_ascii=False, sort_keys=True)
            if location in seen:
                assert seen[location] == signature, "相同輸出槽存在衝突"
                continue
            seen[location] = signature
            totals[item] += quantity
            slots.append({"slot": location, "item_id": item, "quantity": quantity})
    return dict(totals), slots, dict(excluded)


class DrillVerificationWindow(MainWindow):
    def update_inventory(self):
        # 地圖驗收不啟動無關動物記憶體讀取或家中物資工作。
        self.inventory_panel.clear()
        self.animal_panel.clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/drill-inventory-20261102"))
    parser.add_argument("--reference-settings", type=Path, default=settings_path())
    parser.add_argument("--save", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference_bytes = args.reference_settings.read_bytes()
    reference = json.loads(reference_bytes.decode("utf-8-sig"))
    source = args.save or Path(reference["selected_source"])
    before = digest(source)
    result = {"test_type": "真實存檔唯讀逐槽核對及隔離 Qt 地圖互動；非 Windows／遊戲內操作", "success": False}
    window = None
    with tempfile.TemporaryDirectory(prefix="icarus-drill-inventory-") as directory:
        temp = Path(directory)
        snapshot = load_snapshot(source, cache_dir=temp / "snapshots")
        stable_hash = digest(source)
        result.update(world=snapshot.name, map=snapshot.map_name, world_id=snapshot.world_id,
                      saved_at=snapshot.saved_at.isoformat(), snapshot_sha256=snapshot.sha256,
                      source_unchanged_during_read=before == snapshot.sha256 == stable_hash)
        raw_state = json.dumps(snapshot.raw_records, ensure_ascii=False, sort_keys=True, default=str)
        drills = [d for d in snapshot.deployables if d.kind == "drill"]
        assert drills, "所選世界沒有可驗收的鑽機"
        audits = []
        for drill in drills:
            expected, slots, excluded = independent_output(drill.raw)
            summary = drill.raw["_drill_inventory"]
            observed = {item.get("id", item.get("item_id")): item["quantity"] for item in summary["items"]}
            assert observed == expected, (expected, observed)
            assert summary["quantity"] == sum(expected.values())
            foundation = drill.raw.get("DeployableRecord", {}).get("FoundationActorIcarusUID")
            candidates = [d for d in snapshot.resources if d.kind == "deep_deposit" and d.raw.get("IcarusActorGUID") == foundation]
            assert len(candidates) == 1
            deposit = candidates[0]
            linked = deposit.raw["_drill"]
            assert linked["id"] == drill.id and linked["inventory"] == summary
            assert linked["confidence"] == "已確認"
            audits.append({"drill_id": drill.id, "deposit_id": deposit.id,
                           "resource_type": deposit.resource_type, "items": expected,
                           "quantity": sum(expected.values()), "slots": slots,
                           "excluded_other_inventories": excluded,
                           "status": summary["status"], "position": list(drill.position)})
        assert json.dumps(snapshot.raw_records, ensure_ascii=False, sort_keys=True, default=str) == raw_state
        result.update(drill_count=len(drills), drills=audits, raw_records_unchanged=True)
        # MainWindow 只讀取完整原始 bytes 的獨立副本；沿用該版的存檔時間與世界身分。
        isolated_source = temp / "source" / source.name
        isolated_source.parent.mkdir()
        isolated_source.write_bytes(snapshot.cache_path.read_bytes())
        assert digest(isolated_source) == snapshot.sha256
        app = QApplication.instance() or QApplication([])
        for font in ("msjh.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / font))
        app.setFont(QFont("Microsoft JhengHei", 10))
        app.setStyle("Fusion")
        app.setStyleSheet(STYLESHEET)
        settings = Settings(temp / "settings.json")
        settings.data = deepcopy(reference)
        settings.data["selected_source"] = str(isolated_source)
        window = DrillVerificationWindow(initial_path=isolated_source, settings=settings)
        window.show()
        try:
            deadline = time.monotonic() + 60
            while window.snapshot is None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert window.snapshot is not None, getattr(window, "last_error", "載入逾時")
            assert window.snapshot.sha256 == snapshot.sha256
            assert not window._production_backups and not window._backup_service_started
            window.tabs.setCurrentIndex(0)
            window.drill_combo.setCurrentIndex(window.drill_combo.findData("installed"))
            app.processEvents()
            assert len(window.visible_points) == len(drills)
            ui_audits = []
            for audit in audits:
                point = next(p for p in window.visible_points if p.id == audit["deposit_id"])
                window.show_point(point)
                text = window.point_detail.toPlainText()
                quantity = audit["quantity"]
                assert str(quantity) in text or f"{quantity:,}" in text, text
                label = drill_stock_label(point)
                assert str(quantity) in label or f"{quantity:,}" in label, label
                ui_audits.append({"deposit_id": point.id, "label": label,
                                  "detail_has_correct_quantity": True})
            positions = [QPointF(p.x / 100, p.y / 100) for p in window.visible_points]
            bounds = QRectF(positions[0], positions[0])
            for position in positions[1:]:
                bounds = bounds.united(QRectF(position.x(), position.y(), .01, .01))
            window.map._fit_mode = False
            window.map.fitInView(bounds.adjusted(-250, -250, 250, 250), Qt.AspectRatioMode.KeepAspectRatio)
            app.processEvents()
            window.grab().save(str(output / "all-drill-inventories.png"))
            targets = [next((p for p in window.visible_points if p.resource_type == mineral), window.visible_points[index])
                       for index, mineral in enumerate(("Gold", "Platinum"))]
            clicked = []
            for index, target in enumerate(targets):
                window.map.focus_point(target)
                app.processEvents()
                # 點擊圖標下方第二行庫存，驗證整塊名稱底板可操作。
                center = window.map.mapFromScene(target.x / 100, target.y / 100)
                size = window.map._marker_size(target, window.map.transform().m11())
                click = QPointF(center) + QPointF(0, size / 2 + 31)
                hit = window.map.hit_target(click)
                assert hit is not None and hit["members"][0].id == target.id
                QTest.mouseClick(window.map.viewport(), Qt.MouseButton.LeftButton, pos=click.toPoint())
                app.processEvents()
                assert window.selected_point.id == target.id
                expected = next(a["quantity"] for a in audits if a["deposit_id"] == target.id)
                detail = window.point_detail.toPlainText()
                assert str(expected) in detail or f"{expected:,}" in detail
                clicked.append({"deposit_id": target.id, "quantity": expected, "second_line_clicked": True})
                window.grab().save(str(output / f"drill-inventory-selected-{index + 1}.png"))
            window.resize(1100, 760)
            app.processEvents()
            window.grab().save(str(output / "drill-inventory-small.png"))
            # 切回共用地圖必須清除私人鑽機庫存及所選明細。
            shared_index = window.world_combo.findData("shared://prometheus")
            if shared_index < 0:
                from icarus_helper.shared_profile import SHARED_SOURCE
                shared_index = window.world_combo.findData(SHARED_SOURCE)
            window.world_combo.setCurrentIndex(shared_index)
            deadline = time.monotonic() + 30
            while (window.snapshot is None or not getattr(window.snapshot, "is_shared_map", False)) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert window.snapshot is not None and getattr(window.snapshot, "is_shared_map", False)
            assert all(inventory_for_point(p) is None for p in window.points)
            assert window.selected_point is None and "鑽機庫存（存檔）" not in window.point_detail.toPlainText()
            result.update(ui=ui_audits, ui_clicked_deposits=clicked, shared_map_stock_cleared=True,
                          qt_checks=["全圖量級資料載入", "逐台礦床明確關聯", "庫存件數明細", "已放鑽機篩選", "原位點選", "大小視窗渲染"],
                          settings_unchanged=args.reference_settings.read_bytes() == reference_bytes,
                          source_unchanged_after_render=digest(source) == before,
                          success=True)
        finally:
            window.close()
            app.processEvents()
        assert args.reference_settings.read_bytes() == reference_bytes
    (output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"drills", "ui"}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
