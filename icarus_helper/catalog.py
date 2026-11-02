"""Static atlas + current-world resource reconciliation. Unreal coordinates are cm.

Only known save records establish dynamic depletion and drill state. Static map
nodes can establish a generated mineral type, but absence from a save is unknown.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import struct
from typing import Any

from .models import Resource, WorldSnapshot


CACHE_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "IcarusHelper" / "catalog_cache"
CATALOG_BUILD = "26313227"
MINERAL_NAMES = {
    "Gold": "金", "Copper": "銅", "Iron": "鐵", "Aluminium": "鋁", "Aluminum": "鋁", "Platinum": "白金",
    "Titanium": "鈦", "Coal": "煤", "Sulfur": "硫磺", "Sulphur": "硫磺", "Silica": "矽石",
    "Stone": "石材", "Oxite": "氧礦", "Salt": "鹽", "Scoria": "火山渣", "Obsidian": "黑曜石",
    "Clay": "黏土", "Ice": "冰", "Exotic": "異質", "RedExotic": "紅異質", "Uranium": "鈾", "Bauxite": "鋁土", "Metal": "鐵",
}

MAP_BOUNDS = {
    # Each mapping is independently checked against asset/save cave anchors.
    "prometheus": (-403200, -403200, 403200, 403200),
    "olympus": (-403200, -403200, 403200, 403200),
    "styx": (-403200, -403200, 403200, 403200),
}


def installed_game_build() -> str | None:
    locations = [Path(r"C:\Program Files (x86)\Steam\steamapps\appmanifest_1149460.acf")]
    override = os.environ.get("ICARUS_STEAM_MANIFEST")
    if override:
        locations.insert(0, Path(override))
    for path in locations:
        try:
            match = re.search(r'"buildid"\s+"(\d+)"', path.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
        except OSError:
            continue
    return None


def _cache() -> Path:
    local = CACHE_ROOT / CATALOG_BUILD
    bundled = Path(__file__).resolve().parent / "atlas" / CATALOG_BUILD
    local_metadata = _read(local / "prometheus_nodes.json")
    bundled_metadata = _read(bundled / "prometheus_nodes.json")
    if local_metadata is None:
        return bundled
    if bundled_metadata is None:
        return local
    def revision(metadata):
        return int(metadata.get("schema_version", 0)), str(metadata.get("generated_at", ""))
    return local if revision(local_metadata) >= revision(bundled_metadata) else bundled


def _read(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback


def _map_key(name: str) -> str:
    value = name.lower()
    for key in ("prometheus", "olympus", "styx", "elysium"):
        if key in value:
            return key
    return {"terrain_019": "prometheus", "terrain_016": "olympus", "terrain_008": "styx"}.get(value, value)


def _mineral_name(key: str) -> str:
    stem = key.split("_")[0]
    return MINERAL_NAMES.get(stem, key)


def _foliage_lookup() -> dict[str, list[dict]]:
    by_mesh = defaultdict(list)
    for row in _read(_cache() / "foliage_lookup.json", []):
        by_mesh[row.get("mesh")].append(row)
    return by_mesh


def _foliage_label(row: dict, mesh: str) -> str:
    value = str(row.get("view_trace_actor") or mesh)
    words = {"Berry": "漿果", "Wheat": "小麥", "Corn": "玉米", "Carrot": "胡蘿蔔", "Pumpkin": "南瓜", "Potato": "馬鈴薯",
             "Tomato": "番茄", "Cocoa": "可可", "Coffee": "咖啡", "Soy": "黃豆", "Tea": "茶", "Coconut": "椰子",
             "Mushroom": "蘑菇", "Reed": "蘆葦", "Sponge": "海綿", "Aspen": "白楊", "Pine": "松樹", "Fir": "冷杉",
             "Palm": "棕櫚", "Oxite": "氧礦", "Silica": "矽石", "Sulfur": "硫磺", "Salt": "鹽", "Stone": "石材",
             "Rock": "岩石", "Obsidian": "黑曜石", "Scoria": "火山渣", "Tree": "樹木", "Fiber": "纖維植物"}
    return next((label for word, label in words.items() if word.lower() in value.lower()), str(row.get("name") or mesh.split("/")[-1]))


def _world_point(public: dict) -> tuple[float, float, float]:
    return float(public["x"]) / 8192 * 806400 - 403200, float(public["y"]) / 8192 * 806400 - 403200, 0.0


def _distance(a, b) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) / 100


def _calibration(anchors: list[dict], candidates: list[dict]) -> dict:
    if not anchors or not candidates:
        return {"verified": False, "anchors": 0, "reason": "尚無足夠遊戲座標校準"}
    errors = sorted(min(_distance(anchor, candidate) for candidate in candidates) for anchor in anchors)
    close = [distance for distance in errors if distance < 30]
    return {"verified": len(close) >= 3 and len(close) >= len(errors) * 0.65,
            "anchors": len(errors), "within_30m": len(close), "median_error_m": round(statistics.median(errors), 3),
            "p90_error_m": round(errors[min(len(errors) - 1, int(len(errors) * 0.9))], 3),
            "basis": "遊戲資產／存檔入口與社群地穴位置比對；格位為16×16，世界範圍±403200 cm"}


def _tables() -> tuple[dict, dict]:
    data_root = _cache() / "game_data" / "World"
    distribution = _read(data_root / "D_VoxelDistributionRegion.json", {})
    setup = _read(data_root / "D_VoxelSetupData.json", {})
    pools = {}
    for row in distribution.get("Rows", []):
        entries = []
        for key, weight in row.get("VoxelDistribution", {}).items():
            match = re.search(r'RowName="([^"]+)', key)
            if match:
                entries.append((match.group(1), max(float(weight), 1.0)))
        pools[row["Name"]] = entries
    density_default = setup.get("Defaults", {}).get("DensityMultiplier", 1.0)
    density = {row["Name"]: row.get("DensityMultiplier", density_default) for row in setup.get("Rows", [])}
    return pools, density


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def select_mineral(seed: int, entries: list[tuple[str, float]]) -> str:
    """Reproduce the installed game's FRandomStream weighted selection routine."""
    state = ((int(seed) & 0xFFFFFFFF) * 196314165 + 907633515) & 0xFFFFFFFF
    fraction = _f32((state >> 9) / 8388608)
    roll = int(_f32(fraction * _f32(sum(weight for _, weight in entries) + 1)))
    cumulative = 0.0
    for mineral, weight in entries:
        cumulative += weight
        if roll <= cumulative:
            return mineral
    raise ValueError("礦種生成抽樣超出已驗證資源池範圍")


def load_catalog(map_name: str, world_seed: int | None = None) -> dict[str, Any]:
    key = _map_key(map_name)
    public = _read(_cache() / f"{key}_public.json", {})
    provenance = _read(_cache() / f"{key}_public_provenance.json", {})
    installed = _read(_cache() / f"{key}_nodes.json", {})
    actual_build = installed_game_build()
    compatible = actual_build is None or actual_build == installed.get("game_build", CATALOG_BUILD)
    if not compatible:
        # The weighted-generation routine and row indices are version-specific.
        # Never seed old map data with a newer game's world state.
        installed = {}
    caves = []
    for row in public.get("caves", []):
        x, y, z = _world_point(row)
        ident = str(row.get("gid") or row.get("id"))
        cave = {"id": "atlas:cave:" + str(row.get("id")), "kind": "cave", "name": "地穴 " + ident,
                "resource_type": "地穴", "x": x, "y": y, "z": z, "confidence": "候選",
                "remaining_status": "採集狀態未知", "source": "Icarus Intel 社群地圖",
                "detail": f"社群地穴點位；礦點模板 {row.get('oc', '未知')}、深層礦床模板 {row.get('doc', '未知')}。模板數不代表本世界剩餘量。" + str(row.get("n", "")),
                "public": row}
        caves.append(cave)
    assets = [row for row in installed.get("points", []) if row.get("kind") == "cave"]
    calibration = _calibration(assets, caves)
    used = set()
    for anchor in assets:
        nearest = min(caves, key=lambda row: _distance(anchor, row)) if caves else None
        if nearest is not None and _distance(anchor, nearest) < 30 and nearest["id"] not in used:
            used.add(nearest["id"])
            nearest.update({"x": anchor["x"], "y": anchor["y"], "z": anchor["z"], "actor_path": anchor["actor_path"],
                            "confidence": "已確認", "source": "本機遊戲地圖資產＋Icarus Intel 編號"})
            nearest["detail"] += " 入口位置已由目前安裝版本的地圖資產校準。"
        else:
            caves.append({**anchor, "id": "asset:" + anchor["actor_path"], "name": "地穴入口 " + anchor["id"],
                          "resource_type": "地穴", "confidence": "已確認", "remaining_status": "採集狀態未知",
                          "source": "本機遊戲地圖資產", "detail": "遊戲地圖入口；尚未可靠對應社群地穴編號。"})
    resources = []
    pools, _ = _tables()
    foliage_lookup = _foliage_lookup()
    unclassified_groups = 0
    invalid_region_groups = 0
    for row in installed.get("points", []):
        if row.get("kind") not in ("ore", "tree", "plant", "surface_resource", "water"):
            continue
        if row.get("kind") != "ore":
            kind = row["kind"]
            bounds = row.get("region_bounds")
            if bounds and (not all(math.isfinite(float(value)) for value in bounds.values()) or
                           not (-403200 <= row["x"] <= 403200 and -403200 <= row["y"] <= 403200) or
                           bounds["min_x"] > bounds["max_x"] or bounds["min_y"] > bounds["max_y"]):
                invalid_region_groups += 1
                continue
            matches = foliage_lookup.get(row.get("mesh"), [])
            if kind != "water" and (not matches or not any(str(match.get("view_trace_actor", "None")) != "None" and not match.get("disabled") for match in matches)):
                # A visual rock or bush is not necessarily a harvestable resource.
                unclassified_groups += 1
                continue
            names = {"tree": "樹木生成分布", "plant": "植物生成分布", "surface_resource": "地表資源生成分布", "water": "水源"}
            mesh_name = str(row.get("mesh", "")).split("/")[-1]
            label = _foliage_label(matches[0], str(row.get("mesh"))) if matches else "水源"
            detail = "遊戲地圖水體錨點；不代表完整水域邊界或已驗證可飲用性。" if kind == "water" else (
                f"群組原始生成 {row.get('generated_instance_count', '未知')} 個實例；不是目前剩餘量或物品產量。"
                "標記位於分布矩形中心，不是單株位置；可採集性以遊戲物件設定為準。" + mesh_name)
            resources.append({**row, "id": "asset:" + row["actor_path"], "name": label + "分布" if kind != "water" else label,
                              "resource_type": label, "confidence": "已確認", "remaining_status": "目前剩餘實例未知",
                              "flod_record_index": matches[0]["record_index"] if len(matches) == 1 else None,
                              "flod_name": matches[0]["name"] if len(matches) == 1 else "",
                              "source": "本機遊戲地圖資產（原始生成分布）", "detail": detail})
            continue
        mineral = select_mineral(row["seed"] + world_seed, pools[row["pool"]]) if world_seed is not None and row.get("pool") in pools and row.get("seed") is not None else "未知"
        resources.append({**row, "id": "asset:" + row["actor_path"], "resource_type": mineral,
                          "name": ("地穴" if "Caves" in str(row.get("pool")) else "一般") + _mineral_name(mineral) + "礦",
                          "confidence": "已確認" if mineral != "未知" else "礦種待還原", "remaining_status": "採集狀態未知",
                          "source": "本機遊戲地圖資產＋本世界生成種子" if mineral != "未知" else "本機遊戲地圖資產",
                          "detail": "未出現在存檔採掘記錄中不等於未採集；礦點生成位置／礦種與剩餘量分開判定。"})
    for collection, kind, name in (("ores", "deep_candidate", "深層礦床候選"), ("geysers", "geyser", "酵素間歇泉候選"),
                                  ("oilGeysers", "oil", "石油泉候選"), ("metaDeposits", "exotic", "異質／特殊資源候選")):
        for row in public.get(collection, []):
            if "x" not in row or "y" not in row:
                continue
            x, y, z = _world_point(row)
            resources.append({"id": f"atlas:{collection}:{row.get('code', row.get('id', len(resources)))}", "kind": kind,
                              "name": name, "resource_type": str(row.get("variant", "未知")), "x": x, "y": y, "z": z,
                              "confidence": "候選", "remaining_status": "是否生成／數量未知", "drill_status": "無法確認" if kind == "deep_candidate" else "",
                              "source": "Icarus Intel 社群地圖", "detail": "僅為可能位置；是否在本世界生成須依目前存檔確認。", "public": row})
    coverage = {"static_tiles_loaded": installed.get("tiles_loaded", 0), "static_tiles_expected": installed.get("tiles_expected", 0),
                "asset_catalog_schema": installed.get("schema_version"), "asset_catalog_generated_at": installed.get("generated_at"),
                "height_tiles_loaded": installed.get("height_tiles_loaded", 0), "height_tiles_expected": installed.get("height_tiles_expected", 0),
                "static_ordinary_nodes": sum(row.get("kind") == "ore" for row in installed.get("points", [])),
                "static_resource_groups": dict(Counter(row.get("kind") for row in installed.get("points", []) if row.get("kind") not in ("ore", "cave"))),
                "generated_foliage_instances": sum(row.get("generated_instance_count", 0) for row in installed.get("points", [])),
                "unclassified_environment_groups": unclassified_groups,
                "excluded_unverified_region_groups": invalid_region_groups,
                "asset_cave_entrances": len(assets), "community_caves": len(public.get("caves", [])),
                "community_counts": provenance.get("counts", {}), "catalog_game_build": installed.get("game_build", ""),
                "installed_game_build": actual_build, "asset_build_compatible": compatible,
                "calibration": calibration, "limitations": ["植物、樹木與地表資源使用資產群組分布矩形；不宣稱逐株位置或完整水域邊界。",
                                                          "一般礦剩餘量須依存檔採掘記錄；無記錄的礦點採集狀態未知。",
                                                          "社群特殊資源點僅為候選；全圖普通礦生成資產目前支援 Prometheus。"]}
    if not compatible:
        coverage["limitations"].insert(0, f"目前遊戲 build {actual_build} 與資產快取 {CATALOG_BUILD} 不符；已停用舊版資產及礦種還原，請重新建立並驗證資料。")
    bounds = MAP_BOUNDS.get(key)
    return {"map": map_name, "map_key": key, "game_build": installed.get("game_build", CATALOG_BUILD),
            "bounds": dict(zip(("min_x", "min_y", "max_x", "max_y"), bounds)) if bounds else {},
            "calibrated": calibration["verified"], "background_path": str(_cache() / f"{key}.jpg") if (_cache() / f"{key}.jpg").exists() else "",
            "caves": caves, "resources": resources, "coverage": coverage,
            "sources": [provenance, {"source": "本機 ICARUS 地圖資產", "build": installed.get("game_build"), "tiles": installed.get("tiles_loaded", 0),
                                      "generated_at": installed.get("generated_at"), "coordinate_rule": installed.get("coordinate_rule"),
                                      "source_packages": installed.get("source_packages", []), "manifest_sha256": installed.get("source_manifest_sha256")} ]}


def _resource(item: dict) -> Resource:
    fields = Resource.__dataclass_fields__
    values = {key: value for key, value in item.items() if key in fields and key != "raw"}
    values.setdefault("id", str(item.get("actor_path", "unknown")))
    values.setdefault("name", "資源")
    values.setdefault("resource_type", "未知")
    values.setdefault("kind", "resource")
    values.setdefault("x", 0.0)
    values.setdefault("y", 0.0)
    values.setdefault("z", 0.0)
    values["raw"] = item.get("raw", {key: value for key, value in item.items() if key not in fields})
    return Resource(**values)


def _seed(snapshot: WorldSnapshot) -> int | None:
    if getattr(snapshot, "game_state_seed", None) is not None:
        return int(snapshot.game_state_seed)
    for row in snapshot.raw_records:
        mode = row.get("GameModeRecord", {})
        if "GameStateSeed" in mode:
            return int(mode["GameStateSeed"])
    return None


def merge_catalog(snapshot: WorldSnapshot, catalog: dict | None = None) -> list[Resource]:
    seed = _seed(snapshot)
    catalog = catalog if catalog is not None else load_catalog(snapshot.map_name, seed)
    if _map_key(str(catalog.get("map", ""))) != _map_key(snapshot.map_name):
        replacement = load_catalog(snapshot.map_name, seed)
        catalog.clear()
        catalog.update(replacement)
    if seed is not None and any(row.get("kind") == "ore" and row.get("resource_type") == "未知" for row in catalog.get("resources", [])):
        seeded = load_catalog(snapshot.map_name, seed)
        catalog.clear()
        catalog.update(seeded)
    dynamic = list(snapshot.resources)
    by_path = {row.actor_path: row for row in dynamic if row.actor_path}
    game_caves = [row for row in dynamic if row.kind == "cave"]
    public_caves = [row for row in catalog.get("caves", []) if row.get("public")]
    if not catalog.get("calibrated") and game_caves:
        anchors = [{"x": row.x, "y": row.y} for row in game_caves]
        calibration = _calibration(anchors, public_caves)
        catalog["calibrated"] = calibration["verified"]
        catalog["coverage"]["calibration"] = calibration
    pools, density = _tables()
    validation = Counter()
    foliage_records = {}
    for raw in snapshot.raw_records:
        if not str(raw.get("_component", "")).endswith("FLODTileRecorderComponent"):
            continue
        record = raw.get("Record", {})
        for row in record.get("Records", []):
            foliage_records[(str(record.get("TileName", "")).lower(), row.get("RecordIndex"))] = row
    for item in catalog.get("resources", []):
        if item.get("kind") in ("tree", "plant", "surface_resource", "water"):
            if item.get("actor_path") not in by_path:
                generated = _resource(item)
                state = foliage_records.get((str(item.get("tile", "")).lower(), item.get("flod_record_index")))
                if state is not None and item.get("flod_record_index") is not None:
                    destroyed = state.get("DestroyedInstanceIndices", [])
                    original = item.get("generated_instance_count", 0)
                    valid = all(isinstance(index, int) and 0 <= index < original for index in destroyed)
                    if valid and not state.get("DynamicInstances") and not state.get("Instances"):
                        generated.remaining = original - len(set(destroyed))
                        generated.remaining_unit = "生成實例（非物品產量）"
                        generated.remaining_status = "未列為已破壞；不含動態再生"
                        generated.source += "＋目前存檔破壞索引"
                        generated.detail += (f" 已以唯一 mesh→FLOD資料列→同區塊RecordIndex對應：原生成{original}、"
                                             f"已記錄破壞{len(set(destroyed))}、未列為破壞{int(generated.remaining)}；不推算物品產量。")
                        generated.raw["destroyed_instance_count"] = len(set(destroyed))
                        validation["foliage_unique_joins"] += 1
                    elif not valid:
                        validation["foliage_invalid_indices"] += 1
                dynamic.append(generated)
                by_path[generated.actor_path] = generated
            continue
        if item.get("kind") != "ore":
            continue
        existing = by_path.get(item.get("actor_path"))
        mineral = item.get("resource_type", "未知")
        if existing is not None:
            saved = existing.raw.get("VoxelSaveData", {})
            override = saved.get("VoxelResourceOverride")
            if override and override != "None":
                mineral = override
            existing.resource_type = mineral
            existing.name = ("地穴" if "Caves" in str(item.get("pool")) else "一般") + _mineral_name(mineral) + "礦"
            existing.source = "目前存檔＋本機地圖資產＋本世界生成種子"
            existing.raw["static_metadata"] = {"pool": item.get("pool"), "seed": item.get("seed"), "tile": item.get("tile")}
            total = saved.get("TotalResourceCount")
            if total and total > 20 and mineral in density:
                validation["density_matches" if abs(total - 20 * density[mineral]) < .01 else "density_mismatches"] += 1
        else:
            generated = _resource(item)
            dynamic.append(generated)
            by_path[generated.actor_path] = generated
    for item in catalog.get("caves", []):
        existing = by_path.get(item.get("actor_path"))
        if existing is None and game_caves:
            nearest = min(game_caves, key=lambda row: math.hypot(row.x - item["x"], row.y - item["y"]))
            if math.hypot(nearest.x - item["x"], nearest.y - item["y"]) < 3000:
                existing = nearest
        if existing is not None:
            if item.get("public"):
                existing.name = item["name"]
                existing.raw["public_cave"] = item["public"]
            continue
        if item.get("confidence") == "候選" and not catalog.get("calibrated"):
            continue
        static = _resource(item)
        dynamic.append(static)
        if static.actor_path:
            by_path[static.actor_path] = static
    # Only display a candidate if no actual dynamic object of this resource family
    # exists nearby. Candidate presence never establishes a generated deposit.
    families = {"deep_candidate": "deep_deposit", "geyser": "geyser", "oil": "oil", "exotic": "exotic"}
    for item in catalog.get("resources", []):
        if item.get("kind") in ("ore", "tree", "plant", "surface_resource", "water") or not catalog.get("calibrated"):
            continue
        family = families.get(item.get("kind"))
        if any(row.kind == family and math.hypot(row.x - item["x"], row.y - item["y"]) < 4000 for row in snapshot.resources):
            continue
        dynamic.append(_resource(item))
    caves = [row for row in dynamic if row.kind == "cave"]
    bins = defaultdict(list)
    for cave in caves:
        bins[(int(cave.x // 20000), int(cave.y // 20000))].append(cave)
    summaries = defaultdict(Counter)
    for node in dynamic:
        metadata = node.raw.get("static_metadata", node.raw)
        if node.kind != "ore" or "Caves" not in str(metadata.get("pool", "")):
            continue
        cell = (int(node.x // 20000), int(node.y // 20000))
        nearby = [cave for dx in (-1, 0, 1) for dy in (-1, 0, 1) for cave in bins.get((cell[0] + dx, cell[1] + dy), [])]
        if not nearby:
            continue
        cave = min(nearby, key=lambda row: math.hypot(row.x - node.x, row.y - node.y))
        distance = math.hypot(cave.x - node.x, cave.y - node.y) / 100
        if distance <= 150:
            node.raw["cave_association"] = {"id": cave.id, "name": cave.name, "distance_m": round(distance, 1), "confidence": "推定；僅依鄰近入口"}
            node.detail += f" 鄰近 {cave.name} 約{distance:.0f}m（推定關聯，非已確認洞內隸屬）。"
            summaries[cave.id][_mineral_name(node.resource_type)] += 1
    for cave in caves:
        if cave.id in summaries:
            cave.raw["nearby_ore_counts"] = dict(summaries[cave.id])
            cave.detail += " 鄰近礦點（推定關聯，非剩餘量）：" + "、".join(f"{name} {count}點" for name, count in summaries[cave.id].most_common())
    coverage = copy.deepcopy(catalog.get("coverage", {}))
    coverage.update({"world_seed_available": seed is not None, "generated_mineral_validation": dict(validation),
                     "merged_resource_count": len(dynamic), "ordinary_mineral_types": dict(Counter(row.resource_type for row in dynamic if row.kind == "ore"))})
    snapshot.coverage.update(coverage)
    if not coverage.get("asset_build_compatible", True):
        snapshot.warnings.append(coverage["limitations"][0])
    if validation.get("density_mismatches"):
        snapshot.warnings.append("部分礦物密度與還原規則不符，請查核遊戲版本與礦種覆寫。")
    return dynamic


def merge_static_resources(snapshot: WorldSnapshot) -> WorldSnapshot:
    snapshot.resources = merge_catalog(snapshot)
    return snapshot
