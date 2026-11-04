"""English desktop companion with explicitly confirmed world restoration."""
from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QRunnable, QThreadPool
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .controller import SnapshotController, WorkerSignals
from .map_widget import COLORS, WorldMap, kind_group, value
from .settings import DEFAULT_VISIBLE_LAYERS, Settings, settings_path
from .map_icons import marker_pixmap
from .mission_map import MissionMapPanel
from .mission_overview import OperationOverviewPanel
from .inventory_view import InventoryPanel
from .animals_view import AnimalPanel
from .shared_profile import SHARED_SOURCE, seed_shared_bases
from .release_sync import REMOTE_SOURCE, is_remote_cache_path
from .live_positions import local_time_text, player_map_points
from .world_backups import BackupStore
from .world_backups_view import WorldBackupsPanel
from .drill_inventory import inventory_for_point


class _BackupServiceWorker(QRunnable):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.signals = WorkerSignals()

    def run(self):
        try:
            from .backup_daemon import ensure_running
            self.signals.success.emit(0, ensure_running(self.root))
        except Exception as exc:
            self.signals.failure.emit(0, str(exc))


KIND_LABELS = {
    "cave": "Cave", "deep": "Deep deposits", "ore": "Surface ores", "exotic": "Special resources",
    "geyser": "Geysers / gas", "oil": "Oil", "water": "Water", "plant": "Plants",
    "tree": "Trees", "deployable": "Deployable", "drill": "Mining drill", "player": "Player", "resource": "Other resources",
}
STATUS_LABELS = {
    "installed": "Installed", "not_installed": "Not installed", "absent": "Not installed", "unknown": "Cannot confirm",
    "inferred_installed": "Installed (inferred)", "confirmed": "Confirmed", "candidate": "Candidate",
    "inferred": "Inferred", "estimated": "Estimated", "verified": "Confirmed", "save": "Confirmed in save",
    "depleted": "Depleted", "partial": "Partially mined", "unmined": "Unmined", "remaining": "Remaining",
}


def esc(text):
    return html.escape(str(text if text is not None else ""))


def display(text):
    return STATUS_LABELS.get(str(text), str(text or "Unknown"))


def drill_inventory_html(point):
    stock = inventory_for_point(point)
    if stock is None:
        return ""
    from .item_icons import item_icon_path
    from .map_widget import drill_stock_label
    parts = ["<h3>Drill inventory (saved)</h3>", f"<p>{esc(drill_stock_label(point))}</p>"]
    if stock.get("items"):
        parts.append('<table cellspacing="6"><tr><th>Item</th><th>Quantity</th><th>Slots</th></tr>')
        for item in stock["items"]:
            path = item_icon_path(item.get("id", ""))
            icon = f'<img src="{esc(path.as_uri())}" width="24" height="24"> ' if path else "? "
            quantity = item.get("quantity")
            amount = f"{quantity:,}" if isinstance(quantity, int) else f"Known: {item.get('known_quantity', 0):,} + unknown"
            slots = ", ".join(str(slot) for slot in item.get("slots", [])) or "Unknown"
            parts.append(f"<tr><td>{icon}{esc(item.get('name', item.get('id', 'Unknown item')))}</td><td>{esc(amount)}</td><td>{esc(slots)}</td></tr>")
        parts.append("</table>")
    if stock.get("status") == "empty":
        parts.append("<p>Output storage is confirmed empty.</p>")
    for warning in dict.fromkeys([stock.get("reason", ""), *stock.get("warnings", [])]):
        if warning:
            parts.append(f"<p>{esc(warning)}</p>")
    parts.append(f"<p>Save: {esc(local_time_text(stock.get('saved_at')))}<br>Output storage only; excludes fuel and upgrades. Updated with each valid save.</p>")
    return "".join(parts)


def remaining_text(point):
    amount = value(point, "remaining")
    status = display(value(point, "remaining_status", "unknown"))
    if amount is None:
        return "Quantity unknown" if status in ("Cannot confirm", "unknown", "Unknown", "Quantity unknown") else status if "Quantity unknown" in status else f"{status} · Quantity unknown"
    return f"{amount:g} {value(point, 'remaining_unit', '')}" if isinstance(amount, (float, int)) else f"{amount} {value(point, 'remaining_unit', '')}"


def button(text, callback, name=""):
    item = QPushButton(text)
    item.clicked.connect(callback)
    if name:
        item.setObjectName(name)
    return item


def hline():
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.HLine)
    return frame


class MainWindow(QMainWindow):
    def __init__(self, initial_path=None, settings=None):
        super().__init__()
        self.setWindowTitle("ICARUS Helper")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "app.ico")))
        self.resize(1540, 1000)
        self.setMinimumSize(1100, 760)
        self.settings = settings or Settings()
        # Explicit test/reference settings must never register a production
        # source, launch a production daemon, or install a Windows task.
        self._production_backups = self.settings.path.resolve() == settings_path().resolve()
        self.backup_store = BackupStore(root=None if self._production_backups else self.settings.path.parent / "world-backups")
        self._backup_service_started = False
        self._backup_service_workers = []
        self._backup_service_pool = QThreadPool(self)
        self._backup_service_pool.setMaxThreadCount(1)
        self.snapshot = None
        self.catalog = {}
        self.points = []
        self.visible_points = []
        self.missions = []
        self.mission_clusters = {}
        self.selected_point = None
        self._live_positions_summary = None
        self.controller = SnapshotController(self)
        self.controller.loaded.connect(self.loaded)
        self.controller.state.connect(self.status_changed)
        self.controller.failed.connect(self.failed)
        self._build_ui()
        self.populate_worlds(initial_path)
        if self._production_backups and (self.backup_store.root / "worlds.json").is_file():
            self._ensure_backup_service()
        if self.settings.error:
            self.status_changed(self.settings.error)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        source_row = QHBoxLayout()
        self.world_label = QLabel("Select a world to start tracking")
        self.world_label.setObjectName("worldInfo")
        self.world_label.setWordWrap(True)
        self.world_label.setMinimumWidth(180)
        self.world_label.setMaximumWidth(480)
        self.world_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        source_row.addWidget(self.world_label, 1)
        self.world_combo = QComboBox()
        self.world_combo.setFixedWidth(280)
        self.world_combo.setAccessibleName("Tracked world")
        self.world_combo.currentIndexChanged.connect(self.select_world)
        source_row.addWidget(self.world_combo)
        source_row.addStretch(1)
        source_row.addWidget(button("Choose host save…", self.browse_save))
        source_row.addWidget(button("Rescan", self.rescan))
        self.refresh_button = button("Refresh", self.controller.refresh, "primary")
        source_row.addWidget(self.refresh_button)
        source_row.addWidget(button("Coverage && sources", self.show_coverage))
        root.addLayout(source_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._map_tab(), "Map && resources")
        self.tabs.addTab(self._mission_tab(), "Missions && guides")
        self.inventory_panel = InventoryPanel()
        self.tabs.addTab(self.inventory_panel, "Base inventory")
        self.animal_panel = AnimalPanel()
        self.animal_panel.ownerSourcesRequested.connect(self.browse_owner_profiles)
        self.animal_panel.loaded.connect(self.live_positions_updated)
        self.tabs.addTab(self.animal_panel, "Tamed animals")
        self.world_backups_panel = WorldBackupsPanel(self.backup_store)
        self.world_backups_panel.restored.connect(lambda _receipt: self.controller.refresh())
        self.tabs.addTab(self.world_backups_panel, "World saves")
        root.addWidget(self.tabs, 1)

    def _ensure_backup_service(self):
        if not self._production_backups or self._backup_service_started:
            return
        self._backup_service_started = True
        worker = _BackupServiceWorker(self.backup_store.root)
        self._backup_service_workers.append(worker)
        worker.signals.success.connect(self._backup_service_ready)
        worker.signals.failure.connect(lambda _generation, error: self.status_changed("Automatic backup could not start: " + error))
        self._backup_service_pool.start(worker)

    def _backup_service_ready(self, _generation, result):
        if result.get("error"):
            self.status_changed(result["error"])

    def _map_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        self.cursor_label = QLabel("Scroll to zoom · Drag to pan · Click a marker")
        legend = QHBoxLayout()
        for group, mineral, text in [("cave", "", "Cave"), ("deep", "Gold", "Gold ore"), ("deep", "Iron", "Iron ore"), ("deep", "Copper", "Copper ore"), ("drill", "", "Mining drill")]:
            icon = QLabel()
            icon.setPixmap(marker_pixmap(group, mineral, size=23))
            legend.addWidget(icon)
            label = QLabel(text)
            label.setObjectName("muted")
            legend.addWidget(label)
        legend_note = QLabel("Red outline: drill installed · Dashed ?: candidate · Right-click to manage bases")
        legend_note.setWordWrap(True)
        legend.addWidget(legend_note, 1)
        legend.addStretch()
        legend.addWidget(self.cursor_label)
        layout.addLayout(legend)

        upper = QSplitter(Qt.Orientation.Horizontal)
        self.map = WorldMap()
        self.map.selected.connect(self.show_point)
        self.map.positionPicked.connect(lambda x, y: self.show_map_menu(x, y, self.map))
        self.map.cursorPosition.connect(lambda x, y: self.cursor_label.setText(f"{self.map.grid(x, y)}  ·  X {x:,.0f} / Y {y:,.0f} cm"))
        upper.addWidget(self.map)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search minerals, caves or resources…")
        self.search.textChanged.connect(self.filter_points)
        side.addWidget(self.search)
        self.mineral_combo = QComboBox()
        self.mineral_combo.addItem("All resource types", "")
        self.mineral_combo.currentIndexChanged.connect(self.filter_points)
        side.addWidget(self.mineral_combo)
        self.drill_combo = QComboBox()
        for text, data in [("All drill states", ""), ("Drill installed (including inferred)", "installed"), ("Confirmed absent", "not_installed"), ("Drill status unknown", "unknown")]:
            self.drill_combo.addItem(text, data)
        self.drill_combo.currentIndexChanged.connect(self.filter_points)
        side.addWidget(self.drill_combo)
        self.layer_boxes = {}
        display_preferences = self.settings.map_display()
        layer_row = QHBoxLayout()
        layer_left, layer_right = QVBoxLayout(), QVBoxLayout()
        for i, (key, label) in enumerate(KIND_LABELS.items()):
            checkbox = QCheckBox(label)
            checkbox.setChecked(display_preferences["layers"].get(key, False))
            checkbox.setIcon(QIcon(marker_pixmap(key, "Gold" if key == "deep" else "", size=23)))
            checkbox.setIconSize(QSize(23, 23))
            checkbox.setStyleSheet(f"color:{COLORS.get(key, '#d4e0ed')}")
            checkbox.stateChanged.connect(self.layer_preferences_changed)
            self.layer_boxes[key] = checkbox
            (layer_left if i % 2 == 0 else layer_right).addWidget(checkbox)
        layer_row.addLayout(layer_left)
        layer_row.addLayout(layer_right)
        side.addLayout(layer_row)
        self.candidates_check = QCheckBox("Show candidate / inferred locations")
        self.candidates_check.setChecked(display_preferences["show_candidates"])
        self.candidates_check.stateChanged.connect(self.layer_preferences_changed)
        side.addWidget(self.candidates_check)
        side.addWidget(button("Reset default layers", self.reset_default_layers))
        self.result_label = QLabel("Not loaded")
        self.result_label.setObjectName("muted")
        side.addWidget(self.result_label)
        side.addWidget(hline())
        side.addWidget(QLabel("Selected location"))
        self.point_detail = QTextBrowser()
        self.point_detail.setMinimumHeight(160)
        self.point_detail.setOpenExternalLinks(False)
        self.point_detail.setHtml("<p>Click a map marker to view resource and drill details.</p><p>Right-click the map to add, edit or delete bases.</p>")
        side.addWidget(self.point_detail, 1)
        sidebar.setMinimumWidth(430)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setMinimumWidth(450)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setWidget(sidebar)
        upper.addWidget(side_scroll)
        upper.setSizes([920, 450])
        layout.addWidget(upper, 1)
        return tab

    def _mission_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        toolbar = QHBoxLayout()
        self.goal_combo = QComboBox()
        for text, key in [("Story & unlocks", "story"), ("Lower risk", "safe"), ("Resources & rewards", "resources")]:
            self.goal_combo.addItem(text, key)
        self.goal_combo.currentIndexChanged.connect(self.rebuild_missions)
        self.mission_state_combo = QComboBox()
        self.mission_state_combo.addItem("Unlocked & active", "actionable")
        self.mission_state_combo.hide()
        self.mission_search = QLineEdit()
        self.mission_search.setPlaceholderText("Search missions or guide text…")
        self.mission_search.textChanged.connect(self.filter_missions)
        toolbar.addWidget(self.mission_search, 1)
        # Compatibility cannot broaden the requested scope to completed or
        # SMPL3 missions, even when an older script toggles this checkbox.
        self.all_missions_check = QCheckBox("Unlocked missions", tab)
        self.all_missions_check.setChecked(False)
        self.all_missions_check.hide()
        self.all_missions_check.toggled.connect(self._legacy_mission_filter_changed)
        layout.addLayout(toolbar)
        self.mission_summary = QLabel("Load a world to see unlocked missions and their locations")
        self.mission_summary.setWordWrap(True)
        self.mission_summary.setObjectName("muted")
        layout.addWidget(self.mission_summary)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.mission_left_tabs = QTabWidget()
        self.mission_left_tabs.setStyleSheet("QTabBar::tab { padding:10px 12px; }")
        self.mission_left_tabs.setMinimumWidth(240)
        self.mission_list = QListWidget()
        self.mission_list.setWordWrap(True)
        self.mission_list.setAccessibleName("Unlocked mission list")
        self.mission_list.currentItemChanged.connect(self.show_mission)
        self.mission_left_tabs.addTab(self.mission_list, "Unlocked missions")
        cluster_tab = QWidget()
        cluster_layout = QVBoxLayout(cluster_tab)
        cluster_layout.setContentsMargins(0, 6, 0, 0)
        cluster_goal = QHBoxLayout()
        cluster_goal.addWidget(QLabel("Priority"))
        cluster_goal.addWidget(self.goal_combo)
        cluster_layout.addLayout(cluster_goal)
        cluster_toolbar = QHBoxLayout()
        cluster_toolbar.addWidget(QLabel("Maximum separation"))
        self.cluster_radius_combo = QComboBox()
        for meters in (500, 1200, 2000):
            self.cluster_radius_combo.addItem(f"{meters:,} m", meters)
        self.cluster_radius_combo.setCurrentIndex(1)
        self.cluster_radius_combo.currentIndexChanged.connect(self.rebuild_mission_clusters)
        cluster_toolbar.addWidget(self.cluster_radius_combo)
        cluster_layout.addLayout(cluster_toolbar)
        self.mission_cluster_note = QLabel("Groups compare mission stages; verify candidate locations after accepting a mission.")
        self.mission_cluster_note.setWordWrap(True)
        self.mission_cluster_note.setObjectName("muted")
        cluster_layout.addWidget(self.mission_cluster_note)
        self.mission_cluster_list = QListWidget()
        self.mission_cluster_list.setWordWrap(True)
        self.mission_cluster_list.setAccessibleName("Nearby mission groups")
        self.mission_cluster_list.currentItemChanged.connect(self.show_mission_cluster)
        cluster_layout.addWidget(self.mission_cluster_list, 1)
        self.mission_left_tabs.addTab(cluster_tab, "Nearby groups")
        self.mission_left_tabs.currentChanged.connect(self.show_mission_selection)
        split.addWidget(self.mission_left_tabs)
        self.operation_overview = OperationOverviewPanel()
        self.operation_overview.setMinimumWidth(380)
        self.operation_overview.missionSelected.connect(self.operation_selected)
        self.operation_overview.map.positionPicked.connect(lambda x, y: self.show_map_menu(x, y, self.operation_overview.map))
        self.operation_overview.listing.hide()
        split.addWidget(self.operation_overview)
        self.mission_detail_tabs = QTabWidget()
        self.mission_detail_tabs.setMinimumWidth(320)
        self.mission_detail = QTextBrowser()
        self.mission_detail.setOpenExternalLinks(False)
        self.mission_detail.anchorClicked.connect(self.mission_link)
        self.mission_detail.setHtml("<h2>Mission guide & preparation</h2><p>Load a world save to view progress, recommended missions and preparation lists.</p>")
        self.mission_detail_tabs.addTab(self.mission_detail, "Mission details")
        self.mission_route = MissionMapPanel()
        self.mission_route.map.positionPicked.connect(lambda x, y: self.show_map_menu(x, y, self.mission_route.map))
        self.mission_detail_tabs.addTab(self.mission_route, "Stage map")
        split.addWidget(self.mission_detail_tabs)
        split.setChildrenCollapsible(False)
        split.setSizes([250, 700, 550])
        layout.addWidget(split, 1)
        return tab

    def populate_worlds(self, initial_path=None):
        from .saves import discover_saves
        self.world_combo.blockSignals(True)
        self.world_combo.clear()
        descriptors = discover_saves()
        selected = str(initial_path or self.settings.data.get("selected_source") or SHARED_SOURCE)
        self.world_combo.addItem("Shared map · Prometheus · Home", SHARED_SOURCE)
        self.world_combo.addItem("Public sync · GuoGuoGaGa · Prometheus", REMOTE_SOURCE)
        for item in descriptors:
            text = f"{item.name}  ·  {item.map_name}  ·  {datetime.fromtimestamp(item.mtime).astimezone():%m/%d %H:%M}"
            self.world_combo.addItem(text, str(item.path))
        if selected not in (SHARED_SOURCE, REMOTE_SOURCE) and self.world_combo.findData(selected) < 0 and Path(selected).exists():
            self.world_combo.addItem(f"External source · {Path(selected).stem}", selected)
        index = self.world_combo.findData(selected)
        self.world_combo.setCurrentIndex(max(index, 0))
        self.world_combo.blockSignals(False)
        if self.world_combo.count():
            QTimer.singleShot(0, lambda: self.select_world(self.world_combo.currentIndex()))
        else:
            self.status_changed("No local host save found. Use “Choose host save” to select a read-only source or copy.")

    def rescan(self):
        self.populate_worlds(self.world_combo.currentData())

    def select_world(self, index):
        path = self.world_combo.itemData(index)
        if not path:
            return
        self.snapshot = None
        self._live_positions_summary = None
        self.points, self.missions = [], []
        self.mission_clusters = {}
        self.visible_points = []
        self.catalog = {}
        self.selected_point = None
        self.map.chosen = None
        self.map.set_points([])
        self.map.set_bases([])
        self.map.set_catalog({"map": "", "calibrated": False})
        self.inventory_panel.clear()
        self.animal_panel.clear()
        self.world_backups_panel.clear()
        self.mission_list.clear()
        self.operation_overview.clear()
        self.mission_cluster_list.clear()
        self.mission_summary.setText("Loading the selected world…")
        self.mission_cluster_note.setText("Loading the selected world…")
        self.mission_left_tabs.setTabText(0, "Unlocked missions")
        self.mission_left_tabs.setTabText(1, "Nearby groups")
        self.mission_route.clear()
        self.mission_detail.setHtml("<p>Loading the selected world…</p>")
        self.point_detail.setHtml("<p>Loading the selected world…</p>")
        shared = path == SHARED_SOURCE
        remote = path == REMOTE_SOURCE
        self.goal_combo.setEnabled(not shared)
        self.mission_state_combo.setEnabled(not shared)
        self.world_label.setText("Shared map (no host save connected) / Prometheus" if shared else "GuoGuoGaGa / Prometheus · Public sync" if remote else Path(path).stem)
        self.world_label.setToolTip("Public map and shared base locations; no private save data." if shared else "Downloads the host's public world save from GitHub Releases every two minutes for read-only use in Helper." if remote else f"Source: {path}")
        self.world_combo.setToolTip(self.world_label.toolTip())
        self.settings.data["selected_source"] = path
        self.save_settings()
        if shared:
            self.controller.set_shared_map()
        elif remote:
            cache = None if self._production_backups else self.settings.path.parent / "remote-worlds" / "guoguogaga"
            self.controller.set_remote_world(cache)
        else:
            self.controller.set_path(path)

    def browse_save(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose host world save (read-only)", str(Path.home()), "ICARUS world saves (*.json);;All files (*)", options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            if is_remote_cache_path(path):
                self.world_combo.setCurrentIndex(self.world_combo.findData(REMOTE_SOURCE))
                return
            index = self.world_combo.findData(path)
            if index < 0:
                self.world_combo.addItem(f"External source · {Path(path).stem}", path)
                index = self.world_combo.count() - 1
            self.world_combo.setCurrentIndex(index)

    def loaded(self, payload):
        old_world = self.snapshot.world_id if self.snapshot else None
        self.snapshot = payload["snapshot"]
        if self.snapshot.source_path and is_remote_cache_path(self.snapshot.source_path):
            self.snapshot.is_remote = True
        self.world_backups_panel.set_snapshot(self.snapshot)
        if not getattr(self.snapshot, "is_shared_map", False) and not getattr(self.snapshot, "is_remote", False):
            self._ensure_backup_service()
        self._live_positions_summary = None
        self.catalog, self.points, self.missions = payload["catalog"], payload["points"], payload["missions"]
        if getattr(self.snapshot, "is_shared_map", False):
            seed_shared_bases(self.settings, payload["shared_profile"], self.snapshot)
            self.save_settings()
        elif getattr(self.snapshot, "is_remote", False):
            # Only this explicitly published world receives the already public
            # home coordinates, in its exact identity namespace and only once.
            from .shared_profile import load_shared_profile
            if self.snapshot.world_id not in self.settings.data.get("worlds", {}):
                seed_shared_bases(self.settings, load_shared_profile(), self.snapshot)
                self.save_settings()
        self.data_stale = bool(getattr(self.snapshot, "remote_stale", False))
        self.map.set_catalog(self.catalog)
        self.update_bases()
        self.mineral_combo.blockSignals(True)
        previous = self.mineral_combo.currentData()
        self.mineral_combo.clear()
        self.mineral_combo.addItem("All resource types", "")
        minerals = sorted({str(value(p, "resource_type", "")) for p in self.points if value(p, "resource_type")})
        for mineral in minerals:
            self.mineral_combo.addItem(mineral, mineral)
        self.mineral_combo.setCurrentIndex(max(0, self.mineral_combo.findData(previous)))
        self.mineral_combo.blockSignals(False)
        self.filter_points()
        self.rebuild_missions()
        self.update_inventory()
        if old_world != self.snapshot.world_id:
            if self.current_base():
                self.map.focus_point(self.current_base())
            else:
                self.map.fit_map()
        self._update_header()
        if self.world_combo.currentIndex() >= 0:
            if getattr(self.snapshot, "is_shared_map", False):
                self.world_combo.setItemText(self.world_combo.currentIndex(), "Shared map · Prometheus · Home")
                self.world_combo.setToolTip("No host save connected; map and bases use the public shared profile.")
            elif getattr(self.snapshot, "is_remote", False):
                self.world_combo.setItemText(self.world_combo.currentIndex(), f"Public sync · {self.snapshot.name} · {self.snapshot.saved_at.astimezone():%m/%d %H:%M}")
                self.world_combo.setToolTip("Public world save from GitHub Releases; checked every two minutes, not a live game connection.")
            else:
                self.world_combo.setItemText(self.world_combo.currentIndex(), f"{self.snapshot.name} · {self.snapshot.map_name} · {self.snapshot.saved_at.astimezone():%m/%d %H:%M}")
                self.world_combo.setToolTip(f"{self.world_combo.currentText()}\nSource: {self.snapshot.source_path}")
        # Rebind selected detail to the new snapshot instead of retaining stale values.
        selected_id = value(self.selected_point, "id") if self.selected_point else None
        new_selection = next((p for p in self.points if value(p, "id") == selected_id), None)
        if new_selection:
            self.show_point(new_selection)
        else:
            self.selected_point = None
            self.map.chosen = None
            self.point_detail.setHtml("<p>Click a marker for resource details. Right-click to manage bases.</p>")

    def _update_header(self):
        if not self.snapshot:
            return
        base = self.current_base()
        text = f"{self.snapshot.name} / {self.snapshot.map_name}"
        if base:
            text += f" · {base['name']}"
        if getattr(self.snapshot, "is_shared_map", False):
            if getattr(self, "data_stale", False):
                text += " (map update failed)"
            self.world_label.setText(text)
            self.world_label.setToolTip("Shared map and user-designated base locations; no host save timestamp.\nResources are static or candidate locations; remaining quantities, drills, players and inventory are unknown.\nSelect a host save to track world updates.")
            return
        text += f" · {self.snapshot.saved_at.astimezone():%m/%d %H:%M}"
        if getattr(self.snapshot, "is_remote", False):
            text += " · Public sync"
            if self.data_stale:
                text += " (update failed; stale data)"
            self.world_label.setText(text)
            self.world_label.setToolTip(f"Host save: {self.snapshot.saved_at.astimezone():%Y-%m-%d %H:%M:%S}\nChecked: {local_time_text(getattr(self.snapshot, 'remote_checked_at', None))}\nPublic save from GitHub Releases · Checked every two minutes\n{getattr(self.snapshot, 'remote_status', '')}\nPositions, inventory and status come from this save. A downloaded copy cannot restore the host's world.")
            return
        live = (self._live_positions_summary or {}).get("coverage", {}).get("live_stats", {})
        if live.get("applied_player_positions"):
            try:
                sampled = datetime.fromisoformat(live["sample_time"]).astimezone()
                text += f" · Player positions {sampled:%H:%M:%S}"
            except (ValueError, TypeError, KeyError):
                pass
        if getattr(self, "data_stale", False):
            text += " (stale data)"
        self.world_label.setText(text)
        self.world_label.setToolTip(f"Save: {self.snapshot.saved_at.astimezone():%Y-%m-%d %H:%M:%S}\nUpdated: {self.snapshot.loaded_at.astimezone():%H:%M:%S}\nSource: {self.snapshot.source_path}"
                                   + (f"\nPlayer game position: {local_time_text(live.get('sample_time'))} (30-second samples)" if live.get("applied_player_positions") else "\nPlayer positions currently use saved data."))

    def live_positions_updated(self, summary):
        """Project one validated worker result onto the resource map, never saves."""
        if not self.snapshot or getattr(self.snapshot, "is_shared_map", False) or not isinstance(summary, dict):
            return
        if summary.get("world_id") != self.snapshot.world_id or summary.get("snapshot_sha256") != self.snapshot.sha256:
            return
        self._live_positions_summary = summary
        self.filter_points()
        if self.selected_point is not None and kind_group(self.selected_point) == "player":
            ident = value(self.selected_point, "id")
            current = next((p for p in player_map_points(self.points, summary.get("players", [])) if value(p, "id") == ident), None)
            if current is not None:
                self.map.chosen = current
                self.show_point(current)
        self._update_header()

    def status_changed(self, text):
        self.last_status = text
        self.refresh_button.setToolTip(text)
        if "in the background" in text:
            self.refresh_button.setText("Updating…")
        elif "stale" in text or "failed" in text:
            self.data_stale = True
            self.refresh_button.setText("Retry update")
            self._update_header()
        elif "Watching" in text or "Shared map" in text or "Public world synced" in text or "Public sync" in text:
            self.refresh_button.setText("Refresh")

    def failed(self, message):
        self.last_error = message
        self.data_stale = True
        self.refresh_button.setText("Retry update")
        self.refresh_button.setToolTip(message.splitlines()[0])
        self._update_header()

    def save_settings(self):
        try:
            self.settings.save()
        except OSError as exc:
            self.status_changed(f"Settings not saved: {exc}")

    def layer_preferences_changed(self, *_):
        if not hasattr(self, "candidates_check"):
            return
        self.settings.data["map_display"] = {"layers": {key: box.isChecked() for key, box in self.layer_boxes.items()}, "show_candidates": self.candidates_check.isChecked()}
        self.save_settings()
        self.filter_points()

    def reset_default_layers(self):
        for key, box in self.layer_boxes.items():
            box.blockSignals(True)
            box.setChecked(key in DEFAULT_VISIBLE_LAYERS)
            box.blockSignals(False)
        self.candidates_check.blockSignals(True)
        self.candidates_check.setChecked(True)
        self.candidates_check.blockSignals(False)
        self.layer_preferences_changed()

    def bases(self):
        return self.settings.world(self.snapshot.world_id).get("bases", []) if self.snapshot else []

    def current_base(self):
        if not self.snapshot:
            return None
        config = self.settings.world(self.snapshot.world_id)
        base = next((b for b in self.bases() if b["id"] == config.get("active_base")), None)
        if base is None and not config.get("active_base") and len(self.bases()) == 1:
            base = self.bases()[0]
        return dict(base, world_id=self.snapshot.world_id, map_name=self.snapshot.map_name) if base else None

    def update_bases(self):
        self.map.set_bases(self.bases(), self.current_base())
        if self.selected_point is not None:
            if kind_group(self.selected_point) == "base":
                selected_id = value(self.selected_point, "id")
                self.selected_point = next((b for b in self.bases() if b["id"] == selected_id), None)
                self.map.chosen = self.selected_point
            if self.selected_point is not None:
                self.show_point(self.selected_point)
            else:
                self.point_detail.setHtml("<p>Click a marker for resource details. Right-click to manage bases.</p>")
        self._update_header()

    def update_inventory(self):
        if self.snapshot and getattr(self.snapshot, "is_shared_map", False):
            self.inventory_panel.set_missing_source()
            self.animal_panel.set_missing_source()
        else:
            self.inventory_panel.set_snapshot(self.snapshot, self.current_base())
            configured = self.settings.world(self.snapshot.world_id).get("animal_owner_sources", []) if self.snapshot else []
            # Keep automatic local discovery and explicitly connected backups.
            source = getattr(self.snapshot, "source_path", None)
            local = [str(Path(source).parent.parent.parent)] if source and Path(source).parent.name.casefold() == "prospects" else []
            self.animal_panel.owner_profile_roots = local + [dict(world_id=self.snapshot.world_id, path=p) for p in configured if isinstance(p, str)] if self.snapshot else None
            self.animal_panel.set_snapshot(self.snapshot, self.current_base(), self.catalog)

    def browse_owner_profiles(self):
        if not self.snapshot or getattr(self.snapshot, "is_shared_map", False):
            return
        world_id = self.snapshot.world_id
        path = QFileDialog.getExistingDirectory(self, "Connect owner account backup (read-only: Characters / Profile / BestiaryData)", str(Path.home()), options=QFileDialog.Option.DontUseNativeDialog)
        if not path or not self.snapshot or self.snapshot.world_id != world_id:
            return
        config = self.settings.world(world_id)
        paths = [p for p in config.get("animal_owner_sources", []) if isinstance(p, str)]
        if path not in paths:
            config["animal_owner_sources"] = paths + [path]
            self.save_settings()
        self.update_inventory()

    def change_base(self, base_id=None):
        if self.snapshot:
            if base_id is not None:
                self.settings.world(self.snapshot.world_id)["active_base"] = base_id
            self.save_settings()
            self.update_bases()
            self.filter_points()
            self.rebuild_missions()
            self.update_inventory()

    def go_home(self):
        base = self.current_base()
        if base:
            self.map.focus_point(base)
        else:
            self.add_base()

    def build_map_menu(self, x, y, view=None):
        view = view or self.map
        menu = QMenu(self)
        world_id = self.snapshot.world_id if self.snapshot else None

        def add(text, callback, enabled=True):
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(lambda _checked=False: callback() if self.snapshot and self.snapshot.world_id == world_id else None)
            return action

        base = self.current_base()
        heading = menu.addAction(f"Current base: {base['name']}" if base else "No primary base selected")
        heading.setEnabled(False)
        menu.addSeparator()
        add("Add base here…", lambda: self.base_dialog(x, y, 0), bool(self.snapshot))
        cursor = view.mapFromScene(x / 100, y / 100)
        target = view.hit_target(cursor)
        hit_id = value(target["members"][0], "id") if target else None
        clicked_base = next((b for b in self.bases() if b["id"] == hit_id), None)
        if clicked_base is None:
            clicked_base = next((b for b in self.bases() if (view.mapFromScene(b["x"] / 100, b["y"] / 100) - cursor).manhattanLength() <= 22), None)
        if clicked_base:
            add(f"Edit “{clicked_base['name']}”…", lambda: self.base_dialog(clicked_base["x"], clicked_base["y"], clicked_base.get("z", 0), existing=clicked_base))
            add("Set as primary base", lambda: self.change_base(clicked_base["id"]))
            add(f"Delete “{clicked_base['name']}”", lambda: self.remove_base(clicked_base))
        elif base:
            add(f"Move “{base['name']}” here", lambda: self.move_base(base, x, y))
        if self.bases():
            submenu = QMenu("Switch base", menu)
            menu.addMenu(submenu)
            menu._base_submenu = submenu  # Keep the PySide wrapper alive while the menu is open.
            for saved in self.bases():
                action = submenu.addAction(saved["name"])
                action.setCheckable(True)
                action.setChecked(bool(base and saved["id"] == base["id"]))
                action.triggered.connect(lambda _checked=False, ident=saved["id"]: self.change_base(ident) if self.snapshot and self.snapshot.world_id == world_id else None)
        add("Add from base candidates…", self.base_candidates, bool(self.snapshot) and not getattr(self.snapshot, "is_shared_map", False))
        menu.addSeparator()
        add("Go to primary base", lambda: view.focus_point(self.current_base()) if self.current_base() else None, bool(base))
        add("Full map", view.fit_map)
        add("Zoom in", lambda: view.zoom(1.4))
        add("Zoom out", lambda: view.zoom(1 / 1.4))
        return menu

    def show_map_menu(self, x, y, view=None):
        menu = self.build_map_menu(x, y, view)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def move_base(self, base, x, y):
        if not self.snapshot:
            return
        saved = next((b for b in self.bases() if b["id"] == base["id"]), None)
        if saved:
            saved.update(x=float(x), y=float(y))
            self.change_base(saved["id"])

    def pick_base(self, x, y):
        if self.snapshot:
            self.base_dialog(x, y, 0)

    def add_base(self):
        if not self.snapshot:
            return
        point = self.selected_point
        self.base_dialog(value(point, "x", 0), value(point, "y", 0), value(point, "z", 0))

    def selected_as_base(self):
        self.add_base()

    def base_dialog(self, x, y, z, existing=None):
        if not self.snapshot:
            return
        world_id = self.snapshot.world_id
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit base" if existing else "Add base")
        layout = QFormLayout(dialog)
        name = QLineEdit(existing["name"] if existing else "Home" if not self.bases() else "Outpost")
        layout.addRow("Base name", name)
        fields = []
        for label, initial in [("X (game cm)", x), ("Y (game cm)", y), ("Z (game cm)", z)]:
            spin = QDoubleSpinBox()
            spin.setRange(-100000000, 100000000)
            spin.setDecimals(1)
            spin.setValue(float(initial or 0))
            layout.addRow(label, spin)
            fields.append(spin)
        layout.addRow(QLabel("Select a resource or base candidate first, or right-click a location on the map."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and name.text().strip():
            if not self.snapshot or self.snapshot.world_id != world_id:
                self.status_changed("The world has changed. Select a base in the current world.")
                return
            import uuid
            base = {"id": existing["id"] if existing else uuid.uuid4().hex[:12], "name": name.text().strip(), "kind": "base", "x": fields[0].value(), "y": fields[1].value(), "z": fields[2].value()}
            config = self.settings.world(self.snapshot.world_id)
            if existing:
                config["bases"] = [base if b["id"] == base["id"] else b for b in config["bases"]]
            else:
                config["bases"].append(base)
            self.change_base(base["id"])
            self.map.focus_point(base)

    def remove_base(self, base=None):
        base = base or self.current_base()
        if not base:
            return
        config = self.settings.world(self.snapshot.world_id)
        config["bases"] = [b for b in config["bases"] if b["id"] != base["id"]]
        remaining = config["bases"]
        selected = config.get("active_base")
        if selected == base["id"]:
            selected = remaining[0]["id"] if remaining else ""
        self.change_base(selected)

    def base_candidates(self):
        if not self.snapshot:
            return
        world_id = self.snapshot.world_id
        clusters = []
        for point in self.snapshot.deployables:
            x, y, z = value(point, "x", 0), value(point, "y", 0), value(point, "z", 0)
            found = next((c for c in clusters if math.hypot(c["x"] - x, c["y"] - y) < 10000), None)
            if found:
                found["points"].append((x, y, z))
            else:
                clusters.append({"x": x, "y": y, "points": [(x, y, z)]})
        clusters.sort(key=lambda c: len(c["points"]), reverse=True)
        dialog = QDialog(self)
        dialog.setWindowTitle("Deployment clusters · Confirm your home location")
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Deployables grouped within a 100 m horizontal radius; these are candidates, not confirmed bases."))
        listing = QListWidget()
        for cluster in clusters[:30]:
            import statistics
            center = [statistics.median(p[i] for p in cluster["points"]) for i in range(3)]
            item = QListWidgetItem(f"{len(cluster['points'])} deployables · {self.map.grid(*center[:2])} · X {center[0]:,.0f}, Y {center[1]:,.0f}")
            item.setData(Qt.ItemDataRole.UserRole, center)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and listing.currentItem():
            if not self.snapshot or self.snapshot.world_id != world_id:
                self.status_changed("The world has changed. Reload base candidates for the current world.")
                return
            self.base_dialog(*listing.currentItem().data(Qt.ItemDataRole.UserRole))

    def filter_points(self, *_):
        if not hasattr(self, "map"):
            return
        query = self.search.text().strip().casefold()
        mineral = self.mineral_combo.currentData() or ""
        drill = self.drill_combo.currentData() or ""
        allowed = {key for key, box in self.layer_boxes.items() if box.isChecked()}
        selected = []
        map_points = player_map_points(self.points, (self._live_positions_summary or {}).get("players", []))
        for point in map_points:
            group = kind_group(point)
            if group not in allowed:
                continue
            if not self.candidates_check.isChecked() and str(value(point, "confidence", "")) in ("candidate", "inferred", "estimated", "Candidate", "Inferred", "Estimated"):
                continue
            if mineral and str(value(point, "resource_type", "")) != mineral:
                continue
            ds = str(value(point, "drill_status", "unknown"))
            if drill and (group != "deep" or not (ds == drill or drill == "installed" and (ds.startswith("Installed") or ds in ("inferred_installed", "Installed (inferred)")) or drill == "not_installed" and ds in ("Not installed", "absent") or drill == "unknown" and ds in ("Cannot confirm", "Unknown"))):
                continue
            if query and query not in f"{value(point, 'name', '')} {value(point, 'resource_type', '')} {value(point, 'id', '')} {self.map.grid(value(point, 'x', 0), value(point, 'y', 0))}".casefold():
                continue
            selected.append(point)
        self.map.set_points(selected)
        self.visible_points = selected
        self.result_label.setText(f"Showing {len(selected):,} / {len(map_points):,} locations")

    def show_point(self, point):
        self.selected_point = point
        group = kind_group(point)
        x, y, z = (value(point, k, 0) for k in ("x", "y", "z"))
        info = [f"<h3>{esc(value(point, 'name', 'Location'))}</h3>", f"<p>{esc(KIND_LABELS.get(group, group))} · {esc(self.map.grid(x, y))}</p>", f"<p>X {x:,.1f}<br>Y {y:,.1f}<br>Z {z:,.1f} cm</p>"]
        if group not in ("cave", "base", "player", "deployable", "drill"):
            info.append(f"<p>Remaining: {esc(remaining_text(point))}</p>")
        if group == "deep":
            info.append(f"<p>Drill: {esc(display(value(point, 'drill_status', 'unknown')))}</p>")
        info.append(drill_inventory_html(point))
        base = self.current_base()
        if base:
            info.append(f"<p>From {esc(base['name'])}: {math.hypot(x-base['x'], y-base['y'])/100:,.0f} m<br>(Horizontal straight-line distance)</p>")
        detail = value(point, "detail", "")
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False, indent=2, default=str)
        info.extend([f"<p>{esc(detail).replace(chr(10), '<br>')}</p>", f"<p>Confidence: {esc(display(value(point, 'confidence', 'unknown')))}<br>Source: {esc(value(point, 'source', 'Unknown'))}</p>"])
        self.point_detail.setHtml("".join(info))

    @staticmethod
    def mission_active(mission):
        return (mission.get("kind") in {"Operation", "NPC"}
                and not str(mission.get("id", "")).startswith("DYN_")
                and mission.get("status_key") in {"available", "active"})

    def rebuild_missions(self, *_):
        if not self.snapshot:
            return
        from .missions import build_missions
        self.missions = build_missions(self.snapshot, goal=self.goal_combo.currentData(), context={"active_base": self.current_base()})
        if getattr(self.snapshot, "is_shared_map", False):
            for mission in self.missions:
                mission.update(status="No save connected · Guide preview", status_key="unknown", status_label="No save connected · Guide preview",
                               status_reason="No host save is connected to the shared map. Availability, progress and completion cannot be determined.", progress=[])
        from .mission_planner_view import availability_summary
        self.mission_summary.setText(availability_summary(self.missions, getattr(self.snapshot, "is_shared_map", False)))
        self.operation_overview.set_missions(self.snapshot, self.missions, self.catalog, self.bases(), active_base=self.current_base())
        self.rebuild_mission_clusters(refresh=False)
        self.filter_missions()

    def rebuild_mission_clusters(self, *_, refresh=True):
        if not self.snapshot:
            self.mission_clusters = {}
            return
        from .mission_clusters import build_mission_clusters
        self.mission_clusters = build_mission_clusters(
            self.snapshot, self.missions, base=self.current_base(),
            max_span_m=self.cluster_radius_combo.currentData() or 1200)
        self.mission_cluster_note.setText(self.mission_clusters["summary"])
        if refresh:
            self.filter_mission_clusters()

    def filter_missions(self, *_):
        if not hasattr(self, "mission_list"):
            return
        previous_id = self.mission_list.currentItem().data(Qt.ItemDataRole.UserRole).get("id") if self.mission_list.currentItem() else None
        self.mission_list.blockSignals(True)
        self.mission_list.clear()
        query = self.mission_search.text().casefold()
        from .mission_planner_view import mission_catalog_order
        for mission in sorted(self.missions, key=mission_catalog_order):
            if not self.mission_active(mission) or getattr(self.snapshot, "is_shared_map", False):
                continue
            if query and query not in json.dumps(mission, ensure_ascii=False, default=str).casefold():
                continue
            status = mission.get("status_label", display(mission.get("status", "")))
            title = mission.get("title", mission.get("name", mission.get("id", "Mission")))
            summary = str(mission.get("summary", ""))
            kind = "NPC mission" if mission.get("kind") == "NPC" else "Operation"
            item = QListWidgetItem(f"{title}\n{kind} · {status}\n{summary[:100]}")
            item.setData(Qt.ItemDataRole.UserRole, mission)
            item.setToolTip(str(mission.get("status_reason", "")))
            self.mission_list.addItem(item)
        self.mission_list.blockSignals(False)
        total = sum(self.mission_active(m) for m in self.missions)
        self.mission_left_tabs.setTabText(0, f"Unlocked missions ({self.mission_list.count()} / {total})")
        row = next((i for i in range(self.mission_list.count()) if self.mission_list.item(i).data(Qt.ItemDataRole.UserRole).get("id") == previous_id), 0)
        if self.mission_list.count():
            self.mission_list.setCurrentRow(row)
        else:
            if self.mission_left_tabs.currentIndex() == 0:
                self.mission_route.clear()
                self.mission_detail.setHtml("<p>No unlocked missions match this search. All eligible missions remain on the map; clear the search or click a marker.</p>")
        self.filter_mission_clusters()

    def _legacy_mission_filter_changed(self, checked):
        self.filter_missions()

    def filter_mission_clusters(self):
        previous = self.mission_cluster_list.currentItem()
        previous_id = previous.data(Qt.ItemDataRole.UserRole).get("id") if previous else None
        self.mission_cluster_list.blockSignals(True)
        self.mission_cluster_list.clear()
        query = self.mission_search.text().strip().casefold()
        for group in self.mission_clusters.get("groups", []):
            if query and query not in json.dumps(group, ensure_ascii=False).casefold():
                continue
            grids = list(dict.fromkeys(self.map.grid(point["x"], point["y"]) for point in group["points"]))
            kind = "Confirmed locations" if group["confidence"] == "known" else "Candidate locations"
            titles = ", ".join(m["title"] for m in group["missions"])
            item = QListWidgetItem(f"{group['title']} · {' / '.join(grids[:4])}\n{kind} · {len(group['mission_ids'])} missions · maximum separation {group['span_m']:,.0f} m\n{titles}")
            item.setData(Qt.ItemDataRole.UserRole, group)
            self.mission_cluster_list.addItem(item)
        self.mission_cluster_list.blockSignals(False)
        self.mission_left_tabs.setTabText(1, f"Nearby groups ({self.mission_cluster_list.count()})")
        if self.mission_cluster_list.count():
            row = next((i for i in range(self.mission_cluster_list.count()) if self.mission_cluster_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] == previous_id), 0)
            self.mission_cluster_list.setCurrentRow(row)
        elif self.mission_left_tabs.currentIndex() == 1:
            self.show_mission_selection()

    def show_mission_selection(self, *_):
        if self.mission_left_tabs.currentIndex() == 0:
            if self.mission_list.currentItem():
                self.show_mission(self.mission_list.currentItem())
            else:
                self.mission_route.clear()
                self.mission_detail.setHtml("<p>No unlocked missions match this search. Clear the search or click an overview map marker.</p>")
        elif self.mission_cluster_list.currentItem():
            self.show_mission_cluster(self.mission_cluster_list.currentItem())
        else:
            self.mission_route.clear()
            reasons = "".join(f"<li>{esc(m['title'])}: {esc(m['reason'])}</li>" for m in self.mission_clusters.get("unclustered", []))
            self.mission_detail.setHtml(f"<h2>Nearby mission planning</h2><p>{esc(self.mission_clusters.get('summary', 'No missions can currently be grouped.'))}</p><p>No nearby groups match the current range and search.</p><ul>{reasons}</ul>")

    def show_mission_cluster(self, item, previous=None):
        if item is None or not self.snapshot or self.mission_left_tabs.currentIndex() != 1:
            return
        from .mission_planner_view import cluster_html, cluster_map_route
        group = item.data(Qt.ItemDataRole.UserRole)
        self.mission_detail.setHtml(cluster_html(group))
        self.mission_route.set_mission(self.snapshot, {"id": group["id"]}, self.catalog, self.bases(),
                                       route=cluster_map_route(group), active_base=self.current_base())
        kind = "Confirmed" if group["confidence"] == "known" else "Candidate"
        self.mission_route.summary.setText(f"In group: {len(group['points'])} {kind.lower()} stage locations · Other / unlocated stages: see guide")
        self.mission_route.point_note.setText("Numbers identify locations in the group; they do not specify a walking route or mission order.")

    def show_mission(self, item, previous=None):
        if item is None or self.mission_left_tabs.currentIndex() != 0:
            return
        from .missions import mission_html
        mission = item.data(Qt.ItemDataRole.UserRole)
        self.mission_detail.setHtml(mission_html(mission))
        if self.snapshot:
            self.mission_route.set_mission(self.snapshot, mission, self.catalog, self.bases(), active_base=self.current_base())
            self.operation_overview.select_mission(mission["id"], focus=False)

    def operation_selected(self, mission_id):
        self.mission_search.clear()
        self.mission_left_tabs.setCurrentIndex(0)
        self.mission_detail_tabs.setCurrentIndex(0)
        for index in range(self.mission_list.count()):
            item = self.mission_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)["id"] == mission_id:
                self.mission_list.setCurrentItem(item)
                self.show_mission(item)
                break

    def mission_link(self, url):
        if url.scheme() == "mission":
            from urllib.parse import unquote
            mid = unquote(url.host() or url.path().lstrip("/"))
            # QUrl lowercases host names; mission IDs are case insensitive here.
            self.mission_search.clear()
            self.mission_left_tabs.setCurrentIndex(0)
            for index in range(self.mission_list.count()):
                item = self.mission_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole)["id"].casefold() == mid.casefold():
                    self.mission_list.setCurrentItem(item)
                    self.show_mission(item)
                    break
        elif url.scheme() == "map":
            from PySide6.QtCore import QUrlQuery
            query = QUrlQuery(url)
            try:
                point = {"name": "Mission record location", "kind": "resource", "x": float(query.queryItemValue("x")), "y": float(query.queryItemValue("y")), "z": float(query.queryItemValue("z") or 0), "detail": "Coordinates of the saved mission object; not a verified entrance or traversable route.", "source": "Current mission save"}
            except ValueError:
                return
            self.tabs.setCurrentIndex(0)
            self.map.focus_point(point)
            self.show_point(point)
        elif url.scheme() == "resource":
            self.tabs.setCurrentIndex(0)
            self.search.setText(url.path() or url.host())
        elif url.scheme() in ("https", "http"):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)

    def show_coverage(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Coverage, sources & diagnostics")
        dialog.resize(920, 660)
        layout = QVBoxLayout(dialog)
        detail = QTextBrowser()
        source = "Public shared map profile (no host save connected)" if getattr(self.snapshot, "is_shared_map", False) else str(self.snapshot.source_path) if self.snapshot else None
        if getattr(self.snapshot, "is_remote", False):
            source = "Public world save from GitHub Releases (read-only downloaded copy)"
        info = {"World": self.snapshot.name if self.snapshot else None, "Source": source, "Save coverage": self.snapshot.coverage if self.snapshot else None, "Map coverage": self.catalog.get("coverage", {}), "Map sources": self.catalog.get("sources", []), "Warnings": self.snapshot.warnings if self.snapshot else [], "Last error": getattr(self, "last_error", ""), "Original save": "Read-only source; Helper settings and snapshots use separate storage"}
        info["Helper settings file"] = str(self.settings.path.resolve())
        info["Mission location coverage"] = self.mission_route.route.get("coverage", {})
        info["Unlocked mission overview"] = self.operation_overview.overview.get("coverage", {})
        info["Nearby mission planning"] = {key: self.mission_clusters.get(key) for key in ("summary", "eligible_mission_count", "grouped_mission_count", "max_span_m", "distance_type")}
        info["Inventory coverage"] = self.inventory_panel.summary.get("coverage", {}) if self.inventory_panel.summary else {"Status": self.inventory_panel.status.text()}
        info["Animal coverage"] = self.animal_panel.summary.get("coverage", {}) if self.animal_panel.summary else {"Status": self.animal_panel.status.text()}
        if self.missions:
            info["Mission version"] = {key: self.missions[0].get(key) for key in ("source_version", "source_date", "source_build", "installed_build", "version_compatible")}
        detail.setPlainText(json.dumps(info, ensure_ascii=False, indent=2, default=str))
        layout.addWidget(detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        self.world_backups_panel.shutdown()
        self._backup_service_pool.waitForDone()
        self.animal_panel.shutdown()
        self.inventory_panel.shutdown()
        self.controller.close()
        super().closeEvent(event)


STYLESHEET = """
QWidget { background:#101923; color:#dce6ef; font-family:'Segoe UI','Microsoft JhengHei UI'; font-size:13px; }
QMainWindow, QDialog { background:#101923; }
QLabel#worldInfo { font-size:13px; color:#c7d8e7; }
QLabel#muted { color:#8fa5b8; font-size:12px; }
QPushButton { background:#243646; padding:7px 13px; border:1px solid #344c60; border-radius:5px; }
QPushButton:hover { background:#304c5f; border-color:#72e3ae; }
QPushButton:pressed { background:#173c35; }
QPushButton:disabled { color:#617180; }
QPushButton#primary { background:#24745e; border-color:#3e9f82; color:#f1fff9; }
QLineEdit, QComboBox, QDoubleSpinBox { background:#192834; border:1px solid #344857; border-radius:4px; padding:7px; selection-background-color:#337c65; }
QComboBox QAbstractItemView { background:#192834; selection-background-color:#315849; }
QTabWidget::pane { border:none; }
QTabBar::tab { background:#182632; color:#9db1c2; padding:12px 25px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#93edc5; border-bottom:2px solid #72e3ae; }
QTextBrowser, QListWidget, QTableView { background:#14212c; border:1px solid #283d4d; border-radius:5px; padding:6px; selection-background-color:#315b50; alternate-background-color:#182935; }
QListWidget::item { padding:13px 10px; border-bottom:1px solid #263c4a; }
QListWidget::item:selected { background:#294e44; color:#ffffff; }
QHeaderView::section { background:#233746; color:#bcd0df; border:none; padding:8px; }
QTableView::item { padding:5px; }
QCheckBox { spacing:7px; padding:3px 0; }
QCheckBox::indicator { width:15px; height:15px; }
QSplitter::handle { background:#243845; width:5px; height:5px; }
QScrollBar:vertical { background:#14212c; width:11px; }
QScrollBar::handle:vertical { background:#3a5263; border-radius:5px; min-height:30px; }
QScrollBar:horizontal { background:#14212c; height:11px; }
QScrollBar::handle:horizontal { background:#3a5263; border-radius:5px; min-width:30px; }
"""
