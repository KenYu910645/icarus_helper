"""Saved-player previews are private, identity-bound, and keep unknowns unknown."""
from copy import deepcopy
import json

import pytest

from icarus_helper.backup_players import summarize_backup_players
from icarus_helper.saves import load_snapshot
from icarus_helper.world_backups import BackupStore, _inspect
from test_saves import save_fixture


ACCOUNT = "synthetic-private-account"
STAMP = "2026-11-02T08:29:01+00:00"


def member(slot=0, name="測試Player", playing=True, account=ACCOUNT):
    return {"UserID": account, "ChrSlot": slot, "CharacterName": name,
            "AccountName": "private-platform-display-name", "IsCurrentlyPlaying": playing}


def player(slot=0, health=123, alive=True, account=ACCOUNT, **values):
    return {"PlayerCharacterID": {"PlayerID": account, "ChrSlot": slot},
            "PlayerStateRecorderVersion": 1, "ActorStateRecorderVersion": 3,
            "Health": health, "bIsAlive": alive, "FoodLevel": 0, "WaterLevel": 120,
            "OxygenLevel": 200, "RadiationLevel": 0, "RespawnCount": 0,
            "Location": (0, -12345.5, 67), **values}


def summary(records=None, members=None):
    return summarize_backup_players({"AssociatedMembers": [member()] if members is None else members},
                                    [player()] if records is None else records, STAMP, "Terrain_019")


def test_exact_account_and_slot_separate_same_named_characters_and_preserve_zero_false():
    info = [member(0, "同名", False), member(1, "同名", True)]
    original_info = deepcopy(info)
    records = [player(1, health=400), player(0, health=0, alive=False)]
    original = deepcopy(records)
    result = summary(records, info)
    first, second = result["players"]
    assert first["slot"] == 0 and second["slot"] == 1 and first["id"] != second["id"]
    assert first["health"] == 0 and first["alive"] is False and first["is_currently_playing"] is False
    assert first["food"] == first["radiation"] == first["respawn_count"] == 0
    assert second["health"] == 400 and second["is_currently_playing"] is True
    assert first["issues"] and result["sample_time"] == STAMP and result["source"] == "backup"
    encoded = json.dumps(result, ensure_ascii=False)
    assert ACCOUNT not in encoded and "private-platform-display-name" not in encoded
    assert records == original and info == original_info


@pytest.mark.parametrize("invalid", [False, True, -1, float("nan"), float("inf"), "123", None])
def test_bad_or_missing_health_never_becomes_zero(invalid):
    result = summary([player(health=invalid)])
    assert result["players"][0]["health"] is None
    assert result["players"][0]["alive"] is True
    assert result["status"] == "partial"


def test_alive_is_only_explicit_flag_and_no_health_denominator_is_invented():
    result = summary([player(health=0, alive=None)])
    row = result["players"][0]
    assert row["health"] == 0 and row["alive"] is None
    assert "max_health" not in row and "health_percent" not in row
    contradictory = summary([player(health=0, alive=True)])["players"][0]
    assert contradictory["health"] == 0 and contradictory["alive"] is True and contradictory["issues"]


@pytest.mark.parametrize("records", [[player(), player()], [player(), player(health=999)]])
def test_duplicate_player_identity_is_not_chosen_arbitrarily(records):
    result = summary(records)
    assert len(result["players"]) == 1
    row = result["players"][0]
    assert row["health"] is None and row["alive"] is None and row["position"] is None
    assert result["status"] == "unknown" and row["issues"]


def test_conflicting_member_names_do_not_map_values_to_either_name():
    result = summary(members=[member(name="角色甲"), member(name="角色乙")])
    row = result["players"][0]
    assert row["name"].startswith("Name unconfirmed")
    assert row["health"] is None and row["is_currently_playing"] is None


def test_different_account_same_slot_is_not_a_member_match():
    result = summary([player(account="another-private-account")])
    assert len(result["players"]) == 2
    assert all(row["health"] is None for row in result["players"])
    assert "another-private-account" not in json.dumps(result)


@pytest.mark.parametrize("field,value", [("PlayerStateRecorderVersion", 2), ("ActorStateRecorderVersion", 4),
                                         ("PlayerStateRecorderVersion", None), ("PlayerStateRecorderVersion", True)])
def test_unknown_record_version_preserves_name_but_not_values(field, value):
    result = summary([player(**{field: value})])
    row = result["players"][0]
    assert row["name"] == "測試Player" and row["health"] is None and row["position"] is None
    assert result["status"] == "unknown"


def test_names_never_fall_back_to_account_names_or_private_ids():
    result = summary(members=[member(name=ACCOUNT)])
    assert result["players"][0]["name"].startswith("Name unconfirmed")
    assert ACCOUNT not in json.dumps(result)


def test_unknown_playing_flag_and_bad_location_are_not_false_or_origin():
    result = summary([player(Location=[1, float("nan"), 3])], [member(playing=0)])
    row = result["players"][0]
    assert row["is_currently_playing"] is None and row["position"] is None


def test_missing_records_keep_expected_members_visible_and_unknown():
    result = summary([], [member(0), member(1, "另一位")])
    assert len(result["players"]) == 2 and result["status"] == "unknown"
    assert all(row["health"] is None for row in result["players"])


def test_full_backup_inspection_matches_complete_decoder_without_mutating_archives(tmp_path):
    source = tmp_path / "Player來源" / "Prospects" / "測試世界.json"
    records = [("/Script/Icarus.GameModeStateRecorderComponent", {"GameModeRecord": {"GameStateSeed": 3439}}),
               ("/Script/Icarus.PlayerStateRecorderComponent", player(0, health=0, alive=False)),
               ("/Script/Icarus.PlayerStateRecorderComponent", player(1, health=400))]
    envelope = save_fixture(source, records=records)
    envelope["ProspectInfo"]["AssociatedMembers"] = [member(0, "甲", False), member(1, "乙")]
    source.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    before = source.read_bytes()
    result = _inspect(before, source)
    complete = load_snapshot(source)
    full_rows = sorted((r["PlayerCharacterID"]["ChrSlot"], r["Health"], r["bIsAlive"])
                       for r in complete.raw_records if r["_component"].endswith("PlayerStateRecorderComponent"))
    brief = result["player_preview"]
    assert [(r["slot"], r["health"], r["alive"]) for r in brief["players"]] == full_rows
    store = BackupStore(tmp_path / "獨立備份")
    store.register(complete)
    captured = store.capture(complete.world_id)
    path = tmp_path / "獨立備份" / "Backups" / complete.world_id / captured["id"]
    archive_before = path.read_bytes()
    version = store.summary(complete.world_id)["versions"][0]
    assert version["player_preview"]["players"][1]["health"] == 400
    assert source.read_bytes() == before and path.read_bytes() == archive_before


def test_unsupported_player_record_only_makes_preview_unknown(tmp_path):
    source = tmp_path / "source.json"
    envelope = save_fixture(source, records=[
        ("/Script/Icarus.GameModeStateRecorderComponent", {"GameModeRecord": {"GameStateSeed": 1}}),
        ("/Script/Icarus.PlayerStateRecorderComponent", player(PlayerStateRecorderVersion=999)),
    ])
    envelope["ProspectInfo"]["AssociatedMembers"] = [member()]
    source.write_text(json.dumps(envelope), encoding="utf-8")
    result = _inspect(source.read_bytes(), source)
    assert result["world_id"] and result["sha256"]
    assert result["player_preview"]["status"] == "unknown"


def test_preview_failure_does_not_change_valid_backup_acceptance(tmp_path, monkeypatch):
    from icarus_helper import world_backups
    source = tmp_path / "world.json"
    save_fixture(source, records=[
        ("/Script/Icarus.GameModeStateRecorderComponent", {"GameModeRecord": {"GameStateSeed": 1}})])
    def unexpected(*_args):
        raise OverflowError("synthetic presentation failure")
    monkeypatch.setattr(world_backups, "summarize_backup_players", unexpected)
    result = world_backups._inspect(source.read_bytes(), source)
    assert result["sha256"] and result["world_id"]
    assert result["player_preview"]["status"] == "unknown"


def test_restore_rejects_replaced_valid_candidate_after_summary_confirmation(tmp_path, monkeypatch):
    from icarus_helper import world_backups
    monkeypatch.setattr(world_backups, "game_processes", lambda: [])
    source = tmp_path / "source.json"
    def write(health):
        envelope = save_fixture(source, records=[
            ("/Script/Icarus.GameModeStateRecorderComponent", {"GameModeRecord": {"GameStateSeed": 1}}),
            ("/Script/Icarus.PlayerStateRecorderComponent", player(health=health)),
        ])
        envelope["ProspectInfo"]["AssociatedMembers"] = [member()]
        source.write_text(json.dumps(envelope), encoding="utf-8")
    write(100)
    store = BackupStore(tmp_path / "archive")
    snapshot = load_snapshot(source)
    store.register(snapshot)
    version = store.capture(snapshot.world_id)
    write(200)
    before = source.read_bytes()
    # The candidate path is unchanged and the replacement remains this same
    # valid world, but its content no longer matches what the user selected.
    from pathlib import Path
    Path(version["path"]).write_bytes(before)
    with pytest.raises(world_backups.BackupError, match="backup content changed"):
        store.restore(snapshot.world_id, version["id"], expected_backup_sha256=version["sha256"])
    assert source.read_bytes() == before and not (store.root / "Restores").exists()
