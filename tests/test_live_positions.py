"""Live position overlays keep identities, scope and immutable save provenance."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from icarus_helper.animals import _apply_live_stats
from icarus_helper.live_positions import apply_player_positions, player_key, player_map_points, saved_players


def point(account="private-player-a", slot=1, name="同名Player", ident="saved-a"):
    return {"id": ident, "kind": "player", "name": name, "x": 1, "y": 2, "z": 3,
            "detail": "save位置", "raw": {"PlayerCharacterID": {"PlayerID": account, "ChrSlot": slot}}}


def snapshot(players):
    return {"players": players, "saved_at": "2026-11-02T07:00:00+00:00", "world_id": "world-a", "sha256": "snapshot-a"}


def test_namesakes_and_slots_have_distinct_keys_without_private_ids_in_summary():
    players = saved_players(snapshot([point(), point(slot=2, ident="saved-b"), point(account="private-player-b", ident="saved-c")]))
    assert len({p["id"] for p in players}) == 3
    assert "private-player" not in json.dumps(players)
    assert all(p["position_source"] == "save" and p["position"] == [1, 2, 3] for p in players)


def test_shared_unknown_and_conflicting_player_records_are_not_promoted():
    source = snapshot([point()])
    assert not saved_players(dict(source, is_shared_map=True))
    bad = point()
    bad["raw"]["PlayerCharacterID"].pop("ChrSlot")
    assert not saved_players(snapshot([bad]))
    other = point()
    other["x"] = 20
    assert not saved_players(snapshot([point(), other]))


def test_player_without_saved_coordinates_can_gain_a_verified_live_marker():
    source = snapshot([])
    source["raw_records"] = [{"_component": "/Script/Icarus.PlayerStateRecorderComponent",
                               "PlayerCharacterID": {"PlayerID": "private-player-a", "ChrSlot": 1}}]
    players = saved_players(source)
    assert len(players) == 1 and players[0]["position"] is None
    count = apply_player_positions(players, {"status": "ok", "world_verified": True, "sample_time": "live-time",
                                            "players": {players[0]["id"]: {"position": [40, 50, 60]}}})
    assert count == 1
    rendered = player_map_points([], players)
    assert len(rendered) == 1 and rendered[0]["x"] == 40 and rendered[0]["position_source"] == "live"


def test_source_name_cannot_reintroduce_private_account_identity():
    raw = point(name="private-player-a")
    players = saved_players(snapshot([raw]))
    assert players[0]["name"] == "Player"
    assert player_map_points([raw], players)[0]["name"] == "Player"
    apply_player_positions(players, {"status": "ok", "world_verified": True, "sample_time": "live-time",
                                    "players": {players[0]["id"]: {"position": [40, 50, 60]}}})
    assert player_map_points([raw], players)[0]["name"] == "Player"


@pytest.mark.parametrize("verified,status", [(False, "ok"), (True, "unavailable")])
def test_unverified_players_cannot_override_snapshot(verified, status):
    players = saved_players(snapshot([point()]))
    before = deepcopy(players)
    result = apply_player_positions(players, {"world_verified": verified, "status": status,
                                           "players": {players[0]["id"]: {"position": [40, 50, 60]}}})
    assert result == 0 and players == before


def test_live_overlay_preserves_original_resources_and_missing_remote_snapshot():
    sources = [point(), point(account="private-player-b", ident="saved-b")]
    before = deepcopy(sources)
    players = saved_players(snapshot(sources))
    count = apply_player_positions(players, {"world_verified": True, "status": "partial", "sample_time": "live-time",
                                           "players": {player_key(("private-player-a", 1)): {"position": [40, 50, 60]}}})
    assert count == 1
    rendered = player_map_points(sources, players)
    assert sources == before
    assert (rendered[0]["x"], rendered[0]["y"], rendered[0]["z"]) == (40, 50, 60)
    assert rendered[0]["id"] == "saved-a" and rendered[0]["position_source"] == "live"
    assert rendered[1] is sources[1]
    assert players[0]["saved_position"] == [1, 2, 3] and players[1]["position_source"] == "save"


def test_live_food_pairs_and_animal_positions_update_home_distance_together():
    summary = {"coverage": {}, "animals": [{"id": "42", "position": [0, 0, 10], "distance_m": 0, "at_home": True,
               "stats": [{"id": "food", "value": 200, "maximum": 300, "maximum_status": "verified"}]}]}
    _apply_live_stats(summary, {"status": "ok", "world_verified": True, "sample_time": "live-time",
                               "animals": {"42": {"position": [20000, 0, 50], "food": {"value": 150, "maximum": 300}}}},
                      base={"x": 0, "y": 0}, radius_m=100)
    animal = summary["animals"][0]
    assert animal["position"] == [20000, 0, 50] and animal["saved_position"] == [0, 0, 10]
    assert animal["position_source"] == "live" and animal["distance_m"] == 200 and animal["at_home"] is False
    assert summary["coverage"]["outside_home"] == 1 and summary["coverage"]["at_home"] == 0
    stat = animal["stats"][0]
    assert (stat["value"], stat["maximum"], stat["maximum_status"]) == (150, 300, "live")


def test_main_map_rejects_old_world_or_snapshot_and_falls_back_without_game(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from PySide6.QtWidgets import QApplication
    from icarus_helper.models import WorldSnapshot
    from icarus_helper.settings import Settings
    from icarus_helper.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "populate_worlds", lambda *_: None)
    window = MainWindow(settings=Settings(tmp_path / "settings.json"))
    window.controller.poll.stop()
    now = datetime.now(timezone.utc)
    window.snapshot = WorldSnapshot(tmp_path / "synthetic.json", "world-a", "合成世界", "Prometheus", now, now, "snapshot-a")
    window.points = [point()]
    players = saved_players(snapshot(window.points))
    apply_player_positions(players, {"status": "ok", "world_verified": True, "sample_time": now.isoformat(),
                                    "players": {players[0]["id"]: {"position": [40, 50, 60]}}})
    data = {"world_id": "world-a", "snapshot_sha256": "snapshot-a", "players": players, "coverage": {"live_stats": {"applied_player_positions": 1, "sample_time": now.isoformat()}}}
    try:
        window.live_positions_updated(dict(data, world_id="world-b"))
        window.live_positions_updated(dict(data, snapshot_sha256="older"))
        assert window._live_positions_summary is None
        window.live_positions_updated(data)
        rendered = next(p for p in window.visible_points if isinstance(p, dict) and p.get("kind") == "player")
        assert rendered["x"] == 40 and "Player positions" in window.world_label.text()
        fallback = dict(data, players=saved_players(snapshot(window.points)), coverage={"live_stats": {"status": "not_running"}})
        window.live_positions_updated(fallback)
        assert window.visible_points[0] is window.points[0]
        assert "Player positions" not in window.world_label.text()
        assert "use saved data" in window.world_label.toolTip()
        window.snapshot = SimpleNamespace(is_shared_map=True, world_id="shared")
        window._live_positions_summary = None
        window.live_positions_updated(data)
        assert window._live_positions_summary is None
    finally:
        window.close()
        app.processEvents()
