"""Read-only ICARUS save reader and bounded Unreal tagged-property decoder.

The reader validates the compressed envelope before exposing a new snapshot. It
never opens a game file for writing. Optional copies belong to the app cache.
"""
from __future__ import annotations

import base64
import binascii
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import struct
from typing import Any
import uuid
import zlib

from .models import Resource, SaveDescriptor, WorldSnapshot

MAX_SAVE_BYTES = 128 * 1024 * 1024
MAX_BLOB_BYTES = 256 * 1024 * 1024
MAX_ARRAY_ITEMS = 8_000_000
DRILL_CORE_FIELDS = {"StaticItemDataRowName", "ActorClassName", "IcarusActorGUID",
                     "ActorPathName", "DeployableRecord", "FoundationActorClassName",
                     "FoundationActorIcarusUID", "ActorTransform", "Translation"}
MAP_NAMES = {"Terrain_016": "Olympus", "Terrain_019": "Prometheus"}
RESOURCE_NAMES = {
    "Iron": "Iron", "Iron_Ore": "Iron", "Copper": "Copper", "Copper_Ore": "Copper",
    "Gold": "Gold", "Gold_Ore": "Gold", "Aluminium": "Aluminium", "Aluminum": "Aluminium",
    "Aluminium_Ore": "Aluminium", "Bauxite": "Bauxite", "Titanium": "Titanium",
    "Titanium_Ore": "Titanium", "Platinum": "Platinum", "Platinum_Ore": "Platinum",
    "Coal": "Coal", "Sulfur": "Sulfur", "Silica": "Silica", "Oxite": "Oxite",
    "Stone": "Stone", "Obsidian": "Obsidian", "Scoria": "Scoria",
    "Exotic": "Exotics", "Exotic_Raw_Uranium": "Uranium / exotic resource",
    "Ice": "Ice", "Salt": "Salt", "Clay": "Clay", "Super_Cooled_Ice": "Supercooled ice",
}


class SaveError(ValueError):
    """Unsupported, inconsistent or incomplete save; keep last good UI snapshot."""


class SaveChangedError(SaveError):
    """The source changed during a read and needs a later retry."""


class Reader:
    def __init__(self, data: bytes, depth: int = 0):
        self.b = data
        self.p = 0
        self.n = len(data)
        self.skipped: Counter[str] = Counter()
        self.depth = depth
        self.unsupported_fields: set[str] = set()

    def take(self, size: int) -> bytes:
        if size < 0 or self.p + size > self.n:
            raise SaveError("UE property data is incomplete or has an invalid length")
        out = self.b[self.p:self.p + size]
        self.p += size
        return out

    def number(self, fmt: str):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def i32(self): return self.number("i")
    def u32(self): return self.number("I")
    def i64(self): return self.number("q")
    def f32(self): return self.number("f")
    def f64(self): return self.number("d")
    def byte(self): return self.number("B")

    def fstr(self) -> str:
        length = self.i32()
        if length == 0:
            return ""
        if abs(length) > MAX_BLOB_BYTES // 2:
            raise SaveError("UE string length exceeds the limit")
        if length < 0:
            raw = self.take(-length * 2)
            if not raw.endswith(b"\0\0"):
                raise SaveError("Unterminated UE UTF-16 string")
            return raw[:-2].decode("utf-16-le")
        raw = self.take(length)
        if not raw.endswith(b"\0"):
            raise SaveError("Unterminated UE string")
        try:
            return raw[:-1].decode("utf-8")
        except UnicodeDecodeError:
            return raw[:-1].decode("latin-1")


def read_struct(reader: Reader, struct_type: str):
    sizes = {"Vector": 3, "Rotator": 3, "Quat": 4, "Vector2D": 2, "LinearColor": 4}
    if struct_type in sizes:
        return [reader.f32() for _ in range(sizes[struct_type])]
    if struct_type == "Color": return list(reader.take(4))
    if struct_type == "Guid": return reader.take(16).hex()
    if struct_type == "IntPoint": return [reader.i32(), reader.i32()]
    if struct_type in ("DateTime", "Timespan"): return reader.i64()
    return read_props(reader)


def read_value(reader: Reader, ptype: str, size: int, struct_type: str | None,
               inner_type: str | None):
    if ptype == "StructProperty": return read_struct(reader, struct_type or "")
    numeric = {"IntProperty": reader.i32, "Int32Property": reader.i32,
               "UInt32Property": reader.u32, "Int64Property": reader.i64,
               "FloatProperty": reader.f32, "DoubleProperty": reader.f64}
    if ptype in numeric: return numeric[ptype]()
    if ptype in ("StrProperty", "NameProperty", "EnumProperty"): return reader.fstr()
    if ptype == "ByteProperty": return reader.byte() if size == 1 else reader.fstr()
    if ptype == "ArrayProperty":
        count = reader.i32()
        if count < 0 or count > MAX_ARRAY_ITEMS:
            raise SaveError("Invalid UE array element count")
        if inner_type == "StructProperty":
            reader.fstr()
            element_type = reader.fstr()
            if element_type != "StructProperty":
                raise SaveError("Unsupported UE struct array format")
            reader.i32()
            reader.i32()
            element_struct = reader.fstr()
            reader.take(16)
            reader.byte()
            return [read_struct(reader, element_struct) for _ in range(count)]
        if inner_type == "ByteProperty": return list(reader.take(count))
        fn = {"IntProperty": reader.i32, "Int64Property": reader.i64,
              "UInt32Property": reader.u32, "FloatProperty": reader.f32,
              "DoubleProperty": reader.f64, "BoolProperty": reader.byte,
              "StrProperty": reader.fstr, "NameProperty": reader.fstr}.get(inner_type or "")
        if fn: return [fn() for _ in range(count)]
        reader.skipped["ArrayProperty:" + str(inner_type)] += 1
        return None
    reader.skipped[ptype] += 1
    return None


def read_props(reader: Reader, depth: int = 0) -> dict[str, Any]:
    # Every property body has a size: bounded sub-readers keep unknown types
    # from desynchronising subsequent properties, unlike a heuristic scanner.
    if max(depth, reader.depth) > 64:
        raise SaveError("UE property nesting exceeds the limit")
    result: dict[str, Any] = {}
    while reader.p < reader.n:
        name = reader.fstr()
        if name == "None": return result
        ptype = reader.fstr()
        size, array_index = reader.i32(), reader.i32()
        if size < 0 or size > MAX_BLOB_BYTES:
            raise SaveError("Invalid UE property length")
        struct_type = inner_type = None
        bool_value = None
        if ptype == "StructProperty":
            struct_type = reader.fstr()
            reader.take(16)
            reader.byte()
        elif ptype == "ArrayProperty":
            inner_type = reader.fstr()
            reader.byte()
        elif ptype == "BoolProperty":
            bool_value = bool(reader.byte())
            reader.byte()
        elif ptype in ("ByteProperty", "EnumProperty"):
            reader.fstr()
            reader.byte()
        elif ptype == "MapProperty":
            reader.fstr()
            reader.fstr()
            reader.byte()
        else:
            reader.byte()
        body = Reader(reader.take(size), reader.depth + 1)
        value = bool_value if ptype == "BoolProperty" else read_value(
            body, ptype, size, struct_type, inner_type)
        reader.skipped.update(body.skipped)
        reader.unsupported_fields.update(body.unsupported_fields)
        if body.skipped and value is None:
            reader.unsupported_fields.add(name)
        key = name if array_index == 0 else f"{name}[{array_index}]"
        result[key] = value
    return result


def _json_bytes(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_SAVE_BYTES:
        raise SaveError("Save exceeds the supported size")
    try:
        # json.loads(bytes) detects UTF-8/UTF-16/UTF-32 BOM and zero patterns.
        result = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SaveError("Save is not a complete, recognizable JSON envelope") from exc
    if not isinstance(result, dict):
        raise SaveError("Save root must be an object")
    return result


def _read_stable(path: Path) -> tuple[bytes, os.stat_result]:
    before = path.stat()
    if before.st_size > MAX_SAVE_BYTES:
        raise SaveError("Save exceeds the supported size")
    data = path.read_bytes()
    after = path.stat()
    signature = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino)
    if signature(before) != signature(after) or len(data) != after.st_size:
        raise SaveChangedError("Save is still being written; retry shortly")
    return data, after


def _unpack(data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    blob = data.get("ProspectBlob")
    if not isinstance(blob, dict) or not isinstance(blob.get("BinaryBlob"), str):
        raise SaveError("ProspectBlob.BinaryBlob missing; unsupported world save")
    try:
        compressed = base64.b64decode(blob["BinaryBlob"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_BLOB_BYTES + 1)
        if len(raw) > MAX_BLOB_BYTES or decoder.unconsumed_tail:
            raise SaveError("Decompressed data exceeds the limit")
        if not decoder.eof or decoder.unused_data:
            raise SaveError("Compressed envelope is incomplete or has unsupported trailing data")
    except (binascii.Error, zlib.error) as exc:
        raise SaveError("Could not decode the compressed save envelope") from exc
    expected_length = blob.get("UncompressedLength")
    if expected_length is not None and expected_length != len(raw):
        raise SaveError("Save decompressed length verification failed")
    expected_hash = str(blob.get("Hash") or "").lower()
    if expected_hash and hashlib.sha1(raw).hexdigest() != expected_hash:
        raise SaveError("Save content hash verification failed")
    reader = Reader(raw)
    top = read_props(reader)
    blobs = top.get("StateRecorderBlobs")
    if not isinstance(blobs, list):
        raise SaveError("Unsupported world state record format")
    records, warnings = [], []
    skipped = reader.skipped.copy()
    for index, item in enumerate(blobs):
        if not isinstance(item, dict) or not isinstance(item.get("BinaryData"), list):
            raise SaveError(f"World state record {index} is missing BinaryData")
        component = str(item.get("ComponentClassName", ""))
        try:
            component_reader = Reader(bytes(item["BinaryData"]))
            record = read_props(component_reader)
            skipped.update(component_reader.skipped)
        except (SaveError, UnicodeError, ValueError, struct.error) as exc:
            # Do not claim no drill if one of the actors could not be read.
            raise SaveError(f"World state record {index} ({component}) could not be decoded: {exc}") from exc
        record["_component"] = component
        if component_reader.unsupported_fields:
            record["_unsupported_fields"] = sorted(component_reader.unsupported_fields)
        records.append(record)
    if skipped:
        warnings.append("Some UE properties were not decoded; related data may be incomplete: " + ", ".join(sorted(skipped)))
    if top.get("Version") != 2:
        warnings.append(f"World save format version {top.get('Version')!r} not verified; showing recognized fields only. Absence of drills cannot be confirmed.")
    return top, records, warnings


def map_name_for(prospect_key: str, map_key: str = "") -> str:
    for name in ("Prometheus", "Olympus", "Styx", "Elysium"):
        if name.lower() in prospect_key.lower(): return name
    if map_key in MAP_NAMES: return MAP_NAMES[map_key]
    return map_key or prospect_key or "Unknown map"


def discover_saves(root: str | Path | None = None) -> list[SaveDescriptor]:
    base = Path(root) if root is not None else Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Icarus/Saved/PlayerData"
    files = base.glob("*.json") if base.name.lower() == "prospects" else base.glob("*/Prospects/*.json")
    found = []
    for path in files:
        try:
            stat = path.stat()
            if stat.st_size > MAX_SAVE_BYTES: continue
            data = _json_bytes(path.read_bytes())
            info = data.get("ProspectInfo")
            if not isinstance(info, dict) or "ProspectBlob" not in data: continue
            key = str(info.get("ProspectDTKey") or "")
            found.append(SaveDescriptor(path.resolve(), str(info.get("ProspectID") or path.stem), map_name_for(key), stat.st_mtime, key))
        except (OSError, SaveError):
            continue
    return sorted(found, key=lambda s: s.mtime, reverse=True)


def location_of(record: dict[str, Any]) -> tuple[float, float, float] | None:
    transform = record.get("ActorTransform") or {}
    value = transform.get("Translation") if isinstance(transform, dict) else None
    if value is None: value = record.get("Location")
    if not isinstance(value, (list, tuple)) or len(value) < 3: return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value[:3]): return None
    return tuple(float(v) for v in value[:3])


def _resource(record: dict[str, Any], kind: str, name: str, resource_type: str = "") -> Resource | None:
    xyz = location_of(record)
    if xyz is None: return None
    path = str(record.get("ActorPathName") or "")
    return Resource(id=path or str(record.get("IcarusActorGUID") or record.get("ObjectFName")),
                    kind=kind, name=name, resource_type=resource_type, x=xyz[0], y=xyz[1], z=xyz[2],
                    actor_path=path, raw=record)


def _resource_name(key: str) -> str:
    name = RESOURCE_NAMES.get(key)
    if name and name.casefold() == key.casefold():
        return name
    return f"{name} ({key})" if name else key or "Unknown mineral"


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mineral_state(item: Resource, record: dict[str, Any]):
    state = record.get("VoxelSaveData") or {}
    key = state.get("VoxelResourceOverride")
    if key and key != "None":
        item.resource_type = str(key)
        item.name = _resource_name(str(key))
    if state.get("bIsVoxelFullyMined"):
        item.remaining = 0
        item.remaining_status = "Depleted"
        item.remaining_unit = "Base resource units"
        item.detail = "The save explicitly records this mineral as fully mined."
        return
    total, granted = state.get("TotalResourceCount"), state.get("NumResourcesGranted")
    if _is_number(total) and total > 0 and _is_number(granted) and granted >= 0:
        item.remaining = max(0.0, float(total) - float(granted))
        item.remaining_unit = "Base resource units (estimated)"
        item.remaining_status = "Estimated"
        item.detail = f"TotalResourceCount={total:g}, NumResourcesGranted={granted:g}; the difference is not guaranteed item yield. Tools and bonuses may affect output."
    elif state.get("MinedSpheres"):
        item.remaining_status = "Partially mined; quantity unknown"
        item.detail = "Mining geometry is recorded; no verifiable remaining resource count is available."
    else:
        item.detail = "The save provides no verifiable remaining amount; no mining record does not mean unmined."


def _drill_compatible(drill: Resource, deposit: Resource) -> bool:
    name = str(drill.raw.get("StaticItemDataRowName", "")) + str(drill.raw.get("ActorClassName", ""))
    return "Deep_Mining" in name and deposit.kind == "deep_deposit"


def associate_drills(resources: list[Resource], drills: list[Resource], complete: bool = True):
    deposits = [r for r in resources if r.kind == "deep_deposit"]
    ids: dict[int, list[Resource]] = {}
    for deposit in deposits:
        actor_id = deposit.raw.get("IcarusActorGUID")
        if isinstance(actor_id, int) and actor_id > 0:
            ids.setdefault(actor_id, []).append(deposit)
        deposit.drill_status = "Not installed" if complete else "Cannot confirm"
    claimed: set[str] = set()
    pending = []
    for drill in drills:
        foundation = drill.raw.get("DeployableRecord") or {}
        target_id = foundation.get("FoundationActorIcarusUID")
        candidates = [d for d in ids.get(target_id, []) if _drill_compatible(drill, d)] if isinstance(target_id, int) and target_id > 0 else []
        if len(candidates) == 1 and candidates[0].id not in claimed:
            _attach_drill(candidates[0], drill, "Confirmed", "FoundationActorIcarusUID")
            claimed.add(candidates[0].id)
        elif candidates:
            for deposit in candidates:
                deposit.drill_status = "Cannot confirm"
                deposit.detail += " Drill ID links conflict; a one-to-one match cannot be established."
        elif isinstance(target_id, int) and target_id > 0:
            # A recorded explicit target must never be overridden by proximity.
            drill.detail += " Original deposit ID link not found; nearby deposits were not substituted."
        else:
            pending.append(drill)
    # Conservative fallback only with no explicit target. Both directions must
    # have exactly one compatible candidate within 2.5 m in full 3D.
    possible = {d.id: [p for p in deposits if p.id not in claimed and _drill_compatible(d, p)
                       and "Deep_Mining_Ore_Deposit" in str(p.raw.get("ActorClassName", ""))
                       and math.dist(d.position, p.position) <= 250] for d in pending}
    count = Counter(p.id for values in possible.values() for p in values)
    for drill in pending:
        candidates = possible[drill.id]
        if len(candidates) == 1 and count[candidates[0].id] == 1:
            _attach_drill(candidates[0], drill, "Inferred", "3D distance ≤ 2.5 m; mutually unique compatible candidates")
        else:
            for deposit in candidates:
                deposit.drill_status = "Cannot confirm"
                deposit.detail += " Nearby drill has multiple possible matches."


def _attach_drill(deposit: Resource, drill: Resource, confidence: str, evidence: str):
    deposit.drill_status = "Installed" if confidence == "Confirmed" else "Installed (inferred)"
    state = drill.raw.get("DrillSaveData") or {}
    active = state.get("bDrillActive")
    operation = "Mining" if active is True or active == 1 else "Not running" if active is False or active == 0 else "Operating status unknown"
    deposit.detail += f" Drill: {drill.name}; {operation}; Match evidence: {evidence}."
    deposit.raw = dict(deposit.raw, _drill={"id": drill.id, "item": drill.resource_type,
                                             "active": active, "confidence": confidence,
                                             "evidence": evidence})


def _saved_datetime(top: dict[str, Any], mtime: float) -> datetime:
    ticks = top.get("LastSavedDateTime")
    if isinstance(ticks, int) and 0 < ticks < 3_155_378_975_999_999_999:
        try:
            # Unreal FDateTime: 100 ns ticks since 0001-01-01, UTC.
            return (datetime(1, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)).astimezone()
        except (ValueError, OverflowError): pass
    return datetime.fromtimestamp(mtime, timezone.utc).astimezone()


def load_snapshot(path: str | Path, cache_dir: str | Path | None = None) -> WorldSnapshot:
    source = Path(path).resolve()
    data, stat = _read_stable(source)
    envelope = _json_bytes(data)
    top, records, warnings = _unpack(envelope)
    info = envelope.get("ProspectInfo") or top.get("ProspectInfo") or {}
    if not isinstance(info, dict): raise SaveError("Invalid ProspectInfo format")
    key = str(info.get("ProspectDTKey") or "")
    map_key = str(top.get("ProspectMapName") or "")
    game_seed = next(((r.get("GameModeRecord") or {}).get("GameStateSeed") for r in records
                      if str(r.get("_component", "")).endswith("GameModeStateRecorderComponent")), None)
    world_token = f"{info.get('ClaimedAccountID', source.parent.parent.name)}|{info.get('ProspectID',source.stem)}|{key}|{game_seed}"
    snapshot = WorldSnapshot(source_path=source, world_id=hashlib.sha256(world_token.encode("utf-8")).hexdigest()[:24],
                             name=str(info.get("ProspectID") or source.stem), map_name=map_name_for(key, map_key),
                             saved_at=_saved_datetime(top, stat.st_mtime), loaded_at=datetime.now(timezone.utc).astimezone(),
                             sha256=hashlib.sha256(data).hexdigest(), source_mtime=stat.st_mtime,
                             source_mtime_ns=stat.st_mtime_ns, source_size=stat.st_size,
                             prospect_info=info, raw_records=records, warnings=warnings,
                             map_key=map_key, prospect_key=key, difficulty=str(info.get("Difficulty") or ""),
                             game_state_seed=game_seed if isinstance(game_seed, int) else None)
    members = {str(m.get("UserID")): str(m.get("CharacterName") or m.get("AccountName") or "Player")
               for m in info.get("AssociatedMembers", []) if isinstance(m, dict)}
    drills, game_mode, manager = [], {}, {}
    for record in records:
        component = str(record.get("_component") or "")
        actor = str(record.get("ActorClassName") or "")
        item = None
        if component.endswith("ResourceDepositRecorderComponent") or component.endswith("ResourceDepositIceRecorderComponent"):
            rtype = str(record.get("ResourceDTKey") or "")
            kind = "deep_deposit" if "Deep_Mining" in actor else "exotic" if "Exotic" in rtype else "ice" if "Ice" in actor else "deposit"
            item = _resource(record, kind, _resource_name(rtype), rtype)
            if item:
                remaining = record.get("ResourceRemaining")
                if _is_number(remaining) and remaining >= 0:
                    item.remaining, item.remaining_unit = float(remaining), "Saved resource units"
                    item.remaining_status = "Depleted" if remaining == 0 else "Confirmed"
                else:
                    item.detail = f"ResourceRemaining={remaining!r}; negative or missing values are not treated as infinite or zero."
        elif component.endswith("CaveEntranceRecorderComponent"):
            item = _resource(record, "cave", str(record.get("ObjectFName") or "Cave entrance"), "Cave")
            if item: item.detail = "Saved entrance location; does not describe the full cave extent."
        elif component.endswith("VoxelRecorderComponent"):
            item = _resource(record, "ore", "Unknown mineral", "Unknown")
            if item: _mineral_state(item, record)
        elif component.endswith("OilGeyserRecorderComponent"):
            item = _resource(record, "oil", "Crude oil geyser", "Oil")
        elif component.endswith("EnzymeGeyserRecorderComponent"):
            item = _resource(record, "geyser", "Enzyme geyser", "Enzyme")
        if item: snapshot.resources.append(item)
        if record.get("StaticItemDataRowName") and location_of(record):
            name = str(record["StaticItemDataRowName"])
            is_drill = component.endswith("DrillRecorderComponent") or "Deep_Mining_Drill" in name
            deployable = _resource(record, "drill" if is_drill else "deployable", name, name)
            if deployable:
                snapshot.deployables.append(deployable)
                if is_drill: drills.append(deployable)
                if "Bed" in name or "Bedroll" in name:
                    deployable.detail = "Base candidate: bed / bedroll location; requires user confirmation."
                    snapshot.base_candidates.append(deployable)
        if component.endswith("PlayerStateRecorderComponent"):
            pid = str((record.get("PlayerCharacterID") or {}).get("PlayerID") or "")
            player = _resource(record, "player", members.get(pid, "Player"), "Saved player position")
            if player:
                player.detail = "Position at the last save; not a live position."
                snapshot.players.append(player)
        if "Quest" in component or component.endswith("GameModeStateRecorderComponent"):
            snapshot.mission_records.append(record)
        if component.endswith("GameModeStateRecorderComponent"): game_mode = record
        if component.endswith("IcarusQuestManagerRecorderComponent"): manager = record
    deployments_complete = top.get("Version") == 2 and _deployments_complete(records)
    associate_drills(snapshot.resources, drills, complete=deployments_complete)
    from .drill_inventory import attach_drill_inventories
    attach_drill_inventories(snapshot, save_version=top.get("Version"))
    if not deployments_complete:
        snapshot.warnings.append("Some core deployable / drill fields could not be read. Unmatched deposits retain an unknown drill status.")
    counts = Counter(r.kind for r in snapshot.resources)
    snapshot.coverage = {"source": "Valid snapshot of the selected world", "record_count": len(records),
                         "resource_counts": dict(counts), "drill_count": len(drills),
                         "deployment_collection_complete": deployments_complete,
                         "all_map_resources_complete": False,
                         "limitations": ["Dynamic save records do not form a complete static resource catalog", "Plants, trees and water sources are not fully listed in the save"]}
    snapshot.mission_state = {"records": snapshot.mission_records,
                              "mission_history": game_mode.get("MissionHistory"),
                              "session_flags": (game_mode.get("GameModeRecord") or {}).get("SessionFlagRecords", []),
                              "quest_manager": manager,
                              "quests": [r for r in snapshot.mission_records if r.get("QuestName")]}
    if cache_dir is not None:
        cache = Path(cache_dir) / snapshot.world_id
        cache.mkdir(parents=True, exist_ok=True)
        final = cache / "latest.json"
        temporary = cache / f"latest.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(data)
            temporary.replace(final)
        finally:
            temporary.unlink(missing_ok=True)
        snapshot.cache_path = final
    return snapshot


def _deployments_complete(records: list[dict[str, Any]]) -> bool:
    """Only absence needs global completeness; explicit links remain useful.

    Unsupported inventory MapProperty or localized TextProperty does not hide
    the actor's existence. Unsupported actor/type/identity/attachment fields do.
    """
    for record in records:
        component = str(record.get("_component") or "")
        relevant = any(key in component for key in ("Drill", "Deployable", "BedRecorder", "ActorStateRecorder", "ResourceDeposit"))
        if not relevant: continue
        actor_version = record.get("ActorStateRecorderVersion")
        if actor_version is not None and actor_version != 3:
            return False
        if DRILL_CORE_FIELDS.intersection(record.get("_unsupported_fields") or []):
            return False
        if "DrillRecorder" in component or "Deep_Mining_Drill" in str(record.get("StaticItemDataRowName", "")):
            if not isinstance(record.get("StaticItemDataRowName"), str): return False
            if not isinstance(record.get("DeployableRecord"), dict): return False
            if not isinstance(record.get("ActorClassName"), str): return False
            if not isinstance(record.get("IcarusActorGUID"), int): return False
            if location_of(record) is None: return False
        elif "Deployable" in component:
            if not isinstance(record.get("StaticItemDataRowName"), str): return False
            if not isinstance(record.get("ActorClassName"), str): return False
            if not isinstance(record.get("IcarusActorGUID"), int): return False
            if location_of(record) is None: return False
    return True
