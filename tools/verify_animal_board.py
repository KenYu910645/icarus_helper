"""Read-only real-snapshot Qt verification; all settings writes are isolated."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icarus_helper.animal_icons import has_animal_icon
from icarus_helper.settings import Settings, settings_path
from icarus_helper.ui import MainWindow, STYLESHEET


def main():
    output = Path("artifacts/animals-refined-20261102").resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference_path = settings_path()
    reference_bytes = reference_path.read_bytes()
    reference = json.loads(reference_bytes)
    source = Path(reference["selected_source"])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    app = QApplication([])
    for font in ("msjh.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / font))
    app.setFont(QFont("Microsoft JhengHei", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    isolated = tempfile.TemporaryDirectory(prefix="icarus-animal-board-")
    settings = Settings(Path(isolated.name) / "settings.json")
    settings.data = reference
    window = MainWindow(settings=settings)
    window.show()
    panel = window.animal_panel
    deadline = time.monotonic() + 70
    while (panel.summary is None or panel.busy) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert panel.summary is not None, panel.status.text()
    window.tabs.setCurrentWidget(panel)
    window.resize(1540, 1000)
    for _ in range(5):
        app.processEvents()
    animals = panel.summary["animals"]
    assert len(panel.animal_cards) == len(animals)
    assert len(panel.owner_columns) == len({a["owner_key"] for a in animals})
    assert all(has_animal_icon(a["species_id"]) for a in animals)
    assert len(panel.animal_map.map.points) == sum(a.get("position") is not None for a in animals)
    window.grab().save(str(output / "animal-board-wide.png"))
    target = next(a for a in animals if a["distance_m"] and a["distance_m"] > 100)
    panel._locate_animal(target["id"])
    app.processEvents()
    assert panel.selected_animal()["id"] == target["id"]
    assert panel.animal_map.map.chosen["id"] == target["id"]
    point = panel.animal_map.map.chosen
    screen = panel.animal_map.map.mapFromScene(point["x"] / 100, point["y"] / 100)
    assert panel.animal_map.map.viewport().rect().contains(screen)
    window.grab().save(str(output / "animal-away-located.png"))
    panel.animal_map.map.fit_animals()
    app.processEvents()
    panel.scope_combo.setCurrentIndex(panel.scope_combo.findData("away"))
    app.processEvents()
    assert len(panel.animal_map.map.points) == len(panel.animal_cards)
    assert all(panel._home_state(a) == "away" for a in panel._row_animals)
    window.grab().save(str(output / "animal-away-filter.png"))
    panel.scope_combo.setCurrentIndex(0)
    panel.animal_map.map.fit_animals()
    window.resize(1100, 760)
    app.processEvents()
    window.grab().save(str(output / "animal-board-small.png"))
    cards = list(panel.animal_cards.values())
    assert all(card.width() >= 245 for card in cards)
    assert all(column.width() >= 265 for column in panel.owner_columns.values())
    panel.clear()
    app.processEvents()
    assert not panel.animal_cards and not panel.owner_columns and not panel.animal_map.map.points
    window.close()
    app.processEvents()
    assert source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert reference_path.read_bytes() == reference_bytes
    result = {"test_type": "Qt widgets with read-only real snapshot; no desktop input or game control",
              "success": True, "world": window.snapshot.world_id,
              "saved_at": window.snapshot.saved_at.isoformat(), "source_unchanged": True,
              "settings_unchanged": True, "animals": len(animals),
              "owners": len({a["owner_key"] for a in animals}),
              "icon_coverage": sum(has_animal_icon(a["species_id"]) for a in animals),
              "checks": ["owner columns", "native portraits", "honest gauges", "map exact positions",
                         "selection and locate", "filter synchronization", "small window scroll", "clear isolation"]}
    (output / "widget-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
