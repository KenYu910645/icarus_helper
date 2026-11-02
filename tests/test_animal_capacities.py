"""Native capacity arithmetic and conservative source-coverage boundaries."""
from copy import deepcopy

import pytest

from icarus_helper.animal_capacities import evaluate_curve, native_capacity, reconstruct_capacities


@pytest.fixture(autouse=True)
def synthetic_equipment_catalog(monkeypatch):
    from icarus_helper import inventory
    monkeypatch.setattr(inventory, "load_item_catalog", lambda: {"items": {
        "SyntheticSaddle": {}, "SyntheticHealthGear": {}}})


def linear(start, end, value_start, value_end):
    return {"keys": [{"Time": start, "Value": value_start, "InterpMode": "RCIM_Linear"},
                     {"Time": end, "Value": value_end, "InterpMode": "RCIM_Linear"}]}


def catalogs():
    static = {"game_build": "26313227", "schema_version": 1,
              "species": {"SyntheticSpecies": {"source_survival_character": True,
                  "base_capacities": {"health": 100, "stamina": 50, "food": 300, "water": 300, "oxygen": 300},
                  "health_curve": "growth-health", "stamina_curve": "growth-stamina"}},
              "curves": {"growth-health": linear(0, 10, 0, 19),
                         "growth-stamina": linear(0, 10, 0, 7)}}
    effects = {"game_build": "26313227", "schema_version": 1,
               "genetics": {"Vitality": {"base": {"BaseMaximumHealth_+": "gene-vitality"}}},
               "lineages": {"SyntheticLineage": {
                   "stats": {"BaseMaximumHealth_+": 2, "BaseMaximumHealth_+%": 10,
                             "BaseMaximumStamina_+%": -25,
                             "MaximumHealth_+": 9999, "MaximumStamina_+": 9999},
                   "growth": {"BaseMaximumHealth_+": "lineage-health", "BaseMaximumStamina_+": "lineage-stamina"}}},
               "creature_talents": {"SyntheticHealthTalent": [{"BaseMaximumHealth_+%": 10},
                                                               {"BaseMaximumHealth_+%": 25}]},
               "curves": {"gene-vitality": linear(0, 10, -3, 14),
                          "lineage-health": linear(0, 10, 0, 9),
                          "lineage-stamina": linear(0, 10, 0, 13)},
               "modifiers": {"KnownNeutral": {"max_stats": {}},
                             "CapacityEffect": {"max_stats": {"BaseMaximumHealth_+%": 25}}},
               "equippable_items": {"SyntheticSaddle": "saddle", "SyntheticHealthGear": "health-gear"},
               "equippables": {"saddle": {"stats": {}, "global_stats": {}},
                               "health-gear": {"stats": {"BaseMaximumHealth_+": 30}, "global_stats": {}}}}
    return static, effects


def record(**changes):
    result = {"AISetupRowName": "SyntheticSpecies", "bHasGeneratedGenetics": True,
              "Genetics": [{"GeneticValueName": "Vitality", "Value": 3}],
              "Lineage": "SyntheticLineage", "Talents": [{"TalentRowName": "SyntheticHealthTalent", "TalentRank": 2}],
              "Modifiers": [{"RowName": "KnownNeutral"}], "StomachContents": [],
              "SavedInventories": [{"InventoryID": 17, "Slots": [{"ItemStaticData": "SyntheticSaddle", "Location": 0}]}]}
    result.update(changes)
    return result


def reconstruct(source=None, level=4, static=None, effects=None):
    base_catalog, effect_catalog = catalogs()
    return reconstruct_capacities(record() if source is None else source, level,
                                  base_catalog if static is None else static,
                                  effect_catalog if effects is None else effects)


@pytest.mark.parametrize("base,percent,minimum,expected", [
    (0, 0, 1, 1), (0, 0, 0, 0), (1, -25, 1, 1), (1, -25, 0, 0),
    (99, 1, 0, 99), (101, 1, 0, 102), (1374, 30, 1, 1786),
    (1200, -25, 1, 900), (300, 0, 0, 300), (100, -101, 0, -1),
    (100, -101, 1, 1), (100, -100, 0, 0), (100, 100, 0, 200),
])
def test_integer_formula_matches_verified_native_lambda_boundaries(base, percent, minimum, expected):
    # These boundaries are independently emulated from the installed native
    # function bytes in artifacts/animal-capacities-20261102, including min1.
    assert native_capacity(base, percent, minimum) == expected


@pytest.mark.parametrize("base,percent", [(True, 0), (100.0, 0), (100, None), (100, 0.5),
                                          (2**31, 0), (2**30, 100), (1, 2**31 - 1)])
def test_noninteger_or_overflowing_capacity_fails_closed(base, percent):
    # Runtime rejects impossible/corrupt int32 inputs instead of displaying a
    # wrapped native integer as an apparently meaningful capacity.
    with pytest.raises((ValueError, TypeError, OverflowError)):
        native_capacity(base, percent)


def test_curve_rounds_each_intermediate_to_native_single_precision():
    # A double-only midpoint is 16777217, whereas float32 rounds ties to even.
    assert evaluate_curve(linear(0, 1, 16777216, 16777218), .5) == 16777216
    assert evaluate_curve(linear(0, 1, 16777217, 16777217), 1) == 16777216
    assert evaluate_curve(linear(0, 10, -3, 14), 3) == pytest.approx(2.1000003814697266)


def test_curve_constant_and_cubic_modes_preserve_known_endpoints():
    constant = linear(0, 10, 3, 9)
    constant["keys"][0]["InterpMode"] = "RCIM_Constant"
    assert evaluate_curve(constant, 9) == 3
    assert evaluate_curve(constant, 10) == 9
    cubic = linear(0, 10, 0, 10)
    for key in cubic["keys"]:
        key.update(InterpMode="RCIM_Cubic", TangentWeightMode="RCTWM_WeightedNone",
                   LeaveTangent=1, ArriveTangent=1)
    assert evaluate_curve(cubic, 0) == 0 and evaluate_curve(cubic, 10) == 10
    assert evaluate_curve(cubic, 5) == pytest.approx(5, abs=1e-6)
    cubic["keys"][0]["TangentWeightMode"] = "RCTWM_WeightedArrive"
    with pytest.raises(ValueError):
        evaluate_curve(cubic, 5)


def test_growth_gene_and_lineage_truncate_separately_before_single_rank_percent():
    source = record()
    before = deepcopy(source)
    result = reconstruct(source)
    # Health: base100 + trunc(growth7.6) + trunc(gene2.1) + lineage2
    # + trunc(lineageGrowth3.6) = 114. Percent=10+25; rank1's 10 is not added.
    assert result["health"]["maximum"] == 153
    assert result["health"]["maximum_components"] == {"base": 114, "percent": 35}
    # Stamina: (50 + trunc(2.8) + trunc(5.2)) * .75 => 42, not rounded43.
    assert result["stamina"]["maximum"] == 42
    assert result["stamina"]["maximum_components"] == {"base": 57, "percent": -25}
    assert result["health"]["maximum_status"] == result["stamina"]["maximum_status"] == "reconstructed"
    assert "未含" in result["health"]["detail"] and "完整目前上限" in result["health"]["detail"]
    assert all(result[key]["maximum"] == 300 and result[key]["maximum_status"] == "verified"
               for key in ("food", "water", "oxygen"))
    assert source == before


@pytest.mark.parametrize("owner_percent,expected_health,expected_stamina", [(30, 188, 59), (0, 153, 42), (-20, 153, 42)])
def test_owner_percent_added_before_native_final_truncation(owner_percent, expected_health, expected_stamina):
    static, effects = catalogs()
    owner = {"persistent_complete": {"health": True, "stamina": True},
             "percent": {"health": owner_percent, "stamina": owner_percent}, "live_complete": False}
    result = reconstruct_capacities(record(), 4, static, effects, owner_info=owner)
    assert result["health"]["maximum"] == expected_health
    assert result["stamina"]["maximum"] == expected_stamina
    assert result["health"]["maximum_status"] == "persistent"
    assert result["health"]["maximum_components"]["owner_percent"] == owner_percent
    assert "未保存" in result["health"]["detail"]


def test_missing_owner_is_not_zero_or_a_complete_persistent_maximum():
    static, effects = catalogs()
    owner = {"persistent_complete": {"health": False, "stamina": False},
             "known_percent": {"health": 30, "stamina": 10}, "percent": {"health": None, "stamina": None}}
    result = reconstruct_capacities(record(), 4, static, effects, owner_info=owner)
    assert result["health"]["maximum"] == 153
    assert result["health"]["maximum_status"] == "reconstructed"
    assert "不以零" in result["health"]["detail"]


def test_level_zero_and_zero_rank_are_valid_without_plus_one():
    result = reconstruct(record(Genetics=[{"GeneticValueName": "Vitality", "Value": 2}], Talents=[]), level=0)
    assert result["health"]["maximum_components"] == {"base": 102, "percent": 10}
    assert result["health"]["maximum"] == 112
    assert result["stamina"]["maximum"] == 37
    zero_rank = record(Talents=[{"TalentRowName": "SyntheticHealthTalent", "TalentRank": 0}])
    assert reconstruct(zero_rank)["health"]["maximum_components"]["percent"] == 10


@pytest.mark.parametrize("field,unknown", [("game_build", "future-build"), ("schema_version", 99)])
@pytest.mark.parametrize("catalog_index", [0, 1])
def test_wrong_catalog_version_does_not_publish_any_capacities(field, unknown, catalog_index):
    static, effects = catalogs()
    (static, effects)[catalog_index][field] = unknown
    result = reconstruct(static=static, effects=effects)
    assert all(value["maximum"] is None and value["maximum_status"] == "unknown" for value in result.values())


@pytest.mark.parametrize("changes,supply_verified", [
    ({"Genetics": None}, True), ({"bHasGeneratedGenetics": False}, True),
    ({"Genetics": [{"GeneticValueName": "FutureGene", "Value": 1}]}, False),
    ({"Genetics": [{"GeneticValueName": "Vitality", "Value": 1}, {"GeneticValueName": "Vitality", "Value": 1}]}, True),
    ({"Lineage": "FutureLineage"}, False), ({"Talents": None}, True),
    ({"Talents": [{"TalentRowName": "FutureTalent", "TalentRank": 1}]}, False),
    ({"Talents": [{"TalentRowName": "SyntheticHealthTalent", "TalentRank": 3}]}, True),
    ({"Talents": [{"TalentRowName": "SyntheticHealthTalent", "TalentRank": True}]}, True),
    ({"Modifiers": None}, False), ({"Modifiers": [{"RowName": "FutureModifier"}]}, False),
    ({"Modifiers": [{"RowName": "CapacityEffect"}]}, True),
    ({"StomachContents": None}, True), ({"StomachContents": [{"food": "not yet reconstructed"}]}, True),
    ({"SavedInventories": None}, False), ({"SavedInventories": [{"InventoryID": 17, "Slots": None}]}, False),
])
def test_unknown_source_ids_and_incomplete_individual_values_have_distinct_coverage(changes, supply_verified):
    result = reconstruct(record(**changes))
    assert result["health"]["maximum"] is result["stamina"]["maximum"] is None
    assert result["health"]["maximum_status"] == result["stamina"]["maximum_status"] == "unknown"
    if supply_verified:
        assert all(result[key]["maximum"] == 300 and result[key]["maximum_status"] == "verified"
                   for key in ("food", "water", "oxygen"))
    else:
        # Unknown effect or equipment identities can affect supplies, unlike
        # missing ranks/values of known current-version health-only effects.
        assert all(result[key]["maximum"] is None and result[key]["maximum_status"] == "unknown"
                   for key in ("food", "water", "oxygen"))


def test_fractional_genetic_integer_cannot_generate_a_fabricated_native_curve_input():
    result = reconstruct(record(Genetics=[{"GeneticValueName": "Vitality", "Value": 1.5}]))
    assert result["health"]["maximum"] is result["stamina"]["maximum"] is None


def test_unknown_equipped_item_cannot_be_assumed_to_have_no_capacity_effect():
    source = record(SavedInventories=[{"InventoryID": 17, "Slots": [{"ItemStaticData": "FutureSaddle", "Location": 0}]}])
    result = reconstruct(source)
    assert all(row["maximum"] is None and row["maximum_status"] == "unknown" for row in result.values())


@pytest.mark.parametrize("extra", ["AdditionalStats", "Alterations", "LivingItemSlots"])
def test_unexpanded_equipped_item_additions_prevent_a_false_verified_supply_maximum(extra):
    source = record()
    source["SavedInventories"][0]["Slots"][0][extra] = [{"unverified": "capacity effect"}]
    result = reconstruct(source)
    assert all(row["maximum"] is None and row["maximum_status"] == "unknown" for row in result.values())


def test_capacity_affecting_equipment_in_cargo_is_not_treated_as_equipped():
    source = record(SavedInventories=[{"InventoryID": 19, "Slots": [{"ItemStaticData": "SyntheticHealthGear", "Location": 0}]}])
    result = reconstruct(source)
    assert result["health"]["maximum"] == 153 and result["stamina"]["maximum"] == 42
    source["SavedInventories"][0]["InventoryID"] = 17
    result = reconstruct(source)
    assert result["health"]["maximum"] is result["stamina"]["maximum"] is None


@pytest.mark.parametrize("inventory_id", [None, "17", 999])
def test_unrecognised_inventory_type_cannot_determine_whether_gear_is_equipped(inventory_id):
    source = record(SavedInventories=[{"InventoryID": inventory_id, "Slots": [{"ItemStaticData": "SyntheticHealthGear", "Location": 0}]}])
    result = reconstruct(source)
    assert result["health"]["maximum"] is result["stamina"]["maximum"] is None


def test_missing_species_base_cannot_be_filled_in_by_gene_or_talent_contribution():
    static, effects = catalogs()
    species = static["species"]["SyntheticSpecies"]
    species["base_capacities"].pop("health")
    species.pop("health_curve")
    result = reconstruct(static=static, effects=effects)
    assert result["health"]["maximum"] is None
    assert result["stamina"]["maximum"] == 42


def test_missing_supply_capacity_stays_unknown_and_is_never_replaced_by_current_value():
    static, effects = catalogs()
    static["species"]["SyntheticSpecies"]["base_capacities"].pop("oxygen")
    result = reconstruct(record(OxygenLevel=888), static=static, effects=effects)
    assert result["oxygen"]["maximum"] is None and result["oxygen"]["maximum_status"] == "unknown"
