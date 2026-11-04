"""Debounced file watching and generation-isolated background snapshot loading."""
from __future__ import annotations

import traceback
import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QRunnable, QThreadPool, QTimer, Signal


class WorkerSignals(QObject):
    success = Signal(int, object)
    failure = Signal(int, str)


def snapshot_payload(snapshot):
    """Build every view from the same validated local or downloaded snapshot."""
    from .operation_eligibility import load_operation_account
    snapshot.operation_account = load_operation_account(snapshot)
    from .catalog import load_catalog, merge_catalog
    catalog = load_catalog(snapshot.map_name, world_seed=getattr(snapshot, "game_state_seed", None))
    points = merge_catalog(snapshot, catalog)
    known_ids = {point.id for point in points}
    for point in snapshot.deployables + snapshot.players:
        if point.id not in known_ids:
            points.append(point)
            known_ids.add(point.id)
    from .missions import build_missions
    return {"snapshot": snapshot, "catalog": catalog, "points": points,
            "missions": build_missions(snapshot)}


class SnapshotWorker(QRunnable):
    def __init__(self, generation, path):
        super().__init__()
        self.generation, self.path = generation, path
        self.signals = WorkerSignals()

    def run(self):
        try:
            from .saves import load_snapshot
            from .settings import app_dir
            snapshot = load_snapshot(self.path, cache_dir=app_dir() / "snapshots")
            self.signals.success.emit(self.generation, snapshot_payload(snapshot))
        except Exception as exc:
            logging.exception("Snapshot load failed")
            self.signals.failure.emit(self.generation, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


class RemoteSnapshotWorker(QRunnable):
    def __init__(self, generation, cache_dir):
        super().__init__()
        self.generation, self.cache_dir = generation, cache_dir
        self.signals = WorkerSignals()

    def run(self):
        try:
            from .release_sync import load_remote_snapshot
            snapshot = load_remote_snapshot(self.cache_dir)
            self.signals.success.emit(self.generation, snapshot_payload(snapshot))
        except Exception as exc:
            logging.exception("Remote world load failed")
            self.signals.failure.emit(self.generation, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


class SharedMapWorker(QRunnable):
    def __init__(self, generation):
        super().__init__()
        self.generation = generation
        self.signals = WorkerSignals()

    def run(self):
        try:
            from .shared_profile import build_shared_payload
            self.signals.success.emit(self.generation, build_shared_payload())
        except Exception as exc:
            logging.exception("Shared map load failed")
            self.signals.failure.emit(self.generation, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


class SnapshotController(QObject):
    loaded = Signal(object)
    state = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.shared_map = False
        self.remote_world = False
        self.remote_cache_dir = None
        self.closed = False
        self.generation = 0
        self.running = False
        self.dirty = False
        self.last_signature = None
        self.last_attempt_signature = None
        self.profile_path = None
        self.last_profile_signature = None
        self.last_attempt_profile_signature = None
        self.attempts = 0
        self.workers = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.changed)
        self.watcher.directoryChanged.connect(self.changed)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(1200)
        self.debounce.timeout.connect(self.refresh)
        self.poll = QTimer(self)
        self.poll.setInterval(5000)
        self.poll.timeout.connect(self.poll_source)
        self.poll.start()
        # Remote requests have their own bounded cadence. Never let the local
        # five-second fallback or cache file notifications cause API polling.
        self.remote_poll = QTimer(self)
        self.remote_poll.setSingleShot(True)
        self.remote_poll.setInterval(120000)
        self.remote_poll.timeout.connect(self.refresh)

    def signature(self):
        if not self.path:
            return None
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def profile_signature(self):
        if not self.profile_path or self.shared_map or self.remote_world:
            return None
        try:
            stat = self.profile_path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def set_path(self, path):
        if self.closed:
            return
        self.generation += 1
        self.shared_map = False
        self.remote_world = False
        self.remote_cache_dir = None
        self.remote_poll.stop()
        self.path = Path(path)
        from .operation_eligibility import source_profile_path
        self.profile_path = source_profile_path(self.path)
        self.last_signature = None
        self.last_attempt_signature = None
        self.last_profile_signature = self.last_attempt_profile_signature = None
        self.attempts = 0
        self.dirty = False
        self.debounce.stop()
        old = self.watcher.files() + self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        self.ensure_watch()
        self.refresh()

    def set_shared_map(self):
        if self.closed:
            return
        self.generation += 1
        self.path = None
        self.shared_map = True
        self.remote_world = False
        self.remote_cache_dir = None
        self.remote_poll.stop()
        self.last_signature = self.last_attempt_signature = None
        self.profile_path = None
        self.last_profile_signature = self.last_attempt_profile_signature = None
        self.attempts = 0
        self.dirty = False
        self.debounce.stop()
        watched = self.watcher.files() + self.watcher.directories()
        if watched:
            self.watcher.removePaths(watched)
        self.refresh()

    def set_remote_world(self, cache_dir=None):
        """Follow the public Release world, without watching its cache as a save."""
        if self.closed:
            return
        self.generation += 1
        self.path = None
        self.shared_map = False
        self.remote_world = True
        self.remote_cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.last_signature = self.last_attempt_signature = None
        self.profile_path = None
        self.last_profile_signature = self.last_attempt_profile_signature = None
        self.attempts = 0
        self.dirty = False
        self.debounce.stop()
        self.remote_poll.stop()
        watched = self.watcher.files() + self.watcher.directories()
        if watched:
            self.watcher.removePaths(watched)
        self.refresh()

    def ensure_watch(self):
        if self.closed or not self.path:
            return
        wanted = [str(self.path.parent)]
        if self.path.exists():
            wanted.append(str(self.path))
        if self.profile_path is not None:
            if self.profile_path.parent.exists():
                wanted.append(str(self.profile_path.parent))
            if self.profile_path.exists():
                wanted.append(str(self.profile_path))
        current = self.watcher.files() + self.watcher.directories()
        for path in wanted:
            if path not in current:
                self.watcher.addPath(path)

    def changed(self, *_):
        if self.closed or self.remote_world or self.shared_map or not self.path:
            return
        self.ensure_watch()
        signature = self.signature()
        profile_signature = self.profile_signature()
        if signature == self.last_signature and profile_signature == self.last_profile_signature:
            return
        if (signature, profile_signature) != (self.last_attempt_signature, self.last_attempt_profile_signature):
            self.attempts = 0
        elif self.attempts >= 4:
            return
        self.state.emit("Save or account mission data changed; waiting for writing to finish…")
        self.debounce.start()

    def poll_source(self):
        if self.closed or self.remote_world or not self.path:
            return
        self.ensure_watch()
        signature = self.signature()
        profile_signature = self.profile_signature()
        if signature is None:
            self.state.emit("Source missing or unreadable; keeping the last valid snapshot (stale data)")
        elif ((signature, profile_signature) != (self.last_signature, self.last_profile_signature)
              and not self.running and not self.debounce.isActive()
              and ((signature, profile_signature) != (self.last_attempt_signature, self.last_attempt_profile_signature)
                   or self.attempts < 4)):
            self.changed()

    def refresh(self):
        if self.closed or (not self.path and not self.shared_map and not self.remote_world):
            return
        if self.remote_world:
            self.remote_poll.stop()
        if self.running:
            self.dirty = True
            return
        self.running = True
        self.last_attempt_signature = self.signature()
        self.last_attempt_profile_signature = self.profile_signature()
        self.dirty = False
        generation = self.generation
        worker = (RemoteSnapshotWorker(generation, self.remote_cache_dir) if self.remote_world else
                  SharedMapWorker(generation) if self.shared_map else SnapshotWorker(generation, self.path))
        self.workers[generation] = worker
        worker.signals.success.connect(self.success)
        worker.signals.failure.connect(self.failure)
        self.state.emit("Syncing public world save in the background…" if self.remote_world else
                        "Loading shared map in the background…" if self.shared_map else "Loading save, resources and missions in the background…")
        self.pool.start(worker)

    def success(self, generation, payload):
        self.running = False
        self.workers.pop(generation, None)
        if self.closed:
            return
        if generation != self.generation:
            self.refresh()
            return
        snapshot = payload["snapshot"]
        if self.remote_world:
            self.attempts = 0
            self.loaded.emit(payload)
            if self.closed or generation != self.generation or not self.remote_world:
                return
            if getattr(snapshot, "remote_stale", False):
                self.state.emit("Public world sync failed · Showing last valid save (stale); retry in two minutes")
            else:
                self.state.emit("Public world synced · Checked every two minutes; showing the host's last uploaded save")
            if self.dirty:
                self.refresh()
            else:
                self.remote_poll.start()
            return
        if getattr(snapshot, "is_shared_map", False):
            self.attempts = 0
            self.loaded.emit(payload)
            self.state.emit("Shared map (no host save connected) · Static / candidate locations; select a host save for world status")
            if self.dirty:
                self.debounce.start()
            return
        self.last_signature = (getattr(snapshot, "source_mtime_ns", round(snapshot.source_mtime * 1e9)), getattr(snapshot, "source_size", 0))
        account = getattr(snapshot, "operation_account", {}) or {}
        read_profile_signature = account.get("profile_signature") if isinstance(account, dict) else None
        self.last_profile_signature = (tuple(read_profile_signature) if isinstance(read_profile_signature, (list, tuple))
                                       and len(read_profile_signature) == 2 else self.last_attempt_profile_signature)
        # The parser may expose only floating mtime. Avoid endless refresh from rounding.
        current = self.signature()
        if not getattr(snapshot, "source_mtime_ns", 0) and current and abs(current[0] / 1e9 - snapshot.source_mtime) < 0.000002 and current[1] == self.last_signature[1]:
            self.last_signature = current
        self.attempts = 0
        self.debounce.setInterval(1200)
        self.loaded.emit(payload)
        if self.closed or generation != self.generation:
            return
        self.state.emit("Watching · Updates when the save changes; showing the last valid snapshot")
        if (self.dirty or self.signature() != self.last_signature
                or self.profile_signature() != self.last_profile_signature):
            self.debounce.start()

    def failure(self, generation, message):
        self.running = False
        self.workers.pop(generation, None)
        if self.closed:
            return
        if generation != self.generation:
            self.refresh()
            return
        self.attempts += 1
        self.failed.emit(message)
        if self.closed or generation != self.generation:
            return
        if self.remote_world:
            self.state.emit("Public world download failed · Keeping last valid data, if available; retry in two minutes")
            self.remote_poll.start()
            return
        if self.shared_map:
            self.state.emit("Shared map load failed · Keeping the last valid map; click Refresh to retry" if self.attempts >= 4 else f"Shared map load incomplete; retrying ({self.attempts}/4)…")
        else:
            self.state.emit("Load failed · Last valid data is stale; waiting for a valid save" if self.attempts >= 4 else f"Load incomplete; retrying ({self.attempts}/4)…")
        if self.attempts < 4:
            self.debounce.start(1500 * self.attempts)

    def close(self):
        self.closed = True
        self.path = None
        self.profile_path = None
        self.last_profile_signature = self.last_attempt_profile_signature = None
        self.poll.stop()
        self.remote_poll.stop()
        self.debounce.stop()
        self.generation += 1
        watched = self.watcher.files() + self.watcher.directories()
        if watched:
            self.watcher.removePaths(watched)
        self.pool.waitForDone(10000)
