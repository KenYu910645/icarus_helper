"""Locator caching must reduce reads without trusting any old gameplay value."""
from dataclasses import asdict
from types import SimpleNamespace
import struct

import pytest

from icarus_helper import live_animal_stats as live
from icarus_helper.live_actor_cache import ActorLocatorCache
from test_live_animal_stats import BASE, fixture_world


PROCESS = (123, 456, "synthetic.exe", BASE, live.BUILD, live.EXE_HASH)


def setup_cache(monkeypatch):
    memory, snapshot, targets, addresses = fixture_world()
    # Optional native fields have their own memory tests; isolate the cache here.
    monkeypatch.setattr(live, "_world_helpers", lambda snapshot: (None, {}))
    now = [100.0]
    cache = ActorLocatorCache(clock=lambda: now[0])
    return memory, snapshot, targets, addresses, cache, now


def read(memory, snapshot, targets, cache, process=PROCESS):
    return live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets,
                                           cache=cache, process_key=process)


def test_warm_reads_new_values_and_does_not_store_gameplay_values(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    cold = read(m, s, t, cache)
    assert cold["coverage"]["cache_mode"] == "full"
    assert cache.entry is not None
    assert not {"value", "maximum", "health", "stamina"}.intersection(str(asdict(cache.entry)).split("'"))
    m.write(a["state"] + 0x1D8, struct.pack("<ii", 0, 999))
    m.write(a["state"] + 0x278, struct.pack("<ii", 120, 100))
    now[0] += 30
    warm = read(m, s, t, cache)
    assert warm["coverage"]["cache_mode"] == "warm"
    assert warm["coverage"]["scan_complete"] is False
    assert warm["coverage"]["locator_revalidation_complete"] is True
    assert warm["animals"]["77"] == {"health": {"value": 0, "maximum": 999},
                                      "stamina": {"value": 120, "maximum": 100}}


def test_full_inventory_expires_without_warm_reads_extending_it(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    now[0] = 399
    assert read(m, s, t, cache)["coverage"]["cache_mode"] == "warm"
    assert cache.entry.created == 100
    now[0] = 400
    assert read(m, s, t, cache)["coverage"]["cache_mode"] == "full"
    assert cache.entry.created == 400
    # A monotonic clock reset must also discard an old locator set.
    now[0] = 399
    assert read(m, s, t, cache)["coverage"]["cache_mode"] == "full"


@pytest.mark.parametrize("applicable", [False, True])
def test_generic_oxygen_container_is_not_emitted_without_species_applicability(monkeypatch, applicable):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    t["77"]["oxygen_applicable"] = applicable
    fields = SimpleNamespace(candidate_classes=lambda: set(),
                             read_animal_fields=lambda *args: {"oxygen": {"value": 0, "maximum": 1}})
    monkeypatch.setattr(live, "_world_helpers", lambda snapshot: (fields, {}))
    result = read(m, s, t, cache)
    assert ("oxygen" in result["animals"]["77"]) is applicable
    assert "health" in result["animals"]["77"]


@pytest.mark.parametrize("part", range(len(PROCESS)))
def test_each_process_module_build_identity_part_forces_fresh_inventory(monkeypatch, part):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    changed = list(PROCESS)
    changed[part] = str(changed[part]) + "-changed"
    result = read(m, s, t, cache, tuple(changed))
    assert result["coverage"]["cache_mode"] == "full"


@pytest.mark.parametrize("offset,value", [(0, 0x12345678), (8, 0x20000000), (16, 99)])
def test_warm_revalidates_item_pointer_flags_and_serial_before_acceptance(monkeypatch, offset, value):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    m.write(a["items"] + offset, struct.pack("<Q" if offset == 0 else "<I", value))
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert result["coverage"]["cache_invalidated"] is True
    if offset != 16:
        assert result["animals"] == {} and cache.entry is None
    else:
        # A complete rescan can establish a new generation; the warm read cannot.
        assert result["world_verified"] and cache.entry.locators[0].item != struct.pack("<Qiiii", a["actor"], 0, 0, 2, 0)


@pytest.mark.parametrize("field", ["class", "guid", "recorder_owner", "state_owner", "seed", "world", "fname"])
def test_warm_rechecks_actor_components_identity_and_world(monkeypatch, field):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    if field == "class":
        m.pointer(a["actor"] + 0x10, m.class_for(["Object", "Actor", "IcarusCharacter", "DifferentAnimal"]))
    elif field == "guid":
        m.write(a["recorder"] + 0x140, struct.pack("<I", 78))
    elif field == "recorder_owner": m.pointer(a["recorder"] + 0x20, a["world"])
    elif field == "state_owner": m.pointer(a["state"] + 0x20, a["world"])
    elif field == "seed": m.write(a["game_state"] + 0x360, struct.pack("<i", 999))
    elif field == "world": m.write(a["world"] + 0x18, m.fname("Terrain_016"))
    elif field == "fname": m.write(a["actor"] + 0x18, m.fname("Default__BP_Mount_Moa_C"))
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert result["coverage"]["cache_invalidated"]
    assert not result["animals"] and cache.entry is None


def test_changed_valid_actor_state_is_remapped_by_full_inventory(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    state = m.object("ReplacementState", ["Object", "CharacterState", "MountCharacterState"], a["actor"])
    m.write(state + 0x1D8, struct.pack("<ii", 87, 101))
    m.write(state + 0x278, struct.pack("<ii", 0, 120))
    m.pointer(a["actor"] + 0x5A8, state)
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_invalidated"] and result["coverage"]["cache_mode"] == "full"
    assert result["animals"]["77"]["health"] == {"value": 87, "maximum": 101}
    assert cache.entry.locators[0].state == state


def test_new_requested_target_forces_full_scan_and_partial_results_are_not_cached(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    t["78"] = {"class": "BP_Mount_Moa_C", "stamina_applicable": True}
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert result["status"] == "partial" and result["coverage"]["missing_animals"] == 1
    assert cache.entry is None


def test_world_identity_change_cannot_reuse_same_map_locators(monkeypatch):
    import hashlib
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    s["prospect_info"]["ProspectID"] = "another-world-on-the-same-map"
    i = s["prospect_info"]
    s["world_id"] = hashlib.sha256(f"{i['ClaimedAccountID']}|{i['ProspectID']}|{i['ProspectDTKey']}|{s['game_state_seed']}".encode()).hexdigest()[:24]
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full"
    assert not result["animals"] and not result["world_verified"] and cache.entry is None


def test_new_non_candidate_slots_are_scanned_without_extending_inventory_age(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    m.write(a["items"] + live.ITEM_SIZE, bytes(live.ITEM_SIZE))
    now[0] += 30
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "warm" and result["coverage"]["object_slots_checked"] == 2
    assert result["coverage"]["incremental_slots_checked"] == 1
    assert cache.entry.object_count == 2 and cache.entry.created == 100
    assert read(m, s, t, cache)["coverage"]["incremental_slots_checked"] == 0


def test_new_candidate_slot_forces_full_duplicate_and_world_checks(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    actor = m.object("Duplicate", ["Object", "Actor", "IcarusCharacter", "BP_Mount_Moa_C"], a["level"])
    recorder = m.object("DuplicateRecorder", ["Object", "ActorStateRecorderComponent", "IcarusMountCharacterRecorderComponent"], actor)
    state = m.object("DuplicateState", ["Object", "CharacterState", "MountCharacterState"], actor)
    m.pointer(actor + 0x578, recorder)
    m.pointer(actor + 0x5A8, state)
    m.write(actor + 0xC, struct.pack("<i", 1))
    m.write(recorder + 0x140, struct.pack("<I", 77))
    m.write(state + 0x1D8, struct.pack("<ii", 1200, 2000))
    m.write(state + 0x278, struct.pack("<ii", 200, 300))
    m.write(a["items"] + live.ITEM_SIZE, struct.pack("<Qiiii", actor, 0, 0, 3, 0))
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full" and result["coverage"]["cache_invalidated"]
    assert result["coverage"]["duplicate_guids"] == 1
    assert not result["animals"] and cache.entry is None


def test_object_count_shrink_forces_complete_inventory(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    m.write(a["items"] + live.ITEM_SIZE, bytes(live.ITEM_SIZE))
    read(m, s, t, cache)
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 1))
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "full" and result["coverage"]["cache_invalidated"]


def test_incremental_budget_failure_cannot_emit_earlier_warm_samples(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    original = m.read
    def exhausted(address, size):
        if address == a["items"] and size == live.ITEM_SIZE * 2:
            raise live.BudgetExceeded("synthetic incremental budget")
        return original(address, size)
    m.read = exhausted
    with pytest.raises(live.BudgetExceeded, match="synthetic incremental budget"):
        read(m, s, t, cache)
    assert cache.entry is None


def test_warm_budget_failure_aborts_entire_result_and_discards_cache(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    original = m.read
    def exhausted(address, size):
        if address == a["state"] + 0x1D8:
            raise live.BudgetExceeded("synthetic warm budget")
        return original(address, size)
    m.read = exhausted
    with pytest.raises(live.BudgetExceeded, match="synthetic warm budget"):
        read(m, s, t, cache)
    assert cache.entry is None


def test_cache_requires_process_generation_before_any_memory_read(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    m.read = lambda *args: pytest.fail("Unsafe cache identity must stop before reads")
    with pytest.raises(live.Unavailable, match="generation"):
        read(m, s, t, cache, None)


def test_warm_cost_depends_on_targets_instead_of_all_object_headers(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    count = 500
    items = m.allocate(count * live.ITEM_SIZE)
    chunks = struct.unpack("<Q", m.read(BASE + live.GOBJECT_CHUNKS, 8))[0]
    m.pointer(chunks, items)
    m.write(items, m.read(a["items"], live.ITEM_SIZE))
    for slot in range(1, count):
        other = m.object("Unrelated", ["Object", "Unrelated"])
        m.write(other + 0xC, struct.pack("<i", slot))
        m.write(items + slot * live.ITEM_SIZE, struct.pack("<Qiiii", other, 0, 0, slot + 2, 0))
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", count))
    original = m.read
    metrics = {"bytes": 0, "calls": 0}
    def counted(address, size):
        metrics["bytes"] += size
        metrics["calls"] += 1
        return original(address, size)
    m.read = counted
    read(m, s, t, cache)
    cold = dict(metrics)
    metrics.update(bytes=0, calls=0)
    result = read(m, s, t, cache)
    assert result["coverage"]["cache_mode"] == "warm"
    assert result["coverage"]["object_generations_checked"] == count
    assert result["coverage"]["locator_revalidation_count"] == 1
    assert metrics["bytes"] < cold["bytes"] * 0.65
    assert metrics["calls"] < cold["calls"] / 3


def test_public_reader_clears_locators_when_game_stops(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    read(m, s, t, cache)
    monkeypatch.setattr(live, "_LOCATORS", cache)
    monkeypatch.setattr(live, "_find_game_processes", lambda: [])
    result = live.read_live_animal_stats(s)
    assert result["status"] == "not_running" and cache.entry is None


def test_player_inventory_shares_scan_and_offline_player_does_not_block_cache(monkeypatch):
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    player = m.object("OnlinePlayerState", ["Object", "Actor", "IcarusPlayerState"], a["level"])
    m.write(player + 0xC, struct.pack("<i", 1))
    m.write(a["items"] + live.ITEM_SIZE, struct.pack("<Qiiii", player, 0, 0, 7, 0))
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    player_index = {("synthetic-online", 0): {"id": "anonymous-online", "name": "線上玩家"},
                    ("synthetic-offline", 1): {"id": "anonymous-offline", "name": "離線玩家"}}
    position = [1.0, 2.0, 3.0]
    helpers = SimpleNamespace(candidate_classes=lambda: {"IcarusPlayerState"},
                              read_animal_fields=lambda *args: {},
                              player_world=lambda view, obj, identity: view.world(obj, identity),
                              read_player_fields=lambda *args: {"id": "anonymous-online", "name": "線上玩家", "position": list(position)})
    monkeypatch.setattr(live, "_world_helpers", lambda snapshot: (helpers, player_index))
    cold = read(m, s, t, cache)
    assert cold["coverage"]["object_slots_checked"] == 2 and cold["coverage"]["sampled_players"] == 1
    assert cache.entry is not None and len(cache.entry.locators) == 2
    position[:] = [4.0, 5.0, 6.0]
    warm = read(m, s, t, cache)
    assert warm["coverage"]["cache_mode"] == "warm"
    assert warm["players"]["anonymous-online"]["position"] == [4.0, 5.0, 6.0]
    assert warm["players"]["anonymous-online"]["sample_time"] == warm["sample_time"]
    player_index[("synthetic-new-target", 2)] = {"id": "new", "name": "新玩家"}
    assert read(m, s, t, cache)["coverage"]["cache_mode"] == "full"


def test_optional_helper_player_rejection_does_not_abort_animal_inventory(monkeypatch):
    from icarus_helper.live_world_fields import FieldUnavailable
    m, s, t, a, cache, now = setup_cache(monkeypatch)
    player = m.object("UnreadyPlayerState", ["Object", "Actor", "IcarusPlayerState"], a["level"])
    m.write(player + 0xC, struct.pack("<i", 1))
    m.write(a["items"] + 24, struct.pack("<Qiiii", player, 0, 0, 9, 0))
    m.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    def reject(*args): raise FieldUnavailable("synthetic player not ready")
    helpers = SimpleNamespace(candidate_classes=lambda: {"IcarusPlayerState"},
                              read_animal_fields=lambda *args: {}, player_world=reject)
    monkeypatch.setattr(live, "_world_helpers", lambda snapshot: (helpers, {("p", 0): {"id": "p", "name": "玩家"}}))
    result = read(m, s, t, cache)
    assert result["status"] == "ok" and result["animals"]["77"]["health"]["value"] == 1742
    assert result["players"] == {}
