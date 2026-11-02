"""Traditional-Chinese desktop UI for the read-only ICARUS companion."""
from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .controller import SnapshotController
from .map_widget import COLORS, WorldMap, kind_group, value
from .settings import DEFAULT_VISIBLE_LAYERS, Settings
from .map_icons import marker_pixmap
from .mission_map import MissionMapPanel
from .inventory_view import InventoryPanel
from .shared_profile import SHARED_SOURCE, seed_shared_bases


KIND_LABELS = {
    "cave": "地穴", "deep": "深層礦床", "ore": "一般礦石", "exotic": "特殊資源",
    "geyser": "噴泉／氣體", "oil": "石油", "water": "水源", "plant": "植物",
    "tree": "樹木", "deployable": "部署物", "drill": "採礦鑽機", "player": "玩家", "resource": "其他資源",
}
STATUS_LABELS = {
    "installed": "已放置", "not_installed": "未放置", "absent": "未放置", "unknown": "無法確認",
    "inferred_installed": "推定已放置", "confirmed": "已確認", "candidate": "候選點",
    "inferred": "推定", "estimated": "估計", "verified": "已確認", "save": "存檔確認",
    "depleted": "已耗盡", "partial": "部分採集", "unmined": "未採集", "remaining": "有剩餘",
}


def esc(text):
    return html.escape(str(text if text is not None else ""))


def display(text):
    return STATUS_LABELS.get(str(text), str(text or "未知"))


def remaining_text(point):
    amount = value(point, "remaining")
    status = display(value(point, "remaining_status", "unknown"))
    if amount is None:
        return "數量未知" if status in ("無法確認", "unknown", "未知", "數量未知") else status if "數量未知" in status else f"{status} · 數量未知"
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
        self.snapshot = None
        self.catalog = {}
        self.points = []
        self.visible_points = []
        self.missions = []
        self.selected_point = None
        self.controller = SnapshotController(self)
        self.controller.loaded.connect(self.loaded)
        self.controller.state.connect(self.status_changed)
        self.controller.failed.connect(self.failed)
        self._build_ui()
        self.populate_worlds(initial_path)
        if self.settings.error:
            self.status_changed(self.settings.error)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        source_row = QHBoxLayout()
        self.world_label = QLabel("選擇世界，開始追蹤")
        self.world_label.setObjectName("worldInfo")
        self.world_label.setWordWrap(True)
        self.world_label.setMinimumWidth(180)
        self.world_label.setMaximumWidth(480)
        self.world_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        source_row.addWidget(self.world_label, 1)
        self.world_combo = QComboBox()
        self.world_combo.setFixedWidth(280)
        self.world_combo.setAccessibleName("追蹤世界")
        self.world_combo.currentIndexChanged.connect(self.select_world)
        source_row.addWidget(self.world_combo)
        source_row.addStretch(1)
        source_row.addWidget(button("選擇主機存檔…", self.browse_save))
        source_row.addWidget(button("重新掃描", self.rescan))
        self.refresh_button = button("重新整理", self.controller.refresh, "primary")
        source_row.addWidget(self.refresh_button)
        source_row.addWidget(button("資料覆蓋與來源", self.show_coverage))
        root.addLayout(source_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._map_tab(), "地圖與資源")
        self.tabs.addTab(self._mission_tab(), "任務地圖與攻略")
        self.inventory_panel = InventoryPanel()
        self.tabs.addTab(self.inventory_panel, "家中物資")
        root.addWidget(self.tabs, 1)

    def _map_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        self.cursor_label = QLabel("滾輪縮放 · 拖曳平移 · 點選標記")
        legend = QHBoxLayout()
        for group, mineral, text in [("cave", "", "地穴"), ("deep", "Gold", "金礦"), ("deep", "Iron", "鐵礦"), ("deep", "Copper", "銅礦"), ("drill", "", "採礦鑽機")]:
            icon = QLabel()
            icon.setPixmap(marker_pixmap(group, mineral, size=23))
            legend.addWidget(icon)
            label = QLabel(text)
            label.setObjectName("muted")
            legend.addWidget(label)
        legend.addWidget(QLabel("紅色外框：已放鑽機　? 虛線：候選　│　右鍵管理基地"))
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
        self.search.setPlaceholderText("搜尋礦種、地穴、資源名稱…")
        self.search.textChanged.connect(self.filter_points)
        side.addWidget(self.search)
        self.mineral_combo = QComboBox()
        self.mineral_combo.addItem("全部資源種類", "")
        self.mineral_combo.currentIndexChanged.connect(self.filter_points)
        side.addWidget(self.mineral_combo)
        self.drill_combo = QComboBox()
        for text, data in [("全部鑽機狀態", ""), ("已放置鑽機（含推定）", "installed"), ("確認未放置", "not_installed"), ("鑽機狀態無法確認", "unknown")]:
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
        self.candidates_check = QCheckBox("顯示地圖候選／推定點")
        self.candidates_check.setChecked(display_preferences["show_candidates"])
        self.candidates_check.stateChanged.connect(self.layer_preferences_changed)
        side.addWidget(self.candidates_check)
        side.addWidget(button("還原預設圖層", self.reset_default_layers))
        self.result_label = QLabel("尚未載入")
        self.result_label.setObjectName("muted")
        side.addWidget(self.result_label)
        side.addWidget(hline())
        side.addWidget(QLabel("選取位置"))
        self.point_detail = QTextBrowser()
        self.point_detail.setMinimumHeight(160)
        self.point_detail.setOpenExternalLinks(False)
        self.point_detail.setHtml("<p>點選地圖標記查看資源與鑽機狀態。</p><p>右鍵點選地圖可新增、編輯或刪除基地。</p>")
        side.addWidget(self.point_detail, 1)
        sidebar.setMinimumWidth(290)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setWidget(sidebar)
        upper.addWidget(side_scroll)
        upper.setSizes([1040, 330])
        layout.addWidget(upper, 1)
        return tab

    def _mission_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("推薦目標"))
        self.goal_combo = QComboBox()
        for text, key in [("推進主線與解鎖", "story"), ("低風險任務", "safe"), ("資源與獎勵", "resources")]:
            self.goal_combo.addItem(text, key)
        self.goal_combo.currentIndexChanged.connect(self.rebuild_missions)
        toolbar.addWidget(self.goal_combo)
        self.mission_search = QLineEdit()
        self.mission_search.setPlaceholderText("搜尋任務或內容…")
        self.mission_search.textChanged.connect(self.filter_missions)
        toolbar.addWidget(self.mission_search, 1)
        self.all_missions_check = QCheckBox("包含已完成、未解鎖與其他狀態")
        self.all_missions_check.stateChanged.connect(self.filter_missions)
        toolbar.addWidget(self.all_missions_check)
        layout.addLayout(toolbar)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.mission_list = QListWidget()
        self.mission_list.setWordWrap(True)
        self.mission_list.setAccessibleName("任務推薦清單")
        self.mission_list.currentItemChanged.connect(self.show_mission)
        split.addWidget(self.mission_list)
        detail = QSplitter(Qt.Orientation.Horizontal)
        self.mission_detail = QTextBrowser()
        self.mission_detail.setOpenExternalLinks(False)
        self.mission_detail.anchorClicked.connect(self.mission_link)
        self.mission_detail.setHtml("<h2>任務攻略與準備</h2><p>載入世界存檔後，這裡會顯示目前進度、推薦任務與事先準備清單。</p>")
        detail.addWidget(self.mission_detail)
        self.mission_route = MissionMapPanel()
        self.mission_route.map.positionPicked.connect(lambda x, y: self.show_map_menu(x, y, self.mission_route.map))
        self.mission_route.setMinimumWidth(340)
        detail.addWidget(self.mission_route)
        detail.setChildrenCollapsible(False)
        detail.setSizes([570, 620])
        split.addWidget(detail)
        split.setSizes([300, 1140])
        layout.addWidget(split, 1)
        return tab

    def populate_worlds(self, initial_path=None):
        from .saves import discover_saves
        self.world_combo.blockSignals(True)
        self.world_combo.clear()
        descriptors = discover_saves()
        selected = str(initial_path or self.settings.data.get("selected_source") or SHARED_SOURCE)
        self.world_combo.addItem("共用地圖 · Prometheus · 我們家", SHARED_SOURCE)
        for item in descriptors:
            text = f"{item.name}  ·  {item.map_name}  ·  {datetime.fromtimestamp(item.mtime).astimezone():%m/%d %H:%M}"
            self.world_combo.addItem(text, str(item.path))
        if selected != SHARED_SOURCE and self.world_combo.findData(selected) < 0 and Path(selected).exists():
            self.world_combo.addItem(f"外部來源 · {Path(selected).stem}", selected)
        index = self.world_combo.findData(selected)
        self.world_combo.setCurrentIndex(max(index, 0))
        self.world_combo.blockSignals(False)
        if self.world_combo.count():
            QTimer.singleShot(0, lambda: self.select_world(self.world_combo.currentIndex()))
        else:
            self.status_changed("找不到本機主機存檔。請使用「選擇主機存檔」指定唯讀來源／副本。")

    def rescan(self):
        self.populate_worlds(self.world_combo.currentData())

    def select_world(self, index):
        path = self.world_combo.itemData(index)
        if not path:
            return
        self.snapshot = None
        self.points, self.missions = [], []
        self.visible_points = []
        self.catalog = {}
        self.selected_point = None
        self.map.chosen = None
        self.map.set_points([])
        self.map.set_bases([])
        self.map.set_catalog({"map": "", "calibrated": False})
        self.inventory_panel.clear()
        self.mission_list.clear()
        self.mission_route.clear()
        self.mission_detail.setHtml("<p>正在讀取所選世界…</p>")
        self.point_detail.setHtml("<p>正在讀取所選世界…</p>")
        shared = path == SHARED_SOURCE
        self.goal_combo.setEnabled(not shared)
        self.world_label.setText("共用地圖（未連接主機存檔） / Prometheus" if shared else Path(path).stem)
        self.world_label.setToolTip("公開共用地圖與基地位置；不含私人存檔。" if shared else f"來源：{path}")
        self.world_combo.setToolTip(self.world_label.toolTip())
        self.settings.data["selected_source"] = path
        self.save_settings()
        if shared:
            self.controller.set_shared_map()
        else:
            self.controller.set_path(path)

    def browse_save(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇主機世界存檔（唯讀）", str(Path.home()), "ICARUS 世界存檔 (*.json);;所有檔案 (*)")
        if path:
            index = self.world_combo.findData(path)
            if index < 0:
                self.world_combo.addItem(f"外部來源 · {Path(path).stem}", path)
                index = self.world_combo.count() - 1
            self.world_combo.setCurrentIndex(index)

    def loaded(self, payload):
        old_world = self.snapshot.world_id if self.snapshot else None
        self.snapshot = payload["snapshot"]
        self.catalog, self.points, self.missions = payload["catalog"], payload["points"], payload["missions"]
        if getattr(self.snapshot, "is_shared_map", False):
            seed_shared_bases(self.settings, payload["shared_profile"], self.snapshot)
            self.save_settings()
        self.data_stale = False
        self.map.set_catalog(self.catalog)
        self.update_bases()
        self.mineral_combo.blockSignals(True)
        previous = self.mineral_combo.currentData()
        self.mineral_combo.clear()
        self.mineral_combo.addItem("全部資源種類", "")
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
                self.world_combo.setItemText(self.world_combo.currentIndex(), "共用地圖 · Prometheus · 我們家")
                self.world_combo.setToolTip("未連接主機存檔；地圖與基地為公開共用設定。")
            else:
                self.world_combo.setItemText(self.world_combo.currentIndex(), f"{self.snapshot.name} · {self.snapshot.map_name} · {self.snapshot.saved_at.astimezone():%m/%d %H:%M}")
                self.world_combo.setToolTip(f"{self.world_combo.currentText()}\n來源：{self.snapshot.source_path}")
        # Rebind selected detail to the new snapshot instead of retaining stale values.
        selected_id = value(self.selected_point, "id") if self.selected_point else None
        new_selection = next((p for p in self.points if value(p, "id") == selected_id), None)
        if new_selection:
            self.show_point(new_selection)
        else:
            self.selected_point = None
            self.map.chosen = None
            self.point_detail.setHtml("<p>點選標記查看資源。右鍵可管理基地。</p>")

    def _update_header(self):
        if not self.snapshot:
            return
        base = self.current_base()
        text = f"{self.snapshot.name} / {self.snapshot.map_name}"
        if base:
            text += f" · {base['name']}"
        if getattr(self.snapshot, "is_shared_map", False):
            if getattr(self, "data_stale", False):
                text += "（圖資更新失敗）"
            self.world_label.setText(text)
            self.world_label.setToolTip("共用地圖與使用者指定基地位置；沒有主機存檔時間。\n資源為靜態／候選點，礦量、鑽機、玩家與庫存狀態未知。\n請選擇主機存檔以追蹤最新世界狀態。")
            return
        text += f" · {self.snapshot.saved_at.astimezone():%m/%d %H:%M}"
        if getattr(self, "data_stale", False):
            text += "（資料過期）"
        self.world_label.setText(text)
        self.world_label.setToolTip(f"存檔：{self.snapshot.saved_at.astimezone():%Y-%m-%d %H:%M:%S}\n更新：{self.snapshot.loaded_at.astimezone():%H:%M:%S}\n來源：{self.snapshot.source_path}")

    def status_changed(self, text):
        self.last_status = text
        self.refresh_button.setToolTip(text)
        if "背景讀取" in text:
            self.refresh_button.setText("更新中…")
        elif "過期" in text or "失敗" in text:
            self.data_stale = True
            self.refresh_button.setText("重試更新")
            self._update_header()
        elif "監看中" in text or "共用地圖" in text:
            self.refresh_button.setText("重新整理")

    def failed(self, message):
        self.last_error = message
        self.data_stale = True
        self.refresh_button.setText("重試更新")
        self.refresh_button.setToolTip(message.splitlines()[0])
        self._update_header()

    def save_settings(self):
        try:
            self.settings.save()
        except OSError as exc:
            self.status_changed(f"設定未儲存：{exc}")

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
                self.point_detail.setHtml("<p>點選標記查看資源。右鍵可管理基地。</p>")
        self._update_header()

    def update_inventory(self):
        if self.snapshot and getattr(self.snapshot, "is_shared_map", False):
            self.inventory_panel.set_missing_source()
        else:
            self.inventory_panel.set_snapshot(self.snapshot, self.current_base())

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
        heading = menu.addAction(f"目前基地：{base['name']}" if base else "尚未指定主要基地")
        heading.setEnabled(False)
        menu.addSeparator()
        add("新增基地（此處）…", lambda: self.base_dialog(x, y, 0), bool(self.snapshot))
        cursor = view.mapFromScene(x / 100, y / 100)
        target = view.hit_target(cursor)
        hit_id = value(target["members"][0], "id") if target else None
        clicked_base = next((b for b in self.bases() if b["id"] == hit_id), None)
        if clicked_base is None:
            clicked_base = next((b for b in self.bases() if (view.mapFromScene(b["x"] / 100, b["y"] / 100) - cursor).manhattanLength() <= 22), None)
        if clicked_base:
            add(f"編輯「{clicked_base['name']}」…", lambda: self.base_dialog(clicked_base["x"], clicked_base["y"], clicked_base.get("z", 0), existing=clicked_base))
            add("設為主要基地", lambda: self.change_base(clicked_base["id"]))
            add(f"刪除「{clicked_base['name']}」", lambda: self.remove_base(clicked_base))
        elif base:
            add(f"將「{base['name']}」移到此處", lambda: self.move_base(base, x, y))
        if self.bases():
            submenu = QMenu("切換基地", menu)
            menu.addMenu(submenu)
            menu._base_submenu = submenu  # Keep the PySide wrapper alive while the menu is open.
            for saved in self.bases():
                action = submenu.addAction(saved["name"])
                action.setCheckable(True)
                action.setChecked(bool(base and saved["id"] == base["id"]))
                action.triggered.connect(lambda _checked=False, ident=saved["id"]: self.change_base(ident) if self.snapshot and self.snapshot.world_id == world_id else None)
        add("從基地候選新增…", self.base_candidates, bool(self.snapshot) and not getattr(self.snapshot, "is_shared_map", False))
        menu.addSeparator()
        add("定位到主要基地", lambda: view.focus_point(self.current_base()) if self.current_base() else None, bool(base))
        add("顯示全圖", view.fit_map)
        add("放大", lambda: view.zoom(1.4))
        add("縮小", lambda: view.zoom(1 / 1.4))
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
        dialog.setWindowTitle("編輯基地" if existing else "新增基地")
        layout = QFormLayout(dialog)
        name = QLineEdit(existing["name"] if existing else "我們家" if not self.bases() else "前哨基地")
        layout.addRow("基地名稱", name)
        fields = []
        for label, initial in [("X（遊戲 cm）", x), ("Y（遊戲 cm）", y), ("Z（遊戲 cm）", z)]:
            spin = QDoubleSpinBox()
            spin.setRange(-100000000, 100000000)
            spin.setDecimals(1)
            spin.setValue(float(initial or 0))
            layout.addRow(label, spin)
            fields.append(spin)
        layout.addRow(QLabel("可先點選資源／基地候選，或在地圖按右鍵選位置。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and name.text().strip():
            if not self.snapshot or self.snapshot.world_id != world_id:
                self.status_changed("世界已變更，請在目前世界重新指定基地。")
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
        dialog.setWindowTitle("部署物聚集候選 · 請確認哪一個是家")
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("依 100 m 水平範圍聚集部署物；這是候選，並非已確認的基地。"))
        listing = QListWidget()
        for cluster in clusters[:30]:
            import statistics
            center = [statistics.median(p[i] for p in cluster["points"]) for i in range(3)]
            item = QListWidgetItem(f"{len(cluster['points'])} 個部署物 · {self.map.grid(*center[:2])} · X {center[0]:,.0f}, Y {center[1]:,.0f}")
            item.setData(Qt.ItemDataRole.UserRole, center)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and listing.currentItem():
            if not self.snapshot or self.snapshot.world_id != world_id:
                self.status_changed("世界已變更，請重新取得目前世界的基地候選。")
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
        for point in self.points:
            group = kind_group(point)
            if group not in allowed:
                continue
            if not self.candidates_check.isChecked() and str(value(point, "confidence", "")) in ("candidate", "inferred", "estimated", "候選", "推定"):
                continue
            if mineral and str(value(point, "resource_type", "")) != mineral:
                continue
            ds = str(value(point, "drill_status", "unknown"))
            if drill and (group != "deep" or not (ds == drill or drill == "installed" and (ds.startswith("已放置") or ds in ("inferred_installed", "推定已放置")) or drill == "not_installed" and ds in ("未放置", "absent") or drill == "unknown" and ds in ("無法確認", "未知"))):
                continue
            if query and query not in f"{value(point, 'name', '')} {value(point, 'resource_type', '')} {value(point, 'id', '')} {self.map.grid(value(point, 'x', 0), value(point, 'y', 0))}".casefold():
                continue
            selected.append(point)
        self.map.set_points(selected)
        self.visible_points = selected
        self.result_label.setText(f"顯示 {len(selected):,} / {len(self.points):,} 個點位")

    def show_point(self, point):
        self.selected_point = point
        group = kind_group(point)
        x, y, z = (value(point, k, 0) for k in ("x", "y", "z"))
        info = [f"<h3>{esc(value(point, 'name', '位置'))}</h3>", f"<p>{esc(KIND_LABELS.get(group, group))} · {esc(self.map.grid(x, y))}</p>", f"<p>X {x:,.1f}<br>Y {y:,.1f}<br>Z {z:,.1f} cm</p>"]
        if group not in ("cave", "base", "player", "deployable", "drill"):
            info.append(f"<p>剩餘：{esc(remaining_text(point))}</p>")
        if group == "deep":
            info.append(f"<p>鑽機：{esc(display(value(point, 'drill_status', 'unknown')))}</p>")
        base = self.current_base()
        if base:
            info.append(f"<p>距 {esc(base['name'])}：{math.hypot(x-base['x'], y-base['y'])/100:,.0f} m<br>（水平直線距離）</p>")
        detail = value(point, "detail", "")
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False, indent=2, default=str)
        info.extend([f"<p>{esc(detail).replace(chr(10), '<br>')}</p>", f"<p>可信度：{esc(display(value(point, 'confidence', 'unknown')))}<br>來源：{esc(value(point, 'source', '未知'))}</p>"])
        self.point_detail.setHtml("".join(info))

    @staticmethod
    def mission_active(mission):
        return mission.get("status_key") in ("available", "active") or str(mission.get("status", "")).lower() in ("available", "in_progress", "active", "可接取", "進行中", "可接")

    def rebuild_missions(self, *_):
        if not self.snapshot:
            return
        from .missions import build_missions
        self.missions = build_missions(self.snapshot, goal=self.goal_combo.currentData(), context={"active_base": self.current_base()})
        if getattr(self.snapshot, "is_shared_map", False):
            for mission in self.missions:
                mission.update(status="未連接存檔 · 攻略預覽", status_key="unknown", status_label="未連接存檔 · 攻略預覽",
                               status_reason="共用地圖未連接主機存檔，無法判定可接條件、進度或完成狀態。", progress=[])
        self.filter_missions()

    def filter_missions(self, *_):
        if not hasattr(self, "mission_list"):
            return
        previous_id = self.mission_list.currentItem().data(Qt.ItemDataRole.UserRole).get("id") if self.mission_list.currentItem() else None
        self.mission_list.blockSignals(True)
        self.mission_list.clear()
        query = self.mission_search.text().casefold()
        for mission in self.missions:
            if not getattr(self.snapshot, "is_shared_map", False) and not self.all_missions_check.isChecked() and not self.mission_active(mission):
                continue
            if query and query not in json.dumps(mission, ensure_ascii=False, default=str).casefold():
                continue
            status = mission.get("status_label", display(mission.get("status", "")))
            title = mission.get("title", mission.get("name", mission.get("id", "任務")))
            summary = str(mission.get("summary", ""))
            item = QListWidgetItem(f"{title}\n{status}\n{summary[:100]}")
            item.setData(Qt.ItemDataRole.UserRole, mission)
            self.mission_list.addItem(item)
        self.mission_list.blockSignals(False)
        row = next((i for i in range(self.mission_list.count()) if self.mission_list.item(i).data(Qt.ItemDataRole.UserRole).get("id") == previous_id), 0)
        if self.mission_list.count():
            self.mission_list.setCurrentRow(row)
        else:
            self.mission_route.clear()
            self.mission_detail.setHtml("<p>目前篩選下沒有已確認可接／進行中的任務。可勾選「包含已完成、未解鎖與其他狀態」查看條件與資料缺口。</p>")

    def show_mission(self, item, previous=None):
        if item is None:
            return
        from .missions import mission_html
        mission = item.data(Qt.ItemDataRole.UserRole)
        self.mission_detail.setHtml(mission_html(mission))
        if self.snapshot:
            self.mission_route.set_mission(self.snapshot, mission, self.catalog, self.bases(), active_base=self.current_base())

    def mission_link(self, url):
        if url.scheme() == "map":
            from PySide6.QtCore import QUrlQuery
            query = QUrlQuery(url)
            try:
                point = {"name": "任務記錄位置", "kind": "resource", "x": float(query.queryItemValue("x")), "y": float(query.queryItemValue("y")), "z": float(query.queryItemValue("z") or 0), "detail": "任務記錄物件座標；不代表已驗證入口或可通行路線。", "source": "目前任務存檔"}
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
        dialog.setWindowTitle("資料覆蓋、來源與診斷")
        dialog.resize(920, 660)
        layout = QVBoxLayout(dialog)
        detail = QTextBrowser()
        source = "公開共用地圖設定（未連接主機存檔）" if getattr(self.snapshot, "is_shared_map", False) else str(self.snapshot.source_path) if self.snapshot else None
        info = {"世界": self.snapshot.name if self.snapshot else None, "來源": source, "存檔覆蓋": self.snapshot.coverage if self.snapshot else None, "地圖覆蓋": self.catalog.get("coverage", {}), "地圖來源": self.catalog.get("sources", []), "警告": self.snapshot.warnings if self.snapshot else [], "最近錯誤": getattr(self, "last_error", ""), "原始存檔": "全程唯讀；APP設定與快照另存 LocalAppData/IcarusHelper"}
        info["任務位置覆蓋"] = self.mission_route.route.get("coverage", {})
        info["物資統計覆蓋"] = self.inventory_panel.summary.get("coverage", {}) if self.inventory_panel.summary else {"狀態": self.inventory_panel.status.text()}
        if self.missions:
            info["任務版本"] = {key: self.missions[0].get(key) for key in ("source_version", "source_date", "source_build", "installed_build", "version_compatible")}
        detail.setPlainText(json.dumps(info, ensure_ascii=False, indent=2, default=str))
        layout.addWidget(detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        self.inventory_panel.shutdown()
        self.controller.close()
        super().closeEvent(event)


STYLESHEET = """
QWidget { background:#101923; color:#dce6ef; font-family:'Microsoft JhengHei UI','Segoe UI'; font-size:13px; }
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
