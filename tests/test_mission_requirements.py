"""Fixed mission targets must never fabricate saved progress."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

from icarus_helper.missions import build_missions, load_catalog, mission_html
from tools import build_mission_catalog as builder


MID = "PRO_C_Research"
SOLAR = MID + "_Build_Solar"


def research(catalog):
    return next(row for row in catalog["missions"] if row["id"] == MID)


def test_required_facilities_agree_across_preparation_objectives_and_progress_text():
    catalog = load_catalog()
    mission = research(catalog)
    requirements = {row["quest_id"]: row for row in mission["checklist"] if row.get("quest_id")}
    objectives = {row["id"]: row for row in mission["source_objectives"]}
    expected = {"Solar": 4, "Furnace": 2, "Alteration_Bench": 1,
                "Chemisry_Bench": 1, "Cleaning": 1, "Foundry": 1}
    for suffix, amount in expected.items():
        qid = MID + "_Build_" + suffix
        assert requirements[qid]["quantity"] == str(amount)
        assert requirements[qid]["required"] is True
        assert objectives[qid]["required_quantity"] == amount
        assert catalog["quest_texts"][qid] == objectives[qid]["text"]
        assert f"Required: {amount}" in objectives[qid]["text"]
    assert "Solar Panel x 4" in mission["guide"][1]
    assert "Electric Furnace x 2" in mission["guide"][1]
    objectives_html = mission_html(mission).split("<h2>Objectives</h2>", 1)[1]
    for label in ("Solar Panel (0/4)", "Electric Furnace (0/2)",
                  "Advanced Alteration Bench (0/1)", "Miasmic Knife (0/1)",
                  "Miasmic Sickle (0/1)", "Deliver Infected Bark (0/30)"):
        assert label in objectives_html


def test_active_quest_without_counters_does_not_claim_zero_or_complete():
    world = {"world_id": "test", "sha256": "test", "map_name": "Prometheus",
             "mission_state": {"quest_manager": {"FactionMissionName": MID},
                               "quests": [{"QuestName": SOLAR, "VariableRecords": []}]}}
    original = deepcopy(world)
    original_catalog = deepcopy(load_catalog())
    with patch("icarus_helper.missions.installed_game_build", return_value="26313227"):
        mission = next(row for row in build_missions(world) if row["id"] == MID)
    progress = mission["progress"][0]
    assert progress["text"] == "Solar Panel (Required: 4)"
    assert progress["complete"] is False
    assert "unconfirmed" in progress["state"]
    html = mission_html(mission)
    assert "{0}" not in html and "{1}" not in html
    progress_html = html.split("<h2>Mission progress</h2>", 1)[1].split("<h2>", 1)[0]
    assert "0/4" not in progress_html and "4/4" not in progress_html
    assert "Initial objective counts" in html
    assert "Solar Panel (0/4)" in html.split("<h2>Objectives</h2>", 1)[1]
    assert world == original and load_catalog() == original_catalog


def test_verified_items_and_current_delivery_flow_replace_conflicting_table_rows():
    mission = research(load_catalog())
    objectives = {row["id"]: row for row in mission["source_objectives"]}
    assert objectives[MID + "_Craft_Knife"]["text"] == "Miasmic Knife (Required: 1)"
    assert objectives[MID + "_Craft_Sickle"]["text"] == "Miasmic Sickle (Required: 1)"
    assert objectives[MID + "_Refine"]["required_quantity"] == 20
    for suffix in ("Axe", "Knife"):
        assert objectives[MID + "_Test_" + suffix]["required_quantity"] == 30
    assert {row["id"] for row in mission["catalog_only_objectives"]} == {
        MID + "_Test_Sickle", MID + "_Test_Pickaxe"}
    assert "Noxious Crust" not in mission_html(mission)
    assert "Thorned Plants" not in mission_html(mission)


def test_reapplying_evidence_is_idempotent_and_does_not_change_other_missions():
    catalog = deepcopy(load_catalog())
    before = deepcopy(catalog)
    builder.apply_verified_requirements(catalog)
    assert catalog == before


def test_saved_legacy_row_does_not_reintroduce_placeholders_or_required_delivery():
    from icarus_helper.missions import _progress

    progress = _progress({"QuestName": MID + "_Test_Sickle", "VariableRecords": []}, load_catalog())
    assert "{0}" not in progress["text"]
    assert "not a required delivery" in progress["text"]
    assert not progress["complete"]


def test_missing_or_mismatched_evidence_does_not_guess_required_amounts():
    original = json.loads(builder.REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    for change in ("missing", "build", "table", "template"):
        catalog = deepcopy(load_catalog())
        evidence = deepcopy(original)
        if change == "missing":
            evidence["quests"].pop(SOLAR)
        elif change == "build":
            evidence["source_build"] = "unverified-build"
        elif change == "table":
            evidence["quest_table_sha256"] = "different-table"
        else:
            evidence["quests"][SOLAR]["source_text"] = "Different objective ({0} / {1})"
        builder.apply_verified_requirements(catalog, evidence)
        row = next(row for row in research(catalog)["source_objectives"] if row["id"] == SOLAR)
        assert row["required_quantity"] is None
        assert row["text"] == "Solar Panel (Quantity unconfirmed)"
        assert "quantity_source" not in row
        prep = next(row for row in research(catalog)["checklist"] if row.get("quest_id") == SOLAR)
        assert prep["quantity"] == "Unconfirmed"
        assert catalog["quest_texts"][SOLAR] == row["text"]
        if change in {"build", "table"}:
            assert "x 30" not in " ".join(research(catalog)["guide"])


def test_build_pipeline_joins_targets_from_verified_source_table(tmp_path):
    source = tmp_path / "source"
    rows = research(load_catalog())["source_objectives"]
    tables = {
        "Prospects/D_ProspectList.json": {"Rows": [{"Name": MID, "OnProspectAvailability": "Upgrade1",
            "Terrain": {"RowName": "Terrain_019"}}]},
        "Factions/D_FactionMissions.json": {"Rows": [{"Name": MID,
            "InitialQuest": {"RowName": MID}, "MissionObjectives": [{"QuestRow": {"RowName": SOLAR}}]}]},
        "Quests/D_Quests.json": {"Rows": [{"Name": row["id"], "Description": row.get("source_text", row["text"])} for row in rows]},
        "Quests/D_DynamicQuests.json": {"Rows": []}, "Talents/D_Talents.json": {"Rows": []},
    }
    for path, data in tables.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
    evidence = json.loads(builder.REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    evidence["quest_table_sha256"] = hashlib.sha256((source / "Quests/D_Quests.json").read_bytes()).hexdigest()
    evidence_path = tmp_path / "requirements.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with patch.object(builder, "ROOT", tmp_path), patch.object(builder, "REQUIREMENTS_PATH", evidence_path):
        builder.build(source, "26313227")
    result = json.loads((tmp_path / "icarus_helper/data/missions.json").read_text(encoding="utf-8"))
    mission = research(result)
    assert mission["objectives"][0]["required_quantity"] == 4
    assert result["quest_texts"][SOLAR] == "Solar Panel (Required: 4)"
    assert next(row for row in mission["checklist"] if row.get("quest_id") == SOLAR)["quantity"] == "4"
