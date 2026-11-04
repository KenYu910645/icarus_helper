"""Remote cadence and world isolation using Qt workers and no network requests."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from icarus_helper import catalog, missions, saves, settings, shared_profile
from icarus_helper.controller import SnapshotController
from icarus_helper.models import Resource, WorldSnapshot
from test_saves import save_fixture


def spin(app, condition, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.005)
    assert condition(), "Qt remote worker did not reach the expected state"


def pump(app, duration=0.08):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def remote_setup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    calls, controllers = [], []
    snapshot = WorldSnapshot(tmp_path / "remote-cache" / "current.json", "remote-world",
                             "GuoGuoGaGa", "Prometheus", now, now, "remote-hash")
    snapshot.is_remote = True
    snapshot.remote_stale = False
    snapshot.remote_status = "已同步"
    snapshot.remote_checked_at = now
    snapshot.resources = [Resource("ore", "deposit", "Iron", "Iron", 10, 20, 0)]
    snapshot.players = [Resource("player", "player", "Player", "", 30, 40, 0)]
    module = ModuleType("icarus_helper.release_sync")

    def remote_load(cache_dir=None):
        calls.append(cache_dir)
        return snapshot

    module.load_remote_snapshot = remote_load
    monkeypatch.setitem(sys.modules, "icarus_helper.release_sync", module)
    monkeypatch.setattr(settings, "app_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **kw: {"synthetic": True})
    monkeypatch.setattr(catalog, "merge_catalog", lambda world, cat: list(world.resources))
    monkeypatch.setattr(missions, "build_missions", lambda world: [{"world": world.world_id}])
    monkeypatch.setattr(shared_profile, "build_shared_payload", lambda: {
        "snapshot": shared_profile.SharedMapContext(), "catalog": {}, "points": [], "missions": []})

    def create():
        controller = SnapshotController()
        loaded, failed, states = [], [], []
        controller.loaded.connect(loaded.append)
        controller.failed.connect(failed.append)
        controller.state.connect(states.append)
        controllers.append(controller)
        return controller, loaded, failed, states

    yield app, create, module, calls, snapshot
    for controller in controllers:
        controller.close()
    pump(app)


def test_remote_first_load_uses_same_snapshot_and_120_second_cadence(remote_setup, tmp_path):
    app, create, _, calls, snapshot = remote_setup
    controller, loaded, failed, states = create()
    cache = tmp_path / "隔離快取"
    controller.set_remote_world(cache)
    spin(app, lambda: bool(loaded))
    assert calls == [cache]
    assert loaded[-1]["snapshot"] is snapshot
    assert {point.id for point in loaded[-1]["points"]} == {"ore", "player"}
    assert loaded[-1]["missions"] == [{"world": snapshot.world_id}]
    assert controller.remote_world and controller.remote_cache_dir == cache
    assert controller.path is None and not controller.shared_map
    assert controller.remote_poll.isSingleShot() and controller.remote_poll.interval() == 120000
    assert controller.remote_poll.isActive() and not controller.debounce.isActive()
    assert not controller.watcher.files() and not controller.watcher.directories()
    assert any("every two minutes" in state for state in states) and not failed


def test_local_fallback_and_cache_events_never_poll_remote(remote_setup):
    app, create, _, calls, _ = remote_setup
    controller, loaded, _, _ = create()
    controller.set_remote_world()
    spin(app, lambda: bool(loaded))
    for _ in range(5):
        controller.poll_source()
        controller.changed("downloaded-cache.json")
    pump(app)
    assert len(calls) == 1 and not controller.debounce.isActive()


def test_remote_timer_and_manual_refresh_both_load_again(remote_setup):
    app, create, _, calls, _ = remote_setup
    controller, loaded, failed, _ = create()
    controller.set_remote_world()
    spin(app, lambda: len(loaded) == 1)
    controller.remote_poll.timeout.emit()
    spin(app, lambda: len(loaded) == 2)
    controller.refresh()
    spin(app, lambda: len(loaded) == 3)
    assert len(calls) == 3 and controller.remote_poll.isActive() and not failed


def test_remote_failure_preserves_success_and_uses_bounded_retry(remote_setup):
    app, create, module, calls, snapshot = remote_setup
    controller, loaded, failed, states = create()
    controller.set_remote_world()
    spin(app, lambda: bool(loaded))
    previous = loaded[-1]

    def offline(cache_dir=None):
        calls.append(cache_dir)
        raise OSError("offline")

    module.load_remote_snapshot = offline
    controller.refresh()
    spin(app, lambda: bool(failed))
    pump(app)
    assert loaded == [previous] and loaded[0]["snapshot"] is snapshot
    assert len(calls) == 2 and controller.attempts == 1
    assert controller.remote_poll.isActive() and controller.remote_poll.interval() == 120000
    assert not controller.debounce.isActive()
    assert "retry in two minutes" in states[-1]


def test_first_remote_failure_does_not_start_local_fast_retry(remote_setup):
    app, create, module, _, _ = remote_setup
    module.load_remote_snapshot = lambda cache_dir=None: (_ for _ in ()).throw(OSError("offline"))
    controller, loaded, failed, _ = create()
    controller.set_remote_world()
    spin(app, lambda: bool(failed))
    assert not loaded and controller.attempts == 1
    assert not controller.debounce.isActive() and controller.remote_poll.isActive()


def test_cached_stale_remote_payload_stays_explicit(remote_setup):
    app, create, _, _, snapshot = remote_setup
    snapshot.remote_stale = True
    controller, loaded, failed, states = create()
    controller.set_remote_world()
    spin(app, lambda: bool(loaded))
    assert loaded[-1]["snapshot"].remote_stale
    assert "stale" in states[-1] and not failed
    assert controller.remote_poll.isActive()


@pytest.mark.parametrize("destination", ["shared", "local"])
def test_inflight_remote_cannot_replace_new_source(remote_setup, tmp_path, destination):
    app, create, module, _, snapshot = remote_setup
    started, release = threading.Event(), threading.Event()

    def gated(cache_dir=None):
        started.set()
        assert release.wait(3)
        return snapshot

    module.load_remote_snapshot = gated
    controller, loaded, failed, _ = create()
    path = tmp_path / "local.json"
    save_fixture(path)
    try:
        controller.set_remote_world()
        spin(app, started.is_set)
        if destination == "shared":
            controller.set_shared_map()
        else:
            controller.set_path(path)
        release.set()
        spin(app, lambda: bool(loaded))
        assert all(not getattr(row["snapshot"], "is_remote", False) for row in loaded)
        assert not controller.remote_world and not controller.remote_poll.isActive()
        assert not failed
        if destination == "local":
            assert loaded[-1]["snapshot"].source_path == path
            assert str(path) in controller.watcher.files()
        else:
            assert loaded[-1]["snapshot"].is_shared_map
    finally:
        release.set()


@pytest.mark.parametrize("origin", ["shared", "local"])
def test_inflight_other_source_cannot_replace_remote(remote_setup, tmp_path, monkeypatch, origin):
    app, create, _, _, snapshot = remote_setup
    started, release = threading.Event(), threading.Event()
    local = tmp_path / "local.json"
    save_fixture(local)
    original = saves.load_snapshot if origin == "local" else shared_profile.build_shared_payload

    def gated(*args, **kwargs):
        result = original(*args, **kwargs)
        started.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(saves if origin == "local" else shared_profile,
                        "load_snapshot" if origin == "local" else "build_shared_payload", gated)
    controller, loaded, failed, _ = create()
    try:
        controller.set_path(local) if origin == "local" else controller.set_shared_map()
        spin(app, started.is_set)
        controller.set_remote_world(tmp_path / "remote")
        release.set()
        spin(app, lambda: bool(loaded))
        assert all(row["snapshot"] is snapshot for row in loaded)
        assert controller.remote_poll.isActive() and not failed
        assert not controller.watcher.files() and not controller.watcher.directories()
    finally:
        release.set()


def test_switch_from_remote_loaded_signal_does_not_rearm_remote_timer(remote_setup):
    app, create, _, _, _ = remote_setup
    controller, loaded, failed, _ = create()
    controller.loaded.connect(lambda payload: controller.set_shared_map()
                              if getattr(payload["snapshot"], "is_remote", False) else None)
    controller.set_remote_world()
    spin(app, lambda: loaded and getattr(loaded[-1]["snapshot"], "is_shared_map", False))
    assert not controller.remote_world and not controller.remote_poll.isActive() and not failed


def test_close_during_remote_worker_never_restarts_or_emits(remote_setup):
    app, create, module, calls, snapshot = remote_setup
    started, release = threading.Event(), threading.Event()

    def gated(cache_dir=None):
        calls.append(cache_dir)
        started.set()
        assert release.wait(3)
        return snapshot

    module.load_remote_snapshot = gated
    controller, loaded, failed, _ = create()
    try:
        controller.set_remote_world()
        spin(app, started.is_set)
        release.set()
        controller.close()
        pump(app)
        controller.set_remote_world()
        controller.refresh()
        assert len(calls) == 1 and not loaded and not failed and not controller.running
        assert not controller.remote_poll.isActive() and not controller.poll.isActive()
    finally:
        release.set()
