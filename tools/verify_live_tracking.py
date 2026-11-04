"""唯讀核對所選世界的冷／暖讀取、同次狀態與位置，以及隔離 Qt 畫面。

預設要求遊戲世界已開啟，絕不啟動、切換或控制遊戲。
--allow-game-off 僅允許記錄存檔備援驗收，live_verified 仍為 False。
這是原生來源／存檔／Qt 元件驗證，不代稱與遊戲介面逐項比對或桌面操作。
所有私人輸出留在 Git 排除的 artifacts；報告不保存完整角色、帳號或原始記憶體。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIELDS = ("health", "food", "water", "oxygen", "stamina")


def sha_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def snapshot_fingerprint(snapshot):
    """Hash locally without writing any raw snapshot or source identity."""
    return hashlib.sha256(pickle.dumps(snapshot, protocol=5)).hexdigest()


def valid_position(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(
        isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) for number in value)


def valid_pair(pair):
    return isinstance(pair, dict) and all(
        isinstance(pair.get(key), (int, float)) and not isinstance(pair[key], bool)
        and math.isfinite(pair[key]) and pair[key] >= 0 for key in ("value", "maximum")) and pair["maximum"] > 0


def sample_metadata(sample, wall_seconds, fields):
    """The reader already redacts identities; retain only bounded diagnostics."""
    animals, players = sample.get("animals", {}), sample.get("players", {})
    coverage = sample.get("coverage", {})
    keys = ("read_bytes", "read_calls", "read_seconds", "cache_mode", "scan_complete",
            "locator_revalidation_complete", "cache_invalidated", "object_slots_checked",
            "object_generations_checked", "changed_slots_checked", "locator_revalidation_count", "discovery_mode",
            "incremental_slots_checked", "inventory_age_seconds", "discovery_interval_seconds",
            "full_inventory_interval_seconds", "expected_animals", "sampled_animals",
            "expected_players", "sampled_players", "missing_animals", "duplicate_guids", "duplicate_players")
    return {"status": sample.get("status"), "reason": sample.get("reason"),
            "sample_time": sample.get("sample_time"), "world_verified": sample.get("world_verified") is True,
            "wall_seconds": round(wall_seconds, 6),
            "coverage": {key: coverage[key] for key in keys if key in coverage},
            "animal_count": len(animals), "player_count": len(players),
            "valid_stat_pairs": {key: sum(valid_pair(row.get(key)) for row in animals.values()) for key in fields},
            "valid_animal_positions": sum(valid_position(row.get("position")) for row in animals.values()),
            "valid_player_positions": sum(valid_position(row.get("position")) for row in players.values())}


def reductions(cold, warm):
    output = {}
    for key in ("read_bytes", "read_calls", "read_seconds"):
        first, second = cold.get("coverage", {}).get(key), warm.get("coverage", {}).get(key)
        if isinstance(first, (int, float)) and isinstance(second, (int, float)):
            output[key] = {"cold": first, "warm": second, "saved": first - second,
                           "reduction_percent": round((first - second) * 100 / first, 3) if first else None}
    output["timing_assertion"] = False
    output["note"] = "僅要求暖讀 calls／bytes 低於冷讀；秒數受外部負載影響，只記錄不設固定門檻。"
    return output


def owner_source_roots(source, config, world_id):
    configured = config.get("animal_owner_sources", [])
    if not configured:
        return None
    local = [str(source.parent.parent.parent)] if source.parent.name.casefold() == "prospects" else []
    return local + [{"world_id": world_id, "path": path} for path in configured if isinstance(path, str)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-tracking-20261102"))
    parser.add_argument("--reference-settings", type=Path, help="預設使用固定使用者設定；只讀此檔。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-live", dest="require_live", action="store_true", help="要求遊戲來源完整可用（預設）。")
    mode.add_argument("--allow-game-off", dest="require_live", action="store_false", help="遊戲未執行時可驗收存檔備援；不記作遊戲驗收。")
    parser.set_defaults(require_live=True)
    parser.add_argument("--expected-animals", type=int, default=12, help="本次案例的馴養動物數，非遊戲固定規則。")
    parser.add_argument("--expected-players", type=int, default=2, help="存檔可辨識玩家數，包含可能離線者。")
    parser.add_argument("--expected-live-fields", type=int, default=58, help="本案有效五類狀態組數；12隻中 Cow 氧氣／耐力未知，故為58。")
    parser.add_argument("--expected-live-animal-positions", type=int, help="預設與 expected-animals 相同。")
    parser.add_argument("--minimum-live-players", type=int, default=1, help="不要求離線玩家具有遊戲位置。")
    parser.add_argument("--fields", nargs="+", choices=FIELDS, default=list(FIELDS), help="需核對的狀態；變更時同步調整 expected-live-fields。")
    args = parser.parse_args(argv)
    for key in ("expected_animals", "expected_players", "expected_live_fields", "minimum_live_players"):
        if getattr(args, key) < 0:
            parser.error(key.replace("_", "-") + " 不可小於零")
    if args.expected_live_animal_positions is None:
        args.expected_live_animal_positions = args.expected_animals
    if args.expected_live_animal_positions < 0:
        parser.error("expected-live-animal-positions 不可小於零")
    fields = tuple(dict.fromkeys(args.fields))

    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from icarus_helper.animals import summarize_animals
    from icarus_helper.animals_view import AnimalPanel
    from icarus_helper.catalog import load_catalog
    from icarus_helper import live_animal_stats
    from icarus_helper.saves import SaveChangedError, load_snapshot
    from icarus_helper.settings import Settings, settings_path
    from icarus_helper.ui import STYLESHEET

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference_path = args.reference_settings or settings_path()
    checks, required = {}, set()
    report = {"success": False, "live_verified": False, "fallback_verified": False, "result_mode": "failed",
              "started_at": datetime.now(timezone.utc).isoformat(), "require_live": args.require_live,
              "test_type": "目前所選世界唯讀快照＋同程序冷暖讀取＋隔離Qt元件；非遊戲操作／遊戲UI逐項比對／Windows桌面操作",
              "checks": checks, "requested_fields": fields,
              "case_expectations": {"animals": args.expected_animals, "saved_players": args.expected_players,
                                    "live_fields": args.expected_live_fields, "live_animal_positions": args.expected_live_animal_positions,
                                    "minimum_live_players": args.minimum_live_players},
              "raw_profiles_included": False, "raw_memory_included": False,
              "sampling_note": "三次獨立取樣數值可隨遊戲改變；僅用第三次summary與其內含live_stats核對同次值，不跨次要求相等。"}

    def check(name, condition, *, need=True):
        checks[name] = bool(condition)
        if need:
            required.add(name)
        return checks[name]

    app, panel, snapshot = None, None, None
    reference_bytes, source, fingerprint = None, None, None
    try:
        reference_bytes = reference_path.read_bytes()
        reference_data = json.loads(reference_bytes)
        source = Path(reference_data.get("selected_source", ""))
        if not source.is_file():
            raise ValueError("目前設定沒有可讀取的所選世界存檔。")
        report["source_before_sha256"] = sha_file(source)
        app = QApplication.instance() or QApplication([])
        for font in ("msjh.ttc", "segoeui.ttf"):
            font_path = Path("C:/Windows/Fonts") / font
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Microsoft JhengHei", 10))
        app.setStyle("Fusion")
        app.setStyleSheet(STYLESHEET)

        with tempfile.TemporaryDirectory(prefix="icarus-live-tracking-check-") as directory:
            private = Path(directory)
            isolated_path = private / "settings.json"
            isolated_path.write_bytes(reference_bytes)
            settings = Settings(isolated_path)
            for attempt in range(5):
                try:
                    snapshot = load_snapshot(source, cache_dir=private / "snapshots")
                    break
                except SaveChangedError:
                    if attempt == 4:
                        raise
                    app.processEvents()
                    time.sleep(.2)
            if snapshot is None:
                raise ValueError("未取得穩定快照。")
            fingerprint = snapshot_fingerprint(snapshot)
            check("stable_snapshot_loaded", bool(snapshot.sha256))
            config = settings.world(snapshot.world_id)
            bases = config.get("bases", [])
            base = next((row for row in bases if row.get("id") == config.get("active_base")), None)
            if base is None and not config.get("active_base") and len(bases) == 1:
                base = bases[0]
            base = dict(base, world_id=snapshot.world_id, map_name=snapshot.map_name) if base else None
            roots = owner_source_roots(source, config, snapshot.world_id)
            targets = live_animal_stats._targets(snapshot)
            required_fields = {
                ident: {key for key in fields if key not in ("oxygen", "stamina")
                        or target.get(key + "_applicable") is True}
                for ident, target in targets.items()}
            expected_by_type = {key: sum(key in keys for keys in required_fields.values()) for key in fields}
            report["catalog_applicable_fields"] = expected_by_type
            report["catalog_applicability_note"] = "生命／食物／水分；氧氣與耐力另依已驗證物種適用旗標。未知項不以placeholder分母當有效容量。"
            check("catalog_target_count_matches_case", len(targets) == args.expected_animals)
            check("catalog_applicable_count_matches_case", sum(expected_by_type.values()) == args.expected_live_fields)

            # Clear locators, never game state. All three reads remain in this
            # process and against this exact immutable selected-world snapshot.
            live_animal_stats._LOCATORS.clear()
            started = time.perf_counter()
            cold = live_animal_stats.read_live_animal_stats(snapshot)
            cold_meta = sample_metadata(cold, time.perf_counter() - started, fields)
            started = time.perf_counter()
            warm = live_animal_stats.read_live_animal_stats(snapshot)
            warm_meta = sample_metadata(warm, time.perf_counter() - started, fields)
            started = time.perf_counter()
            summary = summarize_animals(snapshot, base, owner_profile_roots=roots)
            third = summary.get("live_stats", {})
            third_meta = sample_metadata(third, time.perf_counter() - started, fields)
            report["samples"] = {"cold": cold_meta, "warm": warm_meta, "summary_warm": third_meta}
            report["cold_warm_reduction"] = reductions(cold_meta, warm_meta)
            check("snapshot_not_mutated_by_readers", snapshot_fingerprint(snapshot) == fingerprint)

            animals, players = summary.get("animals", []), summary.get("players", [])
            check("case_expected_animal_count", len(animals) == args.expected_animals)
            check("case_expected_saved_player_count", len(players) == args.expected_players)
            check("same_snapshot_time", summary.get("coverage", {}).get("snapshot_time") == str(snapshot.saved_at))
            verified = third.get("world_verified") is True and third.get("status") in ("ok", "partial")
            game_off = all(row.get("status") == "not_running" for row in (cold, warm, third))
            live_required = args.require_live or not game_off
            check("all_three_samples_verified_world", all(row.get("world_verified") is True for row in (cold, warm, third)), need=live_required)
            check("cold_performs_full_inventory", cold.get("coverage", {}).get("cache_mode") == "full", need=live_required)
            check("warm_uses_revalidated_locators", warm.get("coverage", {}).get("cache_mode") == "warm"
                  and warm.get("coverage", {}).get("locator_revalidation_complete") is True, need=live_required)
            check("summary_uses_third_warm_sample", third.get("coverage", {}).get("cache_mode") == "warm", need=live_required)
            for key in ("read_bytes", "read_calls"):
                reduction = report["cold_warm_reduction"].get(key, {})
                check("warm_reduces_" + key, isinstance(reduction.get("cold"), int) and isinstance(reduction.get("warm"), int)
                      and 0 < reduction["warm"] < reduction["cold"], need=live_required)

            rows, pair_count, position_count, player_positions = [], 0, 0, 0
            field_counts = {key: 0 for key in fields}
            same_pairs, same_positions, same_players, no_spliced_sources = True, True, True, True
            for animal in animals:
                sampled = third.get("animals", {}).get(str(animal["id"]), {})
                stats = []
                for stat in animal.get("stats", []):
                    key = stat.get("id")
                    if key not in fields:
                        continue
                    stats.append({name: stat.get(name) for name in ("id", "value", "maximum", "maximum_status", "sample_time")})
                    pair = sampled.get(key, {})
                    if stat.get("maximum_status") == "live":
                        pair_count += 1
                        field_counts[key] += 1
                        same_pairs &= (verified and valid_pair(pair) and stat.get("value") == pair.get("value")
                                       and stat.get("maximum") == pair.get("maximum") and stat.get("sample_time") == third.get("sample_time")
                                       and "saved_value" in stat and "persistent_maximum" in stat)
                    elif valid_pair(pair):
                        no_spliced_sources = False
                if animal.get("position_source") == "live":
                    position_count += 1
                    same_positions &= (verified and valid_position(sampled.get("position")) and animal.get("position") == sampled["position"]
                                       and animal.get("position_sample_time") == (sampled.get("position_sample_time") or third.get("sample_time"))
                                       and "saved_position" in animal)
                rows.append({"id": animal["id"], "name": animal.get("name"), "species": animal.get("species"),
                             "owner_name": animal.get("owner_name"), "stats": stats,
                             "position": animal.get("position"), "position_source": animal.get("position_source"),
                             "position_sample_time": animal.get("position_sample_time"), "saved_position": animal.get("saved_position"),
                             "distance_m": animal.get("distance_m")})
            player_rows = []
            for player in players:
                sampled = third.get("players", {}).get(player["id"], {})
                if player.get("position_source") == "live":
                    player_positions += 1
                    same_players &= (verified and valid_position(sampled.get("position")) and player.get("position") == sampled["position"]
                                     and player.get("sample_time") == (sampled.get("sample_time") or third.get("sample_time"))
                                     and "saved_position" in player)
                player_rows.append({key: player.get(key) for key in ("id", "name", "position", "position_source", "sample_time", "saved_position", "local")})
            report.update(world_id=snapshot.world_id, map_name=snapshot.map_name,
                          snapshot_time=snapshot.saved_at.isoformat(), snapshot_sha256=snapshot.sha256,
                          animal_count=len(animals), player_count=len(players), live_stat_pairs=pair_count,
                          live_fields_by_type=field_counts, live_animal_positions=position_count, live_player_positions=player_positions,
                          animal_samples=rows, player_samples=player_rows)
            check("same_sample_value_max_pairs", same_pairs)
            check("same_sample_animal_positions", same_positions)
            check("same_sample_player_positions", same_players)
            check("available_pairs_not_left_as_saved", no_spliced_sources)
            check("requested_live_field_count", pair_count == args.expected_live_fields, need=live_required)
            check("all_applicable_fields_are_live", all(
                any(stat.get("id") == key and stat.get("maximum_status") == "live"
                    for stat in animal.get("stats", []))
                for animal in animals for key in required_fields.get(str(animal["id"]), set())), need=live_required)
            check("each_requested_field_count_matches_catalog", field_counts == expected_by_type, need=live_required)
            check("requested_live_animal_position_count", position_count == args.expected_live_animal_positions, need=live_required)
            check("minimum_live_player_positions", player_positions >= args.minimum_live_players, need=live_required)
            check("no_live_labels_when_game_off", not game_off or pair_count == position_count == player_positions == 0)

            # Apply the third summary directly: rendering must not cause a fourth
            # independent sample whose changing values could invalidate the audit.
            panel = AnimalPanel()
            panel.live_refresh.stop()
            panel.debounce.stop()
            panel.snapshot, panel.base = snapshot, base
            panel.owner_profile_roots = roots
            panel._map_catalog = load_catalog(snapshot.map_name, world_seed=snapshot.game_state_seed)
            panel.base_label.setText("馴養動物 · " + (base.get("name", "所選基地") if base else "尚未指定基地"))
            summary_before = deepcopy(summary)
            check("qt_accepts_summary", panel.set_summary(summary, saved_at=snapshot.saved_at))
            panel.resize(1700, 1050)
            panel.show()
            app.processEvents()
            panel.animal_map.map.fit_animals()
            app.processEvents()
            check("qt_one_card_per_animal", len(panel.animal_cards) == len(animals))
            check("qt_player_markers_match_known_positions", len(panel.animal_map.map.players) == sum(valid_position(p.get("position")) for p in players))
            images = []
            def capture(widget, name):
                success = widget.grab().save(str(output / name))
                check("screenshot_written_" + name, success)
                images.append(name)
            capture(panel, "animals-wide.png")
            if animals:
                panel._select_by_id(str(animals[0]["id"]))
                panel.stat_toggle.setChecked(True)
                app.processEvents()
                capture(panel, "animal-detail.png")
                capture(panel.detail_content, "animal-detail-content.png")
            if panel.animal_map.players:
                previous_id = panel._selected_id
                ordered = sorted(panel.animal_map.players, key=lambda row: row.get("position_source") != "live")
                selected_players = [ordered[0]]
                saved_player = next((row for row in ordered if row.get("position_source") != "live"), None)
                if saved_player is not None and saved_player is not ordered[0]:
                    selected_players.append(saved_player)
                for index, player_point in enumerate(selected_players):
                    panel.animal_map.map.chosen = player_point
                    panel.animal_map._point_selected(player_point)
                    panel.animal_map.map.focus_point(player_point)
                    app.processEvents()
                    capture(panel, "player-detail.png" if index == 0 else "player-save-detail.png")
                check("player_inspection_retains_animal_selection", panel._selected_id == previous_id)
            panel.animal_map.map.fit_animals()
            panel.resize(1100, 760)
            app.processEvents()
            capture(panel, "animals-small.png")
            check("qt_does_not_mutate_summary", summary == summary_before)
            check("qt_does_not_mutate_snapshot", snapshot_fingerprint(snapshot) == fingerprint)
            check("screenshots_saved", all((output / name).is_file() for name in images))
            report["screenshots"] = images
            report["screenshot_sha256"] = {name: sha_file(output / name) for name in images}
            panel.clear()
            check("clear_removes_dynamic_markers_and_owner_sources", panel.summary is None and panel.owner_profile_roots is None
                  and not panel.animal_cards and not panel.animal_map.map.points and not panel.animal_map.map.players)
            all_checks = all(checks[name] for name in required)
            report["live_verified"] = bool(verified and all_checks)
            report["fallback_verified"] = bool(not args.require_live and game_off and all_checks)
            report["success"] = report["live_verified"] or report["fallback_verified"]
            report["result_mode"] = "live" if report["live_verified"] else "save_fallback" if report["fallback_verified"] else "failed"
    except Exception as error:
        report.update(error_type=type(error).__name__, error="驗證未完成；未將失敗或備援資料標為遊戲驗收。")
    finally:
        if panel is not None:
            panel.close()
        if app is not None:
            app.processEvents()
        if source is not None:
            report["source_after_sha256"] = sha_file(source)
            report["source_unchanged"] = bool(report.get("source_before_sha256") and report["source_before_sha256"] == report["source_after_sha256"])
        report["source_change_note"] = "遊戲或其他外部程式可能於驗證期間寫入原始存檔；來源hash改變只記錄外部變動，不單憑此判定助手寫入。"
        if snapshot is not None and fingerprint is not None:
            check("final_snapshot_not_mutated", snapshot_fingerprint(snapshot) == fingerprint)
            report["snapshot_object_sha256"] = fingerprint
        try:
            settings_unchanged = reference_bytes is not None and reference_path.read_bytes() == reference_bytes
        except OSError:
            settings_unchanged = False
        check("reference_settings_unchanged", settings_unchanged)
        if not all(checks[name] for name in required):
            report.update(success=False, live_verified=False, fallback_verified=False, result_mode="failed")
        report["settings_unchanged"] = settings_unchanged
        report["failed_checks"] = [name for name in sorted(required) if not checks[name]]
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("success", "live_verified", "fallback_verified", "result_mode", "animal_count",
                     "player_count", "live_stat_pairs", "live_animal_positions", "live_player_positions", "settings_unchanged", "source_unchanged", "failed_checks")}, ensure_ascii=False))
    assert settings_unchanged, "原始設定於驗證期間改變"
    if not report["success"]:
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    main()
