"""Mission routes must stay ordered, refresh correctly, and keep uncertainty visible."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from icarus_helper.mission_map import MissionMapPanel


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    widget = MissionMapPanel()
    widget.resize(650, 650)
    widget.show()
    app.processEvents()
    yield app, widget
    widget.close()
    app.processEvents()


def route(x=0):
    return {"points": [{"id": "one", "label": "領取裝備", "order": 1, "x": x, "y": 100, "z": 0, "status": "active"},
                       {"id": "two", "label": "Later採集點", "order": 2, "x": 50000, "y": 100, "z": 0, "confidence": "candidate"}],
            "pending_stages": [{"label": "離開任務區域", "order": 3, "reason": "位置將隨Mission progress決定"}], "coverage": {}}


def populate(widget, data):
    widget.set_mission(None, {"id": "test-mission"}, {"map": "Prometheus", "calibrated": True}, [], route=data)


def test_route_lists_current_later_and_unresolved_stages(panel):
    app, widget = panel
    populate(widget, route())
    app.processEvents()
    assert len(widget.map.points) == 2
    assert widget.stages.count() == 3
    assert "領取裝備" in widget.stages.item(0).text()
    assert "Candidate" in widget.stages.item(1).text()
    assert widget.stages.item(2).data(Qt.ItemDataRole.UserRole)["pending"]
    assert "Confirmed: 1" in widget.summary.text()


def test_refresh_rebinds_and_follows_selected_target_when_it_moves(panel):
    app, widget = panel
    populate(widget, route())
    widget.stages.setCurrentRow(0)
    assert widget.map.chosen["x"] == 0
    populate(widget, route(x=200000))
    app.processEvents()
    assert widget.map.chosen["x"] == 200000
    center = widget.map.mapToScene(widget.map.viewport().rect().center())
    assert abs(center.x() - 2000) < 5


def test_clear_discards_old_world_points_and_background(panel):
    app, widget = panel
    populate(widget, route())
    widget.clear()
    assert widget.map.points == []
    assert widget.map.terrain.isNull()
    assert widget.stages.count() == 0
    assert widget.map.map_name == ""


def test_unchanged_snapshot_preserves_manual_zoom(panel):
    app, widget = panel
    populate(widget, route())
    widget.map.zoom(1.4)
    scale = widget.map.transform().m11()
    populate(widget, route())
    app.processEvents()
    assert widget.map.transform().m11() == pytest.approx(scale)


def test_route_distance_uses_selected_base_and_refreshes_when_base_changes(panel):
    app, widget = panel
    base = {"id": "home", "name": "我們家", "kind": "base", "x": 300, "y": 500, "z": 9000}
    widget.set_mission(None, {"id": "test-mission"}, {"calibrated": True}, [base], route=route(), active_base=base)
    assert widget.map.points[0]["distance_m"] == pytest.approx(5)
    assert "5 m from 我們家 (horizontal straight-line)" in widget.stages.item(0).text()
    assert "Candidate" in widget.stages.item(1).text()
    moved = dict(base, x=0, y=100)
    widget.set_mission(None, {"id": "test-mission"}, {"calibrated": True}, [moved], route=route(), active_base=moved)
    assert widget.map.points[0]["distance_m"] == 0
    assert widget.stages.item(2).data(Qt.ItemDataRole.UserRole)["pending"]
    widget.set_mission(None, {"id": "test-mission"}, {"calibrated": True}, [], route=route(), active_base=None)
    assert widget.map.points[0]["distance_m"] is None
