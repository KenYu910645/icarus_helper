"""Exercise actual Qt base-menu actions with isolated synthetic worlds/settings."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox, QLineEdit, QListWidget

from icarus_helper.inventory_view import InventoryPanel
from icarus_helper.models import WorldSnapshot
from icarus_helper.settings import Settings
from icarus_helper.ui import MainWindow


def base(ident="home", name="Home", x=0, y=0, z=125):
    return {"id": ident, "name": name, "kind": "base", "x": x, "y": y, "z": z}


def snapshot(tmp_path, world="one", map_name="Prometheus"):
    now = datetime.now(timezone.utc)
    return WorldSnapshot(tmp_path / (world + "-synthetic.json"), world, "合成測試World", map_name, now, now, "synthetic-sha256")


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    inventory_calls, mission_bases = [], []
    monkeypatch.setattr(MainWindow, "populate_worlds", lambda *_: None)
    monkeypatch.setattr(MainWindow, "rebuild_missions", lambda w, *_: mission_bases.append(deepcopy(w.current_base())))
    monkeypatch.setattr(InventoryPanel, "set_snapshot", lambda panel, snap, selected: inventory_calls.append((snap.world_id, deepcopy(selected))))
    widget = MainWindow(settings=Settings(tmp_path / "settings.json"))
    widget.controller.poll.stop()
    widget.snapshot = snapshot(tmp_path)
    widget.show()
    app.processEvents()
    yield app, widget, inventory_calls, mission_bases
    widget.close()
    app.processEvents()
    assert widget.inventory_panel._closed
    assert widget.animal_panel._closed
    assert widget.controller.closed


def configure(window, *bases, active=None):
    _, widget, *_ = window
    config = widget.settings.world(widget.snapshot.world_id)
    config["bases"] = deepcopy(list(bases))
    if active is None:
        config.pop("active_base", None)
    else:
        config["active_base"] = active
    widget.update_bases()
    return config


def action(menu, text):
    return next(item for item in menu.actions() if item.text() == text)


def accept_base_dialog(monkeypatch, name, coordinates):
    """Simulate accepting the native dialog while retaining its save logic."""
    def accept(dialog):
        dialog.findChild(QLineEdit).setText(name)
        fields = dialog.findChildren(QDoubleSpinBox)
        assert len(fields) == 3
        for field, value in zip(fields, coordinates):
            field.setValue(value)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", accept)


def test_current_base_uses_saved_selection_and_returns_world_bound_copy(window):
    _, widget, *_ = window
    config = configure(window, base())
    selected = widget.current_base()
    assert selected == dict(base(), world_id="one", map_name="Prometheus")
    selected["x"] = 99
    assert config["bases"][0]["x"] == 0
    assert "world_id" not in config["bases"][0]
    config["active_base"] = "removed-base"
    assert widget.current_base() is None
    config.pop("active_base")
    config["bases"].append(base("outpost", "前哨", 100000))
    assert widget.current_base() is None
    config["active_base"] = "outpost"
    assert widget.current_base()["name"] == "前哨"


def test_animal_tab_uses_same_world_and_home_as_other_panels(window, monkeypatch):
    _, widget, *_ = window
    calls = []
    monkeypatch.setattr(widget.animal_panel, "set_snapshot", lambda snap, selected, catalog=None: calls.append((snap, deepcopy(selected))))
    configure(window, base())
    widget.update_inventory()
    assert widget.tabs.tabText(widget.tabs.indexOf(widget.animal_panel)) == "Tamed animals"
    assert calls == [(widget.snapshot, widget.current_base())]
    configure(window, base("outpost", "前哨", 25000, 30000), active="outpost")
    widget.update_inventory()
    assert calls[-1][1]["id"] == "outpost"
    assert calls[-1][1]["world_id"] == widget.snapshot.world_id


def test_right_menu_add_and_edit_persist_coordinates_and_sync_dependents(window, monkeypatch):
    app, widget, inventory_calls, mission_bases = window
    configure(window)
    accept_base_dialog(monkeypatch, "湖邊家", (12345.6, -23456.7, 987.6))
    menu = widget.build_map_menu(12345.6, -23456.7)
    action(menu, "Add base here…").trigger()
    selected = widget.current_base()
    assert selected["name"] == "湖邊家"
    assert (selected["x"], selected["y"], selected["z"]) == pytest.approx((12345.6, -23456.7, 987.6))
    assert inventory_calls[-1] == ("one", selected)
    assert mission_bases[-1] == selected
    assert widget.map.base == selected
    saved = Settings(widget.settings.path).world("one")
    assert saved["active_base"] == selected["id"]
    assert saved["bases"][0]["name"] == "湖邊家"

    app.processEvents()
    accept_base_dialog(monkeypatch, "新Base名", (-1000, 2000, 3000))
    menu = widget.build_map_menu(selected["x"], selected["y"])
    action(menu, "Edit “湖邊家”…").trigger()
    updated = widget.current_base()
    assert updated["id"] == selected["id"]
    assert updated["name"] == "新Base名"
    assert (updated["x"], updated["y"], updated["z"]) == (-1000, 2000, 3000)
    assert len(widget.bases()) == 1
    assert inventory_calls[-1] == ("one", updated)
    assert mission_bases[-1] == updated
    assert Settings(widget.settings.path).world("one")["bases"][0]["name"] == "新Base名"


def test_right_menu_switch_uses_each_action_id_and_updates_home_distances(window):
    _, widget, inventory_calls, mission_bases = window
    configure(window, base(), base("outpost", "前哨", 100000, 0), active="home")
    menu = widget.build_map_menu(200000, 150000)
    submenu = action(menu, "Switch base").menu()
    assert action(submenu, "Home").isChecked()
    assert not action(submenu, "前哨").isChecked()
    action(submenu, "前哨").trigger()
    selected = widget.current_base()
    assert selected["id"] == "outpost"
    assert widget.map.base == selected
    assert inventory_calls[-1] == ("one", selected)
    assert mission_bases[-1] == selected
    widget.show_point({"kind": "cave", "name": "測試洞穴", "x": 100000, "y": 40000, "z": 0})
    assert "From 前哨: 400 m" in widget.point_detail.toPlainText()
    action(submenu, "Home").trigger()
    assert widget.current_base()["id"] == "home"
    assert "From Home: 1,077 m" in widget.point_detail.toPlainText()
    assert inventory_calls[-1][1]["id"] == "home"
    assert Settings(widget.settings.path).world("one")["active_base"] == "home"


def test_right_menu_move_preserves_base_identity_height_and_refreshes(window):
    _, widget, inventory_calls, mission_bases = window
    configure(window, base(z=777), active="home")
    menu = widget.build_map_menu(200000, -150000)
    action(menu, "Move “Home” here").trigger()
    selected = widget.current_base()
    assert (selected["id"], selected["name"], selected["z"]) == ("home", "Home", 777)
    assert (selected["x"], selected["y"]) == (200000, -150000)
    assert inventory_calls[-1] == ("one", selected)
    assert mission_bases[-1] == selected
    assert widget.map.base == selected
    saved = Settings(widget.settings.path).world("one")["bases"][0]
    assert (saved["x"], saved["y"], saved["z"]) == (200000, -150000, 777)


def test_right_menu_delete_active_selects_remaining_then_clears_last_base(window):
    app, widget, inventory_calls, mission_bases = window
    configure(window, base(), base("outpost", "前哨", 100000, 0), active="home")
    action(widget.build_map_menu(0, 0), "Delete “Home”").trigger()
    assert widget.current_base()["id"] == "outpost"
    assert len(widget.bases()) == 1
    assert inventory_calls[-1][1]["id"] == "outpost"
    assert mission_bases[-1]["id"] == "outpost"
    app.processEvents()
    action(widget.build_map_menu(100000, 0), "Delete “前哨”").trigger()
    assert widget.bases() == [] and widget.current_base() is None
    assert widget.map.base is None
    assert inventory_calls[-1] == ("one", None)
    assert mission_bases[-1] is None
    saved = Settings(widget.settings.path).world("one")
    assert saved["bases"] == [] and not saved["active_base"]


def test_old_world_menu_cannot_add_delete_move_or_switch_current_world(window, monkeypatch, tmp_path):
    _, widget, inventory_calls, _ = window
    configure(window, base(), base("outpost", "前哨", 100000), active="home")
    clicked_menu = widget.build_map_menu(0, 0)
    empty_menu = widget.build_map_menu(200000, -150000)
    old_bases = deepcopy(widget.bases())
    widget.snapshot = snapshot(tmp_path, "two", "Olympus")
    config = configure(window, base("home", "其他World的家", -100000), active="home")
    current = deepcopy(config)
    monkeypatch.setattr(widget, "base_dialog", lambda *_args, **_kw: pytest.fail("Old-world action opened a dialog"))
    for menu, label in ((clicked_menu, "Add base here…"), (clicked_menu, "Delete “Home”"), (empty_menu, "Move “Home” here")):
        action(menu, label).trigger()
    action(action(clicked_menu, "Switch base").menu(), "前哨").trigger()
    assert config == current
    assert widget.settings.world("one")["bases"] == old_bases
    assert widget.current_base()["name"] == "其他World的家"
    assert widget.current_base()["world_id"] == "two"
    assert widget.current_base()["map_name"] == "Olympus"
    assert not inventory_calls


def test_dialog_accepted_after_world_switch_cannot_save_old_coordinates(window, monkeypatch, tmp_path):
    _, widget, inventory_calls, _ = window
    configure(window, base(), active="home")
    before = deepcopy(widget.settings.world("one"))
    def switch_while_open(dialog):
        dialog.findChild(QLineEdit).setText("不應寫入的舊WorldBase")
        widget.snapshot = snapshot(tmp_path, "two", "Olympus")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", switch_while_open)
    action(widget.build_map_menu(200000, 150000), "Add base here…").trigger()
    assert widget.settings.world("one") == before
    assert widget.settings.world("two")["bases"] == []
    assert "world has changed" in widget.last_status
    assert not inventory_calls


def test_native_right_click_supplies_clicked_world_position_to_menu(window, monkeypatch):
    app, widget, *_ = window
    configure(window, base(x=160000, y=-80000), active="home")
    widget.go_home()
    app.processEvents()
    received = []
    monkeypatch.setattr(widget, "show_map_menu", lambda x, y, view=None: received.append((x, y, view)))
    viewport = widget.map.viewport()
    point = viewport.rect().center()
    expected = widget.map.mapToScene(point)
    QTest.mouseClick(viewport, Qt.MouseButton.RightButton, pos=point)
    assert len(received) == 1
    assert received[0][:2] == pytest.approx((expected.x() * 100, expected.y() * 100))
    assert received[0][2] is widget.map


def test_candidates_from_old_world_cannot_be_added_after_snapshot_replacement(window, monkeypatch, tmp_path):
    _, widget, *_ = window
    widget.snapshot.deployables = [{"id": "synthetic-box", "x": 12345, "y": -54321, "z": 987}]
    def accept_in_other_world(dialog):
        dialog.findChild(QListWidget).setCurrentRow(0)
        widget.snapshot = snapshot(tmp_path, "replacement", "Olympus")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", accept_in_other_world)
    monkeypatch.setattr(widget, "base_dialog", lambda *_args, **_kw: pytest.fail("Old candidate opened in new world"))
    widget.base_candidates()
    assert not widget.bases()
    assert not widget.settings.world("one")["bases"]
