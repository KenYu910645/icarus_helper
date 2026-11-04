"""Published copies cannot acquire local restore or process-reading authority."""
from datetime import datetime, timezone
from types import SimpleNamespace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from icarus_helper.models import WorldSnapshot
from icarus_helper.settings import Settings
from icarus_helper.world_backups import BackupStore, BackupError
from icarus_helper.world_backups_view import WorldBackupsPanel
from icarus_helper.release_sync import REMOTE_SOURCE


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def remote(tmp_path):
    now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
    snap = WorldSnapshot(tmp_path / "download.json", "published-world", "GuoGuoGaGa", "Prometheus", now, now, "a" * 64)
    snap.is_remote = True
    snap.remote_stale = False
    snap.remote_checked_at = now
    snap.remote_status = "Public sync成功"
    return snap


def test_remote_backups_panel_has_no_write_authority(app, tmp_path):
    panel = WorldBackupsPanel(BackupStore(tmp_path / "backups"))
    panel.set_snapshot(remote(tmp_path))
    assert panel.snapshot is None and not panel._connected()
    assert not panel.restore_button.isEnabled()
    assert "Read-only" in panel.world_label.text()
    assert not (tmp_path / "backups" / "worlds.json").exists()
    panel.shutdown()


def test_store_rejects_remote_even_if_path_is_outside_known_cache(tmp_path):
    with pytest.raises(BackupError, match="read-only"):
        BackupStore(tmp_path / "backups").register(remote(tmp_path))


def test_remote_live_reader_does_not_open_process(monkeypatch, tmp_path):
    from icarus_helper import live_animal_stats as live
    monkeypatch.setattr(live, "_find_game_processes", lambda: pytest.fail("remote process access"))
    assert live.read_live_animal_stats(remote(tmp_path))["status"] == "unavailable"


def test_remote_animal_summary_does_not_mix_local_sources(monkeypatch, tmp_path):
    from icarus_helper import animals, live_animal_stats, owner_stats
    monkeypatch.setattr(live_animal_stats, "read_live_animal_stats", lambda *_: pytest.fail("local live access"))
    monkeypatch.setattr(owner_stats, "_profile_candidates", lambda *_: pytest.fail("local profile access"))
    monkeypatch.setattr(animals, "installed_game_build", lambda: None)
    summary = animals.summarize_animals(remote(tmp_path))
    assert summary["live_stats"]["world_verified"] is False
    assert summary["animals"] == []


def test_remote_ui_selects_background_source_and_preserves_existing_home(app, tmp_path, monkeypatch):
    from icarus_helper import saves, catalog
    from icarus_helper.controller import SnapshotController
    from icarus_helper.ui import MainWindow
    monkeypatch.setattr(saves, "discover_saves", lambda: [])
    monkeypatch.setattr(SnapshotController, "set_shared_map", lambda *_: None)
    calls = []
    monkeypatch.setattr(SnapshotController, "set_remote_world", lambda self, path=None: calls.append(path))
    settings = Settings(tmp_path / "settings.json")
    original = {"id": "custom", "name": "我的Base", "x": 120, "y": 240, "z": 0}
    settings.world("published-world")["bases"] = [original.copy()]
    window = MainWindow(settings=settings)
    app.processEvents()
    window.world_combo.setCurrentIndex(window.world_combo.findData(REMOTE_SOURCE))
    assert calls == [tmp_path / "remote-worlds" / "guoguogaga"]
    monkeypatch.setattr(window, "_ensure_backup_service", lambda: pytest.fail("remote backup daemon"))
    monkeypatch.setattr(window, "update_inventory", lambda: None)
    window.loaded({"snapshot": remote(tmp_path), "catalog": {"map": "Prometheus", "calibrated": False}, "points": [], "missions": []})
    assert window.current_base()["name"] == "我的Base"
    assert settings.world("published-world")["bases"] == [original]
    assert "Public sync" in window.world_label.text()
    assert "Public sync" in window.world_combo.currentText()
    assert not window.world_backups_panel._connected()
    window.snapshot.remote_stale = True
    window.loaded({"snapshot": window.snapshot, "catalog": {"map": "Prometheus", "calibrated": False}, "points": [], "missions": []})
    assert "stale" in window.world_label.text()
    window.close()
    app.processEvents()


def test_remote_animal_timer_waits_for_new_snapshot(app, tmp_path, monkeypatch):
    from icarus_helper.animals_view import AnimalPanel
    panel = AnimalPanel()
    panel.snapshot = remote(tmp_path)
    monkeypatch.setattr(panel, "_start", lambda: pytest.fail("remote resampled locally"))
    panel._refresh_visible()
    panel.shutdown()


def test_old_registry_cannot_restore_source_that_becomes_remote(tmp_path, monkeypatch):
    from icarus_helper import release_sync
    from icarus_helper.saves import load_snapshot
    from test_saves import save_fixture
    source = tmp_path / "Prospects" / "test.json"
    save_fixture(source, records=[('/Script/Icarus.GameModeStateRecorderComponent', {'GameModeRecord': {'GameStateSeed': 918}})])
    snap = load_snapshot(source)
    store = BackupStore(tmp_path / "backups")
    store.register(snap)
    monkeypatch.setattr(release_sync, "is_remote_cache_path", lambda p: str(p) == str(source.resolve()))
    with pytest.raises(BackupError, match="host world source"):
        store.capture(snap.world_id)
