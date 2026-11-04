"""Hit-testing must follow the visible fixed-pixel icon and its text label."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper.map_widget import WorldMap
from icarus_helper.models import Resource


@pytest.fixture
def visible_map():
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


def point(ident, x=0, y=0, kind="deep_deposit", mineral="Gold"):
    return Resource(ident, kind, "Gold ore", mineral, x, y, 0)


def test_large_icon_outer_edge_selects_the_displayed_resource(visible_map):
    app, view = visible_map
    ore = point("gold")
    view.set_points([ore])
    view.viewport().repaint()
    app.processEvents()
    selected = []
    view.selected.connect(selected.append)
    center = view.mapFromScene(0, 0)
    # This visible edge lies beyond the previous 12-pixel circular hit radius.
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center + QPoint(15, 0))
    assert selected == [ore]


def test_mineral_name_label_is_clickable_and_maps_to_its_ore(visible_map):
    app, view = visible_map
    ore = point("gold")
    view.set_points([ore])
    view.viewport().repaint()
    app.processEvents()
    selected = []
    view.selected.connect(selected.append)
    center = view.mapFromScene(0, 0)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center + QPoint(0, 29))
    assert selected == [ore]


def test_overlapping_icons_stay_at_exact_locations_and_select_topmost(visible_map):
    app, view = visible_map
    ores = [point(f"ore-{i}", i * 10, i * 10) for i in range(4)]
    view.set_points(ores)
    view.viewport().repaint()
    app.processEvents()
    selected = []
    view.selected.connect(selected.append)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(0, 0))
    assert selected == [ores[-1]]
    assert {p.id for p in view.points} == {p.id for p in ores}


def test_base_stays_at_real_coordinates_above_overlapping_icons(visible_map):
    app, view = visible_map
    view.set_points([point(f"ore-{i}", i * 10, i * 10) for i in range(12)])
    base = {"id": "our-home", "kind": "base", "name": "Home", "x": 0, "y": 0, "z": 0}
    view.set_bases([base], base)
    view.fit_map()
    view.viewport().repaint()
    app.processEvents()
    selected = []
    view.selected.connect(selected.append)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(0, 0))
    assert selected == [base]


def test_drilled_deposit_has_red_ring_and_remains_selectable(visible_map):
    app, view = visible_map
    ore = point("installed")
    ore.drill_status = "Installed"
    view.set_points([ore])
    view.viewport().repaint()
    app.processEvents()
    center = view.mapFromScene(0, 0)
    pixel = view.viewport().grab().toImage().pixelColor(center + QPoint(22, 0))
    assert pixel.red() > 230 and pixel.green() < 130 and pixel.blue() < 150
    selected = []
    view.selected.connect(selected.append)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center + QPoint(22, 0))
    assert selected == [ore]


def test_drilled_deposit_draws_above_nearby_unplaced_deposit(visible_map):
    app, view = visible_map
    installed, unplaced = point("installed"), point("unplaced")
    installed.drill_status = "Installed"
    view.set_points([installed, unplaced])
    view.viewport().repaint()
    app.processEvents()
    target = view.hit_target(view.mapFromScene(0, 0))
    assert target["members"] == [installed]


def test_world_edge_clips_icon_and_hit_area_without_moving_coordinates(visible_map):
    app, view = visible_map
    view.set_catalog({"bounds": [-10000, -10000, 10000, 10000]})
    view.viewport().repaint()
    app.processEvents()
    outside = view.mapFromScene(107, 0)
    original_pixel = view.viewport().grab().toImage().pixelColor(outside)
    ore = point("near-edge", x=9900)
    ore.drill_status = "Installed"
    view.set_points([ore])
    view.viewport().repaint()
    app.processEvents()
    assert view.viewport().grab().toImage().pixelColor(outside) == original_pixel
    assert view.hit_target(outside) is None
    assert view.hit_target(view.mapFromScene(99, 0))["members"] == [ore]
    assert ore.x == 9900 and ore.y == 0
