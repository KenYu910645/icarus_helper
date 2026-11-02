"""Complete, source-isolated atlas updates retain the previous valid background."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from unittest.mock import patch

from PIL import Image
import pytest

from tools import refresh_atlas_sources as atlas


def jpeg(color=(70, 110, 150), size=(512, 512)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG")
    return output.getvalue()


def tile_fetch(url):
    match = re.fullmatch(r"https://www\.icarusintel\.com/maps/prometheus2/3/(\d)/(\d)\.jpg", url)
    assert match, url
    x, y = map(int, match.groups())
    return jpeg((30 + 20 * x, 30 + 20 * y, 100))


def seed_previous(root: Path):
    files = {
        "prometheus.jpg": jpeg((5, 5, 5), (2048, 2048)),
        "prometheus_public.json": b'{"ores":[{"id":"keep-resource"}]}',
        "prometheus_public_provenance.json": json.dumps({
            "fetched_at": "2026-10-31T15:20:27+00:00", "counts": {"ores": 1},
            "background_url_pattern": "https://www.icarusintel.com/maps/prometheus/3/{x}/{y}.jpg",
        }).encode(),
    }
    for name, content in files.items():
        (root / name).write_bytes(content)
    return files


def test_background_only_uses_null_sector_tiles_and_preserves_resources(tmp_path):
    previous = seed_previous(tmp_path)
    old_tiles = tmp_path / "image_tiles/prometheus"
    old_tiles.mkdir(parents=True)
    (old_tiles / "0_0.jpg").write_bytes(b"old masked cache must remain unused")
    with patch.object(atlas, "fetch", side_effect=tile_fetch) as fetch, patch.object(atlas, "fetch_layers") as layers:
        provenance = atlas.refresh("prometheus", background_only=True, cache_root=tmp_path)
    layers.assert_not_called()
    assert fetch.call_count == 64
    assert (tmp_path / "prometheus_public.json").read_bytes() == previous["prometheus_public.json"]
    assert (old_tiles / "0_0.jpg").read_bytes() == b"old masked cache must remain unused"
    assert provenance["fetched_at"] == "2026-10-31T15:20:27+00:00"
    assert provenance["counts"] == {"ores": 1}
    background = provenance["background"]
    assert background["tileset"] == "prometheus2" and background["zoom"] == 3
    assert background["source_tile_size"] == 512 and background["output_tile_size"] == 256
    assert background["tiles_loaded"] == background["tiles_expected"] == 64
    assert background["width"] == background["height"] == 2048
    assert background["sha256"] == hashlib.sha256((tmp_path / "prometheus.jpg").read_bytes()).hexdigest()
    assert background["fetched_at"].endswith("+00:00") and background["generated_at"].endswith("+00:00")
    assert len(background["tiles"]) == 64
    assert len(list((tmp_path / "image_tiles/prometheus2/3").glob("*.jpg"))) == 64
    with Image.open(tmp_path / "prometheus.jpg") as image:
        assert image.size == (2048, 2048)
        # Assert x/y placement, not just a complete-looking output file.
        for x, y in ((0, 0), (7, 0), (0, 7), (7, 7), (2, 6)):
            actual = image.getpixel((x * 256 + 128, y * 256 + 128))
            assert all(abs(a - b) <= 3 for a, b in zip(actual, (30 + 20 * x, 30 + 20 * y, 100)))


@pytest.mark.parametrize("bad_tile", [b"not an image", jpeg(size=(256, 256))])
def test_bad_download_keeps_old_background_provenance_and_resource_bytes(tmp_path, bad_tile):
    previous = seed_previous(tmp_path)

    def fetch(url):
        return bad_tile if url.endswith("/7/7.jpg") else tile_fetch(url)

    with patch.object(atlas, "fetch", side_effect=fetch), pytest.raises((OSError, ValueError)):
        atlas.refresh("prometheus", background_only=True, cache_root=tmp_path)
    for name, content in previous.items():
        assert (tmp_path / name).read_bytes() == content


def test_cached_tiles_require_matching_source_and_hash_receipts(tmp_path):
    with patch.object(atlas, "fetch", side_effect=tile_fetch):
        atlas.refresh("prometheus", background_only=True, cache_root=tmp_path)
    with patch.object(atlas, "fetch", side_effect=AssertionError("valid source cache should be reused")):
        atlas.refresh("prometheus", background_only=True, cache_root=tmp_path)
    tile = tmp_path / "image_tiles/prometheus2/3/6_5.jpg"
    tile.write_bytes(jpeg((5, 5, 5)))  # Valid image, but does not match the recorded hash.
    with patch.object(atlas, "fetch", side_effect=tile_fetch) as fetch:
        atlas.refresh("prometheus", background_only=True, cache_root=tmp_path)
    fetch.assert_called_once_with("https://www.icarusintel.com/maps/prometheus2/3/6/5.jpg")


def test_publication_failure_rolls_back_background_with_old_metadata(tmp_path):
    previous = seed_previous(tmp_path)
    original_replace = atlas.os.replace
    failure_used = False

    def replace(source, target):
        nonlocal failure_used
        if Path(target).name == "prometheus_public_provenance.json" and not failure_used:
            failure_used = True
            raise PermissionError("simulated locked provenance")
        return original_replace(source, target)

    with patch.object(atlas, "fetch", side_effect=tile_fetch), patch.object(atlas.os, "replace", side_effect=replace):
        with pytest.raises(PermissionError):
            atlas.refresh("prometheus", background_only=True, cache_root=tmp_path)
    assert failure_used
    for name, content in previous.items():
        assert (tmp_path / name).read_bytes() == content
    assert not list(tmp_path.glob(".*.part"))


def test_cli_selects_only_requested_background_and_reports_failure(tmp_path):
    with patch.object(atlas, "refresh") as refresh:
        assert atlas.main(["--map", "prometheus", "--background-only", "--cache-root", str(tmp_path)]) == 0
    refresh.assert_called_once_with("prometheus", background_only=True, cache_root=tmp_path)
    with patch.object(atlas, "refresh", side_effect=OSError("unavailable")):
        assert atlas.main(["--map", "prometheus", "--background-only"]) == 1
