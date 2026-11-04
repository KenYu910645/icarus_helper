"""Real XP intervals, honest unknown gauges, and clickable native animal cards."""
from __future__ import annotations

from copy import deepcopy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from icarus_helper.animal_icons import animal_pixmap
from icarus_helper.animal_widgets import AnimalCard, AnimalGauge
from icarus_helper.animals import _experience_progress, load_animal_catalog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def sample_animal():
    catalog = load_animal_catalog()
    return {"id": "stable-one", "name": "阿鳥", "species": "恐鳥", "species_id": "Mount_Moa",
            "owner_name": "Owner甲", "level": 31, "position": [1, 2, 3], "distance_m": 0,
            "stats": [{"id": "health", "value": 180, "maximum": None},
                      {"id": "food", "value": 0, "maximum": None},
                      {"id": "experience", "value": 162560, "maximum": None}],
            "experience_progress": _experience_progress({"Experience": 162560}, catalog, catalog["species"]["Mount_Moa"])}


def render(app, widget):
    widget.show()
    app.processEvents()
    return widget.grab().toImage()


def test_xp_gauge_uses_the_verified_current_level_interval(app):
    animal = sample_animal()
    progress = animal["experience_progress"]
    assert progress["value"] == 5560 and progress["maximum"] == 17800
    gauge = AnimalGauge(animal["stats"][-1], progress)
    gauge.resize(240, 27)
    image = render(app, gauge)
    assert gauge.ratio_known and gauge.display_text() == "5,560 / 17,800"
    assert "162,560" in gauge.toolTip() and "next level 32" in gauge.toolTip()
    # A 31.2% interval fills the first third, independently of cumulative XP.
    assert image.pixelColor(45, 7) != image.pixelColor(180, 7)
    assert image.pixelColor(120, 7) == image.pixelColor(180, 7)
    gauge.close()


@pytest.mark.parametrize("xp,expected,capped", [(944, "0 / 888", False), (1150000, "MAX", True)])
def test_xp_boundary_shows_empty_next_level_or_verified_maximum(app, xp, expected, capped):
    catalog = load_animal_catalog()
    progress = _experience_progress({"Experience": xp}, catalog, catalog["species"]["Mount_Moa"])
    gauge = AnimalGauge({"id": "experience", "value": xp}, progress)
    gauge.resize(240, 27)
    image = render(app, gauge)
    assert gauge.display_text() == expected and gauge.capped is capped
    if not capped:
        assert image.pixelColor(45, 7) == image.pixelColor(180, 7)
    else:
        empty = AnimalGauge({"id": "experience", "value": 0, "maximum": 100})
        empty.resize(240, 27)
        assert image.pixelColor(180, 7) != render(app, empty).pixelColor(180, 7)
        empty.close()
    gauge.close()


@pytest.mark.parametrize("value,maximum,expected", [(0, 100, "0 / 100"), (0, None, "0 / ?"),
                                                        (None, 100, "? / ?"), (None, None, "? / ?")])
def test_zero_unknown_value_and_unknown_capacity_are_distinct(app, value, maximum, expected):
    gauge = AnimalGauge({"id": "food", "value": value, "maximum": maximum})
    assert gauge.value is value and gauge.maximum is maximum
    assert gauge.display_text() == expected
    assert gauge.ratio_known is (value == 0 and maximum == 100)
    assert "%" not in gauge.display_text()
    if value is None:
        assert "cannot be treated as zero" in gauge.toolTip()
    elif maximum is None:
        assert "hatching does not represent a percentage" in gauge.toolTip()
    gauge.close()


def test_unknown_hatching_does_not_render_like_confirmed_empty_bar(app):
    unknown = AnimalGauge({"id": "health", "value": 180, "maximum": None})
    zero = AnimalGauge({"id": "health", "value": 0, "maximum": 100})
    for gauge in (unknown, zero):
        gauge.resize(240, 27)
    unknown_image, zero_image = render(app, unknown), render(app, zero)
    # Ignore the centered numbers; compare the track's first quarter.
    differences = sum(unknown_image.pixelColor(x, y) != zero_image.pixelColor(x, y)
                      for x in range(28, 70) for y in range(6, 21))
    assert differences > 40
    unknown.close()
    zero.close()


def test_card_preserves_unknowns_and_uses_the_exact_species_portrait(app):
    animal = sample_animal()
    before = deepcopy(animal)
    card = AnimalCard(animal, "#55bbaa", "home")
    card.resize(300, 230)
    render(app, card)
    portraits = card.findChildren(QLabel, "animalPortrait")
    assert len(portraits) == 1
    assert portraits[0].pixmap().toImage() == animal_pixmap("Mount_Moa", 62).toImage()
    assert card.gauges["health"].display_text() == "180 / ?"
    assert card.gauges["food"].display_text() == "0 / ?"
    assert card.gauges["oxygen"].display_text() == "? / ?"
    assert card.gauges["experience"].display_text() == "5,560 / 17,800"
    assert animal == before
    card.close()


def test_card_mouse_keyboard_and_locate_button_emit_stable_identity(app):
    card = AnimalCard(sample_animal(), "#55bbaa", "home")
    card.resize(300, 230)
    render(app, card)
    selected, locate = QSignalSpy(card.selected), QSignalSpy(card.locate)
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(6, 30))
    assert selected.count() == 1 and selected.at(0) == ["stable-one"]
    QTest.keyClick(card, Qt.Key.Key_Return)
    QTest.keyClick(card, Qt.Key.Key_Space)
    assert selected.count() == 3
    QTest.mouseClick(card.findChild(QPushButton), Qt.MouseButton.LeftButton)
    assert locate.count() == 1 and locate.at(0) == ["stable-one"]
    QTest.mouseDClick(card, Qt.MouseButton.LeftButton, pos=QPoint(6, 30))
    assert locate.count() == 2
    card.close()


def test_card_selection_changes_visible_background_and_keeps_owner_accent(app):
    card = AnimalCard(sample_animal(), "#55bbaa", "home")
    card.resize(300, 230)
    normal = render(app, card)
    card.set_selected(True)
    selected = render(app, card)
    assert normal.pixelColor(6, 30) != selected.pixelColor(6, 30)
    assert "border-left:3px solid #55bbaa" in card.styleSheet()
    card.set_selected(False)
    assert render(app, card).pixelColor(6, 30) == normal.pixelColor(6, 30)
    card.close()


def test_card_without_coordinates_disables_locate_button(app):
    animal = sample_animal()
    animal["position"] = None
    card = AnimalCard(animal, "#55bbaa", "unknown")
    assert not card.findChild(QPushButton).isEnabled()
    card.close()
