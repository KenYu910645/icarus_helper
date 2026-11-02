"""Column grouping uses a private-safe character identity, never a display name."""
import json

import pytest

from icarus_helper.animals import _owner_key, summarize_animals


def record(player="SYNTHETIC_PRIVATE_PLATFORM_A", slot=0, name="Same Display Name", uid=1):
    return {"_component": "/Script/Icarus.IcarusMountCharacterRecorderComponent",
            "ActorStateRecorderVersion": 3, "IcarusActorGUID": uid,
            "OwnerCharacterID": {"PlayerID": player, "ChrSlot": slot}, "OwnerName": name,
            "BoolVariables": [{"VariableName": "bIsWildTame", "bVariable": False}],
            "AISetupRowName": "Mount_Moa"}


def test_same_character_has_stable_key_across_animal_and_display_name_changes():
    assert _owner_key(record()) == _owner_key(record(name="Renamed", uid=2))
    assert _owner_key(record()).startswith("owner:")
    assert len(_owner_key(record())) == 70


def test_same_display_names_belonging_to_different_accounts_or_slots_stay_separate():
    keys = {_owner_key(record()), _owner_key(record(slot=1)),
            _owner_key(record(player="SYNTHETIC_PRIVATE_PLATFORM_B"))}
    assert len(keys) == 3


@pytest.mark.parametrize("identity", [None, {}, {"PlayerID": "0", "ChrSlot": 0},
    {"PlayerID": "null", "ChrSlot": 0}, {"PlayerID": " ", "ChrSlot": 0},
    {"PlayerID": "Private", "ChrSlot": -1}, {"PlayerID": "Private", "ChrSlot": True},
    {"PlayerID": "Private", "ChrSlot": "0"}, {"PlayerID": 123, "ChrSlot": 0}])
def test_unconfirmed_owner_uses_explicit_unknown_group(identity):
    assert _owner_key({"OwnerCharacterID": identity, "OwnerName": "Unconfirmed"}) == "unknown"


def test_summary_contains_only_hash_and_character_name_with_no_platform_identifier():
    result = summarize_animals({"raw_records": [record(), record(slot=1, uid=2)],
                               "world_id": "synthetic-owner-groups", "map_name": "Prometheus"})
    assert len(result["animals"]) == 2
    assert len({a["owner_key"] for a in result["animals"]}) == 2
    assert all(a["owner_name"] == "Same Display Name" for a in result["animals"])
    rendered = json.dumps(result)
    assert "SYNTHETIC_PRIVATE_PLATFORM_A" not in rendered
    assert "OwnerCharacterID" not in rendered and "PlayerID" not in rendered


def test_explicit_tame_with_no_owner_keeps_name_and_group_unknown():
    juvenile = record()
    juvenile.update(_component="/Script/Icarus.IcarusJuvenileRecorderComponent",
                    OwnerCharacterID={}, OwnerName="Stale unconfirmed name", TamedState="ETamedState::Tamed")
    result = summarize_animals({"raw_records": [juvenile], "world_id": "synthetic-owner-groups",
                               "map_name": "Prometheus"})
    assert len(result["animals"]) == 1
    assert result["animals"][0]["owner_name"] == "未知"
    assert result["animals"][0]["owner_key"] == "unknown"
