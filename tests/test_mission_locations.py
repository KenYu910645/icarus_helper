"""Mission locations must keep stages, UID scope and uncertainty separate."""
import pytest

from icarus_helper import mission_locations as locations


@pytest.fixture(autouse=True)
def validated_build(monkeypatch):
    monkeypatch.setattr(locations, "installed_game_build", lambda: "26313227")


def world(records=(), map_name="Prometheus"):
    return {"map_name": map_name, "raw_records": list(records)}


def mission(mid="PRO_A_Extraction", **extra):
    return {"id": mid, "map_name": "Prometheus", "status_key": "available", **extra}


def actor(uid, xyz):
    return {"IcarusActorGUID": uid, "ActorTransform": {"Translation": xyz}}


def quest(name, uid=1, xyz=(0, 0, 0), refs=(), **extra):
    return {"QuestName": name, "IcarusActorGUID": uid, "ActorTransform": {"Translation": xyz}, "RelevantActorRecords": [{"RelevantActorIcarusUID": target} for target in refs], **extra}


def test_crystal_keeps_all_five_stages_without_fabricating_dynamic_pods():
    route = locations.build_mission_locations(world(), mission())
    assert {p["order"] for p in route["points"]} == {2, 3, 4}
    assert {p["order"] for p in route["pending_stages"]} == {1, 5}
    assert all(p["confidence"] == "candidate" for p in route["points"])
    assert len({p["id"] for p in route["points"]}) == len(route["points"])


def test_shadowed_includes_three_scans_and_later_destinations():
    route = locations.build_mission_locations(world(), mission("PRO_Story_3"))
    assert len(route["points"]) == 5
    assert len({(p["x"], p["y"]) for p in route["points"]}) == 5
    assert [p["order"] for p in route["points"]] == [1, 2, 3, 4, 5]


def test_rescue_keeps_both_return_trips_to_the_same_evacuation_ship():
    route = locations.build_mission_locations(world(), mission("PRO_D_Rescue"))
    by_order = {p["order"]: p for p in route["points"]}
    assert set(by_order) == {1, 2, 3, 4, 5}
    assert (by_order[3]["x"], by_order[3]["y"]) == (by_order[5]["x"], by_order[5]["y"])
    assert by_order[3]["id"] != by_order[5]["id"]
    assert (by_order[2]["x"], by_order[2]["y"]) != (by_order[4]["x"], by_order[4]["y"])


def test_fracture_starts_with_the_traitor_instead_of_the_final_cave():
    route = locations.build_mission_locations(world(), mission("PRO_Story_6"))
    assert route["points"][0]["query"] == "PRO_Story_6_TraitorLocation"
    assert route["points"][-1]["query"] == "PRO_Story_6_LavaCave_Outside"
    assert route["coverage"]["stage_count"] == 13


def test_explicit_world_uid_resolves_a_pod_instead_of_a_query_anchor():
    records = [quest("PRO_A_Extraction_Collect", refs=[100]), actor(100, (1500, 2500, 200))]
    route = locations.build_mission_locations(world(records), mission())
    pod = next(p for p in route["points"] if p["order"] == 1)
    assert (pod["x"], pod["y"], pod["z"]) == (1500, 2500, 200)
    assert pod["confidence"] == "known"
    assert 1 not in {p["order"] for p in route["pending_stages"]}


def test_conflicting_uid_positions_are_not_silently_chosen():
    records = [quest("PRO_A_Extraction_Collect", refs=[100]), actor(100, (1500, 2500, 200)), actor(100, (9000, 9000, 0))]
    route = locations.build_mission_locations(world(records), mission())
    assert 1 in {p["order"] for p in route["pending_stages"]}
    assert not any(p["confidence"] == "known" for p in route["points"])


def test_other_mission_and_uninitialised_quest_transforms_are_not_targets():
    records = [quest("PRO_Story_5_Investigate", xyz=(500, 900, 100), refs=[100]), actor(100, (1000, 1200, 500)), quest("PRO_A_Extraction_Collect", xyz=(500, 900, 100))]
    route = locations.build_mission_locations(world(records), mission())
    assert not any(p["confidence"] == "known" for p in route["points"])
    assert 1 in {p["order"] for p in route["pending_stages"]}


def test_completed_stage_flag_is_preserved_on_a_verified_location():
    record = quest("PRO_A_Extraction_Collect", refs=[100], VariableRecords=[{"VariableName": "Completed", "bVariable": True}])
    route = locations.build_mission_locations(world([record, actor(100, (1000, 2000, 0))]), mission())
    assert next(p for p in route["points"] if p["order"] == 1)["status"] == "completed"


def test_version_change_hides_old_anchors_but_keeps_current_uid_evidence(monkeypatch):
    monkeypatch.setattr(locations, "installed_game_build", lambda: "new-build")
    records = [quest("PRO_A_Extraction_Collect", refs=[100]), actor(100, (1500, 2500, 200))]
    route = locations.build_mission_locations(world(records), mission())
    assert len(route["points"]) == 1
    assert route["points"][0]["confidence"] == "known"
    assert not route["coverage"]["static_compatible"]
    assert {p["order"] for p in route["pending_stages"]} == {2, 3, 4, 5}


def test_dynamic_mission_alias_follows_initial_uid_and_child_area():
    manager = {"_component": "/Script/Icarus.IcarusQuestManagerRecorderComponent", "FactionMissionName": "DYN_Boss", "InitialQuestRecord": {"RelevantActorIcarusUID": 10}}
    parent = quest("DYN_Kill", uid=10, xyz=(1000, 2000, 0), VariableRecords=[{"VariableName": "LocationSet", "bVariable": True}], SubQuestRecords=[{"RelevantActorIcarusUID": 11}])
    child = quest("DYN_Kill_Boss", uid=11, xyz=(1000, 2000, 0))
    route = locations.build_mission_locations(world([manager, parent, child]), mission("DYN_Boss", map_name="All", status_key="active"))
    assert len(route["points"]) == 2 and not route["pending_stages"]
    assert all(p["confidence"] == "known" for p in route["points"])


def test_different_map_does_not_reuse_selected_world_actors():
    route = locations.build_mission_locations(world(), mission(map_name="Olympus"))
    assert not route["points"] and not route["pending_stages"]


def test_origin_and_nonfinite_locations_are_never_drawn():
    record = quest("PRO_A_Extraction_Collect", refs=[100], VariableRecords=[{"VariableName": "LocationSet", "bVariable": True}])
    route = locations.build_mission_locations(world([record, actor(100, (float("nan"), 1000, 0))]), mission())
    assert 1 in {p["order"] for p in route["pending_stages"]}
    assert not any(p["confidence"] == "known" for p in route["points"])
