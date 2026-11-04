"""Synthetic UE memory: exact world/identity gates and cached UI getter values."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import time

import pytest

from icarus_helper import live_animal_stats as live


BASE = 0x140000000


class Memory:
    def __init__(self):
        self.data, self.next, self.names, self.classes = {}, 0x20000000, {}, {}
        self.pool_block = self.allocate(0x10000)
        self.cursor = 0
        self.write(BASE + live.FNAME_READY, b"\x01")
        self.write(BASE + live.FNAME_POOL + 8, struct.pack("<II", 0, 0x10000))
        self.write(BASE + live.FNAME_POOL + 0x10, struct.pack("<Q", self.pool_block))

    def allocate(self, size=0x1000):
        result = self.next
        self.next += size
        self.write(result, bytes(size))
        return result

    def write(self, address, data):
        self.data.update({address + index: byte for index, byte in enumerate(data)})

    def read(self, address, size):
        try: return bytes(self.data[address + index] for index in range(size))
        except KeyError: raise live.Unavailable("synthetic unmapped read")

    def pointer(self, address, pointer): self.write(address, struct.pack("<Q", pointer))

    def fname(self, name):
        if name not in self.names:
            offset = self.cursor
            encoded = name.encode("ascii")
            self.write(self.pool_block + offset, struct.pack("<H", len(encoded) << 6) + encoded)
            self.cursor = (offset + 2 + len(encoded) + 1) & ~1
            self.names[name] = offset // 2
        return struct.pack("<II", self.names[name], 0)

    def class_for(self, chain):
        name = chain[-1]
        if name not in self.classes:
            parent = self.class_for(chain[:-1]) if len(chain) > 1 else None
            cls = self.allocate()
            self.classes[name] = cls
            self.write(cls + 0x18, self.fname(name))
            ancestors = [self.classes[item] + 0x30 for item in chain]
            array = self.allocate()
            self.write(array, struct.pack("<" + "Q" * len(chain), *ancestors))
            self.write(cls + 0x30, struct.pack("<Qi", array, len(chain) - 1))
        return self.classes[name]

    def object(self, name, chain, outer=0):
        obj = self.allocate()
        self.pointer(obj + 0x10, self.class_for(chain))
        self.write(obj + 0x18, self.fname(name))
        self.pointer(obj + 0x20, outer)
        return obj

    def string(self, address, value):
        raw = value.encode("utf-16le") + b"\0\0"
        pointer = self.allocate()
        self.write(pointer, raw)
        self.write(address, struct.pack("<Qii", pointer, len(raw) // 2, len(raw) // 2))


def fixture_world():
    memory = Memory()
    info = {"ClaimedAccountID": "synthetic-account", "ProspectID": "synthetic-world", "ProspectDTKey": "OpenWorld_Prometheus"}
    seed = 1234
    world_id = hashlib.sha256(f"{info['ClaimedAccountID']}|{info['ProspectID']}|{info['ProspectDTKey']}|{seed}".encode()).hexdigest()[:24]
    snapshot = {"world_id": world_id, "prospect_info": info, "game_state_seed": seed, "map_key": "Terrain_019"}
    world = memory.object("Terrain_019_DLC", ["Object", "World"])
    level = memory.object("PersistentLevel", ["Object", "Level"], world)
    memory.pointer(level + 0xB8, world)
    game_state = memory.object("GameState", ["Object", "Actor", "IcarusGameStateSurvival"], level)
    memory.pointer(world + 0x120, game_state)
    memory.write(game_state + 0x360, struct.pack("<i", seed))
    for offset, key in [(0, "ProspectID"), (0x10, "ClaimedAccountID"), (0x28, "ProspectDTKey")]:
        memory.string(game_state + 0x498 + offset, info[key])
    actor = memory.object("BP_Mount_Moa_C_4", ["Object", "Actor", "IcarusCharacter", "BP_Mount_Moa_C"], level)
    recorder = memory.object("Recorder", ["Object", "ActorStateRecorderComponent", "IcarusMountCharacterRecorderComponent"], actor)
    memory.write(recorder + 0x140, struct.pack("<I", 77))
    state = memory.object("ActorState", ["Object", "CharacterState", "MountCharacterState"], actor)
    memory.write(state + 0x1D8, struct.pack("<ii", 1742, 1749))
    memory.write(state + 0x278, struct.pack("<ii", 453, 458))
    memory.pointer(actor + 0x578, recorder)
    memory.pointer(actor + 0x5A8, state)
    memory.write(actor + 0xC, struct.pack("<i", 0))
    chunks, items = memory.allocate(), memory.allocate()
    memory.pointer(chunks, items)
    memory.write(items, struct.pack("<Qiiii", actor, 0, 0, 2, 0))
    memory.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 1))
    memory.pointer(BASE + live.GOBJECT_CHUNKS, chunks)
    targets = {"77": {"class": "BP_Mount_Moa_C", "stamina_applicable": True}}
    return memory, snapshot, targets, {"actor": actor, "recorder": recorder, "state": state, "world": world, "level": level, "game_state": game_state, "items": items}


def test_reads_cached_native_ui_current_and_maximum_after_exact_world_gates():
    memory, snapshot, targets, addresses = fixture_world()
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["status"] == "ok" and result["world_verified"] is True
    assert result["animals"] == {"77": {"health": {"value": 1742, "maximum": 1749}, "stamina": {"value": 453, "maximum": 458}}}
    assert result["coverage"]["scan_complete"] is True
    assert "synthetic-account" not in str(result)


def test_legitimate_large_fname_instance_suffix_is_not_rejected():
    memory, snapshot, targets, a = fixture_world()
    original = memory.read(a["actor"] + 0x18, 8)
    memory.write(a["actor"] + 0x18, original[:4] + struct.pack("<I", 2**31))
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["status"] == "ok" and result["world_verified"]


def test_duplicate_guid_in_same_world_never_emits_either_actor():
    memory, snapshot, targets, a = fixture_world()
    actor = memory.object("Duplicate", ["Object", "Actor", "IcarusCharacter", "BP_Mount_Moa_C"], a["level"])
    recorder = memory.object("DuplicateRecorder", ["Object", "ActorStateRecorderComponent", "IcarusMountCharacterRecorderComponent"], actor)
    state = memory.object("DuplicateState", ["Object", "CharacterState", "MountCharacterState"], actor)
    memory.pointer(actor + 0x578, recorder)
    memory.pointer(actor + 0x5A8, state)
    memory.write(actor + 0xC, struct.pack("<i", 1))
    memory.write(recorder + 0x140, struct.pack("<I", 77))
    memory.write(state + 0x1D8, struct.pack("<ii", 1200, 2000))
    memory.write(state + 0x278, struct.pack("<ii", 200, 300))
    memory.write(a["items"] + 24, struct.pack("<Qiiii", actor, 0, 0, 3, 0))
    memory.write(BASE + live.GOBJECT_COUNT, struct.pack("<i", 2))
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["animals"] == {} and not result["world_verified"]
    assert result["coverage"]["duplicate_guids"] == 1


def test_changing_current_maximum_pair_is_not_a_stable_sample():
    memory, snapshot, targets, a = fixture_world()
    original = memory.read
    sequence = 0
    def changing(address, size):
        nonlocal sequence
        if address == a["state"] + 0x1D8 and size == 8:
            sequence += 1
            return struct.pack("<ii", sequence, 2000)
        return original(address, size)
    memory.read = changing
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["animals"] == {} and not result["world_verified"]


@pytest.mark.parametrize("field_offset,value", [(0, 0x12345678), (8, 0x20000000), (16, 9)])
def test_actor_item_pointer_flags_and_serial_are_rechecked(field_offset, value):
    memory, snapshot, targets, a = fixture_world()
    original = memory.read
    item_reads = 0
    def changed(address, size):
        nonlocal item_reads
        if address == a["items"] and size == live.ITEM_SIZE:
            item_reads += 1
            data = bytearray(original(address, size))
            if item_reads > 1:
                struct.pack_into("<Q" if field_offset == 0 else "<I", data, field_offset, value)
            return bytes(data)
        return original(address, size)
    memory.read = changed
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["animals"] == {} and not result["world_verified"]


def test_budget_exhaustion_in_an_object_aborts_the_entire_scan():
    memory, snapshot, targets, a = fixture_world()
    original = memory.read
    def exhausted(address, size):
        if address == a["state"] + 0x1D8:
            raise live.BudgetExceeded("synthetic byte budget")
        return original(address, size)
    memory.read = exhausted
    with pytest.raises(live.BudgetExceeded, match="synthetic byte budget"):
        live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)


@pytest.mark.parametrize("field", ["ProspectID", "ClaimedAccountID", "ProspectDTKey", "seed", "map", "guid", "class", "recorder_owner", "state_owner"])
def test_rejects_wrong_world_or_ambiguous_animal_identity(field):
    memory, snapshot, targets, a = fixture_world()
    if field in ("ProspectID", "ClaimedAccountID", "ProspectDTKey"):
        offset = {"ProspectID": 0, "ClaimedAccountID": 0x10, "ProspectDTKey": 0x28}[field]
        memory.string(a["game_state"] + 0x498 + offset, "different")
    elif field == "seed": memory.write(a["game_state"] + 0x360, struct.pack("<i", 999))
    elif field == "map": memory.write(a["world"] + 0x18, memory.fname("Terrain_016"))
    elif field == "guid": memory.write(a["recorder"] + 0x140, struct.pack("<I", 78))
    elif field == "class": targets["77"]["class"] = "BP_Mount_Horse_C"
    elif field == "recorder_owner": memory.pointer(a["recorder"] + 0x20, a["world"])
    elif field == "state_owner": memory.pointer(a["state"] + 0x20, a["world"])
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["animals"] == {} and result["world_verified"] is False


def test_over_capacity_and_zero_are_preserved_without_changing_denominator():
    memory, snapshot, targets, a = fixture_world()
    memory.write(a["state"] + 0x1D8, struct.pack("<ii", 120, 100))
    memory.write(a["state"] + 0x278, struct.pack("<ii", 0, 200))
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert result["animals"]["77"]["health"] == {"value": 120, "maximum": 100}
    assert result["animals"]["77"]["stamina"] == {"value": 0, "maximum": 200}


def test_unsupported_stamina_applicability_does_not_emit_placeholder_capacity():
    memory, snapshot, targets, a = fixture_world()
    targets["77"]["stamina_applicable"] = False
    memory.write(a["state"] + 0x278, struct.pack("<ii", 0, 1))
    result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
    assert "health" in result["animals"]["77"] and "stamina" not in result["animals"]["77"]


def test_pending_kill_and_invalid_object_indices_are_not_sampled():
    for pending in (True, False):
        memory, snapshot, targets, a = fixture_world()
        if pending: memory.write(a["items"] + 8, struct.pack("<I", 0x20000000))
        else: memory.write(a["actor"] + 0xC, struct.pack("<i", 99))
        result = live._read_snapshot_from_memory(snapshot, memory, BASE, targets=targets)
        assert result["animals"] == {} and result["world_verified"] is False


def test_no_process_branch_does_not_open_memory_or_start_game(monkeypatch):
    _, snapshot, _, _ = fixture_world()
    monkeypatch.setattr(live, "_find_game_processes", lambda: [])
    monkeypatch.setattr(live, "_WindowsMemory", lambda pid, **kwargs: pytest.fail("No process handle should be opened"))
    result = live.read_live_animal_stats(snapshot)
    assert result["status"] == "not_running" and not result["world_verified"] and not result["animals"]


def test_exact_executable_hash_gate_runs_before_opening_memory(monkeypatch):
    _, snapshot, _, _ = fixture_world()
    monkeypatch.setattr(live, "_find_game_processes", lambda: [123])
    monkeypatch.setattr(live, "_module", lambda pid: (Path("synthetic.exe"), BASE))
    def reject(path): raise live.Unavailable("unverified executable")
    monkeypatch.setattr(live, "_validate_executable", reject)
    monkeypatch.setattr(live, "_WindowsMemory", lambda pid, **kwargs: pytest.fail("No read before version gate"))
    result = live.read_live_animal_stats(snapshot)
    assert result["status"] == "unavailable" and result["reason"] == "unverified executable"


def test_handle_is_closed_after_success_or_decode_failure(monkeypatch):
    _, snapshot, _, _ = fixture_world()
    monkeypatch.setattr(live, "_find_game_processes", lambda: [123])
    monkeypatch.setattr(live, "_module", lambda pid: (Path("synthetic.exe"), BASE))
    monkeypatch.setattr(live, "_validate_executable", lambda path: None)
    class Handle:
        closed = False
        creation_time = 123456
        bytes = calls = 0
        def __init__(self, pid): self.started = time.monotonic()
        def close(self): self.closed = True
    for failing in (True, False):
        handle = Handle(123)
        monkeypatch.setattr(live, "_WindowsMemory", lambda pid, **kwargs: handle)
        def decode(*args, **kwargs):
            if failing: raise live.Unavailable("synthetic unstable")
            return live._result("ok", "synthetic")
        monkeypatch.setattr(live, "_read_snapshot_from_memory", decode)
        result = live.read_live_animal_stats(snapshot)
        assert result["status"] == ("unavailable" if failing else "ok")
        assert handle.closed


def test_snapshot_world_id_must_match_its_raw_prospect_identity(monkeypatch):
    _, snapshot, _, _ = fixture_world()
    snapshot["world_id"] = "wrong-world"
    monkeypatch.setattr(live, "_find_game_processes", lambda: pytest.fail("Invalid snapshot must stop before process lookup"))
    result = live.read_live_animal_stats(snapshot)
    assert result["status"] == "unavailable" and result["animals"] == {}
