"""Native XP thresholds drive progress; unavailable capacity never becomes 100%."""
from copy import deepcopy

import pytest

from icarus_helper import animals


SPECIES = {"level_growth_id": "SyntheticCurve"}
CATALOG = {"level_rule": {"type": "curve_thresholds", "verified": True, "curves": {
    "SyntheticCurve": {"thresholds": [0, 10, 30], "max_level": 2, "max_display_level": 2}}}}


@pytest.mark.parametrize("total,expected", [
    (0, {"value": 0, "maximum": 10, "total": 0, "level_start": 0,
         "next_level": 1, "next_level_total": 10, "capped": False}),
    (9, {"value": 9, "maximum": 10, "total": 9, "level_start": 0,
         "next_level": 1, "next_level_total": 10, "capped": False}),
    (10, {"value": 0, "maximum": 20, "total": 10, "level_start": 10,
          "next_level": 2, "next_level_total": 30, "capped": False}),
    (29.5, {"value": 19.5, "maximum": 20, "total": 29.5, "level_start": 10,
            "next_level": 2, "next_level_total": 30, "capped": False}),
    (30, {"value": 0, "maximum": None, "total": 30, "level_start": 30,
          "next_level": None, "next_level_total": None, "capped": True}),
    (100, {"value": 0, "maximum": None, "total": 100, "level_start": 30,
           "next_level": None, "next_level_total": None, "capped": True}),
])
def test_current_level_xp_uses_verified_boundary_and_distinguishes_cap(total, expected):
    assert animals._experience_progress({"Experience": total}, CATALOG, SPECIES) == expected


@pytest.mark.parametrize("total", [None, -1, True, "12", float("nan"), float("inf")])
def test_missing_or_invalid_experience_has_no_fabricated_progress(total):
    assert animals._experience_progress({"Experience": total}, CATALOG, SPECIES) is None


def test_unverified_curve_and_saved_integer_rule_do_not_allow_xp_percentage():
    for change in ({"verified": False}, {"type": "saved_integer"}, {"curves": None}):
        catalog = deepcopy(CATALOG)
        catalog["level_rule"].update(change)
        assert animals._experience_progress({"Experience": 15}, catalog, SPECIES) is None
    assert animals._experience_progress({"Experience": 15}, CATALOG, {}) is None


@pytest.mark.parametrize("thresholds", [[0, 10, 10], [0, 30, 10], [1, 10, 30], [0, True, 30], [0, 10]])
def test_corrupt_thresholds_fail_closed_for_both_level_and_progress(thresholds):
    catalog = deepcopy(CATALOG)
    catalog["level_rule"]["curves"]["SyntheticCurve"]["thresholds"] = thresholds
    assert animals._experience_progress({"Experience": 15}, catalog, SPECIES) is None
    assert animals._level({"Experience": 15}, catalog, SPECIES, None)[0] is None


def test_display_cap_is_not_misrepresented_as_progress_to_a_hidden_level():
    catalog = deepcopy(CATALOG)
    catalog["level_rule"]["curves"]["SyntheticCurve"]["max_display_level"] = 1
    progress = animals._experience_progress({"Experience": 15}, catalog, SPECIES)
    assert progress["capped"] is True
    assert progress["next_level"] is None
    assert progress["maximum"] is None
    assert progress["total"] == 15 and progress["level_start"] == 10


def test_installed_curves_progress_agrees_with_each_verified_displayed_level():
    catalog = animals.load_animal_catalog()
    for growth_id, curve in catalog["level_rule"]["curves"].items():
        species = {"level_growth_id": growth_id}
        for level, threshold in enumerate(curve["thresholds"]):
            record = {"Experience": threshold}
            progress = animals._experience_progress(record, catalog, species)
            assert animals._level(record, catalog, species, None)[0] == min(level, curve["max_display_level"])
            assert progress["level_start"] == threshold
            if progress["capped"]:
                assert progress["next_level"] is None
            else:
                assert progress["value"] == 0
                assert progress["maximum"] == curve["thresholds"][level + 1] - threshold
