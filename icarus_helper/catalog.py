"""Static atlas + current-world resource reconciliation. Unreal coordinates are cm.

Only known save records establish dynamic depletion and drill state. Static map
nodes can establish a generated mineral type, but absence from a save is unknown.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import hashlib
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
    "Gold": "Gold", "Copper": "Copper", "Iron": "Iron", "Aluminium": "Aluminium", "Aluminum": "Aluminium", "Platinum": "Platinum",
    "Titanium": "Titanium", "Coal": "Coal", "Sulfur": "Sulfur", "Sulphur": "Sulfur", "Silica": "Silica",
    "Stone": "Stone", "Oxite": "Oxite", "Salt": "Salt", "Scoria": "Scoria", "Obsidian": "Obsidian",
    "Clay": "Clay", "Ice": "Ice", "Exotic": "Exotics", "RedExotic": "Red exotics", "Uranium": "Uranium", "Bauxite": "Bauxite", "Metal": "Iron",
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


def _bundled_cache() -> Path:
    return Path(__file__).resolve().parent / "atlas" / CATALOG_BUILD


def _cache() -> Path:
    local = CACHE_ROOT / CATALOG_BUILD
    bundled = _bundled_cache()
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


def _background_info(key: str) -> dict:
    """Select imagery separately from the generated-resource atlas revision.

    A pre-update local node cache must not hide a bundled Null Sector image.
    Only the image hash in its own provenance can establish the full tileset.
    """
    roots = dict.fromkeys((_cache(), CACHE_ROOT / CATALOG_BUILD, _bundled_cache()))
    candidates = []
    for root in roots:
        path = root / f"{key}.jpg"
        provenance = _read(root / f"{key}_public_provenance.json", {})
        if not isinstance(provenance, dict):
            provenance = {}
        metadata = provenance.get("background", {})
        if not isinstance(metadata, dict):
            metadata = {}
        expected_hash = str(metadata.get("sha256", "")).lower()
        full = key == "prometheus" and metadata.get("tileset") == "prometheus2"
        try:
            if not path.is_file():
                continue
            # A complete-tileset claim needs a matching receipt; a partially
            # replaced or corrupted file must not be accepted as full terrain.
            if full and not expected_hash:
                continue
            if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                continue
        except OSError:
            continue
        candidates.append({**metadata, "source": provenance.get("source", "Icarus Intel"),
                           "path": str(path), "includes_null_sector": full,
                           "hash_verified": bool(expected_hash),
                           "fetched_at": metadata.get("fetched_at", provenance.get("fetched_at", "")),
                           "url_pattern": metadata.get("url_pattern", provenance.get("background_url_pattern", ""))})
    if not candidates:
        return {}
    complete = [info for info in candidates if info["includes_null_sector"]]
    if not complete:
        return candidates[0]

    def fetched_at(info):
        try:
            value = datetime.fromisoformat(str(info["fetched_at"]).replace("Z", "+00:00"))
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    return max(complete, key=fetched_at)


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
    words = {"Berry": "Berries", "Wheat": "Wheat", "Corn": "Corn", "Carrot": "Carrot", "Pumpkin": "Pumpkin", "Potato": "Potato",
             "Tomato": "Tomato", "Cocoa": "Cocoa", "Coffee": "Coffee", "Soy": "Soybean", "Tea": "Tea", "Coconut": "Coconut",
             "Mushroom": "Mushroom", "Reed": "Reed", "Sponge": "Sponge", "Aspen": "Aspen", "Pine": "Pine", "Fir": "Fir",
             "Palm": "Palm", "Oxite": "Oxite", "Silica": "Silica", "Sulfur": "Sulfur", "Salt": "Salt", "Stone": "Stone",
             "Rock": "Rock", "Obsidian": "Obsidian", "Scoria": "Scoria", "Tree": "Trees", "Fiber": "Fiber plant"}
    return next((label for word, label in words.items() if word.lower() in value.lower()), str(row.get("name") or mesh.split("/")[-1]))


def _world_point(public: dict) -> tuple[float, float, float]:
    return float(public["x"]) / 8192 * 806400 - 403200, float(public["y"]) / 8192 * 806400 - 403200, 0.0


def _distance(a, b) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) / 100


def _calibration(anchors: list[dict], candidates: list[dict]) -> dict:
    if not anchors or not candidates:
        return {"verified": False, "anchors": 0, "reason": "Insufficient game coordinate calibration"}
    errors = sorted(min(_distance(anchor, candidate) for candidate in candidates) for anchor in anchors)
    close = [distance for distance in errors if distance < 30]
    return {"verified": len(close) >= 3 and len(close) >= len(errors) * 0.65,
            "anchors": len(errors), "within_30m": len(close), "median_error_m": round(statistics.median(errors), 3),
            "p90_error_m": round(errors[min(len(errors) - 1, int(len(errors) * 0.9))], 3),
            "basis": "Game asset / saved entrances compared with community cave locations; 16×16 grid, world bounds ±403200 cm"}


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
    raise ValueError("Mineral generation sample is outside the verified resource pool")


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
        cave = {"id": "atlas:cave:" + str(row.get("id")), "kind": "cave", "name": "Cave " + ident,
                "resource_type": "Cave", "x": x, "y": y, "z": z, "confidence": "Candidate",
                "remaining_status": "Mining status unknown", "source": "Icarus Intel community map",
                "detail": f"Community cave location; ore template {row.get('oc', 'Unknown')}, deep deposit template {row.get('doc', 'Unknown')}. Template counts do not represent remaining resources in this world. " + str(row.get("n", "")),
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
                            "confidence": "Confirmed", "source": "Local game map assets + Icarus Intel numbering"})
            nearest["detail"] += " Entrance calibrated against map assets from the installed game version."
        else:
            caves.append({**anchor, "id": "asset:" + anchor["actor_path"], "name": "Cave entrance " + anchor["id"],
                          "resource_type": "Cave", "confidence": "Confirmed", "remaining_status": "Mining status unknown",
                          "source": "Local game map assets", "detail": "Game map entrance; no verified community cave number association."})
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
            names = {"tree": "Tree spawn distribution", "plant": "Plant spawn distribution", "surface_resource": "Surface resource spawn distribution", "water": "Water"}
            mesh_name = str(row.get("mesh", "")).split("/")[-1]
            label = _foliage_label(matches[0], str(row.get("mesh"))) if matches else "Water"
            detail = "Game map water anchor; not a full water boundary or verified drinking source." if kind == "water" else (
                f"Group originally generated {row.get('generated_instance_count', 'Unknown')} instances; not a current remaining count or item yield."
                " Marker is at the distribution rectangle center, not an individual plant. Harvestability depends on game object settings. " + mesh_name)
            resources.append({**row, "id": "asset:" + row["actor_path"], "name": label + " distribution" if kind != "water" else label,
                              "resource_type": label, "confidence": "Confirmed", "remaining_status": "Current remaining instances unknown",
                              "flod_record_index": matches[0]["record_index"] if len(matches) == 1 else None,
                              "flod_name": matches[0]["name"] if len(matches) == 1 else "",
                              "source": "Local game map assets (original spawn distribution)", "detail": detail})
            continue
        mineral = select_mineral(row["seed"] + world_seed, pools[row["pool"]]) if world_seed is not None and row.get("pool") in pools and row.get("seed") is not None else "Unknown"
        resources.append({**row, "id": "asset:" + row["actor_path"], "resource_type": mineral,
                          "name": ("Cave " if "Caves" in str(row.get("pool")) else "Surface ") + _mineral_name(mineral),
                          "confidence": "Confirmed" if mineral != "Unknown" else "Mineral identity unresolved", "remaining_status": "Mining status unknown",
                          "source": "Local game map assets + world generation seed" if mineral != "Unknown" else "Local game map assets",
                          "detail": "Absence from mining records does not imply unmined. Spawn position / mineral identity and remaining quantity are assessed separately."})
    for collection, kind, name in (("ores", "deep_candidate", "Deep deposit candidate"), ("geysers", "geyser", "Enzyme geyser candidate"),
                                  ("oilGeysers", "oil", "Oil geyser candidate"), ("metaDeposits", "exotic", "Exotic / special resource candidate")):
        for row in public.get(collection, []):
            if "x" not in row or "y" not in row:
                continue
            x, y, z = _world_point(row)
            resources.append({"id": f"atlas:{collection}:{row.get('code', row.get('id', len(resources)))}", "kind": kind,
                              "name": name, "resource_type": str(row.get("variant", "Unknown")), "x": x, "y": y, "z": z,
                              "confidence": "Candidate", "remaining_status": "Spawn status / quantity unknown", "drill_status": "Cannot confirm" if kind == "deep_candidate" else "",
                              "source": "Icarus Intel community map", "detail": "Possible location only; generation in this world must be checked against the current save.", "public": row})
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
                "calibration": calibration, "limitations": ["Plants, trees and surface resources use asset group distribution rectangles, not individual plant locations or complete water boundaries.",
                                                          "Ore remaining amounts require saved mining records; mining status is unknown where records are absent.",
                                                          "Community special resource points are candidates only. Full-map ore spawn assets currently support Prometheus."]}
    if not compatible:
        coverage["limitations"].insert(0, f"Installed game build {actual_build} differs from asset cache {CATALOG_BUILD}; outdated assets and mineral reconstruction disabled. Rebuild and verify the data.")
    background = _background_info(key)
    coverage["background"] = background
    bounds = MAP_BOUNDS.get(key)
    return {"map": map_name, "map_key": key, "game_build": installed.get("game_build", CATALOG_BUILD),
            "bounds": dict(zip(("min_x", "min_y", "max_x", "max_y"), bounds)) if bounds else {},
            "calibrated": calibration["verified"], "background_path": background.get("path", ""),
            "caves": caves, "resources": resources, "coverage": coverage,
            "sources": [provenance, {"source": "Local ICARUS map assets", "build": installed.get("game_build"), "tiles": installed.get("tiles_loaded", 0),
                                      "generated_at": installed.get("generated_at"), "coordinate_rule": installed.get("coordinate_rule"),
                                      "source_packages": installed.get("source_packages", []), "manifest_sha256": installed.get("source_manifest_sha256")} ]}


def _resource(item: dict) -> Resource:
    fields = Resource.__dataclass_fields__
    values = {key: value for key, value in item.items() if key in fields and key != "raw"}
    values.setdefault("id", str(item.get("actor_path", "unknown")))
    values.setdefault("name", "Resource")
    values.setdefault("resource_type", "Unknown")
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
    if seed is not None and any(row.get("kind") == "ore" and row.get("resource_type") == "Unknown" for row in catalog.get("resources", [])):
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
                        generated.remaining_unit = "Spawned instances (not item yield)"
                        generated.remaining_status = "Not recorded as destroyed; excludes dynamic regrowth"
                        generated.source += " + current saved destruction indices"
                        generated.detail += (f" Unique mesh → FLOD row → same-tile RecordIndex match: originally spawned {original}, "
                                             f"recorded destroyed {len(set(destroyed))}, not recorded destroyed {int(generated.remaining)}; no item yield inferred.")
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
        mineral = item.get("resource_type", "Unknown")
        if existing is not None:
            saved = existing.raw.get("VoxelSaveData", {})
            override = saved.get("VoxelResourceOverride")
            if override and override != "None":
                mineral = override
            existing.resource_type = mineral
            existing.name = ("Cave " if "Caves" in str(item.get("pool")) else "Surface ") + _mineral_name(mineral)
            existing.source = "Current save + local map assets + world generation seed"
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
        if item.get("confidence") == "Candidate" and not catalog.get("calibrated"):
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
            node.raw["cave_association"] = {"id": cave.id, "name": cave.name, "distance_m": round(distance, 1), "confidence": "Inferred; nearby entrance only"}
            node.detail += f" Near {cave.name}, about {distance:.0f} m (inferred association; not confirmed inside the cave)."
            summaries[cave.id][_mineral_name(node.resource_type)] += 1
    for cave in caves:
        if cave.id in summaries:
            cave.raw["nearby_ore_counts"] = dict(summaries[cave.id])
            cave.detail += " Nearby ore points (inferred association, not remaining quantities): " + ", ".join(f"{name}: {count} points" for name, count in summaries[cave.id].most_common())
    coverage = copy.deepcopy(catalog.get("coverage", {}))
    coverage.update({"world_seed_available": seed is not None, "generated_mineral_validation": dict(validation),
                     "merged_resource_count": len(dynamic), "ordinary_mineral_types": dict(Counter(row.resource_type for row in dynamic if row.kind == "ore"))})
    snapshot.coverage.update(coverage)
    if not coverage.get("asset_build_compatible", True):
        snapshot.warnings.append(coverage["limitations"][0])
    if validation.get("density_mismatches"):
        snapshot.warnings.append("Some mineral densities differ from reconstruction rules. Check game version and mineral overrides.")
    return dynamic


def merge_static_resources(snapshot: WorldSnapshot) -> WorldSnapshot:
    snapshot.resources = merge_catalog(snapshot)
    return snapshot
