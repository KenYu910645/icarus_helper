"""Ownership, uncertainty and inventory integrity use synthetic fixtures only."""
from copy import deepcopy
import json

import pytest

from icarus_helper import animals


BUILD = "26313227"
CATALOG = {
    "game_build": BUILD, "supported_recorder_versions": [3], "level_rule": None,
    "species": {"Mount_Moa": {"name": "恐鳥", "english": "Moa"}, "Cow": {"name": "牛", "english": "Cow"},
                "Juvenile_Moa": {"name": "恐鳥幼獸", "english": "Juvenile Moa"}},
    "enums": {"movement": {"EMountMovementBehaviourState::IdleStanding": "Stand idle"},
              "combat": {}, "consumption": {}, "grazing": {}, "sex": {"1": "測試Sex"}},
    "modifiers": {"ReadyToMilk": {"name": "可以擠奶"}},
    "genetics": {}, "talents": {}, "lineages": {},
}
ITEMS = {"game_build": BUILD, "items": {"Wood": {"name": "木材"}, "Saddle": {"name": "Saddle"}},
         "dynamic_properties": {"ItemableStack": 7, "InventoryContainer_LinkedInventoryId": 13}}


@pytest.fixture(autouse=True)
def verified_catalogs(monkeypatch):
    monkeypatch.setattr(animals, "installed_game_build", lambda: BUILD)
    monkeypatch.setattr(animals, "load_animal_catalog", lambda: deepcopy(CATALOG))
    monkeypatch.setattr(animals, "load_item_catalog", lambda: deepcopy(ITEMS))


def slot(item="Wood", quantity=3, location=0, **extra):
    dynamic = [{"Index": 6, "Value": 98000}]
    if quantity is not None:
        dynamic.append({"Index": 7, "Value": quantity})
    return {"ItemStaticData": item, "Location": location, "DynamicData": dynamic, **extra}


def tame(uid=1, xyz=(0, 0, 0), component="IcarusMountCharacterRecorderComponent", **extra):
    return {"_component": "/Script/Icarus." + component, "ActorStateRecorderVersion": 3,
            "IcarusActorGUID": uid, "MountName": "測試動物", "AISetupRowName": "Mount_Moa",
            "OwnerCharacterID": {"PlayerID": "SYNTHETIC_PRIVATE_PLAYER", "ChrSlot": 0},
            "OwnerName": "測試Player", "BoolVariables": [{"VariableName": "bIsWildTame", "bVariable": False}],
            "IntVariables": [{"VariableName": "LastLevelAchieved", "iVariable": 31}],
            "CharacterRecord": {"CurrentHealth": 120}, "FoodLevel": 80, "WaterLevel": 90,
            "OxygenLevel": 100, "Stamina": 45, "Experience": 1000, "Modifiers": [],
            "MovementBehaviourState": "EMountMovementBehaviourState::IdleStanding",
            "ActorTransform": {"Translation": list(xyz)},
            "SavedInventories": [{"InventoryID": 19, "Slots": [slot()]}], **extra}


def world(*records, **extra):
    return {"world_id": "synthetic-world", "map_name": "Prometheus", "saved_at": "2026-01-01T00:00:00+08:00",
            "raw_records": list(records), **extra}


def summarize(*records, base=None, **kwargs):
    return animals.summarize_animals(world(*records), base, **kwargs)


def test_mount_cow_and_explicit_tamed_juvenile_are_admitted():
    cow = tame(2, component="IcarusCowCharacterRecorderComponent", AISetupRowName="Cow", CurrentMilk=3000,
               SavedInventories=[], Modifiers=[{"RowName": "ReadyToMilk"}])
    juvenile = tame(3, component="IcarusJuvenileRecorderComponent", AISetupRowName="Juvenile_Moa",
                    OwnerCharacterID={}, TamedState="ETamedState::Tamed", OwnerName="")
    result = summarize(tame(), cow, juvenile)
    assert result["coverage"]["total_tamed"] == 3
    selected_cow = next(a for a in result["animals"] if a["id"] == "2")
    assert selected_cow["species"] == "Cow"
    assert selected_cow["inventory_status"] == "empty"
    assert next(s for s in selected_cow["stats"] if s["id"] == "milk")["value"] == 3000
    assert selected_cow["status_label"] == "可以擠奶"
    assert next(a for a in result["animals"] if a["id"] == "3")["owner_name"] == "Unknown"


@pytest.mark.parametrize("changes", [
    {"OwnerCharacterID": {"PlayerID": "", "ChrSlot": -1}},
    {"OwnerCharacterID": {"PlayerID": "-1", "ChrSlot": 0}},
    {"OwnerCharacterID": {"PlayerID": "null", "ChrSlot": 0}},
    {"BoolVariables": [{"VariableName": "bIsWildTame", "bVariable": True}]},
    {"TamedState": "ETamedState::NotTamed"},
    {"TamedState": 0},
    {"_component": "/Script/Icarus.IcarusJuvenileRecorderComponent"},
    {"OwnerCharacterID": {}, "TamedState": "ETamedState::NotTamed",
     "LastPlayerLeaderID": {"PlayerID": "SYNTHETIC_PRIVATE_PLAYER", "ChrSlot": 0}},
    {"OwnerCharacterID": {"PlayerID": "SYNTHETIC_PRIVATE_PLAYER", "ChrSlot": -1}},
    {"OwnerCharacterID": {}, "BoolVariables": [], "TamedState": "ETamedState::Tamed"},
])
def test_name_near_home_or_leader_never_proves_player_taming(changes):
    result = summarize(tame(**changes), base={"x": 0, "y": 0})
    assert result["animals"] == []


def test_worldwide_animals_preserved_and_distance_is_horizontal_optional():
    result = summarize(tame(), tame(2, (30000, 40000, 90000)), base={"x": 0, "y": 0}, radius_m=100)
    assert result["coverage"]["total_tamed"] == 2
    assert result["coverage"]["at_home"] == 1
    assert result["coverage"]["outside_home"] == 1
    outside = next(a for a in result["animals"] if a["id"] == "2")
    assert outside["distance_m"] == 500
    assert outside["at_home"] is False
    unbased = summarize(tame())["animals"][0]
    assert unbased["distance_m"] is None and unbased["at_home"] is None


def test_wrong_world_base_does_not_change_ownership_or_fabricate_distance():
    result = summarize(tame(), base={"x": 0, "y": 0, "world_id": "different-world"})
    assert len(result["animals"]) == 1
    assert result["animals"][0]["distance_m"] is None
    assert result["coverage"]["warnings"]


def test_missing_stats_and_position_are_unknown_but_zero_is_real():
    record = tame(FoodLevel=None, WaterLevel=0, ActorTransform={})
    record.pop("CharacterRecord")
    row = summarize(record)["animals"][0]
    stats = {s["id"]: s for s in row["stats"]}
    assert stats["food"]["value"] is None
    assert stats["health"]["value"] is None
    assert stats["water"]["value"] == 0
    assert all(s["maximum"] is None for s in row["stats"])
    assert row["position"] is None and row["at_home"] is None
    assert "Water is 0" in row["status_label"]


def test_raw_last_level_is_not_presented_as_verified_level():
    row = summarize(tame())["animals"][0]
    assert row["level"] is None
    assert "not been verified" in row["level_note"]
    assert {"label": "Last recorded level (raw)", "value": "31"} in row["details"]


def test_explicit_verified_saved_level_rule_is_required():
    catalog = deepcopy(CATALOG)
    catalog["level_rule"] = {"verified": True, "type": "saved_integer", "field": "LastLevelAchieved", "offset": 0}
    assert summarize(tame(), catalog=catalog)["animals"][0]["level"] == 31


@pytest.mark.parametrize("experience,expected", [(0, 0), (9, 0), (10, 1), (29, 1), (30, 2), (9000, 2)])
def test_verified_curve_uses_threshold_boundaries_without_plus_one(experience, expected):
    catalog = deepcopy(CATALOG)
    catalog["species"]["Mount_Moa"]["level_growth_id"] = "SyntheticCurve"
    catalog["level_rule"] = {"type": "curve_thresholds", "verified": True, "curves": {
        "SyntheticCurve": {"thresholds": [0, 10, 30], "max_level": 2, "max_display_level": 2}}}
    row = summarize(tame(Experience=experience), catalog=catalog)["animals"][0]
    assert row["level"] == expected


def test_curve_display_cap_and_invalid_curve_fail_closed():
    catalog = deepcopy(CATALOG)
    catalog["species"]["Mount_Moa"]["level_growth_id"] = "SyntheticCurve"
    curve = {"thresholds": [0, 10, 30], "max_level": 2, "max_display_level": 1}
    catalog["level_rule"] = {"type": "curve_thresholds", "verified": True,
                             "curves": {"SyntheticCurve": curve}}
    assert summarize(tame(Experience=30), catalog=catalog)["animals"][0]["level"] == 1
    curve["thresholds"] = [0, 30, 10]
    assert summarize(tame(Experience=30), catalog=catalog)["animals"][0]["level"] is None


def test_saddle_cargo_and_heavy_cargo_are_separate_and_use_stack_property():
    record = tame(SavedInventories=[{"InventoryID": 17, "Slots": [slot("Saddle", 1)]},
                                   {"InventoryID": 19, "Slots": [slot("wood", 100, 27)]},
                                   {"InventoryID": 20, "Slots": []}])
    row = summarize(record)["animals"][0]
    assert [i["id"] for i in row["inventories"]] == [17, 19, 20]
    assert row["inventories"][1]["items"][0] == {"id": "Wood", "name": "木材", "quantity": 100,
        "slot": 27, "warnings": [], "nested_inventory_status": "none"}
    assert row["inventories"][2]["status"] == "empty"


def test_missing_and_empty_inventories_have_distinct_states():
    missing = tame()
    missing.pop("SavedInventories")
    assert summarize(missing)["animals"][0]["inventory_status"] == "unavailable"
    assert summarize(tame(SavedInventories=[]))["animals"][0]["inventory_status"] == "empty"


def test_unknown_quantity_is_not_replaced_by_durability_or_slot_number():
    result = summarize(tame(SavedInventories=[{"InventoryID": 19, "Slots": [slot(quantity=None, location=80)]}]))
    row = result["animals"][0]["inventories"][0]["items"][0]
    assert row["quantity"] is None
    assert result["coverage"]["unknown_stacks"] == 1
    assert result["coverage"]["status"] == "partial"


def test_duplicate_actors_and_slots_do_not_duplicate_inventory():
    record = tame()
    record["SavedInventories"].append(deepcopy(record["SavedInventories"][0]))
    result = summarize(record, deepcopy(record))
    assert result["coverage"]["duplicate_records"] == 1
    assert result["coverage"]["duplicate_slots"] == 1
    assert len(result["animals"][0]["inventories"][0]["items"]) == 1


def test_conflicting_actor_ownership_is_excluded_before_ownership_filtering():
    record = tame()
    conflict = tame(OwnerCharacterID={"PlayerID": "", "ChrSlot": -1})
    result = summarize(record, conflict)
    assert result["animals"] == []
    assert result["coverage"]["conflicting_records"] == 1


def test_conflicting_slots_preserve_candidates_with_unknown_quantity():
    record = tame(SavedInventories=[{"InventoryID": 19, "Slots": [slot(quantity=3), slot(quantity=8)]}])
    result = summarize(record)
    assert result["coverage"]["conflicting_slots"] == 1
    assert result["animals"][0]["inventories"][0]["items"][0]["quantity"] is None


def test_linked_item_inventory_is_not_reported_as_fully_expanded():
    record = tame(SavedInventories=[{"InventoryID": 19, "Slots": [slot(DynamicData=[{"Index": 7, "Value": 1}, {"Index": 13, "Value": 777}])]}])
    result = summarize(record)
    assert result["coverage"]["unexpanded_item_inventories"] == 1
    row = result["animals"][0]
    assert row["inventory_status"] == "partial"
    assert row["inventories"][0]["items"][0]["nested_inventory_status"] == "unexpanded"


def test_malformed_and_unknown_inventory_data_remain_visible_as_partial():
    record = tame(SavedInventories=[{"InventoryID": 999, "Slots": [slot("Future_Item", 2)]},
                                   {"InventoryID": 19, "Slots": {}}])
    result = summarize(record)
    assert result["coverage"]["unknown_inventory_ids"] == [999]
    assert result["coverage"]["malformed_inventories"] == 1
    inventories = {entry["id"]: entry for entry in result["animals"][0]["inventories"]}
    assert inventories[999]["items"][0]["name"] == "Future_Item"
    assert inventories[19]["status"] == "partial"


@pytest.mark.parametrize("slots", [
    None, {}, [None], [{}], [{"Location": 0, "StackCount": 3}],
    [{"Location": 0, "ItemStaticData": 5}],
    [{"Location": 0, "ItemStaticData": {}}],
    [{"Location": 0, "ItemStaticData": {"RowName": None}}],
])
def test_unreadable_inventory_contents_never_confirm_empty(slots):
    row = summarize(tame(SavedInventories=[{"InventoryID": 19, "Slots": slots}]))["animals"][0]
    assert row["inventory_status"] == "partial"
    assert row["inventories"][0]["items"] == []
    assert row["inventories"][0]["status"] == "partial"
    assert row["inventories"][0]["warnings"]


@pytest.mark.parametrize("item_data", ["", "None", "null", {"RowName": "None"}])
def test_explicit_empty_item_identity_still_confirms_empty(item_data):
    row = summarize(tame(SavedInventories=[{"InventoryID": 19, "Slots": [slot(item_data)]}]))["animals"][0]
    assert row["inventory_status"] == "empty"
    assert row["inventories"][0]["status"] == "empty"


def test_partial_slot_data_only_marks_the_affected_inventory():
    inventories = [{"InventoryID": 17, "Slots": []},
                   {"InventoryID": 19, "Slots": [slot(quantity=3), None]},
                   {"InventoryID": 20, "Slots": []}]
    row = summarize(tame(SavedInventories=inventories))["animals"][0]
    assert row["inventory_status"] == "partial"
    assert [entry["status"] for entry in row["inventories"]] == ["empty", "partial", "empty"]
    assert row["inventories"][1]["items"][0]["quantity"] == 3


def test_duplicate_inventory_with_unreadable_slots_cannot_confirm_empty():
    row = summarize(tame(SavedInventories=[{"InventoryID": 19, "Slots": []},
                                          {"InventoryID": 19, "Slots": None}]))["animals"][0]
    assert row["inventory_status"] == "partial"
    assert len(row["inventories"]) == 1
    assert row["inventories"][0]["status"] == "partial"


@pytest.mark.parametrize("second_slots", [[], None, [slot(quantity=8, location=1)]])
def test_conflicting_inventory_copies_keep_other_inventories_known(second_slots):
    record = tame(SavedInventories=[{"InventoryID": 17, "Slots": [slot("Saddle", 1)]},
                                   {"InventoryID": 19, "Slots": [slot(quantity=3)]},
                                   {"InventoryID": 19, "Slots": second_slots},
                                   {"InventoryID": 20, "Slots": [slot(quantity=5)]}])
    original = deepcopy(record)
    result = summarize(record)
    row = result["animals"][0]
    inventories = {entry["id"]: entry for entry in row["inventories"]}
    assert row["inventory_status"] == "partial"
    assert result["coverage"]["conflicting_inventories"] == 1
    assert inventories[19]["status"] == "partial"
    assert all(item["quantity"] is None for item in inventories[19]["items"])
    assert inventories[17]["status"] == inventories[20]["status"] == "ok"
    assert inventories[17]["items"][0]["quantity"] == 1
    assert inventories[20]["items"][0]["quantity"] == 5
    assert record == original


@pytest.mark.parametrize("slots,expected_status,malformed", [([], "empty", 0), ([None], "partial", 1)])
def test_identical_inventory_copies_are_deduplicated_before_validation(slots, expected_status, malformed):
    inventory = {"InventoryID": 19, "Slots": slots}
    result = summarize(tame(SavedInventories=[inventory, deepcopy(inventory)]))
    row = result["animals"][0]
    assert len(row["inventories"]) == 1
    assert row["inventory_status"] == expected_status
    assert result["coverage"]["conflicting_inventories"] == 0
    assert result["coverage"]["malformed_inventories"] == malformed


@pytest.mark.parametrize("location", [None, -1, True, 2**31])
def test_unlocated_item_keeps_reported_quantity_only_as_unverified_diagnostic(location):
    row = summarize(tame(SavedInventories=[{"InventoryID": 19, "Slots": [slot(location=location)]}]))["animals"][0]
    assert row["inventory_status"] == "partial"
    inventory = row["inventories"][0]
    assert inventory["status"] == "partial"
    assert inventory["items"][0]["quantity"] is None
    assert inventory["items"][0]["unverified_quantity"] == 3
    assert inventory["items"][0]["slot"] is None
    assert inventory["items"][0]["warnings"]


def test_identical_unlocated_items_are_not_presented_as_separate_stacks():
    unlocated = slot(quantity=3)
    unlocated.pop("Location")
    result = summarize(tame(SavedInventories=[{"InventoryID": 19, "Slots": [unlocated, deepcopy(unlocated)]}]))
    inventory = result["animals"][0]["inventories"][0]
    assert inventory["status"] == "partial"
    assert len(inventory["items"]) == 1
    assert inventory["items"][0]["quantity"] is None
    assert inventory["items"][0]["unverified_quantity"] == 3
    assert result["coverage"]["unknown_stacks"] == 1
    assert result["coverage"]["duplicate_slots"] == 1


def test_unverified_recorder_or_installed_build_fails_closed(monkeypatch):
    assert summarize(tame(ActorStateRecorderVersion=99))["animals"] == []
    result = summarize(tame(ActorStateRecorderVersion=99))
    assert result["coverage"]["unsupported_records"] == 1
    monkeypatch.setattr(animals, "installed_game_build", lambda: "future-build")
    result = summarize(tame())
    assert result["animals"] == []
    assert result["coverage"]["status"] == "unsupported_version"


def test_unverified_world_envelope_version_fails_closed():
    for extra in ({"save_format_version": 9}, {"coverage": {"save_format_version": 9}},
                  {"warnings": ["World save format version 9 not verified"]}):
        result = animals.summarize_animals(world(tame(), **extra))
        assert result["animals"] == []
        assert result["coverage"]["status"] == "unsupported_version"


def test_shared_map_cannot_create_animals():
    result = animals.summarize_animals(world(tame(), is_shared_map=True))
    assert result["coverage"]["status"] == "unavailable"
    assert result["animals"] == []


def test_private_ids_never_leave_extractor_and_inputs_are_unchanged():
    snapshot = world(tame())
    before = deepcopy(snapshot)
    result = animals.summarize_animals(snapshot)
    rendered = json.dumps(result, ensure_ascii=False)
    assert "SYNTHETIC_PRIVATE_PLAYER" not in rendered
    assert "OwnerCharacterID" not in rendered
    assert snapshot == before
