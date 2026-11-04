"""Versioned C0NT4CT gates and read-only, source-account talent evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path


@lru_cache(maxsize=1)
def load_operation_rules():
    return json.loads((Path(__file__).with_name("data") / "operation_rules.json").read_text(encoding="utf-8"))


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def source_profile_path(source_path):
    if source_path is None:
        return None
    source = Path(source_path)
    if source.parent.name.casefold() != "prospects":
        return None
    return source.parent.parent / "Profile.json"


def load_operation_account(snapshot):
    """Read only the selected local save owner's Profile, never a receiver's.

    The profile is independent evidence with its own hash and timestamp. UserID
    is checked against both the world and account directory, then discarded.
    """
    result = {"status": "unavailable", "world_id": _get(snapshot, "world_id"),
              "snapshot_sha256": _get(snapshot, "sha256"), "talents": {}, "flags": [],
              "profile_signature": None, "reason": "Unlock data for the selected world's host account is missing"}
    if _get(snapshot, "is_remote", False) or _get(snapshot, "is_shared_map", False):
        result["reason"] = "The public source has no host account unlock data; the recipient's local account is not substituted"
        return result
    source = _get(snapshot, "source_path")
    path = source_profile_path(source)
    if path is None:
        return result
    from .release_sync import is_remote_cache_path
    if is_remote_cache_path(source):
        return result
    info = _get(snapshot, "prospect_info", {}) or {}
    owner = str(info.get("ClaimedAccountID") or "")
    if not owner or path.parent.name != owner:
        result.update(status="identity_mismatch", reason="The world and source account directory do not match; account data was not used")
        return result
    try:
        from .saves import _read_stable
        raw, stat = _read_stable(path)
        result["profile_signature"] = (stat.st_mtime_ns, stat.st_size)
        profile = json.loads(raw)
        if not isinstance(profile, dict) or str(profile.get("UserID") or "") != owner:
            result.update(status="identity_mismatch", reason="The account identity does not match the selected world's host")
            return result
        entries, flags = profile.get("Talents"), profile.get("UnlockedFlags")
        if (not isinstance(entries, list) or not isinstance(flags, list)
                or any(not isinstance(f, str) and not (type(f) is int and f >= 0) for f in flags)):
            raise ValueError("Account unlock data format is not verified")
        ranks = {}
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("RowName"), str):
                raise ValueError("Account mission talent data is incomplete")
            name, rank = item["RowName"], item.get("Rank")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
                raise ValueError("Account mission talent rank is invalid")
            if name in ranks and ranks[name] != rank:
                raise ValueError("Account mission talent records are duplicated and conflicting")
            ranks[name] = rank
        result.update(status="verified", talents=ranks, flags=flags,
                      profile_sha256=hashlib.sha256(raw).hexdigest(),
                      profile_saved_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                      checked_at=datetime.now(timezone.utc).isoformat(),
                      scope="Selected world's host account; timestamp is independent of the world save", reason="Host account and world identity verified")
    except FileNotFoundError:
        result["reason"] = "Profile.json for the selected world's host was not found"
    except (OSError, ValueError) as exc:
        result.update(status="unavailable", reason=f"Host account unlock data could not be confirmed: {exc}")
    return result


def _account(world):
    evidence = _get(world, "operation_account", {}) or {}
    if (not _get(world, "world_id") or not _get(world, "sha256")
            or evidence.get("status") != "verified"
            or evidence.get("world_id") != _get(world, "world_id")
            or evidence.get("snapshot_sha256") != _get(world, "sha256")):
        return None
    return evidence


def operation_talent_gate(world, mission_id):
    """Account-scoped native talent state, including default reroute closure."""
    rules = load_operation_rules()
    rule = rules["missions"].get(mission_id)
    if rule is None:
        return "unknown", "No verified acceptance rules exist for this Operation"
    ids = rule["talent_ids"]
    if not ids:
        return "unlisted", "This version's mission board has no acceptance node for this Operation; empty prerequisites do not establish availability"
    account = _account(world)
    if account is None:
        evidence = _get(world, "operation_account", {}) or {}
        return "unknown", evidence.get("reason", "Account mission unlock data matching this world's host has not been obtained")
    ranks = dict(account["talents"])
    flags = set(account["flags"])
    nodes = rules["talents"]
    if any(type(flag) is int for flag in flags):
        # Current saves persist account flags as native numeric IDs. Until a
        # versioned row-name mapping is verified, do not apply those IDs to
        # unrelated named talent restrictions. Prometheus has no such gates.
        pending, seen = list(ids), set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            node = nodes[name]
            if node["required_flags"] or node["forbidden_flags"]:
                return "unknown", "This Operation requires account-flag lookup; numeric flags have not been mapped to names"
            pending.extend(node["required_talents"])

    def restricted(node):
        return not set(node["required_flags"]).issubset(flags) or bool(set(node["forbidden_flags"]) & flags)

    def positive(name):
        return ranks.get(name, 0) > 0 and name in nodes and not restricted(nodes[name])

    # Default reroutes are calculated by the talent model, not unconditional
    # saved unlocks. Rebuild their closure from the persistent normal ranks.
    for name, node in nodes.items():
        if node["default_unlocked"]:
            ranks[name] = 0
    for _ in range(len(nodes) + 1):
        changed = False
        for name, node in nodes.items():
            if not node["default_unlocked"] or ranks.get(name, 0) > 0 or restricted(node):
                continue
            requirements = node["required_talents"]
            if not requirements or any(positive(parent) for parent in requirements):
                ranks[name] = 1
                changed = True
        if not changed:
            break
    unknown = False
    for ident in ids:
        node = nodes[ident]
        if node["required_level"] or node["required_rank"] not in (None, "None", ""):
            unknown = True
            continue
        if restricted(node):
            continue
        if ranks.get(ident, 0) > 0 or not node["required_talents"] or any(
                positive(parent) for parent in node["required_talents"]):
            return "available", "Host account mission-tree unlocks and prerequisites verified"
    if unknown:
        return "unknown", "This Operation has additional unverified account-rank / character-level conditions"
    return "locked", "The host account has not met this Operation's mission-tree prerequisites"


def operation_retry_gate(world, mission_id, history):
    """Reconstruct native locks from history and prospect elapsed seconds."""
    rule = load_operation_rules()["missions"].get(mission_id)
    if rule is None:
        return "unknown", "Native retry settings for this Operation are missing"
    if not rule["use_retry_timeout"]:
        return "available", "This Operation does not use a world-history retry lock"
    if not isinstance(history, list):
        return "unknown", "World mission history is unreadable; the absence of a retry lock cannot be assumed"
    records = _get(world, "raw_records", []) or []
    elapsed = next(((r.get("GameModeRecord") or {}).get("LevelTimeElapsedSec")
                    for r in records if isinstance(r.get("GameModeRecord"), dict)), None)
    seconds = load_operation_rules()["native_contract"]["retry_seconds"]
    for entry in history:
        if not isinstance(entry, dict):
            return "unknown", "World mission history contains an unrecognized record"
        if entry.get("Mission") != mission_id:
            continue
        outcome = entry.get("Status")
        if type(outcome) is not int or outcome not in (0, 1, 2, 3):
            return "unknown", "The Operation result format in world history is unknown"
        if outcome in (2, 3):
            continue
        end = entry.get("MissionEndTime")
        if (isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end)
                or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed)):
            return "unknown", "The same world's Operation end time / elapsed seconds is missing; the retry lock cannot be checked"
        deadline = (elapsed if end < 0 else end) + seconds
        if deadline > elapsed:
            return "locked", "This Operation is still restricted by the native world-history retry lock"
    return "available", "World mission history verified; abandoned / failed records do not impose a retry lock"


def operation_start_block(world):
    state = _get(world, "mission_state", {}) or {}
    flags = state.get("session_flags")
    if flags is None:
        flags = next(((r.get("GameModeRecord") or {}).get("SessionFlagRecords")
                      for r in (_get(world, "raw_records", []) or []) if isinstance(r.get("GameModeRecord"), dict)), [])
    manager = state.get("quest_manager", {}) or {}
    if "Mission_In_Progress" in (flags or []):
        return "Complete or end the current Operation before accepting another mission"
    delay = manager.get("DynamicQuestDelay", 0)
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay > 0:
        return "The mission device is still waiting for the next Operation's acceptance interval"
    return ""
