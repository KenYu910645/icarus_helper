"""Build compact native item thumbnails from the installed read-only UE4 paks.

Only 128px PNGs and source/hash metadata enter the application. Extracted game
packages remain in LocalAppData; no game/save file is written or loaded.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import subprocess

from PIL import Image

from catalog_inventory import CACHE, GAME, REPAK, require_validated_build

ROOT = Path(__file__).resolve().parents[1]
WORK = CACHE / "item_icon_source"
OUTPUT = ROOT / "icarus_helper" / "assets" / "items"
FORMATS = {"PF_DXT1": (b"DXT1", 8), "PF_DXT3": (b"DXT3", 16), "PF_DXT5": (b"DXT5", 16), "PF_BC7": (b"DX10", 16)}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dds(payload: bytes, width: int, height: int, pixel_format: str) -> bytes:
    fourcc, _ = FORMATS[pixel_format]
    header = struct.pack("<7I11I8I5I", 124, 0x81007, height, width, len(payload), 0, 1,
                         *([0] * 11), 32, 4, struct.unpack("<I", fourcc)[0], 0, 0, 0, 0, 0,
                         0x1000, 0, 0, 0, 0)
    # BC7_UNORM, Texture2D, miscFlag=0, arraySize=1, miscFlags2=0.
    return b"DDS " + header + (struct.pack("<5I", 98, 3, 0, 1, 0) if fourcc == b"DX10" else b"") + payload


def decode_texture(header: bytes, exported: bytes) -> tuple[Image.Image, dict]:
    """Read validated cooked top mip; reject unsupported layouts explicitly.

    UE4.27 serializes SizeX/Y/PackedData/FString PixelFormat, FirstMip,
    MipCount, then the cooked mip's FByteBulkData and its trailing dimensions.
    For these UI textures the top mip is inline in the paired .uexp file.
    """
    candidates = []
    for match in re.finditer(rb"PF_[A-Za-z0-9_]+\x00", exported):
        start, end = match.span()
        if start < 16 or len(exported) < end + 32:
            continue
        width, height, packed, length = struct.unpack_from("<4I", exported, start - 16)
        if length != end - start or not (0 < width <= 16384 and 0 < height <= 16384) or packed != 1:
            continue
        pixel_format = match.group()[:-1].decode("ascii")
        first_mip, mip_count, cooked, flags, count, size, offset = struct.unpack_from("<6Iq", exported, end)
        data_start = end + 32
        if cooked != 1 or first_mip != 0 or not 1 <= mip_count <= 16:
            continue
        if flags not in (0x48, 0x40) or offset != len(header) + data_start:
            continue
        if size != count or size < 1 or data_start + size + 12 > len(exported):
            continue
        if struct.unpack_from("<3I", exported, data_start + size) != (width, height, 1):
            continue
        candidates.append((width, height, pixel_format, exported[data_start:data_start + size], mip_count))
    if len(candidates) != 1:
        raise ValueError(f"Expected one validated cooked inline texture, found {len(candidates)}")
    width, height, pixel_format, payload, mip_count = candidates[0]
    if pixel_format in FORMATS:
        expected = ((width + 3) // 4) * ((height + 3) // 4) * FORMATS[pixel_format][1]
        if len(payload) != expected:
            raise ValueError("Compressed mip size does not match dimensions")
        with Image.open(io.BytesIO(_dds(payload, width, height, pixel_format))) as image:
            picture = image.convert("RGBA")
    elif pixel_format == "PF_B8G8R8A8" and len(payload) == width * height * 4:
        picture = Image.frombytes("RGBA", (width, height), payload, "raw", "BGRA")
    elif pixel_format == "PF_G8" and len(payload) == width * height:
        picture = Image.frombytes("L", (width, height), payload).convert("RGBA")
    else:
        raise ValueError(f"Unsupported pixel format or byte count: {pixel_format}")
    return picture, {"width": width, "height": height, "pixel_format": pixel_format, "mip_count": mip_count}


def icon_references() -> tuple[dict, list]:
    source = CACHE / "inventory_source"
    item_path = source / "Items" / "D_ItemsStatic.json"
    trait_path = source / "Traits" / "D_Itemable.json"
    statics = json.loads(item_path.read_text(encoding="utf-8"))
    itemables = json.loads(trait_path.read_text(encoding="utf-8"))
    traits = {row["Name"]: row for row in itemables["Rows"]}
    catalog = json.loads((ROOT / "icarus_helper/data/inventory_items.json").read_text(encoding="utf-8"))
    references = {}
    for row in statics["Rows"]:
        if row["Name"] not in catalog["items"]:
            continue
        trait = traits.get(row.get("Itemable", {}).get("RowName"), {})
        references[row["Name"]] = trait.get("Icon", itemables["Defaults"]["Icon"])
    sources = [{"path": str(path.relative_to(source)), "sha256": sha(path.read_bytes())} for path in (item_path, trait_path)]
    return references, sources


def pak_index() -> dict:
    cached = WORK / "pak_icon_index.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    def one(pak):
        lines = subprocess.run([str(REPAK), "list", str(pak)], capture_output=True, text=True, check=True).stdout.splitlines()
        return pak.name, [path for path in lines if "/2DArt/" in path or "/UI/Items/" in path]
    with ThreadPoolExecutor(5) as pool:
        result = dict(pool.map(one, GAME.joinpath("Paks").glob("*.pak")))
    cached.write_text(json.dumps(result), encoding="utf-8")
    return result


def main() -> None:
    require_validated_build()
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    references, sources = icon_references()
    index = pak_index()
    locations = defaultdict(list)
    for pak, paths in index.items():
        for path in paths:
            locations[path.casefold()].append((pak, path))
    textures, missing = {}, {}
    for item, reference in references.items():
        if not reference or reference == "None":
            missing[item] = "Itemable has no icon reference"
            continue
        package = reference.split(".")[0]
        asset = "Icarus/Content/" + package.removeprefix("/Game/")
        matches = locations.get((asset + ".uasset").casefold(), [])
        if len(matches) != 1:
            missing[item] = f"Icon package missing or ambiguous ({len(matches)} matches)"
            continue
        pak, path = matches[0]
        textures.setdefault(package, {"pak": pak, "asset": path[:-7], "items": []})["items"].append(item)
    extract = defaultdict(set)
    for spec in textures.values():
        # Directory includes avoid reopening a gigabyte pak once per small batch
        # of files. Only required UI folders are copied to the build cache.
        extract[spec["pak"]].add(spec["asset"].rsplit("/", 1)[0])
    for pak, directories in extract.items():
        directories = sorted(directories)
        directories = [path for path in directories if not any(path.startswith(parent + "/") for parent in directories if parent != path)]
        for start in range(0, len(directories), 80):
            command = [str(REPAK), "unpack", str(GAME / "Paks" / pak), "-o", str(WORK), "-q", "-f"]
            for path in directories[start:start + 80]:
                command.extend(("-i", path))
            subprocess.run(command, check=True)
        print(f"Extracted {len(directories)} UI icon folders from {pak}", flush=True)
    result = {"schema_version": 1, "game_build": "26313227", "verified_at": "2026-11-01", "thumbnail_max_px": 128,
              "source_chain": "D_ItemsStatic.Itemable -> D_Itemable.Icon -> exact cooked texture package",
              "sources": sources, "items": {}, "textures": {}, "missing": missing}
    errors = {}
    def convert(entry):
        package, spec = entry
        header = (WORK / (spec["asset"] + ".uasset")).read_bytes()
        exported = (WORK / (spec["asset"] + ".uexp")).read_bytes()
        image, metadata = decode_texture(header, exported)
        source_hash = sha(header + exported)
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        name = sha(package.casefold().encode())[:24] + ".png"
        image.save(OUTPUT / name, optimize=True)
        metadata.update(source=package, source_pak=spec["pak"], source_sha256=source_hash,
                        thumbnail_width=image.width, thumbnail_height=image.height,
                        png_sha256=sha((OUTPUT / name).read_bytes()))
        return name, metadata, spec["items"]
    with ThreadPoolExecutor(6) as pool:
        futures = {pool.submit(convert, entry): entry for entry in textures.items()}
        from concurrent.futures import as_completed
        for future in as_completed(futures):
            package, spec = futures[future]
            try:
                name, metadata, items = future.result()
                result["textures"][name] = metadata
                for item in items:
                    result["items"][item] = name
            except Exception as exc:
                errors[package] = str(exc)
                for item in spec["items"]:
                    result["missing"][item] = str(exc)
    result["items"] = dict(sorted(result["items"].items()))
    result["textures"] = dict(sorted(result["textures"].items()))
    result["missing"] = dict(sorted(result["missing"].items()))
    result["coverage"] = {"item_definitions": len(references), "native_icons": len(result["items"]),
                          "unique_textures": len(result["textures"]), "missing_icons": len(result["missing"]),
                          "pixel_formats": dict(Counter(meta["pixel_format"] for meta in result["textures"].values()))}
    output = ROOT / "icarus_helper/data/item_icons.json"
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (WORK / "decode_errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print(json.dumps(result["coverage"]), flush=True)
    if errors:
        print(f"{len(errors)} unsupported icon packages; retained explicit missing records", flush=True)


if __name__ == "__main__":
    main()
