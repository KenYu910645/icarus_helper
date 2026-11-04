"""Rebuild the compact operation rules from three verified, local game tables.

Only static identifiers and eligibility fields are exported. This tool never
reads saves, Profile.json, account settings, a running process, or game archives.
Native behavior is pinned to the independently audited executable, not inferred
from the presence of rows in these tables.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


SOURCE_BUILD = "26313227"
EXE_SHA256 = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
SOURCE_HASHES = {
    "D_Talents.json": "09a02098a0d054aad8c5a9c7182b2c024a3e4c30a4994ad4de00be50bbda37a1",
    "D_ProspectList.json": "b44bcb187535dae60edfde1a461af557b352ace98423807d893b0eb8a4e476cd",
    "D_FactionMissions.json": "0c18b33e56e3e1cf80db31b7f48bc84ffbcff4037e0c1d36cd069543d28551e1",
}
# Audited native/BP contracts for this build. New source tables require a new
# native audit; changing a build label alone cannot establish compatibility.
# ProspectTalentController storage: vtable 0x4eee050+0x408 -> 0x8e0c70 (Account=2).
# Talent recalculation: 0x1938670. Retry history: 0x14f88a0 (excludes outcomes 2/3).
# Feature cutoff: 0x3f92a20. Shared start UI: UMG_MissionBoardProspectSelected.
NATIVE_CONTRACT = {
    "talent_storage": "Account",
    "prerequisite_combination": "any_positive_rank",
    "release_feature_is_entitlement": False,
    "retry_seconds": 157680000,
    "retry_excluded_outcomes": [2, 3],
    "history_outcomes": {"0": "InProgress", "1": "Completed", "2": "Abandoned", "3": "Failed"},
    "one_operation_session_flag": "Mission_In_Progress",
}


def _load_tables(source: Path) -> tuple[dict, dict]:
    tables, hashes = {}, {}
    for name, expected_hash in SOURCE_HASHES.items():
        raw = (source / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_hash:
            raise ValueError(f"{name} differs from the verified source hash for build {SOURCE_BUILD}; recheck the version and native rules first")
        table = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(table.get("Defaults"), dict) or not isinstance(table.get("Rows"), list):
            raise ValueError(f"{name} has no valid Defaults / Rows")
        tables[name] = table
        hashes[name] = digest
    return tables, hashes


def _rows(table: dict) -> dict[str, dict]:
    result = {}
    for raw in table["Rows"]:
        name = raw.get("Name")
        if not isinstance(name, str) or not name or name in result:
            raise ValueError("Source row names are missing or duplicated")
        result[name] = {**table["Defaults"], **raw}
    return result


def _row_names(values: list) -> list[str]:
    result = []
    for value in values:
        name = value.get("RowName") if isinstance(value, dict) else value
        if not isinstance(name, str):
            raise ValueError("A source condition contains an unrecognized row reference")
        result.append(name)
    return result


def build_rules(source: Path) -> dict:
    """Build schema 1 without importing any private runtime data or old output."""
    tables, hashes = _load_tables(Path(source))
    talents = _rows(tables["D_Talents.json"])
    prospects = _rows(tables["D_ProspectList.json"])
    factions = _rows(tables["D_FactionMissions.json"])
    missions, used = {}, set()
    for name, row in prospects.items():
        if row["OnProspectAvailability"] == "None":
            continue
        talent_ids = sorted(
            ident for ident, talent in talents.items()
            if talent["ExtraData"].get("DataTableName") == "D_ProspectList"
            and talent["ExtraData"].get("RowName") == name
        )
        used.update(talent_ids)
        faction = factions.get(row["FactionMission"]["RowName"], {})
        missions[name] = {
            "talent_ids": talent_ids,
            "use_retry_timeout": faction.get("bUseOpenWorldRetryTimeout", True),
            "required_flags": _row_names(row["RequiredFlags"]),
            "required_character_flags": _row_names(row["RequiredCharacterFlags"]),
            "forbidden_character_flags": _row_names(row["ForbiddenCharacterFlags"]),
            "required_level": row["RequiredLevel"],
            "terminal_availability": row["OnProspectAvailability"],
        }

    # Keep every referenced Reroute/parent, even when it has no prospect row.
    # An empty talent_ids remains empty: missing UI entry is not a free unlock.
    pending = list(used)
    while pending:
        ident = pending.pop()
        if ident not in talents:
            raise ValueError("A mission prerequisite reference has no matching Talent row")
        for parent in _row_names(talents[ident]["RequiredTalents"]):
            if parent not in used:
                used.add(parent)
                pending.append(parent)

    nodes = {}
    for ident in sorted(used):
        row = talents[ident]
        nodes[ident] = {
            "required_talents": _row_names(row["RequiredTalents"]),
            "required_flags": _row_names(row["RequiredFlags"]),
            "forbidden_flags": _row_names(row["ForbiddenFlags"]),
            "required_level": row["RequiredLevel"],
            "default_unlocked": row["bDefaultUnlocked"],
            "tree": row["TalentTree"]["RowName"],
            "required_rank": row["RequiredRank"]["RowName"],
        }
    return {
        "schema_version": 1,
        "source_build": SOURCE_BUILD,
        "exe_sha256": EXE_SHA256,
        "source_hashes": hashes,
        "native_contract": deepcopy(NATIVE_CONTRACT),
        "missions": missions,
        "talents": nodes,
    }


def write_rules(source: Path, output: Path) -> dict:
    source, output = Path(source), Path(output)
    if output.resolve() in {(source / name).resolve() for name in SOURCE_HASHES}:
        raise ValueError("Output must not overwrite the source tables")
    rules = build_rules(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\r\n",
                                         dir=output.parent, prefix=output.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(rules, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return rules


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Rebuild compact Operation acceptance rules from three verified read-only game tables; does not read saves or account data")
    parser.add_argument("--source", type=Path, required=True,
                        help="Folder containing D_Talents.json, D_ProspectList.json and D_FactionMissions.json")
    parser.add_argument("--output", type=Path, required=True, help="Explicit destination for the compact rules JSON")
    args = parser.parse_args()
    try:
        rules = write_rules(args.source, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Rule rebuild failed: {exc}\n")
    print(json.dumps({"path": str(args.output), "source_build": SOURCE_BUILD,
                      "missions": len(rules["missions"]), "talents": len(rules["talents"]),
                      "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
