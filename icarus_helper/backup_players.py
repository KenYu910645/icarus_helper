"""Privacy-preserving player values taken only from a particular world backup.

These are saved recorder values, never current game values.  An offline member
can still have a recorder; there is no per-player update timestamp or verified
maximum-health denominator in this data contract.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math


SCHEMA_VERSION = 1
PLAYER_VERSION = 1
ACTOR_VERSION = 3
NUMERIC_FIELDS = {
    "health": ("Health", "Health"),
    "food": ("FoodLevel", "Food"),
    "water": ("WaterLevel", "Water"),
    "oxygen": ("OxygenLevel", "Oxygen"),
    "radiation": ("RadiationLevel", "Radiation"),
    "respawn_count": ("RespawnCount", "Respawns"),
}


def _identity(account, slot):
    if (not isinstance(account, str) or not account or account != account.strip()
            or account.casefold() in {"0", "-1", "none", "null"}
            or not isinstance(slot, int) or isinstance(slot, bool) or slot < 0):
        return None
    return account, slot


def _record_identity(record):
    identity = record.get("PlayerCharacterID")
    return _identity(identity.get("PlayerID"), identity.get("ChrSlot")) if isinstance(identity, dict) else None


def _key(identity):
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "backup-player:" + hashlib.sha256(b"icarus-helper-backup-player-v1\0" + encoded).hexdigest()


def _name(member, identity):
    value = member.get("CharacterName") if isinstance(member, dict) else None
    if (isinstance(value, str) and value.strip() and identity[0] not in value
            and all(character.isprintable() for character in value)
            and not (value.isdecimal() and len(value) >= 8)):
        return value.strip()
    return f"Name unconfirmed (Character slot {identity[1]})"


def _number(value, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 2**31 - 1 or not math.isfinite(value)
            or integer and not isinstance(value, int)):
        return None
    return value


def _position(record):
    value = record.get("Location")
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    try:
        if any(isinstance(axis, bool) or not isinstance(axis, (int, float)) or not math.isfinite(axis) for axis in value):
            return None
    except OverflowError:
        return None
    return list(value)


def summarize_backup_players(info, player_records, saved_at, map_key=None):
    """Summarize an already integrity-checked backup without external sources.

    Match both account and character slot.  Duplicate identities, unsupported
    versions and missing member matches stay visible but have unknown values.
    No raw account identifier, account display name or actor GUID is returned.
    """
    members, records = defaultdict(list), defaultdict(list)
    warnings = ["Player values come from this backup, not the current game state.",
                "Verified maximum health and individual update times are not saved; health percentages are unavailable."]
    raw_members = info.get("AssociatedMembers", []) if isinstance(info, dict) else []
    if not isinstance(raw_members, list):
        raw_members = []
        warnings.append("The member list format is unrecognized; character names cannot be confirmed.")
    for member in raw_members:
        identity = _identity(member.get("UserID"), member.get("ChrSlot")) if isinstance(member, dict) else None
        if identity is None:
            warnings.append("Some members have no valid character identity; no match is guessed.")
        else:
            members[identity].append(member)
    if not isinstance(player_records, (list, tuple)):
        player_records = []
        warnings.append("The saved player record format is unrecognized.")
    for record in player_records:
        identity = _record_identity(record) if isinstance(record, dict) else None
        if identity is None:
            warnings.append("Some player records cannot be decoded or have no valid character identity.")
        else:
            records[identity].append(record)
    players = []
    for identity in sorted(set(members) | set(records), key=lambda key: (key[1], _key(key))):
        matches = members.get(identity, [])
        sources = records.get(identity, [])
        member = matches[0] if len(matches) == 1 else None
        row = {"id": _key(identity), "name": _name(member, identity), "slot": identity[1],
               **{key: None for key in NUMERIC_FIELDS}, "alive": None,
               "is_currently_playing": None, "position": None, "issues": []}
        issues = row["issues"]
        if len(matches) != 1:
            issues.append("The member identity/name match is missing or duplicated; player values remain unknown.")
        else:
            playing = member.get("IsCurrentlyPlaying")
            row["is_currently_playing"] = playing if isinstance(playing, bool) else None
            if row["name"].startswith("Name unconfirmed"):
                issues.append("No confirmed character name is available; platform names and account identifiers are not substituted.")
        if not sources:
            issues.append("This backup has no state record for this character; missing values are not zero.")
        elif len(sources) != 1:
            issues.append("Duplicate records exist for this character; a unique state cannot be confirmed.")
        elif member is not None:
            record = sources[0]
            versions = record.get("PlayerStateRecorderVersion"), record.get("ActorStateRecorderVersion")
            if (any(isinstance(version, bool) or not isinstance(version, int) for version in versions)
                    or versions != (PLAYER_VERSION, ACTOR_VERSION)):
                issues.append("The player save format version has not been verified; state values remain unknown.")
            else:
                for field, (source, label) in NUMERIC_FIELDS.items():
                    row[field] = _number(record.get(source), integer=field == "respawn_count")
                    if row[field] is None:
                        issues.append(f"{label} is missing or has an unrecognized format.")
                flag = record.get("bIsAlive")
                row["alive"] = flag if isinstance(flag, bool) else None
                if row["alive"] is None:
                    issues.append("The survival flag is missing or unrecognized; it cannot be inferred from health.")
                row["position"] = _position(record)
                if row["position"] is None:
                    issues.append("The saved position is missing or has an unrecognized format.")
                if row["health"] == 0 and row["alive"] is True or row["health"] is not None and row["health"] > 0 and row["alive"] is False:
                    issues.append("Health and the survival flag disagree; original values are preserved without inferring death or unconsciousness.")
        if row["is_currently_playing"] is False:
            issues.append("The member is flagged as not playing; values and position are last saved values with no individual update time.")
        elif row["is_currently_playing"] is None:
            issues.append("No playing flag is available; this cannot establish whether the character was online at this time.")
        players.append(row)
    has_values = any(row["health"] is not None or row["alive"] is not None or row["position"] is not None for row in players)
    status = "unknown" if not has_values else "partial" if any(row["issues"] for row in players) else "available"
    if not players:
        warnings.append("No confirmed player data is available in this backup.")
    return {"schema_version": SCHEMA_VERSION, "status": status, "players": players,
            "warnings": list(dict.fromkeys(warnings)), "source": "backup", "sample_time": saved_at}
