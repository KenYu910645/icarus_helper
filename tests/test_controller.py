"""Exercise real Qt file notifications with synthetic saves and event pumping."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from icarus_helper.controller import SnapshotController
from icarus_helper import catalog, missions, saves, settings
from test_saves import save_fixture


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def spin(app, condition, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition(): return
        time.sleep(0.005)
    app.processEvents()
    assert condition(), "Qt event condition did not become true before the deadline"


def pump(app, duration=0.08):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def controllers(qt_app, tmp_path, monkeypatch):
    # Retain the real worker, parser, QFileSystemWatcher, QTimer and thread pool;
    # expensive static catalogs / mission tables are unrelated to watcher races.
    monkeypatch.setattr(settings, "app_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **kw: {"synthetic": True})
    monkeypatch.setattr(catalog, "merge_catalog", lambda snapshot, cat: snapshot.resources)
    monkeypatch.setattr(missions, "build_missions", lambda snapshot: [])
    created = []

    def make():
        controller = SnapshotController()
        controller.poll.stop()  # Success below must be due to native file events.
        controller.debounce.setInterval(35)
        loaded, failed, states = [], [], []
        controller.loaded.connect(loaded.append)
        controller.failed.connect(failed.append)
        controller.state.connect(states.append)
        created.append(controller)
        return controller, loaded, failed, states

    yield make
    for controller in created:
        controller.close()
    pump(qt_app)


def test_native_watcher_follows_consecutive_saves(qt_app, tmp_path, controllers):
    path = tmp_path / "世界" / "world.json"
    save_fixture(path)
    controller, loaded, failed, _ = controllers()
    notifications = []
    controller.watcher.fileChanged.connect(notifications.append)
    controller.set_path(path)
    spin(qt_app, lambda: len(loaded) == 1)
    first_hash = loaded[-1]["snapshot"].sha256
    assert str(path) in controller.watcher.files()
    for count in (2, 3, 4):
        save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": count})])
        spin(qt_app, lambda: len(loaded) >= count)
        assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == count
    assert loaded[-1]["snapshot"].sha256 != first_hash
    assert notifications and not failed


def test_atomic_file_replacement_is_rewatched(qt_app, tmp_path, controllers):
    path = tmp_path / "world.json"
    save_fixture(path)
    controller, loaded, failed, _ = controllers()
    controller.set_path(path)
    spin(qt_app, lambda: len(loaded) == 1)
    replacement = tmp_path / "replacement.tmp"
    save_fixture(replacement, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 42})])
    replacement.replace(path)
    spin(qt_app, lambda: len(loaded) >= 2)
    assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == 42
    assert str(path) in controller.watcher.files()
    save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 43})])
    spin(qt_app, lambda: len(loaded) >= 3)
    assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == 43
    assert not failed


def test_failed_refresh_keeps_last_success_then_recovers(qt_app, tmp_path, controllers):
    path = tmp_path / "world.json"
    save_fixture(path)
    controller, loaded, failed, states = controllers()
    controller.set_path(path)
    spin(qt_app, lambda: len(loaded) == 1)
    previous = loaded[-1]
    path.write_bytes(b'{"ProspectBlob":')
    spin(qt_app, lambda: bool(failed))
    controller.debounce.stop()
    assert len(loaded) == 1 and loaded[-1] is previous
    assert controller.attempts == 1
    controller.debounce.setInterval(35)
    save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 7})])
    spin(qt_app, lambda: len(loaded) == 2)
    assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == 7
    assert controller.attempts == 0
    assert any("未完成" in state or "失敗" in state for state in states)


def test_new_world_generation_discards_inflight_old_worker(qt_app, tmp_path, controllers, monkeypatch):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    save_fixture(old)
    save_fixture(new, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 999})])
    parsed, release = threading.Event(), threading.Event()
    original = saves.load_snapshot

    def gated(path, cache_dir=None):
        snapshot = original(path, cache_dir)
        if Path(path) == old:
            parsed.set()
            assert release.wait(3.0), "Test gate was not released"
        return snapshot

    monkeypatch.setattr(saves, "load_snapshot", gated)
    controller, loaded, failed, _ = controllers()
    try:
        controller.set_path(old)
        spin(qt_app, parsed.is_set)
        controller.set_path(new)
        release.set()
        spin(qt_app, lambda: bool(loaded))
        assert all(p["snapshot"].source_path == new for p in loaded)
        assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == 999
        assert not failed
    finally:
        release.set()


def test_save_written_while_worker_parses_eventually_loads_latest(qt_app, tmp_path, controllers, monkeypatch):
    path = tmp_path / "world.json"
    save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 1})])
    parsed, release = threading.Event(), threading.Event()
    original = saves.load_snapshot
    calls = []

    def gated(source, cache_dir=None):
        snapshot = original(source, cache_dir)
        calls.append(snapshot)
        if len(calls) == 1:
            parsed.set()
            assert release.wait(3.0), "Test gate was not released"
        return snapshot

    monkeypatch.setattr(saves, "load_snapshot", gated)
    controller, loaded, failed, _ = controllers()
    try:
        controller.set_path(path)
        spin(qt_app, parsed.is_set)
        save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 22})])
        pump(qt_app)
        release.set()
        spin(qt_app, lambda: loaded and loaded[-1]["snapshot"].raw_records[0]["Marker"] == 22)
        assert len(calls) >= 2 and not failed
        assert controller.last_signature == controller.signature()
    finally:
        release.set()


def test_controller_restart_reads_source_again(qt_app, tmp_path, controllers):
    path = tmp_path / "world.json"
    save_fixture(path)
    first, first_loaded, _, _ = controllers()
    first.set_path(path)
    spin(qt_app, lambda: len(first_loaded) == 1)
    first.close()
    save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 80})])
    second, loaded, failed, _ = controllers()
    second.set_path(path)
    spin(qt_app, lambda: bool(loaded))
    assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == 80
    assert not failed


def test_fallback_poll_recovers_new_source_after_retry_limit(qt_app, tmp_path, controllers):
    path = tmp_path / "world.json"
    save_fixture(path)
    controller, loaded, failed, _ = controllers()
    controller.set_path(path)
    spin(qt_app, lambda: len(loaded) == 1)
    controller.watcher.blockSignals(True)  # Simulate a missed native file event.
    path.write_bytes(b'{"ProspectBlob":')
    controller.refresh()
    spin(qt_app, lambda: bool(failed))
    controller.debounce.stop()
    controller.attempts = 4
    save_fixture(path, [("/Script/Icarus.GameModeStateRecorderComponent", {"Marker": 321})])
    controller.poll_source()
    spin(qt_app, lambda: len(loaded) == 2)
    assert loaded[-1]["snapshot"].raw_records[0]["Marker"] == 321
    assert controller.attempts == 0


def test_unrelated_directory_changes_do_not_reset_failed_source_budget(qt_app, tmp_path, controllers):
    path = tmp_path / "world.json"
    save_fixture(path)
    controller, loaded, failed, _ = controllers()
    controller.set_path(path)
    spin(qt_app, lambda: len(loaded) == 1)
    path.write_bytes(b'{"ProspectBlob":')
    controller.refresh()
    spin(qt_app, lambda: bool(failed))
    controller.debounce.stop()
    controller.attempts = 4
    (tmp_path / "other-world.tmp").write_bytes(b"another world's save event")
    pump(qt_app, 0.15)
    assert controller.attempts == 4
    assert not controller.debounce.isActive()
    assert len(loaded) == 1


def test_close_during_worker_cannot_restart_loading(qt_app, tmp_path, controllers, monkeypatch):
    path = tmp_path / "world.json"
    save_fixture(path)
    parsed, release = threading.Event(), threading.Event()
    original = saves.load_snapshot
    calls = []

    def gated(source, cache_dir=None):
        snapshot = original(source, cache_dir)
        calls.append(snapshot)
        parsed.set()
        assert release.wait(3.0), "Test gate was not released"
        return snapshot

    monkeypatch.setattr(saves, "load_snapshot", gated)
    controller, loaded, failed, _ = controllers()
    try:
        controller.set_path(path)
        spin(qt_app, parsed.is_set)
        release.set()
        controller.close()  # Worker success is now queued, not yet delivered.
        pump(qt_app, 0.15)
        assert len(calls) == 1
        assert not loaded and not failed
        assert not controller.running
    finally:
        release.set()
