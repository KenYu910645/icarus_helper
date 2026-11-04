"""Build-bound optional world fields for the read-only live scanner.

This module never opens a process. Its caller supplies the already version-gated
memory view and selected-world identity. Missing optional animal fields are
omitted; a read budget failure always propagates. Player identities remain in an
internal lookup only: returned records contain an anonymous key and saved name.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import struct


BUILD = "26313227"
ANIMAL_SUPPLIES = {
    "food": (0x330, 0x348),
    "water": (0x32C, 0x340),
    "oxygen": (0x328, 0x338),
}
ROOT_COMPONENT = 0x130
COMPONENT_WORLD_TRANSLATION = 0x1D0
WORLD_ORIGIN = 0x5B8
PLAYER_IDENTITY = 0x498
PLAYER_PAWN = 0x280
PAWN_PLAYER_STATE = 0x240
MAX_VALUE = 1_000_000_000


class FieldUnavailable(ValueError):
    """An optional field cannot be established from the selected live object."""


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _identity(raw):
    if isinstance(raw, dict):
        account, slot = raw.get("PlayerID"), raw.get("ChrSlot")
    elif isinstance(raw, (tuple, list)) and len(raw) == 2:
        account, slot = raw
    else:
        return None
    if (not isinstance(account, str) or account != account.strip() or not account
            or account.casefold() in {"0", "-1", "none", "null"}
            or any(ord(char) < 32 for char in account)):
        return None
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < 2**31:
        return None
    return account, slot


def player_key(identity):
    """Anonymous stable key; the source identity is never included in the result."""
    identity = _identity(identity)
    if identity is None:
        raise FieldUnavailable("Player character identity is incomplete.")
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "player:" + hashlib.sha256(b"icarus-helper-player-v1\0" + encoded).hexdigest()


def snapshot_player_index(snapshot):
    """Internal exact-ID index; ambiguous saved characters are excluded.

    The tuple keys contain source identities for matching only and must not be
    serialized. Public values contain only id/name. Raw PlayerState records can
    establish membership even when their old position was unavailable.
    """
    resources = _get(snapshot, "players", []) or []
    by_identity = {}
    resource_conflicts = set()
    for resource in resources:
        raw = _get(resource, "raw", {})
        identity = _identity(raw.get("PlayerCharacterID")) if isinstance(raw, dict) else None
        if identity is None:
            continue
        resource_id = str(_get(resource, "id", ""))
        name = _get(resource, "name", "Player")
        row = {"id": player_key(identity),
               "name": name if isinstance(name, str) and name and identity[0] not in name else "Player"}
        if identity in by_identity:
            resource_conflicts.add(identity)
        by_identity[identity] = resource_id, row, raw
    records = [record for record in (_get(snapshot, "raw_records", []) or [])
               if isinstance(record, dict)
               and str(record.get("_component", "")).endswith("PlayerStateRecorderComponent")]
    if not records:
        records = [value[2] for value in by_identity.values()]
    counts = Counter(identity for record in records
                     if (identity := _identity(record.get("PlayerCharacterID"))) is not None)
    return {identity: by_identity.get(identity, (None, {"id": player_key(identity), "name": "Player"}))[1]
            for identity, count in counts.items() if count == 1 and identity not in resource_conflicts}


def candidate_classes():
    """Native ancestor required by the scanner's player-object candidate test."""
    return frozenset({"IcarusPlayerState"})


def _optional(operation):
    try:
        return operation()
    except (ValueError, OSError, UnicodeError, struct.error) as error:
        # Imported at call time to avoid a scanner/helper import cycle.
        from .live_animal_stats import BudgetExceeded
        if isinstance(error, BudgetExceeded):
            raise
        return None


def _guard(view, obj):
    """Capture a UObject generation without retaining sampled gameplay values."""
    from .live_animal_stats import GOBJECT_CHUNKS, GOBJECT_COUNT, CHUNK_SIZE, ITEM_SIZE, MAX_OBJECTS
    header = view.read(obj, 0x28)
    index = struct.unpack_from("<i", header, 0xC)[0]
    count = view.i32(view.base + GOBJECT_COUNT)
    if not 0 <= index < count <= MAX_OBJECTS:
        raise FieldUnavailable("Object identity changed.")
    chunks = view.u64(view.base + GOBJECT_CHUNKS)
    chunk = view.u64(chunks + index // CHUNK_SIZE * 8)
    address = chunk + index % CHUNK_SIZE * ITEM_SIZE
    item = view.read(address, ITEM_SIZE)
    pointer, flags = struct.unpack_from("<QI", item)
    if pointer != obj or flags & 0x30000000:
        raise FieldUnavailable("Object is no longer valid.")
    return obj, header, address, item


def _unchanged(view, guard):
    obj, header, address, item = guard
    if view.read(obj, len(header)) != header or view.read(address, len(item)) != item:
        raise FieldUnavailable("Object changed while being read.")


def _supply_pair(view, state, current_offset, maximum_offset):
    for _ in range(3):
        a = view.read(state + current_offset, 4), view.read(state + maximum_offset, 4)
        b = view.read(state + current_offset, 4), view.read(state + maximum_offset, 4)
        if a == b:
            value, maximum = struct.unpack("<i", a[0])[0], struct.unpack("<i", a[1])[0]
            if not 0 <= value <= MAX_VALUE or not 0 < maximum <= MAX_VALUE:
                raise FieldUnavailable("Supply state format is unknown.")
            # A temporary over-capacity value never changes its denominator.
            return {"value": value, "maximum": maximum}
    raise FieldUnavailable("Supply state keeps changing.")


def actor_position(view, actor, identity):
    """K2_GetActorLocation's world-transform translation, in Unreal centimetres.

    Nonzero world origins are deliberately unsupported until save-coordinate
    rebasing semantics are proven. RelativeLocation is never used or added.
    """
    actor_guard = _guard(view, actor)
    world = view.world(actor, identity)
    root = view.u64(actor + ROOT_COMPONENT)
    if "SceneComponent" not in view.class_names(root) or view.u64(root + 0x20) != actor:
        raise FieldUnavailable("Character position component could not be verified.")
    root_guard = _guard(view, root)
    for _ in range(3):
        a = view.read(root + COMPONENT_WORLD_TRANSLATION, 12), view.read(world + WORLD_ORIGIN, 12)
        b = view.read(root + COMPONENT_WORLD_TRANSLATION, 12), view.read(world + WORLD_ORIGIN, 12)
        if a == b:
            if struct.unpack("<iii", a[1]) != (0, 0, 0):
                raise FieldUnavailable("Coordinate conversion for a nonzero world origin has not been verified.")
            position = list(struct.unpack("<fff", a[0]))
            if not all(math.isfinite(value) and abs(value) <= MAX_VALUE for value in position):
                raise FieldUnavailable("Character position format is unknown.")
            if view.u64(actor + ROOT_COMPONENT) != root or view.world(actor, identity) != world:
                raise FieldUnavailable("Character position source changed.")
            _unchanged(view, actor_guard)
            _unchanged(view, root_guard)
            return position
    raise FieldUnavailable("Character position keeps changing.")


def read_animal_fields(view, actor, state, identity):
    """Optional current/max supplies plus location; caller adds sample_time.

    The caller must already have matched the animal GUID/class and selected
    world. Each optional pair and location is sampled twice independently.
    Unsupported optional sources do not discard the caller's health/stamina.
    """
    result = {}

    def supplies():
        if ("SurvivalCharacterState" not in view.class_names(state)
                or view.u64(state + 0x20) != actor or view.u64(actor + 0x5A8) != state):
            raise FieldUnavailable("Animal supply state source is unknown.")
        guard = _guard(view, state)
        values = {}
        for channel, offsets in ANIMAL_SUPPLIES.items():
            value = _optional(lambda offsets=offsets: _supply_pair(view, state, *offsets))
            if value is not None:
                values[channel] = value
        if view.u64(actor + 0x5A8) != state or view.u64(state + 0x20) != actor:
            raise FieldUnavailable("Animal supply source changed.")
        _unchanged(view, guard)
        return values

    values = _optional(supplies)
    if values:
        result.update(values)
    position = _optional(lambda: actor_position(view, actor, identity))
    if position is not None:
        result["position"] = position
    return result


def _player_links(view, player_state, identity):
    if "IcarusPlayerState" not in view.class_names(player_state):
        raise FieldUnavailable("Player state class could not be verified.")
    saved_identity = _identity({"PlayerID": view.string(player_state + PLAYER_IDENTITY),
                                "ChrSlot": view.i32(player_state + PLAYER_IDENTITY + 0x10)})
    if saved_identity is None:
        raise FieldUnavailable("Player character identity is incomplete.")
    pawn = view.u64(player_state + PLAYER_PAWN)
    classes = view.class_names(pawn)
    if not {"Pawn", "IcarusCharacter"}.issubset(classes) or view.u64(pawn + PAWN_PLAYER_STATE) != player_state:
        raise FieldUnavailable("Player character and state could not be verified in both directions.")
    return saved_identity, pawn, view.world(pawn, identity)


def player_world(view, player_state, identity):
    """Internal world pointer for the scanner's multiple-world rejection gate."""
    return _player_links(view, player_state, identity)[2]


def read_player_fields(view, player_state, identity, index):
    """Return one anonymous snapshot member's current position, or None.

    No raw identity, object pointer or source address enters the returned record.
    The scanner owns the PlayerState slot/header gate and success sample_time.
    """
    state_guard = _guard(view, player_state)
    initial_identity, pawn, world = _player_links(view, player_state, identity)
    target = index.get(initial_identity)
    if target is None:
        return None
    pawn_guard = _guard(view, pawn)
    position = actor_position(view, pawn, identity)
    if _player_links(view, player_state, identity) != (initial_identity, pawn, world):
        raise FieldUnavailable("Player identity or world changed while being read.")
    _unchanged(view, state_guard)
    _unchanged(view, pawn_guard)
    # Recompute the key instead of trusting a caller-provided public identifier.
    name = target.get("name", "Player")
    if not isinstance(name, str) or not name or initial_identity[0] in name:
        name = "Player"
    return {"id": player_key(initial_identity), "name": name, "position": position}
