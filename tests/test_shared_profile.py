"""Public preset loading and shared/live source isolation with real Qt workers."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from icarus_helper import catalog, saves, shared_profile
from icarus_helper.controller import SnapshotController
from icarus_helper.models import SaveDescriptor, WorldSnapshot
from icarus_helper.settings import Settings
from icarus_helper.shared_profile import SHARED_SOURCE, SharedMapContext, load_shared_profile, seed_shared_bases
from icarus_helper.ui import MainWindow


def spin(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.005)
    assert condition(), "Qt background load did not complete"


def map_catalog(*args, **kwargs):
    return {"map": "Prometheus", "map_key": "prometheus", "calibrated": True,
            "bounds": {"min_x": -403200, "min_y": -403200, "max_x": 403200, "max_y": 403200},
            "caves": [{"id": "cave", "kind": "cave", "name": "測試入口", "resource_type": "地穴",
                       "x": -30000, "y": -190000, "z": 100, "confidence": "已確認"}],
            "resources": [{"id": "deep", "kind": "deep_candidate", "name": "候選礦床", "resource_type": "未知",
                           "x": -35000, "y": -180000, "z": 0, "confidence": "候選", "drill_status": "無法確認"}]}


def test_public_profile_only_contains_map_and_base_coordinates():
    raw = json.loads(shared_profile.PROFILE_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"schema_version", "map_name", "bases"}
    assert set(raw["bases"][0]) == {"name", "x", "y", "z"}
    profile = load_shared_profile()
    assert profile["map_name"] == "Prometheus"
    assert profile["bases"][0]["x"] == -38989.4
    assert profile["bases"][0]["y"] == -189495.1
    context = SharedMapContext()
    assert context.saved_at is None and context.source_path is None and context.sha256 == ""
    assert context.coverage["host_save_connected"] is False


def test_profile_filters_unapproved_identifiers_and_paths(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"schema_version": 1, "map_name": "Prometheus", "world_id": "private-world",
                               "source": "C:/private/PlayerData/account", "bases": [
                                   {"id": "private-base", "name": "共用基地", "x": 0, "y": 0, "z": 0, "account": "private"}]}))
    loaded = load_shared_profile(path)
    encoded = json.dumps(loaded)
    assert all(secret not in encoded for secret in ("private-world", "private-base", "C:/private", '"account"'))


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), 403201])
def test_profile_rejects_unusable_coordinates(tmp_path, coordinate):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"schema_version": 1, "map_name": "Prometheus", "bases": [{"x": coordinate, "y": 0}]}))
    with pytest.raises(ValueError):
        load_shared_profile(path)


def test_seeded_bases_never_cross_world_or_overwrite_local_changes(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    settings.world("another-world")["bases"] = [{"id": "other", "x": 11, "y": 22}]
    profile, context = load_shared_profile(), SharedMapContext()
    seed_shared_bases(settings, profile, context)
    settings.world(context.world_id)["bases"] = []
    seed_shared_bases(settings, profile, context)
    assert settings.world(context.world_id)["bases"] == []
    assert settings.world("another-world")["bases"] == [{"id": "other", "x": 11, "y": 22}]
    assert profile["bases"]


def test_shared_payload_does_not_read_or_merge_private_save(monkeypatch):
    monkeypatch.setattr(catalog, "load_catalog", map_catalog)
    monkeypatch.setattr(saves, "load_snapshot", lambda *_args, **_kw: pytest.fail("A shared map must not read a save"))
    monkeypatch.setattr(catalog, "merge_catalog", lambda *_args, **_kw: pytest.fail("A shared map must not merge private state"))
    payload = shared_profile.build_shared_payload()
    assert payload["snapshot"].is_shared_map
    assert len(payload["points"]) == 2
    assert payload["points"][1]["drill_status"] == "無法確認"
    assert all(mission["status_key"] == "unknown" and not mission["progress"] for mission in payload["missions"])


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def shared_window(qt_app, tmp_path, monkeypatch):
    other = tmp_path / "other.json"
    other.write_text("{}")
    monkeypatch.setattr(saves, "discover_saves", lambda: [SaveDescriptor(other, "自己的世界", "Prometheus", other.stat().st_mtime)])
    monkeypatch.setattr(catalog, "load_catalog", map_catalog)
    widget = MainWindow(settings=Settings(tmp_path / "settings.json"))
    widget.controller.poll.stop()
    widget.show()
    spin(qt_app, lambda: bool(widget.snapshot))
    yield widget, other
    widget.close()
    qt_app.processEvents()


def test_first_launch_opens_shared_map_even_with_unrelated_local_saves(shared_window):
    widget, _ = shared_window
    assert widget.world_combo.currentData() == SHARED_SOURCE
    assert widget.snapshot.is_shared_map
    assert widget.current_base()["name"] == "我們家"
    assert widget.current_base()["world_id"] == SHARED_SOURCE
    assert "未連接主機存檔" in widget.world_label.text()
    assert widget.controller.path is None
    assert not widget.controller.watcher.files() and not widget.controller.watcher.directories()
    assert widget.inventory_panel.snapshot is None and widget.inventory_panel.summary is None
    assert "未連接主機存檔" in widget.inventory_panel.status.text()
    assert widget.mission_list.count() > 0
    assert all(m["status_key"] == "unknown" and not m["progress"] for m in widget.missions)
    assert "攻略預覽" in widget.mission_list.item(0).text()


def test_real_world_selection_is_remembered_and_does_not_inherit_shared_home(shared_window, qt_app, monkeypatch):
    widget, other = shared_window
    selected = []
    monkeypatch.setattr(widget.controller, "set_path", selected.append)
    widget.world_combo.setCurrentIndex(widget.world_combo.findData(str(other)))
    assert selected == [str(other)]
    assert widget.current_base() is None
    assert Settings(widget.settings.path).data["selected_source"] == str(other)
    now = datetime.now(timezone.utc)
    real = WorldSnapshot(other, "real-world", "自己的世界", "Prometheus", now, now, "synthetic")
    widget.loaded({"snapshot": real, "catalog": map_catalog(), "points": [], "missions": []})
    assert widget.current_base() is None
    assert widget.settings.world("real-world")["bases"] == []
    assert widget.settings.world(SHARED_SOURCE)["bases"][0]["name"] == "我們家"
    widget.populate_worlds()
    qt_app.processEvents()
    assert widget.world_combo.currentData() == str(other)


def test_shared_refresh_recovers_from_failure_without_fake_save_time(shared_window):
    widget, _ = shared_window
    widget.failed("temporary shared asset read failure")
    assert "圖資更新失敗" in widget.world_label.text()
    widget.loaded(shared_profile.build_shared_payload())
    assert not widget.data_stale
    assert "圖資更新失敗" not in widget.world_label.text()
    assert widget.snapshot.saved_at is None


def test_old_real_save_job_cannot_replace_new_shared_map(qt_app, tmp_path, monkeypatch):
    controller = SnapshotController()
    controller.poll.stop()
    started, release = threading.Event(), threading.Event()
    now = datetime.now(timezone.utc)
    old = WorldSnapshot(tmp_path / "old.json", "old", "舊世界", "Prometheus", now, now, "synthetic")

    def gated(*_args, **_kwargs):
        started.set()
        assert release.wait(3)
        return old

    monkeypatch.setattr(saves, "load_snapshot", gated)
    monkeypatch.setattr(catalog, "load_catalog", map_catalog)
    monkeypatch.setattr(catalog, "merge_catalog", lambda *_: [])
    loaded = []
    controller.loaded.connect(loaded.append)
    try:
        controller.set_path(old.source_path)
        spin(qt_app, started.is_set)
        controller.set_shared_map()
        release.set()
        spin(qt_app, lambda: bool(loaded))
        assert all(getattr(item["snapshot"], "is_shared_map", False) for item in loaded)
        assert controller.path is None
        assert not controller.watcher.files() and not controller.watcher.directories()
    finally:
        release.set()
        controller.close()


def test_old_shared_map_job_cannot_replace_new_real_save(qt_app, tmp_path, monkeypatch):
    controller = SnapshotController()
    controller.poll.stop()
    started, release = threading.Event(), threading.Event()
    original = shared_profile.build_shared_payload

    def gated():
        started.set()
        assert release.wait(3)
        return original()

    now = datetime.now(timezone.utc)
    source = tmp_path / "real.json"
    source.write_text("{}")
    stat = source.stat()
    real = WorldSnapshot(source, "real", "選定的世界", "Prometheus", now, now, "synthetic",
                         source_mtime=stat.st_mtime, source_mtime_ns=stat.st_mtime_ns, source_size=stat.st_size)
    monkeypatch.setattr(shared_profile, "build_shared_payload", gated)
    monkeypatch.setattr(saves, "load_snapshot", lambda *_args, **_kwargs: real)
    monkeypatch.setattr(catalog, "load_catalog", map_catalog)
    monkeypatch.setattr(catalog, "merge_catalog", lambda *_: [])
    loaded = []
    controller.loaded.connect(loaded.append)
    try:
        controller.set_shared_map()
        spin(qt_app, started.is_set)
        controller.set_path(source)
        release.set()
        spin(qt_app, lambda: bool(loaded))
        assert all(item["snapshot"].world_id == "real" for item in loaded)
        assert controller.path == source and str(source) in controller.watcher.files()
    finally:
        release.set()
        controller.close()
