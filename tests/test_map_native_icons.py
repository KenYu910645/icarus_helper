"""Map minerals reuse verified raw-item thumbnails without changing status UI."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from icarus_helper import map_icons
from icarus_helper.inventory import load_item_catalog
from icarus_helper.item_icons import has_item_icon, item_icon_path, item_pixmap


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    map_icons.marker_pixmap.cache_clear()
    yield instance
    map_icons.marker_pixmap.cache_clear()


def test_all_current_deep_minerals_resolve_to_native_raw_item_art():
    # ResourceDTKey values from the validated Prometheus snapshot, including
    # Silicon (deposit) -> Silica (collectible), and Aluminium -> Bauxite.
    minerals = ["Aluminium", "Iron", "Gold", "Silicon", "Platinum", "Scoria",
                "Sulfur", "Titanium", "Coal", "Stone", "Copper", "Clay",
                "Super_Cooled_Ice", "Obsidian", "Salt", "Oxite", "Frozen_Wood", "Limestone"]
    catalog = load_item_catalog()["items"]
    for mineral in minerals:
        ident = map_icons.mineral_item_id(mineral)
        assert ident in catalog and has_item_icon(ident), mineral
        assert catalog[ident]["form"] != "ingot", mineral
    assert map_icons.mineral_item_id("Aluminium") == "Bauxite"
    assert catalog["Aluminium"]["form"] == "ingot"
    assert map_icons.mineral_item_id("Silicon") == "Silica"
    assert map_icons.mineral_item_id("Gold_Dense") == map_icons.mineral_item_id("Gold ore") == "Gold_Ore"


def test_native_art_is_drawn_for_both_ore_and_deep_markers_with_status_badges(app, monkeypatch):
    calls = []
    sentinel = QColor("#e437c9")

    def picture(ident, size=32):
        calls.append(ident)
        pixmap = QPixmap(size, size)
        pixmap.fill(sentinel)
        return pixmap

    monkeypatch.setattr(map_icons, "has_item_icon", lambda _ident: True)
    monkeypatch.setattr(map_icons, "item_pixmap", picture)
    for kind in ("ore", "deep_deposit"):
        plain = map_icons.marker_pixmap(kind, "Gold", 32).toImage()
        status = map_icons.marker_pixmap(kind, "Gold", 32, candidate=True, drilled=True).toImage()
        assert plain.pixelColor(16, 16) == sentinel
        assert status.pixelColor(16, 16) == sentinel
        assert status.pixelColor(5, 5) != plain.pixelColor(5, 5)
        assert status.pixelColor(26, 26) != plain.pixelColor(26, 26)
    assert calls == ["Gold_Ore"] * 4


def test_unknown_or_missing_native_icon_is_a_question_not_a_guessed_rock(app, monkeypatch):
    assert map_icons.mineral_item_id("Raw_Iron_Wood") is None
    assert map_icons.mineral_item_id("New_Unknown_Mineral") is None
    monkeypatch.setattr(map_icons, "has_item_icon", lambda _ident: False)
    question = map_icons.marker_pixmap("deep", "Unknown", 32)
    missing = map_icons.marker_pixmap("deep", "Gold", 32)
    assert not question.isNull() and not missing.isNull()
    # The missing artwork still uses a white question, while its known-mineral
    # frame/name can remain available; it never invokes an old rock sketch.
    monkeypatch.setitem(map_icons._PAINTERS, "rock", lambda *_args: pytest.fail("Custom mineral sketch used"))
    map_icons.marker_pixmap("ore", "Copper", 32)


def test_red_raw_exotic_and_uranium_keep_distinct_exact_native_items():
    assert map_icons.mineral_item_id("Exotic_Red_Raw") == "Exotic_Red_Raw"
    assert map_icons.mineral_item_id("Exotic_Red") == "Exotic_Red"
    assert item_icon_path("Exotic_Red_Raw") != item_icon_path("Exotic_Red")
    assert map_icons.mineral_item_id("Exotic_Raw_Uranium") == "Uranium_Raw"
    assert map_icons.mineral_item_id("Uranium") != "Raw_Iron_Wood"


def test_exotic_category_and_biome_candidates_show_native_art_with_small_question(app):
    category = map_icons.marker_pixmap("exotic", "", 32).toImage()
    native = item_pixmap("MetaResource", 26).toImage()
    opaque_samples = [(x, y) for x in range(10, 22) for y in range(10, 22)
                      if native.pixelColor(x - 3, y - 3).alpha() == 255]
    assert len(opaque_samples) > 20
    assert all(category.pixelColor(x, y) == native.pixelColor(x - 3, y - 3) for x, y in opaque_samples)
    for biome in ("Conifer", "Arctic", "Plant", "Volcanic"):
        assert map_icons.mineral_item_id(biome) is None
        assert map_icons.marker_item_id("exotic", biome, candidate=True) == "MetaResource"
        candidate = map_icons.marker_pixmap("exotic", biome, 32, candidate=True).toImage()
        assert all(candidate.pixelColor(x, y) == category.pixelColor(x, y) for x in range(12, 22) for y in range(12, 22))
        assert candidate.pixelColor(5, 5) != category.pixelColor(5, 5)
        assert candidate != category  # Question badge and dashed frame remain.


def test_exact_exotic_red_and_uranium_art_is_not_replaced_by_category_art(app):
    expected = {"Exotic": "MetaResource", "Exotic_Red_Raw": "Exotic_Red_Raw",
                "Exotic_Red": "Exotic_Red", "Exotic_Raw_Uranium": "Uranium_Raw"}
    images = []
    for resource, ident in expected.items():
        assert map_icons.marker_item_id("exotic", resource) == ident
        assert map_icons.marker_item_id("exotic", resource, candidate=True) == ident
        images.append(map_icons.marker_pixmap("exotic", resource, 32, candidate=True).toImage())
    assert all(left != right for index, left in enumerate(images) for right in images[index + 1:])


def test_confirmed_unknown_exotic_and_unknown_deep_stay_questions(app, monkeypatch):
    monkeypatch.setattr(map_icons, "item_pixmap", lambda *_args, **_kwargs: pytest.fail("Unknown resource guessed a native image"))
    for kind, resource, candidate in (("exotic", "Unknown_New_Subtype", False),
                                      ("exotic", "Conifer", False),
                                      ("deep", "Unknown_New_Mineral", True)):
        assert map_icons.marker_item_id(kind, resource, candidate=candidate) is None
        assert not map_icons.marker_pixmap(kind, resource, 32, candidate=candidate).isNull()
