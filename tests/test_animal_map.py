"""Animal map coordinates, snapshot isolation, selection and honest unknowns."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper.animal_map import AnimalMapPanel, owner_color


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    widget = AnimalMapPanel()
    widget.resize(780, 650)
    widget.show()
    app.processEvents()
    yield app, widget
    widget.close()
    app.processEvents()


def animal(ident="one", position=None, owner="Player甲"):
    return {"id": ident, "name": "阿牛" if ident == "one" else "阿羊", "species_id": "Buffalo",
            "species": "水牛", "owner_name": owner, "position": position,
            "distance_m": 5}


def snapshot(world="one", shared=False):
    return SimpleNamespace(world_id=world, map_name="Prometheus", is_shared_map=shared,
                           saved_at=datetime(2026, 11, 2, 5, 6, 7, tzinfo=timezone.utc))


CATALOG = {"map": "Prometheus", "calibrated": True,
           "bounds": {"min_x": -403200, "min_y": -403200,
                      "max_x": 403200, "max_y": 403200}}


def test_original_xyz_missing_coordinates_and_shared_privacy(panel):
    app, widget = panel
    source = {"animals": [animal(position=[12000, -44000, 900]), animal("two"),
                           animal("bad", [float("nan"), 30, 10])]}
    before = deepcopy(source)
    widget.set_context(snapshot(), source, catalog=CATALOG)
    app.processEvents()
    assert len(widget.map.points) == 1
    point = widget.map.points[0]
    assert (point["x"], point["y"], point["z"]) == (12000, -44000, 900)
    assert source["animals"][0] == before["animals"][0]
    assert "Unknown positions: 2" in widget.status.text()
    assert "save" in widget.status.text().lower()
    assert "not continuously" in widget.status.toolTip()
    widget.set_context(snapshot(shared=True), source, catalog=CATALOG)
    assert widget.animals == [] and widget.map.points == []
    assert widget.map.chosen is None and widget.map.terrain.isNull()
    assert "shared" in widget.status.text()


def test_card_selection_focus_click_and_overlap_keep_true_centers(panel):
    app, widget = panel
    source = {"animals": [animal(position=[10000, 20000, 80]),
                           animal("two", [10000, 20000, 80], owner="Player乙")]}
    widget.set_context(snapshot(), source, catalog=CATALOG)
    assert widget.select_animal("one")
    events = []
    widget.animal_selected.connect(events.append)
    app.processEvents()
    pos = widget.map.mapFromScene(QPointF(100, 200))
    assert widget.map.hit_target(QPointF(pos))["members"][0]["id"] == "one"
    for target in widget.map._hit_targets:
        assert target["scene_position"] == QPointF(100, 200)
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert events == ["one"]
    assert widget.map.chosen["id"] == "one"


def test_refresh_tracks_selected_animal_but_preserves_unmoved_manual_zoom(panel):
    app, widget = panel
    source = {"animals": [animal(position=[0, 0, 80])]}
    widget.set_context(snapshot(), source, catalog=CATALOG)
    widget.select_animal("one")
    widget.map.zoom(1.4)
    scale = widget.map.transform().m11()
    widget.set_context(snapshot(), source, catalog=CATALOG)
    app.processEvents()
    assert widget.map.transform().m11() == pytest.approx(scale)
    moved = {"animals": [animal(position=[90000, 130000, 600])]}
    widget.set_context(snapshot(), moved, catalog=CATALOG)
    app.processEvents()
    assert widget.map.chosen["x"] == 90000
    center = widget.map.mapToScene(widget.map.viewport().rect().center())
    assert abs(center.x() - 900) < 5 and abs(center.y() - 1300) < 5


def test_world_change_and_filter_remove_old_targets_and_base(panel):
    app, widget = panel
    home = {"id": "home", "name": "我們家", "world_id": "one", "x": 0, "y": 0, "z": 100}
    source = {"animals": [animal(position=[0, 0, 80]), animal("two")]}
    widget.set_context(snapshot(), source, home, CATALOG)
    widget.select_animal("one")
    widget.set_animals([source["animals"][1]])
    assert widget.map.points == []
    assert not widget.select_animal("two")
    assert "Position unknown" in widget.position_note.text()
    assert widget.position_note.toolTip() == ""
    widget.set_context(snapshot("different"), {"animals": []}, home, CATALOG)
    assert widget.map.chosen is None and widget.map.base is None
    assert widget.map.all_bases == []
    assert widget.position_note.text() == ""
    widget.set_context(snapshot(), source, dict(home, map_name="Olympus"), CATALOG)
    assert widget.map.base is None and widget.map.all_bases == []
    widget.clear()
    assert widget.world_id is None and widget.map.map_name == ""


def test_owner_color_is_stable_and_unknown_is_neutral():
    assert owner_color("Player甲") == owner_color("Player甲")
    assert owner_color("") == owner_color("Unknown") == owner_color("unknown") == "#99a9b7"


def test_map_owner_border_uses_same_private_group_key_as_cards(panel, monkeypatch):
    from icarus_helper import animal_map
    app, widget = panel
    source = animal(position=[0, 0, 10], owner="同名Owner")
    source["owner_key"] = "owner:unique-private-group"
    seen = []
    original_color = owner_color
    monkeypatch.setattr(animal_map, "owner_color", lambda owner: seen.append(owner) or original_color(owner))
    widget.set_context(snapshot(), {"animals": [source]}, catalog=CATALOG)
    app.processEvents()
    widget.map.viewport().grab()
    assert seen and set(seen) == {source["owner_key"]}
