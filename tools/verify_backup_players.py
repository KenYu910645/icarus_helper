"""Audit archived player previews against full decoding; never restore a world."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from icarus_helper.saves import load_snapshot
from icarus_helper.world_backups import BackupStore


def audit_live(output):
    store = BackupStore()
    selected = json.loads((Path.home() / ".icarus-helper/settings.json").read_text(encoding="utf-8-sig"))["selected_source"]
    worlds = [world for world in store.worlds() if Path(world["source"]).resolve() == Path(selected).resolve()]
    assert len(worlds) == 1
    world = worlds[0]
    started = time.perf_counter()
    summary = store.summary(world["world_id"])
    first_seconds = time.perf_counter() - started
    started = time.perf_counter()
    store.summary(world["world_id"])
    repeat_seconds = time.perf_counter() - started
    versions = summary["versions"]
    wanted = ["20261031_021945_363000_20a22f2dbddfd789.json", "20261031_224531_283000_cb1184d526da05b1.json"]
    samples = [version for name in wanted for version in versions if version["id"] == name]
    if versions:
        samples.append(versions[0])
    cases = []
    for version in samples:
        path = Path(version["path"])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        full = load_snapshot(path)
        members = {(str(member["UserID"]), member["ChrSlot"]): member
                   for member in full.prospect_info.get("AssociatedMembers", [])}
        expected = []
        for record in full.raw_records:
            if not str(record.get("_component", "")).endswith("PlayerStateRecorderComponent"):
                continue
            identity = record["PlayerCharacterID"]
            member = members[(str(identity["PlayerID"]), identity["ChrSlot"])]
            expected.append({"name": member["CharacterName"], "slot": identity["ChrSlot"],
                             "health": record.get("Health"), "alive": record.get("bIsAlive"),
                             "is_currently_playing": member.get("IsCurrentlyPlaying")})
        preview = version["player_preview"]
        actual = [{key: row.get(key) for key in ("name", "slot", "health", "alive", "is_currently_playing")}
                  for row in preview["players"]]
        assert sorted(actual, key=lambda row: row["slot"]) == sorted(expected, key=lambda row: row["slot"]), version["id"]
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        assert before == after == version["sha256"]
        cases.append({"version": version["id"], "saved_at": version["saved_at"], "players": actual,
                      "full_decoder_agrees": True, "archive_unchanged": True})
    assert any(row["health"] == 0 and row["alive"] is False for case in cases for row in case["players"])
    assert any(row["is_currently_playing"] is False for case in cases for row in case["players"])
    return {"world_name": world["name"], "retention_days": summary["retention_days"],
            "versions": len(versions), "initial_seconds": first_seconds, "cached_seconds": repeat_seconds,
            "preview_status_counts": {state: sum(v.get("player_preview", {}).get("status") == state for v in versions)
                                      for state in ("available", "partial", "unknown")}, "cases": cases}


def audit_packaged(executable, output):
    from test_saves import save_fixture
    source = output / "synthetic/帳號/Prospects/驗證世界.json"
    data = save_fixture(source, records=[
        ("/Script/Icarus.GameModeStateRecorderComponent", {"GameModeRecord": {"GameStateSeed": 918273}}),
        ("/Script/Icarus.PlayerStateRecorderComponent", {"PlayerStateRecorderVersion": 1, "ActorStateRecorderVersion": 3,
         "PlayerCharacterID": {"PlayerID": "synthetic-account", "ChrSlot": 0}, "Health": 180,
         "bIsAlive": True, "FoodLevel": 100.0, "WaterLevel": 80.0, "OxygenLevel": 70.0, "Location": (1, 2, 3)}),
        ("/Script/Icarus.PlayerStateRecorderComponent", {"PlayerStateRecorderVersion": 1, "ActorStateRecorderVersion": 3,
         "PlayerCharacterID": {"PlayerID": "synthetic-account", "ChrSlot": 1}, "Health": 0,
         "bIsAlive": False, "Location": (4, 5, 6)})])
    data["ProspectInfo"]["AssociatedMembers"] = [
        {"UserID": "synthetic-account", "ChrSlot": 0, "CharacterName": "合成角色甲", "IsCurrentlyPlaying": True},
        {"UserID": "synthetic-account", "ChrSlot": 1, "CharacterName": "合成角色乙", "IsCurrentlyPlaying": False}]
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    store = BackupStore(output / "packaged-worker")
    snapshot = load_snapshot(source)
    store.register(snapshot)
    before = source.read_bytes()
    result = subprocess.run([str(executable.resolve()), "--backup-daemon", "--backup-root", str(store.root), "--backup-once"],
                            timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0 and source.read_bytes() == before
    status = json.loads((store.root / "daemon-status.json").read_text(encoding="utf-8"))
    preview = status["worlds"][snapshot.world_id]["last_success"]["result"]["player_preview"]
    rows = {row["slot"]: row for row in preview["players"]}
    assert rows[0]["name"] == "合成角色甲" and rows[0]["health"] == 180 and rows[0]["alive"] is True
    assert rows[1]["health"] == 0 and rows[1]["alive"] is False and rows[1]["is_currently_playing"] is False
    assert "synthetic-account" not in json.dumps(preview)
    return {"exe": str(executable), "player_preview_packaged": True, "zero_and_false_preserved": True,
            "platform_identity_hidden": True, "source_unchanged": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/backup-player-details-20261102")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"checked_at": datetime.now(timezone.utc).isoformat()}
    if args.live:
        result["live_readonly"] = audit_live(args.output)
    if args.exe:
        result["packaged"] = audit_packaged(args.exe, args.output)
    result["success"] = True
    (args.output / "player-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
