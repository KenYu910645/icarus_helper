"""Qt interactions for mixed live/save positions and background world sampling."""
from copy import deepcopy
from datetime import datetime, timezone
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from icarus_helper.animal_map import AnimalMapPanel
from icarus_helper.animal_widgets import AnimalCard, AnimalGauge, position_source_text, sample_time_text
from icarus_helper.animals_view import AnimalPanel
from test_animals_view import animal, summary, wait_for


SAVED = datetime(2026, 11, 2, 5, 6, 7, tzinfo=timezone.utc)
SAMPLED = "2026-11-02T07:08:09+00:00"
CATALOG = {"map": "Prometheus", "calibrated": True,
           "bounds": {"min_x": -403200, "min_y": -403200, "max_x": 403200, "max_y": 403200}}


def source(world="test-world", shared=False):
    return {"world_id": world, "map_name": "Prometheus", "saved_at": SAVED, "is_shared_map": shared}


def live_animal():
    entry = animal("animal-a", "阿牛")
    entry.update(position=[-80000, 24000, 900], saved_position=[3000, 4000, 800],
                 position_source="live", position_sample_time=SAMPLED, species="Buffalo",
                 owner_key="owner:test")
    entry["stats"] = [{"id": key, "value": 51, "maximum": 300, "maximum_status": "live",
                       "sample_time": SAMPLED, "unit": "Current game value"}
                      for key in ("health", "food", "water", "oxygen", "stamina")]
    return entry


def player(ident="player:opaque-hash", *, live=True):
    return {"id": ident, "name": "同行隊友", "position": [140000, -90000, 1000],
            "position_source": "live" if live else "save", "sample_time": SAMPLED if live else SAVED}


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def map_panel(app):
    widget = AnimalMapPanel()
    widget.resize(900, 720)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def test_mixed_source_coordinates_times_and_player_filter_survive(app, map_panel):
    live = live_animal()
    saved = animal("saved-b", "save動物")
    saved.update(position=[50000, 70000, 500], position_source="save")
    data = {"animals": [live, saved], "players": [player()]}
    before = deepcopy(data)
    map_panel.set_context(source(), data, catalog=CATALOG)
    app.processEvents()
    assert [(p["x"], p["y"], p["z"]) for p in map_panel.map.points] == [tuple(live["position"]), tuple(saved["position"])]
    assert (map_panel.map.players[0]["x"], map_panel.map.players[0]["y"], map_panel.map.players[0]["z"]) == tuple(player()["position"])
    assert data == before
    map_panel.select_animal(live["id"])
    assert "Game position" in map_panel.position_note.text()
    assert sample_time_text(SAMPLED) in map_panel.position_note.text()
    assert "Last saved X 3,000" in map_panel.position_note.toolTip()
    assert "horizontal straight-line distance" in map_panel.position_note.text()
    map_panel.select_animal(saved["id"])
    assert "Saved position" in map_panel.position_note.text()
    assert sample_time_text(SAVED) in map_panel.position_note.text()
    map_panel.set_animals([])
    app.processEvents()
    assert not map_panel.map.points
    assert len(map_panel.map.players) == 1
    assert "Players 1" in map_panel.status.text()
    map_panel.map.fit_animals()
    center = map_panel.map.mapToScene(map_panel.map.viewport().rect().center())
    assert center.x() == pytest.approx(1400, abs=5)
    assert center.y() == pytest.approx(-900, abs=5)


def test_native_player_click_never_selects_an_animal(app, map_panel, monkeypatch):
    from icarus_helper import animal_map
    calls = []
    original = animal_map.marker_pixmap
    def picture(kind, *args, **kwargs):
        calls.append(kind)
        return original(kind, *args, **kwargs)
    monkeypatch.setattr(animal_map, "marker_pixmap", picture)
    entry = live_animal()
    map_panel.set_context(source(), {"animals": [entry], "players": [player()]}, catalog=CATALOG)
    map_panel.select_animal(entry["id"], focus=False)
    map_panel.map.fit_animals()
    app.processEvents()
    selected, selected_players = [], []
    map_panel.animal_selected.connect(selected.append)
    map_panel.player_selected.connect(selected_players.append)
    pos = map_panel.map.mapFromScene(QPointF(1400, -900))
    target = map_panel.map.hit_target(QPointF(pos))
    assert target["members"][0]["kind"] == "player"
    assert target["scene_position"] == QPointF(1400, -900)
    QTest.mouseClick(map_panel.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert not selected and len(selected_players) == 1
    assert map_panel._selected_id == entry["id"]
    assert "Player 同行隊友" in map_panel.position_note.text()
    assert "Game position" in map_panel.position_note.text()
    assert "player" in calls
    # Animal selection remains available after inspecting a player.
    pos = map_panel.map.mapFromScene(QPointF(-800, 240))
    QTest.mouseClick(map_panel.map.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert selected == [entry["id"]]


def test_invalid_players_and_world_switch_cannot_leave_private_targets(app, map_panel):
    players = [player(), player("player:duplicate"), player("player:duplicate"),
               dict(player("player:bad"), position=[0, float("nan"), 0]),
               dict(player("raw-platform-id")), dict(player("player:boolean"), position=[True, 0, 0])]
    map_panel.set_context(source(), {"animals": [live_animal()], "players": players}, catalog=CATALOG)
    assert [p["id"] for p in map_panel.players] == [player()["id"]]
    map_panel._point_selected(map_panel.players[0])
    assert map_panel.position_note.toolTip()
    map_panel.set_context(source("different-world"), {"animals": [], "players": []}, catalog=CATALOG)
    app.processEvents()
    assert not map_panel.players and not map_panel.map.players and not map_panel.map._hit_targets
    assert not map_panel.position_note.text() and not map_panel.position_note.toolTip()
    map_panel.set_context(source(shared=True), {"animals": [live_animal()], "players": [player()]}, catalog=CATALOG)
    map_panel.set_players([player()])
    assert map_panel.snapshot is None and not map_panel.map.players and not map_panel.map.points
    assert "shared" in map_panel.status.text()


@pytest.mark.parametrize("key", ["health", "food", "water", "oxygen", "stamina"])
def test_all_live_gauges_keep_same_sample_value_max_and_source_time(app, key):
    stat = {"id": key, "value": 51, "maximum": 300, "maximum_status": "live", "sample_time": SAMPLED}
    gauge = AnimalGauge(stat)
    assert gauge.ratio_known and gauge.value == 51 and gauge.maximum == 300
    assert "51 / 300" in gauge.toolTip()
    assert "Current game value and maximum sampled together" in gauge.toolTip()
    assert sample_time_text(SAMPLED) in gauge.toolTip()
    gauge.close()


def test_live_without_time_never_borrows_save_time_and_invalid_location_is_disabled(app):
    assert position_source_text({"position_source": "live"}, SAVED) == "Game position · Time unknown"
    assert position_source_text({"position_source": "save"}, SAVED) == "Saved position · " + sample_time_text(SAVED)
    entry = live_animal()
    card = AnimalCard(entry, "#74c9ef", "home")
    assert "Game position" in card.location_label.toolTip()
    assert sample_time_text(SAMPLED) in card.location_label.toolTip()
    assert card.findChild(QPushButton).isEnabled()
    card.close()
    entry["position"] = [0, float("inf"), 0]
    card = AnimalCard(entry, "#74c9ef", "home")
    assert not card.findChild(QPushButton).isEnabled()
    card.close()


def test_hidden_tab_samples_every_30_seconds_and_stops_for_clear_shared_closed(app):
    calls, loaded = [], []
    lock = threading.Lock()
    def summarize(snapshot, base, radius_m):
        with lock:
            calls.append(snapshot["world_id"])
        data = summary(live_animal())
        data["players"] = [player()]
        data["live_stats"] = {"world_verified": True, "sample_time": SAMPLED}
        return data
    panel = AnimalPanel(summarize)
    panel.loaded.connect(loaded.append)
    assert panel.live_refresh.interval() == 30000
    assert not panel.isVisible()
    panel.set_snapshot(source(), None, catalog=CATALOG)
    wait_for(app, lambda: len(loaded) == 1 and not panel.busy)
    panel.live_refresh.timeout.emit()
    wait_for(app, lambda: len(loaded) == 2 and not panel.busy)
    assert calls == ["test-world", "test-world"]
    assert loaded[-1]["players"][0]["position_source"] == "live"
    assert "5 fields" in panel.status.text()
    assert "Game positions: animals 1 / players 1" in panel.status.text()
    # Filtering animal cards must not suppress players used by either map.
    panel.search_edit.setText("沒有動物")
    assert not panel.animal_map.map.points and len(panel.animal_map.map.players) == 1
    panel.clear()
    panel.live_refresh.timeout.emit()
    QTest.qWait(160)
    assert calls == ["test-world", "test-world"]
    panel.set_snapshot(source(shared=True), None, catalog=CATALOG)
    panel.live_refresh.timeout.emit()
    QTest.qWait(160)
    assert panel.snapshot is None and not panel.summary and len(calls) == 2
    panel.close()
    assert not panel.live_refresh.isActive()
    panel.live_refresh.timeout.emit()
    QTest.qWait(160)
    assert len(calls) == 2
