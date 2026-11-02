"""Installed-source capacity contracts: exact keys, native ranks and honest gaps."""
import json
from pathlib import Path
import re

import pytest


DATA = Path(__file__).resolve().parents[1] / "icarus_helper/data"


@pytest.fixture(scope="module")
def catalog():
    return json.loads((DATA / "animal_capacity_effects.json").read_text(encoding="utf8"))


def test_schema_and_source_hashes_match_independently_built_animal_catalog(catalog):
    assert catalog["schema_version"] == 1
    assert catalog["game_build"] == "26313227"
    baseline = json.loads((DATA / "animal_catalog.json").read_text(encoding="utf8"))
    assert catalog["executable_sha256"] == baseline["executable_sha256"]
    sources = {source["path"]: source["sha256"] for source in catalog["sources"]}
    assert len(sources) == len(catalog["sources"]) == 8
    assert all(re.fullmatch("[0-9a-f]{64}", value) for value in sources.values())
    baseline_sources = {source["path"]: source["sha256"] for source in baseline["sources"]}
    common = set(sources).intersection(baseline_sources)
    assert len(common) >= 4
    assert all(sources[name] == baseline_sources[name] for name in common)


def test_genetic_and_lineage_curves_keep_native_keys_and_conversion(catalog):
    for gene, stat in (("Vitality", "BaseMaximumHealth_+"), ("Endurance", "BaseMaximumStamina_+")):
        curve_id = catalog["genetics"][gene]["base"][stat]
        curve = catalog["curves"][curve_id]
        assert [(key["Time"], key["Value"]) for key in curve["keys"]] == [(0, -80), (3, 0), (4, 50), (10, 200)]
        assert all(key["InterpMode"] == "RCIM_Linear" for key in curve["keys"])
        assert re.fullmatch("[0-9a-f]{64}", curve["sha256"])
    double = catalog["lineages"]["Bold"]["growth"]["BaseMaximumStamina_+"]
    assert [(key["Time"], key["Value"]) for key in catalog["curves"][double]["keys"]] == [(0, 0), (50, 100)]
    assert catalog["lineages"]["Brave"]["stats"]["BaseMaximumStamina_+%"] == -10
    assert catalog["lineages"]["Resolute"]["stats"]["BaseMaximumHealth_+%"] == -10
    assert catalog["verified_rules"]["genetics"]["integer_conversion"] == "truncate_toward_zero_per_curve"
    assert catalog["verified_rules"]["lineages"]["level_offset_adjustment"] == 0


def test_talent_ranks_preserve_whole_reward_and_select_only_current_rank(catalog):
    health = catalog["creature_talents"]["Creature_Base_Health_Moa"]
    assert [rank["BaseMaximumHealth_+%"] for rank in health] == [2, 5, 10, 20]
    horse = catalog["creature_talents"]["Creature_Horse_Stamina"]
    assert horse[2] == {"BaseMaximumStamina_+%": 10, "BaseHeatResistance_%": 5}
    assert catalog["verified_rules"]["talents"]["rank_index_offset"] == -1
    assert catalog["verified_rules"]["talents"]["cumulative"] is False


def test_npc_modifiers_and_animal_inputs_do_not_change_three_supply_capacities(catalog):
    supply = {f"{prefix}Maximum{stat}_{operation}" for stat in ("Food", "Water", "Oxygen")
              for prefix, operation in (("", "+"), ("Base", "+"), ("Base", "+%"))}
    assert all(not supply.intersection(modifier["max_stats"]) for modifier in catalog["modifiers"].values() if modifier["npc"])
    oxygen = catalog["modifiers"]["Oxygen_Buff"]
    assert oxygen["max_stats"] == {"BaseMaximumOxygen_+%": 10} and oxygen["npc"] is False
    assert all(not supply.intersection(rank) for rewards in catalog["creature_talents"].values() for rank in rewards)
    assert all(not supply.intersection(row["base"]) for row in catalog["genetics"].values())
    assert all(not supply.intersection(row["stats"]) and not supply.intersection(row["growth"]) for row in catalog["lineages"].values())
    saddles = [row for row in catalog["equippables"].values() if row["behavior"].endswith("BP_EquippableModifier_Saddle_C")]
    assert len(saddles) >= 20 and all(not supply.intersection(row["stats"]) for row in saddles)
    assert all(row["granted"] not in supply for row in catalog["owner_conversion"])


def test_saved_modifiers_are_not_a_complete_health_stamina_capacity(catalog):
    feed = catalog["modifiers"]["Animal_Feed"]
    assert feed["npc"] and not feed["save_to_database"]
    assert feed["max_stats"] == {"BaseMaximumStamina_+": 125}
    assert feed["effectiveness_affectors"] == ["FruitAndVegeModifierEffectiveness_+%"]
    assert catalog["modifiers"]["Animal_Feed_Bean"]["max_stats"] == {"BaseMaximumHealth_+": 175}
    assert catalog["modifiers"]["Growth_Enzyme"]["max_stack"] == 5
    assert "incomplete" in catalog["contract"]["modifier_completeness"]


def test_known_empty_sources_remain_distinct_from_unknown_rows(catalog):
    assert "Tame_Hunger_3" in catalog["known_ineffective"]["modifiers"]
    assert catalog["modifiers"]["Tame_Hunger_3"]["max_stats"] == {}
    assert catalog["modifiers"].get("FUTURE_UNKNOWN_MODIFIER") is None
    assert "FUTURE_UNKNOWN_MODIFIER" not in catalog["known_ineffective"]["modifiers"]
    assert catalog["lineages"]["Wild"] == {"stats": {}, "growth": {}}
    assert catalog["lineages"].get("FUTURE_UNKNOWN_LINEAGE") is None
    assert catalog["equippable_items"]["Saddle_Basic_Desert"] == "Saddle_Basic_Desert"
    assert not set(catalog["equippables"]["Saddle_Basic_Desert"]["stats"]).intersection(catalog["max_stat_keys"])
    assert catalog["equippable_items"].get("FUTURE_UNKNOWN_SADDLE") is None
    assert "unknown identifiers" in catalog["contract"]["known_ineffective_meaning"]


def test_owner_transfers_are_explicit_and_include_the_module_gap(catalog):
    health = next(row for row in catalog["owner_conversion"] if row["row"] == "Health")
    assert health == {"row": "Health", "requirement": "TamedCreatureMaximumHealth_+%", "granted": "BaseMaximumHealth_+%", "effects": "TamedCreature"}
    module = catalog["equippable_items"]["Meta_Module_Taming"]
    assert catalog["equippables"][module]["stats"]["BaseTamedCreatureMaximumStamina_+%"] == 50
    assert "full current stat container" in catalog["contract"]["owner_scope"]
