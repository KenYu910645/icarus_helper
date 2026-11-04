"""Actionable mission UI uses isolated settings and invented world/route evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper import mission_clusters, mission_locations, mission_overview, missions
from icarus_helper.models import WorldSnapshot
from icarus_helper.settings import Settings
from icarus_helper.ui import MainWindow


def mission(ident, title, status="available"):
    return {
        "id": ident, "title": title, "summary": title + "測試內容",
        "map_name": "Prometheus", "kind": "Operation", "status_key": status,
        "status": {"available": "Available", "active": "In progress", "unknown": "Unconfirmed",
                   "locked": "Locked", "completed": "Completed"}[status],
        "guide": ["依CurrentStage完成測試目標"], "checklist": [],
        "recommendation_score": 500, "risk_rank": 1,
    }


def point(ident, x, stage="survey", confidence="candidate"):
    return {"id": ident, "label": "搜尋區域", "stage": stage, "order": 1,
            "x": x, "y": 1000, "z": 500, "confidence": confidence,
            "status": "candidate" if confidence == "candidate" else "known"}


def synthetic_snapshot(tmp_path, world="world-a"):
    now = datetime(2026, 11, 3, 1, tzinfo=timezone.utc)
    return WorldSnapshot(tmp_path / (world + ".json"), world, "合成任務世界",
                         "Prometheus", now, now, "synthetic-" + world)


@pytest.fixture
def planner(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "populate_worlds", lambda *_: None)
    data = [mission("PRO_A_Test", "近處採集"),
            mission("PRO_B_Test", "調查哨站", "active"),
            mission("PRO_C_Test", "冰原救援"),
            mission("PRO_U_Test", "狀態未明", "unknown"),
            mission("PRO_L_Test", "仍未解鎖", "locked"),
            mission("PRO_D_Test", "Completed任務", "completed")]
    routes = {
        "PRO_A_Test": {"points": [point("a:survey", 0)],
                       "pending_stages": [{"id": "a:deliver", "stage": "deliver", "order": 2,
                                           "label": "交付補給", "reason": "交付位置待生成"}]},
        "PRO_B_Test": {"points": [point("b:survey", 50000),
                                  dict(point("b:later", 400500, "later"), order=2)],
                       "pending_stages": []},
        "PRO_C_Test": {"points": [point("c:survey", 400000)], "pending_stages": []},
        "PRO_U_Test": {"points": [point("u:survey", 50)], "pending_stages": []},
        "PRO_L_Test": {"points": [point("l:survey", 25000)], "pending_stages": []},
        "PRO_D_Test": {"points": [point("d:survey", 75000)], "pending_stages": []},
    }
    monkeypatch.setattr(missions, "build_missions", lambda *_args, **_kw: deepcopy(data))
    def locate(_snapshot, current):
        return deepcopy(routes.get(current["id"], {"points": [], "pending_stages": []}))
    monkeypatch.setattr(mission_locations, "build_mission_locations", locate)
    monkeypatch.setattr(mission_clusters, "build_mission_locations", locate)
    monkeypatch.setattr(mission_overview, "build_mission_locations", locate)
    widget = MainWindow(settings=Settings(tmp_path / "settings.json"))
    widget.controller.poll.stop()
    widget.snapshot = synthetic_snapshot(tmp_path)
    home = {"id": "home", "name": "測試基地", "kind": "base", "x": 0, "y": 1000, "z": 500}
    config = widget.settings.world(widget.snapshot.world_id)
    config.update(bases=[home], active_base="home")
    widget.catalog = {"map": "Prometheus", "map_key": "prometheus", "calibrated": True}
    widget.rebuild_missions()
    widget.show()
    widget.tabs.setCurrentIndex(1)
    app.processEvents()
    yield app, widget, data, routes
    widget.close()
    app.processEvents()
    assert widget.controller.closed
    assert widget.animal_panel._closed


def listed_ids(listing):
    return [listing.item(index).data(Qt.ItemDataRole.UserRole)["id"]
            for index in range(listing.count())]


def cluster_item(widget, member_ids):
    return next(widget.mission_cluster_list.item(index)
                for index in range(widget.mission_cluster_list.count())
                if set(widget.mission_cluster_list.item(index).data(Qt.ItemDataRole.UserRole)["mission_ids"])
                == set(member_ids))


def test_default_list_contains_every_actionable_operation_and_npc_without_recommendation_limit(planner):
    _app, widget, data, _routes = planner
    data.extend(mission(f"PRO_Many_{index}", f"可接任務{index}") for index in range(18))
    data.append(dict(mission("PRO_NPC_Test", "商人補給"), kind="NPC"))
    data.append(dict(mission("PRO_Simple_Test", "隨機委派"), kind="SMPL3"))
    widget.rebuild_missions()
    expected = {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test", "PRO_NPC_Test"}
    expected.update(f"PRO_Many_{index}" for index in range(18))
    assert set(listed_ids(widget.mission_list)) == expected
    assert len(expected) == 22
    assert {m["id"] for m in widget.operation_overview.overview["missions"]} == expected
    assert widget.mission_state_combo.currentData() == "actionable"
    assert "Unlocked missions (22 / 22)" == widget.mission_left_tabs.tabText(0)
    assert "Nearby groups" in widget.mission_left_tabs.tabText(1)
    npc = next(widget.mission_list.item(i) for i in range(widget.mission_list.count())
               if widget.mission_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] == "PRO_NPC_Test")
    assert "NPC mission" in npc.text()
    assert all("PRO_U_Test" not in group["mission_ids"] for group in widget.mission_clusters["groups"])


def test_selecting_candidate_group_maps_only_its_members_and_preserves_stage_uncertainty(planner):
    app, widget, _data, routes = planner
    before = deepcopy(routes)
    overview_points = deepcopy(widget.operation_overview.map.points)
    item = cluster_item(widget, {"PRO_A_Test", "PRO_B_Test"})
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_cluster_list.setCurrentItem(item)
    app.processEvents()
    group = item.data(Qt.ItemDataRole.UserRole)
    assert {p["id"] for p in widget.mission_route.map.points} == {p["id"] for p in group["points"]}
    assert {p["mission_id"] for p in widget.mission_route.map.points} == {"PRO_A_Test", "PRO_B_Test"}
    assert all(p["confidence"] == "candidate" for p in widget.mission_route.map.points)
    assert "Candidate" in widget.mission_detail.toPlainText()
    assert "近處採集" in widget.mission_detail.toPlainText()
    assert "調查哨站" in widget.mission_detail.toPlainText()
    assert "冰原救援" not in widget.mission_detail.toPlainText()
    assert any(m["pending_stage_count"] for m in group["missions"])
    assert any(m["other_stage_count"] for m in group["missions"])
    assert widget.operation_overview.map.points == overview_points
    assert routes == before


def test_search_filters_lists_and_stage_map_but_keeps_every_actionable_overview_location(planner):
    app, widget, _data, _routes = planner
    overview_points = deepcopy(widget.operation_overview.map.points)
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_search.setText("近處採集")
    app.processEvents()
    assert listed_ids(widget.mission_list) == ["PRO_A_Test"]
    assert widget.mission_cluster_list.count() == 1
    assert set(widget.mission_cluster_list.item(0).data(Qt.ItemDataRole.UserRole)["mission_ids"]) == {"PRO_A_Test", "PRO_B_Test"}
    assert widget.operation_overview.map.points == overview_points
    widget.mission_search.setText("完全不存在的關鍵詞")
    app.processEvents()
    assert widget.mission_list.count() == widget.mission_cluster_list.count() == 0
    assert widget.mission_route.map.points == []
    assert widget.operation_overview.map.points == overview_points
    widget.mission_search.clear()
    app.processEvents()
    assert set(listed_ids(widget.mission_list)) == {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test"}
    assert widget.mission_cluster_list.count() == 2
    assert widget.operation_overview.map.points == overview_points


def test_cluster_mission_link_opens_exact_guide_even_with_uppercase_identifier(planner):
    app, widget, _data, _routes = planner
    overview_points = deepcopy(widget.operation_overview.map.points)
    item = cluster_item(widget, {"PRO_A_Test", "PRO_B_Test"})
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_cluster_list.setCurrentItem(item)
    app.processEvents()
    links = re.findall(r'href="([^"]+)"', widget.mission_detail.toHtml())
    target = next(link for link in links if link.startswith("mission:") and "pro_b_test" in link.casefold())
    widget.mission_link(QUrl(target))
    app.processEvents()
    assert widget.mission_left_tabs.currentIndex() == 0
    assert widget.mission_list.currentItem().data(Qt.ItemDataRole.UserRole)["id"] == "PRO_B_Test"
    assert widget.mission_route.mission_id == "PRO_B_Test"
    assert "調查哨站" in widget.mission_detail.toPlainText()
    assert {p["id"] for p in widget.mission_route.map.points} == {"b:survey", "b:later"}
    assert widget.operation_overview.selected_mission_id == "PRO_B_Test"
    assert widget.operation_overview.map.points == overview_points


def test_refresh_preserves_selected_group_and_manual_map_zoom(planner):
    app, widget, _data, _routes = planner
    item = cluster_item(widget, {"PRO_B_Test", "PRO_C_Test"})
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_cluster_list.setCurrentItem(item)
    app.processEvents()
    selected = item.data(Qt.ItemDataRole.UserRole)["id"]
    point_ids = {p["id"] for p in widget.mission_route.map.points}
    widget.mission_route.map.zoom(1.4)
    scale = widget.mission_route.map.transform().m11()
    widget.operation_overview.map.zoom(1.3)
    overview_scale = widget.operation_overview.map.transform().m11()
    overview_points = deepcopy(widget.operation_overview.map.points)
    widget.rebuild_missions()
    app.processEvents()
    assert widget.mission_left_tabs.currentIndex() == 1
    assert widget.mission_cluster_list.currentItem().data(Qt.ItemDataRole.UserRole)["id"] == selected
    assert {p["id"] for p in widget.mission_route.map.points} == point_ids
    assert widget.mission_route.map.transform().m11() == pytest.approx(scale)
    assert widget.operation_overview.map.transform().m11() == pytest.approx(overview_scale)
    assert widget.operation_overview.map.points == overview_points


def test_select_world_discards_previous_group_selection_and_locations(planner, monkeypatch, tmp_path):
    app, widget, _data, _routes = planner
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_cluster_list.setCurrentItem(cluster_item(widget, {"PRO_A_Test", "PRO_B_Test"}))
    app.processEvents()
    assert widget.mission_route.map.points
    assert widget.operation_overview.map.points
    sources = []
    monkeypatch.setattr(widget.controller, "set_path", lambda path: sources.append(path))
    widget.world_combo.blockSignals(True)
    source = str(tmp_path / "other-world.json")
    widget.world_combo.addItem("另一個合成世界", source)
    widget.world_combo.blockSignals(False)
    widget.select_world(0)
    assert sources == [source]
    assert widget.snapshot is None
    assert widget.mission_list.count() == widget.mission_cluster_list.count() == 0
    assert not widget.mission_clusters.get("groups")
    assert widget.mission_route.map.points == []
    assert widget.mission_route.mission_id is None
    assert widget.operation_overview.world_id is None
    assert widget.operation_overview.selected_mission_id is None
    assert widget.operation_overview.map.points == []
    assert not widget.operation_overview.overview.get("missions")
    assert widget.operation_overview.listing.count() == 0


def test_empty_search_then_switch_to_mission_tab_cannot_leave_old_cluster_map(planner):
    app, widget, _data, _routes = planner
    overview_points = deepcopy(widget.operation_overview.map.points)
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_cluster_list.setCurrentItem(cluster_item(widget, {"PRO_A_Test", "PRO_B_Test"}))
    assert widget.mission_route.map.points
    widget.mission_search.setText("沒有符合結果")
    widget.mission_left_tabs.setCurrentIndex(0)
    app.processEvents()
    assert widget.mission_route.map.points == []
    assert widget.mission_route.mission_id is None
    assert widget.operation_overview.map.points == overview_points


@pytest.mark.parametrize("overrides", [
    {"status_key": "completed", "status": "Available"},
    {"status_key": "unknown", "status": "active"},
    {"status_key": "locked", "status": "Available"},
    {"kind": "SMPL3"},
    {"kind": "Other"},
    {"id": "DYN_Random_Test"},
])
def test_excluded_kinds_and_states_cannot_enter_list_or_overview_even_with_stale_available_label(planner, overrides):
    _app, widget, data, routes = planner
    excluded = dict(mission("PRO_Excluded_Test", "不應顯示任務"), **overrides)
    data.append(excluded)
    routes[excluded["id"]] = {"points": [point("excluded:survey", 100000)], "pending_stages": []}
    widget.rebuild_missions()
    for legacy_checked in (True, False):
        widget.all_missions_check.setChecked(legacy_checked)
        assert set(listed_ids(widget.mission_list)) == {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test"}
        assert {m["id"] for m in widget.operation_overview.overview["missions"]} == {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test"}
        assert {p["mission_id"] for p in widget.operation_overview.map.points} == {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test"}
    widget.mission_link(QUrl("mission:/" + excluded["id"]))
    assert widget.mission_route.mission_id != excluded["id"]
    assert "不應顯示任務" not in widget.mission_detail.toPlainText()


def test_catalog_order_is_independent_of_recommendation_goal_and_score(planner):
    app, widget, data, _routes = planner
    for order, entry in enumerate(reversed(data)):
        entry.update(order=order, recommendation_score=10000 - order)
    widget.rebuild_missions()
    expected = [entry["id"] for entry in reversed(data) if entry["status_key"] in {"available", "active"}]
    assert listed_ids(widget.mission_list) == expected
    data.reverse()
    for entry in data:
        entry["recommendation_score"] *= -1
    widget.goal_combo.setCurrentIndex(widget.goal_combo.findData("safe"))
    app.processEvents()
    assert listed_ids(widget.mission_list) == expected
    assert widget.mission_state_combo.currentData() == "actionable"


def test_hidden_legacy_filters_cannot_restore_completed_unknown_locked_or_simple_missions(planner):
    _app, widget, data, routes = planner
    data.append(dict(mission("PRO_Simple_Test", "隨機委派"), kind="SMPL3"))
    routes["PRO_Simple_Test"] = {"points": [point("simple:survey", 100000)], "pending_stages": []}
    widget.rebuild_missions()
    assert widget.all_missions_check.isHidden()
    assert widget.mission_state_combo.isHidden()
    assert widget.mission_state_combo.count() == 1
    assert widget.mission_state_combo.findData("all") == -1
    for checked in (True, False, True):
        widget.all_missions_check.setChecked(checked)
        assert widget.mission_state_combo.currentData() == "actionable"
        assert set(listed_ids(widget.mission_list)) == {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test"}
        assert {p["mission_id"] for p in widget.operation_overview.map.points} == {"PRO_A_Test", "PRO_B_Test", "PRO_C_Test"}


def test_overview_selection_opens_exact_detail_and_clears_unrelated_search(planner):
    app, widget, _data, _routes = planner
    overview_points = deepcopy(widget.operation_overview.map.points)
    widget.mission_search.setText("近處採集")
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_detail_tabs.setCurrentIndex(1)
    widget.operation_overview.missionSelected.emit("PRO_B_Test")
    app.processEvents()
    assert widget.mission_search.text() == ""
    assert widget.mission_left_tabs.currentIndex() == widget.mission_detail_tabs.currentIndex() == 0
    assert widget.mission_list.currentItem().data(Qt.ItemDataRole.UserRole)["id"] == "PRO_B_Test"
    assert widget.mission_route.mission_id == "PRO_B_Test"
    assert "調查哨站" in widget.mission_detail.toPlainText()
    assert widget.operation_overview.map.points == overview_points


def test_actual_overview_marker_click_opens_main_window_guide_and_keeps_other_missions(planner):
    app, widget, _data, _routes = planner
    overview_points = deepcopy(widget.operation_overview.map.points)
    widget.mission_search.setText("近處採集")
    widget.mission_left_tabs.setCurrentIndex(1)
    widget.mission_detail_tabs.setCurrentIndex(1)
    overview_map = widget.operation_overview.map
    overview_map.fit_targets()
    app.processEvents()
    overview_map.viewport().repaint()
    point_b = next(p for p in overview_map.points if p["mission_id"] == "PRO_B_Test" and p["stage"] == "survey")
    pos = overview_map.mapFromScene(QPointF(point_b["x"] / 100, point_b["y"] / 100))
    assert overview_map.viewport().rect().contains(pos)
    QTest.mouseClick(overview_map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    app.processEvents()
    assert widget.mission_search.text() == ""
    assert widget.mission_left_tabs.currentIndex() == widget.mission_detail_tabs.currentIndex() == 0
    assert widget.mission_list.currentItem().data(Qt.ItemDataRole.UserRole)["id"] == "PRO_B_Test"
    assert widget.mission_route.mission_id == "PRO_B_Test"
    assert "調查哨站" in widget.mission_detail.toPlainText()
    assert widget.operation_overview.selected_mission_id == "PRO_B_Test"
    assert widget.operation_overview.map.points == overview_points


def test_refresh_removes_newly_completed_operation_from_both_list_and_overview(planner):
    app, widget, data, _routes = planner
    widget.operation_overview.missionSelected.emit("PRO_B_Test")
    completed = next(m for m in data if m["id"] == "PRO_B_Test")
    completed.update(status_key="completed", status="Available")
    widget.rebuild_missions()
    app.processEvents()
    assert set(listed_ids(widget.mission_list)) == {"PRO_A_Test", "PRO_C_Test"}
    assert {m["id"] for m in widget.operation_overview.overview["missions"]} == {"PRO_A_Test", "PRO_C_Test"}
    assert {p["mission_id"] for p in widget.operation_overview.map.points} == {"PRO_A_Test", "PRO_C_Test"}
    assert widget.operation_overview.selected_mission_id != "PRO_B_Test"
    assert widget.mission_route.mission_id != "PRO_B_Test"
