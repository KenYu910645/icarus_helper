"""Read-only household stock totals from the same snapshot as the world map.

Only inventories owned by deployed containers/processors inside the selected
base's horizontal radius are included. Player, mount, dropped-item, pooled and
installed-equipment inventories are deliberately outside this scope.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .catalog import installed_game_build

CATEGORY_LABELS = {
    "resources": "Ores, Metals & Fuel", "construction": "Materials, Parts & Construction",
    "supplies": "Food & Medical Supplies", "gear": "Tools, Equipment & Ammunition", "other": "Other Items",
    "unknown": "Unrecognized Item",
}
CATEGORY_GROUPS = {
    "metals": "resources", "minerals": "resources",
    "materials": "construction", "building": "construction",
    "food": "supplies", "medical": "supplies",
    "tools": "gear", "equipment": "gear",
}
FORM_LABELS = {"ore": "Ore", "ingot": "Ingot", "processed": "Processed Material", "other": "Item"}
# D_InventoryID: general, processor, fuel, seed, fertilizer, turret ammo.
# Suit/equipment, machine upgrades, sockets and alteration slots are not stock.
STOCK_INVENTORY_IDS = frozenset({1, 6, 7, 15, 16, 24})
COMPONENTS = frozenset({"/Script/Icarus.DeployableRecorderComponent", "/Script/Icarus.DrillRecorderComponent"})


@lru_cache(maxsize=1)
def load_item_catalog() -> dict:
    return json.loads((Path(__file__).with_name("data") / "inventory_items.json").read_text(encoding="utf-8"))


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _count(value: Any) -> int | None:
    value = _number(value)
    return int(value) if value is not None and 0 <= value <= 2**31 - 1 and value.is_integer() else None


def _position(record: dict) -> tuple[float, float, float] | None:
    transform = record.get("ActorTransform")
    xyz = transform.get("Translation") if isinstance(transform, dict) else None
    if not isinstance(xyz, (list, tuple)) or len(xyz) != 3:
        return None
    result = tuple(_number(v) for v in xyz)
    return result if all(v is not None for v in result) else None


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _identity(record: dict) -> str:
    uid = record.get("IcarusActorGUID")
    if isinstance(uid, (int, str)) and str(uid) not in {"", "0", "-1", "None"}:
        return "actor:" + str(uid)
    path = record.get("ActorPathName")
    if path:
        return "actor-path:" + str(path)
    return "record:" + _fingerprint({k: record.get(k) for k in ("StaticItemDataRowName", "ActorTransform", "SavedInventories")})


def _item_id(slot: dict) -> str:
    value = slot.get("ItemStaticData", "")
    if isinstance(value, dict):
        value = value.get("RowName", "")
    return value if isinstance(value, str) and value.casefold() not in {"", "none", "null"} else ""


def _quantity(slot: dict, stack_index: int) -> int | None:
    dynamic = slot.get("DynamicData", slot.get("ItemDynamicData", []))
    values = []
    if isinstance(dynamic, list):
        values = [entry.get("Value") for entry in dynamic if isinstance(entry, dict) and entry.get("Index", entry.get("Type")) == stack_index]
    # Explicitly named stack fields are also safe; never use Location, durability,
    # weight, transmutable units or an inferred max stack as a substitute.
    if "StackCount" in slot:
        values.append(slot["StackCount"])
    counts = [_count(v) for v in values]
    if not counts or any(v is None for v in counts) or len(set(counts)) != 1:
        return None
    return counts[0]


def summarize_inventory(snapshot: Any, base: dict | None, radius_m: float = 100) -> dict:
    """Return chart rows and per-item/container detail without altering a save.

    Category rows group ore and ingots under the same material. Counts remain
    actual item counts, with no recipe conversion or metal-equivalent sum.
    ``None`` means an included stack has an unknown quantity; ``known_quantity``
    and ``known_values`` preserve the verifiable portion of such groups.
    """
    catalog = load_item_catalog()
    definitions = catalog["items"]
    # Unreal FName row identifiers compare without case. Real saves include e.g.
    # Homestead_WildFlower_Daisy while D_ItemsStatic uses Wildflower.
    canonical_ids = {key.casefold(): key for key in definitions}
    coverage = {
        "status": "ok", "reason": "", "base_name": str((base or {}).get("name", "")),
        "radius_m": radius_m, "distance_type": "Horizontal straight-line distance", "counted_containers": 0,
        "excluded_containers": 0, "outside_radius": 0, "missing_locations": 0,
        "excluded_inventory_slots": 0, "duplicate_records": 0, "conflicting_records": 0,
        "unknown_stacks": 0, "unknown_items": 0, "empty_slots": 0, "linked_item_inventories": 0,
        "conflicting_slots": 0, "malformed_inventories": 0, "unknown_inventory_ids": [],
        "game_build": catalog["game_build"], "verified_at": catalog["verified_at"],
        "snapshot_time": str(_get(snapshot, "saved_at", "")),
        "scope": "Storage, processor, fuel, seed, fertilizer and ammunition slots in deployed containers and equipment within the base radius",
        "warnings": [],
    }
    result = {"categories": [], "items": [], "containers": [], "coverage": coverage}
    radius = _number(radius_m)
    if radius is None or radius <= 0:
        coverage.update(status="invalid_radius", reason="Choose a radius greater than zero.")
        return result
    coverage["radius_m"] = radius
    if not isinstance(base, dict) or _number(base.get("x")) is None or _number(base.get("y")) is None:
        coverage.update(status="base_required", reason="Set a base on the map to view home inventory.")
        return result
    if (base.get("world_id") and base["world_id"] != _get(snapshot, "world_id")) or (base.get("map_name") and base["map_name"] != _get(snapshot, "map_name")):
        coverage.update(status="base_mismatch", reason="The base does not match the current world. Select the base again.")
        return result
    actual_build = installed_game_build()
    coverage["installed_game_build"] = actual_build
    if actual_build is not None and actual_build != catalog["game_build"]:
        coverage.update(status="unsupported_version", reason="The game version has changed; item quantity formats have not been verified.")
        return result

    records = _get(snapshot, "raw_records", [])
    if not isinstance(records, list):
        coverage.update(status="unavailable", reason="This snapshot has no readable container records.")
        return result
    coverage["records_with_inventory"] = sum(isinstance(r, dict) and isinstance(r.get("SavedInventories"), list) and bool(r["SavedInventories"]) for r in records)
    unique = {}
    conflicts = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("SavedInventories"), list) or not record["SavedInventories"]:
            continue
        if record.get("_component") not in COMPONENTS:
            coverage["excluded_containers"] += 1
            continue
        identity = _identity(record)
        signature = _fingerprint({k: record.get(k) for k in ("StaticItemDataRowName", "ActorTransform", "SavedInventories")})
        if identity in unique:
            if unique[identity][0] == signature:
                coverage["duplicate_records"] += 1
            else:
                conflicts.add(identity)
            continue
        unique[identity] = (signature, record)
    coverage["conflicting_records"] = len(conflicts)
    stacks = []
    for identity, (_, record) in unique.items():
        if identity in conflicts:
            continue
        xyz = _position(record)
        if xyz is None:
            coverage["missing_locations"] += 1
            continue
        distance = math.hypot(xyz[0] - base["x"], xyz[1] - base["y"]) / 100
        if distance > radius:
            coverage["outside_radius"] += 1
            continue
        containers = record["SavedInventories"]
        counted_inventories = []
        for inv in containers:
            if not isinstance(inv, dict) or not isinstance(inv.get("InventoryID"), int):
                coverage["malformed_inventories"] += 1
                continue
            inventory_id = inv["InventoryID"]
            if str(inventory_id) not in catalog["inventory_ids"] and inventory_id not in coverage["unknown_inventory_ids"]:
                coverage["unknown_inventory_ids"].append(inventory_id)
            if inventory_id in STOCK_INVENTORY_IDS:
                counted_inventories.append(inv)
            elif isinstance(inv.get("Slots", []), list):
                coverage["excluded_inventory_slots"] += len(inv.get("Slots", []))
        if not counted_inventories:
            continue
        container_item = str(record.get("StaticItemDataRowName", ""))
        container = {"id": identity, "name": definitions.get(canonical_ids.get(container_item.casefold(), container_item), {}).get("name", container_item or "Unrecognized Container"),
                     "x": xyz[0], "y": xyz[1], "z": xyz[2], "distance_m": distance,
                     "item_count": 0, "unknown_stacks": 0}
        seen_slots = {}
        # A saved inventory may repeat; the identity is actor + inventory + slot.
        for inv in counted_inventories:
            slots = inv.get("Slots", [])
            if not isinstance(slots, list):
                coverage["malformed_inventories"] += 1
                continue
            for index, slot in enumerate(slots):
                if not isinstance(slot, dict):
                    continue
                location = slot.get("Location", "unlocated:" + str(index))
                key = (inv["InventoryID"], _fingerprint(location))
                signature = _fingerprint(slot)
                candidates = seen_slots.setdefault(key, {})
                if signature in candidates:
                    coverage["duplicate_records"] += 1
                candidates[signature] = slot
        for key, candidates in seen_slots.items():
            disputed = len(candidates) > 1
            coverage["conflicting_slots"] += int(disputed)
            seen_items = set()
            for slot in candidates.values():
                item_id = _item_id(slot)
                if not item_id:
                    coverage["empty_slots"] += 1
                    continue
                item_id = canonical_ids.get(item_id.casefold(), item_id)
                if item_id in seen_items:
                    continue
                seen_items.add(item_id)
                quantity = None if disputed else _quantity(slot, catalog["dynamic_properties"]["ItemableStack"])
                if quantity == 0:
                    coverage["empty_slots"] += 1
                    continue
                if quantity is None:
                    container["unknown_stacks"] += 1
                    coverage["unknown_stacks"] += 1
                dynamic = slot.get("DynamicData", [])
                if isinstance(dynamic, list) and any(isinstance(e, dict) and e.get("Index") == catalog["dynamic_properties"]["InventoryContainer_LinkedInventoryId"] for e in dynamic):
                    coverage["linked_item_inventories"] += 1
                stacks.append((item_id, quantity, identity))
                container["item_count"] += 1
        result["containers"].append(container)

    grouped = {}
    for item_id, quantity, container_id in stacks:
        if item_id not in grouped:
            info = definitions.get(item_id)
            if info is None:
                coverage["unknown_items"] += 1
                info = {"name": item_id, "category": "unknown", "material": item_id, "material_label": item_id, "form": "other"}
            grouped[item_id] = {"id": item_id, **{k: info[k] for k in ("name", "category", "material", "material_label", "form")},
                                "quantity": 0, "known_quantity": 0, "unknown_stacks": 0, "containers": [], "stacks": 0}
            grouped[item_id]["source_category"] = info["category"]
            grouped[item_id]["category"] = CATEGORY_GROUPS.get(info["category"], info["category"])
        item = grouped[item_id]
        item["stacks"] += 1
        if container_id not in item["containers"]:
            item["containers"].append(container_id)
        if quantity is None:
            item["unknown_stacks"] += 1
        else:
            item["known_quantity"] += quantity
        item["quantity"] = None if item["unknown_stacks"] else item["known_quantity"]
    result["items"] = sorted(grouped.values(), key=lambda it: (it["category"], it["material"], it["form"], it["id"]))
    categories = defaultdict(list)
    for item in result["items"]:
        categories[item["category"]].append(item)
    for category in CATEGORY_LABELS:
        members = categories.get(category, [])
        if not members:
            continue
        materials = defaultdict(list)
        for item in members:
            materials[item["material"]].append(item)
        rows = []
        for material, items in materials.items():
            values, known_values, unknown_forms = Counter(), Counter(), set()
            for item in items:
                form = item["form"]
                known_values[form] += item["known_quantity"]
                values[form] += item["known_quantity"]
                if item["quantity"] is None:
                    unknown_forms.add(form)
            rows.append({"key": material, "label": items[0]["material_label"],
                         "values": {form: None if form in unknown_forms else count for form, count in values.items()},
                         "known_values": dict(known_values), "items": items})
        forms = {item["form"] for item in members}
        result["categories"].append({"id": category, "label": CATEGORY_LABELS[category],
                                      "rows": sorted(rows, key=lambda row: (0 if category == "resources" and any(item["source_category"] == "metals" for item in row["items"]) else 1, row["key"])),
                                      "series": [{"id": form, "label": "Minerals / Fuel" if category == "resources" and form == "other" else label} for form, label in FORM_LABELS.items() if form in forms]})
    coverage["counted_containers"] = len(result["containers"])
    coverage["item_types"] = len(result["items"])
    coverage["counted_stacks"] = len(stacks)
    coverage["known_quantity"] = sum(item["known_quantity"] for item in result["items"])
    if coverage["unknown_stacks"] or coverage["unknown_items"] or conflicts or coverage["missing_locations"] or coverage["malformed_inventories"] or coverage["unknown_inventory_ids"]:
        coverage["status"] = "partial"
    if not coverage["records_with_inventory"] or not unique:
        coverage.update(status="unavailable", reason="This snapshot has no container inventory records; home inventory is unavailable.")
    if conflicts or coverage["conflicting_slots"]:
        coverage["warnings"].append("Some duplicate records conflict. Conflicting containers are excluded; quantities for possible items in conflicting slots remain unknown.")
    if coverage["malformed_inventories"] or coverage["unknown_inventory_ids"]:
        coverage["warnings"].append("Some inventory formats or slot types are unrecognized and are excluded from totals.")
    if coverage["missing_locations"]:
        coverage["warnings"].append("Some containers have no verified position and cannot be confirmed inside the base radius.")
    coverage["warnings"] += [
        "Counts use the horizontal base radius; ownership, floor level and container accessibility are not determined.",
        "Excludes player and mount inventories, dropped items, global item pools, equipped items and equipment upgrade slots.",
        "Quantities are actual item counts. Ore and ingots are separate; no smelting conversion is applied.",
    ]
    if coverage["linked_item_inventories"]:
        coverage["warnings"].append("Attachments and nested bags in stored equipment are not expanded; global item pools are not counted again.")
    return result
