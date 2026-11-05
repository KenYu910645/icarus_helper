"""Catalog-wide user surfaces and malformed numeric progress regressions."""
from copy import deepcopy
from html import unescape
import re

import pytest

from icarus_helper.mission_text import PLACEHOLDER, display_mission_text
from icarus_helper.missions import _progress, load_catalog, mission_html


def test_all_catalog_guides_and_saved_quest_labels_have_no_unfilled_arguments():
    catalog = load_catalog()
    original = deepcopy(catalog)
    for mission in catalog["missions"]:
        assert not PLACEHOLDER.search(mission_html(mission)), mission["id"]
    for qid in catalog["quest_texts"]:
        progress = _progress({"QuestName": qid, "VariableRecords": []}, catalog)
        assert not PLACEHOLDER.search(progress["text"]), qid
        assert not progress["complete"]
    assert catalog == original


@pytest.mark.parametrize("text", ["{2} ({0} / {1})", "{item} ({0} / {1})", "Transmission {0}% Complete",
                                  "{0} Seconds", "Health {0}/{1}", "Provide pylon {0} {1} for analysis"])
def test_unverified_arguments_are_explicitly_unknown_not_invented_requirements(text):
    rendered = display_mission_text(text)
    assert "Unconfirmed values" in rendered
    assert not PLACEHOLDER.search(rendered)
    assert "Required:" not in rendered
    assert not re.search(r"\d", rendered)
    assert display_mission_text(rendered) == rendered


def test_unknown_source_text_is_still_html_escaped():
    html = mission_html({"title": "A < B {0}", "checklist": [], "guide": [],
                         "source_objectives": [{"text": "<img src=x> {item}"}]})
    assert "<img src=x>" not in html
    assert "&lt;img src=x&gt;" in html
    assert "Unconfirmed values" in unescape(html)


@pytest.mark.parametrize("slots", [{}, {"fVariable": None}, {"fVariable": float("nan")},
                                   {"iVariable": float("inf")}, {"fVariable": True},
                                   {"fVariable": "150"}, {"fVariable": 150, "iVariable": 200},
                                   {"fVariable": -1}])
def test_missing_invalid_or_conflicting_transmission_timer_does_not_become_zero(slots):
    progress = _progress({"QuestName": "PRO_Story_5_Outpost_Progress", "VariableRecords": [
        {"VariableName": "CurrentTime", **slots}]}, load_catalog())
    assert not any("/600" in detail for detail in progress["details"])
    assert "countdown is unavailable" in progress["details"][-1]
    assert progress["state"] == "Record exists; completion unconfirmed"


@pytest.mark.parametrize("slots,expected", [({"fVariable": 0}, 0), ({"iVariable": 0}, 0),
    ({"fVariable": 0, "iVariable": 0}, 0), ({"fVariable": 150, "iVariable": 0}, 150),
    ({"fVariable": 0, "iVariable": 150}, 150)])
def test_verified_zero_and_numeric_slot_defaults_remain_readable(slots, expected):
    progress = _progress({"QuestName": "PRO_Story_5_Outpost_Progress", "VariableRecords": [
        {"VariableName": "CurrentTime", **slots}]}, load_catalog())
    assert progress["details"][0].startswith(f"Transmission {expected}/600 s")
    count = _progress({"QuestName": "synthetic", "VariableRecords": [
        {"VariableName": "Count", **slots}]}, load_catalog())
    assert count["details"][0].startswith(f"Count: {expected} ")


def test_duplicate_conflicting_numeric_records_are_unknown():
    progress = _progress({"QuestName": "PRO_Story_5_Outpost_Progress", "VariableRecords": [
        {"VariableName": "CurrentTime", "fVariable": 150},
        {"VariableName": "CurrentTime", "fVariable": 200}]}, load_catalog())
    assert "countdown is unavailable" in progress["details"][-1]
