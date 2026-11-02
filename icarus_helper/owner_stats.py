"""Read-only persistent owner bonuses, with exact identity and source provenance.

This module never claims that persistent profile files plus a world save contain
all live owner stats. It deliberately exposes persistent_complete and
live_complete separately and never treats an absent owner's profile as zero.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any

from .saves import SaveChangedError, _json_bytes, _read_stable


BUILD = "26313227"
PLAYER_COMPONENT = "/Script/Icarus.PlayerStateRecorderComponent"
CHANNELS = ("health", "stamina")


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _integer(value, minimum=None):
    return isinstance(value, int) and not isinstance(value, bool) and -(2**31) <= value < 2**31 and (minimum is None or value >= minimum)


def owner_identity(value) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    account, slot = value.get("PlayerID"), value.get("ChrSlot")
    if not isinstance(account, str) or account != account.strip() or not account or account.casefold() in {"0", "-1", "none", "null"}:
        return None
    if any(character in account for character in '/\\:"<>|?*') or account in (".", "..") or any(ord(c) < 32 for c in account):
        return None
    return (account, slot) if _integer(slot, 0) else None


def owner_key(identity: tuple[str, int]) -> str:
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf8")
    return "owner:" + hashlib.sha256(b"icarus-helper-owner-v1\0" + encoded).hexdigest()


@lru_cache(maxsize=1)
def load_owner_stat_catalog() -> dict:
    result = json.loads((Path(__file__).with_name("data") / "owner_stat_catalog.json").read_text(encoding="utf8"))
    _validate_catalog(result)
    return result


def _validate_catalog(catalog):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1 or catalog.get("game_build") != BUILD:
        raise ValueError("飼養者加成目錄版本未驗證")
    rules = catalog.get("rules", {})
    if any(not isinstance(rules.get(key), dict) or rules[key].get("verified") is not True
           for key in ("talent_rank", "owner_derived", "bestiary")):
        raise ValueError("飼養者加成計算規則未驗證")


def _read_profile(path: Path):
    """One stable read, with independent metadata; no paths/IDs enter summaries."""
    source = {"kind": path.name, "same_snapshot": False, "status": "unavailable"}
    try:
        raw, stat = _read_stable(path)
        data = _json_bytes(raw)
        source.update(status="ok", sha256=hashlib.sha256(raw).hexdigest(), size=stat.st_size,
                      mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), mtime_ns=stat.st_mtime_ns)
        return data, source
    except FileNotFoundError:
        source.update(status="missing", reason="此飼養者的獨立資料檔未提供。")
    except SaveChangedError:
        source.update(status="unstable", reason="角色資料讀取期間變更，請等待下一次更新。")
    except (OSError, ValueError, TypeError):
        source.update(status="invalid", reason="角色資料無法穩定讀取或格式未支援。")
    return None, source


def _profile_candidates(snapshot, profile_roots):
    warnings = []
    if profile_roots is None:
        source = Path(_get(snapshot, "source_path", ""))
        roots = [source.parent.parent.parent] if source.parent.name.casefold() == "prospects" else []
    else:
        roots = []
        for item in profile_roots:
            if isinstance(item, dict):
                if item.get("world_id") != _get(snapshot, "world_id"):
                    warnings.append("已略過綁定其他世界的飼養者資料夾。")
                    continue
                item = item.get("path")
            if item:
                roots.append(Path(item))
    candidates = []
    for root in roots:
        if (root / "Characters.json").is_file() or (root / "Profile.json").is_file():
            candidates.append(root)
        elif root.is_dir():
            try:
                candidates.extend(child for child in root.iterdir() if child.is_dir()
                                  and ((child / "Characters.json").is_file() or (child / "Profile.json").is_file()))
            except OSError:
                warnings.append("部分飼養者來源資料夾無法讀取。")
    return list(dict.fromkeys(path.resolve() for path in candidates)), warnings


class _OwnerResult:
    def __init__(self, key, name, catalog):
        self.catalog = catalog
        self.reasons = {key: [] for key in CHANNELS}
        self.total = {key: 0 for key in CHANNELS}
        self.known = False
        self.indexes = {}
        for group in ("talents", "items", "bestiary", "equippables", "armour", "armour_sets", "armour_set_bonuses", "modifiers"):
            folded = {}
            for ident in catalog[group]:
                key_fold = ident.casefold()
                folded[key_fold] = ident if key_fold not in folded else None
            self.indexes[group] = folded
        self.data = {"owner_key": key, "owner_name": name, "identity_match": "exact_account_slot",
                     "sources": [], "components": [], "candidate_components": [], "coverage": {}}

    def resolve(self, group, ident):
        if not isinstance(ident, str):
            return None, None
        canonical = ident if ident in self.catalog[group] else self.indexes[group].get(ident.casefold())
        return canonical, self.catalog[group].get(canonical)

    def unknown(self, reason, channels=CHANNELS):
        for channel in channels:
            if reason not in self.reasons[channel]:
                self.reasons[channel].append(reason)

    def affected(self, values):
        return [channel for channel, names in self.catalog["targets"].items()
                if any(values.get(name) for name in names.values())]

    def add(self, values, source, row, **metadata):
        contribution = {}
        for channel, names in self.catalog["targets"].items():
            if values.get(names["derived"], 0):
                self.unknown("直接衍生stat來源尚未驗證，不能與Base再相加。", [channel])
            value = values.get(names["base"], 0)
            if not _integer(value):
                self.unknown("飼養者加成含不合法整數。", [channel])
                continue
            if value:
                total = self.total[channel] + value
                if not _integer(total):
                    self.unknown("飼養者加成合計超出已驗證的原生整數範圍。", [channel])
                    continue
                self.total[channel] = total
                contribution[channel] = value
        if contribution:
            self.data["components"].append({"source": source, "row": row, "stats": contribution, **metadata})

    def finish(self):
        persistent = {key: self.known and not self.reasons[key] for key in CHANNELS}
        known = {key: self.total[key] if self.known else None for key in CHANNELS}
        percent = {key: known[key] if persistent[key] else None for key in CHANNELS}
        self.data.update(known_percent=known, percent=percent, persistent_complete=persistent, live_complete=False,
                         animal_percent={key: max(0, value) if value is not None else None for key, value in percent.items()},
                         scope="獨立角色／帳號資料與世界保存資料的持久來源；不是完整即時上限。",
                         unresolved=self.reasons)
        self.data["coverage"]["live_gaps"] = ["角色／帳號檔與世界檔不是一致快照。", "存檔未包含所有即時效果或完整主人StatContainer。"]
        return self.data


def _talents(owner, rows, kind, seen_scopes):
    if not isinstance(rows, list):
        owner.unknown("角色或帳號天賦清單缺失。")
        return
    entries, conflicts = {}, set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("RowName"), str) or not _integer(row.get("Rank"), 0):
            owner.unknown("角色天賦資料格式未知。")
            continue
        name, _ = owner.resolve("talents", row["RowName"])
        name, rank = name or row["RowName"], row["Rank"]
        if name in entries and entries[name] != rank:
            conflicts.add(name)
        entries[name] = rank
    owner.data["coverage"][kind + "_count"] = len(entries)
    for name, rank in entries.items():
        rewards = owner.catalog["talents"].get(name)
        if name in conflicts or rewards is None or rank > len(rewards):
            owner.unknown("天賦來源未知、階級無效或有衝突。")
            continue
        if rank == 0:
            continue
        reward = rewards[rank - 1]
        if reward and name in seen_scopes and seen_scopes[name] != kind:
            owner.unknown("同一加成天賦跨角色／帳號重複，作用範圍尚未驗證。")
            continue
        if reward:
            seen_scopes[name] = kind
        owner.add(reward, kind, name, rank=rank, same_snapshot=False)


def _bestiary(owner, data):
    affected = {channel for row in owner.catalog["bestiary"].values() for field in ("unlock1", "unlock2")
                for channel, names in owner.catalog["targets"].items() if names["base"] in row[field] or names["derived"] in row[field]}
    rows = data.get("BestiaryTracking") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        owner.unknown("帳號圖鑑進度缺失。", affected)
        return
    seen = {}
    for entry in rows:
        if not isinstance(entry, dict) or not isinstance(entry.get("BestiaryGroup"), dict):
            owner.unknown("圖鑑資料格式未支援。", affected)
            continue
        name, row = owner.resolve("bestiary", entry["BestiaryGroup"].get("RowName"))
        points = entry.get("NumPoints")
        if row is None or not _integer(points) or name in seen:
            owner.unknown("圖鑑種類未知、點數無效或有重複。", affected)
            continue
        seen[name] = points
        total = row["total_points"]
        if not _integer(total, 1) or row.get("progressive"):
            owner.unknown("圖鑑容量計算方式尚未驗證。", affected)
            continue
        if points <= 0:
            continue
        # Native: multiply total by float64 0.4, convert to float32, then trunc.
        first = math.trunc(struct.unpack("<f", struct.pack("<f", float(total) * 0.4))[0])
        if points >= first:
            owner.add(row["unlock1"], "account_bestiary", name, unlock=1, points=points, threshold=first, same_snapshot=False)
        if points >= total:
            owner.add(row["unlock2"], "account_bestiary", name, unlock=2, points=points, threshold=total, same_snapshot=False)
    owner.data["coverage"]["bestiary_groups"] = len(seen)


def _world_owner(owner, record, snapshot):
    source = {"kind": "PlayerStateRecorderComponent", "status": "ok", "same_snapshot": True,
              "sha256": _get(snapshot, "sha256"), "snapshot_time": str(_get(snapshot, "saved_at", "")),
              "world_id": _get(snapshot, "world_id")}
    modified, modified_ns = _get(snapshot, "source_mtime"), _get(snapshot, "source_mtime_ns")
    if isinstance(modified, (int, float)) and not isinstance(modified, bool) and math.isfinite(modified) and modified > 0:
        source["mtime"] = datetime.fromtimestamp(modified, timezone.utc).isoformat()
    if isinstance(modified_ns, int) and not isinstance(modified_ns, bool) and modified_ns > 0:
        source["mtime_ns"] = modified_ns
    owner.data["sources"].append(source)
    inventories = record.get("SavedInventories")
    if not isinstance(inventories, list):
        owner.unknown("同世界主人裝備資料缺失。")
        inventories = []
    active = {owner.catalog["inventory_ids"].get(key) for key in ("Equipment", "Upgrade", "Vision", "Suit")}
    armour_sets = Counter()
    seen_slots = {}
    for inventory in inventories:
        slots = inventory.get("Slots") if isinstance(inventory, dict) else None
        inventory_id = inventory.get("InventoryID") if isinstance(inventory, dict) else None
        if not isinstance(slots, list) or not _integer(inventory_id, 0):
            owner.unknown("主人庫存格式未支援。")
            continue
        for item in slots:
            if not isinstance(item, dict):
                owner.unknown("主人裝備槽格式未支援。")
                continue
            location, ident = item.get("Location"), item.get("ItemStaticData", item.get("StaticItemDataRowName"))
            if not _integer(location, 0):
                owner.unknown("主人裝備槽位未知。")
                continue
            slot_key = (inventory_id, location)
            if slot_key in seen_slots:
                if seen_slots[slot_key] != item:
                    owner.unknown("同一主人裝備槽有衝突。")
                continue
            seen_slots[slot_key] = item
            canonical, definition = owner.resolve("items", ident)
            if definition is None:
                owner.unknown("主人保存物品未知，無法排除攜帶中的加成。")
                continue
            ident = canonical
            equipped = inventory_id in active
            relevant = {}
            trait = definition.get("equippable")
            if trait:
                _, effect = owner.resolve("equippables", trait)
                if effect is None:
                    owner.unknown("主人物品的Equippable來源未知。")
                    continue
                if effect["global_stats"]:
                    owner.unknown("主人全域裝備加成尚未驗證。", owner.affected(effect["global_stats"]))
                if effect["stats"]:
                    if not equipped and not effect["all_inventories"]:
                        owner.unknown("主人加成物品目前是否啟用無法從保存槽位確認。", owner.affected(effect["stats"]))
                    elif effect["diminishing_returns"]:
                        owner.data["candidate_components"].append({"source": "world_equipment", "item": ident, "trait": trait,
                                                                    "stats": effect["stats"], "same_snapshot": True})
                        owner.unknown("裝備加成的堆疊遞減尚未核對。", owner.affected(effect["stats"]))
                    else:
                        relevant.update(effect["stats"])
            armour_id = definition.get("armour")
            if armour_id and equipped:
                _, armour = owner.resolve("armour", armour_id)
                if armour is None:
                    owner.unknown("主人護甲定義未知。")
                else:
                    for key, value in armour["stats"].items():
                        relevant[key] = relevant.get(key, 0) + value
                    if armour.get("set"):
                        armour_sets[armour["set"]] += 1
            for field in ("AdditionalStats", "Alterations", "LivingItemSlots"):
                if not isinstance(item.get(field), list):
                    owner.unknown("主人物品附加資料缺失，不能當作空。")
            if item.get("LivingItemSlots"):
                owner.unknown("主人裝備的內嵌配件尚未展開。")
            for alteration in item.get("Alterations", []) or []:
                if not isinstance(alteration, dict) or alteration.get("Name") not in owner.catalog["known_alterations"]:
                    owner.unknown("主人裝備改造來源未知。")
            for additional in item.get("AdditionalStats", []) or []:
                if not isinstance(additional, dict) or not _integer(additional.get("Value")):
                    owner.unknown("主人裝備附加stat格式未知。")
                    continue
                name, index = additional.get("Name"), additional.get("Index")
                if _integer(index, 0):
                    names = owner.catalog["stat_enum"]
                    indexed = names[index] if index < len(names) else None
                    if indexed is None or (name and name != indexed):
                        owner.unknown("主人裝備附加stat識別衝突。")
                        continue
                    name = indexed
                if name not in owner.catalog["stat_enum"]:
                    owner.unknown("主人裝備附加stat種類未知。")
                    continue
                target_names = {value for row in owner.catalog["targets"].values() for value in row.values()}
                if name in target_names:
                    if equipped:
                        relevant[name] = relevant.get(name, 0) + additional["Value"]
                    else:
                        owner.unknown("攜帶物品的附加加成啟用狀態未知。")
            owner.add(relevant, "world_equipment", ident, inventory_id=inventory_id, slot=location, same_snapshot=True)
    for name in armour_sets:
        _, bonuses = owner.resolve("armour_sets", name)
        if bonuses is None:
            owner.unknown("主人護甲套裝來源未知。")
            continue
        for bonus in bonuses:
            _, row = owner.resolve("armour_set_bonuses", bonus)
            if row is None or row.get("definition_has_target"):
                owner.unknown("主人護甲套裝加成條件尚未驗證。")
    modifiers = record.get("Modifiers")
    if not isinstance(modifiers, list):
        owner.unknown("同世界主人保存效果清單缺失。")
        modifiers = []
    for modifier in modifiers:
        _, row = owner.resolve("modifiers", modifier.get("RowName")) if isinstance(modifier, dict) else (None, None)
        if row is None:
            owner.unknown("同世界主人效果種類未知。")
        elif row["stats"]:
            owner.unknown("主人效果的有效性與疊加計算尚未驗證。", owner.affected(row["stats"]))
    owner.data["coverage"].update(world_player_state=True, inventory_slots=len(seen_slots), saved_modifiers=len(modifiers))


def summarize_owner_stats(snapshot: Any, profile_roots=None, catalog: dict | None = None) -> dict:
    """Read persistent owner sources bound to this world only.

    profile_roots accepts account folders or PlayerData folders. Explicit entries
    may be {"world_id": ..., "path": ...}; mismatched worlds are ignored. Plain
    paths are scoped to this call and are never saved globally. Profile.UserID
    and the saved OwnerCharacterID are compared internally, not emitted.
    """
    result = {"owners": {}, "coverage": {"status": "unavailable", "world_id": _get(snapshot, "world_id"),
               "world_sha256": _get(snapshot, "sha256"), "warnings": [], "live_complete": False}}
    if snapshot is None or _get(snapshot, "is_shared_map", False):
        result["coverage"]["reason"] = "共用地圖／缺少世界快照，沒有私人飼養者資料。"
        return result
    try:
        catalog = load_owner_stat_catalog() if catalog is None else catalog
        _validate_catalog(catalog)
        from .animals import ANIMAL_COMPONENTS, _ownership
        records = _get(snapshot, "raw_records")
        if not isinstance(records, list):
            raise ValueError("世界記錄缺失")
        identities = {}
        for record in records:
            if not isinstance(record, dict) or record.get("_component") not in ANIMAL_COMPONENTS or _ownership(record) not in ("owned", "explicit_tamed"):
                continue
            identity = owner_identity(record.get("OwnerCharacterID"))
            if identity is not None:
                identities.setdefault(identity, str(record.get("OwnerName") or "未知"))
        paths, warnings = _profile_candidates(snapshot, profile_roots)
        result["coverage"]["warnings"].extend(warnings)
        accounts = {}
        for path in paths:
            profile, psource = _read_profile(path / "Profile.json")
            account = profile.get("UserID") if isinstance(profile, dict) else path.name
            if not isinstance(account, str) or not any(identity[0] == account for identity in identities):
                continue
            characters, csource = _read_profile(path / "Characters.json")
            bestiary, bsource = _read_profile(path / "BestiaryData.json")
            entry = (profile, characters, bestiary, [psource, csource, bsource])
            signature = tuple(source.get("sha256") for source in entry[3])
            existing = accounts.get(account)
            if existing is not None and existing[0] != signature:
                accounts[account] = (None, None)
            elif existing is None:
                accounts[account] = (signature, entry)
        world_players = {}
        for record in records:
            if isinstance(record, dict) and record.get("_component") == PLAYER_COMPONENT:
                identity = owner_identity(record.get("PlayerCharacterID"))
                if identity is None:
                    continue
                if identity in world_players and world_players[identity] != record:
                    world_players[identity] = None
                else:
                    world_players.setdefault(identity, record)
        for identity, name in identities.items():
            key = owner_key(identity)
            owner = _OwnerResult(key, name, catalog)
            account = accounts.get(identity[0])
            if account is None or account[1] is None:
                owner.unknown("此飼養者的精確帳號／角色資料缺失或來源衝突，不能當作零加成。")
                owner.data["coverage"]["profile_match"] = False
            else:
                profile, characters, bestiary, sources = account[1]
                owner.data["sources"].extend(sources)
                rows = characters.get("Characters.json") if isinstance(characters, dict) else None
                matching = []
                if isinstance(rows, list):
                    for raw in rows:
                        try:
                            character = json.loads(raw) if isinstance(raw, str) else raw
                        except (ValueError, TypeError):
                            owner.unknown("部分角色資料無法解析，不能確認角色slot唯一。")
                            continue
                        if isinstance(character, dict) and _integer(character.get("ChrSlot"), 0) and character.get("ChrSlot") == identity[1]:
                            matching.append(character)
                if len(matching) != 1:
                    owner.unknown("角色slot缺失或重複，不能以同名角色代替。")
                    owner.data["coverage"]["profile_match"] = False
                else:
                    owner.known = True
                    owner.data["coverage"]["profile_match"] = True
                    seen_scopes = {}
                    _talents(owner, matching[0].get("Talents"), "character_talents", seen_scopes)
                    _talents(owner, profile.get("Talents") if isinstance(profile, dict) else None, "account_talents", seen_scopes)
                    _bestiary(owner, bestiary)
                    starting = catalog["starting_stats"].get("Base_Stats")
                    if starting is None:
                        owner.unknown("玩家初始stat來源缺失。")
                    else:
                        owner.add(starting, "starting_stats", "Base_Stats", same_snapshot=False)
            player = world_players.get(identity)
            if player is None:
                owner.unknown("此世界缺少唯一對應的主人保存狀態，不能確認裝備及效果。")
                owner.data["coverage"]["world_player_state"] = False
            else:
                _world_owner(owner, player, snapshot)
            result["owners"][key] = owner.finish()
        result["coverage"].update(status="partial" if any(not all(owner["persistent_complete"].values()) for owner in result["owners"].values()) else "ok",
                                  owners=len(identities), profiles_matched=sum(owner["coverage"].get("profile_match", False) for owner in result["owners"].values()))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        result["coverage"].update(status="unavailable", reason="飼養者資料或加成格式尚未支援；已保留未知。")
    return result
