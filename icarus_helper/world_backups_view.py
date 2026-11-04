"""World save history and explicit rollback controls, with isolated background I/O."""
from __future__ import annotations

from datetime import datetime
import html
import math
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from .backup_health_bars import PlayerHealthDelegate, health_bar_image, player_rows_height


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def size_text(size):
    if not isinstance(size, (int, float)) or isinstance(size, bool) or size < 0:
        return "Size unknown"
    if size < 1024:
        return f"{size:,.0f} B"
    for unit, scale in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if size >= scale:
            return f"{size / scale:,.2f} {unit}"
    return "Size unknown"


def time_text(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return "Time unknown"
        return stamp.astimezone().strftime("%Y/%m/%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return "Time unknown"


def number_text(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return "Unknown"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}".rstrip("0").rstrip(".")


def _players(version):
    preview = version.get("player_preview") if isinstance(version, dict) and version.get("verified") else None
    if not isinstance(preview, dict) or preview.get("source") != "backup":
        return {}, []
    players = preview.get("players")
    return preview, [player for player in players if isinstance(player, dict)] if isinstance(players, list) else []


def alive_text(value):
    return "Alive" if value is True else "Dead" if value is False else "Survival status unknown"


def playing_text(value):
    return "Playing flag: yes (saved record)" if value is True else "Playing flag: no (last saved value)" if value is False else "Playing flag unknown"


def player_summary(version):
    """A separate line per saved character allows comparison without truncating names."""
    _, players = _players(version)
    if not players:
        return "Saved player data unknown"
    return "\n".join(
        f"{player.get('name') or 'Player'} · HP {number_text(player.get('health'))} · {alive_text(player.get('alive'))}"
        + (" · Last saved value" if player.get("is_currently_playing") is False else "")
        for player in players)


def _position_text(position):
    if (not isinstance(position, (tuple, list)) or len(position) != 3
            or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in position)):
        return "Position unknown"
    return " / ".join(f"{axis} {value:,.0f}" for axis, value in zip("XYZ", position)) + " cm"


def player_details_html(version, bar_urls=None):
    preview, players = _players(version)
    escape = lambda value: html.escape(str(value))
    if not players:
        return "<p>No verified player data is available for this version; health, survival status and position remain unknown.</p>"
    cells = []
    for player_index, player in enumerate(players):
        name = escape(player.get("name") or "Player")
        slot = player.get("slot")
        slot_text = f" · Character slot {slot}" if isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0 else ""
        issues = player.get("issues") if isinstance(player.get("issues"), list) else []
        bar = (f"<img src='{escape(bar_urls[player_index])}'><br>"
               if bar_urls and player_index < len(bar_urls) else "")
        cell = (f"<td width='50%' valign='top' style='padding:6px 12px 8px 0'><b>{name}{slot_text}</b><br>"
                + bar
                +
                f"Saved health: <b>{number_text(player.get('health'))}</b> · {alive_text(player.get('alive'))}<br>"
                f"{playing_text(player.get('is_currently_playing'))}<br>"
                f"Food {number_text(player.get('food'))} Water {number_text(player.get('water'))} Oxygen {number_text(player.get('oxygen'))}<br>"
                f"Radiation {number_text(player.get('radiation'))} Respawns {number_text(player.get('respawn_count'))}<br>"
                f"{escape(_position_text(player.get('position')))}"
                + ("<br>" + "; ".join(escape(issue) for issue in issues) if issues else "") + "</td>")
        cells.append(cell)
    rows = ["<tr>" + "".join(cells[index:index + 2]) + "</tr>" for index in range(0, len(cells), 2)]
    warnings = preview.get("warnings") if isinstance(preview.get("warnings"), list) else []
    return ("<p style='margin:0'><b>Player Status in Selected Backup</b> · World saved "
            + escape(time_text(preview.get("sample_time") or version.get("saved_at"))) + "</p>"
            + "<table width='100%' cellspacing='0'>" + "".join(rows) + "</table>"
            + ("<p>" + "; ".join(escape(warning) for warning in warnings) + "</p>" if warnings else ""))


class _Signals(QObject):
    finished = Signal(int, int, str, object, str)


class _Task(QRunnable):
    def __init__(self, serial, generation, operation, callback, cancelled):
        super().__init__()
        self.serial, self.generation, self.operation = serial, generation, operation
        self.callback, self.cancelled = callback, cancelled
        self.signals = _Signals()

    def run(self):
        result, error = None, ""
        try:
            if not self.cancelled.is_set():
                result = self.callback()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.signals.finished.emit(self.serial, self.generation, self.operation, result, error)


class WorldBackupsPanel(QWidget):
    """The selected world is captured before each action; all disk work runs serially."""
    restored = Signal(object)
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.snapshot = None
        self.summary = None
        self.last_error = ""
        self.busy = False
        self._closed = False
        self._generation = 0
        self._serial = 0
        self._jobs = {}
        self._pending = False
        self._cancelled = threading.Event()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._build_ui()
        self.poll = QTimer(self)
        self.poll.setInterval(15000)
        self.poll.timeout.connect(self._poll)
        self.poll.start()
        self.clear()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.world_label = QLabel("World Backups · Host save not connected")
        self.world_label.setObjectName("worldInfo")
        self.world_label.setTextFormat(Qt.TextFormat.PlainText)
        top.addWidget(self.world_label, 1)
        self.refresh_button = QPushButton("Refresh Backups")
        self.refresh_button.clicked.connect(self.refresh)
        top.addWidget(self.refresh_button)
        self.capture_button = QPushButton("Back Up Now")
        self.capture_button.clicked.connect(self.capture_now)
        top.addWidget(self.capture_button)
        self.folder_button = QPushButton("Open Backup Folder")
        self.folder_button.clicked.connect(self.open_folder)
        top.addWidget(self.folder_button)
        layout.addLayout(top)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.target_label = QLabel()
        self.target_label.setWordWrap(True)
        self.target_label.setTextFormat(Qt.TextFormat.PlainText)
        self.target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.target_label.setObjectName("muted")
        layout.addWidget(self.target_label)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Backup retention"))
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(0, 36500)
        self.retention_spin.setSpecialValueText("Keep forever")
        self.retention_spin.setSuffix(" days")
        self.retention_spin.setAccessibleName("Backup retention in days; zero keeps backups forever")
        self.retention_spin.setMinimumWidth(140)
        self.retention_spin.valueChanged.connect(self._retention_changed)
        controls.addWidget(self.retention_spin)
        self.apply_button = QPushButton("Apply Retention")
        self.apply_button.clicked.connect(self.apply_retention)
        controls.addWidget(self.apply_button)
        self.retention_label = QLabel("0 days = keep forever")
        self.retention_label.setObjectName("muted")
        controls.addWidget(self.retention_label, 1)
        layout.addLayout(controls)
        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("World Save Backups")
        self.health_delegate = PlayerHealthDelegate(lambda version: _players(version)[1], self.table)
        self.table.setItemDelegateForColumn(4, self.health_delegate)
        self.table.setHorizontalHeaderLabels(["World Save Time", "Backup Created", "File Size", "Verification", "Saved Player Status"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(58)
        header = self.table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        history = QSplitter(Qt.Orientation.Vertical)
        self.table.setMinimumHeight(150)
        history.addWidget(self.table)
        self.player_detail = QTextBrowser()
        self.player_detail.setAccessibleName("Saved Player Status in Selected Backup")
        self.player_detail.setOpenExternalLinks(False)
        self.player_detail.setOpenLinks(False)
        self.player_detail.setMinimumHeight(110)
        self.player_detail.setHtml("<p>Select a version to view the player health, survival status and positions recorded in that backup.</p>")
        history.addWidget(self.player_detail)
        history.setChildrenCollapsible(False)
        history.setSizes([460, 170])
        layout.addWidget(history, 1)
        self.detail_label = QLabel("Select a version to view its save time and restore details.")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail_label)
        bottom = QHBoxLayout()
        note = QLabel("Retention uses the backup creation time. The latest valid version is always kept; safety copies made before restoration are kept forever.\n"
                      "Player data comes from the selected backup. Offline characters may show last saved values; health is not live. Percentages require a verified maximum.\n"
                      "Striped health bars indicate an unknown maximum, not a percentage. Values are from the selected backup.\n"
                      "Restoring rolls back the entire selected world. Close the game/server first. This does not include all account and character data.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        bottom.addWidget(note, 1)
        self.restore_button = QPushButton("Restore Selected Version…")
        self.restore_button.setAccessibleName("Restore Selected World Save Version")
        self.restore_button.setStyleSheet("QPushButton { background:#713d39; border:1px solid #aa6158; padding:10px 16px; } "
                                         "QPushButton:disabled { background:#283139; border-color:#39434c; color:#76838c; }")
        self.restore_button.clicked.connect(self.restore_selected)
        bottom.addWidget(self.restore_button)
        layout.addLayout(bottom)

    def _context(self):
        return (self._generation, _value(self.snapshot, "world_id"), str(_value(self.snapshot, "source_path", "")))

    def _connected(self):
        return self.snapshot is not None and not _value(self.snapshot, "is_shared_map", False) and not _value(self.snapshot, "is_remote", False)

    def set_snapshot(self, snapshot):
        if self._closed:
            return
        if _value(snapshot, "is_remote", False):
            self.clear()
            self.world_label.setText("World Backups · Public Sync Copy (Read-only)")
            self.status.setText("This is a copy downloaded from GitHub Releases. It cannot register local backups or restore the host world.")
            self.detail_label.setText("Manage backup history and restoration on the host holding the original world save.")
            return
        if not snapshot or _value(snapshot, "is_shared_map", False) or not _value(snapshot, "source_path"):
            self.clear()
            return
        changed = (_value(self.snapshot, "world_id"), str(_value(self.snapshot, "source_path", ""))) != (
            _value(snapshot, "world_id"), str(_value(snapshot, "source_path", "")))
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._generation += 1
        self.snapshot = snapshot
        self.world_label.setText(f"World Backups · {_value(snapshot, 'name', 'Selected World')} / {_value(snapshot, 'map_name', '')}")
        self.target_label.setText(f"Restore target: {_value(snapshot, 'source_path')}")
        self.target_label.setToolTip(str(_value(snapshot, "source_path")))
        if changed:
            self.summary = None
            self.table.setRowCount(0)
            self.detail_label.setText("Loading backup history for this world…")
            self._set_player_detail_text("Loading saved player data for this world…")
            self.retention_spin.blockSignals(True)
            self.retention_spin.setValue(0)
            self.retention_spin.blockSignals(False)
        self.refresh()

    def clear(self):
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._generation += 1
        self.snapshot = self.summary = None
        self._pending = False
        self.last_error = ""
        self.world_label.setText("World Backups · Host save not connected")
        self.status.setText("Select an actual world save first. The shared map has no private backups.")
        self.target_label.clear()
        self.table.setRowCount(0)
        self.detail_label.setText("Connect a host save to view backup times and sizes, and select a version to restore.")
        self._set_player_detail_text("No actual world save is selected; player backup data is unavailable.")
        self.retention_spin.blockSignals(True)
        self.retention_spin.setValue(0)
        self.retention_spin.blockSignals(False)
        self.retention_label.setText("0 days = keep forever")
        self._update_buttons()

    set_missing_source = clear

    def _poll(self):
        if self.isVisible() and self._connected() and not self.busy:
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self._connected():
            self.refresh()

    def _begin(self, operation, callback):
        if self._closed or not self._connected() or self.busy:
            return False
        self._serial += 1
        serial = self._serial
        worker = _Task(serial, self._generation, operation, callback, self._cancelled)
        self._jobs[serial] = worker
        self.busy = True
        worker.signals.finished.connect(self._finished)
        self._update_buttons()
        self.pool.start(worker)
        return True

    def refresh(self):
        if not self._connected() or self._closed:
            return
        if self.busy:
            self._pending = True
            return
        snapshot = self.snapshot
        world_id = _value(snapshot, "world_id")
        self.status.setText("Loading and verifying backup history…")
        def read():
            self.store.register(snapshot)
            return self.store.summary(world_id)
        self._begin("refresh", read)

    def _finished(self, serial, generation, operation, result, error):
        self._jobs.pop(serial, None)
        self.busy = False
        if self._closed:
            return
        if generation == self._generation:
            if error:
                self.last_error = error
                self.status.setText(f"Operation failed: {error}" + ("\nKeeping the last successfully loaded backup list." if self.summary else ""))
                self.failed.emit(error)
            elif result is not None:
                self.last_error = ""
                if operation == "preview":
                    self._preview_ready(result)
                elif operation == "restore":
                    if result.get("summary") is not None:
                        self._set_summary(result["summary"])
                    receipt = result["receipt"]
                    warnings = [str(message) for message in (receipt.get("warning"), result.get("refresh_error")) if message]
                    warning = "\n".join(warnings)
                    self.status.setText("World restored; " + warning if warning else "World restored. The previous world was preserved in a safety backup.")
                    self.restored.emit(receipt)
                    if warning:
                        QMessageBox.warning(self, "World Replaced — Verify Restoration", warning)
                elif operation == "retention":
                    self._set_summary(result["summary"])
                    deleted = result["cleanup"].get("deleted", [])
                    count = len(deleted) if isinstance(deleted, (tuple, list)) else deleted
                    self.status.setText(f"Retention applied; cleaned up {count} expired versions.")
                elif operation == "capture":
                    self._set_summary(result["summary"])
                    self.status.setText("The current world has been verified and backed up. Identical content is not saved twice.")
                else:
                    self._set_summary(result)
        self._update_buttons()
        if self._connected() and not self.busy and (self._pending or generation != self._generation):
            self._pending = False
            QTimer.singleShot(0, self.refresh)

    def _set_summary(self, summary):
        selected = self.selected_version()
        selected_id = selected.get("id") if selected else None
        old_days = self.summary.get("retention_days") if self.summary else None
        self.summary = summary
        context = summary.get("world", summary.get("context", {}))
        days = summary.get("retention_days", context.get("retention_days", 0))
        if old_days is None or self.retention_spin.value() == old_days:
            self.retention_spin.blockSignals(True)
            self.retention_spin.setValue(days)
            self.retention_spin.blockSignals(False)
        self._retention_changed()
        versions = summary.get("versions", [])
        self.table.blockSignals(True)
        self.table.setRowCount(len(versions))
        selected_row = -1
        for row, version in enumerate(versions):
            self.table.setRowHeight(row, player_rows_height(_players(version)[1]))
            verified = bool(version.get("verified"))
            status = "Verified" if verified else "Cannot restore / needs verification"
            if version.get("protected"):
                status += " · Protected"
            values = [time_text(version.get("saved_at")), time_text(version.get("captured_at")),
                      size_text(version.get("size")), status, player_summary(version)]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, version)
                item.setToolTip(player_summary(version) + "\nVersion: " + str(version.get("id", ""))
                                + "\n" + str(version.get("error") or version.get("path", "")))
                if column == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
            if version.get("id") == selected_id:
                selected_row = row
        self.table.blockSignals(False)
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
            self.table.clearSelection()
        daemon = summary.get("status", {})
        daemon_text = daemon.get("message", "") if isinstance(daemon, dict) else str(daemon or "")
        count = summary.get("version_count", len(versions))
        self.status.setText(f"{count} versions · Total {size_text(summary.get('total_bytes'))}"
                            + (f" · {daemon_text}" if daemon_text else ""))
        self._selection_changed()
        self.loaded.emit(summary)

    def selected_version(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self):
        version = self.selected_version()
        if version:
            self.detail_label.setText(
                f"World saved: {time_text(version.get('saved_at'))}  /  Backup created: {time_text(version.get('captured_at'))}  /  {size_text(version.get('size'))}\n"
                f"Backup file: {version.get('path', '')}"
                + (("\nNote: " if version.get("verified") else "\nCannot restore: ") + str(version["error"]) if version.get("error") else ""))
            self.detail_label.setToolTip(str(version.get("sha256") or ""))
            self._set_player_details(version)
        else:
            self.detail_label.setText("Select a version to view its save time and restore details." if self.table.rowCount() else "No backups yet. Select “Back Up Now” to save the current valid world.")
            self._set_player_detail_text("Select a version to view the player health, survival status and positions recorded in that backup.")
        self._update_buttons()

    def _set_player_details(self, version):
        document = QTextDocument(self.player_detail)
        document.setDefaultFont(self.player_detail.font())
        _, players = _players(version)
        width = max(110, min(360, (self.player_detail.viewport().width() - 64) // 2))
        urls = []
        for index, player in enumerate(players):
            url = QUrl(f"backup-health://selected/{index}")
            document.addResource(QTextDocument.ResourceType.ImageResource, url, health_bar_image(player, width))
            urls.append(url.toString())
        document.setHtml(player_details_html(version, urls))
        self._replace_player_document(document)

    def _set_player_detail_text(self, text):
        # A fresh owned document releases image resources from the old version.
        document = QTextDocument(self.player_detail)
        document.setDefaultFont(self.player_detail.font())
        document.setPlainText(text)
        self._replace_player_document(document)

    def _replace_player_document(self, document):
        previous = self.player_detail.document()
        self.player_detail.setDocument(document)
        # Qt deletes its original internal document itself; documents installed
        # by us need an explicit deferred release after they leave the browser.
        try:
            previous.deleteLater()
        except RuntimeError:
            pass

    def _retention_changed(self, *_):
        days = self.retention_spin.value()
        self.retention_label.setText("0 days = keep forever" if days == 0 else f"Automatically remove backups created more than {days} days ago")

    def _update_buttons(self):
        ready = self._connected() and not self._closed and not self.busy
        self.refresh_button.setEnabled(ready)
        self.capture_button.setEnabled(ready)
        self.retention_spin.setEnabled(ready and self.summary is not None)
        self.apply_button.setEnabled(ready and self.summary is not None)
        context = (self.summary or {}).get("world", (self.summary or {}).get("context", {}))
        self.folder_button.setEnabled(ready and bool(context.get("backup_dir")))
        selected = self.selected_version()
        self.restore_button.setEnabled(ready and bool(selected and selected.get("verified"))
                                       and bool((self.summary or {}).get("source_sha256")))

    def capture_now(self):
        if self.busy or not self._connected():
            return
        world_id = _value(self.snapshot, "world_id")
        self.status.setText("Creating an independent world backup…")
        def capture():
            self.store.capture(world_id)
            return {"summary": self.store.summary(world_id)}
        self._begin("capture", capture)

    def open_folder(self):
        context = (self.summary or {}).get("world", (self.summary or {}).get("context", {}))
        if context.get("backup_dir"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(context["backup_dir"]))))

    def apply_retention(self):
        if self.busy or not self._connected() or self.summary is None:
            return
        context, days = self._context(), self.retention_spin.value()
        world_id = _value(self.snapshot, "world_id")
        self.status.setText("Calculating expired backups and recoverable space…")
        def preview():
            return {"context": context, "days": days, "preview": self.store.retention_preview(world_id, days)}
        self._begin("preview", preview)

    def _preview_ready(self, result):
        context, days, preview = result["context"], result["days"], result["preview"]
        if context != self._context() or days != self.retention_spin.value():
            return
        if preview.get("count", 0):
            world_name = _value(self.snapshot, "name", "Selected World")
            answer = QMessageBox.question(
                self, "Apply Retention and Remove Expired Backups",
                f"World: {world_name}\nKeep for {days} days; this will remove {preview['count']} expired versions, approximately {size_text(preview.get('total_bytes'))}.\n\n"
                "Expired versions will then be removed automatically. The latest valid version is always kept; safety copies made before restoration are kept forever.\nApply this setting?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.status.setText("Retention settings were not changed.")
                return
        if context != self._context() or days != self.retention_spin.value():
            self.status.setText("The world or retention period has changed. Apply the setting again.")
            return
        world_id = context[1]
        def apply():
            self.store.set_retention(world_id, days)
            cleanup = self.store.prune(world_id)
            return {"summary": self.store.summary(world_id), "cleanup": cleanup}
        self.status.setText("Saving retention settings and removing expired backups…")
        self._begin("retention", apply)

    def restore_selected(self):
        version = self.selected_version()
        if self.busy or not self._connected() or not version or not version.get("verified") or not self.summary:
            return
        source_hash = self.summary.get("source_sha256")
        backup_hash = version.get("sha256")
        if not source_hash or not backup_hash:
            self.status.setText("The current world has not been verified. Refresh first.")
            return
        context = self._context()
        world_name = _value(self.snapshot, "name", "Selected World")
        confirmation = (
            f"World: {world_name}\nRestore to: {time_text(version.get('saved_at'))}\nVersion size: {size_text(version.get('size'))}\n\n"
            f"Player Status in Selected Backup: \n{player_summary(version)}\n"
            "Offline characters may show last saved values; health is not live.\n\n"
            f"Restore target: {_value(self.snapshot, 'source_path')}\n\n"
            "Buildings, animals and world items will all return to this version. A safety backup of the current world will be preserved first.\n"
            "Fully close the game and server first. Restoration is blocked while either is running.\nRestore this world?")
        # QMessageBox detects rich text automatically; explicitly escape every
        # source-controlled name/path while preserving line breaks in the dialog.
        confirmation_html = "<p>" + html.escape(confirmation).replace("\n", "<br>") + "</p>"
        answer = QMessageBox.question(
            self, "Confirm World Save Restoration", confirmation_html,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        selected = self.selected_version()
        if (context != self._context() or not self.summary or self.summary.get("source_sha256") != source_hash
                or not selected or selected.get("id") != version.get("id") or selected.get("sha256") != backup_hash):
            self.status.setText("The world, current save or selected version has changed. Select the version again before restoring.")
            return
        world_id, version_id = context[1], version["id"]
        def restore():
            receipt = self.store.restore(world_id, version_id, expected_source_sha256=source_hash,
                                         expected_backup_sha256=backup_hash)
            try:
                summary, refresh_error = self.store.summary(world_id), ""
            except Exception as exc:
                summary = None
                refresh_error = f"The backup list could not be updated. Refresh again: {type(exc).__name__}: {exc}"
            return {"receipt": receipt, "summary": summary, "refresh_error": refresh_error}
        self.status.setText("Verifying the version, preserving the current state and restoring the world…")
        self._begin("restore", restore)

    def shutdown(self):
        self._closed = True
        self._generation += 1
        self._cancelled.set()
        self._pending = False
        self.poll.stop()
        self.pool.waitForDone()
        self._jobs.clear()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
