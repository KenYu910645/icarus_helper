"""Native animal portrait provenance, complete decoding, and conservative fallback."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from icarus_helper import animal_icons


def test_native_portraits_decode_and_match_manifest_hashes():
    app = QApplication.instance() or QApplication([])
    catalog = animal_icons.load_animal_icon_catalog()
    assert catalog["game_build"] == "26313227"
    assert catalog["coverage"]["species_definitions"] == len(catalog["species"]) + len(catalog["missing"])
    assert set(catalog["species"]).isdisjoint(catalog["missing"])
    for source in catalog["sources"]:
        assert not Path(source["path"]).is_absolute()
        assert "Users" not in source["path"] and "PlayerData" not in source["path"]
        assert re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
    from PySide6.QtGui import QImage
    for filename, metadata in catalog["textures"].items():
        path = animal_icons.ROOT / "assets/animals" / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["png_sha256"]
        picture = QImage(str(path))
        assert not picture.isNull()
        assert (picture.width(), picture.height()) == (metadata["thumbnail_width"], metadata["thumbnail_height"])
        assert max(picture.width(), picture.height()) <= 128
        assert any(picture.pixelColor(x, y).alpha() > 0 for y in range(picture.height()) for x in range(picture.width()))


def test_current_mount_species_have_exact_distinct_native_images():
    app = QApplication.instance() or QApplication([])
    species = animal_icons.load_animal_icon_catalog()["species"]
    ids = ["Mount_Horse", "Mount_Moa", "Mount_Buffalo", "Cow", "Mount_SwampBird", "Mount_Arctic_Moa"]
    assert len({species[key]["file"] for key in ids}) == len(ids)
    assert species["Mount_Arctic_Moa"]["bestiary_group"] == "Arctic_Moa"
    for key in ids:
        assert animal_icons.has_animal_icon(key)
        assert not animal_icons.animal_pixmap(key, 48).isNull()
        assert not animal_icons.animal_icon(key).isNull()
        assert animal_icons.animal_icon_path(key.upper()) == animal_icons.animal_icon_path(key)


def test_unmapped_species_and_unsafe_paths_never_infer_a_related_animal(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(animal_icons, "ROOT", tmp_path)
    monkeypatch.setattr(animal_icons, "_species_paths", lambda: {"bad": "../private.png"})
    animal_icons._pixmap.cache_clear()
    assert animal_icons.animal_icon_path("bad") is None
    assert not animal_icons.has_animal_icon("New_Moa_Variant")
    assert not animal_icons.animal_pixmap("New_Moa_Variant", 32).isNull()
    animal_icons._pixmap.cache_clear()
