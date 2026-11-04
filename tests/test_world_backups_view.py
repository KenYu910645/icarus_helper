"""History selection and destructive actions keep the captured world and source hash."""
from copy import deepcopy
from datetime import datetime, timezone
import html
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from icarus_helper.world_backups_view import WorldBackupsPanel, player_summary, size_text, time_text


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def snapshot(tmp_path, world="one"):
    return {"world_id": world, "name": f"世界 {world}", "map_name": "Prometheus",
            "source_path": tmp_path / (world + ".json"), "sha256": "current-" + world}


class FakeStore:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.configs = {}
        self.calls = []
        self.fail_read = False
        self.fail_restore = False
        self.preview_count = 2
        self.gate_world = None
        self.started, self.release = threading.Event(), threading.Event()

    def register(self, snap):
        self.calls.append(("register", snap["world_id"], threading.get_ident()))
        config = self.configs.setdefault(snap["world_id"], {"world_id": snap["world_id"],
            "name": snap["name"], "map_name": snap["map_name"], "source": str(snap["source_path"]),
            "backup_dir": str(self.root / snap["world_id"]), "retention_days": 0})
        return deepcopy(config)

    def summary(self, world):
        self.calls.append(("summary", world))
        if world == self.gate_world:
            self.started.set()
            self.release.wait(3)
        if self.fail_read:
            raise ValueError("測試讀取失敗")
        config = self.configs[world]
        versions = [{"id": world + "-new", "path": str(self.root / world / "new.json"),
                     "saved_at": "2026-11-02T06:00:00+00:00", "captured_at": "2026-11-02T06:01:00+00:00",
                     "size": 1780666, "verified": True, "error": "", "protected": True, "sha256": "new-hash"},
                    {"id": world + "-bad", "path": str(self.root / world / "bad.json"),
                     "saved_at": None, "captured_at": None, "size": 15, "verified": False,
                     "error": "雜湊不符", "protected": False, "sha256": None}]
        return {"world": deepcopy(config), "context": deepcopy(config), "versions": versions,
                "retention_days": config["retention_days"], "total_bytes": 1780681, "version_count": 2,
                "source_sha256": "current-" + world,
                "status": {"state": "running", "message": "自動備份運作中"}}

    def capture(self, world):
        self.calls.append(("capture", world, threading.get_ident()))

    def retention_preview(self, world, days):
        self.calls.append(("preview", world, days))
        return {"count": self.preview_count if days else 0, "total_bytes": 10000, "days": days, "versions": []}

    def set_retention(self, world, days):
        self.calls.append(("retention", world, days))
        self.configs[world]["retention_days"] = days

    def prune(self, world):
        self.calls.append(("prune", world))
        return {"deleted": [f"old-{index}.json" for index in range(self.preview_count)]}

    def restore(self, world, version, *, expected_source_sha256=None, expected_backup_sha256=None):
        self.calls.append(("restore", world, version, expected_source_sha256, expected_backup_sha256))
        if self.fail_restore:
            raise ValueError("遊戲仍在執行，拒絕還原")
        return {"world_id": world, "version": version, "safety_backup": "before.json"}


def make_panel(app, tmp_path):
    store = FakeStore(tmp_path)
    panel = WorldBackupsPanel(store)
    panel.set_snapshot(snapshot(tmp_path))
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    return panel, store


def test_disconnected_and_shared_map_never_register_or_enable_mutation(app, tmp_path):
    store = FakeStore(tmp_path)
    panel = WorldBackupsPanel(store)
    panel.set_snapshot({"world_id": "shared", "is_shared_map": True})
    app.processEvents()
    assert not store.calls and panel.summary is None
    assert not panel.capture_button.isEnabled() and not panel.restore_button.isEnabled()
    assert not panel.apply_button.isEnabled() and panel.table.rowCount() == 0
    panel.shutdown()


def test_history_shows_distinct_times_sizes_and_only_verified_version_can_restore(app, tmp_path):
    panel, store = make_panel(app, tmp_path)
    assert panel.table.rowCount() == 2
    assert panel.table.item(0, 0).text() != panel.table.item(0, 1).text()
    assert panel.table.item(0, 2).text() == "1.70 MiB"
    assert panel.table.item(1, 0).text() == "Time unknown"
    assert "one.json" in panel.target_label.text()
    panel.table.selectRow(0)
    assert panel.restore_button.isEnabled()
    panel.refresh()
    wait_for(app, lambda: not panel.busy)
    assert panel.selected_version()["id"] == "one-new"
    panel.table.selectRow(1)
    assert not panel.restore_button.isEnabled()
    assert "雜湊不符" in panel.detail_label.text()
    panel.shutdown()


def test_manual_capture_runs_away_from_gui_thread_and_refreshes(app, tmp_path):
    panel, store = make_panel(app, tmp_path)
    gui_thread = threading.get_ident()
    panel.capture_now()
    wait_for(app, lambda: not panel.busy)
    capture = next(call for call in store.calls if call[0] == "capture")
    assert capture[:2] == ("capture", "one") and capture[2] != gui_thread
    assert "backed up" in panel.status.text()
    panel.shutdown()


def test_restore_confirmation_captures_world_version_and_current_hash(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    panel.table.selectRow(0)
    receipts = []
    panel.restored.connect(receipts.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    panel.restore_selected()
    wait_for(app, lambda: not panel.busy)
    assert ("restore", "one", "one-new", "current-one", "new-hash") in store.calls
    assert receipts == [{"world_id": "one", "version": "one-new", "safety_backup": "before.json"}]
    panel.shutdown()


def test_game_running_rejection_keeps_list_and_does_not_emit_restored(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    store.fail_restore = True
    panel.table.selectRow(0)
    receipts = []
    panel.restored.connect(receipts.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    panel.restore_selected()
    wait_for(app, lambda: not panel.busy)
    assert not receipts and panel.table.rowCount() == 2
    assert "拒絕還原" in panel.status.text()
    panel.shutdown()


def test_completed_restore_warning_still_notifies_parent_to_reload(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    store.restore = lambda *args, **kwargs: {"status": "restored_warning", "warning": "存檔已替換，但收據更新失敗"}
    panel.table.selectRow(0)
    receipts, warnings = [], []
    panel.restored.connect(receipts.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args[2]))
    panel.restore_selected()
    wait_for(app, lambda: not panel.busy)
    assert receipts[0]["status"] == "restored_warning"
    assert warnings == ["存檔已替換，但收據更新失敗"]
    assert "restored" in panel.status.text()
    panel.shutdown()


def test_restored_file_success_is_not_lost_if_post_restore_summary_fails(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    original_restore = store.restore
    def restore(*args, **kwargs):
        receipt = original_restore(*args, **kwargs)
        store.fail_read = True
        return receipt
    store.restore = restore
    panel.table.selectRow(0)
    receipts, warnings = [], []
    panel.restored.connect(receipts.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args[2]))
    panel.restore_selected()
    wait_for(app, lambda: not panel.busy)
    assert len(receipts) == 1 and "The backup list could not be updated" in warnings[0]
    assert "restored" in panel.status.text() and panel.table.rowCount() == 2
    panel.shutdown()


def test_world_change_during_confirmation_cannot_restore_either_world(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    panel.table.selectRow(0)
    def answer(*args, **kwargs):
        panel.set_snapshot(snapshot(tmp_path, "two"))
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", answer)
    panel.restore_selected()
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    assert not any(call[0] == "restore" for call in store.calls)
    assert panel.summary["world"]["world_id"] == "two"
    panel.shutdown()


def test_retention_preview_cancel_preserves_policy_and_approve_applies_selected_days(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    panel.retention_spin.setValue(7)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    panel.apply_retention()
    wait_for(app, lambda: not panel.busy)
    assert not any(call[0] in ("retention", "prune") for call in store.calls)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    panel.apply_retention()
    wait_for(app, lambda: not panel.busy and panel.summary["retention_days"] == 7)
    assert ("retention", "one", 7) in store.calls and ("prune", "one") in store.calls
    assert "2 expired" in panel.status.text()
    panel.shutdown()


def test_switching_world_during_slow_read_discards_old_history(app, tmp_path):
    store = FakeStore(tmp_path)
    store.gate_world = "old"
    panel = WorldBackupsPanel(store)
    panel.set_snapshot(snapshot(tmp_path, "old"))
    wait_for(app, store.started.is_set)
    panel.set_snapshot(snapshot(tmp_path, "new"))
    assert panel.summary is None and panel.table.rowCount() == 0
    store.release.set()
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    assert panel.summary["world"]["world_id"] == "new"
    assert panel.table.item(0, 4).data(256)["id"] == "new-new"
    assert panel.table.item(0, 4).text() == "Saved player data unknown"
    panel.shutdown()


def test_refresh_failure_preserves_last_good_history_and_unsaved_retention_input(app, tmp_path):
    panel, store = make_panel(app, tmp_path)
    panel.retention_spin.setValue(14)
    panel.refresh()
    wait_for(app, lambda: not panel.busy)
    assert panel.retention_spin.value() == 14
    previous = panel.summary
    store.fail_read = True
    panel.refresh()
    wait_for(app, lambda: not panel.busy)
    assert panel.summary is previous and panel.table.rowCount() == 2
    assert "last successful" in panel.status.text()
    panel.shutdown()


def test_dates_without_timezone_and_missing_sizes_remain_unknown():
    assert time_text("2026-11-02T11:11:11") == "Time unknown"
    assert time_text(datetime(2026, 11, 2, tzinfo=timezone.utc)) != "Time unknown"
    assert size_text(None) == "Size unknown" and size_text(True) == "Size unknown"


def test_real_store_widget_restores_only_isolated_fixture_and_keeps_safety_copy(app, tmp_path, monkeypatch):
    from icarus_helper import world_backups
    from icarus_helper.saves import load_snapshot
    from test_saves import save_fixture

    monkeypatch.setattr(world_backups, "game_processes", lambda: [])
    source = tmp_path / "獨立測試帳號" / "Prospects" / "世界.json"
    def write(marker):
        save_fixture(source, records=[("/Script/Icarus.GameModeStateRecorderComponent",
                                      {"GameModeRecord": {"GameStateSeed": 919}, "Marker": marker})])
        return load_snapshot(source)
    first = write(1)
    original = source.read_bytes()
    store = world_backups.BackupStore(tmp_path / "獨立備份")
    panel = WorldBackupsPanel(store)
    panel.set_snapshot(first)
    wait_for(app, lambda: panel.summary is not None and not panel.busy)
    panel.capture_now()
    wait_for(app, lambda: not panel.busy and panel.table.rowCount() == 1)
    first_id = panel.table.item(0, 0).data(256)["id"]
    second = write(2)
    latest = source.read_bytes()
    assert latest != original
    panel.set_snapshot(second)
    wait_for(app, lambda: not panel.busy)
    panel.capture_now()
    wait_for(app, lambda: not panel.busy and panel.table.rowCount() == 2)
    old_row = next(row for row in range(panel.table.rowCount()) if panel.table.item(row, 0).data(256)["id"] == first_id)
    panel.table.selectRow(old_row)
    receipts = []
    panel.restored.connect(receipts.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    panel.restore_selected()
    wait_for(app, lambda: not panel.busy)
    assert source.read_bytes() == original and len(receipts) == 1
    safety = list((store.root / "Restores").rglob("*.json"))
    assert any(path.read_bytes() == latest for path in safety)
    assert receipts[0]["status"] == "restored"
    panel.shutdown()


def player_preview(name="甲角色", health=563, alive=True, playing=True):
    return {"schema_version": 1, "status": "available", "source": "backup",
            "sample_time": "2026-11-02T06:00:00+00:00", "warnings": [],
            "players": [{"id": "anonymous-player", "name": name, "slot": 0,
                         "health": health, "alive": alive, "is_currently_playing": playing,
                         "food": 0, "water": 5, "oxygen": 100, "radiation": 0,
                         "respawn_count": 0, "position": [-1234, 5678, 0], "issues": []}]}


def test_player_details_keep_zero_false_and_escape_saved_names(app, tmp_path):
    panel, _ = make_panel(app, tmp_path)
    summary = deepcopy(panel.summary)
    version = summary["versions"][0]
    version["player_preview"] = player_preview("<b>角色 & 名稱</b>", health=0, alive=False, playing=False)
    panel._set_summary(summary)
    panel.table.selectRow(0)
    brief = panel.table.item(0, 4).text()
    details = panel.player_detail.toPlainText()
    assert "HP 0" in brief and "Dead" in brief and "Last saved value" in brief
    assert "<b>角色 & 名稱</b>" in details
    assert "Saved health: 0" in details and "Respawns 0" in details and "Radiation 0" in details
    assert "Food 0" in details and "X -1,234" in details and "Z 0 cm" in details
    assert "Playing flag: no (last saved value)" in details
    assert "percentage" not in details and "%" not in details
    assert "&lt;b&gt;角色 &amp; 名稱&lt;/b&gt;" in panel.player_detail.toHtml()
    panel.shutdown()


def test_changing_versions_does_not_retain_player_health_from_previous_version(app, tmp_path):
    panel, _ = make_panel(app, tmp_path)
    summary = deepcopy(panel.summary)
    first = summary["versions"][0]
    first["player_preview"] = player_preview("甲角色", 563)
    second = dict(first, id="older", player_preview=player_preview("乙角色", 0, alive=False, playing=False))
    third = dict(first, id="unknown", player_preview=None)
    summary["versions"] = [first, second, third]
    panel._set_summary(summary)
    panel.table.selectRow(0)
    assert "563" in panel.player_detail.toPlainText()
    panel.table.selectRow(1)
    assert "乙角色" in panel.player_detail.toPlainText() and "Dead" in panel.player_detail.toPlainText()
    assert "563" not in panel.player_detail.toPlainText() and "甲角色" not in panel.player_detail.toPlainText()
    panel.table.selectRow(2)
    assert "unknown" in panel.player_detail.toPlainText().lower()
    assert "乙角色" not in panel.player_detail.toPlainText()
    panel.clear()
    assert "player backup data" in panel.player_detail.toPlainText() and "563" not in panel.player_detail.toPlainText()
    panel.shutdown()


def test_restore_confirmation_uses_selected_backup_player_values_and_escapes_text(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    summary = deepcopy(panel.summary)
    summary["versions"][0]["player_preview"] = player_preview("<b>舊角色</b>", 321, alive=False, playing=False)
    panel.snapshot["health"] = 999
    panel._set_summary(summary)
    panel.table.selectRow(0)
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: questions.append(args[2]) or QMessageBox.StandardButton.No)
    panel.restore_selected()
    assert len(questions) == 1
    question = questions[0]
    assert "&lt;b&gt;舊角色&lt;/b&gt;" in question and "<b>舊角色</b>" not in question
    assert "HP 321" in question and "Dead" in question and "Last saved value" in question
    assert "999" not in question and "one.json" in html.unescape(question)
    assert not any(call[0] == "restore" for call in store.calls)
    panel.shutdown()


def test_unknown_flags_and_invalid_values_never_infer_death_or_zero(app, tmp_path):
    panel, _ = make_panel(app, tmp_path)
    summary = deepcopy(panel.summary)
    version = summary["versions"][0]
    version["player_preview"] = player_preview("Unknown角色", None, alive=None, playing=None)
    version["player_preview"]["players"][0].update(food=float("nan"), water=True, position=[float("inf"), 0, 0])
    panel._set_summary(summary)
    panel.table.selectRow(0)
    details = panel.player_detail.toPlainText()
    assert "Saved health: Unknown" in details and "Survival status unknown" in details
    assert "Playing flag unknown" in details and "Food Unknown" in details and "Water Unknown" in details
    assert "Position unknown" in details and "Dead" not in details
    # A zero health value without a death flag is not proof of death.
    version["player_preview"]["players"][0]["health"] = 0
    assert "HP 0" in player_summary(version) and "Dead" not in player_summary(version)
    panel.shutdown()


def test_version_hash_changed_during_confirmation_cancels_restore(app, tmp_path, monkeypatch):
    panel, store = make_panel(app, tmp_path)
    panel.table.selectRow(0)
    def answer(*args, **kwargs):
        replacement = deepcopy(panel.summary)
        replacement["versions"][0]["sha256"] = "changed-after-preview"
        panel._set_summary(replacement)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", answer)
    panel.restore_selected()
    assert not any(call[0] == "restore" for call in store.calls)
    assert "selected version has changed" in panel.status.text()
    panel.shutdown()


def test_unverified_or_nonbackup_preview_cannot_show_trusted_player_details(app, tmp_path):
    panel, _ = make_panel(app, tmp_path)
    summary = deepcopy(panel.summary)
    summary["versions"][1]["player_preview"] = player_preview("不可信角色", 888)
    panel._set_summary(summary)
    panel.table.selectRow(1)
    assert "888" not in panel.player_detail.toPlainText() and "unknown" in panel.player_detail.toPlainText().lower()
    version = summary["versions"][0]
    version["player_preview"] = player_preview("目前遊戲角色", 999)
    version["player_preview"]["source"] = "live"
    assert player_summary(version) == "Saved player data unknown"
    panel.shutdown()


def test_selected_health_bar_resources_change_and_release_with_version_and_world(app, tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtGui import QImage, QTextDocument
    from shiboken6 import isValid
    panel, _ = make_panel(app, tmp_path)
    summary = deepcopy(panel.summary)
    first = summary["versions"][0]
    first["player_preview"] = player_preview("甲角色", 563)
    second = dict(first, id="empty", player_preview=player_preview("乙角色", 0, alive=False, playing=False))
    summary["versions"] = [first, second]
    panel._set_summary(summary)
    panel.table.selectRow(0)
    resource = QTextDocument.ResourceType.ImageResource
    url = QUrl("backup-health://selected/0")
    old_document = panel.player_detail.document()
    saved_image = old_document.resource(resource, url)
    assert isinstance(saved_image, QImage) and not saved_image.isNull()
    assert "563" in panel.player_detail.toPlainText()
    panel.table.selectRow(1)
    document = panel.player_detail.document()
    empty_image = document.resource(resource, url)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(old_document) and isinstance(empty_image, QImage)
    assert saved_image != empty_image
    assert "563" not in panel.player_detail.toPlainText() and "Dead" in panel.player_detail.toPlainText()
    panel.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(document)
    assert panel.player_detail.document().resource(resource, url) is None
    panel.shutdown()


def test_history_draws_health_bars_without_cell_widgets_or_losing_model_values(app, tmp_path):
    from icarus_helper.backup_health_bars import PlayerHealthDelegate
    panel, _ = make_panel(app, tmp_path)
    panel.resize(1100, 760)
    panel.show()
    wait_for(app, lambda: not panel.busy)
    summary = deepcopy(panel.summary)
    version = summary["versions"][0]
    version["player_preview"] = player_preview("甲角色", 563)
    version["player_preview"]["players"].append(dict(version["player_preview"]["players"][0], name="乙角色", health=0, alive=False))
    panel._set_summary(summary)
    app.processEvents()
    assert isinstance(panel.table.itemDelegateForColumn(4), PlayerHealthDelegate)
    assert panel.table.cellWidget(0, 4) is None
    assert panel.table.rowHeight(0) >= 80
    assert "HP 563" in panel.table.item(0, 4).text() and "HP 0" in panel.table.item(0, 4).text()
    assert not panel.table.viewport().grab().toImage().isNull()
    panel.table.selectRow(0)
    assert panel.restore_button.isEnabled()
    panel.close()
