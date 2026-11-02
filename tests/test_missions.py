"""Mission eligibility/progress tests use invented isolated state, never game saves."""
import copy
import unittest
from unittest.mock import patch

from icarus_helper.missions import _distance, build_missions, load_catalog, mission_html


def terminal(tier=4):
    modules = [{"ItemStaticData": "Mission_Communication_Device_T3"}]
    if tier >= 4:
        modules.append({"ItemStaticData": "Mission_Communication_Device_T4"})
    return {"StaticItemDataRowName": "Mission_Device", "SavedInventories": [{"Slots": modules}]}


def world():
    return {"map_name": "Prometheus", "prospect_info": {"Difficulty": "Medium"},
            "raw_records": [terminal()], "mission_state": {
                "mission_history": [{"Mission": f"PRO_Story_{i}", "Status": 1} for i in range(1, 5)],
                "quest_manager": {"FactionMissionName": "PRO_Story_5", "bMissionComplete": 0},
                "quests": []}}


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.build_patch = patch("icarus_helper.missions.installed_game_build", return_value="26313227")
        self.build_patch.start()
        self.addCleanup(self.build_patch.stop)

    def test_current_map_has_every_operation_and_type_with_guides(self):
        missions = build_missions(world())
        pro = [m for m in missions if m["map_name"] == "Prometheus"]
        self.assertEqual(24, len(pro))
        self.assertEqual(11, len([m for m in missions if m["kind"] == "SMPL3"]))
        for mission in pro:
            self.assertGreaterEqual(len(mission["guide"]), 3, mission["id"])
            self.assertGreaterEqual(len(mission["checklist"]), 4, mission["id"])
            self.assertIn("任務專屬", mission["guide_coverage"])
        self.assertTrue(all(m["map_name"] in {"Prometheus", "All"} for m in missions))

    def test_active_completed_locked_and_first_recommendation(self):
        missions = build_missions(world())
        by_id = {m["id"]: m for m in missions}
        self.assertEqual("active", by_id["PRO_Story_5"]["status_key"])
        self.assertEqual("completed", by_id["PRO_Story_4"]["status_key"])
        self.assertEqual("locked", by_id["PRO_Story_6"]["status_key"])
        self.assertEqual("PRO_Story_5", missions[0]["id"])

    def test_installed_module_is_required_not_cargo_in_another_container(self):
        data = world()
        data["mission_state"]["quest_manager"] = {}
        data["raw_records"] = [{"StaticItemDataRowName": "Wood_Crate_Small", "SavedInventories": [{"Slots": [{"ItemStaticData": "Mission_Communication_Device_T4"}]}]}]
        by_id = {m["id"]: m for m in build_missions(data)}
        self.assertEqual("unknown", by_id["PRO_Story_5"]["status_key"])
        data["raw_records"] = [terminal(tier=3)]
        by_id = {m["id"]: m for m in build_missions(data)}
        self.assertEqual("locked", by_id["PRO_Story_5"]["status_key"])

    def test_abandoned_is_not_failed_or_available_without_retry_confirmation(self):
        data = world()
        data["mission_state"]["mission_history"].append({"Mission": "PRO_A_Stockpile", "Status": 2})
        mission = next(m for m in build_missions(data) if m["id"] == "PRO_A_Stockpile")
        self.assertEqual("unknown", mission["status_key"])
        self.assertIn("已放棄", mission["status_reason"])
        self.assertNotIn("失敗", mission["status_reason"])

    def test_completed_flag_does_not_treat_dialogue_or_reached_as_whole_stage(self):
        data = world()
        data["mission_state"]["quests"] = [
            {"QuestName": "PRO_Story_5_Investigate", "VariableRecords": [{"VariableName": "Complete", "bVariable": 1}]},
            {"QuestName": "PRO_Story_5_Investigate_Enter", "VariableRecords": [{"VariableName": "DialoguePlayed", "bVariable": 1}, {"VariableName": "LocationReached", "bVariable": 1}]},
            {"QuestName": "PRO_Story_5_Outpost_Power", "VariableRecords": []}]
        mission = next(m for m in build_missions(data) if m["id"] == "PRO_Story_5")
        progress = {p["id"]: p for p in mission["progress"]}
        self.assertTrue(progress["PRO_Story_5_Investigate"]["complete"])
        self.assertFalse(progress["PRO_Story_5_Investigate_Enter"]["complete"])
        self.assertTrue(progress["PRO_Story_5_Investigate_Enter"]["reached"])
        self.assertIn("10000 W", mission["next_step"])
        self.assertIn("未提供足夠資料", mission["next_step"])

    def test_transmission_time_is_bounded_and_updates_next_step(self):
        data = world()
        data["mission_state"]["quests"] = [{"QuestName": "PRO_Story_5_Outpost_Progress", "VariableRecords": [{"VariableName": "CurrentTime", "fVariable": 150.0}]}]
        mission = next(m for m in build_missions(data) if m["id"] == "PRO_Story_5")
        self.assertIn("25.0%", mission["next_step"])
        self.assertIn("450", mission["next_step"])

    def test_html_escapes_game_text_and_keeps_map_data_out_of_guide(self):
        mission = build_missions(world())[0]
        mission["position"] = [1, 2, 3]
        mission["summary"] = "<script>unsafe</script>"
        html = mission_html(mission)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("map://", html)
        self.assertEqual([1, 2, 3], mission["position"])

    def test_repeated_refresh_does_not_modify_snapshot_or_duplicate_progress(self):
        data = world()
        quest = {"QuestName": "PRO_Story_5_Outpost_Power", "VariableRecords": []}
        data["mission_state"]["quests"] = [quest]
        data["mission_records"] = [quest]
        original = copy.deepcopy(data)
        first = build_missions(data)
        second = build_missions(data)
        self.assertEqual(original, data)
        self.assertEqual(first, second)
        mission = next(m for m in first if m["id"] == "PRO_Story_5")
        self.assertEqual(1, len(mission["progress"]))

    def test_smpl_types_are_not_falsely_reported_as_current_offers(self):
        for mission in build_missions(world()):
            if mission["kind"] == "SMPL3":
                self.assertEqual("unknown", mission["status_key"])
                self.assertIn("抽選", mission["status_reason"])

    def test_horizontal_base_distance_ignores_height_and_labels_record_scope(self):
        data = world()
        data["mission_state"]["quests"] = [{"QuestName": "PRO_Story_5_Outpost_Power", "VariableRecords": [], "ActorTransform": {"Translation": [30000, 40000, 900000]}}]
        missions = build_missions(data, context={"active_base": {"name": "測試基地", "x": 0, "y": 0, "z": 0}})
        current = missions[0]
        self.assertAlmostEqual(500, current["horizontal_distance_m"])
        self.assertFalse(current["distance_affects_ranking"])
        self.assertIn("任務記錄點", current["distance_note"])
        self.assertNotIn("基地距離", mission_html(current))
        candidate = next(m for m in missions if m["id"] == "PRO_A_Extraction")
        self.assertGreater(candidate["horizontal_distance_m"], 0)
        self.assertEqual("candidate_area", candidate["distance_status"])
        self.assertFalse(candidate["distance_affects_ranking"])
        self.assertIn("候選地點", candidate["distance_note"])

    def test_existing_base_is_not_reported_missing_when_target_is_pending(self):
        mission = {"id": "not-yet-generated"}
        with patch("icarus_helper.mission_locations.build_mission_locations", return_value={"points": []}):
            adjustment = _distance(mission, world(), {"active_base": {"name": "我們家", "x": 0, "y": 0}})
        self.assertEqual(0, adjustment)
        self.assertTrue(mission["base_known"])
        self.assertIsNone(mission["horizontal_distance_m"])
        self.assertEqual("target_pending", mission["distance_status"])
        self.assertIn("已指定基地「我們家」", mission["distance_note"])
        self.assertIn("任務位置尚未確定", mission["distance_note"])

    def test_route_distance_uses_known_area_before_nearer_unselected_candidate(self):
        mission = {"id": "mission-with-route"}
        route = {"points": [
            {"id": "unselected", "x": 100, "y": 0, "z": 0, "confidence": "candidate"},
            {"id": "selected", "x": 30000, "y": 40000, "z": 900000, "confidence": "known"},
        ]}
        with patch("icarus_helper.mission_locations.build_mission_locations", return_value=route):
            adjustment = _distance(mission, world(), {"active_base": {"name": "家", "x": 0, "y": 0}})
        self.assertAlmostEqual(500, mission["horizontal_distance_m"])
        self.assertEqual("selected", mission["distance_target_id"])
        self.assertEqual("known_area", mission["distance_status"])
        self.assertFalse(mission["distance_affects_ranking"])
        self.assertEqual(0, adjustment)

    def test_candidate_only_route_distance_is_explicit_and_not_ranked(self):
        mission = {"id": "mission-with-unselected-route"}
        route = {"points": [{"id": "possible", "x": 30000, "y": 40000, "z": 800000, "confidence": "candidate"}]}
        with patch("icarus_helper.mission_locations.build_mission_locations", return_value=route):
            adjustment = _distance(mission, world(), {"active_base": {"x": 0, "y": 0}})
        self.assertAlmostEqual(500, mission["horizontal_distance_m"])
        self.assertEqual("candidate_area", mission["distance_status"])
        self.assertIn("候選地點", mission["distance_note"])
        self.assertFalse(mission["distance_affects_ranking"])
        self.assertEqual(0, adjustment)

    def test_verified_goal_distance_only_adjusts_within_status(self):
        catalog = copy.deepcopy(load_catalog())
        for mission in catalog["missions"]:
            mission.update(position=[800000, 0, 0], position_reliable=True, position_scope="objective")
            if mission["id"] == "PRO_B_Extermination":
                mission["position"] = [10000, 0, 0]
            if mission["id"] == "PRO_Story_5":
                mission["position"] = [100000000, 0, 0]
        with patch("icarus_helper.missions.load_catalog", return_value=catalog):
            missions = build_missions(world(), context={"active_base": {"name": "家", "x": 0, "y": 0}})
        self.assertEqual("PRO_Story_5", missions[0]["id"])
        available = [m for m in missions if m["status_key"] == "available"]
        self.assertEqual("PRO_B_Extermination", available[0]["id"])
        self.assertTrue(available[0]["distance_affects_ranking"])
        order = {"active": 0, "available": 1, "unknown": 2, "locked": 3, "completed": 4}
        self.assertEqual(sorted(order[m["status_key"]] for m in missions), [order[m["status_key"]] for m in missions])

    def test_mismatched_world_base_does_not_produce_zero_or_cross_world_distance(self):
        data = world()
        data["world_id"] = "world-B"
        data["mission_state"]["quests"] = [{"QuestName": "PRO_Story_5_Outpost_Power", "ActorTransform": {"Translation": [100, 200, 0]}}]
        mission = build_missions(data, context={"active_base": {"world_id": "world-A", "x": 0, "y": 0}})[0]
        self.assertIsNone(mission["horizontal_distance_m"])
        self.assertIn("世界不符", mission["distance_note"])

    def test_save_member_list_does_not_claim_realtime_player_presence(self):
        data = world()
        data["prospect_info"]["AssociatedMembers"] = [{"UserID": "A", "IsCurrentlyPlaying": True}, {"UserID": "B", "IsCurrentlyPlaying": False}]
        mission = build_missions(data)[0]
        self.assertEqual(2, mission["party_size_recorded"])
        self.assertIn("不代表即時在場", mission["party_note"])
        self.assertIn("尚未確認", mission["technology_note"])

    def test_updated_game_downgrades_eligibility_but_preserves_save_facts(self):
        with patch("icarus_helper.missions.installed_game_build", return_value="99999999"):
            missions = build_missions(world())
        by_id = {m["id"]: m for m in missions}
        self.assertEqual("active", by_id["PRO_Story_5"]["status_key"])
        self.assertEqual("completed", by_id["PRO_Story_4"]["status_key"])
        self.assertEqual("unknown", by_id["PRO_A_Extraction"]["status_key"])
        self.assertFalse(by_id["PRO_A_Extraction"]["version_compatible"])
        html = mission_html(by_id["PRO_Story_5"])
        self.assertNotIn("版本不符", html)
        self.assertNotIn("99999999", html)
        self.assertIn("版本不符", by_id["PRO_Story_5"]["version_note"])

    def test_missing_installed_build_is_unverified(self):
        with patch("icarus_helper.missions.installed_game_build", return_value=None):
            missions = build_missions(world())
        mission = next(m for m in missions if m["id"] == "PRO_A_Extraction")
        self.assertEqual("unknown", mission["status_key"])
        self.assertIsNone(mission["version_compatible"])
        self.assertIn("版本未驗證", mission["version_note"])

    def test_key_mission_guides_include_required_transport_and_late_stages(self):
        missions = {m["id"]: m for m in build_missions(world())}
        rescue = missions["PRO_D_Rescue"]
        bags = next(p for p in rescue["checklist"] if p["item"] == "Stasis Bags")
        self.assertEqual("2", bags["quantity"])
        self.assertTrue(bags["required"])
        self.assertIn("Medevac Ship", " ".join(rescue["guide"]))
        self.assertIn("Daisy", " ".join(missions["PRO_Story_1"]["guide"]))
        self.assertIn("Giant Lava Creature", " ".join(missions["PRO_Story_6"]["guide"]))
        self.assertIn("Noxious Crust", " ".join(missions["PRO_C_Research"]["guide"]))
        self.assertIn("Chi", " ".join(missions["PRO_D_Research"]["guide"]))

    def test_other_map_fallback_guide_is_visibly_marked_as_incomplete(self):
        data = world()
        data["map_name"] = "Olympus"
        data["mission_state"] = {}
        mission = next(m for m in build_missions(data) if m["kind"] != "SMPL3")
        self.assertIn("個別細節待補", mission_html(mission))

    def test_guide_removes_requested_diagnostics_without_losing_gameplay_content(self):
        mission = build_missions(world())[0]
        mission["progress"] = [{"text": "供電", "state": "進行中", "details": ["傳輸 150 / 600 秒"]}]
        original = copy.deepcopy(mission)
        rendered = mission_html(mission)
        for heading in ("判定依據", "推薦理由", "下一步", "基地距離", "目前世界難度", "前置與接取", "來源與適用範圍", "攻略範圍"):
            self.assertNotIn(heading, rendered)
        for diagnostic in ("version_note", "status_reason", "recommendation_reason", "next_step", "distance_note", "party_note", "technology_note", "inventory_note", "source_version", "source_date"):
            value = str(mission.get(diagnostic, ""))
            if value:
                self.assertNotIn(value, rendered, diagnostic)
        for heading in ("出發前準備", "任務攻略", "任務進度", "風險與補給", "獎勵與解鎖", "任務目標"):
            self.assertIn(heading, rendered)
        self.assertIn("傳輸 150 / 600 秒", rendered)
        self.assertIn(mission["checklist"][0]["item"], rendered)
        self.assertEqual(original, mission)


if __name__ == "__main__":
    unittest.main()
