"""Resolve FLOD stable record indices to installed static meshes for save joins."""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import subprocess

from catalog_inventory import CACHE, GAME, REPAK, UASSET_LIBRARY, require_validated_build


def build() -> None:
    require_validated_build()
    description_path = CACHE / "game_data/FLOD/D_FLODDescriptions.json"
    if not description_path.exists():
        raise RuntimeError("First extract current FLOD/D_FLODDescriptions.json from data.pak")
    description = json.loads(description_path.read_text(encoding="utf-8-sig"))
    wanted = set()
    for row in description["Rows"]:
        reference = row.get("FoliageType", "None")
        if reference == "None":
            continue
        stem = reference.split(".")[0].replace("/Game/", "Icarus/Content/", 1)
        wanted.update((stem + ".uasset", stem + ".uexp"))
    tool = REPAK
    output = CACHE / "game_assets"

    def one(pak):
        result = subprocess.run([str(tool), "list", str(pak)], capture_output=True, text=True, check=True)
        return pak, [path for path in result.stdout.splitlines() if path in wanted and not (output / path).exists()]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        listings = list(pool.map(one, GAME.joinpath("Paks").glob("*.pak")))
    for pak, paths in listings:
        if not paths:
            continue
        command = [str(tool), "unpack", str(pak), "-o", str(output), "-f", "-q"]
        for path in paths:
            command += ["-i", path]
        subprocess.run(command, check=True)
        print(f"Extracted {len(paths)} FoliageType asset parts", flush=True)
    script = Path(__file__).with_name("extract_foliage_lookup.ps1")
    subprocess.run(["pwsh", "-NoProfile", "-File", str(script), "-AssetRoot", str(output),
                    "-DescriptionPath", str(description_path), "-OutputPath", str(CACHE / "foliage_lookup.json"),
                    "-UAssetLibrary", str(UASSET_LIBRARY)], check=True)


if __name__ == "__main__":
    build()
