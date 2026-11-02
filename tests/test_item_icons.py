"""Native thumbnail decoding rejects wrong offsets instead of guessing pixels."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from icarus_helper import item_icons

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_item_icons import decode_texture


def sample():
    # Single red opaque DXT5 block; header and first mip offsets are independent.
    header = b"header" * 20
    pixel_format = b"PF_DXT5\0"
    platform = struct.pack("<4I", 4, 4, 1, len(pixel_format)) + pixel_format
    prefix = b"serialized properties" + platform
    offset = len(header) + len(prefix) + 32
    mip = struct.pack("<6Iq", 0, 1, 1, 0x48, 16, 16, offset)
    payload = bytes([255, 0]) + bytes(6) + struct.pack("<HHI", 0xF800, 0x07E0, 0)
    return header, prefix + mip + payload + struct.pack("<3I", 4, 4, 1), len(prefix)


def test_cooked_top_mip_offset_size_and_alpha_are_decoded_exactly():
    header, exported, _ = sample()
    image, metadata = decode_texture(header, exported)
    assert image.size == (4, 4)
    assert metadata["pixel_format"] == "PF_DXT5"
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert image.getpixel((3, 3)) == (255, 0, 0, 255)


@pytest.mark.parametrize("field", ["offset", "dimensions", "length"])
def test_wrong_bulk_boundaries_are_rejected(field):
    header, exported, mip = sample()
    changed = bytearray(exported)
    if field == "offset":
        struct.pack_into("<q", changed, mip + 24, 0)
    elif field == "dimensions":
        struct.pack_into("<I", changed, len(changed) - 12, 8)
    else:
        struct.pack_into("<I", changed, mip + 20, 15)
    with pytest.raises(ValueError):
        decode_texture(header, changed)


def test_missing_item_and_unsafe_catalog_path_use_visible_fallback(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(item_icons, "ROOT", tmp_path)
    monkeypatch.setattr(item_icons, "_item_paths", lambda: {"bad": "../private.png"})
    item_icons._pixmap.cache_clear()
    assert item_icons.item_icon_path("bad") is None
    assert not item_icons.has_item_icon("not-in-catalog")
    fallback = item_icons.item_pixmap("not-in-catalog", 32)
    assert not fallback.isNull() and fallback.width() == 32
    assert fallback.toImage().pixelColor(16, 16).alpha() > 0
    item_icons._pixmap.cache_clear()


def test_native_icon_resolution_uses_case_insensitive_identifier(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    filename = "a" * 24 + ".png"
    target = tmp_path / "assets/items" / filename
    target.parent.mkdir(parents=True)
    from PySide6.QtGui import QColor, QPixmap
    picture = QPixmap(128, 128)
    picture.fill(QColor("#ff9900"))
    assert picture.save(str(target))
    monkeypatch.setattr(item_icons, "ROOT", tmp_path)
    monkeypatch.setattr(item_icons, "_item_paths", lambda: {"gold_ore": filename})
    item_icons._pixmap.cache_clear()
    assert item_icons.has_item_icon("Gold_Ore")
    assert item_icons.item_icon_path("GOLD_ORE") == target
    assert item_icons.item_pixmap("Gold_Ore", 32).size().width() == 32
    assert not item_icons.item_icon("Gold_Ore").isNull()
    item_icons._pixmap.cache_clear()
