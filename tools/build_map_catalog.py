"""Build compact, current-version map metadata in the user's local APP cache.

The installed game is read-only. Large extracted Unreal files stay in LOCALAPPDATA,
not in the project; only compact metadata and image caches are consumed by the APP.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
from pathlib import Path

from catalog_inventory import CACHE, GAME, REPAK, UASSET_LIBRARY, inventory, require_validated_build


def build() -> None:
    require_validated_build()
    data = inventory()
    tool = REPAK
    data_tables = ["World/D_VoxelDistributionRegion.json", "World/D_VoxelSetupData.json", "FLOD/D_FLODDescriptions.json"]
    data_pak = GAME / "Data/data.pak"
    listing = subprocess.run([str(tool), "list", str(data_pak), "-s", ""], capture_output=True, text=True, check=True)
    first = next(path for path in listing.stdout.splitlines() if path.endswith(data_tables[0]))
    prefix = first[:-len(data_tables[0])]
    command = [str(tool), "unpack", str(data_pak), "-o", str(CACHE / "game_data"), "-s", prefix, "-f", "-q"]
    for table in data_tables:
        command += ["-i", table]
    subprocess.run(command, check=True)
    extracted = CACHE / "game_assets"
    paths = [r for r in data["prometheus_generated"]
             if re.search(r"/T019_Generated_x\d+_y\d+\.(umap|uexp)$", r["path"])]
    height_paths = [r for r in data["prometheus_heightmaps"]
                    if re.search(r"/heightmap_x\d+_y\d+\.(umap|uexp)$", r["path"])]
    paths += height_paths
    for pak in sorted(set(r["pak"] for r in paths)):
        missing = [r for r in paths if r["pak"] == pak and not (extracted / r["path"]).exists()]
        if not missing:
            continue
        command = [str(tool), "unpack", str(GAME / "Paks" / pak), "-o", str(extracted), "-f", "-q"]
        for item in missing:
            command += ["-i", item["path"]]
        subprocess.run(command, check=True)
        print(f"Extracted {len(missing)} map parts from {pak}", flush=True)

    scripts = Path(__file__).resolve().parent
    command = ["pwsh", "-NoProfile", "-File", str(scripts / "extract_map_catalog.ps1"),
               "-AssetRoot", str(extracted / "Icarus/Content/Maps/Terrain_019_DLC/Sublevels/Generated"),
               "-OutputRoot", str(CACHE / "tiles"), "-UAssetLibrary", str(UASSET_LIBRARY)]
    subprocess.run(command, check=True)
    command[command.index("-AssetRoot") + 1] = str(extracted / "Icarus/Content/Maps/Terrain_019_DLC/heightmap")
    command[command.index("-OutputRoot") + 1] = str(CACHE / "height_tiles")
    subprocess.run(command, check=True)
    records = [json.loads(p.read_text(encoding="utf-8-sig")) for p in sorted((CACHE / "tiles").glob("*.json"))]
    heights = [json.loads(p.read_text(encoding="utf-8-sig")) for p in sorted((CACHE / "height_tiles").glob("*.json"))]
    compact = {
        "schema_version": 4, "game_build": "26313227", "map": "Prometheus", "map_key": "Terrain_019",
        "source": "Installed ICARUS Unreal 4.27 map assets", "tiles_expected": (len(paths) - len(height_paths)) // 2,
        "tiles_loaded": len(records), "tiles": [r["tile"] for r in records],
        "height_tiles_expected": len(height_paths) // 2, "height_tiles_loaded": len(heights),
        "raw_export_count": sum(r["raw_export_count"] for r in records),
        "points": [point for record in records + heights for point in record["points"]],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coordinate_rule": "UE cm; ordinary actors RootComponent.RelativeLocation + WorldTileInfo.Position; foliage BuiltInstanceBounds + component/attach-parent RelativeLocation + WorldTileInfo.Position, identity rotation/scale only",
        "source_manifest_sha256": hashlib.sha256((GAME.parents[3] / "appmanifest_1149460.acf").read_bytes()).hexdigest(),
        "source_packages": [{"name": name, "size": (GAME / "Paks" / name).stat().st_size,
                             "modified_ns": (GAME / "Paks" / name).stat().st_mtime_ns}
                            for name in sorted(set(row["pak"] for row in paths))],
    }
    target = CACHE / "prometheus_nodes.json"
    target.write_text(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"catalog": str(target), "tiles": len(records), "points": len(compact["points"])}), flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
