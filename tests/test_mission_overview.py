"""Actionable operations share one original-position map, without private I/O."""
from copy import deepcopy
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper import mission_overview as overview


def world(**extra):
    return SimpleNamespace(source_path="synthetic-world.json", world_id="world-a", map_name="Prometheus",
                           sha256="synthetic-sha", **extra)


def mission(ident, state="available", kind="Operation", **extra):
    return {"id": ident, "title": ident + "｜測試任務", "status_key": state,
            "map_name": "Prometheus", "kind": kind, **extra}


def point(ident="location", x=0, y=0, confidence="known", **extra):
    return {"id": ident, "stage": ident, "label": "調查地點", "order": 1,
            "x": x, "y": y, "z": 100, "confidence": confidence,
            "status": "candidate" if confidence == "candidate" else "known", **extra}


def routes(monkeypatch, data):
    calls = []
    def locate(snapshot, current):
        calls.append(current["id"])
        return deepcopy(data.get(current["id"], {"points": [], "pending_stages": []}))
    monkeypatch.setattr(overview, "build_mission_locations", locate)
    return calls


def test_model_only_keeps_current_world_actionable_operations(monkeypatch):
    calls = routes(monkeypatch, {ident: {"points": [point(ident)], "pending_stages": []}
                                  for ident in ("A", "B", "done", "simple", "unknown", "other-map", "other-world")})
    missions = [mission("A"), mission("B", "active", "NPC"), mission("done", "completed"),
                mission("simple", kind="SMPL3"), mission("DYN_Something"), mission("unknown", "unknown"),
                mission("other-map", map_name="Olympus"), mission("other-world", world_id="world-b")]
    result = overview.build_operation_overview(world(), missions)
    assert [m["id"] for m in result["missions"]] == ["A", "B"]
    assert calls == ["A", "B"]
    assert {p["mission_id"] for p in result["points"]} == {"A", "B"}
    assert result["coverage"]["mission_count"] == 2


def test_missing_and_nonfinite_locations_remain_selectable_without_fake_points(monkeypatch):
    routes(monkeypatch, {"A": {"points": [point(x=float("nan"))], "pending_stages": []},
                         "B": {"points": [], "pending_stages": [{"stage": "deliver", "label": "回收艙"}]}})
    result = overview.build_operation_overview(world(), [mission("A"), mission("B")])
    assert len(result["missions"]) == 2 and result["points"] == []
    assert len(result["unlocated"]) == 2
    assert result["coverage"]["pending_stage_count"] == 2


def test_candidates_keep_exact_coordinates_and_duplicate_points_do_not_inflate_counts(monkeypatch):
    candidate = point("candidate", x=-180012.5, y=2109.75, confidence="candidate")
    routes(monkeypatch, {"A": {"points": [candidate, deepcopy(candidate), point("done", status="completed")],
                               "pending_stages": [{"stage": "candidate"}, {"stage": "later"}]}})
    result = overview.build_operation_overview(world(), [mission("A"), mission("A")])
    assert len(result["points"]) == 1
    assert (result["points"][0]["x"], result["points"][0]["y"]) == (-180012.5, 2109.75)
    assert result["points"][0]["confidence"] == "candidate"
    assert result["coverage"]["mission_count"] == 1
    assert result["coverage"]["candidate_point_count"] == 1
    assert result["coverage"]["pending_stage_count"] == 1


def test_unlocks_and_current_operation_slot_are_counted_separately(monkeypatch):
    routes(monkeypatch, {"ready": {"points": [point("r")], "pending_stages": []},
                         "waiting": {"points": [point("w1"), point("w2", x=400)], "pending_stages": []},
                         "active": {"points": [point("a")], "pending_stages": []}})
    result = overview.build_operation_overview(world(), [
        mission("ready", can_start_now=True),
        mission("waiting", can_start_now=False, start_block_reason="先完成Current的 CRYSTAL 操作"),
        mission("active", "active", can_start_now=False),
        mission("legacy"),  # Old fixture/caller without the field retains availability.
        mission("unknown-start", can_start_now=None),
        mission("done", "completed", can_start_now=True),
    ])
    coverage = result["coverage"]
    assert coverage["ready_mission_count"] == 2
    assert coverage["waiting_mission_count"] == 2
    assert coverage["active_mission_count"] == 1
    assert coverage["mission_count"] == 5 and coverage["point_count"] == 4
    assert "Ready: 2" in result["summary"]
    assert "Unlocked, waiting: 2" in result["summary"]
    assert "In progress: 1" in result["summary"]
    waiting = next(m for m in result["missions"] if m["id"] == "waiting")
    assert waiting["overview_display_status"] == "Unlocked, waiting"
    assert waiting["overview_availability"] == "waiting"
    assert waiting["start_block_reason"] == "先完成Current的 CRYSTAL 操作"
    assert {p["mission_display_status"] for p in result["points"] if p["mission_id"] == "waiting"} == {"Unlocked, waiting"}


@pytest.mark.parametrize("snapshot", [None, SimpleNamespace(source_path=None),
                                      SimpleNamespace(source_path="world.json", is_shared_map=True)])
def test_no_private_actionable_inference_without_source(monkeypatch, snapshot):
    calls = routes(monkeypatch, {})
    result = overview.build_operation_overview(snapshot, [mission("A")])
    assert not result["missions"] and not result["points"] and not calls
    assert "No valid host save" in result["summary"]


def test_projection_does_not_mutate_any_inputs(monkeypatch):
    raw_routes = {"A": {"points": [point()], "pending_stages": []}}
    routes(monkeypatch, raw_routes)
    snapshot, missions = world(), [mission("A")]
    before = deepcopy((vars(snapshot), missions, raw_routes))
    overview.build_operation_overview(snapshot, missions)
    assert before == (vars(snapshot), missions, raw_routes)


@pytest.mark.parametrize("key", ["world_id", "map_name"])
def test_missing_world_identity_never_builds_actionable_locations(monkeypatch, key):
    calls = routes(monkeypatch, {})
    snapshot = world()
    setattr(snapshot, key, "")
    result = overview.build_operation_overview(snapshot, [mission("A")])
    assert result["points"] == result["missions"] == []
    assert not calls and "unconfirmed" in result["summary"]


@pytest.fixture
def panel(monkeypatch):
    app = QApplication.instance() or QApplication([])
    data = {"A": {"points": [point("a", x=-20000, confidence="candidate")], "pending_stages": []},
            "B": {"points": [point("b", x=20000)], "pending_stages": []},
            "C": {"points": [], "pending_stages": [{"stage": "later", "label": "地點待定"}]}}
    routes(monkeypatch, data)
    widget = overview.OperationOverviewPanel()
    widget.resize(850, 700)
    widget.show()
    widget.set_missions(world(), [mission("A"), mission("B", kind="NPC"), mission("C")],
                        {"map": "Prometheus", "calibrated": True}, [])
    app.processEvents()
    yield app, widget, data
    widget.close()
    app.processEvents()


def test_all_missions_remain_on_map_when_one_is_selected(panel):
    app, widget, _data = panel
    emitted = []
    widget.missionSelected.connect(emitted.append)
    ids = [p["id"] for p in widget.map.points]
    assert widget.select_mission("A", focus=True)
    app.processEvents()
    assert widget.map.selected_mission_id == "A"
    assert [p["id"] for p in widget.map.points] == ids
    assert emitted == []
    assert widget.listing.count() == 3
    assert "Unlocated: 1" in widget.summary.text()


def test_real_qt_marker_click_emits_exact_mission_and_keeps_all_points(panel):
    app, widget, _data = panel
    emitted = []
    widget.missionSelected.connect(emitted.append)
    p = next(p for p in widget.map.points if p["mission_id"] == "B")
    pos = widget.map.mapFromScene(QPointF(p["x"] / 100, p["y"] / 100))
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert emitted == ["B"]
    assert widget.selected_mission_id == "B"
    assert {p["mission_id"] for p in widget.map.points} == {"A", "B"}


def test_waiting_operation_remains_visible_and_clickable_without_claiming_ready(panel):
    app, widget, _data = panel
    missions = [mission("A", can_start_now=False, start_block_reason="CRYSTAL 仍占用操作欄位"),
                mission("B", can_start_now=True), mission("C", "active", can_start_now=False)]
    widget.set_missions(world(), missions, {"map": "Prometheus"}, [])
    app.processEvents()
    assert "Ready: 1" in widget.summary.text()
    assert "Unlocked, waiting: 1" in widget.summary.text()
    assert "In progress: 1" in widget.summary.text()
    assert "Unlocked, waiting" in widget.listing.item(0).text()
    assert "Ready to accept" not in widget.listing.item(0).text()
    assert "CRYSTAL 仍占用操作欄位" in widget.listing.item(0).toolTip()
    emitted = []
    widget.missionSelected.connect(emitted.append)
    p = next(p for p in widget.map.points if p["mission_id"] == "A")
    pos = widget.map.mapFromScene(QPointF(p["x"] / 100, p["y"] / 100))
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert emitted == ["A"] and widget.selected_mission_id == "A"
    assert {p["mission_id"] for p in widget.map.points} == {"A", "B"}
    # A new snapshot can free the slot while retaining the selected operation
    # and every map location; the projection must use the new readiness value.
    missions[0] = mission("A", can_start_now=True)
    widget.set_missions(world(), missions, {"map": "Prometheus"}, [])
    app.processEvents()
    assert "Ready: 2" in widget.summary.text()
    assert "Unlocked, waiting: 0" in widget.summary.text()
    assert "Ready to accept" in widget.listing.item(0).text()
    assert widget.selected_mission_id == "A"
    assert emitted == ["A"]
    assert {p["mission_id"] for p in widget.map.points} == {"A", "B"}


def test_unlocated_mission_list_click_still_opens_detail(panel):
    app, widget, _data = panel
    emitted = []
    widget.missionSelected.connect(emitted.append)
    widget.listing.setCurrentRow(2)
    app.processEvents()
    assert emitted == ["C"]
    assert widget.selected_mission_id == "C"
    assert widget.map.chosen is None


def test_refresh_preserves_zoom_and_selection_but_world_change_resets(panel):
    app, widget, _data = panel
    widget.select_mission("A")
    widget.map.zoom(1.5)
    scale = widget.map.transform().m11()
    widget.set_missions(world(), [mission("A"), mission("B")], {"map": "Prometheus"}, [])
    app.processEvents()
    assert widget.selected_mission_id == "A"
    assert widget.map.transform().m11() == pytest.approx(scale)
    changed = world()
    changed.world_id = "world-b"
    widget.set_missions(changed, [mission("B")], {"map": "Prometheus"}, [])
    app.processEvents()
    assert widget.selected_mission_id is None
    assert {p["mission_id"] for p in widget.map.points} == {"B"}


def test_shared_map_clears_old_selection_points_bases_and_terrain(panel):
    _app, widget, _data = panel
    widget.select_mission("A")
    shared = world(is_shared_map=True)
    widget.set_missions(shared, [mission("A")], {"map": "Prometheus"}, [])
    assert widget.map.points == [] and widget.map.all_bases == []
    assert widget.map.terrain.isNull()
    assert widget.listing.count() == 0
    assert widget.selected_mission_id is None


def test_overlapping_original_positions_choose_exact_mission_without_offsets(panel, monkeypatch):
    app, widget, data = panel
    data["B"]["points"] = [point("b", x=-20000)]
    widget.set_missions(world(), [mission("A"), mission("B")], {"map": "Prometheus"}, [])
    app.processEvents()
    assert len({(p["x"], p["y"], p["z"]) for p in widget.map.points}) == 1
    emitted, choices = [], []
    widget.missionSelected.connect(emitted.append)
    def choose(members, _position):
        choices.append([p["mission_id"] for p in members])
        return next(p for p in members if p["mission_id"] == "B")
    monkeypatch.setattr(widget.map, "choose_overlap", choose)
    p = widget.map.points[0]
    pos = widget.map.mapFromScene(QPointF(p["x"] / 100, p["y"] / 100))
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert choices == [["A", "B"]]
    assert emitted == ["B"]


def test_base_at_original_position_keeps_click_priority(panel):
    app, widget, _data = panel
    base = {"id": "home", "kind": "base", "name": "我們家", "x": -20000, "y": 0, "z": 100,
            "world_id": "world-a"}
    widget.set_missions(world(), [mission("A"), mission("B")], {"map": "Prometheus"}, [base], active_base=base)
    app.processEvents()
    emitted = []
    widget.missionSelected.connect(emitted.append)
    pos = widget.map.mapFromScene(QPointF(-200, 0))
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert emitted == []
    assert widget.map.chosen["id"] == "home"


def test_foreign_base_is_not_displayed_or_used_for_home(panel):
    _app, widget, _data = panel
    base = {"id": "home", "x": 0, "y": 0, "world_id": "other"}
    widget.set_missions(world(), [mission("A")], {"map": "Prometheus"}, [base], active_base=base)
    assert widget.active_base is None
    assert widget.map.all_bases == []


def test_external_ui_can_hide_duplicate_list_without_affecting_selection(panel):
    app, widget, _data = panel
    widget.listing.hide()
    widget.select_mission("B")
    app.processEvents()
    assert widget.map.selected_mission_id == "B"
    assert len(widget.map.points) == 2


def test_unknown_selection_clears_only_selection_not_other_missions(panel):
    _app, widget, _data = panel
    widget.select_mission("A")
    assert widget.select_mission("not-listed") is False
    assert widget.selected_mission_id is None
    assert widget.listing.currentItem() is None
    assert len(widget.map.points) == 2


def test_right_click_preserves_base_management_position_signal(panel):
    app, widget, _data = panel
    positions, missions = [], []
    widget.map.positionPicked.connect(lambda x, y: positions.append((x, y)))
    widget.missionSelected.connect(missions.append)
    target = widget.map.mapFromScene(QPointF(200, 0))
    expected = widget.map.mapToScene(target)
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.RightButton, pos=target)
    app.processEvents()
    assert positions[0] == pytest.approx((expected.x() * 100, expected.y() * 100))
    assert missions == []


@pytest.mark.parametrize("change", ["world", "snapshot", "clear"])
def test_overlap_menu_cannot_apply_old_selection_after_projection_changes(panel, monkeypatch, change):
    app, widget, data = panel
    data["B"]["points"] = [point("b", x=-20000)]
    widget.set_missions(world(), [mission("A"), mission("B")], {"map": "Prometheus"}, [])
    app.processEvents()
    emitted = []
    widget.missionSelected.connect(emitted.append)
    def choose(members, _position):
        old_choice = next(p for p in members if p["mission_id"] == "B")
        if change == "clear":
            widget.clear()
        else:
            snapshot = world()
            if change == "world":
                snapshot.world_id = "world-b"
            else:
                snapshot.sha256 = "a-new-valid-snapshot"
            widget.set_missions(snapshot, [mission("A")], {"map": "Prometheus"}, [])
        return old_choice
    monkeypatch.setattr(widget.map, "choose_overlap", choose)
    pos = widget.map.mapFromScene(QPointF(-200, 0))
    QTest.mouseClick(widget.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert emitted == []
    assert widget.map.selected_mission_id is None
    assert widget.map.chosen is None
    assert all(p["mission_id"] != "B" for p in widget.map.points)
