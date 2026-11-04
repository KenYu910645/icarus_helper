"""Saved output stock for individual deep-mining drills, never deposit reserves.

The current catalog verifies General=1 and ItemableStack=7. Fuel, equipment and
upgrade inventories are separate. All summaries come from the same validated
world snapshot as the map; no game reads or save writes happen in this module.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from typing import Any

from .catalog import installed_game_build
from .inventory import _fingerprint, _item_id, _quantity, load_item_catalog

OUTPUT_INVENTORY_ID = 1
DRILL_ITEMS = frozenset({"deep_mining_drill_electric", "deep_mining_drill_biofuel", "deep_mining_drill_oil"})
INVENTORY_FIELDS = frozenset({"SavedInventories", "InventoryID", "Slots", "Location", "ItemStaticData",
                              "DynamicData", "ItemDynamicData", "Index", "Type", "Value", "StackCount"})


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _empty_summary() -> dict:
    return {"status": "unavailable", "reason": "No verified drill output inventory is available.", "items": [],
            "quantity": None, "known_quantity": 0, "unknown_stacks": 0, "warnings": [],
            "inventory_id": OUTPUT_INVENTORY_ID, "inventory_role": "General",
            "source": "Drill output slots in the selected world save", "saved_at": "", "source_sha256": "", "world_id": "",
            "duplicate_slots": 0, "conflicting_slots": 0, "excluded_inventory_ids": []}


def summarize_drill_inventory(record: dict) -> dict:
    """Summarize one saved drill's General inventory without mutating it.

    Explicit empty output slots establish zero. Missing inventories, malformed
    slots, unsupported versions and conflicts do not. ``known_quantity`` is
    only the sum of undisputed, explicitly counted slots; ``quantity`` is a
    complete total only for ``ok``/``empty`` summaries.
    """
    result = _empty_summary()
    if not isinstance(record, dict):
        return result
    name = record.get("StaticItemDataRowName")
    if not isinstance(name, str) or name.casefold() not in DRILL_ITEMS:
        result["reason"] = "Output slots have not been verified for this device type."
        return result
    try:
        catalog = load_item_catalog()
        definitions = catalog["items"]
        stack_index = catalog["dynamic_properties"]["ItemableStack"]
        verified = catalog["inventory_ids"].get("1") == "General" and type(stack_index) is int and stack_index == 7
        actual_build = installed_game_build()
        result["game_build"] = catalog["game_build"]
    except (OSError, ValueError, KeyError, TypeError):
        result["reason"] = "The item quantity format source is unreadable."
        return result
    if not verified or (actual_build is not None and actual_build != catalog["game_build"]):
        result.update(status="unsupported_version", reason="The game version or drill inventory quantity format has not been verified.")
        return result
    if (record.get("ActorStateRecorderVersion") not in (None, 3)
            or record.get("DrillRecorderVersion") is not None
            or INVENTORY_FIELDS.intersection(record.get("_unsupported_fields") or [])):
        result["reason"] = "The saved drill inventory contains unsupported fields or format versions."
        return result
    inventories = record.get("SavedInventories")
    if not isinstance(inventories, list):
        result["reason"] = "The save has no readable drill inventory."
        return result
    output = {}
    incomplete = False
    for inv in inventories:
        if not isinstance(inv, dict) or type(inv.get("InventoryID")) is not int:
            incomplete = True
            continue
        inv_id = inv["InventoryID"]
        if str(inv_id) not in catalog["inventory_ids"]:
            incomplete = True
        if inv_id == OUTPUT_INVENTORY_ID:
            output[_fingerprint(inv)] = inv
        elif inv_id not in result["excluded_inventory_ids"]:
            result["excluded_inventory_ids"].append(inv_id)
    if not output:
        result["reason"] = "The save is missing verified output slots; missing records cannot be counted as zero."
        return result
    # Repeated identical inventories are harmless; differing copies cannot be
    # treated as multiple separate inventories or combined into a known total.
    inventory_conflict = len(output) > 1
    incomplete |= inventory_conflict
    if inventory_conflict:
        result["warnings"].append("Conflicting copies of the same output inventory exist; possible items are retained with unknown quantities.")
    canonical = {item_id.casefold(): item_id for item_id in definitions}
    slots_by_location = defaultdict(dict)
    for inv in output.values():
        slots = inv.get("Slots")
        if not isinstance(slots, list):
            incomplete = True
            continue
        for slot in slots:
            if not isinstance(slot, dict):
                incomplete = True
                result["unknown_stacks"] += 1
                continue
            signature = _fingerprint(slot)
            location = slot.get("Location")
            valid_location = type(location) is int and 0 <= location <= 2**31 - 1
            if not valid_location:
                incomplete = True
            key = location if valid_location else "unlocated:" + signature
            if signature in slots_by_location[key]:
                result["duplicate_slots"] += 1
            slots_by_location[key][signature] = slot
    grouped = {}
    for location, candidates in slots_by_location.items():
        disputed = len(candidates) > 1 or inventory_conflict or not isinstance(location, int)
        result["conflicting_slots"] += int(len(candidates) > 1)
        seen_items = set()
        for slot in candidates.values():
            item_id = _item_id(slot)
            if not item_id:
                # Empty is explicit in UE saves. A missing/malformed field is
                # not evidence that the slot is empty.
                static = slot.get("ItemStaticData")
                if isinstance(static, dict):
                    static = static.get("RowName")
                if not isinstance(static, str) or static.casefold() not in {"", "none", "null"}:
                    incomplete = True
                    result["unknown_stacks"] += 1
                continue
            item_id = canonical.get(item_id.casefold(), item_id)
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            quantity = None if disputed else _quantity(slot, stack_index)
            if quantity == 0:
                continue
            info = definitions.get(item_id)
            if info is None:
                incomplete = True
            item = grouped.setdefault(item_id, {"id": item_id, "name": (info or {}).get("name", item_id),
                                                "quantity": 0, "known_quantity": 0, "unknown_stacks": 0, "slots": []})
            if isinstance(location, int) and location not in item["slots"]:
                item["slots"].append(location)
            if quantity is None:
                item["unknown_stacks"] += 1
                result["unknown_stacks"] += 1
                incomplete = True
            else:
                item["known_quantity"] += quantity
            item["quantity"] = None if item["unknown_stacks"] else item["known_quantity"]
    result["items"] = sorted(grouped.values(), key=lambda item: item["id"])
    for item in result["items"]:
        item["slots"].sort()
    result["known_quantity"] = sum(item["known_quantity"] for item in result["items"])
    if incomplete:
        result.update(status="partial", reason="Some output inventory is missing, unrecognized or conflicting; no complete total is available.")
    else:
        result.update(status="ok" if result["items"] else "empty", quantity=result["known_quantity"], reason="")
    if result["conflicting_slots"]:
        result["warnings"].append("Records for the same output slot conflict; they are neither counted twice nor arbitrarily selected.")
    return result


def attach_drill_inventories(snapshot: Any, *, save_version: Any = None) -> None:
    """Attach display summaries without changing the parsed raw-record list."""
    drills = [point for point in _get(snapshot, "deployables", []) if _get(point, "kind") == "drill"]
    by_id = defaultdict(list)
    by_actor = defaultdict(list)
    actor_records = defaultdict(list)
    def actor_key(raw, fallback):
        uid = raw.get("IcarusActorGUID")
        return ("uid", uid) if type(uid) is int and uid > 0 else ("id", fallback)

    for raw in _get(snapshot, "raw_records", []):
        if not isinstance(raw, dict):
            continue
        if "DrillRecorder" in str(raw.get("_component", "")) or str(raw.get("StaticItemDataRowName", "")).casefold() in DRILL_ITEMS:
            actor_records[actor_key(raw, raw.get("ActorPathName"))].append(raw)
    for drill in drills:
        by_id[_get(drill, "id")].append(drill)
        raw = _get(drill, "raw", {})
        by_actor[actor_key(raw, _get(drill, "id"))].append(drill)
    for identity, members in by_actor.items():
        records = actor_records.get(identity) or [_get(point, "raw", {}) for point in members]
        # All source fields belong to one actor record. Differing attachment,
        # class, version, operating state or unreadable duplicate is a conflict
        # even when the output counts happen to agree.
        signatures = {_fingerprint({key: value for key, value in raw.items() if not key.startswith("_drill")}) for raw in records}
        for drill in members:
            raw = _get(drill, "raw", {})
            summary = summarize_drill_inventory(raw)
            saved = _get(snapshot, "saved_at", "")
            summary.update(saved_at=saved.isoformat() if hasattr(saved, "isoformat") else str(saved),
                           source_sha256=str(_get(snapshot, "sha256", "")), world_id=str(_get(snapshot, "world_id", "")),
                           drill_id=str(_get(drill, "id", "")), save_format_version=save_version)
            if type(save_version) is not int or save_version != 2:
                summary.update(status="unsupported_version", reason="Drill inventory has not been verified for this world save format version.",
                               quantity=None, known_quantity=0, items=[], unknown_stacks=0)
            if len(signatures) > 1:
                summary.update(status="conflict", reason="This drill has conflicting records; inventory cannot be confirmed.",
                               quantity=None, known_quantity=0, items=[], unknown_stacks=0)
            drill.raw = dict(raw, _drill_inventory=summary)
    for deposit in _get(snapshot, "resources", []):
        raw = _get(deposit, "raw", {})
        link = raw.get("_drill")
        if not isinstance(link, dict):
            continue
        new_link = {key: value for key, value in link.items() if key != "inventory"}
        candidates = by_id.get(link.get("id"), [])
        if str(_get(deposit, "drill_status", "")).startswith(("Installed", "已放置")) and len(candidates) == 1:
            new_link["inventory"] = copy.deepcopy(candidates[0].raw["_drill_inventory"])
        deposit.raw = dict(raw, _drill=new_link)


def inventory_for_point(point: Any) -> dict | None:
    """Select only the summary belonging to this drill or valid deposit link."""
    raw = _get(point, "raw", {})
    if not isinstance(raw, dict):
        return None
    if _get(point, "kind") == "drill":
        summary = raw.get("_drill_inventory")
    elif _get(point, "kind") == "deep_deposit" and str(_get(point, "drill_status", "")).startswith(("Installed", "已放置")):
        link = raw.get("_drill")
        if not isinstance(link, dict) or not link.get("id"):
            return None
        summary = link.get("inventory")
        if isinstance(summary, dict) and summary.get("drill_id") != str(link["id"]):
            return None
    else:
        return None
    return summary if isinstance(summary, dict) else None
