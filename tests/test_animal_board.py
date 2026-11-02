"""Owner columns and snapshot maps stay in sync under real Qt interactions."""
from copy import deepcopy
from datetime import datetime, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from icarus_helper.animals_view import AnimalPanel
from test_animals_view import animal, summary, wait_for


CATALOG = {"map": "Prometheus", "calibrated": True,
           "bounds": {"min_x": -403200, "min_y": -403200,
                      "max_x": 403200, "max_y": 403200}}


def entry(ident, owner_key="owner:first", name=None, owner="同名飼養者", distance=20,
          position=(10000, 20000, 300)):
    result = animal(ident, name or ident, distance, owner)
    result.update(owner_key=owner_key, position=list(position) if position is not None else None)
    return result


def snapshot(*entries, world="synthetic-first", revision=1):
    return {"world_id": world, "map_name": "Prometheus", "revision": revision,
            "saved_at": datetime(2026, 11, 2, 5, 6, revision, tzinfo=timezone.utc),
            "animals": list(entries)}


@pytest.fixture
def board():
    app = QApplication.instance() or QApplication([])
    panel = AnimalPanel(lambda source, base, radius_m: summary(*source["animals"]))
    panel.resize(1300, 850)
    panel.show()
    app.processEvents()
    yield app, panel
    panel.close()
    app.processEvents()


def load(app, panel, source):
    previous = panel.summary
    panel.set_snapshot(source, None, catalog=CATALOG)
    wait_for(app, lambda: panel.summary is not None and panel.summary is not previous and not panel.busy)


def card_ids(panel):
    return set(panel.animal_cards)


def point_ids(panel):
    return {point["id"] for point in panel.animal_map.map.points}


def test_namesake_owners_have_separate_columns_and_shared_character_groups_together(board):
    app, panel = board
    source = snapshot(entry("a"), entry("b"), entry("c", owner_key="owner:second"),
                      entry("d", owner_key="unknown", owner="未知"))
    before = deepcopy(source)
    load(app, panel, source)
    assert set(panel.owner_columns) == {"owner:first", "owner:second", "unknown"}
    assert len(panel.owner_columns) == 3
    assert panel.owner_columns["owner:first"].accessibleName() == "飼養者 同名飼養者"
    assert panel.owner_columns["owner:second"].accessibleName() == "飼養者 同名飼養者"
    assert panel.animal_cards["a"].parentWidget() is panel.animal_cards["b"].parentWidget()
    assert panel.animal_cards["a"].parentWidget() is not panel.animal_cards["c"].parentWidget()
    assert card_ids(panel) == point_ids(panel) == {"a", "b", "c", "d"}
    assert source == before


def test_scope_and_search_apply_to_cards_map_and_unknown_position_consistently(board):
    app, panel = board
    load(app, panel, snapshot(entry("home", distance=20),
                             entry("away", owner_key="owner:second", owner="另一位", distance=250,
                                   position=(80000, -90000, 900)),
                             entry("missing", distance=None, position=None)))
    assert card_ids(panel) == {"home", "away", "missing"}
    assert point_ids(panel) == {"home", "away"}
    for scope, expected in (("home", {"home"}), ("away", {"away"}), ("unknown", {"missing"})):
        panel.scope_combo.setCurrentIndex(panel.scope_combo.findData(scope))
        assert card_ids(panel) == expected
        assert {str(row["id"]) for row in panel._row_animals} == expected
        assert point_ids(panel) == expected - {"missing"}
    assert panel.animal_map.map.chosen is None
    assert "位置未知" in panel.animal_map.position_note.text()
    panel.scope_combo.setCurrentIndex(panel.scope_combo.findData("all"))
    panel.search_edit.setText("另一位")
    assert card_ids(panel) == point_ids(panel) == {"away"}
    assert set(panel.owner_columns) == {"owner:second"}
    panel.search_edit.setText("no matching animal")
    assert not panel.animal_cards and not panel.owner_columns
    assert not panel.animal_map.map.points and panel.animal_map.map.chosen is None
    assert not panel.inventory_tables


def test_world_switch_immediately_clears_old_cards_map_selection_and_detail(board):
    app, panel = board
    load(app, panel, snapshot(entry("old")))
    assert panel.animal_map.map.chosen["id"] == "old"
    panel.set_snapshot(snapshot(entry("new", owner_key="owner:new"), world="synthetic-new"),
                       None, catalog=CATALOG)
    assert panel.summary is None and panel.selected_animal() is None
    assert not panel.animal_cards and not panel.owner_columns
    assert not panel.animal_map.map.points and panel.animal_map.map.chosen is None
    assert not panel.inventory_tables and "old" not in panel.detail_title.text()
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    assert card_ids(panel) == point_ids(panel) == {"new"}
    assert set(panel.owner_columns) == {"owner:new"}
    assert panel.animal_map.world_id == "synthetic-new"
    assert panel.animal_map.map.chosen["id"] == "new"


@pytest.mark.parametrize("missing_source", [False, True])
def test_clear_removes_private_columns_positions_and_selection(board, missing_source):
    app, panel = board
    load(app, panel, snapshot(entry("old")))
    panel.set_missing_source() if missing_source else panel.clear()
    app.processEvents()
    assert panel.summary is None and panel.snapshot is None
    assert not panel.animal_cards and not panel.owner_columns
    assert not panel.animal_map.animals and not panel.animal_map.map.points
    assert panel.animal_map.map.chosen is None and panel.animal_map.world_id is None
    assert panel.animal_map.position_note.text() == ""
    assert not panel.inventory_tables and panel.stats_table.rowCount() == 0


def test_mouse_selects_map_animal_card_and_card_locate_centers_its_true_position(board):
    app, panel = board
    load(app, panel, snapshot(entry("first", position=(-40000, -30000, 700)),
                             entry("second", owner_key="owner:second", position=(60000, 80000, 900))))
    target_card = panel.animal_cards["second"]
    QTest.mouseClick(target_card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert panel.selected_animal()["id"] == "second"
    assert panel.animal_map.map.chosen["id"] == "second"
    panel.animal_map.map.fit_animals()
    app.processEvents()
    position = panel.animal_map.map.mapFromScene(QPointF(-400, -300))
    QTest.mouseClick(panel.animal_map.map.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert panel.selected_animal()["id"] == "first"
    assert panel._selected_id == "first" and panel.animal_map.map.chosen["id"] == "first"
    assert "first" in panel.detail_title.text()
    locate = next(button for button in target_card.findChildren(QPushButton)
                  if button.accessibleName().startswith("地圖定位"))
    QTest.mouseClick(locate, Qt.MouseButton.LeftButton)
    assert panel.selected_animal()["id"] == "second"
    assert panel.animal_map.map.chosen["id"] == "second"
    center = panel.animal_map.map.mapToScene(panel.animal_map.map.viewport().rect().center())
    assert abs(center.x() - 600) < 5 and abs(center.y() - 800) < 5
    assert panel.animal_map.map.chosen["z"] == 900


def test_refresh_keeps_selected_identity_and_updates_card_and_map_from_same_snapshot(board):
    app, panel = board
    first, second = entry("first"), entry("second", owner_key="owner:second", position=(60000, 80000, 900))
    load(app, panel, snapshot(first, second))
    QTest.mouseClick(panel.animal_cards["second"], Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    revised = entry("second", owner_key="owner:second", name="updated animal", position=(100000, -70000, 1900))
    revised["stats"][0]["value"] = 77
    load(app, panel, snapshot(revised, first, revision=2))
    assert panel.selected_animal()["id"] == panel._selected_id == "second"
    assert panel.animal_map.map.chosen["id"] == "second"
    assert panel.animal_cards["second"].animal["name"] == "updated animal"
    assert panel.animal_cards["second"].gauges["health"].value == 77
    assert (panel.animal_map.map.chosen["x"], panel.animal_map.map.chosen["y"], panel.animal_map.map.chosen["z"]) == (100000, -70000, 1900)
    assert panel.animal_map.snapshot is panel.snapshot
    assert "updated animal" in panel.detail_title.text()
