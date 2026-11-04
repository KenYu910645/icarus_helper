"""Geographic grouping preserves task eligibility, full scope and uncertainty."""
from copy import deepcopy
import json

import pytest

from icarus_helper import mission_clusters as clusters


def mission(mid, status="available", **extra):
    return {"id": mid, "title": mid, "map_name": "Prometheus", "status_key": status, **extra}


def point(mid, x, *, stage="travel", confidence="known", **extra):
    return {"id": f"{mid}-{stage}-{x}", "stage": stage, "order": 1, "label": stage,
            "x": x * 100, "y": 100, "z": 0, "confidence": confidence, "status": "future", **extra}


def world(**extra):
    return {"world_id": "world-A", "map_name": "Prometheus", **extra}


def install_routes(monkeypatch, routes):
    calls = []

    def route(snapshot, mission):
        calls.append(mission["id"])
        points = routes.get(mission["id"], [])
        return points if isinstance(points, dict) else {"points": points, "pending_stages": []}

    monkeypatch.setattr(clusters, "build_mission_locations", route)
    return calls


def test_nearby_eligible_missions_have_original_points_and_bounded_span(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", 10)], "B": [point("B", 1010)]})
    result = clusters.build_mission_clusters(world(), [mission("A"), mission("B", "active")], base={"x": 0, "y": 100})
    group, = result["groups"]
    assert group["mission_ids"] == ["A", "B"]
    assert group["span_m"] == 1000
    assert group["base_distance_m"] == 510
    assert {p["x"] for p in group["points"]} == {1000, 101000}
    assert not group["complete_trip_confirmed"]
    assert result["unclustered"] == []
    assert result["eligible_mission_count"] == result["grouped_mission_count"] == 2


def test_complete_link_does_not_turn_a_near_neighbor_chain_into_a_large_group(monkeypatch):
    install_routes(monkeypatch, {mid: [point(mid, x)] for mid, x in (("A", 0), ("B", 1000), ("C", 2000))})
    result = clusters.build_mission_clusters(world(), [mission(mid) for mid in ("A", "B", "C")])
    group, = result["groups"]
    assert len(group["mission_ids"]) == 2
    assert group["span_m"] <= 1200
    assert result["grouped_mission_count"] == 2
    assert len(result["unclustered"]) == 1


def test_known_and_candidate_points_never_imply_one_confirmed_group(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", 0)], "B": [point("B", 10, confidence="candidate")]})
    result = clusters.build_mission_clusters(world(), [mission("A"), mission("B")])
    assert not result["groups"]


def test_candidate_alternatives_do_not_count_as_required_other_stages(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", 0, confidence="candidate"), point("A", 9000, confidence="candidate")],
                                 "B": [point("B", 20, confidence="candidate")]})
    group, = clusters.build_mission_clusters(world(), [mission("A"), mission("B")])["groups"]
    member = next(m for m in group["missions"] if m["id"] == "A")
    assert group["confidence"] == "candidate"
    assert member["other_stage_count"] == 0
    assert member["alternative_point_count"] == 1
    assert any("alternatives, not required stops" in text for text in group["limitations"])


def test_distant_later_stage_and_unlocated_delivery_are_preserved(monkeypatch):
    install_routes(monkeypatch, {"A": {"points": [point("A", 0), point("A", 9000, stage="later")],
                                       "pending_stages": [{"stage": "deliver", "label": "交付", "order": 3}]},
                                 "B": [point("B", 20)]})
    group, = clusters.build_mission_clusters(world(), [mission("A"), mission("B")])["groups"]
    member = next(m for m in group["missions"] if m["id"] == "A")
    assert member["other_stage_count"] == 1
    assert member["other_stages"] == [{"id": "later", "label": "later"}]
    assert member["pending_stage_count"] == 1
    assert any("travel between regions" in text for text in group["limitations"])
    assert any("unlocated" in text for text in group["limitations"])
    assert not group["complete_trip_confirmed"]


def test_a_mission_can_have_nearby_stages_in_two_separate_groups(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", 0), point("A", 9000, stage="later")],
                                 "B": [point("B", 20)], "C": [point("C", 9020)]})
    result = clusters.build_mission_clusters(world(), [mission(mid) for mid in ("A", "B", "C")])
    assert len(result["groups"]) == 2
    assert all("A" in group["mission_ids"] for group in result["groups"])
    assert result["grouped_mission_count"] == 3
    assert all(next(m for m in group["missions"] if m["id"] == "A")["other_stage_count"] == 1 for group in result["groups"])


def test_several_confirmed_actors_in_one_stage_are_not_candidate_alternatives(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", 0), point("A", 9000)], "B": [point("B", 20)]})
    group, = clusters.build_mission_clusters(world(), [mission("A"), mission("B")])["groups"]
    member = next(m for m in group["missions"] if m["id"] == "A")
    assert member["alternative_point_count"] == 0
    assert member["other_stage_count"] == 1
    assert any("travel between regions" in text for text in group["limitations"])


def test_unknown_locked_completed_and_other_world_map_are_not_eligible(monkeypatch):
    routes = {mid: [point(mid, 0)] for mid in ("A", "B", "C", "D", "E", "F", "G")}
    calls = install_routes(monkeypatch, routes)
    missions = [mission("A"), mission("B", "unknown"), mission("C", "locked"), mission("D", "completed"),
                mission("E", map_name="Olympus"), mission("F", world_id="world-B"), mission("G", map_name="All")]
    result = clusters.build_mission_clusters(world(), missions)
    assert calls == ["A", "G"]
    assert result["eligible_mission_count"] == 2
    assert result["groups"][0]["mission_ids"] == ["A", "G"]


@pytest.mark.parametrize("extra", [{"world_id": "world-B"}, {"map_name": "Olympus"}])
def test_another_worlds_base_never_provides_a_distance(monkeypatch, extra):
    install_routes(monkeypatch, {"A": [point("A", 10)], "B": [point("B", 20)]})
    result = clusters.build_mission_clusters(world(), [mission("A"), mission("B")], {"x": 0, "y": 0, **extra})
    assert result["groups"][0]["base_distance_m"] is None
    assert result["base_status"] in {"world_mismatch", "map_mismatch"}


def test_shared_map_has_no_private_eligibility_or_groups(monkeypatch):
    calls = install_routes(monkeypatch, {"A": [point("A", 0)], "B": [point("B", 10)]})
    result = clusters.build_mission_clusters(world(is_shared_map=True), [mission("A"), mission("B")])
    assert result["groups"] == result["unclustered"] == []
    assert not calls
    assert "no host save" in result["summary"]


def test_nonfinite_or_missing_coordinates_and_completed_stages_are_not_points(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", float("nan")), {"stage": "missing", "confidence": "known"},
                                       point("A", 10, stage="done", status="completed")], "B": [point("B", 10)]})
    result = clusters.build_mission_clusters(world(), [mission("A"), mission("B")])
    assert not result["groups"]
    assert result["unclustered"][0]["id"] == "A"
    assert "No unfinished stage" in result["unclustered"][0]["reason"]
    json.dumps(result, allow_nan=False)


def test_one_mission_and_repeated_records_do_not_count_as_multiple_missions(monkeypatch):
    p = point("A", 10)
    install_routes(monkeypatch, {"A": [p, deepcopy(p), point("A", 20, stage="later")]})
    result = clusters.build_mission_clusters(world(), [mission("A"), mission("A")])
    assert result["eligible_mission_count"] == 1
    assert not result["groups"]


def test_group_ids_and_members_are_stable_across_input_order(monkeypatch):
    routes = {"A": [point("A", 10), point("A", 20, stage="later")], "B": [point("B", 30)], "C": [point("C", 40)]}
    install_routes(monkeypatch, routes)
    missions = [mission(mid) for mid in routes]
    first = clusters.build_mission_clusters(world(), missions)
    for route in routes.values():
        route.reverse()
    second = clusters.build_mission_clusters(world(), list(reversed(missions)))
    assert first == second
    third = clusters.build_mission_clusters(world(world_id="other-world"), missions)
    assert first["groups"][0]["id"] != third["groups"][0]["id"]


def test_model_does_not_mutate_snapshot_routes_missions_or_base(monkeypatch):
    routes = {"A": [point("A", 10)], "B": [point("B", 20)]}
    install_routes(monkeypatch, routes)
    snapshot, missions, base = world(), [mission("A"), mission("B")], {"x": 0, "y": 0}
    before = deepcopy((snapshot, routes, missions, base))
    clusters.build_mission_clusters(snapshot, missions, base)
    assert before == (snapshot, routes, missions, base)


@pytest.mark.parametrize("span", [0, -1, float("nan"), float("inf"), True, None])
def test_invalid_span_never_creates_clusters(monkeypatch, span):
    calls = install_routes(monkeypatch, {})
    result = clusters.build_mission_clusters(world(), [mission("A")], max_span_m=span)
    assert not result["groups"] and not calls
    assert "greater than zero" in result["summary"]
    json.dumps(result, allow_nan=False)


def test_missing_map_and_no_eligible_missions_explain_why(monkeypatch):
    calls = install_routes(monkeypatch, {})
    assert "map" in clusters.build_mission_clusters(world(map_name=""), [mission("A")])["summary"]
    assert "No confirmed" in clusters.build_mission_clusters(world(), [mission("A", "locked")])["summary"]
    assert not calls


def test_recommendation_scores_rank_groups_without_changing_membership(monkeypatch):
    install_routes(monkeypatch, {"A": [point("A", 0)], "B": [point("B", 10)],
                                 "C": [point("C", 4000)], "D": [point("D", 4010)]})
    missions = [mission(mid) for mid in ("A", "B", "C", "D")]
    for current in missions:
        current["recommendation_score"] = 900 if current["id"] in ("C", "D") else 500
    first = clusters.build_mission_clusters(world(), missions, max_span_m=100)
    assert first["groups"][0]["mission_ids"] == ["C", "D"]
    for current in missions:
        current["recommendation_score"] = 1000 - current["recommendation_score"]
    second = clusters.build_mission_clusters(world(), missions, max_span_m=100)
    assert second["groups"][0]["mission_ids"] == ["A", "B"]
    assert {g["id"] for g in first["groups"]} == {g["id"] for g in second["groups"]}
