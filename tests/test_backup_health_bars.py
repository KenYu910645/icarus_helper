"""Rendering tests distinguish zero, missing and unknown capacity honestly."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from icarus_helper.backup_health_bars import health_bar_image, health_bar_state


def test_positive_values_never_gain_an_invented_ratio_or_maximum():
    for value in (1, 585, 1000, 100000):
        state = health_bar_state({"health": value, "maximum": 1000})
        assert state["state"] == "saved" and state["value"] == value
        assert state["ratio"] is None and state["maximum"] is None
        assert state["text"].endswith(" / ?") and "%" not in state["text"]


def test_missing_values_stay_distinct_from_empty_known_zero():
    zero = health_bar_state({"health": 0, "alive": False})
    assert zero["state"] == "empty" and zero["text"] == "0 / ?"
    for value in (None, float("nan"), float("inf"), -1, True, "0"):
        missing = health_bar_state({"health": value})
        assert missing["state"] == "unknown" and missing["text"] == "? / ?"


def test_unknown_capacity_is_striped_and_zero_is_empty_without_normalized_fill():
    _app = QApplication.instance() or QApplication([])
    saved = health_bar_image({"health": 585}, 240)
    missing = health_bar_image({"health": None}, 240)
    zero = health_bar_image({"health": 0, "alive": False}, 240)
    assert saved.size() == missing.size() == zero.size()
    # The left track does not overlap numeric labels; stripe variation must
    # exist for unknown capacity but not for a known zero/empty track.
    def interior_colors(image):
        return {image.pixelColor(x, y).rgba() for x in range(12, 56) for y in range(6, 20)}
    assert len(interior_colors(saved)) > 1
    assert len(interior_colors(missing)) > 1
    assert len(interior_colors(zero)) == 1
    assert saved != zero and missing != saved
