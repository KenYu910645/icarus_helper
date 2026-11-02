"""Cache public map metadata/backgrounds for personal local use; no runtime network."""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image
from catalog_inventory import CACHE


ORIGIN = "https://www.icarusintel.com"


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


def refresh(map_key: str) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    layers = fetch_layers(map_key)
    (CACHE / f"{map_key}_public.json").write_text(json.dumps(layers, ensure_ascii=False), encoding="utf-8")
    tile_root = CACHE / "image_tiles" / map_key
    tile_root.mkdir(parents=True, exist_ok=True)

    def tile(pair: tuple[int, int]):
        x, y = pair
        path = tile_root / f"{x}_{y}.jpg"
        if not path.exists():
            content = fetch(f"{ORIGIN}/maps/{map_key}/3/{x}/{y}.jpg")
            Image.open(BytesIO(content)).verify()
            path.write_bytes(content)
        return x, y, path

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        tiles = list(pool.map(tile, [(x, y) for x in range(8) for y in range(8)]))
    background = Image.new("RGB", (2048, 2048), "#182b36")
    for x, y, path in tiles:
        with Image.open(path) as image:
            background.paste(image.resize((256, 256)), (x * 256, y * 256))
    background.save(CACHE / f"{map_key}.jpg", quality=91)
    provenance = {
        "source": "Icarus Intel", "url": f"{ORIGIN}/map/layers/{map_key}",
        "background_url_pattern": f"{ORIGIN}/maps/{map_key}/3/{{x}}/{{y}}.jpg",
        "fetched_at": datetime.now(timezone.utc).isoformat(), "private_local_cache": True,
        "counts": {key: len(value) for key, value in layers.items() if isinstance(value, list)},
        "notice": "Community map catalog: potential positions, not proof of a live world resource. Attribution retained; game assets are not distributed.",
    }
    (CACHE / f"{map_key}_public_provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(map_key, provenance["counts"], flush=True)


if __name__ == "__main__":
    for key in ("prometheus", "olympus", "styx", "elysium"):
        try:
            refresh(key)
        except Exception as error:
            print(f"{key}: refresh failed: {error}", flush=True)
