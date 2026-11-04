"""Build-bound, read-only cached animal stats; never starts or calls the game.

Offsets are verified against build 26313227's native getters/reflection metadata.
Synthetic decoding and one real selected world were verified read only. Values
have not yet been compared one by one against the visible game UI.
Only object locators/generation metadata are retained between samples. Every
sample checks all object generations and rereads values after exact world gates.
No raw memory, account identifiers or process addresses are returned.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time

from .live_actor_cache import ActorLocatorCache, ObjectLocator, FULL_SCAN_INTERVAL

BUILD = "26313227"
EXE_HASH = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
PROCESS_NAME = "Icarus-Win64-Shipping.exe"
GOBJECT_CHUNKS = 0x63AACF0
GOBJECT_COUNT = 0x63AAD04
FNAME_POOL = 0x636E980
FNAME_READY = 0x636E978
ITEM_SIZE = 24
CHUNK_SIZE = 65536
MAX_OBJECTS = 1_000_000
MAX_READ_BYTES = 64 * 1024 * 1024
MAX_READ_SECONDS = 12.0
_LOCATORS = ActorLocatorCache()


class Unavailable(ValueError):
    pass


class BudgetExceeded(Unavailable):
    """Abort the entire scan; incomplete duplicate detection is never success."""


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _result(status, reason):
    return {"status": status, "reason": reason, "sample_time": datetime.now(timezone.utc).isoformat(),
            "world_verified": False, "animals": {}, "players": {},
            "coverage": {"source": "Read-only game process state cache", "runtime_validation": "Native rules, synthetic memory, and selected-world read-only checks passed; individual fields have not been compared with the game UI",
                         "snapshot_positions_are_live": False}}


def _identity(snapshot):
    info = _get(snapshot, "prospect_info", {})
    seed, map_key = _get(snapshot, "game_state_seed"), _get(snapshot, "map_key", "")
    if not isinstance(info, dict) or not isinstance(seed, int) or isinstance(seed, bool) or not isinstance(map_key, str) or not map_key:
        raise Unavailable("Verifiable world identity, map, or seed is missing.")
    fields = tuple(info.get(key) for key in ("ClaimedAccountID", "ProspectID", "ProspectDTKey"))
    if any(not isinstance(value, str) or not value for value in fields):
        raise Unavailable("Verifiable world identity is missing.")
    expected = hashlib.sha256(("|".join(fields) + "|" + str(seed)).encode("utf-8")).hexdigest()[:24]
    if _get(snapshot, "world_id") != expected or _get(snapshot, "is_shared_map", False):
        raise Unavailable("World identity does not match the snapshot.")
    return (*fields, seed, map_key)


def _targets(snapshot):
    from .animals import ANIMAL_COMPONENTS, _identity as saved_identity, _ownership, load_animal_catalog
    from .animal_capacities import load_capacity_catalog
    species = load_animal_catalog().get("species", {})
    static, _ = load_capacity_catalog()
    rows, conflicts = {}, set()
    for record in _get(snapshot, "raw_records", []):
        if record.get("_component") not in ANIMAL_COMPONENTS or _ownership(record) not in ("owned", "explicit_tamed"):
            continue
        ident = saved_identity(record)
        definition = species.get(record.get("AISetupRowName"), {})
        actor_class = definition.get("actor_class", "")
        if not ident or not ident.isdigit() or not 0 < int(ident) < 2**32 or "." not in actor_class:
            continue
        capacity = static.get("species", {}).get(record.get("AISetupRowName"), {})
        row = {"class": actor_class.rsplit(".", 1)[-1],
               "stamina_applicable": bool(capacity.get("stamina_curve") or "stamina" in capacity.get("base_capacities", {})),
               "oxygen_applicable": "oxygen" in capacity.get("base_capacities", {})}
        if ident in rows:
            conflicts.add(ident)
        rows[ident] = row
    for ident in conflicts:
        rows.pop(ident, None)
    return rows


class _View:
    def __init__(self, memory, base):
        self.memory, self.base = memory, base
        self.names, self.classes = {}, {}
        if self.read(base + FNAME_READY, 1) != b"\x01":
            raise Unavailable("Game name index has not been initialized.")

    def read(self, address, size):
        if not isinstance(address, int) or not 0x10000 <= address < 0x800000000000 or not 0 < size <= 2 * 1024 * 1024:
            raise Unavailable("Game data address or size is invalid.")
        data = self.memory.read(address, size)
        if len(data) != size:
            raise Unavailable("Game data is not yet stable.")
        return data

    def u64(self, address): return struct.unpack("<Q", self.read(address, 8))[0]
    def i32(self, address): return struct.unpack("<i", self.read(address, 4))[0]

    def fname(self, raw):
        index, number = struct.unpack("<II", raw)
        if index not in self.names:
            block, offset = index >> 16, (index & 65535) * 2
            current, cursor = struct.unpack("<II", self.read(self.base + FNAME_POOL + 8, 8))
            if not 0 <= block <= current < 8192 or not 0 <= cursor <= 0x20000:
                raise Unavailable("Name index is out of range.")
            address = self.u64(self.base + FNAME_POOL + 0x10 + block * 8) + offset
            header = struct.unpack("<H", self.read(address, 2))[0]
            length, wide = header >> 6, bool(header & 1)
            size = length * (2 if wide else 1)
            if not 0 < length <= 1023 or offset + 2 + size > (cursor if block == current else 0x20000):
                raise Unavailable("Name format is unknown.")
            self.names[index] = self.read(address + 2, size).decode("utf-16le" if wide else "ascii")
        return self.names[index] + (f"_{number - 1}" if number else "")

    def object_name(self, obj): return self.fname(self.read(obj + 0x18, 8))

    def class_names(self, obj, cls=None):
        cls = self.u64(obj + 0x10) if cls is None else cls
        if cls not in self.classes:
            # Native ClassIsChildOf uses UStructBaseChain, not a guessed linked list.
            array, depth = struct.unpack("<Qi", self.read(cls + 0x30, 12))
            if not 0 <= depth < 64:
                raise Unavailable("Game class inheritance information is unknown.")
            chain = struct.unpack("<" + "Q" * (depth + 1), self.read(array, 8 * (depth + 1)))
            if chain[-1] != cls + 0x30:
                raise Unavailable("Game class inheritance information is inconsistent.")
            self.classes[cls] = tuple(self.object_name(ptr - 0x30) for ptr in chain)
        return self.classes[cls]

    def string(self, address):
        pointer, length, capacity = struct.unpack("<Qii", self.read(address, 16))
        if not 1 <= length <= capacity <= 512:
            raise Unavailable("World identity string is unknown.")
        raw = self.read(pointer, length * 2)
        if raw[-2:] != b"\0\0":
            raise Unavailable("World identity string is not yet stable.")
        return raw[:-2].decode("utf-16le")

    def world(self, actor, identity):
        account, prospect, row, seed, map_key = identity
        level = self.u64(actor + 0x20)
        if "Level" not in self.class_names(level):
            raise Unavailable("Animal is not linked to a valid world region.")
        world = self.u64(level + 0xB8)
        if "World" not in self.class_names(world):
            raise Unavailable("Animal world class could not be confirmed.")
        world_name = self.object_name(world)
        if world_name.casefold() not in {map_key.casefold(), (map_key + "_DLC").casefold()}:
            raise Unavailable("Game map does not match the selected snapshot.")
        state = self.u64(world + 0x120)
        if "IcarusGameStateSurvival" not in self.class_names(state):
            raise Unavailable("The game has not entered a verifiable survival world.")
        info = state + 0x498
        live = (self.string(info + 0x10), self.string(info), self.string(info + 0x28), self.i32(state + 0x360), map_key)
        if live != identity:
            raise Unavailable("Game world identity or seed does not match the selected snapshot.")
        return world


def _world_helpers(snapshot):
    # These optional additions share this inventory instead of scanning GObjects again.
    try:
        from . import live_world_fields as fields
    except ImportError:
        return None, {}
    return fields, fields.snapshot_player_index(snapshot)


def _cache_key(process_key, identity, targets, players):
    player_targets = sorted((account, slot, row.get("id", ""), row.get("name", ""))
                            for (account, slot), row in players.items())
    digest = hashlib.sha256(json.dumps([identity, targets, player_targets], sort_keys=True,
                                      ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return process_key, digest


def _object_table(view):
    count = view.i32(view.base + GOBJECT_COUNT)
    chunks = view.u64(view.base + GOBJECT_CHUNKS)
    if not 0 < count <= MAX_OBJECTS:
        raise Unavailable("Game object index is outside the supported range.")
    return count, chunks


def _current_item(view, chunks, index):
    chunk = view.u64(chunks + (index // CHUNK_SIZE) * 8)
    address = chunk + (index % CHUNK_SIZE) * ITEM_SIZE
    return address, view.read(address, ITEM_SIZE)


def _check_item(view, item_address, item):
    current = view.read(item_address, ITEM_SIZE)
    if current != item or struct.unpack_from("<I", current, 8)[0] & 0x30000000:
        raise Unavailable("Animal or player object was updated or removed; locate and verify again.")


def _animal_sample(view, obj, header, item_address, item, index, targets, identity,
                   fields=None, expected=None):
    classes = view.class_names(obj, struct.unpack_from("<Q", header, 0x10)[0])
    recorder, state = view.u64(obj + 0x578), view.u64(obj + 0x5A8)
    if expected is not None and (recorder != expected.recorder or state != expected.state):
        raise Unavailable("Animal state location changed.")
    if "ActorStateRecorderComponent" not in view.class_names(recorder) or view.u64(recorder + 0x20) != obj:
        raise Unavailable("Animal save identity source is unknown.")
    ident = str(struct.unpack("<I", view.read(recorder + 0x140, 4))[0])
    if ident not in targets or classes[-1] != targets[ident]["class"] or "IcarusCharacter" not in classes:
        raise Unavailable("Animal species or save identity changed.")
    if expected is not None and ident != expected.identity:
        raise Unavailable("Animal save identity changed.")
    if "CharacterState" not in view.class_names(state) or view.u64(state + 0x20) != obj:
        raise Unavailable("Animal state source is unknown.")
    world = view.world(obj, identity)
    if expected is not None and expected.world != world:
        raise Unavailable("Animal world changed.")
    stable = None
    for _ in range(3):
        a = (view.read(state + 0x1D8, 8), view.read(state + 0x278, 8))
        b = (view.read(state + 0x1D8, 8), view.read(state + 0x278, 8))
        _check_item(view, item_address, item)
        if a == b and view.read(obj, 0x28) == header and view.u64(obj + 0x5A8) == state and view.u64(obj + 0x578) == recorder:
            if str(struct.unpack("<I", view.read(recorder + 0x140, 4))[0]) == ident and view.world(obj, identity) == world:
                stable = a
                break
    if stable is None:
        raise Unavailable("Animal state keeps changing; retaining the previous display.")
    current_health, maximum_health = struct.unpack("<ii", stable[0])
    current_stamina, maximum_stamina = struct.unpack("<ii", stable[1])
    if not 0 <= current_health <= 1_000_000_000 or not 0 < maximum_health <= 1_000_000_000:
        raise Unavailable("Health state format is unknown.")
    values = {"health": {"value": current_health, "maximum": maximum_health}}
    if targets[ident]["stamina_applicable"]:
        if not 0 <= current_stamina <= 1_000_000_000 or not 0 < maximum_stamina <= 1_000_000_000:
            raise Unavailable("Stamina state format is unknown.")
        values["stamina"] = {"value": current_stamina, "maximum": maximum_stamina}
    if fields is not None:
        additional = fields.read_animal_fields(view, obj, state, identity)
        if not targets[ident].get("oxygen_applicable", False):
            # Cow has no proven oxygen applicability; the native generic state
            # container's clamp-to-one placeholder is not an oxygen capacity.
            additional.pop("oxygen", None)
        values.update(additional)
        # Added fields must never weaken the original generation/world gates.
        _check_item(view, item_address, item)
        if (view.read(obj, 0x28) != header or view.u64(obj + 0x578) != recorder or
                view.u64(obj + 0x5A8) != state or view.u64(recorder + 0x20) != obj or
                view.u64(state + 0x20) != obj or
                str(struct.unpack("<I", view.read(recorder + 0x140, 4))[0]) != ident or
                view.world(obj, identity) != world):
            raise Unavailable("Animal identity changed during sampling.")
    return ident, values, ObjectLocator("animal", ident, index, item, header, obj, recorder, state, world)


def _player_sample(view, obj, header, item_address, item, index, identity, fields, player_index,
                   expected=None):
    classes = view.class_names(obj, struct.unpack_from("<Q", header, 0x10)[0])
    if not set(fields.candidate_classes()).intersection(classes):
        raise Unavailable("Player state class changed.")
    world = fields.player_world(view, obj, identity)
    values = fields.read_player_fields(view, obj, identity, player_index)
    if values is None:
        return None
    ident = values["id"]
    _check_item(view, item_address, item)
    if (view.read(obj, 0x28) != header or fields.player_world(view, obj, identity) != world or
            (expected is not None and (ident != expected.identity or world != expected.world))):
        raise Unavailable("Player identity changed during sampling.")
    return ident, values, ObjectLocator("player", ident, index, item, header, obj, world=world)


def _changed_slot_candidates(view, inventory, count, chunks, targets, player_index, fields):
    """Check all FUObjectItem generations, decoding only changed/new slots.

    Old empty slots can be reused without increasing NumElements. Their new
    occupants must receive the same candidate/duplicate/world checks as appended
    slots. Only object-array metadata is retained, never gameplay state values.
    """
    animal_classes = {row["class"] for row in targets.values()}
    player_classes = set(fields.candidate_classes()) if player_index else set()
    visited, current_chunks = 0, []
    for start in range(0, count, CHUNK_SIZE):
        block = start // CHUNK_SIZE
        length = min(CHUNK_SIZE, count - start)
        chunk = view.u64(chunks + block * 8)
        items = view.read(chunk, length * ITEM_SIZE)
        current_chunks.append(items)
        previous = inventory.object_chunks[block] if block < len(inventory.object_chunks) else b""
        if items == previous:
            continue
        for slot in range(length):
            offset = slot * ITEM_SIZE
            item = items[offset:offset + ITEM_SIZE]
            if item == previous[offset:offset + ITEM_SIZE]:
                continue
            visited += 1
            obj, flags = struct.unpack_from("<QI", item)
            if not obj or flags & 0x30000000:
                continue
            try:
                header = view.read(obj, 0x28)
                if struct.unpack_from("<i", header, 0xC)[0] != start + slot:
                    continue
                classes = view.class_names(obj, struct.unpack_from("<Q", header, 0x10)[0])
                candidate = (("IcarusCharacter" in classes and classes[-1] in animal_classes)
                             or bool(player_classes.intersection(classes)))
                if candidate and not view.object_name(obj).startswith("Default__"):
                    return True, visited, current_chunks
            except BudgetExceeded:
                raise
            except (ValueError, OSError, UnicodeError, struct.error):
                continue
    return False, visited, current_chunks


def _warm_sample(view, inventory, targets, identity, fields, player_index):
    count, chunks = _object_table(view)
    if count < inventory.object_count:
        raise Unavailable("Game object count decreased; rebuilding the inventory.")
    animals, players, worlds = {}, {}, set()
    for locator in inventory.locators:
        if not 0 <= locator.index < count:
            raise Unavailable("Game object index changed.")
        address, item = _current_item(view, chunks, locator.index)
        if item != locator.item or struct.unpack_from("<I", item, 8)[0] & 0x30000000:
            raise Unavailable("Game object generation changed.")
        obj = struct.unpack_from("<Q", item)[0]
        header = view.read(obj, 0x28)
        if header != locator.header or struct.unpack_from("<i", header, 0xC)[0] != locator.index:
            raise Unavailable("Game object class or identity changed.")
        if locator.kind == "animal":
            ident, values, checked = _animal_sample(view, obj, header, address, item, locator.index,
                                                   targets, identity, fields, expected=locator)
            animals[ident] = values
        else:
            sampled = _player_sample(view, obj, header, address, item, locator.index, identity,
                                     fields, player_index, expected=locator)
            if sampled is None:
                raise Unavailable("Player character identity changed.")
            ident, values, checked = sampled
            players[ident] = values
        worlds.add(checked.world)
    if len(worlds) != 1 or not (animals or players):
        raise Unavailable("Game world could not be uniquely verified.")
    candidate, visited, current_chunks = _changed_slot_candidates(view, inventory, count, chunks,
                                                               targets, player_index, fields)
    if candidate:
        # Do not append a possible duplicate or a second live world to a warm
        # inventory. Reuse the normal full-scan identity/uniqueness checks.
        raise Unavailable("New animal or player candidates found; rebuilding the full inventory.")
    return animals, players, count, visited, current_chunks


def _read_snapshot_from_memory(snapshot, memory, base, *, targets=None, cache=None, process_key=None):
    """Bounded decoding; optional cache stores only revalidated object locators.

    A warm sample checks every cached object's complete FUObjectItem generation,
    header/class, identity, ownership components and exact selected world afresh.
    The full inventory expires after five minutes, even if warm reads succeed.
    """
    result = _result("unavailable", "No verifiable in-game animal data is available.")
    identity = _identity(snapshot)
    targets = _targets(snapshot) if targets is None else targets
    fields, player_index = _world_helpers(snapshot)
    result["coverage"].update(expected_animals=len(targets), expected_players=len(player_index),
                              full_inventory_interval_seconds=FULL_SCAN_INTERVAL,
                              discovery_interval_seconds=FULL_SCAN_INTERVAL,
                              discovery_mode="all_slot_generations_each_sample")
    if not targets and not player_index:
        result["reason"] = "This snapshot has no supported animals or players that can be uniquely matched."
        return result
    key = _cache_key(process_key, identity, targets, player_index)
    if cache is not None and process_key is None:
        raise Unavailable("A verifiable process generation is missing; locator cache was not used.")
    view = _View(memory, base)
    entry = cache.get(key) if cache is not None else None
    invalidated = False
    if entry is not None:
        try:
            animals, players, count, changed_slots, current_chunks = _warm_sample(view, entry, targets, identity, fields, player_index)
            cache.advance(entry, count, current_chunks)
            result.update(status="ok" if len(animals) == len(targets) else "partial",
                          reason="Selected world and animal/player locations were reverified and sampled.",
                          world_verified=True, animals=animals, players=players,
                          sample_time=datetime.now(timezone.utc).isoformat())
            result["coverage"].update(cache_mode="warm", scan_complete=False,
                                      locator_revalidation_complete=True,
                                      inventory_age_seconds=round(cache.clock() - entry.created, 3),
                                      object_slots_checked=count, object_generations_checked=count,
                                      locator_revalidation_count=len(entry.locators),
                                      incremental_slots_checked=count - entry.object_count,
                                      changed_slots_checked=changed_slots, sampled_animals=len(animals),
                                      sampled_players=len(players), missing_animals=len(targets) - len(animals))
            for row in players.values():
                row["sample_time"] = result["sample_time"]
            return result
        except BudgetExceeded:
            cache.clear()
            raise
        except (ValueError, OSError, UnicodeError, struct.error):
            cache.clear()
            # A failed warm probe never emits a subset or falls back to old values.
            # The remaining budget is shared with the replacement full inventory.
            invalidated = True
            view = _View(memory, base)
    if cache is not None:
        cache.clear()
    count, chunks = _object_table(view)
    objects, players, duplicates, player_duplicates, worlds = {}, {}, set(), set(), set()
    locators, inventory_chunks = {}, []
    target_classes = {row["class"] for row in targets.values()}
    player_classes = set(fields.candidate_classes()) if player_index else set()
    rejected, visited = Counter(), 0
    matched_classes = matched_guids = 0
    started = time.monotonic()
    for start in range(0, count, CHUNK_SIZE):
        chunk = view.u64(chunks + (start // CHUNK_SIZE) * 8)
        length = min(CHUNK_SIZE, count - start)
        items = view.read(chunk, length * ITEM_SIZE)
        inventory_chunks.append(items)
        for slot in range(length):
            if time.monotonic() - started > MAX_READ_SECONDS:
                raise BudgetExceeded("Read-only check timed out; incomplete animal data was not applied.")
            visited += 1
            memory.object_slots_checked = visited
            item = items[slot * ITEM_SIZE:(slot + 1) * ITEM_SIZE]
            obj, flags = struct.unpack_from("<QI", item)
            if not obj or flags & 0x30000000:
                continue
            try:
                header = view.read(obj, 0x28)
                if struct.unpack_from("<i", header, 0xC)[0] != start + slot:
                    continue
                classes = view.class_names(obj, struct.unpack_from("<Q", header, 0x10)[0])
                animal = "IcarusCharacter" in classes and classes[-1] in target_classes
                player = bool(player_classes.intersection(classes))
                if not animal and not player:
                    continue
                matched_classes += bool(animal)
                if view.object_name(obj).startswith("Default__"):
                    continue
                if animal:
                    ident, values, locator = _animal_sample(view, obj, header, chunk + slot * ITEM_SIZE,
                                                           item, start + slot, targets, identity, fields)
                    matched_guids += 1
                    if ident in objects:
                        duplicates.add(ident)
                    objects[ident] = values
                    locators["animal", ident] = locator
                else:
                    sampled = _player_sample(view, obj, header, chunk + slot * ITEM_SIZE, item,
                                             start + slot, identity, fields, player_index)
                    if sampled is None:
                        continue
                    ident, values, locator = sampled
                    if ident in players:
                        player_duplicates.add(ident)
                    players[ident] = values
                    locators["player", ident] = locator
                worlds.add(locator.world)
            except BudgetExceeded:
                raise
            except (ValueError, OSError, UnicodeError, struct.error) as error:
                rejected[str(error) if isinstance(error, Unavailable) else "unknown_object_layout"] += 1
    result["coverage"].update(object_slots_checked=visited, matching_actor_classes=matched_classes,
                              matching_saved_guids=matched_guids, rejected_objects=dict(rejected),
                              scan_complete=True, cache_mode="full", cache_invalidated=invalidated,
                              inventory_age_seconds=0)
    if len(worlds) != 1:
        result["reason"] = "Game world could not be uniquely verified; sampled values were not applied."
        return result
    for ident in duplicates:
        objects.pop(ident, None)
        locators.pop(("animal", ident), None)
    for ident in player_duplicates:
        players.pop(ident, None)
        locators.pop(("player", ident), None)
    result.update(status="ok" if len(objects) == len(targets) else "partial",
                  reason="Game state cache verified for the selected world." if objects or players else "No animal or player states could be uniquely matched.",
                  world_verified=bool(objects or players), animals=objects, players=players,
                  sample_time=datetime.now(timezone.utc).isoformat())
    result["coverage"].update(sampled_animals=len(objects), sampled_players=len(players),
                              duplicate_guids=len(duplicates), duplicate_players=len(player_duplicates),
                              missing_animals=len(targets) - len(objects))
    for row in players.values():
        row["sample_time"] = result["sample_time"]
    if cache is not None and result["world_verified"] and not duplicates and not player_duplicates and len(objects) == len(targets):
        cache.put(key, locators.values(), count, inventory_chunks)
    return result


def _kernel():
    if sys.platform != "win32":
        raise Unavailable("This source is supported only on Windows.")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    api.ReadProcessMemory.restype = wintypes.BOOL
    return api


def _find_game_processes():
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("name", wintypes.WCHAR * 260)]
    api = _kernel()
    api.Process32FirstW.argtypes = api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32FirstW.restype = api.Process32NextW.restype = wintypes.BOOL
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise Unavailable("Could not check whether the game is running.")
    try:
        entry, result = Entry(), []
        entry.dwSize = ctypes.sizeof(entry)
        success = api.Process32FirstW(handle, ctypes.byref(entry))
        while success:
            if entry.name.casefold() == PROCESS_NAME.casefold():
                result.append(entry.pid)
            success = api.Process32NextW(handle, ctypes.byref(entry))
        return result
    finally:
        api.CloseHandle(handle)


def _module(pid):
    class Module(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("module_id", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("global_usage", wintypes.DWORD), ("process_usage", wintypes.DWORD),
                    ("base", ctypes.c_void_p), ("size", wintypes.DWORD), ("module", wintypes.HMODULE),
                    ("name", wintypes.WCHAR * 256), ("path", wintypes.WCHAR * 260)]
    api = _kernel()
    api.Module32FirstW.argtypes = api.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Module)]
    api.Module32FirstW.restype = api.Module32NextW.restype = wintypes.BOOL
    handle = api.CreateToolhelp32Snapshot(0x18, pid)
    if handle == ctypes.c_void_p(-1).value:
        raise Unavailable("Could not read game version information.")
    try:
        entry = Module()
        entry.dwSize = ctypes.sizeof(entry)
        success = api.Module32FirstW(handle, ctypes.byref(entry))
        while success:
            if entry.name.casefold() == PROCESS_NAME.casefold():
                return Path(entry.path), entry.base
            success = api.Module32NextW(handle, ctypes.byref(entry))
        raise Unavailable("The verified game executable could not be found.")
    finally:
        api.CloseHandle(handle)


@lru_cache(maxsize=4)
def _file_hash(path, modified, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validate_executable(path):
    stat = path.stat()
    if _file_hash(str(path), stat.st_mtime_ns, stat.st_size) != EXE_HASH:
        raise Unavailable("This game version has not passed read-only state source verification.")
    manifest = path.parents[5] / "appmanifest_1149460.acf"
    match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8"))
    if not match or match[1] != BUILD:
        raise Unavailable("Steam game version differs from the verified source.")


class _WindowsMemory:
    def __init__(self, pid, expected_path):
        self.api = _kernel()
        # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ; no write/debug rights.
        self.handle = self.api.OpenProcess(0x1000 | 0x10, False, pid)
        if not self.handle:
            raise Unavailable("The game does not allow read-only queries; retaining save data.")
        try:
            # A PID may be reused between Toolhelp enumeration and OpenProcess.
            self.api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            self.api.QueryFullProcessImageNameW.restype = wintypes.BOOL
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not self.api.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(length)) or Path(buffer.value).resolve() != expected_path.resolve():
                raise Unavailable("Game process identity changed; no other process was read.")
            self.api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            self.api.GetProcessTimes.restype = wintypes.BOOL
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.api.GetProcessTimes(self.handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                raise Unavailable("Game process creation time could not be verified; old locators were not used.")
            self.creation_time = (created.dwHighDateTime << 32) | created.dwLowDateTime
            if self.creation_time <= 0:
                raise Unavailable("Game process creation time is invalid.")
        except Exception:
            self.close()
            raise
        self.started, self.bytes, self.calls, self.object_slots_checked = time.monotonic(), 0, 0, 0

    def read(self, address, size):
        self.bytes += size
        self.calls += 1
        if self.bytes > MAX_READ_BYTES or time.monotonic() - self.started > MAX_READ_SECONDS:
            raise BudgetExceeded("The current read-only game query budget has been reached.")
        buffer = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        if not self.api.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(received)) or received.value != size:
            raise Unavailable("Game data could not be read consistently.")
        return buffer.raw

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def read_live_animal_stats(snapshot):
    """Read only the named ICARUS process, after exact version and world gates."""
    if _get(snapshot, "is_remote", False):
        return _result("unavailable", "Public sync uses only the host save; the local game process is not read.")
    with _LOCATORS.lock:
        return _read_live_animal_stats_locked(snapshot)


def _read_live_animal_stats_locked(snapshot):
    memory = None
    try:
        _identity(snapshot)
        pids = _find_game_processes()
        if not pids:
            _LOCATORS.clear()
            return _result("not_running", "Game is not running; displaying save data.")
        if len(pids) != 1:
            _LOCATORS.clear()
            return _result("unavailable", "Multiple game processes detected; the source could not be uniquely verified.")
        path, base = _module(pids[0])
        _validate_executable(path)
        memory = _WindowsMemory(pids[0], expected_path=path)
        process_key = (pids[0], memory.creation_time, str(path.resolve()).casefold(), base, BUILD, EXE_HASH)
        result = _read_snapshot_from_memory(snapshot, memory, base, cache=_LOCATORS, process_key=process_key)
        result["coverage"].update(read_bytes=memory.bytes, read_calls=memory.calls,
                                  read_seconds=round(time.monotonic() - memory.started, 3))
        return result
    except (Unavailable, OSError, ValueError, IndexError, TypeError, AttributeError, struct.error) as error:
        _LOCATORS.clear()
        reason = str(error) if isinstance(error, Unavailable) else "The read-only game state source could not be verified at this time."
        result = _result("unavailable", reason)
        if memory is not None:
            result["coverage"].update(read_bytes=getattr(memory, "bytes", 0), read_calls=getattr(memory, "calls", 0),
                                      object_slots_checked=getattr(memory, "object_slots_checked", 0))
        return result
    finally:
        if memory is not None:
            memory.close()
