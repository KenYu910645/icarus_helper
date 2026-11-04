"""Small synthetic save fixtures: no private account or original world data."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest

from icarus_helper.models import Resource
from icarus_helper.saves import (
    SaveChangedError, SaveError, Reader, _deployments_complete, _mineral_state, _read_stable,
    associate_drills, discover_saves, load_snapshot, map_name_for, read_props,
)


def fstr(value):
    raw = value.encode("utf-8") + b"\0"
    return struct.pack("<i", len(raw)) + raw


def props(values):
    out = b""
    for name, value in values.items():
        extra = b"\0"
        if isinstance(value, str):
            typ, body = "StrProperty", fstr(value)
        elif isinstance(value, bool):
            typ, body, extra = "BoolProperty", b"", bytes([value, 0])
        elif isinstance(value, int):
            typ, body = "IntProperty", struct.pack("<i", value)
        elif isinstance(value, float):
            typ, body = "FloatProperty", struct.pack("<f", value)
        elif isinstance(value, dict):
            typ, body = "StructProperty", props(value)
            extra = fstr("TestStruct") + bytes(17)
        elif isinstance(value, bytes):
            typ, body = "ArrayProperty", struct.pack("<i", len(value)) + value
            extra = fstr("ByteProperty") + b"\0"
        elif isinstance(value, tuple):
            typ, body = "StructProperty", struct.pack("<fff", *value)
            extra = fstr("Vector") + bytes(17)
        elif isinstance(value, list):
            typ = "ArrayProperty"
            extra = fstr("StructProperty") + b"\0"
            header = fstr(name) + fstr("StructProperty") + struct.pack("<ii", 0, 0) + fstr("TestStruct") + bytes(17)
            body = struct.pack("<i", len(value)) + header + b"".join(props(v) for v in value)
        else:
            raise TypeError(value)
        out += fstr(name) + fstr(typ) + struct.pack("<ii", len(body), 0) + extra + body
    return out + fstr("None")


def save_fixture(path: Path, records=None, encoding="utf-8"):
    records = records or []
    raw = props({"StateRecorderBlobs": [
        {"ComponentClassName": component, "BinaryData": props(record)} for component, record in records
    ], "Version": 2, "ProspectMapName": "Terrain_019"})
    envelope = {"ProspectInfo": {"ProspectID": "測試World", "ClaimedAccountID": "synthetic", "ProspectDTKey": "OpenWorld_Prometheus"},
                "ProspectBlob": {"BinaryBlob": base64.b64encode(zlib.compress(raw)).decode(),
                                 "Hash": hashlib.sha1(raw).hexdigest(), "UncompressedLength": len(raw)}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding=encoding)
    return envelope


def point(id, kind="deep_deposit", xyz=(0, 0, 0), **raw):
    data = {"IcarusActorGUID": id, "ActorClassName": "BP_Deep_Mining_Ore_Deposit_C", **raw}
    return Resource(str(id), kind, "測試", "Iron", *xyz, raw=data)


def drill(id, target=None, xyz=(0, 0, 0)):
    return point(id, "drill", xyz, StaticItemDataRowName="Deep_Mining_Drill_Electric",
                 DeployableRecord={"FoundationActorIcarusUID": target}, DrillSaveData={"bDrillActive": False})


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le"])
def test_envelope_encoding_and_integrity(tmp_path, encoding):
    source = tmp_path / "原始" / "World.json"
    save_fixture(source, encoding=encoding)
    original = source.read_bytes()
    snapshot = load_snapshot(source, tmp_path / "cache")
    assert snapshot.name == "測試World"
    assert snapshot.map_name == "Prometheus"
    assert snapshot.sha256 == hashlib.sha256(original).hexdigest()
    assert snapshot.source_size == len(original)
    assert snapshot.source_mtime_ns == source.stat().st_mtime_ns
    assert source.read_bytes() == original
    assert snapshot.cache_path.read_bytes() == original


def test_discovery_ignores_backups_and_nonworlds(tmp_path):
    save_fixture(tmp_path / "account" / "Prospects" / "測試.json")
    (tmp_path / "account" / "Prospects" / "測試.json.backup").write_text("{}");
    (tmp_path / "account" / "Prospects" / "notworld.json").write_text("{}")
    result = discover_saves(tmp_path)
    assert len(result) == 1
    assert result[0].name == "測試World"


@pytest.mark.parametrize("field, value", [("Hash", "0" * 40), ("UncompressedLength", 1), ("BinaryBlob", "truncated!")])
def test_reject_corrupt_envelope(tmp_path, field, value):
    source = tmp_path / "world.json"
    data = save_fixture(source)
    data["ProspectBlob"][field] = value
    source.write_text(json.dumps(data))
    with pytest.raises(SaveError): load_snapshot(source)


def test_reject_incomplete_json(tmp_path):
    source = tmp_path / "world.json"
    source.write_bytes(b'{"ProspectBlob":')
    with pytest.raises(SaveError): load_snapshot(source)


def test_changed_during_read_is_retryable(tmp_path, monkeypatch):
    source = tmp_path / "world.json"
    source.write_bytes(b"123")
    read = Path.read_bytes
    def changing(path):
        data = read(path)
        path.write_bytes(b"longer")
        return data
    monkeypatch.setattr(Path, "read_bytes", changing)
    with pytest.raises(SaveChangedError): _read_stable(source)


def test_fully_mined_is_zero_but_absent_count_is_unknown():
    exhausted, unknown = point(1, "ore"), point(2, "ore")
    _mineral_state(exhausted, {"VoxelSaveData": {"bIsVoxelFullyMined": True}})
    _mineral_state(unknown, {"VoxelSaveData": {"VoxelResourceOverride": "None"}})
    assert exhausted.remaining == 0 and exhausted.remaining_status == "Depleted"
    assert unknown.remaining is None and unknown.remaining_status == "Quantity unknown"


def test_partial_mineral_is_estimate_not_final_item_count():
    ore = point(1, "ore")
    _mineral_state(ore, {"VoxelSaveData": {"TotalResourceCount": 40.0, "NumResourcesGranted": 10, "VoxelResourceOverride": "Gold"}})
    assert ore.remaining == 30 and ore.remaining_status == "Estimated"
    assert ore.resource_type == "Gold"
    assert "not guaranteed item yield" in ore.detail


def test_explicit_drill_link_wins_over_distance_and_not_operation():
    deposit, nearby = point(10, xyz=(10000, 0, 0)), point(11)
    machine = drill(100, target=10)
    associate_drills([deposit, nearby], [machine])
    assert deposit.drill_status == "Installed"
    assert nearby.drill_status == "Not installed"
    assert "Not running" in deposit.detail


def test_missing_explicit_target_does_not_attach_to_other_deposit():
    deposit = point(10)
    associate_drills([deposit], [drill(100, target=999)])
    assert deposit.drill_status == "Not installed"


def test_3d_fallback_uses_height_and_unambiguous_matching():
    low, high = point(10), point(11, xyz=(0, 0, 1000))
    associate_drills([low, high], [drill(100)])
    assert low.drill_status == "Installed (inferred)"
    assert high.drill_status == "Not installed"
    first, second = point(20), point(21, xyz=(100, 0, 0))
    associate_drills([first, second], [drill(200)])
    assert first.drill_status == second.drill_status == "Cannot confirm"


def test_two_drills_cannot_claim_same_deposit_as_confirmed():
    deposit = point(10)
    associate_drills([deposit], [drill(100, target=10), drill(101, target=10)])
    assert deposit.drill_status == "Cannot confirm"


def test_absent_actor_ids_do_not_form_false_confirmed_link():
    deposit = point(10)
    deposit.raw.pop("IcarusActorGUID")
    associate_drills([deposit], [drill(100)])
    assert deposit.drill_status == "Installed (inferred)"


def test_unknown_core_fields_make_deployment_absence_unknown():
    bad = {"_component": "/Script/Icarus.DrillRecorderComponent", "_unsupported_fields": ["FoundationActorIcarusUID"]}
    assert not _deployments_complete([bad])
    deposit = point(10)
    associate_drills([deposit], [], complete=_deployments_complete([bad]))
    assert deposit.drill_status == "Cannot confirm"
    inventory = {"_component": "/Script/Icarus.DeployableRecorderComponent", "StaticItemDataRowName": "Storage",
                 "ActorClassName": "BP_Storage_C", "IcarusActorGUID": 123,
                 "ActorTransform": {"Translation": [0, 0, 0]},
                 "_unsupported_fields": ["SavedInventories", "TextVariables"]}
    assert _deployments_complete([inventory])


def test_decoder_tracks_unknown_core_property_name():
    binary = fstr("IcarusActorGUID") + fstr("UInt64Property") + struct.pack("<ii", 8, 0) + b"\0" + bytes(8) + fstr("None")
    reader = Reader(binary)
    assert read_props(reader)["IcarusActorGUID"] is None
    assert "IcarusActorGUID" in reader.unsupported_fields


def test_deployment_collection_incomplete_keeps_absence_unknown():
    deposit = point(10)
    associate_drills([deposit], [], complete=False)
    assert deposit.drill_status == "Cannot confirm"


def test_world_identity_and_mission_state(tmp_path):
    source = tmp_path / "world.json"
    record = {"MissionHistory": [{"Mission": "PRO_Story_0", "Status": 1}],
              "GameModeRecord": {"GameStateSeed": 123}, "ActorTransform": {"Translation": (0.0, 0.0, 0.0)}}
    save_fixture(source, [("/Script/Icarus.GameModeStateRecorderComponent", record)])
    snapshot = load_snapshot(source)
    assert snapshot.mission_state["mission_history"][0]["Mission"] == "PRO_Story_0"
    assert snapshot.raw_records[0]["GameModeRecord"]["GameStateSeed"] == 123
    other = tmp_path / "other.json"
    envelope = save_fixture(other)
    envelope["ProspectInfo"]["ProspectID"] = "另一World"
    other.write_text(json.dumps(envelope))
    assert load_snapshot(other).world_id != snapshot.world_id


def test_prospect_explicit_map_name_has_priority():
    assert map_name_for("Outpost006_Olympus", "Terrain_016") == "Olympus"
    assert map_name_for("OpenWorld_Styx", "Terrain_016") == "Styx"


def test_recreated_same_name_world_with_other_seed_has_separate_identity(tmp_path):
    source = tmp_path / "world.json"
    record = {"GameModeRecord": {"GameStateSeed": 123}}
    save_fixture(source, [("/Script/Icarus.GameModeStateRecorderComponent", record)])
    original = load_snapshot(source)
    record["GameModeRecord"]["GameStateSeed"] = 456
    save_fixture(source, [("/Script/Icarus.GameModeStateRecorderComponent", record)])
    recreated = load_snapshot(source)
    assert original.world_id != recreated.world_id
    assert recreated.game_state_seed == 456


def test_out_of_bounds_property_is_rejected():
    with pytest.raises(SaveError): read_props(Reader(fstr("Broken") + fstr("StrProperty") + struct.pack("<ii", 999, 0) + b"\0"))


def test_save_transitions_mining_drill_placement_and_removal(tmp_path):
    source = tmp_path / "world.json"
    deposit = {"ResourceRemaining": 100, "ResourceDTKey": "Iron", "IcarusActorGUID": 10,
               "ActorClassName": "BP_Deep_Mining_Ore_Deposit_C", "ActorPathName": "world.deposit.10",
               "ActorTransform": {"Translation": (0.0, 0.0, 0.0)}}
    machine = {"IcarusActorGUID": 20, "StaticItemDataRowName": "Deep_Mining_Drill_Electric",
               "ActorClassName": "BP_Deep_Mining_Drill_Electric_C", "ActorPathName": "world.drill.20",
               "ActorTransform": {"Translation": (0.0, 0.0, 10.0)},
               "DeployableRecord": {"FoundationActorIcarusUID": 10},
               "DrillSaveData": {"bDrillActive": True}}
    resource_component = "/Script/Icarus.ResourceDepositRecorderComponent"
    save_fixture(source, [(resource_component, deposit)])
    first = load_snapshot(source)
    assert first.resources[0].remaining == 100
    assert first.resources[0].drill_status == "Not installed"
    deposit["ResourceRemaining"] = 50
    save_fixture(source, [(resource_component, deposit), ("/Script/Icarus.DrillRecorderComponent", machine)])
    second = load_snapshot(source)
    assert second.resources[0].remaining == 50
    assert second.resources[0].drill_status == "Installed"
    assert "Mining" in second.resources[0].detail
    assert first.resources[0].remaining == 100  # Immutable-by-separation snapshot.
    deposit["ResourceRemaining"] = 0
    save_fixture(source, [(resource_component, deposit)])
    third = load_snapshot(source)
    assert third.resources[0].remaining_status == "Depleted"
    assert third.resources[0].drill_status == "Not installed"


def test_invalid_refresh_does_not_replace_cached_good_snapshot(tmp_path):
    source = tmp_path / "world.json"
    save_fixture(source)
    snapshot = load_snapshot(source, tmp_path / "cache")
    cached = snapshot.cache_path.read_bytes()
    source.write_bytes(b"{incomplete")
    with pytest.raises(SaveError): load_snapshot(source, tmp_path / "cache")
    assert snapshot.cache_path.read_bytes() == cached


def test_negative_deposit_amount_is_not_claimed_infinite_or_zero(tmp_path):
    source = tmp_path / "world.json"
    deposit = {"ResourceRemaining": -1, "ResourceDTKey": "Iron", "IcarusActorGUID": 10,
               "ActorClassName": "BP_Deep_Mining_Ore_Deposit_C", "ActorPathName": "world.deposit.10",
               "ActorTransform": {"Translation": (0.0, 0.0, 0.0)}}
    save_fixture(source, [("/Script/Icarus.ResourceDepositRecorderComponent", deposit)])
    item = load_snapshot(source).resources[0]
    assert item.remaining is None
    assert item.remaining_status == "Quantity unknown"
