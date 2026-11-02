"""Live maxima and current values must move together across the UI boundary."""
from copy import deepcopy

import pytest

from icarus_helper.animals import _apply_live_stats


def sample():
    return {"coverage": {}, "animals": [{"id": "42", "stats": [
        {"id": "health", "value": 10, "maximum": 100, "maximum_status": "persistent"},
        {"id": "stamina", "value": 20, "maximum": 200, "maximum_status": "persistent"},
        {"id": "food", "value": 30, "maximum": 300, "maximum_status": "verified"}]}]}


@pytest.mark.parametrize("status,verified", [("unavailable", False), ("ok", False), ("unavailable", True)])
def test_unverified_live_source_cannot_change_saved_pairs(status, verified):
    summary = sample()
    before = deepcopy(summary["animals"])
    _apply_live_stats(summary, {"status": status, "world_verified": verified,
                               "animals": {"42": {"health": {"value": 80, "maximum": 120}}}})
    assert summary["animals"] == before


def test_verified_pair_replaces_both_values_and_preserves_save_provenance():
    summary = sample()
    live = {"status": "partial", "world_verified": True, "sample_time": "2026-11-02T05:00:00+00:00",
            "animals": {"42": {"health": {"value": 80, "maximum": 120}}}}
    _apply_live_stats(summary, live)
    hp, stamina, food = summary["animals"][0]["stats"]
    assert (hp["value"], hp["maximum"], hp["maximum_status"]) == (80, 120, "live")
    assert (hp["saved_value"], hp["persistent_maximum"]) == (10, 100)
    assert hp["sample_time"] == live["sample_time"]
    assert stamina["value"] == 20 and stamina["maximum_status"] == "persistent"
    assert food["value"] == 30 and food["maximum"] == 300
    assert summary["coverage"]["capacity_status_counts"] == {"live": 1, "persistent": 1, "verified": 1}


@pytest.mark.parametrize("pair", [{"maximum": 120}, {"value": 80}, {"value": 80, "maximum": 0},
                                  {"value": -1, "maximum": 120}, {"value": 80, "maximum": float('nan')}])
def test_incomplete_or_invalid_pair_does_not_mix_sources(pair):
    summary = sample()
    before = deepcopy(summary["animals"])
    _apply_live_stats(summary, {"status": "ok", "world_verified": True, "animals": {"42": {"health": pair}}})
    assert summary["animals"] == before
