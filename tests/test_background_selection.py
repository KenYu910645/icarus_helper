"""Background revisions cannot be hidden by older generated-resource caches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from icarus_helper import catalog


@pytest.fixture
def atlas_roots(tmp_path, monkeypatch):
    local = tmp_path / "快取" / catalog.CATALOG_BUILD
    bundled = tmp_path / "封裝" / catalog.CATALOG_BUILD
    local.mkdir(parents=True)
    bundled.mkdir(parents=True)
    monkeypatch.setattr(catalog, "CACHE_ROOT", local.parent)
    monkeypatch.setattr(catalog, "_bundled_cache", lambda: bundled)
    monkeypatch.setattr(catalog, "installed_game_build", lambda: catalog.CATALOG_BUILD)
    # Keep a newer ordinary-node catalog locally. Background selection must
    # never switch this catalog just to obtain the bundled terrain picture.
    for root, date, node_id in ((local, "2026-11-02", "local"), (bundled, "2026-10-31", "bundled")):
        (root / "prometheus_nodes.json").write_text(json.dumps({
            "game_build": catalog.CATALOG_BUILD, "schema_version": 4,
            "generated_at": date, "points": [], "tiles_loaded": 64,
            "source_packages": [node_id],
        }), encoding="utf-8")
    return local, bundled


def image(root: Path, key="prometheus", content=b"terrain", *, full=False, fetched_at="2026-11-02T02:00:00Z"):
    (root / f"{key}.jpg").write_bytes(content)
    metadata = {"source": "Icarus Intel", "fetched_at": "2026-10-31T00:00:00Z",
                "background_url_pattern": f"https://example.invalid/{key}/3/{{x}}/{{y}}.jpg"}
    if full:
        metadata["background"] = {
            "tileset": "prometheus2", "sha256": hashlib.sha256(content).hexdigest(),
            "fetched_at": fetched_at,
            "url_pattern": "https://example.invalid/prometheus2/3/{x}/{y}.jpg",
        }
    (root / f"{key}_public_provenance.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_bundled_full_image_beats_old_local_without_changing_resource_atlas(atlas_roots):
    local, bundled = atlas_roots
    image(local, content=b"old black region")
    image(bundled, content=b"full south terrain", full=True)
    result = catalog.load_catalog("Prometheus")
    assert catalog._cache() == local
    assert result["background_path"] == str(bundled / "prometheus.jpg")
    assert result["coverage"]["background"]["includes_null_sector"] is True
    assert result["coverage"]["background"]["hash_verified"] is True
    assert result["sources"][1]["source_packages"] == ["local"]


def test_newer_verified_complete_local_background_is_selected(atlas_roots):
    local, bundled = atlas_roots
    image(local, content=b"new complete", full=True, fetched_at="2026-11-02T11:00:00+08:00")
    image(bundled, content=b"older complete", full=True, fetched_at="2026-11-02T02:30:00Z")
    info = catalog._background_info("prometheus")
    assert info["path"] == str(local / "prometheus.jpg")
    assert info["fetched_at"] == "2026-11-02T11:00:00+08:00"


def test_new_complete_local_image_is_available_when_node_catalog_uses_bundle(atlas_roots):
    local, bundled = atlas_roots
    (local / "prometheus_nodes.json").unlink()
    image(local, content=b"full locally refreshed", full=True)
    image(bundled, content=b"old black region")
    assert catalog._cache() == bundled
    assert catalog._background_info("prometheus")["path"] == str(local / "prometheus.jpg")


def test_hash_mismatch_full_image_falls_back_to_verified_complete_image(atlas_roots):
    local, bundled = atlas_roots
    image(local, content=b"receipt original", full=True, fetched_at="2026-11-03T00:00:00Z")
    (local / "prometheus.jpg").write_bytes(b"corrupt replacement")
    image(bundled, content=b"valid complete", full=True)
    info = catalog._background_info("prometheus")
    assert info["path"] == str(bundled / "prometheus.jpg")
    assert info["hash_verified"] is True


def test_unverified_full_claim_falls_back_to_old_image(atlas_roots):
    local, bundled = atlas_roots
    image(local, content=b"old standard")
    image(bundled, content=b"unverified complete", full=True)
    receipt = bundled / "prometheus_public_provenance.json"
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data["background"].pop("sha256")
    receipt.write_text(json.dumps(data), encoding="utf-8")
    info = catalog._background_info("prometheus")
    assert info["path"] == str(local / "prometheus.jpg")
    assert info["includes_null_sector"] is False
    assert info["hash_verified"] is False


def test_all_invalid_full_images_return_no_background(atlas_roots):
    local, _bundled = atlas_roots
    image(local, full=True)
    (local / "prometheus.jpg").write_bytes(b"corrupt")
    assert catalog._background_info("prometheus") == {}


def test_olympus_never_uses_prometheus_full_image(atlas_roots):
    local, bundled = atlas_roots
    image(local, key="olympus", content=b"Olympus terrain")
    image(bundled, content=b"Prometheus complete", full=True)
    info = catalog._background_info("olympus")
    assert info["path"] == str(local / "olympus.jpg")
    assert info["includes_null_sector"] is False
    assert catalog._background_info("styx") == {}
