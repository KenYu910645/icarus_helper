"""Build native creature portraits using only explicit game data references.

Reads the installed data.pak and UI textures without changing game or save files.
Set ICARUS_REPAK/ICARUS_CONTENT as described in tools/README.md.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import subprocess

from PIL import Image

from build_item_icons import decode_texture, pak_index, sha
from catalog_inventory import CACHE, GAME, REPAK, require_validated_build

ROOT = Path(__file__).resolve().parents[1]
WORK = CACHE / "animal_icon_source"
OUTPUT = ROOT / "icarus_helper/assets/animals"


def main() -> None:
    require_validated_build()
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    index = subprocess.check_output([str(REPAK), "list", str(GAME / "Data/data.pak"), "-s", ""], text=True).splitlines()
    sources = []

    def table(relative):
        matches = [path for path in index if path.endswith("/" + relative)]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous game table: {relative}")
        raw = subprocess.check_output([str(REPAK), "get", str(GAME / "Data/data.pak"), matches[0], "-s", ""])
        sources.append({"path": relative, "sha256": sha(raw)})
        return json.loads(raw)

    setups = table("AI/D_AISetup.json")
    bestiary = table("Bestiary/D_BestiaryData.json")
    guide = {row["Name"]: row for row in bestiary["Rows"]}
    catalog_path = ROOT / "icarus_helper/data/animal_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    locations = defaultdict(list)
    for pak, paths in pak_index().items():
        for path in paths:
            locations[path.casefold()].append((pak, path))
    species, textures, missing = {}, {}, {}
    for row in setups["Rows"]:
        species_id = row["Name"]
        if species_id not in catalog["species"]:
            continue
        group = row.get("BestiaryGroup", setups["Defaults"]["BestiaryGroup"]).get("RowName")
        reference = guide.get(group, {}).get("Image", bestiary["Defaults"]["Image"])
        if not isinstance(reference, str) or not reference.startswith("/Game/"):
            missing[species_id] = "AISetup does not reference a BestiaryGroup with an image"
            continue
        package = reference.split(".")[0]
        asset = "Icarus/Content/" + package.removeprefix("/Game/")
        matches = locations.get((asset + ".uasset").casefold(), [])
        if len(matches) != 1:
            missing[species_id] = f"Image package missing or ambiguous ({len(matches)} matches)"
            continue
        pak, actual = matches[0]
        filename = sha(package.casefold().encode())[:24] + ".png"
        species[species_id] = {"file": filename, "bestiary_group": group, "image": reference}
        textures.setdefault(filename, {"package": package, "asset": actual[:-7], "pak": pak})
    extract = defaultdict(set)
    for spec in textures.values():
        extract[spec["pak"]].add(spec["asset"].rsplit("/", 1)[0])
    for pak, directories in extract.items():
        command = [str(REPAK), "unpack", str(GAME / "Paks" / pak), "-o", str(WORK), "-q", "-f"]
        for directory in sorted(directories):
            command.extend(("-i", directory))
        subprocess.run(command, check=True)
    result = {"schema_version": 1, "game_build": "26313227", "verified_at": "2026-11-02",
              "thumbnail_max_px": 128,
              "source_chain": "D_AISetup.BestiaryGroup -> D_BestiaryData.Image -> exact cooked texture",
              "meaning": "Native bestiary species/group portrait; not the individual animal's coat or sex.",
              "sources": sources, "species": species, "textures": {}, "missing": missing}
    for filename, spec in textures.items():
        header = (WORK / (spec["asset"] + ".uasset")).read_bytes()
        exported = (WORK / (spec["asset"] + ".uexp")).read_bytes()
        picture, metadata = decode_texture(header, exported)
        # Transparent margins are presentation padding, not an individual animal's state.
        bounds = picture.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError(f"Empty native portrait: {spec['asset']}")
        picture = picture.crop(bounds)
        picture.thumbnail((128, 128), Image.Resampling.LANCZOS)
        picture.save(OUTPUT / filename, optimize=True)
        metadata.update(source=spec["package"], source_pak=spec["pak"], source_sha256=sha(header + exported),
                        alpha_crop=list(bounds), thumbnail_width=picture.width, thumbnail_height=picture.height,
                        png_sha256=sha((OUTPUT / filename).read_bytes()))
        result["textures"][filename] = metadata
    result["coverage"] = {"species_definitions": len(catalog["species"]), "native_icons": len(species),
                          "unique_textures": len(textures), "missing_icons": len(missing)}
    output = ROOT / "icarus_helper/data/animal_icons.json"
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(result["coverage"]))


if __name__ == "__main__":
    main()
