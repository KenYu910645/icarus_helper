"""Read installed Unreal pak indexes; never modifies game files."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


GAME = Path(os.environ.get("ICARUS_CONTENT", r"C:\Program Files (x86)\Steam\steamapps\common\Icarus\Icarus\Content")).expanduser()
CACHE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IcarusHelper" / "catalog_cache" / "26313227"
REPAK = Path(os.environ.get("ICARUS_REPAK", shutil.which("repak") or "repak.exe")).expanduser()
UASSET_LIBRARY = Path(os.environ.get("ICARUS_UASSET_LIBRARY", str(Path(__file__).resolve().parent / "vendor" / "UAssetAPI" / "UAssetAPI.dll"))).expanduser()


def require_validated_build() -> None:
    manifest = Path(os.environ.get("ICARUS_STEAM_MANIFEST", str(GAME.parents[3] / "appmanifest_1149460.acf"))).expanduser()
    actual = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8"))
    if actual is None or actual.group(1) != "26313227":
        raise RuntimeError("Game build differs from validated 26313227; revalidate parser, transforms and native generation before rebuilding this catalog")


def inventory() -> dict[str, list[dict[str, str]]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / "pak_inventory.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if "prometheus_heightmaps" in cached:
            return cached
    tool = REPAK

    def one(pak: Path):
        proc = subprocess.run([str(tool), "list", str(pak)], capture_output=True, text=True, check=True)
        paths = proc.stdout.splitlines()
        return pak.name, paths

    output = {"prometheus_generated": [], "prometheus_heightmaps": [], "map_textures": [], "cave_assets": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for pak, paths in pool.map(one, GAME.joinpath("Paks").glob("*.pak")):
            for path in paths:
                item = {"pak": pak, "path": path}
                if "/Terrain_019_DLC/Sublevels/Generated/" in path and path.endswith((".umap", ".uexp")):
                    output["prometheus_generated"].append(item)
                if "/Terrain_019_DLC/heightmap/" in path and path.endswith((".umap", ".uexp")):
                    output["prometheus_heightmaps"].append(item)
                if "Map" in path and "Terrain_019" in path and "Texture" in path:
                    output["map_textures"].append(item)
                if "/Terrain_019_DLC/" in path and "Cave" in path:
                    output["cave_assets"].append(item)
    cache_file.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    data = inventory()
    print(json.dumps({k: len(v) for k, v in data.items()}))
    print(json.dumps(data["prometheus_generated"][:4]))
