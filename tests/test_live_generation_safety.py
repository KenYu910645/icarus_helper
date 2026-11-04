"""Reused object slots must not bypass duplicate or multiple-world gates."""
from types import SimpleNamespace
import struct

import pytest

from icarus_helper import live_animal_stats as live
from test_live_actor_cache import read, setup_cache
from test_live_animal_stats import BASE


def hole_inventory(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    m.write(a["items"] + 24, bytes(24))
    assert read(m, s, t, cache)["coverage"]["cache_mode"] == "full"
    return m, s, t, a, cache, now


def duplicate_animal(m, a, *, level=None):
    actor = m.object("ReusedSlotAnimal", ["Object", "Actor", "IcarusCharacter", "BP_Mount_Moa_C"], level or a["level"])
    recorder = m.object("ReusedRecorder", ["Object", "ActorStateRecorderComponent", "IcarusMountCharacterRecorderComponent"], actor)
    state = m.object("ReusedState", ["Object", "CharacterState", "MountCharacterState"], actor)
    m.pointer(actor + 0x578, recorder)
    m.pointer(actor + 0x5A8, state)
    m.write(actor + 0xC, struct.pack("<i", 1))
    m.write(recorder + 0x140, struct.pack("<I", 77))
    m.write(state + 0x1D8, struct.pack("<ii", 100, 200))
    m.write(state + 0x278, struct.pack("<ii", 30, 40))
    m.write(a["items"] + 24, struct.pack("<Qiiii", actor, 0, 0, 99, 0))
    return actor


def test_reused_old_hole_with_duplicate_guid_forces_full_and_emits_neither(monkeypatch):
    m, s, t, a, cache, now = hole_inventory(monkeypatch)
    duplicate_animal(m, a)
    now[0] += 30
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert result["coverage"]["cache_invalidated"] is True
    assert result["coverage"]["duplicate_guids"] == 1
    assert not result["animals"] and not result["world_verified"]
    assert cache.entry is None


def test_reused_old_slot_cannot_hide_second_world_with_identical_save_identity(monkeypatch):
    m, s, t, a, cache, now = hole_inventory(monkeypatch)
    world = m.object("Terrain_019_DLC", ["Object", "World"])
    level = m.object("SecondPersistentLevel", ["Object", "Level"], world)
    state = m.object("SecondGameState", ["Object", "Actor", "IcarusGameStateSurvival"], level)
    m.pointer(level + 0xB8, world)
    m.pointer(world + 0x120, state)
    m.write(state + 0x360, struct.pack("<i", s["game_state_seed"]))
    for offset, key in [(0, "ProspectID"), (0x10, "ClaimedAccountID"), (0x28, "ProspectDTKey")]:
        m.string(state + 0x498 + offset, s["prospect_info"][key])
    duplicate_animal(m, a, level=level)
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert not result["animals"] and not result["world_verified"]
    assert cache.entry is None


def test_reused_old_hole_with_same_player_key_discards_both_player_candidates(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    first = m.object("FirstPlayer", ["Object", "Actor", "IcarusPlayerState"], a["level"])
    m.write(first + 0xC, struct.pack("<i", 1))
    m.write(a["items"] + 24, struct.pack("<Qiiii", first, 0, 0, 7, 0))
    m.write(a["items"] + 48, bytes(24))
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 3))
    helpers = SimpleNamespace(candidate_classes=lambda: {"IcarusPlayerState"},
                              read_animal_fields=lambda *args: {},
                              player_world=lambda view, obj, identity: view.world(obj, identity),
                              read_player_fields=lambda *args: {"id": "anonymous-player", "name": "玩家", "position": [1, 2, 3]})
    monkeypatch.setattr(live, "_world_helpers", lambda snapshot: (helpers, {("private-test", 1): {"id": "anonymous-player", "name": "玩家"}}))
    assert read(m, s, t, cache)["players"]
    duplicate = m.object("DuplicatePlayer", ["Object", "Actor", "IcarusPlayerState"], a["level"])
    m.write(duplicate + 0xC, struct.pack("<i", 2))
    m.write(a["items"] + 48, struct.pack("<Qiiii", duplicate, 0, 0, 17, 0))
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert result["coverage"]["duplicate_players"] == 1 and result["players"] == {}
    assert result["animals"] and cache.entry is None


def test_unrelated_reused_slot_advances_metadata_without_extending_full_scan_age(monkeypatch):
    m, s, t, a, cache, now = hole_inventory(monkeypatch)
    unrelated = m.object("UnrelatedReplacement", ["Object", "Unrelated"])
    m.write(unrelated + 0xC, struct.pack("<i", 1))
    m.write(a["items"] + 24, struct.pack("<Qiiii", unrelated, 0, 0, 44, 0))
    original, header_reads = m.read, []
    def measured(address, size):
        if address == unrelated and size == 0x28:
            header_reads.append(address)
        return original(address, size)
    m.read = measured
    now[0] += 30
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "warm" and header_reads
    assert cache.entry.created == 100
    header_reads.clear()
    assert read(m, s, t, cache)["coverage"]["cache_mode"] == "warm"
    assert not header_reads and cache.entry.created == 100


def test_bulk_generation_budget_failure_discards_earlier_warm_values(monkeypatch):
    m, s, t, a, cache, now = hole_inventory(monkeypatch)
    original = m.read
    def exhausted(address, size):
        if address == a["items"] and size == 48:
            raise live.BudgetExceeded("synthetic metadata budget")
        return original(address, size)
    m.read = exhausted
    with pytest.raises(live.BudgetExceeded, match="synthetic metadata budget"):
        read(m, s, t, cache)
    assert cache.entry is None
