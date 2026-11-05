"""Regress verified mission targets and flow membership without game/save access."""
from copy import deepcopy
import json
from unittest.mock import patch

from icarus_helper.missions import _progress, build_missions, load_catalog, mission_html
from tools import build_mission_catalog as builder


def requirements():
    return json.loads(builder.REQUIREMENTS_PATH.read_text(encoding="utf-8"))


def missions():
    return {mission["id"]: mission for mission in load_catalog()["missions"]}


def objectives(mission):
    return {row["id"]: row for row in mission["source_objectives"]}


def test_all_native_targets_are_used_only_in_their_verified_mission_flows():
    evidence = requirements()["quests"]
    assert len(evidence) == 83
    assert sum("quantity" in row for row in evidence.values()) == 58
    assert sum("quantity_by_difficulty" in row for row in evidence.values()) == 25
    seen = set()
    for mission in missions().values():
        for row in mission.get("source_objectives", []):
            if not row.get("quantity_source"):
                continue
            source = evidence[row["id"]]
            assert mission["id"] in source["evidence"]["missions"]
            if "quantity" in source:
                assert row["required_quantity"] == source["quantity"]
            else:
                assert row["required_quantity_by_difficulty"] == source["quantity_by_difficulty"]
            assert "{0}" not in row["text"] and "{1}" not in row["text"]
            seen.add(row["id"])
    assert seen == set(evidence)


def test_a_shared_quest_id_does_not_authorize_a_target_in_another_mission():
    catalog = deepcopy(load_catalog())
    qid = "PRO_C_Construction_Build_Beds"
    source = objectives(missions()["PRO_C_Construction"])[qid]
    template = source.get("source_text", source["text"])
    foreign = {
        "id": "OLY_Unverified_Mission", "map_name": "Olympus", "guide": [],
        "objectives": [], "source_objectives": [{"id": qid, "text": template}],
        "checklist": [],
    }
    catalog["missions"] = [foreign]
    builder.apply_verified_requirements(catalog)
    assert not foreign["source_objectives"][0].get("quantity_source")
    assert not foreign["source_objectives"][0].get("required_quantity")
    assert not foreign["checklist"]


def test_saved_progress_for_shared_npc_quest_uses_the_selected_mission_scope():
    qid = "OLY_SQ_Farmer_Setup_Collect_Glass"
    for map_name, mid in (("Olympus", "OLY_SQ_Farmer"), ("Prometheus", "PRO_SQ_Farmer")):
        world = {"world_id": "synthetic-" + map_name, "sha256": "synthetic",
                 "map_name": map_name, "mission_state": {
                     "quest_manager": {"FactionMissionName": mid},
                     "quests": [{"QuestName": qid, "VariableRecords": []}]}}
        before = deepcopy(world)
        with patch("icarus_helper.missions.installed_game_build", return_value="26313227"):
            mission = next(row for row in build_missions(world) if row["id"] == mid)
        progress = next(row for row in mission["progress"] if row["id"] == qid)
        if map_name == "Prometheus":
            assert "Required: 100" in progress["text"]
        else:
            assert "Required: 100" not in progress["text"]
            assert "unconfirmed" in progress["text"].casefold()
        assert not progress["complete"]
        assert world == before


def test_progress_without_an_objective_row_does_not_borrow_a_global_target():
    qid = "OLY_SQ_Farmer_Setup_Collect_Glass"
    quest = {"QuestName": qid, "VariableRecords": []}
    mission = {"id": "OLY_Unverified", "source_objectives": [], "objectives": []}
    progress = _progress(quest, load_catalog(), mission)
    assert "Glass" in progress["text"]
    assert "Required: 100" not in progress["text"]
    assert "unconfirmed" in progress["text"].casefold()
    # Even an incomplete imported catalog with only a filled global label
    # must not turn the target into this mission's verified requirement.
    incomplete = {"quest_texts": {qid: "Glass (Required: 100)"}, "missions": []}
    progress = _progress(quest, incomplete, mission)
    assert "100" not in progress["text"]
    assert "unconfirmed" in progress["text"].casefold()


def test_progress_for_catalog_only_rows_preserves_legacy_status():
    qid = "PRO_Story1_Bed"
    progress = _progress({"QuestName": qid, "VariableRecords": []},
                         load_catalog(), missions()["PRO_Story_1"])
    assert "not required" in progress["text"]
    assert "Required: 1" not in progress["text"]
    assert not progress["complete"]


def test_pylon_item_name_arguments_are_not_mistaken_for_current_and_target_counts():
    data = objectives(missions()["PRO_E_Research"])
    expected = {
        "0": ("Stone", {"Easy": 80, "Normal": 200, "Hard": 280}),
        "1": ("Gold Ore", {"Easy": 40, "Normal": 100, "Hard": 160}),
        "2": ("Sulfur", {"Easy": 120, "Normal": 160, "Hard": 200}),
    }
    for suffix, (item, quantities) in expected.items():
        row = data["PRO_E_Research_Collect_" + suffix]
        assert row["source_text"] == "Provide pylon {0} {1} for analysis."
        assert row["required_quantity_by_difficulty"] == quantities
        assert item in row["text"] and "Required" in row["text"]
        assert not row.get("required_quantity")
        assert "{" not in row["text"]


def test_difficulty_targets_remain_explicit_and_never_become_one_guessed_amount():
    expected = {
        ("PRO_A_Stockpile", "PRO_A_Stockpile_Food1"): {"Easy": 25, "Normal": 30, "Hard": 40},
        ("PRO_B_Extermination", "PRO_B_Extermination_Fish_4"): {"Easy": 5, "Normal": 10, "Hard": 15},
        ("PRO_D_Transport", "PRO_D_Transport_Stockpile_Wood"): {"Easy": 1200, "Normal": 1600, "Hard": 2000},
    }
    for (mid, qid), quantities in expected.items():
        row = objectives(missions()[mid])[qid]
        assert row["required_quantity_by_difficulty"] == quantities
        assert not row.get("required_quantity")
        for difficulty, amount in quantities.items():
            assert f"{difficulty}: {amount}" in row["text"]


def test_fisher_collects_then_deploys_the_same_equipment_without_two_preparation_sets():
    mission = missions()["PRO_SQ_Fisher"]
    data = objectives(mission)
    expected = {"Bench": ("Fishing Bench", 1), "Trap": ("Basic Fishing Trap", 2),
                "Tackle": ("Tackle Box", 1), "Crop": ("Wood Crop Plot", 2),
                "Barrel": ("Water Barrel", 1)}
    evidence = requirements()["quests"]
    for suffix, (item, amount) in expected.items():
        acquire = "OLY_SQ_Fisher_Establish_Equipment_" + suffix
        deploy = "OLY_SQ_Fisher_Establish_Restore_" + suffix
        for qid in (acquire, deploy):
            assert data[qid]["required_quantity"] == amount
            assert item in data[qid]["text"]
            assert "{" not in data[qid]["text"]
        assert evidence[deploy]["reuse_preparation_from"] == acquire
        prep = [row for row in mission["checklist"]
                if row.get("quest_id") in (acquire, deploy)]
        assert len(prep) == 1
        assert prep[0]["quantity"] == str(amount)


def test_disconnected_nomad_base_is_not_a_current_hard_requirement():
    mission = missions()["PRO_Story_1"]
    old = {"PRO_Story1_Base", "PRO_Story1_Bed", "PRO_Story1_Oxite_Dissolver",
           "PRO_Story1_Crafting_Bench", "PRO_Story1_Campfire"}
    assert not old.intersection(objectives(mission))
    assert old.issubset({row["id"] for row in mission["catalog_only_objectives"]})
    for row in mission["checklist"]:
        if row.get("quest_id") in old or row.get("item") == "Bedroll, Oxidizer, Crafting Bench, Campfire":
            assert row["required"] is False
    assert "Establish a small base and place a Bedroll" not in " ".join(mission["guide"])


def test_truck_repair_and_supplies_belong_to_stranded_not_crisis():
    data = missions()
    crisis = objectives(data["PRO_D_Recovery"])
    stranded = objectives(data["PRO_D_Resupply"])
    assert not any(qid.startswith("PRO_D_Recovery_Aid") for qid in crisis)
    for suffix, amount in (("Repair_Radiator", 1), ("Repair_Alternator", 2), ("Resupply_Rope", 300)):
        qid = "PRO_D_Recovery_Aid_" + suffix
        assert stranded[qid]["required_quantity"] == amount
        assert requirements()["quests"][qid]["evidence"]["missions"] == ["PRO_D_Resupply"]


def test_icesheet_uses_the_actual_shared_beacon_quest_and_keeps_its_target():
    mission = missions()["PRO_Icesheet_Story"]
    data = objectives(mission)
    qid = "PRO_Ashlands_CraftTrailBeacons"
    assert "PRO_Icesheet_CraftTrailBeacons" not in data
    assert "PRO_Ashlands_CraftTrailBeaconTool" in data
    assert data[qid]["required_quantity"] == 100
    assert set(requirements()["quests"][qid]["evidence"]["missions"]) == {
        "PRO_Ashlands_Story", "PRO_Icesheet_Story"}
    assert "PRO_Ashlands_Outpost_Clear" not in objectives(missions()["PRO_Ashlands_Story"])
    assert "{0}" not in mission_html(mission)


def test_native_target_reapplication_is_idempotent():
    catalog = deepcopy(load_catalog())
    previous = deepcopy(catalog)
    builder.apply_verified_requirements(catalog)
    assert catalog == previous
