"""Observe two real public-world polls with isolated Qt settings and caches."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from icarus_helper.release_sync import REMOTE_SOURCE
from icarus_helper.settings import Settings
from icarus_helper.ui import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = {"success": False, "observations": [], "kind": "real_public_source_and_isolated_qt", "error": ""}
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="icarus-remote-polls-") as directory:
        window = MainWindow(REMOTE_SOURCE, Settings(Path(directory) / "settings.json"))
        def finish():
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
            app.quit()
        def observe(payload):
            snap = payload["snapshot"]
            if not getattr(snap, "is_remote", False):
                return
            record = {"checked_at": datetime.now(timezone.utc).isoformat(), "sha256": snap.sha256,
                      "saved_at": snap.saved_at.isoformat(), "header": window.world_label.text(),
                      "stale": getattr(snap, "remote_stale", True),
                      "restore_disabled": not window.world_backups_panel.restore_button.isEnabled()}
            result["observations"].append(record)
            if len(result["observations"]) >= 2:
                result["success"] = all(not x["stale"] and x["restore_disabled"] for x in result["observations"])
                result["background_poll_ms"] = window.controller.remote_poll.interval()
                result["content_advanced"] = result["observations"][0]["sha256"] != snap.sha256
                finish()
        window.controller.loaded.connect(observe)
        window.controller.failed.connect(lambda message: result.update(error=message.splitlines()[0]))
        QTimer.singleShot(185000, finish)
        app.exec()
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
