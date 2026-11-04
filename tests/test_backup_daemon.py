"""Daemon ticks stay isolated and prune independently of source changes."""
from pathlib import Path
import json

from icarus_helper.backup_daemon import BackupRunner, _singleton


class FakeStore:
    def __init__(self, root):
        self.root = root
        self.source = root / "合成來源.json"
        self.source.write_text("synthetic", encoding="utf-8")
        self.calls = []

    def worlds(self):
        return [{"world_id": "one", "source": str(self.source)}]

    def capture(self, ident):
        self.calls.append(("capture", ident))
        return {"id": "synthetic"}

    def prune(self, ident):
        self.calls.append(("prune", ident))
        return {"deleted": []}


def test_unchanged_source_still_prunes(tmp_path):
    store = FakeStore(tmp_path)
    runner = BackupRunner(store)
    runner.tick(0)
    runner.tick(5)
    runner.tick(61)
    assert [call[0] for call in store.calls] == ["capture", "prune", "capture", "prune"]
    data = json.loads((tmp_path / "daemon-status.json").read_text(encoding="utf-8"))
    assert data["worlds"]["one"]["last_success"]
    assert data["heartbeat_at_utc"].endswith("+00:00")


def test_missing_source_retries_and_retains_status(tmp_path):
    store = FakeStore(tmp_path)
    runner = BackupRunner(store)
    store.source.unlink()
    assert runner.tick(0)["worlds"]["one"]["last_error"]
    assert store.calls == [("prune", "one")]
    store.source.write_text("new", encoding="utf-8")
    assert "last_error" not in runner.tick(1)["worlds"]["one"]


def test_singleton_rejects_second_daemon(tmp_path):
    with _singleton(tmp_path) as first:
        assert first
        with _singleton(tmp_path) as second:
            assert not second
