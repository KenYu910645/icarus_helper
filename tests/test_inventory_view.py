"""Inventory charts preserve stack units, missing values and world isolation."""
from datetime import datetime, timezone
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from icarus_helper import inventory_view
from icarus_helper.inventory_view import InventoryPanel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def summary(quantity=320, label="銅"):
    ore = {"id": "CopperOre", "name": f"{label}礦", "category": "metals", "material": "copper", "form": "ore", "quantity": quantity, "containers": ["box"]}
    ingot = {"id": "CopperIngot", "name": f"{label}錠", "category": "metals", "material": "copper", "form": "ingot", "quantity": 70, "containers": ["box"]}
    return {"categories": [{"id": "metals", "label": "金屬", "rows": [{"key": "copper", "label": label, "values": {"ore": quantity, "ingot": 70}, "items": [ore, ingot]}], "series": [{"id": "ore", "label": "原礦"}, {"id": "ingot", "label": "錠"}]}], "items": [ore, ingot], "containers": [{"id": "box", "name": "木製儲物箱"}], "coverage": {"status": "ok", "counted_containers": 1}}


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def test_material_groups_keep_ore_and_ingots_separate(app):
    panel = InventoryPanel()
    panel.resize(1000, 750)
    panel.set_summary(summary(), datetime(2026, 11, 1, tzinfo=timezone.utc))
    panel.show()
    app.processEvents()
    assert len(panel.chart.rows) == 1
    assert panel.chart.rows[0]["values"] == {"ore": 320, "ingot": 70}
    assert len(panel.chart.bar_targets) == 2
    assert len(panel.chart.icon_targets) == 2
    assert len({series["color"] for series in panel.chart.series}) == 2
    assert [panel.table.item(i, 3).text() for i in range(2)] == ["320", "70"]
    panel.close()


def test_unknown_quantity_keeps_known_partial_and_never_draws_zero(app):
    panel = InventoryPanel()
    data = summary(None)
    data["items"][0]["known_quantity"] = 45
    data["items"][0]["unknown_stacks"] = 1
    data["coverage"]["unknown_stacks"] = 1
    panel.set_summary(data)
    panel.resize(900, 700)
    panel.show()
    app.processEvents()
    assert panel.table.item(0, 3).text() == "70"
    assert panel.table.item(1, 3).text() == "未知（已確認 45）"
    assert panel.chart.rows[0]["values"]["ore"] is None
    assert "數量：未知" in panel.chart.bar_targets[0][1]
    assert not panel.table.item(0, 0).icon().isNull()
    assert panel.chart.icon_targets[0]["item_id"] == "CopperOre"
    panel.close()


def test_category_change_replaces_rows_and_details(app):
    panel = InventoryPanel()
    data = summary()
    wood = {"id": "Wood", "name": "木材", "category": "organics", "form": "other", "quantity": 500, "containers": ["box"]}
    data["items"].append(wood)
    data["categories"].append({"id": "organics", "label": "有機資源", "rows": [{"key": "Wood", "label": "木材", "values": {"other": 500}, "items": [wood]}]})
    panel.set_summary(data)
    panel.category_combo.setCurrentIndex(1)
    assert panel.chart.rows[0]["label"] == "木材"
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 0).text() == "木材"
    assert panel.table.item(0, 3).text() == "500"
    panel.close()


def test_missing_base_and_unreadable_inventory_are_not_zero(app):
    panel = InventoryPanel()
    panel.set_snapshot({"world_id": "one"}, None)
    assert panel.summary is None and not panel.chart.rows
    assert "指定" in panel.chart.message
    panel.set_summary({"coverage": {"status": "unsupported", "reason": "目前存檔格式尚未支援庫存讀取。"}})
    assert "尚未支援" in panel.chart.message
    assert not panel.chart.rows and panel.table.rowCount() == 0
    panel.close()


def test_old_world_worker_cannot_overwrite_new_base_and_radius(app):
    started, release = threading.Event(), threading.Event()
    calls = []
    def compute(snapshot, base, radius_m):
        calls.append((snapshot["world_id"], base["name"], radius_m))
        if snapshot["world_id"] == "old":
            started.set()
            release.wait(3)
        return summary(radius_m, base["name"])
    panel = InventoryPanel(compute)
    panel.set_snapshot({"world_id": "old"}, {"id": "old", "name": "舊家", "x": 0, "y": 0})
    panel.debounce.stop()
    panel._start()
    wait_for(app, started.is_set)
    panel.set_snapshot({"world_id": "new"}, {"id": "new", "name": "新家", "x": 100, "y": 100})
    panel.radius_spin.setValue(200)
    assert panel.summary is None
    release.set()
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    assert calls == [("old", "舊家", 100), ("new", "新家", 200)]
    assert panel.chart.rows[0]["label"] == "新家"
    assert panel.chart.rows[0]["values"]["ore"] == 200
    panel.close()


def test_same_base_refresh_failure_retains_successful_chart(app):
    fail = False
    def compute(snapshot, base, radius_m):
        if fail:
            raise ValueError("unreadable snapshot")
        return summary()
    panel = InventoryPanel(compute)
    snapshot, base = {"world_id": "one"}, {"id": "home", "name": "我們家", "x": 0, "y": 0}
    panel.set_snapshot(snapshot, base)
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    previous = panel.summary
    fail = True
    panel.set_snapshot(snapshot, base)
    wait_for(app, lambda: bool(panel.last_error) and not panel.busy)
    assert panel.summary is previous
    assert panel.chart.rows[0]["values"]["ore"] == 320
    assert "上次成功" in panel.status.text()
    panel.close()


def test_each_form_keeps_its_exact_item_icon_in_chart_and_table(app, monkeypatch):
    colors = {"CopperOre": QColor("#d46635"), "CopperIngot": QColor("#37bbc8")}
    def pixmap(ident, size=32):
        result = QPixmap(size, size // 2 if ident == "CopperOre" else size)
        result.fill(colors[ident])
        return result
    monkeypatch.setattr(inventory_view, "item_pixmap", pixmap)
    monkeypatch.setattr(inventory_view, "item_icon", lambda ident: QIcon(pixmap(ident)))
    monkeypatch.setattr(inventory_view, "has_item_icon", lambda ident: ident in colors)
    panel = InventoryPanel()
    panel.resize(1000, 750)
    panel.set_summary(summary())
    panel.show()
    app.processEvents()
    targets = panel.chart.icon_targets
    assert [(target["item_id"], target["form"]) for target in targets] == [("CopperOre", "ore"), ("CopperIngot", "ingot")]
    rendered = panel.chart.grab().toImage()
    for target in targets:
        assert target["has_icon"]
        assert rendered.pixelColor(target["rect"].center().toPoint()) == colors[target["item_id"]]
    assert rendered.pixelColor(targets[0]["rect"].center().toPoint() + QPoint(0, 12)) != colors["CopperOre"]
    assert [panel.table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(2)] == ["CopperOre", "CopperIngot"]
    assert panel.table.item(0, 0).icon().cacheKey() != panel.table.item(1, 0).icon().cacheKey()
    assert [panel.table.item(i, 3).text() for i in range(2)] == ["320", "70"]
    panel.close()


def test_small_window_can_scroll_to_item_icons_and_axis_labels(app):
    panel = InventoryPanel()
    panel.resize(700, 480)
    panel.set_summary(summary())
    panel.show()
    app.processEvents()
    scroll = panel.chart_scroll
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    app.processEvents()
    for target in panel.chart.icon_targets:
        bottom = panel.chart.mapTo(scroll.viewport(), target["rect"].bottomLeft().toPoint())
        assert scroll.viewport().rect().contains(bottom)
    axis_bottom = panel.chart.mapTo(scroll.viewport(), panel.chart.rect().bottomLeft())
    assert axis_bottom.y() <= scroll.viewport().height()
    panel.close()


@pytest.mark.parametrize("items_in_summary", [True, False])
def test_quantity_order_is_numeric_descending_for_chart_and_details(app, items_in_summary):
    from copy import deepcopy

    def item(ident, quantity, material, form="other"):
        return {"id": ident, "name": ident, "category": "metals", "material": material,
                "form": form, "quantity": quantity, "containers": ["box"]}

    copper = [item("CopperOre", 200, "copper", "ore"), item("CopperIngot", 200, "copper", "ingot")]
    iron = [item("IronOre", 300, "iron", "ore"), item("IronIngot", 5, "iron", "ingot")]
    coal = [item("Coal", 4000, "coal")]
    zero = [item("Zero", 0, "zero")]
    unknown = [item("UnknownOre", None, "unknown", "ore")]
    unknown[0]["known_quantity"] = 9000
    rows = [
        {"key": "copper", "label": "Copper", "values": {"ore": 200, "ingot": 200}, "items": copper},
        {"key": "unknown", "label": "Unknown", "values": {"ore": None}, "items": unknown},
        {"key": "zero", "label": "Zero", "values": {"other": 0}, "items": zero},
        {"key": "iron", "label": "Iron", "values": {"ore": 300, "ingot": 5}, "items": iron},
        {"key": "coal", "label": "Coal", "values": {"other": 4000}, "items": coal},
    ]
    data = {"categories": [{"id": "metals", "label": "金屬", "rows": rows}],
            "items": [value for row in rows for value in row["items"]] if items_in_summary else [],
            "coverage": {"status": "partial", "unknown_stacks": 1}}
    before = deepcopy(data)
    panel = InventoryPanel()
    panel.set_summary(data)
    assert [row["key"] for row in panel.chart.rows] == ["coal", "copper", "iron", "zero", "unknown"]
    assert [panel.table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(panel.table.rowCount())] == [
        "Coal", "IronOre", "CopperIngot", "CopperOre", "IronIngot", "Zero", "UnknownOre"]
    assert [panel.table.item(i, 3).text() for i in range(panel.table.rowCount())] == [
        "4,000", "300", "200", "200", "5", "0", "未知（已確認 9,000）"]
    assert data == before
    panel.close()
