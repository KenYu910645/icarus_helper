"""Rendered English surfaces must not translate names or erase uncertainty."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QComboBox, QLabel, QLineEdit, QListWidget,
    QScrollArea, QTableWidget, QTabWidget, QTextBrowser, QWidget,
)

from icarus_helper import catalog, saves
from icarus_helper.controller import SnapshotController
from icarus_helper.inventory import load_item_catalog
from icarus_helper.map_icons import mineral_item_id, mineral_style
from icarus_helper.map_widget import WorldMap, drill_stock_label
from icarus_helper.missions import load_catalog as load_missions, mission_html
from icarus_helper.models import Resource
from icarus_helper.settings import Settings
from icarus_helper.shared_profile import SHARED_SOURCE, build_shared_payload
from icarus_helper.ui import MainWindow, STYLESHEET, remaining_text


# CJK text and prose punctuation are forbidden in generated English surfaces.
# Symbols used as controls, such as full-width plus/minus, are not prose.
NON_ENGLISH = re.compile(r"[\u3400-\u9fff，。、；：！？「」『』（）／]")


def assert_english(text, *custom_names):
    text = str(text or "")
    for name in custom_names:
        text = text.replace(name, "<user name>")
    assert not NON_ENGLISH.search(text), text


def widget_strings(root):
    for widget in (root, *root.findChildren(QWidget)):
        yield widget.accessibleName()
        yield widget.toolTip()
        yield widget.windowTitle()
        if isinstance(widget, (QLabel, QAbstractButton)):
            yield widget.text()
        if isinstance(widget, QLineEdit):
            yield widget.placeholderText()
        if isinstance(widget, QTextBrowser):
            yield widget.toPlainText()
        if isinstance(widget, QTabWidget):
            yield from (widget.tabText(i) for i in range(widget.count()))
        if isinstance(widget, QComboBox):
            yield from (widget.itemText(i) for i in range(widget.count()))
        if isinstance(widget, QListWidget):
            yield from (widget.item(i).text() for i in range(widget.count()))
        if isinstance(widget, QTableWidget):
            yield from (widget.horizontalHeaderItem(i).text()
                        for i in range(widget.columnCount())
                        if widget.horizontalHeaderItem(i))


@pytest.fixture
def english_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    previous_style, previous_stylesheet = app.style().objectName(), app.styleSheet()
    # Qt's offscreen Windows backend may omit system font discovery and measure
    # ASCII as square fallback glyphs. Load the actual production font read-only
    # so geometry assertions measure the same text the desktop user sees.
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    # Exercise actual bundled catalogs and widgets without discovering or
    # monitoring private game files, remote sources, or production backups.
    monkeypatch.setattr(saves, "discover_saves", lambda: [])
    monkeypatch.setattr(SnapshotController, "set_shared_map", lambda self: None)
    monkeypatch.setattr(catalog, "_cache", catalog._bundled_cache)
    settings = Settings(tmp_path / "settings.json")
    base_name = "我們家・基地甲"
    settings.world(SHARED_SOURCE).update(
        shared_profile_initialized=True,
        bases=[{"id": "custom", "kind": "base", "name": base_name,
                "x": 0, "y": 0, "z": 0}], active_base="custom")
    window = MainWindow(settings=settings)
    window.controller.poll.stop()
    app.processEvents()  # Finish the scheduled initial source selection.
    window.loaded(build_shared_payload())
    try:
        yield window, base_name
    finally:
        window.close()
        app.processEvents()
        app.setStyleSheet(previous_stylesheet)
        if previous_style:
            app.setStyle(previous_style)
        if font_id >= 0:
            QFontDatabase.removeApplicationFont(font_id)


def test_all_pages_headers_filters_and_context_actions_are_english(english_window):
    window, base_name = english_window
    assert window.tabs.count() == 5
    assert window.snapshot.is_shared_map and window.snapshot.source_path is None
    assert window.current_base()["name"] == base_name
    for text in widget_strings(window):
        assert_english(text, base_name)
    menu = window.build_map_menu(0, 0)
    try:
        for action in menu.actions():
            assert_english(action.text(), base_name)
        assert any(base_name in action.text() for action in menu.actions())
    finally:
        menu.deleteLater()
    assert base_name in window.world_label.text()
    assert window.inventory_panel.summary is None
    assert window.animal_panel.summary is None


def test_real_catalog_and_guides_have_no_chinese_display_text(english_window):
    window, _ = english_window
    assert len(window.points) > 25000  # Real full-map catalog, not sample markers.
    for point in window.points:
        for field in ("name", "resource_type", "confidence", "source", "detail", "remaining_status"):
            assert_english(point.get(field, ""))
    items = load_item_catalog()["items"]
    assert len(items) > 3000
    for item in items.values():
        for field in ("name", "material_label", "category_label", "form_label"):
            assert_english(item.get(field, ""))
        if item.get("english"):
            assert item["name"] == item["english"]
    missions = load_missions()["missions"]
    assert len(missions) > 100
    for mission in missions:
        assert_english(mission_html(mission))


def test_english_markers_preserve_custom_names_and_unknown_quantities(english_window):
    window, base_name = english_window
    # Old aliases can still be recognized while all generated labels are English.
    assert mineral_style("金礦").label == "Gold"
    assert mineral_item_id("金礦") == "Gold_Ore"
    assert mineral_item_id("unidentified-future-mineral") is None
    assert saves._resource_name("Gold") == "Gold"
    assert saves._resource_name("Gold_Normal") == "Gold_Normal"
    custom_player = "玩家自訂名字"
    assert custom_player in WorldMap._marker_label({"kind": "player", "name": custom_player}, 1)
    assert base_name in WorldMap._marker_label(window.current_base(), 1)
    point = Resource("unknown", "deep_deposit", "Unknown mineral", "Unknown", 10, 20, 30,
                     drill_status="Cannot confirm", confidence="Candidate")
    before = deepcopy(point)
    window.show_point(point)
    text = window.point_detail.toPlainText()
    assert "Quantity unknown" in text and "Cannot confirm" in text
    assert_english(text, base_name)
    assert point == before and point.remaining is None
    assert remaining_text(point) != "0"
    stock = {"kind": "drill", "raw": {"_drill_inventory": {"quantity": None, "known_quantity": 0}}}
    assert "unknown" in drill_stock_label(stock).lower()
    stock["raw"]["_drill_inventory"].update(quantity=0, status="empty")
    assert "0 items" in drill_stock_label(stock)


@pytest.mark.parametrize("width,height", [(1100, 760), (1540, 1000)])
def test_map_filters_fit_english_labels_at_small_and_default_sizes(english_window, width, height):
    window, _ = english_window
    app = QApplication.instance()
    window.resize(width, height)
    window.tabs.setCurrentIndex(0)
    window.show()
    app.processEvents()
    window.layout().activate()
    app.processEvents()

    # A translated label must not force the entire window beyond the supported
    # small size or take so much width that the actual map becomes unusable.
    assert window.width() == width
    assert window.minimumWidth() <= 1100
    splitter = window.map.parentWidget()
    sidebar = splitter.widget(1)
    assert isinstance(sidebar, QScrollArea)
    assert sidebar.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert window.map.viewport().width() >= width * 0.45
    viewport = sidebar.viewport()
    clipped = []
    for checkbox in (*window.layer_boxes.values(), window.candidates_check):
        # Qt's size hint includes the real font, indicator, icon and spacing;
        # checking both allocation and viewport catches clipping in either place.
        required = checkbox.sizeHint().width()
        origin = checkbox.mapTo(viewport, QPoint(0, 0))
        visible_width = min(checkbox.width(), viewport.width() - origin.x())
        assert origin.x() >= 0, (checkbox.text(), origin.x())
        if visible_width < required:
            clipped.append((checkbox.text(), visible_width, required,
                            checkbox.font().pixelSize(), checkbox.fontMetrics().horizontalAdvance(checkbox.text())))
    assert not clipped, clipped
