"""Output counts remain clickable at real marker coordinates and snapshot-scoped."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper.map_widget import WorldMap, drill_stock_label
from icarus_helper.models import Resource
from icarus_helper.ui import drill_inventory_html


def stock(quantity=314, **changes):
    result = {"status": "ok", "quantity": quantity, "known_quantity": quantity, "drill_id": "drill",
              "saved_at": "2026-11-02T13:08:49+00:00", "source_sha256": "snapshot-a",
              "items": [{"id": "Gold_Ore", "name": "Gold ore", "quantity": quantity,
                         "known_quantity": quantity, "slots": [0, 1]}], "warnings": []}
    result.update(changes)
    return result


def deposit(summary):
    return Resource("deposit", "deep_deposit", "Gold ore", "Gold", 0, 0, 0,
                    drill_status="Installed", raw={"_drill": {"id": "drill", "inventory": summary}})


@pytest.fixture
def map_view():
    app = QApplication.instance() or QApplication([])
    view = WorldMap()
    view.resize(800, 600)
    view.show()
    view._fit_mode = False
    view.resetTransform()
    view.centerOn(0, 0)
    app.processEvents()
    yield app, view
    view.close()
    app.processEvents()


def test_stock_second_line_selects_deposit_and_hides_exact_paired_drill(map_view):
    app, view = map_view
    ore = deposit(stock())
    drill = Resource("drill", "drill", "電動Drill", "Electric_Deep_Mining_Drill", 0, 0, 0,
                     raw={"_drill_inventory": stock()})
    view.set_points([ore, drill])
    view.viewport().repaint()
    app.processEvents()
    selected = []
    view.selected.connect(selected.append)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     pos=view.mapFromScene(0, 0) + QPoint(0, 48))
    assert selected == [ore]
    assert len(view._hit_targets) == 1
    assert (ore.x, ore.y, drill.x, drill.y) == (0, 0, 0, 0)


def test_saved_stock_unknown_and_empty_are_distinct_and_have_details():
    assert drill_stock_label(deposit(stock(0, status="empty", items=[]))) == "Stock: 0 items"
    unknown = deposit(stock(None, status="unavailable", known_quantity=0, items=[]))
    assert drill_stock_label(unknown) == "Stock unknown"
    partial = deposit(stock(None, status="partial", known_quantity=7))
    assert drill_stock_label(partial) == "Known: 7 items + unknown"
    html = drill_inventory_html(deposit(stock()))
    assert "314" in html and "2026-11-02" in html and "Save" in html
    assert "fuel" in html and "Gold ore" in html


def test_changed_snapshot_replaces_stock_and_shared_points_have_no_stock(map_view):
    app, view = map_view
    ore = deposit(stock())
    view.set_points([ore])
    view.viewport().repaint()
    app.processEvents()
    new_ore = deposit(stock(12, source_sha256="snapshot-b"))
    view.set_points([new_ore])
    view.viewport().repaint()
    app.processEvents()
    assert drill_stock_label(view._hit_targets[0]["members"][0]) == "Stock: 12 items"
    shared = Resource("shared", "deep_deposit", "CandidateGold ore", "Gold", 0, 0, 0,
                      confidence="Candidate", drill_status="Cannot confirm")
    view.set_points([shared])
    view.viewport().repaint()
    app.processEvents()
    assert drill_stock_label(view._hit_targets[0]["members"][0]) == ""
    assert drill_inventory_html(shared) == ""


def test_conflicted_link_does_not_show_old_stock():
    ore = deposit(stock())
    ore.drill_status = "Cannot confirm"
    assert drill_stock_label(ore) == ""
    assert drill_inventory_html(ore) == ""
