"""Native operation gates with invented local accounts and isolated world data."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from icarus_helper import operation_eligibility as eligibility, release_sync, saves
from icarus_helper.models import WorldSnapshot


def make_world(tmp_path, *, owner="synthetic-host", world_id="world-a", sha256="world-a-hash"):
    source = tmp_path / "遊戲資料" / owner / "Prospects" / (world_id + ".json")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"invented world bytes; never parsed as a real save")
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    return WorldSnapshot(source, world_id, "合成任務世界", "Prometheus", now, now,
                         sha256, prospect_info={"ClaimedAccountID": owner})


def write_profile(world, **changes):
    profile = {"UserID": world.prospect_info["ClaimedAccountID"],
               "Talents": [{"RowName": "Prospect_Pro_Story_2", "Rank": 1}],
               "UnlockedFlags": ["Synthetic_Unlock"],
               "PrivateTestField": "DO_NOT_RETURN_TEST_SECRET"}
    profile.update(changes)
    path = world.source_path.parent.parent / "Profile.json"
    path.write_bytes(json.dumps(profile, ensure_ascii=False).encode("utf-8"))
    return path


def attach_account(world, talents=None, flags=()):
    world.operation_account = {"status": "verified", "world_id": world.world_id,
                               "snapshot_sha256": world.sha256,
                               "talents": dict(talents or {}), "flags": list(flags)}
    return world


@pytest.fixture
def local_world(tmp_path):
    return make_world(tmp_path)


def test_profile_path_is_only_adjacent_to_selected_prospects_directory(tmp_path):
    source = tmp_path / "主機account" / "pRoSpEcTs" / "世界.json"
    assert eligibility.source_profile_path(source) == source.parent.parent / "Profile.json"
    assert eligibility.source_profile_path(None) is None
    assert eligibility.source_profile_path(tmp_path / "snapshot" / "latest.json") is None
    assert eligibility.source_profile_path(tmp_path / "Prospects-other" / "world.json") is None


def test_verified_profile_has_its_own_hash_time_and_world_identity_without_private_fields(local_world):
    path = write_profile(local_world)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (path, local_world.source_path)}
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "verified"
    assert result["world_id"] == local_world.world_id
    assert result["snapshot_sha256"] == local_world.sha256
    assert result["profile_sha256"] == hashlib.sha256(before[path][0]).hexdigest()
    assert result["profile_signature"] == (path.stat().st_mtime_ns, path.stat().st_size)
    assert datetime.fromisoformat(result["profile_saved_at"]).tzinfo is not None
    assert datetime.fromisoformat(result["checked_at"]).tzinfo is not None
    assert result["talents"] == {"Prospect_Pro_Story_2": 1}
    assert result["flags"] == ["Synthetic_Unlock"]
    assert "UserID" not in result and "DO_NOT_RETURN_TEST_SECRET" not in json.dumps(result)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


def test_profile_refresh_observes_new_unlocks_and_hash_without_rewriting_world(local_world):
    path = write_profile(local_world)
    original_world = local_world.source_path.read_bytes()
    first = eligibility.load_operation_account(local_world)
    write_profile(local_world, Talents=[{"RowName": "Prospect_Pro_Story_5", "Rank": 2}],
                  UnlockedFlags=[])
    second = eligibility.load_operation_account(local_world)
    assert first["profile_sha256"] != second["profile_sha256"]
    assert second["profile_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first["talents"] == {"Prospect_Pro_Story_2": 1}
    assert second["talents"] == {"Prospect_Pro_Story_5": 2}
    assert second["snapshot_sha256"] == first["snapshot_sha256"] == local_world.sha256
    assert local_world.source_path.read_bytes() == original_world


def test_directory_owner_mismatch_is_rejected_before_opening_any_profile(local_world, monkeypatch):
    write_profile(local_world)
    local_world.prospect_info["ClaimedAccountID"] = "another-synthetic-host"
    monkeypatch.setattr(saves, "_read_stable", lambda *_: pytest.fail("mismatched account was read"))
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "identity_mismatch"
    assert result["talents"] == {} and result["flags"] == []


@pytest.mark.parametrize("profile_owner", [None, "another-synthetic-host"])
def test_profile_user_identity_must_match_world_and_directory(local_world, profile_owner):
    write_profile(local_world, UserID=profile_owner)
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "identity_mismatch"
    assert not result["talents"] and not result["flags"]
    assert "profile_sha256" not in result


@pytest.mark.parametrize("attribute", ["is_remote", "is_shared_map"])
def test_remote_and_shared_worlds_never_read_receiver_profile(local_world, monkeypatch, attribute):
    write_profile(local_world)
    setattr(local_world, attribute, True)
    monkeypatch.setattr(saves, "_read_stable", lambda *_: pytest.fail("receiver profile was read"))
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "unavailable" and result["profile_signature"] is None
    assert not result["talents"] and not result["flags"]


def test_marked_remote_cache_is_rejected_even_when_remote_flag_is_missing(local_world, monkeypatch):
    write_profile(local_world)
    marker = local_world.source_path.parent.parent.parent / release_sync.MARKER_NAME
    marker.write_text(json.dumps(release_sync._MARKER), encoding="utf-8")
    assert release_sync.is_remote_cache_path(local_world.source_path)
    monkeypatch.setattr(saves, "_read_stable", lambda *_: pytest.fail("remote cache profile was read"))
    assert eligibility.load_operation_account(local_world)["status"] == "unavailable"


def test_missing_or_nonlocal_profile_does_not_search_other_accounts(local_world, tmp_path, monkeypatch):
    another = make_world(tmp_path, owner="other-synthetic-host", world_id="world-b")
    write_profile(another)
    assert eligibility.load_operation_account(local_world)["status"] == "unavailable"
    local_world.source_path = tmp_path / "snapshot" / "latest.json"
    monkeypatch.setattr(saves, "_read_stable", lambda *_: pytest.fail("nonlocal profile was read"))
    assert eligibility.load_operation_account(local_world)["status"] == "unavailable"


@pytest.mark.parametrize("changes", [
    {"Talents": None}, {"Talents": {}}, {"UnlockedFlags": None},
    {"UnlockedFlags": ["valid", True]}, {"UnlockedFlags": ["valid", 1.0]},
    {"UnlockedFlags": ["valid", {}]}, {"UnlockedFlags": ["valid", -1]},
    {"Talents": [None]},
    {"Talents": [{"RowName": 123, "Rank": 1}]},
    {"Talents": [{"RowName": "Synthetic", "Rank": True}]},
    {"Talents": [{"RowName": "Synthetic", "Rank": -1}]},
    {"Talents": [{"RowName": "Synthetic", "Rank": 1.5}]},
    {"Talents": [{"RowName": "Synthetic"}]},
    {"Talents": [{"RowName": "Synthetic", "Rank": 0}, {"RowName": "Synthetic", "Rank": 1}]},
])
def test_malformed_profile_unlocks_never_become_verified_or_partially_available(local_world, changes):
    write_profile(local_world, **changes)
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "unavailable"
    assert result["talents"] == {} and result["flags"] == []
    assert "profile_sha256" not in result


@pytest.mark.parametrize("raw", [b"{invalid json", b"null", b"[]", b"\xff\x80"])
def test_invalid_profile_envelope_is_conservative(local_world, raw):
    path = write_profile(local_world)
    path.write_bytes(raw)
    result = eligibility.load_operation_account(local_world)
    assert result["status"] != "verified"
    assert result["talents"] == {} and result["flags"] == []


@pytest.mark.parametrize("flags", [[0, 1, 24], [1433523076, 3698625322], ["Synthetic_Unlock", 42]])
def test_numeric_account_flags_are_valid_profile_data_for_unrestricted_prometheus_talents(local_world, flags):
    write_profile(local_world, UnlockedFlags=flags)
    local_world.operation_account = eligibility.load_operation_account(local_world)
    assert local_world.operation_account["status"] == "verified"
    assert eligibility.operation_talent_gate(local_world, "PRO_SQ_Fisher")[0] == "available"
    assert eligibility.operation_talent_gate(local_world, "PRO_A_Extraction")[0] == "available"


def test_current_prometheus_nodes_have_no_account_flag_requirements():
    rules = eligibility.load_operation_rules()
    nodes = [node for node in rules["talents"].values() if node["tree"] == "Prospect_Prometheus"]
    assert len(nodes) == 26
    assert all(not node["required_flags"] and not node["forbidden_flags"] for node in nodes)
    assert all(not rule["required_flags"] for mid, rule in rules["missions"].items() if mid.startswith("PRO_"))


def test_identical_duplicate_talent_ranks_and_legal_zero_are_not_conflicts(local_world):
    write_profile(local_world, Talents=[{"RowName": "Synthetic", "Rank": 0},
                                       {"RowName": "Synthetic", "Rank": 0}])
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "verified" and result["talents"] == {"Synthetic": 0}


def test_profile_changed_while_reading_is_unavailable_instead_of_using_partial_unlocks(local_world, monkeypatch):
    write_profile(local_world)
    def changing(_path):
        raise saves.SaveChangedError("synthetic profile replacement during read")
    monkeypatch.setattr(saves, "_read_stable", changing)
    result = eligibility.load_operation_account(local_world)
    assert result["status"] == "unavailable" and not result["talents"]


@pytest.mark.parametrize("change", [{"world_id": "world-b"}, {"sha256": "new-snapshot-hash"}])
def test_account_evidence_cannot_cross_world_or_snapshot_generation(local_world, change):
    write_profile(local_world)
    local_world.operation_account = eligibility.load_operation_account(local_world)
    assert eligibility.operation_talent_gate(local_world, "PRO_SQ_Fisher")[0] == "available"
    for key, value in change.items():
        setattr(local_world, key, value)
    assert eligibility.operation_talent_gate(local_world, "PRO_SQ_Fisher")[0] == "unknown"


def test_separate_worlds_use_independently_bound_account_evidence(tmp_path):
    first = make_world(tmp_path)
    second = make_world(tmp_path, world_id="world-b", sha256="world-b-hash")
    write_profile(first)
    first.operation_account = eligibility.load_operation_account(first)
    second.operation_account = deepcopy(first.operation_account)
    assert eligibility.operation_talent_gate(second, "PRO_SQ_Fisher")[0] == "unknown"
    second.operation_account = eligibility.load_operation_account(second)
    assert eligibility.operation_talent_gate(second, "PRO_SQ_Fisher")[0] == "available"
    assert first.operation_account["world_id"] != second.operation_account["world_id"]


@pytest.mark.parametrize("field, evidence_field", [("world_id", "world_id"), ("sha256", "snapshot_sha256")])
def test_missing_identity_cannot_pass_merely_because_world_and_evidence_both_lack_it(local_world, field, evidence_field):
    attach_account(local_world, {"Prospect_Pro_Story_2": 1})
    setattr(local_world, field, "")
    local_world.operation_account[evidence_field] = ""
    assert eligibility.operation_talent_gate(local_world, "PRO_SQ_Fisher")[0] == "unknown"


@pytest.mark.parametrize("talents, expected", [
    ({}, "locked"), ({"Prospect_OLY_Canyon_Expedition": 0}, "locked"),
    ({"Prospect_OLY_Canyon_Expedition": 1}, "available"),
    ({"Prospect_OLY_Glacier_Expedition": 1}, "available"),
])
def test_native_multiple_prerequisites_are_or_not_and(local_world, talents, expected):
    attach_account(local_world, talents)
    assert eligibility.operation_talent_gate(local_world, "Tier2_Canyon_Stockpile")[0] == expected


def test_packaged_prometheus_reroute_unlocks_without_requiring_rank_on_the_reroute(local_world):
    attach_account(local_world, {"Prospect_Pro_Story_2": 1})
    before = deepcopy(local_world.operation_account)
    assert eligibility.operation_talent_gate(local_world, "PRO_A_Extraction")[0] == "available"
    assert local_world.operation_account == before
    attach_account(local_world, {"Prospect_Pro_Story_2": 0})
    assert eligibility.operation_talent_gate(local_world, "PRO_A_Extraction")[0] == "locked"


@pytest.mark.parametrize("talents", [
    {"Prospect_Pro_A_Exploration": 1},
    {"Prospect_Pro_A_Exploration": 1, "Prospect_Pro_Story_2": 0},
])
def test_saved_default_reroute_rank_cannot_bypass_its_current_normal_prerequisite(local_world, talents):
    attach_account(local_world, talents)
    before = deepcopy(local_world.operation_account)
    assert eligibility.operation_talent_gate(local_world, "PRO_A_Extraction")[0] == "locked"
    assert local_world.operation_account == before


def synthetic_node(parents=(), *, default=False, required_flags=(), forbidden_flags=(), level=0, rank="None"):
    return {"required_talents": list(parents), "required_flags": list(required_flags),
            "forbidden_flags": list(forbidden_flags), "required_level": level,
            "required_rank": rank, "default_unlocked": default, "tree": "Synthetic"}


def synthetic_rules(monkeypatch, nodes):
    rules = {"missions": {"SYNTHETIC": {"talent_ids": ["leaf"]}}, "talents": nodes}
    monkeypatch.setattr(eligibility, "load_operation_rules", lambda: rules)


def test_reroute_closure_reaches_fixed_point_even_in_reverse_dependency_order(local_world, monkeypatch):
    synthetic_rules(monkeypatch, {
        "leaf": synthetic_node(["reroute-three"]),
        "reroute-three": synthetic_node(["reroute-two"], default=True),
        "reroute-two": synthetic_node(["reroute-one"], default=True),
        "reroute-one": synthetic_node(["seed"], default=True),
        "seed": synthetic_node(),
    })
    attach_account(local_world, {"seed": 1})
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == "available"
    attach_account(local_world)
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == "locked"


def test_unseeded_reroute_cycle_does_not_create_an_unlock_or_loop_forever(local_world, monkeypatch):
    synthetic_rules(monkeypatch, {"leaf": synthetic_node(["cycle-a"]),
                                 "cycle-a": synthetic_node(["cycle-b"], default=True),
                                 "cycle-b": synthetic_node(["cycle-a"], default=True)})
    attach_account(local_world)
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == "locked"


@pytest.mark.parametrize("flags, expected", [([], "locked"), (["Allow"], "available"),
                                             (["Allow", "Deny"], "locked")])
def test_reroute_closure_respects_required_and_forbidden_account_flags(local_world, monkeypatch, flags, expected):
    synthetic_rules(monkeypatch, {"leaf": synthetic_node(["reroute"]),
                                 "reroute": synthetic_node(default=True, required_flags=["Allow"],
                                                           forbidden_flags=["Deny"])})
    attach_account(local_world, flags=flags)
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == expected


@pytest.mark.parametrize("through_reroute", [False, True])
@pytest.mark.parametrize("flags, expected", [([], "locked"), (["Allow"], "available"),
                                             (["Allow", "Deny"], "locked")])
def test_positive_normal_parent_rank_does_not_bypass_its_flag_restrictions(local_world, monkeypatch, through_reroute, flags, expected):
    nodes = {"leaf": synthetic_node(["reroute" if through_reroute else "seed"]),
             "seed": synthetic_node(required_flags=["Allow"], forbidden_flags=["Deny"])}
    if through_reroute:
        nodes["reroute"] = synthetic_node(["seed"], default=True)
    synthetic_rules(monkeypatch, nodes)
    attach_account(local_world, {"seed": 1}, flags=flags)
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == expected


@pytest.mark.parametrize("node", [synthetic_node(required_flags=["Allow"]),
                                  synthetic_node(forbidden_flags=["Deny"])])
def test_unmapped_numeric_flags_keep_named_flag_requirement_unknown(local_world, monkeypatch, node):
    write_profile(local_world, UnlockedFlags=[1433523076])
    local_world.operation_account = eligibility.load_operation_account(local_world)
    assert local_world.operation_account["status"] == "verified"
    synthetic_rules(monkeypatch, {"leaf": node})
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == "unknown"


@pytest.mark.parametrize("node", [synthetic_node(level=10), synthetic_node(rank="SyntheticRank")])
def test_unverified_level_or_rank_requirement_is_unknown_even_with_an_owned_talent(local_world, monkeypatch, node):
    synthetic_rules(monkeypatch, {"leaf": node})
    attach_account(local_world, {"leaf": 1})
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC")[0] == "unknown"


def test_caravan_has_no_verified_entrance_and_missing_mission_rule_is_unknown(local_world):
    attach_account(local_world, {"Prospect_Pro_Story_6": 999})
    assert eligibility.operation_talent_gate(local_world, "PRO_D_Transport")[0] == "unlisted"
    assert eligibility.operation_talent_gate(local_world, "SYNTHETIC_MISSING_RULE")[0] == "unknown"


def test_packaged_retry_contract_matches_the_verified_native_build():
    rules = eligibility.load_operation_rules()
    assert rules["source_build"] == "26313227"
    assert rules["native_contract"]["retry_seconds"] == 157680000
    assert rules["native_contract"]["retry_excluded_outcomes"] == [2, 3]
    assert rules["native_contract"]["prerequisite_combination"] == "any_positive_rank"


@pytest.mark.parametrize("outcome", [2, 3])
def test_abandoned_or_failed_history_does_not_need_end_time_or_create_retry_lock(local_world, outcome):
    history = [{"Mission": "PRO_A_Stockpile", "Status": outcome}]
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == "available"


@pytest.mark.parametrize("outcome", [0, 1])
@pytest.mark.parametrize("seconds_remaining, expected", [(37, "locked"), (0, "available"), (-1, "available")])
def test_native_retry_deadline_includes_in_progress_and_completed_outcomes(local_world, outcome, seconds_remaining, expected):
    retry_seconds = eligibility.load_operation_rules()["native_contract"]["retry_seconds"]
    end = 600
    elapsed = end + retry_seconds - seconds_remaining
    local_world.raw_records = [{"GameModeRecord": {"LevelTimeElapsedSec": elapsed}}]
    history = [{"Mission": "PRO_A_Stockpile", "Status": outcome, "MissionEndTime": end}]
    before = deepcopy(history)
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == expected
    assert history == before


def test_negative_end_time_uses_current_world_elapsed_time_and_still_locks(local_world):
    local_world.raw_records = [{"GameModeRecord": {"LevelTimeElapsedSec": 1000}}]
    history = [{"Mission": "PRO_A_Stockpile", "Status": 0, "MissionEndTime": -1}]
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == "locked"


@pytest.mark.parametrize("history", [None, {}, "missing-history"])
def test_unreadable_history_does_not_mean_no_retry_lock(local_world, history):
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == "unknown"


@pytest.mark.parametrize("history", [[None], ["corrupt-entry"]])
def test_malformed_history_entry_is_unknown_and_does_not_crash(local_world, history):
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == "unknown"


@pytest.mark.parametrize("entry, elapsed", [
    ({"Status": True, "MissionEndTime": 0}, 10),
    ({"Status": 9, "MissionEndTime": 0}, 10),
    ({"Status": 1}, 10),
    ({"Status": 1, "MissionEndTime": float("nan")}, 10),
    ({"Status": 1, "MissionEndTime": True}, 10),
    ({"Status": 1, "MissionEndTime": 0}, None),
    ({"Status": 1, "MissionEndTime": 0}, float("inf")),
])
def test_unknown_retry_outcome_or_time_is_not_treated_as_elapsed(local_world, entry, elapsed):
    local_world.raw_records = [{"GameModeRecord": {"LevelTimeElapsedSec": elapsed}}]
    history = [dict(entry, Mission="PRO_A_Stockpile")]
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == "unknown"


def test_empty_or_unrelated_world_history_does_not_apply_another_missions_lock(local_world):
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", [])[0] == "available"
    other = [{"Mission": "PRO_Story_6", "Status": 1, "MissionEndTime": 999}]
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", other)[0] == "available"
    assert eligibility.operation_retry_gate(local_world, "SYNTHETIC_MISSING_RULE", [])[0] == "unknown"


def test_abandoning_later_does_not_erase_an_earlier_unexpired_world_lock(local_world):
    local_world.raw_records = [{"GameModeRecord": {"LevelTimeElapsedSec": 1000}}]
    history = [{"Mission": "PRO_A_Stockpile", "Status": 2},
               {"Mission": "PRO_A_Stockpile", "Status": 1, "MissionEndTime": 900}]
    assert eligibility.operation_retry_gate(local_world, "PRO_A_Stockpile", history)[0] == "locked"


@pytest.mark.parametrize("state_flags", [["Mission_In_Progress"], ["Other", "Mission_In_Progress"]])
def test_world_session_operation_flag_blocks_starting_a_second_operation(local_world, state_flags):
    local_world.mission_state = {"session_flags": state_flags}
    assert eligibility.operation_start_block(local_world)


def test_operation_flag_can_be_read_from_raw_world_record_without_cached_state(local_world):
    local_world.raw_records = [{"GameModeRecord": {"SessionFlagRecords": ["Mission_In_Progress"]}}]
    assert eligibility.operation_start_block(local_world)
    local_world.raw_records = [{"GameModeRecord": {"SessionFlagRecords": ["Mission_Resupply_Available"]}}]
    assert eligibility.operation_start_block(local_world) == ""


@pytest.mark.parametrize("delay, blocked", [(30, True), (0, False), (-1, False), (True, False), ("30", False)])
def test_operation_device_delay_is_separate_from_single_operation_flag(local_world, delay, blocked):
    local_world.mission_state = {"session_flags": [], "quest_manager": {"DynamicQuestDelay": delay}}
    assert bool(eligibility.operation_start_block(local_world)) is blocked
