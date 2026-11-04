"""Per-drill stock uses real output slots, never fuel or deposit reserves."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from icarus_helper import drill_inventory as stock
from icarus_helper.models import Resource, WorldSnapshot
from icarus_helper.saves import associate_drills, load_snapshot


@pytest.fixture(autouse=True)
def validated_build(monkeypatch):
    monkeypatch.setattr(stock, "installed_game_build", lambda: "26313227")


def slot(item="Gold_Ore", count=50, location=0):
    return {"ItemStaticData": item, "Location": location,
            "DynamicData": [{"Index": 6, "Value": 99999}] + ([] if count is None else [{"Index": 7, "Value": count}])}


def record(*slots, uid=2, target=1):
    return {"_component": "/Script/Icarus.DrillRecorderComponent", "IcarusActorGUID": uid,
            "StaticItemDataRowName": "Deep_Mining_Drill_Electric", "ActorClassName": "BP_Deep_Mining_Drill_Electric_C",
            "DeployableRecord": {"FoundationActorIcarusUID": target}, "ActorTransform": {"Translation": (0, 0, 0)},
            "SavedInventories": [{"InventoryID": 1, "Slots": list(slots)}]}


def test_output_counts_do_not_include_fuel_upgrade_or_durability():
    data = record(slot(count=50), slot(count=14, location=1))
    data["SavedInventories"] += [{"InventoryID": 7, "Slots": [slot("Jerrycan", 1)]},
                                 {"InventoryID": 23, "Slots": [slot("Gold_Ore", 9000)]}]
    original = deepcopy(data)
    result = stock.summarize_drill_inventory(data)
    assert result["status"] == "ok" and result["quantity"] == 64
    assert result["items"][0]["slots"] == [0, 1]
    assert result["excluded_inventory_ids"] == [7, 23]
    assert data == original


def test_empty_output_is_zero_but_missing_output_is_unknown():
    assert stock.summarize_drill_inventory(record())["quantity"] == 0
    assert stock.summarize_drill_inventory(record())["status"] == "empty"
    for value in (None, [], [{"InventoryID": 7, "Slots": []}], [{"InventoryID": 1}]):
        result = stock.summarize_drill_inventory(dict(record(), SavedInventories=value))
        assert result["quantity"] is None
        assert result["status"] in {"unavailable", "partial"}


@pytest.mark.parametrize("count", [None, -1, 1.5, True, "50", float("nan")])
def test_unknown_or_invalid_counts_do_not_become_zero_or_max_stack(count):
    result = stock.summarize_drill_inventory(record(slot(count=23), slot(count=count, location=1)))
    assert result["status"] == "partial" and result["quantity"] is None
    assert result["known_quantity"] == 23
    assert result["items"][0]["quantity"] is None
    assert result["items"][0]["unknown_stacks"] == 1


def test_repeated_slots_and_identical_inventory_copies_are_not_added_twice():
    data = record(slot(count=13), slot(count=13))
    data["SavedInventories"].append(deepcopy(data["SavedInventories"][0]))
    result = stock.summarize_drill_inventory(data)
    assert result["quantity"] == 13 and result["duplicate_slots"] == 1


def test_conflicting_slot_retains_candidates_with_unknown_counts():
    result = stock.summarize_drill_inventory(record(slot(count=13), slot("Copper_Ore", 15)))
    assert result["quantity"] is None and result["known_quantity"] == 0
    assert result["conflicting_slots"] == 1
    assert {item["id"] for item in result["items"]} == {"Gold_Ore", "Copper_Ore"}
    assert all(item["quantity"] is None for item in result["items"])


def test_different_output_inventory_copies_are_not_union_counted():
    data = record(slot(count=13))
    data["SavedInventories"].append({"InventoryID": 1, "Slots": []})
    result = stock.summarize_drill_inventory(data)
    assert result["quantity"] is None and result["known_quantity"] == 0
    assert result["items"][0]["quantity"] is None


def test_unlocated_or_malformed_slots_do_not_establish_complete_count():
    unlocated = slot(count=12)
    unlocated.pop("Location")
    for slots in ([unlocated], [None], [{"Location": 0}], [dict(slot(), Location=True)]):
        result = stock.summarize_drill_inventory(record(*slots))
        assert result["status"] == "partial" and result["quantity"] is None


def test_explicit_none_and_zero_stacks_are_empty():
    result = stock.summarize_drill_inventory(record(slot("None"), slot("", location=1), slot(count=0, location=2)))
    assert result["status"] == "empty" and result["quantity"] == 0 and result["items"] == []


def test_unsupported_build_actor_version_or_fields_do_not_supply_counts(monkeypatch):
    data = record(slot())
    monkeypatch.setattr(stock, "installed_game_build", lambda: "future")
    assert stock.summarize_drill_inventory(data)["status"] == "unsupported_version"
    monkeypatch.setattr(stock, "installed_game_build", lambda: "26313227")
    for invalid in (dict(data, ActorStateRecorderVersion=99), dict(data, _unsupported_fields=["DynamicData"])):
        result = stock.summarize_drill_inventory(invalid)
        assert result["quantity"] is None and result["status"] == "unavailable"


def test_unsupported_drill_type_does_not_reuse_known_format():
    result = stock.summarize_drill_inventory(dict(record(slot()), StaticItemDataRowName="Future_Deep_Mining_Drill"))
    assert result["quantity"] is None and result["status"] == "unavailable"


def snapshot_with(*drill_records):
    deposit_raw = {"IcarusActorGUID": 1, "ActorClassName": "BP_Deep_Mining_Ore_Deposit_C"}
    deposit = Resource("deposit", "deep_deposit", "金", "Gold", 0, 0, 0, remaining=999999, raw=deposit_raw)
    drills = [Resource(str(r["IcarusActorGUID"]), "drill", "鑽機", r["StaticItemDataRowName"], 0, 0, 0, raw=r) for r in drill_records]
    now = datetime.now(timezone.utc)
    result = WorldSnapshot(Path("synthetic.json"), "A", "測試", "Prometheus", now, now, "a" * 64,
                           resources=[deposit], deployables=drills, raw_records=[deposit_raw, *drill_records])
    associate_drills(result.resources, drills)
    return result


def test_attached_summary_has_source_and_does_not_modify_raw_records_or_reserves():
    world = snapshot_with(record(slot(count=27)))
    original = deepcopy(world.raw_records)
    stock.attach_drill_inventories(world, save_version=2)
    drill, deposit = world.deployables[0], world.resources[0]
    assert world.raw_records == original
    assert deposit.remaining == 999999
    summary = stock.inventory_for_point(deposit)
    assert summary == stock.inventory_for_point(drill)
    assert summary is not drill.raw["_drill_inventory"]
    assert summary["quantity"] == 27 and summary["saved_at"] == world.saved_at.isoformat()
    assert summary["world_id"] == "A" and summary["source_sha256"] == "a" * 64


def test_conflicting_actor_does_not_publish_or_retain_old_deposit_inventory():
    world = snapshot_with(record(slot(count=27)), record(slot(count=29)))
    world.resources[0].raw["_drill"]["inventory"] = {"quantity": 123}
    stock.attach_drill_inventories(world, save_version=2)
    assert stock.inventory_for_point(world.resources[0]) is None
    assert "inventory" not in world.resources[0].raw["_drill"]
    assert all(stock.inventory_for_point(drill)["status"] == "conflict" for drill in world.deployables)


def test_missing_explicit_target_or_far_height_does_not_transfer_other_drill_stock():
    world = snapshot_with(record(slot(count=27), target=999))
    stock.attach_drill_inventories(world, save_version=2)
    assert stock.inventory_for_point(world.resources[0]) is None
    assert stock.inventory_for_point(world.deployables[0])["quantity"] == 27


def test_summary_from_another_drill_id_is_rejected_even_if_old_status_says_installed():
    world = snapshot_with(record(slot(count=27)))
    stock.attach_drill_inventories(world, save_version=2)
    deposit = world.resources[0]
    deposit.raw["_drill"]["inventory"]["drill_id"] = "another-id"
    assert stock.inventory_for_point(deposit) is None


def test_load_snapshot_hooks_summary_to_validated_same_save(tmp_path):
    from test_saves import save_fixture
    source = tmp_path / "合成鑽機.json"
    raw = record(slot(count=17))
    raw.pop("_component")
    save_fixture(source, [("/Script/Icarus.ResourceDepositRecorderComponent",
                           {"IcarusActorGUID": 1, "ActorClassName": "BP_Deep_Mining_Ore_Deposit_C",
                            "ResourceDTKey": "Gold", "ActorTransform": {"Translation": (0, 0, 0)}}),
                          ("/Script/Icarus.DrillRecorderComponent", raw)])
    before = source.read_bytes()
    world = load_snapshot(source)
    assert stock.inventory_for_point(world.resources[0])["quantity"] == 17
    assert stock.inventory_for_point(world.deployables[0])["source_sha256"] == world.sha256
    assert source.read_bytes() == before


@pytest.mark.parametrize("field,value", [("DeployableRecord", {"FoundationActorIcarusUID": 999}),
                                         ("ActorClassName", "Other_Drill_Class"),
                                         ("DrillSaveData", {"bDrillActive": True}),
                                         ("ActorStateRecorderVersion", 99)])
def test_duplicate_actor_conflict_includes_foundation_class_operation_and_version(field, value):
    first = record(slot(count=27))
    second = dict(deepcopy(first), **{field: value})
    world = snapshot_with(first, second)
    stock.attach_drill_inventories(world, save_version=2)
    assert all(stock.inventory_for_point(drill)["status"] == "conflict" for drill in world.deployables)


def test_unlocated_duplicate_raw_actor_cannot_hide_a_conflict():
    first = record(slot(count=27))
    world = snapshot_with(first)
    second = dict(deepcopy(first), ActorTransform={})
    world.raw_records.append(second)
    stock.attach_drill_inventories(world, save_version=2)
    assert stock.inventory_for_point(world.deployables[0])["status"] == "conflict"
    assert stock.inventory_for_point(world.resources[0])["quantity"] is None


@pytest.mark.parametrize("version", [None, 1, 99, True, "2"])
def test_unknown_save_format_cannot_reuse_supported_inventory_quantities(version):
    world = snapshot_with(record(slot(count=27)))
    stock.attach_drill_inventories(world, save_version=version)
    assert stock.inventory_for_point(world.resources[0])["status"] == "unsupported_version"
    assert stock.inventory_for_point(world.resources[0])["quantity"] is None


def test_two_distinct_drills_on_one_deposit_leave_both_individual_stocks_but_no_deposit_stock():
    world = snapshot_with(record(slot(count=27), uid=2), record(slot(count=29), uid=3))
    stock.attach_drill_inventories(world, save_version=2)
    assert world.resources[0].drill_status == "Cannot confirm"
    assert stock.inventory_for_point(world.resources[0]) is None
    assert [stock.inventory_for_point(point)["quantity"] for point in world.deployables] == [27, 29]
