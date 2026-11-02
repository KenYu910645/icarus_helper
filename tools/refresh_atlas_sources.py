"""Cache public map metadata/backgrounds for personal local use; no runtime network."""
from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image
try:
    from .catalog_inventory import CACHE
except ImportError:  # Direct script invocation.
    from catalog_inventory import CACHE


ORIGIN = "https://www.icarusintel.com"
BACKGROUND_TILESETS = {"prometheus": "prometheus2"}
MAP_KEYS = ("prometheus", "olympus", "styx", "elysium")
TILE_ZOOM, SOURCE_TILE_SIZE, TILE_SIZE, TILE_AXIS = 3, 512, 256, 8


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "IcarusHelper/0.1 (local personal map cache)"}), timeout=25) as response:
        return response.read()


def fetch_layers(map_key: str) -> dict:
    url = f"{ORIGIN}/map/layers/{map_key}"
    try:
        return json.loads(fetch(url))
    except HTTPError as error:
        details = json.loads(error.read())
        version = details.get("serverVersion")
        if error.code != 426 or not version:
            raise
        return json.loads(fetch(url + "?" + urlencode({"clientVersion": version})))


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _temporary_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".part", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def _publish_files(files: dict[Path, bytes]) -> None:
    """Stage all outputs before replacement; restore earlier files on failure."""
    staged, originals, replaced = {}, {}, []
    try:
        for path, content in files.items():
            originals[path] = path.read_bytes() if path.exists() else None
            staged[path] = _temporary_bytes(path, content)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                restore = _temporary_bytes(path, originals[path])
                try:
                    os.replace(restore, path)
                finally:
                    restore.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _validate_tile(content: bytes) -> None:
    with Image.open(BytesIO(content)) as image:
        if image.format != "JPEG" or image.size != (SOURCE_TILE_SIZE, SOURCE_TILE_SIZE):
            raise ValueError(f"Expected {SOURCE_TILE_SIZE}x{SOURCE_TILE_SIZE} JPEG tile, got {image.format} {image.size}")
        image.load()


def _build_background(map_key: str, cache: Path) -> tuple[bytes, dict]:
    tileset = BACKGROUND_TILESETS.get(map_key, map_key)
    # Tileset and zoom isolate the Null Sector map from the old masked map.
    tile_root = cache / "image_tiles" / tileset / str(TILE_ZOOM)
    tile_root.mkdir(parents=True, exist_ok=True)

    def tile(pair: tuple[int, int]):
        x, y = pair
        path = tile_root / f"{x}_{y}.jpg"
        receipt_path = path.with_suffix(".json")
        url = f"{ORIGIN}/maps/{tileset}/{TILE_ZOOM}/{x}/{y}.jpg"
        receipt = _read_json(receipt_path)
        try:
            content = path.read_bytes()
            _validate_tile(content)
            if (receipt.get("url") != url or not receipt.get("fetched_at")
                    or receipt.get("sha256") != hashlib.sha256(content).hexdigest()):
                raise ValueError("Tile cache has no matching source receipt")
        except (OSError, ValueError):
            content = fetch(url)
            _validate_tile(content)
            receipt = {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                       "sha256": hashlib.sha256(content).hexdigest(), "width": SOURCE_TILE_SIZE, "height": SOURCE_TILE_SIZE}
            _publish_files({path: content, receipt_path: _json_bytes(receipt)})
        return x, y, content, receipt

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        tiles = list(pool.map(tile, [(x, y) for x in range(TILE_AXIS) for y in range(TILE_AXIS)]))
    expected = {(x, y) for x in range(TILE_AXIS) for y in range(TILE_AXIS)}
    if len(tiles) != len(expected) or {(x, y) for x, y, _, _ in tiles} != expected:
        raise ValueError("Background tile set is incomplete or duplicated")
    size = TILE_AXIS * TILE_SIZE
    background = Image.new("RGB", (size, size))
    for x, y, content, _ in tiles:
        with Image.open(BytesIO(content)) as image:
            background.paste(image.convert("RGB").resize((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS),
                             (x * TILE_SIZE, y * TILE_SIZE))
    output = BytesIO()
    background.save(output, format="JPEG", quality=91)
    content = output.getvalue()
    with Image.open(BytesIO(content)) as complete:
        if complete.size != (size, size):
            raise ValueError("Stitched background dimensions are invalid")
        complete.load()
    metadata = {
        "tileset": tileset, "zoom": TILE_ZOOM, "source_tile_size": SOURCE_TILE_SIZE, "output_tile_size": TILE_SIZE,
        "url_pattern": f"{ORIGIN}/maps/{tileset}/{TILE_ZOOM}/{{x}}/{{y}}.jpg",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": max(receipt["fetched_at"] for _, _, _, receipt in tiles),
        "width": size, "height": size, "tiles_loaded": len(tiles), "tiles_expected": len(expected),
        "sha256": hashlib.sha256(content).hexdigest(),
        "tiles": [{"x": x, "y": y, **receipt} for x, y, _, receipt in tiles],
    }
    return content, metadata


def refresh(map_key: str, *, background_only: bool = False, cache_root: Path | None = None) -> dict:
    cache = Path(cache_root) if cache_root is not None else CACHE
    cache.mkdir(parents=True, exist_ok=True)
    layers = None if background_only else fetch_layers(map_key)
    background, metadata = _build_background(map_key, cache)
    provenance_path = cache / f"{map_key}_public_provenance.json"
    provenance = _read_json(provenance_path)
    provenance.update({"source": "Icarus Intel", "url": f"{ORIGIN}/map/layers/{map_key}",
                       "background_url_pattern": metadata["url_pattern"], "background": metadata,
                       "private_local_cache": True,
                       "notice": "Community map catalog: potential positions, not proof of a live world resource. Attribution retained; game assets are not distributed."})
    files = {cache / f"{map_key}.jpg": background}
    if layers is not None:
        provenance["fetched_at"] = datetime.now(timezone.utc).isoformat()
        provenance["counts"] = {key: len(value) for key, value in layers.items() if isinstance(value, list)}
        files[cache / f"{map_key}_public.json"] = _json_bytes(layers)
    files[provenance_path] = _json_bytes(provenance)
    _publish_files(files)
    print(map_key, f"background={metadata['tileset']} {metadata['width']}x{metadata['height']}", flush=True)
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="更新 Icarus Intel 社群目錄與完整底圖。")
    parser.add_argument("--map", dest="maps", choices=MAP_KEYS, action="append", help="僅更新指定地圖；可重複指定。")
    parser.add_argument("--background-only", action="store_true", help="只更新底圖與其來源紀錄，不重抓資源清單。")
    parser.add_argument("--cache-root", type=Path, help="指定輸出／圖塊快取目錄。")
    arguments = parser.parse_args(argv)
    failed = False
    for key in arguments.maps or MAP_KEYS:
        try:
            refresh(key, background_only=arguments.background_only, cache_root=arguments.cache_root)
        except Exception as error:
            failed = True
            print(f"{key}: refresh failed: {error}", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
