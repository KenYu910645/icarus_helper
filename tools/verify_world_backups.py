"""Isolated packaged-worker checks and optional read-only production inventory."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from icarus_helper.backup_daemon import BackupRunner
from icarus_helper.saves import load_snapshot
from icarus_helper.world_backups import BackupStore, game_processes
from test_saves import save_fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/world-backups-20261102")
    parser.add_argument("--live-inventory", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "game_processes": game_processes()}

    source = output / "synthetic-source/帳號/Prospects/測試世界.json"
    def world(marker):
        save_fixture(source, records=[("/Script/Icarus.GameModeStateRecorderComponent",
                                      {"GameModeRecord": {"GameStateSeed": 343919}, "Marker": marker})])
        return load_snapshot(source)
    snapshot = world(1)
    store = BackupStore(output / "packaged-worker")
    store.register(snapshot)
    def tick():
        if args.exe:
            result = subprocess.run([str(args.exe.resolve()), "--backup-daemon", "--backup-root", str(store.root), "--backup-once"],
                                    timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0, "封裝背景模式未成功退出"
        else:
            BackupRunner(store).tick()
    tick()
    first = store.summary(snapshot.world_id)["versions"][0]
    # Exercise a new valid version, then make the earlier *capture* expire.
    world(2)
    tick()
    sidecar = Path(first["path"]).with_suffix(".metadata.json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["captured_at_utc"] = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    assert store.retention_preview(snapshot.world_id, 7)["count"] == 1
    store.set_retention(snapshot.world_id, 7)
    tick()
    final = store.summary(snapshot.world_id)
    assert final["version_count"] == 1 and not Path(first["path"]).exists()
    assert final["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    report["packaged_worker"] = {"frozen_exe": str(args.exe) if args.exe else None,
                                  "capture_new_version": True, "retention_deleted_only_expired": True,
                                  "retained_latest": final["version_count"], "source_unchanged_by_cleanup": True}

    if args.live_inventory:
        live = BackupStore()
        legacy = json.loads((live.root / "config.json").read_text(encoding="utf-8-sig"))
        actual = Path(legacy["source"])
        before = hashlib.sha256(actual.read_bytes()).hexdigest()
        live_snapshot = load_snapshot(actual)
        context = live.register(live_snapshot)
        start = time.perf_counter()
        summary = live.summary(live_snapshot.world_id)
        first_seconds = time.perf_counter() - start
        start = time.perf_counter()
        cached = live.summary(live_snapshot.world_id)
        cached_seconds = time.perf_counter() - start
        after = hashlib.sha256(actual.read_bytes()).hexdigest()
        assert before == after
        assert cached["version_count"] == summary["version_count"]
        report["live_readonly_inventory"] = {
            "world_id": live_snapshot.world_id, "world_name": live_snapshot.name,
            "version_count": summary["version_count"], "verified_count": sum(v["verified"] for v in summary["versions"]),
            "total_bytes": summary["total_bytes"], "retention_days": context["retention_days"],
            "initial_seconds": first_seconds, "cached_seconds": cached_seconds,
            "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": before == after,
            "error_versions": [{"id": v["id"], "error": v["error"]} for v in summary["versions"] if v["error"]],
        }
    report["success"] = True
    (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
