"""Render real archived player health bars without opening or restoring a world."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QFontDatabase, QImage, QTextDocument
from PySide6.QtWidgets import QApplication

from icarus_helper.backup_health_bars import PlayerHealthDelegate
from icarus_helper.world_backups import BackupStore
from icarus_helper.world_backups_view import WorldBackupsPanel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/backup-health-bars-20261102")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    store = BackupStore()
    selected = json.loads((Path.home() / ".icarus-helper/settings.json").read_text(encoding="utf-8-sig"))["selected_source"]
    worlds = [world for world in store.worlds() if Path(world["source"]).resolve() == Path(selected).resolve()]
    assert len(worlds) == 1
    world = worlds[0]
    summary = store.summary(world["world_id"])
    samples = [("alive", "20261031_021945_363000_20a22f2dbddfd789.json"),
               ("zero", "20261031_224531_283000_cb1184d526da05b1.json")]
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    # The headless platform may not discover Windows fonts automatically.
    for filename in ("msjh.ttc", "segoeui.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft JhengHei UI", 9))
    # Read the literal theme without loading unrelated map/live data modules.
    source = ast.parse((ROOT / "icarus_helper/ui.py").read_text(encoding="utf-8-sig"))
    theme = next(ast.literal_eval(node.value) for node in source.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "STYLESHEET" for target in node.targets))
    app.setStyleSheet(theme)
    panel = WorldBackupsPanel(None)
    panel.poll.stop()
    # Render an already read-only summary. Disable every I/O entry point for QA.
    panel.refresh = lambda *_: None
    panel.snapshot = {"world_id": world["world_id"], "name": world["name"],
                      "source_path": Path(world["source"])}
    panel.world_label.setText("世界存檔 · " + world["name"])
    panel.target_label.setText("唯讀歷史備份血條驗證")
    panel._set_summary(summary)
    assert isinstance(panel.table.itemDelegateForColumn(4), PlayerHealthDelegate)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "retention_days": summary["retention_days"],
              "version_count": len(summary["versions"]), "cases": [], "classification": "Qt component rendering, not desktop input"}
    for label, ident in samples:
        row = next(index for index, version in enumerate(summary["versions"]) if version["id"] == ident)
        version = summary["versions"][row]
        path = Path(version["path"])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        for size_name, width, height in [("wide", 1440, 950), ("narrow", 1050, 760)]:
            panel.resize(width, height)
            panel.show()
            panel.table.selectRow(row)
            panel.table.scrollToItem(panel.table.item(row, 4))
            app.processEvents()
            images = []
            for index in range(len(version["player_preview"]["players"])):
                picture = panel.player_detail.document().resource(QTextDocument.ResourceType.ImageResource,
                                                                 QUrl(f"backup-health://selected/{index}"))
                assert isinstance(picture, QImage) and not picture.isNull()
                images.append({"width": picture.width(), "height": picture.height()})
            assert len(images) == 2
            target = args.output / f"{label}-{size_name}.png"
            assert panel.grab().save(str(target))
            report["cases"].append({"version": ident, "layout": size_name, "detail_health_images": images,
                                    "table_row_height": panel.table.rowHeight(row), "image": str(target),
                                    "players": [{key: player.get(key) for key in ("name", "health", "alive", "is_currently_playing")}
                                                for player in version["player_preview"]["players"]]})
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before == version["sha256"]
    panel.shutdown()
    panel.close()
    report.update(success=True, archived_bytes_unchanged=True, original_world_restore_performed=False)
    (args.output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("success", "retention_days", "version_count", "archived_bytes_unchanged")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
