from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icarus_helper.catalog import load_catalog, merge_catalog, select_mineral
from icarus_helper.models import Resource, WorldSnapshot


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = "actor:ordinary:1"
        self.node = {"id": "ore_1", "actor_path": self.paths, "kind": "ore", "x": 100, "y": 200, "z": 300,
                     "seed": 123, "pool": "Test_Caves", "tile": "T019_Generated_x1_y1"}
        self.write("prometheus_nodes.json", {"game_build": "26313227", "tiles_loaded": 64, "tiles_expected": 64, "points": [self.node]})
        self.write("prometheus_public.json", {"caves": [], "ores": []})
        self.write("game_data/World/D_VoxelDistributionRegion.json", {"Rows": [{"Name": "Test_Caves", "VoxelDistribution": {'(RowName="Gold_Normal")': 1}}]})
        self.write("game_data/World/D_VoxelSetupData.json", {"Rows": [{"Name": "Gold_Normal", "DensityMultiplier": 1.2}]})
        self.cache_patch = patch("icarus_helper.catalog._cache", return_value=self.root)
        self.build_patch = patch("icarus_helper.catalog.installed_game_build", return_value="26313227")
        self.cache_patch.start()
        self.build_patch.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.cache_patch.stop)
        self.addCleanup(self.build_patch.stop)

    def write(self, filename, data):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def world(self, name="Prometheus"):
        now = datetime.now(timezone.utc)
        result = WorldSnapshot(Path("fixture.json"), "world-fixture", "Fixture", name, now, now, "hash")
        result.raw_records = [{"GameModeRecord": {"GameStateSeed": 42}}]
        return result

    def test_static_mineral_has_unknown_remaining_without_save_record(self):
        world = self.world()
        resources = merge_catalog(world)
        ore = next(row for row in resources if row.kind == "ore")
        self.assertEqual(ore.resource_type, "Gold_Normal")
        self.assertIsNone(ore.remaining)
        self.assertEqual(ore.remaining_status, "採集狀態未知")

    def test_exact_actor_join_preserves_depletion_and_override(self):
        world = self.world()
        world.resources = [Resource("saved:ore", "ore", "礦", "未知", 100, 200, 300, remaining=0,
                                    remaining_status="已耗盡", actor_path=self.paths,
                                    raw={"VoxelSaveData": {"VoxelResourceOverride": "Copper_Normal", "bIsVoxelFullyMined": 1}})]
        resources = merge_catalog(world)
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].remaining, 0)
        self.assertEqual(resources[0].resource_type, "Copper_Normal")
        self.assertEqual(resources[0].remaining_status, "已耗盡")

    def test_missing_world_seed_does_not_guess_mineral(self):
        world = self.world()
        world.raw_records = []
        resources = merge_catalog(world)
        self.assertEqual(resources[0].resource_type, "未知")
        self.assertIsNone(resources[0].remaining)

    def test_new_game_build_disables_old_assets(self):
        with patch("icarus_helper.catalog.installed_game_build", return_value="99999999"):
            catalog = load_catalog("Prometheus", 42)
            self.assertFalse(catalog["coverage"]["asset_build_compatible"])
            self.assertFalse(any(row["kind"] == "ore" for row in catalog["resources"]))

    def test_wrong_world_catalog_is_not_merged(self):
        wrong = load_catalog("Prometheus", 42)
        world = self.world("Olympus")
        resources = merge_catalog(world, wrong)
        self.assertFalse(any(row.kind == "ore" for row in resources))
        self.assertEqual(wrong["map"], "Olympus")

    def test_out_of_bounds_density_group_is_quarantined(self):
        bad = {"id": "bad", "actor_path": "actor:bad", "kind": "tree", "x": -1127091, "y": 200, "z": 0,
               "mesh": "mesh:tree", "generated_instance_count": 10,
               "region_bounds": {"min_x": -1127191, "max_x": -1126991, "min_y": 100, "max_y": 300}}
        self.write("prometheus_nodes.json", {"game_build": "26313227", "points": [bad]})
        catalog = load_catalog("Prometheus", 42)
        self.assertEqual(catalog["coverage"]["excluded_unverified_region_groups"], 1)
        self.assertEqual(catalog["resources"], [])

    def test_unique_foliage_join_counts_instances_not_items(self):
        group = {"id": "tree", "actor_path": "actor:tree", "kind": "tree", "x": 100, "y": 200, "z": 0,
                 "tile": "heightmap_x1_y1", "mesh": "mesh:tree", "generated_instance_count": 10,
                 "region_bounds": {"min_x": 0, "max_x": 200, "min_y": 100, "max_y": 300}}
        self.write("prometheus_nodes.json", {"game_build": "26313227", "points": [group]})
        self.write("foliage_lookup.json", [{"record_index": 7, "mesh": "mesh:tree", "name": "tree", "view_trace_actor": "BP_Tree"}])
        world = self.world()
        world.raw_records += [{"_component": "FLODTileRecorderComponent", "Record": {"TileName": "HeightMap_x1_y1", "Records": [
            {"RecordIndex": 7, "DestroyedInstanceIndices": [1, 2, 2], "Instances": [], "DynamicInstances": []}]}}]
        tree = next(row for row in merge_catalog(world) if row.kind == "tree")
        self.assertEqual(tree.remaining, 8)
        self.assertEqual(tree.remaining_unit, "生成實例（非物品產量）")
        self.assertEqual(tree.raw["region_bounds"], group["region_bounds"])

    def test_out_of_range_destroyed_index_does_not_create_negative_count(self):
        group = {"id": "tree", "actor_path": "actor:tree", "kind": "tree", "x": 100, "y": 200, "z": 0,
                 "tile": "heightmap_x1_y1", "mesh": "mesh:tree", "generated_instance_count": 2,
                 "region_bounds": {"min_x": 0, "max_x": 200, "min_y": 100, "max_y": 300}}
        self.write("prometheus_nodes.json", {"game_build": "26313227", "points": [group]})
        self.write("foliage_lookup.json", [{"record_index": 7, "mesh": "mesh:tree", "name": "tree", "view_trace_actor": "BP_Tree"}])
        world = self.world()
        world.raw_records += [{"_component": "FLODTileRecorderComponent", "Record": {"TileName": "HeightMap_x1_y1", "Records": [
            {"RecordIndex": 7, "DestroyedInstanceIndices": [999], "Instances": [], "DynamicInstances": []}]}}]
        tree = next(row for row in merge_catalog(world) if row.kind == "tree")
        self.assertIsNone(tree.remaining)

    def test_seed_uses_unreal_unsigned_overflow(self):
        pool = [("Gold_Normal", 1.0), ("Copper_Normal", 3.0)]
        self.assertEqual(select_mineral(-1, pool), select_mineral(0xFFFFFFFF, pool))
        self.assertEqual(select_mineral(10, pool), select_mineral(10 + 2**32, pool))


if __name__ == "__main__":
    unittest.main()
