"""Exercise real Qt file notifications with synthetic saves and event pumping."""
from __future__ import annotations

import os
import json
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
    path = tmp_path / "World" / "world.json"
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
    assert any("incomplete" in state or "failed" in state for state in states)


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


@pytest.fixture
def account_loader(monkeypatch):
    """Controller tests use an invented profile contract, never local accounts."""
    from icarus_helper import operation_eligibility
    reads = []

    def load(snapshot):
        if getattr(snapshot, "is_remote", False):
            return {"status": "remote", "world_id": snapshot.world_id,
                    "snapshot_sha256": snapshot.sha256, "profile_signature": None}
        profile = operation_eligibility.source_profile_path(snapshot.source_path)
        if profile is None or not profile.exists():
            return {"status": "missing", "world_id": snapshot.world_id,
                    "snapshot_sha256": snapshot.sha256, "profile_signature": None}
        stat = profile.stat()
        marker = json.loads(profile.read_text(encoding="utf-8"))["marker"]
        reads.append(profile)
        return {"status": "ok", "world_id": snapshot.world_id, "snapshot_sha256": snapshot.sha256,
                "marker": marker, "profile_signature": (stat.st_mtime_ns, stat.st_size)}

    monkeypatch.setattr(operation_eligibility, "load_operation_account", load)
    return operation_eligibility, load, reads


def account_world(root, marker="first", *, profile_exists=True):
    account = root / "帳號"
    source = account / "Prospects" / "World.json"
    source.parent.mkdir(parents=True)
    save_fixture(source)
    profile = account / "Profile.json"
    if profile_exists:
        profile.write_text(json.dumps({"marker": marker}), encoding="utf-8")
    return source, profile


def test_profile_only_native_change_refreshes_same_world(qt_app, tmp_path, controllers, account_loader):
    source, profile = account_world(tmp_path)
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: len(loaded) == 1)
    world_signature, world_hash = controller.signature(), loaded[-1]["snapshot"].sha256
    assert str(profile) in controller.watcher.files()
    assert str(profile.parent) in controller.watcher.directories()
    profile.write_text(json.dumps({"marker": "a different account unlock"}), encoding="utf-8")
    spin(qt_app, lambda: len(loaded) >= 2)
    assert loaded[-1]["snapshot"].operation_account["marker"] == "a different account unlock"
    assert loaded[-1]["snapshot"].sha256 == world_hash
    assert controller.signature() == world_signature
    assert controller.last_profile_signature == controller.profile_signature()
    assert not failed


def test_profile_creation_and_replacement_are_watched(qt_app, tmp_path, controllers, account_loader):
    source, profile = account_world(tmp_path, profile_exists=False)
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: len(loaded) == 1)
    assert controller.profile_path == profile
    assert str(profile.parent) in controller.watcher.directories()
    profile.write_text(json.dumps({"marker": "created"}), encoding="utf-8")
    spin(qt_app, lambda: len(loaded) >= 2)
    assert loaded[-1]["snapshot"].operation_account["marker"] == "created"
    replacement = profile.with_suffix(".replacement")
    replacement.write_text(json.dumps({"marker": "atomically replaced profile"}), encoding="utf-8")
    replacement.replace(profile)
    spin(qt_app, lambda: len(loaded) >= 3)
    assert loaded[-1]["snapshot"].operation_account["marker"] == "atomically replaced profile"
    assert str(profile) in controller.watcher.files()
    assert not failed


def test_profile_changed_during_payload_build_is_not_lost(qt_app, tmp_path, controllers, account_loader, monkeypatch):
    source, profile = account_world(tmp_path)
    module, original, _reads = account_loader
    parsed, release = threading.Event(), threading.Event()
    attempts = []
    def gated(snapshot):
        result = original(snapshot)
        attempts.append(result)
        if len(attempts) == 1:
            parsed.set()
            assert release.wait(3), "Account test gate was not released"
        return result
    monkeypatch.setattr(module, "load_operation_account", gated)
    controller, loaded, failed, _states = controllers()
    try:
        controller.set_path(source)
        spin(qt_app, parsed.is_set)
        profile.write_text(json.dumps({"marker": "changed while background work was running"}), encoding="utf-8")
        pump(qt_app)
        release.set()
        spin(qt_app, lambda: loaded and loaded[-1]["snapshot"].operation_account.get("marker") == "changed while background work was running")
        assert len(attempts) >= 2
        assert controller.last_profile_signature == controller.profile_signature()
        assert len({item["snapshot"].sha256 for item in loaded}) == 1
        assert not failed
    finally:
        release.set()


def test_profile_poll_recovers_a_missed_native_event(qt_app, tmp_path, controllers, account_loader):
    source, profile = account_world(tmp_path)
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: len(loaded) == 1)
    controller.watcher.blockSignals(True)
    profile.write_text(json.dumps({"marker": "fallback account change"}), encoding="utf-8")
    controller.poll_source()
    spin(qt_app, lambda: len(loaded) >= 2)
    assert loaded[-1]["snapshot"].operation_account["marker"] == "fallback account change"
    assert not failed


def test_switching_world_discards_inflight_account_and_watches_new_account(qt_app, tmp_path, controllers, account_loader, monkeypatch):
    old, old_profile = account_world(tmp_path / "old", "old")
    new, new_profile = account_world(tmp_path / "new", "new")
    module, original, _reads = account_loader
    parsed, release = threading.Event(), threading.Event()
    def gated(snapshot):
        result = original(snapshot)
        if snapshot.source_path == old:
            parsed.set()
            assert release.wait(3), "Account test gate was not released"
        return result
    monkeypatch.setattr(module, "load_operation_account", gated)
    controller, loaded, failed, _states = controllers()
    try:
        controller.set_path(old)
        spin(qt_app, parsed.is_set)
        controller.set_path(new)
        assert controller.profile_path == new_profile
        assert str(old_profile) not in controller.watcher.files()
        assert str(old_profile.parent) not in controller.watcher.directories()
        release.set()
        spin(qt_app, lambda: bool(loaded))
        assert all(item["snapshot"].source_path == new for item in loaded)
        assert loaded[-1]["snapshot"].operation_account["marker"] == "new"
        assert controller.last_profile_signature == controller.profile_signature()
        assert not failed
    finally:
        release.set()


def test_remote_source_clears_local_profile_watch_and_never_reads_it(qt_app, tmp_path, controllers, account_loader, monkeypatch):
    from icarus_helper import release_sync
    source, profile = account_world(tmp_path)
    module, original, reads = account_loader
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: len(loaded) == 1)
    local_reads = len(reads)
    remote = saves.load_snapshot(source)
    remote.is_remote = True
    remote.remote_stale = False
    monkeypatch.setattr(release_sync, "load_remote_snapshot", lambda _cache: remote)
    controller.set_remote_world(tmp_path / "remote-cache")
    spin(qt_app, lambda: len(loaded) == 2)
    assert controller.profile_path is None
    assert controller.last_profile_signature is controller.last_attempt_profile_signature is None
    assert controller.profile_signature() is None
    assert not controller.watcher.files() and not controller.watcher.directories()
    assert len(reads) == local_reads
    assert loaded[-1]["snapshot"].operation_account["status"] == "remote"
    profile.write_text(json.dumps({"marker": "must not trigger remote work"}), encoding="utf-8")
    controller.poll_source()
    controller.changed(str(profile))
    pump(qt_app)
    assert len(loaded) == 2 and len(reads) == local_reads and not failed


def test_account_is_loaded_before_missions_on_the_background_worker(qt_app, tmp_path, controllers, account_loader, monkeypatch):
    source, _profile = account_world(tmp_path)
    built = []
    def build(snapshot):
        built.append((threading.get_ident(), snapshot.operation_account["marker"]))
        return []
    monkeypatch.setattr(missions, "build_missions", build)
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: bool(loaded))
    assert built == [(built[0][0], "first")]
    assert built[0][0] != threading.get_ident()
    assert not failed


def test_real_account_loader_refreshes_verified_talents_without_world_change(qt_app, tmp_path, controllers):
    # Match the invented ClaimedAccountID in save_fixture; no installed account
    # directory or game files are consulted by this integration check.
    source = tmp_path / "synthetic" / "Prospects" / "World.json"
    save_fixture(source)
    profile = source.parent.parent / "Profile.json"
    profile.write_text(json.dumps({"UserID": "synthetic", "Talents": [], "UnlockedFlags": []}), encoding="utf-8")
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: len(loaded) == 1)
    first = loaded[-1]["snapshot"]
    assert first.operation_account["status"] == "verified"
    assert first.operation_account["talents"] == {}
    profile.write_text(json.dumps({"UserID": "synthetic", "Talents": [{"RowName": "Synthetic_Mission", "Rank": 1}],
                                   "UnlockedFlags": ["Synthetic_Unlock"]}), encoding="utf-8")
    spin(qt_app, lambda: len(loaded) >= 2)
    snapshot = loaded[-1]["snapshot"]
    assert snapshot.sha256 == first.sha256
    assert snapshot.operation_account["status"] == "verified"
    assert snapshot.operation_account["talents"] == {"Synthetic_Mission": 1}
    assert snapshot.operation_account["flags"] == ["Synthetic_Unlock"]
    assert snapshot.operation_account["world_id"] == snapshot.world_id
    assert snapshot.operation_account["snapshot_sha256"] == snapshot.sha256
    assert controller.last_profile_signature == controller.profile_signature()
    assert not failed


def test_shared_map_clears_account_watch_and_ignores_its_changes(qt_app, tmp_path, controllers, account_loader, monkeypatch):
    from icarus_helper import shared_profile
    source, profile = account_world(tmp_path)
    _module, _load, reads = account_loader
    monkeypatch.setattr(shared_profile, "build_shared_payload", lambda: {
        "snapshot": shared_profile.SharedMapContext(), "catalog": {}, "points": [], "missions": []})
    controller, loaded, failed, _states = controllers()
    controller.set_path(source)
    spin(qt_app, lambda: len(loaded) == 1)
    local_reads = len(reads)
    controller.set_shared_map()
    spin(qt_app, lambda: len(loaded) == 2)
    assert controller.profile_path is None
    assert controller.last_profile_signature is controller.last_attempt_profile_signature is None
    assert not controller.watcher.files() and not controller.watcher.directories()
    profile.write_text(json.dumps({"marker": "must not enter the shared map"}), encoding="utf-8")
    controller.poll_source()
    controller.changed(str(profile))
    pump(qt_app)
    assert len(loaded) == 2 and len(reads) == local_reads and not failed
