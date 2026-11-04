"""Synthetic Qt animal summaries: exact contents, unknowns and context isolation."""
from copy import deepcopy
from datetime import datetime, timezone
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from icarus_helper import animals_view
from icarus_helper.animals_view import AnimalPanel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def animal(ident="one", name="阿牛", distance=20, owner="隊友甲"):
    return {
        "id": ident, "name": name, "species": "水牛", "species_id": "Buffalo",
        "owner_name": owner, "level": 12, "level_note": "依目前資料規則計算",
        "status_label": "存活", "distance_m": distance,
        "stats": [{"id": "health", "label": "Health", "value": 180, "maximum": None,
                   "unit": "", "detail": "save值，未取得可驗證上限"},
                  {"id": "water", "label": "Water", "value": 0, "maximum": 100}],
        "details": [{"label": "行為", "value": "Follow"}],
        "inventory_status": "ok",
        "inventories": [
            {"id": "bag", "label": "背包", "status": "ok", "items": [
                {"id": "CopperOre", "name": "銅礦", "quantity": 320, "slot": 0},
                {"id": "CopperIngot", "name": "銅錠", "quantity": None, "slot": 1}]},
            {"id": "gear", "label": "裝備", "status": "ok", "items": [
                {"id": "Saddle", "name": "Saddle", "quantity": 1, "slot": 0}]},
        ], "warnings": [],
    }


def summary(*animals):
    return {"animals": list(animals or [animal()]), "coverage": {"status": "ok", "total_tamed": len(animals) if animals else 1}}


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def test_selected_animal_shows_exact_item_icons_and_quantities(app, monkeypatch):
    colors = {"CopperOre": QColor("#cb7232"), "CopperIngot": QColor("#31b8bd"), "Saddle": QColor("#af49da")}
    def icon(ident):
        pixmap = QPixmap(32, 32)
        pixmap.fill(colors[ident])
        return QIcon(pixmap)
    monkeypatch.setattr(animals_view, "item_icon", icon)
    monkeypatch.setattr(animals_view, "has_item_icon", lambda ident: ident in colors)
    panel = AnimalPanel()
    data = summary()
    before = deepcopy(data)
    panel.set_summary(data, datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert panel.selected_animal()["id"] == "one"
    assert panel.table.item(0, 0).text() == "阿牛"
    assert panel.table.item(0, 2).text() == "12"
    assert panel.table.item(0, 5).text() == "隊友甲"
    assert len(panel.inventory_tables) == 2
    table = panel.inventory_tables[0]
    assert [table.item(row, 1).text() for row in range(2)] == ["320", "Unknown"]
    assert [table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(2)] == ["CopperOre", "CopperIngot"]
    assert table.item(0, 0).icon().pixmap(32, 32).toImage().pixelColor(16, 16) == colors["CopperOre"]
    assert table.item(1, 0).icon().pixmap(32, 32).toImage().pixelColor(16, 16) == colors["CopperIngot"]
    assert panel.inventory_tables[1].item(0, 1).text() == "1"
    assert "save" in panel.status.text().lower()
    assert data == before
    panel.close()


def test_all_animals_default_and_distance_search_filters(app):
    panel = AnimalPanel()
    panel.set_summary(summary(animal("home", "阿牛", 50), animal("away", "遠方鳥", 250, "另一位Player"), animal("unknown", "失去座標", None)))
    assert panel.table.rowCount() == 3
    panel.scope_combo.setCurrentIndex(panel.scope_combo.findData("home"))
    assert [entry["id"] for entry in panel._row_animals] == ["home"]
    panel.scope_combo.setCurrentIndex(panel.scope_combo.findData("away"))
    assert [entry["id"] for entry in panel._row_animals] == ["away"]
    panel.scope_combo.setCurrentIndex(panel.scope_combo.findData("unknown"))
    assert [entry["id"] for entry in panel._row_animals] == ["unknown"]
    panel.scope_combo.setCurrentIndex(0)
    panel.search_edit.setText("另一位Player")
    assert [entry["id"] for entry in panel._row_animals] == ["away"]
    panel.search_edit.setText("水牛")
    assert panel.table.rowCount() == 3
    panel.search_edit.setText("沒有這個Name")
    assert panel.table.rowCount() == 0 and not panel.inventory_tables
    assert "match the current filters" in panel.detail_title.text()
    panel.close()


def test_unknown_stats_never_become_zero_or_an_invented_percentage(app):
    panel = AnimalPanel()
    entry = animal()
    entry["level"] = None
    panel.set_summary(summary(entry))
    values = {panel.stats_table.item(row, 0).data(Qt.ItemDataRole.UserRole):
              [panel.stats_table.item(row, column).text() for column in (1, 2)]
              for row in range(panel.stats_table.rowCount())}
    assert values["health"] == ["180", "Not provided"]
    assert values["water"] == ["0", "100"]
    assert values["oxygen"] == ["Unknown", "Not provided"]
    assert values["experience"] == ["Unknown", "Not provided"]
    assert panel.table.item(0, 2).text() == "Unknown"
    assert all("%" not in cell for values_for_stat in values.values() for cell in values_for_stat)
    panel.close()


def test_level_zero_is_a_valid_displayed_level(app):
    panel = AnimalPanel()
    entry = animal()
    entry["level"] = 0
    panel.set_summary(summary(entry))
    assert panel.table.item(0, 2).text() == "0"
    panel.close()


def test_inventory_precedes_long_genetics_and_talent_details(app):
    panel = AnimalPanel()
    entry = animal()
    value = "Health與StaminaLineage加成, Talents效果及saveStatusNotes." * 24
    entry["details"] = [{"label": "Talents與Lineage", "value": value}]
    panel.set_summary(summary(entry))
    panel.resize(720, 480)
    panel.show()
    app.processEvents()
    layout = panel.detail_content.layout()
    assert layout.indexOf(panel.inventory_host) == layout.indexOf(panel.stats_table) + 1
    assert layout.indexOf(panel.details_table) > layout.indexOf(panel.inventory_host)
    assert panel.details_label.text() == "Lineage, talents, and other details"
    assert panel.details_table.item(0, 1).toolTip() == value
    assert panel.details_table.rowHeight(0) > 100
    assert panel.details_table.height() > panel.details_table.rowHeight(0)
    old_height = panel.details_table.rowHeight(0)
    panel.details_table.setColumnWidth(0, 300)
    app.processEvents()
    assert panel.details_table.rowHeight(0) > old_height
    panel.close()


@pytest.mark.parametrize("status,expect_empty", [("empty", True), ("unavailable", False)])
def test_absent_inventory_distinguishes_unknown_from_confirmed_empty(app, status, expect_empty):
    panel = AnimalPanel()
    entry = animal()
    entry["inventories"] = []
    entry["inventory_status"] = status
    panel.set_summary(summary(entry))
    text = "\n".join(widget.text() for widget in panel.inventory_host.findChildren(QLabel))
    assert ("is empty" in text) == expect_empty
    assert ("contents are unknown" in text) == (not expect_empty)
    assert not panel.inventory_tables
    panel.close()


def test_unknown_inventory_section_does_not_claim_empty(app):
    panel = AnimalPanel()
    entry = animal()
    entry["inventories"] = [{"id": "gear", "label": "裝備", "status": "unavailable", "items": []}]
    panel.set_summary(summary(entry))
    text = "\n".join(widget.text() for widget in panel.inventory_host.findChildren(QLabel))
    assert "empty state unconfirmed" in text and "Confirmed empty" not in text
    panel.close()


def test_repeated_names_use_stable_identity_across_refresh(app):
    panel = AnimalPanel()
    panel.set_summary(summary(animal("first", "同名"), animal("second", "同名")))
    panel.table.selectRow(1)
    assert panel.selected_animal()["id"] == "second"
    revised = animal("second", "改名後")
    panel.set_summary(summary(revised, animal("first", "同名")))
    assert panel.selected_animal()["id"] == "second"
    assert panel.table.currentRow() == 0
    assert "改名後" in panel.detail_title.text()
    panel.close()


def test_background_all_animals_work_without_a_selected_base(app):
    calls = []
    def compute(snapshot, base, radius_m):
        calls.append((snapshot["world_id"], base, radius_m))
        return summary(animal(distance=None))
    panel = AnimalPanel(compute)
    panel.set_snapshot({"world_id": "one"}, None)
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    assert calls == [("one", None, 100)]
    assert panel.table.rowCount() == 1
    assert "unknown" in panel.table.item(0, 4).text().lower()
    panel.close()


def test_same_context_failure_retains_success_and_selected_identity(app):
    failing = False
    def compute(snapshot, base, radius_m):
        if failing:
            raise ValueError("synthetic unavailable")
        return summary(animal("first"), animal("second"))
    panel = AnimalPanel(compute)
    snapshot, base = {"world_id": "one"}, {"id": "home", "name": "我們家", "x": 0, "y": 0}
    panel.set_snapshot(snapshot, base)
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    previous = panel.summary
    panel.table.selectRow(1)
    failing = True
    panel.set_snapshot(snapshot, base)
    wait_for(app, lambda: bool(panel.last_error) and not panel.busy)
    assert panel.summary is previous and panel.selected_animal()["id"] == "second"
    assert "last valid result" in panel.status.text()
    panel.close()


def test_unavailable_summary_retains_last_good_for_same_context(app):
    panel = AnimalPanel()
    panel.set_summary(summary())
    previous = panel.summary
    assert not panel.set_summary({"animals": [], "coverage": {"status": "unsupported", "reason": "尚未支援此格式"}})
    assert panel.summary is previous and panel.table.rowCount() == 1
    assert "last valid result" in panel.status.text()
    panel.close()


def test_same_world_background_refresh_retains_selected_identity(app):
    def compute(snapshot, base, radius_m):
        if snapshot.get("revision") == 2:
            return summary(animal("second", "更新後的動物"), animal("first"))
        return summary(animal("first"), animal("second"))
    panel = AnimalPanel(compute)
    base = {"id": "home", "name": "我們家", "x": 0, "y": 0}
    panel.set_snapshot({"world_id": "one", "revision": 1}, base)
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    panel.table.selectRow(1)
    previous = panel.summary
    panel.set_snapshot({"world_id": "one", "revision": 2}, base)
    assert panel.summary is previous
    wait_for(app, lambda: panel.summary is not previous and not panel.busy)
    assert panel.selected_animal()["id"] == "second"
    assert "更新後的動物" in panel.detail_title.text()
    panel.close()


def test_new_base_failure_cannot_retain_previous_base_success(app):
    def compute(snapshot, base, radius_m):
        if base["id"] == "new":
            raise ValueError("synthetic new-base failure")
        return summary()
    panel = AnimalPanel(compute)
    panel.set_snapshot({"world_id": "one"}, {"id": "old", "name": "舊家", "x": 0, "y": 0})
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    panel.set_snapshot({"world_id": "one"}, {"id": "new", "name": "新家", "x": 100, "y": 100})
    assert panel.summary is None and panel.table.rowCount() == 0
    wait_for(app, lambda: bool(panel.last_error) and not panel.busy)
    assert panel.summary is None and not panel.inventory_tables
    assert "last valid result" not in panel.status.text()
    panel.close()


def test_stale_old_world_job_cannot_overwrite_new_base_or_radius(app):
    started, release = threading.Event(), threading.Event()
    calls = []
    def compute(snapshot, base, radius_m):
        calls.append((snapshot["world_id"], base["name"], radius_m))
        if snapshot["world_id"] == "old":
            started.set()
            release.wait(3)
        return summary(animal(snapshot["world_id"], base["name"], radius_m))
    panel = AnimalPanel(compute)
    panel.set_snapshot({"world_id": "old"}, {"id": "home", "name": "舊家", "x": 0, "y": 0})
    panel.debounce.stop()
    panel._start()
    wait_for(app, started.is_set)
    panel.set_snapshot({"world_id": "new"}, {"id": "new", "name": "新家", "x": 100, "y": 100})
    panel.radius_spin.setValue(200)
    assert panel.summary is None and panel.table.rowCount() == 0
    release.set()
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    assert calls == [("old", "舊家", 100), ("new", "新家", 200)]
    assert panel.selected_animal()["id"] == "new"
    assert panel.table.item(0, 0).text() == "新家"
    panel.close()


@pytest.mark.parametrize("missing_source", [False, True])
def test_clear_and_shared_map_invalidate_inflight_jobs(app, missing_source):
    started, release = threading.Event(), threading.Event()
    def compute(snapshot, base, radius_m):
        started.set()
        release.wait(3)
        return summary()
    panel = AnimalPanel(compute)
    panel.set_snapshot({"world_id": "old"}, None)
    panel.debounce.stop()
    panel._start()
    wait_for(app, started.is_set)
    panel.set_missing_source() if missing_source else panel.clear()
    release.set()
    wait_for(app, lambda: not panel.busy)
    assert panel.summary is None and panel.table.rowCount() == 0
    assert not panel.inventory_tables and panel.stats_table.rowCount() == 0
    assert "save" in panel.status.text().lower() and "0 animals" not in panel.status.text()
    if missing_source:
        assert "Choose host save" in panel.status.text()
    panel.close()


def test_small_window_detail_scroll_reaches_all_equipment(app):
    panel = AnimalPanel()
    entry = animal()
    entry["inventories"][0]["items"] = [{"id": "CopperOre", "name": "銅礦", "quantity": i, "slot": i} for i in range(12)]
    panel.set_summary(summary(entry))
    panel.resize(720, 480)
    panel.show()
    app.processEvents()
    scroll = panel.detail_scroll
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    app.processEvents()
    equipment = panel.inventory_tiles[-1]
    bottom = equipment.mapTo(scroll.viewport(), equipment.rect().bottomLeft())
    assert scroll.viewport().rect().contains(bottom)
    assert panel.table.horizontalScrollBar().maximum() > 0
    panel.close()
