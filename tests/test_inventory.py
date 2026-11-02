"""Household totals must use actual stack counts and explicit spatial scope."""
from copy import deepcopy

import pytest

from icarus_helper import inventory


@pytest.fixture(autouse=True)
def validated_build(monkeypatch):
    monkeypatch.setattr(inventory, "installed_game_build", lambda: "26313227")


def slot(name, count=1, location=0, **extra):
    dynamic = [{"Index": 6, "Value": 98000}]
    if count is not None:
        dynamic.append({"Index": 7, "Value": count})
    return {"ItemStaticData": name, "Location": location, "DynamicData": dynamic, **extra}


def container(uid=1, xyz=(0, 0, 0), slots=(), component="DeployableRecorderComponent", inv_id=1):
    return {"IcarusActorGUID": uid, "StaticItemDataRowName": "Kit_Metal_Cupboard",
            "ActorTransform": {"Translation": xyz}, "_component": "/Script/Icarus." + component,
            "SavedInventories": [{"InventoryID": inv_id, "Slots": list(slots)}]}


def world(*records, **extra):
    return {"world_id": "world-A", "map_name": "Prometheus", "raw_records": list(records), **extra}


def summarize(*records, base=None, radius=100):
    return inventory.summarize_inventory(world(*records), base or {"name": "我們家", "x": 0, "y": 0, "z": 0}, radius)


def item(result, key):
    return next(it for it in result["items"] if it["id"] == key)


def test_actual_ore_and_ingots_share_material_without_conversion():
    result = summarize(container(slots=[slot("Gold_Ore", 53), slot("Refined_Gold", 7, 1),
                                         slot("Copper_Ore", 26, 2), slot("Refined_Copper", 2, 3), slot("Coal_Ore", 11, 4)]))
    metals = next(c for c in result["categories"] if c["id"] == "resources")
    rows = {row["key"]: row for row in metals["rows"]}
    assert rows["gold"]["values"] == {"ore": 53, "ingot": 7}
    assert rows["copper"]["values"] == {"ore": 26, "ingot": 2}
    assert rows["Coal_Ore"]["values"] == {"other": 11}
    assert len(result["categories"]) == 1
    assert item(result, "Coal_Ore")["source_category"] == "minerals"
    assert {s["id"] for s in metals["series"]} == {"ore", "ingot", "other"}
    assert result["coverage"]["known_quantity"] == 99


def test_stack_property_is_not_slot_index_or_durability():
    result = summarize(container(slots=[slot("Gold_Ore", 4, 27), slot("Refined_Gold", None, 50)]))
    assert item(result, "Gold_Ore")["quantity"] == 4
    assert item(result, "Refined_Gold")["quantity"] is None
    assert item(result, "Refined_Gold")["known_quantity"] == 0
    assert result["coverage"]["unknown_stacks"] == 1


def test_unknown_quantity_preserves_known_portion_and_chart_uncertainty():
    result = summarize(container(slots=[slot("Gold_Ore", 12), slot("Gold_Ore", None, 1)]))
    assert item(result, "Gold_Ore")["quantity"] is None
    assert item(result, "Gold_Ore")["known_quantity"] == 12
    row = result["categories"][0]["rows"][0]
    assert row["values"]["ore"] is None
    assert row["known_values"]["ore"] == 12
    assert result["coverage"]["status"] == "partial"


def test_only_deployed_storage_and_processors_inside_horizontal_radius():
    records = [container(1, (6000, 8000, 90000), [slot("Gold_Ore", 5)]),
               container(2, (10000.1, 0, 0), [slot("Gold_Ore", 100)]),
               container(3, slots=[slot("Gold_Ore", 100)], component="PlayerStateRecorderComponent"),
               container(4, slots=[slot("Gold_Ore", 100)], component="IcarusMountCharacterRecorderComponent"),
               container(5, slots=[slot("Gold_Ore", 100)], component="ItemStateRecorderComponent"),
               container(6, slots=[slot("Gold_Ore", 100)], component="IcarusContainerManagerRecorderComponent"),
               container(7, slots=[slot("Gold_Ore", 2)], component="DrillRecorderComponent", inv_id=6)]
    result = summarize(*records)
    assert item(result, "Gold_Ore")["quantity"] == 7
    assert result["coverage"]["counted_containers"] == 2
    assert result["coverage"]["outside_radius"] == 1
    assert result["coverage"]["excluded_containers"] == 4
    assert result["containers"][0]["distance_m"] == 100


def test_equipped_and_installed_upgrade_slots_are_not_stock():
    result = summarize(container(1, slots=[slot("Gold_Ore", 10)], inv_id=1),
                       container(2, slots=[slot("Gold_Ore", 20)], inv_id=5),
                       container(3, slots=[slot("Gold_Ore", 30)], inv_id=18),
                       container(4, slots=[slot("Gold_Ore", 40)], inv_id=23))
    assert item(result, "Gold_Ore")["quantity"] == 10
    assert result["coverage"]["excluded_inventory_slots"] == 3


def test_duplicate_actors_inventories_and_slots_do_not_double_count():
    record = container(slots=[slot("Gold_Ore", 17)])
    record["SavedInventories"].append(deepcopy(record["SavedInventories"][0]))
    result = summarize(record, deepcopy(record))
    assert item(result, "Gold_Ore")["quantity"] == 17
    assert result["coverage"]["duplicate_records"] == 2


def test_conflicting_duplicate_actor_is_excluded_and_reported():
    result = summarize(container(slots=[slot("Gold_Ore", 17)]), container(slots=[slot("Gold_Ore", 19)]))
    assert result["items"] == []
    assert result["coverage"]["conflicting_records"] == 1
    assert result["coverage"]["status"] == "partial"


def test_conflicting_slot_keeps_both_candidate_items_with_unknown_counts():
    result = summarize(container(slots=[slot("Gold_Ore", 17), slot("New_DLC_Resource", 19)]))
    assert item(result, "Gold_Ore")["quantity"] is None
    assert item(result, "New_DLC_Resource")["quantity"] is None
    assert result["coverage"]["conflicting_slots"] == 1


def test_malformed_or_unrecognized_inventory_is_explicitly_partial():
    record = container(slots=[])
    record["SavedInventories"] += [{"InventoryID": 600, "Slots": [slot("Gold_Ore", 20)]},
                                   {"InventoryID": [], "Slots": []}, {"InventoryID": 6, "Slots": {}}]
    result = summarize(record)
    assert not result["items"]
    assert result["coverage"]["unknown_inventory_ids"] == [600]
    assert result["coverage"]["malformed_inventories"] == 2
    assert result["coverage"]["status"] == "partial"


def test_unrecognized_item_is_retained_with_count_and_case_insensitive_fname():
    result = summarize(container(slots=[slot("New_DLC_Resource", 8), slot("gold_ore", 13, 1)]))
    assert item(result, "New_DLC_Resource")["quantity"] == 8
    assert item(result, "New_DLC_Resource")["category"] == "unknown"
    assert item(result, "Gold_Ore")["quantity"] == 13
    assert result["coverage"]["unknown_items"] == 1


def test_empty_and_zero_slots_do_not_become_items():
    result = summarize(container(slots=[slot("None", 100), slot("", 1, 1), slot("Gold_Ore", 0, 2)]))
    assert result["items"] == []
    assert result["coverage"]["empty_slots"] == 3
    assert result["coverage"]["status"] == "ok"


@pytest.mark.parametrize("value", [-1, 1.5, float("nan"), True, "5"])
def test_invalid_stack_quantity_remains_unknown(value):
    result = summarize(container(slots=[slot("Gold_Ore", value)]))
    assert item(result, "Gold_Ore")["quantity"] is None


def test_no_base_wrong_world_and_unknown_version_cannot_look_like_zero(monkeypatch):
    data = world(container(slots=[slot("Gold_Ore", 10)]))
    assert inventory.summarize_inventory(data, None)["coverage"]["status"] == "base_required"
    mismatched = {"x": 0, "y": 0, "world_id": "world-B"}
    assert inventory.summarize_inventory(data, mismatched)["coverage"]["status"] == "base_mismatch"
    monkeypatch.setattr(inventory, "installed_game_build", lambda: "future")
    result = inventory.summarize_inventory(data, {"x": 0, "y": 0})
    assert result["coverage"]["status"] == "unsupported_version"
    assert result["coverage"]["reason"] and not result["items"]


def test_missing_inventory_source_is_unavailable_and_bad_location_is_partial():
    assert summarize()["coverage"]["status"] == "unavailable"
    record = container(slots=[slot("Gold_Ore", 20)])
    record.pop("ActorTransform")
    result = summarize(record)
    assert result["coverage"]["missing_locations"] == 1
    assert not result["items"]
    assert result["coverage"]["status"] == "partial"


def test_steel_is_separate_material_and_miasma_is_not_iron():
    result = summarize(container(slots=[slot("Steel_Ingot", 6), slot("Refined_Metal", 8, 1), slot("Raw_Iron_Wood", 9, 2)]))
    assert item(result, "Steel_Ingot")["material"] == "steel"
    assert item(result, "Refined_Metal")["material"] == "iron"
    assert item(result, "Raw_Iron_Wood")["source_category"] != "metals"
    assert item(result, "Raw_Iron_Wood")["material"] != "iron"


def test_nested_inventory_reference_does_not_recount_global_equipment_pool():
    stored = slot("Rifle_BoltAction", 1)
    stored["DynamicData"].append({"Index": 13, "Value": 7})
    result = summarize(container(slots=[stored]), container(2, slots=[slot("Gold_Ore", 500)], component="IcarusContainerManagerRecorderComponent"))
    assert len(result["items"]) == 1
    assert result["coverage"]["linked_item_inventories"] == 1


def test_catalog_retains_real_game_layout_and_source_provenance():
    catalog = inventory.load_item_catalog()
    assert catalog["dynamic_properties"]["ItemableStack"] == 7
    assert catalog["dynamic_properties"]["Durability"] == 6
    assert len(catalog["sources"]) == 3
    assert len(catalog["executable_sha256"]) == 64
    assert catalog["items"]["Bauxite"]["material"] == "aluminium"
