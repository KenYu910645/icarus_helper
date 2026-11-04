"""Position presentation shared by live sampling and snapshot-backed maps.

Private source identities are used only to construct opaque stable player keys.
The original world snapshot and its map resources are never mutated here.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime


def get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def local_time_text(value):
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %z") if isinstance(value, datetime) else "Time unknown"
    except (ValueError, TypeError):
        return "Time unknown"


def position(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in value):
        return None
    return list(value)


def player_identity(value):
    if not isinstance(value, dict):
        return None
    account, slot = value.get("PlayerID"), value.get("ChrSlot")
    if not isinstance(account, str) or not account or account != account.strip() or account.casefold() in {"0", "-1", "none", "null"}:
        return None
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        return None
    return account, slot


def player_key(identity):
    raw = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "player:" + hashlib.sha256(b"icarus-helper-player-v1\0" + raw).hexdigest()


def saved_players(snapshot):
    if get(snapshot, "is_shared_map", False):
        return []
    from .live_world_fields import snapshot_player_index
    identities = snapshot_player_index(snapshot)
    result, conflicts = {}, set()
    for source in get(snapshot, "players", []) or []:
        raw = get(source, "raw", {})
        identity = player_identity(get(raw, "PlayerCharacterID"))
        if identity is None or identity not in identities:
            continue
        ident = player_key(identity)
        row = {"id": ident, "saved_resource_id": get(source, "id"), "name": str(identities[identity].get("name") or "Player"),
               "position": position([get(source, axis) for axis in ("x", "y", "z")]),
               "position_source": "save", "sample_time": str(get(snapshot, "saved_at", ""))}
        if ident in result and result[ident] != row:
            conflicts.add(ident)
        result[ident] = row
    for identity, metadata in identities.items():
        ident = metadata["id"]
        if ident not in result:
            result[ident] = {"id": ident, "saved_resource_id": ident, "name": metadata.get("name") or "Player",
                             "position": None, "position_source": "save", "sample_time": str(get(snapshot, "saved_at", ""))}
    return [row for key, row in result.items() if key not in conflicts]


def apply_player_positions(players, live):
    """Accept only readers that verified the complete selected-world identity."""
    if live.get("world_verified") is not True or live.get("status") not in ("ok", "partial"):
        return 0
    sampled = live.get("players", {})
    count = 0
    for player in players:
        values = sampled.get(player["id"], {}) if isinstance(sampled, dict) else {}
        current = position(values.get("position"))
        if current is None:
            continue
        player.update(saved_position=player["position"], position=current, position_source="live",
                      sample_time=values.get("sample_time") or live.get("sample_time"))
        if isinstance(values.get("local"), bool):
            player["local"] = values["local"]
        count += 1
    return count


def player_map_points(points, players):
    """Overlay verified positions without changing saved resources or identities."""
    by_saved_id = {p.get("saved_resource_id"): p for p in players
                   if p.get("saved_resource_id") and p.get("position_source") == "live" and position(p.get("position")) is not None}
    safe_names = {p.get("saved_resource_id"): p.get("name") or "Player" for p in players if p.get("saved_resource_id")}
    output, rendered = [], set()
    def marker(ident, name, player):
        xyz = player["position"]
        return {"id": ident, "kind": "player", "name": name,
                "x": xyz[0], "y": xyz[1], "z": xyz[2], "resource_type": "Current player position",
                "source": "Read-only game state (30-second samples)", "confidence": "Confirmed",
                "position_source": "live", "position_sample_time": player.get("sample_time"),
                "detail": f"Game position sampled at: {local_time_text(player.get('sample_time'))}.\nHorizontal straight-line distance is not a walking route."}
    for point in points:
        player = by_saved_id.get(get(point, "id")) if get(point, "kind") == "player" else None
        if player is None:
            safe = safe_names.get(get(point, "id")) if get(point, "kind") == "player" else None
            if safe is not None and safe != get(point, "name"):
                cleaned = {key: get(point, key) for key in ("id", "kind", "resource_type", "x", "y", "z", "detail", "source", "confidence")}
                cleaned["name"] = safe
                output.append(cleaned)
            else:
                output.append(point)
            continue
        ident = get(point, "id")
        output.append(marker(ident, player.get("name") or "Player", player))
        rendered.add(ident)
    known_ids = {get(point, "id") for point in points}
    for ident, player in by_saved_id.items():
        if ident not in rendered and ident not in known_ids:
            output.append(marker(ident, player.get("name") or "Player", player))
    return output
