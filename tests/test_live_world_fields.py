"""Synthetic UE objects exercise optional fields without opening a process."""
from __future__ import annotations

from copy import deepcopy
import struct

import pytest

from icarus_helper import live_animal_stats as live
from icarus_helper import live_world_fields as fields
from test_live_animal_stats import BASE, fixture_world


def register(memory, objects):
    objects = list(dict.fromkeys(objects))
    chunks, items = memory.allocate(), memory.allocate(max(0x1000, len(objects) * 24))
    memory.pointer(chunks, items)
    for index, obj in enumerate(objects):
        memory.write(obj + 0xC, struct.pack("<i", index))
        memory.write(items + index * 24, struct.pack("<Qiiii", obj, 0, 0, index + 5, 0))
    memory.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", len(objects)))
    memory.pointer(BASE + live.GOBJECT_CHUNKS, chunks)
    return items


def animal_fixture():
    memory, snapshot, _, a = fixture_world()
    memory.class_for(["Object", "ActorComponent"])
    memory.pointer(a["state"] + 0x10, memory.class_for(
        ["Object", "ActorComponent", "CharacterState", "SurvivalCharacterState"]))
    root = memory.object("Capsule", ["Object", "ActorComponent", "SceneComponent", "CapsuleComponent"], a["actor"])
    memory.pointer(a["actor"] + fields.ROOT_COMPONENT, root)
    memory.write(root + fields.COMPONENT_WORLD_TRANSLATION, struct.pack("<fff", 125.5, -210.25, 3000.0))
    memory.write(a["world"] + fields.WORLD_ORIGIN, struct.pack("<iii", 0, 0, 0))
    for channel, value, maximum in [("food", 274, 325), ("water", 99, 401), ("oxygen", 0, 201)]:
        current_offset, maximum_offset = fields.ANIMAL_SUPPLIES[channel]
        memory.write(a["state"] + current_offset, struct.pack("<i", value))
        memory.write(a["state"] + maximum_offset, struct.pack("<i", maximum))
    a["root"] = root
    a["objects"] = [a[key] for key in ("actor", "recorder", "state", "root")]
    a["items"] = register(memory, a["objects"])
    return memory, snapshot, a


def view_and_identity(memory, snapshot):
    return live._View(memory, BASE), live._identity(snapshot)


def test_animal_supplies_read_actual_current_and_maximum_without_catalog_guesses():
    memory, snapshot, a = animal_fixture()
    view, identity = view_and_identity(memory, snapshot)
    actual = fields.read_animal_fields(view, a["actor"], a["state"], identity)
    assert actual == {"food": {"value": 274, "maximum": 325},
                      "water": {"value": 99, "maximum": 401},
                      "oxygen": {"value": 0, "maximum": 201},
                      "position": [125.5, -210.25, 3000.0]}


def test_current_above_maximum_preserves_both_native_values():
    memory, snapshot, a = animal_fixture()
    memory.write(a["state"] + 0x330, struct.pack("<i", 400))
    view, identity = view_and_identity(memory, snapshot)
    assert fields.read_animal_fields(view, a["actor"], a["state"], identity)["food"] == {"value": 400, "maximum": 325}


@pytest.mark.parametrize("bad_maximum", [0, -1, 1_000_000_001])
def test_invalid_single_supply_is_omitted_without_losing_other_fields(bad_maximum):
    memory, snapshot, a = animal_fixture()
    memory.write(a["state"] + 0x348, struct.pack("<i", bad_maximum))
    view, identity = view_and_identity(memory, snapshot)
    result = fields.read_animal_fields(view, a["actor"], a["state"], identity)
    assert "food" not in result
    assert "water" in result and "oxygen" in result and "position" in result


def test_unknown_state_class_does_not_guess_supply_offsets():
    memory, snapshot, a = animal_fixture()
    memory.pointer(a["state"] + 0x10, memory.class_for(["Object", "UnknownState"]))
    view, identity = view_and_identity(memory, snapshot)
    assert fields.read_animal_fields(view, a["actor"], a["state"], identity) == {"position": [125.5, -210.25, 3000.0]}


def test_missing_optional_fixture_data_does_not_raise_or_fabricate_zero():
    memory, snapshot, _, a = fixture_world()
    view, identity = view_and_identity(memory, snapshot)
    assert fields.read_animal_fields(view, a["actor"], a["state"], identity) == {}


def test_relative_location_is_not_used_as_world_translation():
    memory, snapshot, a = animal_fixture()
    memory.write(a["root"] + 0x11C, struct.pack("<fff", 9999., 8888., 7777.))
    view, identity = view_and_identity(memory, snapshot)
    assert fields.actor_position(view, a["actor"], identity) == [125.5, -210.25, 3000.0]


@pytest.mark.parametrize("origin", [(1, 0, 0), (0, -400000, 0), (0, 0, 5)])
def test_nonzero_world_origin_is_explicitly_unsupported_without_false_position(origin):
    memory, snapshot, a = animal_fixture()
    memory.write(a["world"] + fields.WORLD_ORIGIN, struct.pack("<iii", *origin))
    view, identity = view_and_identity(memory, snapshot)
    with pytest.raises(fields.FieldUnavailable, match="world origin"):
        fields.actor_position(view, a["actor"], identity)
    assert "position" not in fields.read_animal_fields(view, a["actor"], a["state"], identity)


def test_continuously_moving_position_is_omitted_but_supplies_survive():
    memory, snapshot, a = animal_fixture()
    original, sequence = memory.read, 0
    def changing(address, size):
        nonlocal sequence
        if address == a["root"] + fields.COMPONENT_WORLD_TRANSLATION:
            sequence += 1
            return struct.pack("<fff", float(sequence), 10., 20.)
        return original(address, size)
    memory.read = changing
    view, identity = view_and_identity(memory, snapshot)
    result = fields.read_animal_fields(view, a["actor"], a["state"], identity)
    assert "position" not in result and "food" in result


def test_read_budget_failure_always_propagates_beyond_optional_fields():
    memory, snapshot, a = animal_fixture()
    original = memory.read
    def limited(address, size):
        if address == a["state"] + 0x330:
            raise live.BudgetExceeded("synthetic budget")
        return original(address, size)
    memory.read = limited
    view, identity = view_and_identity(memory, snapshot)
    with pytest.raises(live.BudgetExceeded):
        fields.read_animal_fields(view, a["actor"], a["state"], identity)


def test_changed_state_generation_discards_all_supply_pairs():
    memory, snapshot, a = animal_fixture()
    original, changed = memory.read, False
    state_item = a["items"] + 2 * 24
    def changing(address, size):
        nonlocal changed
        if not changed and address == a["state"] + 0x330:
            changed = True
            memory.write(state_item + 16, struct.pack("<i", 999))
        return original(address, size)
    memory.read = changing
    view, identity = view_and_identity(memory, snapshot)
    result = fields.read_animal_fields(view, a["actor"], a["state"], identity)
    assert set(result) == {"position"}


def player_fixture():
    memory, snapshot, a = animal_fixture()
    memory.class_for(["Object", "Actor", "Pawn"])
    memory.class_for(["Object", "Actor", "Pawn", "Character"])
    player_state = memory.object("PlayerState", ["Object", "Actor", "PlayerState", "IcarusPlayerState"], a["level"])
    pawn = memory.object("PlayerPawn", ["Object", "Actor", "Pawn", "Character", "IcarusCharacter", "IcarusPlayerCharacter"], a["level"])
    root = memory.object("PlayerCapsule", ["Object", "ActorComponent", "SceneComponent", "CapsuleComponent"], pawn)
    memory.string(player_state + fields.PLAYER_IDENTITY, "synthetic-private-account")
    memory.write(player_state + fields.PLAYER_IDENTITY + 0x10, struct.pack("<i", 1))
    memory.pointer(player_state + fields.PLAYER_PAWN, pawn)
    memory.pointer(pawn + fields.PAWN_PLAYER_STATE, player_state)
    memory.pointer(pawn + fields.ROOT_COMPONENT, root)
    memory.write(root + fields.COMPONENT_WORLD_TRANSLATION, struct.pack("<fff", -1234., 5678., 9000.))
    raw = {"_component": "/Script/Icarus.PlayerStateRecorderComponent",
           "PlayerCharacterID": {"PlayerID": "synthetic-private-account", "ChrSlot": 1}}
    snapshot["players"] = [{"id": "saved-player-1", "name": "同名Player", "raw": raw}]
    snapshot["raw_records"] = [raw]
    a.update(player_state=player_state, pawn=pawn, player_root=root)
    a["objects"] += [player_state, pawn, root]
    a["items"] = register(memory, a["objects"])
    return memory, snapshot, a


def test_exact_snapshot_identity_returns_only_anonymous_key_saved_name_position():
    memory, snapshot, a = player_fixture()
    view, identity = view_and_identity(memory, snapshot)
    index = fields.snapshot_player_index(snapshot)
    result = fields.read_player_fields(view, a["player_state"], identity, index)
    assert result == {"id": fields.player_key(("synthetic-private-account", 1)),
                      "name": "同名Player", "position": [-1234., 5678., 9000.]}
    assert "synthetic-private-account" not in repr(result)
    assert fields.player_world(view, a["player_state"], identity) == a["world"]


def test_same_account_different_slot_does_not_match_same_display_name():
    memory, snapshot, a = player_fixture()
    snapshot["players"][0]["raw"]["PlayerCharacterID"]["ChrSlot"] = 2
    view, identity = view_and_identity(memory, snapshot)
    assert fields.read_player_fields(view, a["player_state"], identity, fields.snapshot_player_index(snapshot)) is None
    assert fields.player_key(("synthetic-private-account", 1)) != fields.player_key(("synthetic-private-account", 2))


def test_player_absent_from_selected_snapshot_is_never_emitted():
    memory, snapshot, a = player_fixture()
    snapshot["players"], snapshot["raw_records"] = [], []
    view, identity = view_and_identity(memory, snapshot)
    assert fields.read_player_fields(view, a["player_state"], identity, fields.snapshot_player_index(snapshot)) is None


@pytest.mark.parametrize("bad_link", ["pawn_backlink", "world", "root_owner"])
def test_player_requires_bidirectional_pawn_and_same_world(bad_link):
    memory, snapshot, a = player_fixture()
    if bad_link == "pawn_backlink": memory.pointer(a["pawn"] + fields.PAWN_PLAYER_STATE, a["actor"])
    elif bad_link == "world": memory.write(a["game_state"] + 0x360, struct.pack("<i", 9988))
    else: memory.pointer(a["player_root"] + 0x20, a["actor"])
    view, identity = view_and_identity(memory, snapshot)
    with pytest.raises(ValueError):
        fields.read_player_fields(view, a["player_state"], identity, fields.snapshot_player_index(snapshot))


def test_duplicate_saved_identity_is_excluded_instead_of_combined():
    _, snapshot, _ = player_fixture()
    snapshot["raw_records"].append(deepcopy(snapshot["raw_records"][0]))
    assert fields.snapshot_player_index(snapshot) == {}


def test_distinct_same_name_players_keep_separate_keys():
    _, snapshot, _ = player_fixture()
    second = deepcopy(snapshot["players"][0])
    second["id"] = "saved-player-2"
    second["raw"]["PlayerCharacterID"]["ChrSlot"] = 2
    snapshot["players"].append(second)
    snapshot["raw_records"].append(second["raw"])
    index = fields.snapshot_player_index(snapshot)
    assert len(index) == 2
    assert len({row["id"] for row in index.values()}) == 2
    assert {row["name"] for row in index.values()} == {"同名Player"}


def test_platform_identity_accidentally_used_as_display_name_is_hidden():
    memory, snapshot, a = player_fixture()
    snapshot["players"][0]["name"] = "synthetic-private-account"
    index = fields.snapshot_player_index(snapshot)
    assert index[("synthetic-private-account", 1)]["name"] == "Player"
    view, identity = view_and_identity(memory, snapshot)
    result = fields.read_player_fields(view, a["player_state"], identity, index)
    assert result["name"] == "Player" and "synthetic-private-account" not in repr(result)


def test_same_identity_in_multiple_saved_resources_is_ambiguous_even_with_equal_ids():
    _, snapshot, _ = player_fixture()
    snapshot["players"].append(deepcopy(snapshot["players"][0]))
    assert fields.snapshot_player_index(snapshot) == {}


@pytest.mark.parametrize("identity", [("", 0), ("synthetic", True), ("synthetic", -1), (None, 0), (" synthetic", 0)])
def test_invalid_identity_does_not_create_a_player_key(identity):
    with pytest.raises(fields.FieldUnavailable):
        fields.player_key(identity)
