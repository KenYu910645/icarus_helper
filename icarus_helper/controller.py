"""Debounced file watching and generation-isolated background snapshot loading."""
from __future__ import annotations

import traceback
import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QRunnable, QThreadPool, QTimer, Signal


class WorkerSignals(QObject):
    success = Signal(int, object)
    failure = Signal(int, str)


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
            from .catalog import load_catalog, merge_catalog
            catalog = load_catalog(snapshot.map_name, world_seed=getattr(snapshot, "game_state_seed", None))
            points = merge_catalog(snapshot, catalog)
            known_ids = {point.id for point in points}
            for point in snapshot.deployables + snapshot.players:
                if point.id not in known_ids:
                    points.append(point)
                    known_ids.add(point.id)
            from .missions import build_missions
            missions = build_missions(snapshot)
            self.signals.success.emit(self.generation, {"snapshot": snapshot, "catalog": catalog, "points": points, "missions": missions})
        except Exception as exc:
            logging.exception("Snapshot load failed")
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
        self.closed = False
        self.generation = 0
        self.running = False
        self.dirty = False
        self.last_signature = None
        self.last_attempt_signature = None
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

    def signature(self):
        if not self.path:
            return None
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def set_path(self, path):
        if self.closed:
            return
        self.generation += 1
        self.shared_map = False
        self.path = Path(path)
        self.last_signature = None
        self.last_attempt_signature = None
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
        self.last_signature = self.last_attempt_signature = None
        self.attempts = 0
        self.dirty = False
        self.debounce.stop()
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
        current = self.watcher.files() + self.watcher.directories()
        for path in wanted:
            if path not in current:
                self.watcher.addPath(path)

    def changed(self, *_):
        if self.closed:
            return
        self.ensure_watch()
        signature = self.signature()
        if signature == self.last_signature:
            return
        if signature != self.last_attempt_signature:
            self.attempts = 0
        elif self.attempts >= 4:
            return
        self.state.emit("偵測到存檔變動，等待寫入完成…")
        self.debounce.start()

    def poll_source(self):
        if self.closed or not self.path:
            return
        self.ensure_watch()
        signature = self.signature()
        if signature is None:
            self.state.emit("來源不存在／無法讀取；保留上次成功快照（資料過期）")
        elif signature != self.last_signature and not self.running and not self.debounce.isActive() and (signature != self.last_attempt_signature or self.attempts < 4):
            self.changed()

    def refresh(self):
        if self.closed or (not self.path and not self.shared_map):
            return
        if self.running:
            self.dirty = True
            return
        self.running = True
        self.last_attempt_signature = self.signature()
        self.dirty = False
        generation = self.generation
        worker = SharedMapWorker(generation) if self.shared_map else SnapshotWorker(generation, self.path)
        self.workers[generation] = worker
        worker.signals.success.connect(self.success)
        worker.signals.failure.connect(self.failure)
        self.state.emit("正在背景讀取共用地圖…" if self.shared_map else "正在背景讀取存檔、資源與任務…")
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
        if getattr(snapshot, "is_shared_map", False):
            self.attempts = 0
            self.loaded.emit(payload)
            self.state.emit("共用地圖（未連接主機存檔）· 靜態資源／候選點；最新狀態請選擇主機存檔")
            if self.dirty:
                self.debounce.start()
            return
        self.last_signature = (getattr(snapshot, "source_mtime_ns", round(snapshot.source_mtime * 1e9)), getattr(snapshot, "source_size", 0))
        # The parser may expose only floating mtime. Avoid endless refresh from rounding.
        current = self.signature()
        if not getattr(snapshot, "source_mtime_ns", 0) and current and abs(current[0] / 1e9 - snapshot.source_mtime) < 0.000002 and current[1] == self.last_signature[1]:
            self.last_signature = current
        self.attempts = 0
        self.debounce.setInterval(1200)
        self.loaded.emit(payload)
        self.state.emit("監看中 · 存檔變更後自動更新；目前顯示最後成功快照")
        if self.dirty or self.signature() != self.last_signature:
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
        if self.shared_map:
            self.state.emit("共用地圖讀取失敗 · 保留上次成功地圖，可按重新整理重試" if self.attempts >= 4 else f"共用地圖讀取未完成，將重試（{self.attempts}/4）…")
        else:
            self.state.emit("讀取失敗 · 上次成功資料已過期；等待有效存檔" if self.attempts >= 4 else f"讀取未完成，將重試（{self.attempts}/4）…")
        if self.attempts < 4:
            self.debounce.start(1500 * self.attempts)

    def close(self):
        self.closed = True
        self.path = None
        self.poll.stop()
        self.debounce.stop()
        self.generation += 1
        watched = self.watcher.files() + self.watcher.directories()
        if watched:
            self.watcher.removePaths(watched)
        self.pool.waitForDone(10000)
