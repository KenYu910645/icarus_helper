"""Application-owned settings; never write to game directories."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import tempfile
import time


DEFAULT_VISIBLE_LAYERS = frozenset({"cave", "deep", "geyser", "exotic", "oil", "drill", "player"})
MAP_LAYERS = ("cave", "deep", "ore", "exotic", "geyser", "oil", "water", "plant", "tree", "deployable", "drill", "player", "resource")


def app_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "IcarusHelper"
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_path() -> Path:
    """Keep small user preferences outside Store-app AppData redirection.

    Large map caches, snapshots and logs deliberately remain in app_dir().
    """
    return Path.home() / ".icarus-helper" / "settings.json"


def legacy_settings_paths() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidates = [local / "IcarusHelper" / "settings.json"]
    candidates += sorted((local / "Packages").glob("OpenAI.Codex_*/LocalCache/Local/IcarusHelper/settings.json"))
    seen, result = set(), []
    for path in candidates:
        identity = str(path.resolve()).casefold()
        if identity not in seen and path.is_file():
            seen.add(identity)
            result.append(path)
    return result


def _validate(data):
    if not isinstance(data, dict):
        raise ValueError("Settings root must be an object")
    worlds = data.get("worlds", {})
    if not isinstance(worlds, dict):
        raise ValueError("Invalid worlds settings format")
    for world in worlds.values():
        if not isinstance(world, dict) or not isinstance(world.get("bases", []), list):
            raise ValueError("Invalid base settings format")
        ids = set()
        for base in world.get("bases", []):
            if not isinstance(base, dict) or not isinstance(base.get("id"), str) or not base["id"] or base["id"] in ids:
                raise ValueError("Base ID is missing or duplicated")
            ids.add(base["id"])
            for axis in ("x", "y", "z"):
                if axis in base and (isinstance(base[axis], bool) or not isinstance(base[axis], (int, float)) or not math.isfinite(base[axis])):
                    raise ValueError("Invalid base coordinate format")
    return data


def _read_settings(path: Path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Settings file exceeds the supported size")
    return _validate(json.loads(path.read_text(encoding="utf-8-sig")))


@contextmanager
def _file_lock(path: Path, timeout=5):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.with_name(path.name + ".lock").open("a+b", buffering=0)
    acquired = False
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
        deadline = time.monotonic() + timeout
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise OSError("Another Helper is saving settings. Try again shortly")
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _atomic_write(path: Path, data):
    _validate(data)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


_MISSING = object()


def _copy(value):
    return _MISSING if value is _MISSING else deepcopy(value)


def _merge(base, local, disk, path=()):
    """Apply only local changes to the newest disk version, including deletes."""
    if local == base:
        return _copy(disk)
    if local is _MISSING:
        return _MISSING
    if path and path[-1] == "bases" and isinstance(local, list):
        before = {item["id"]: item for item in base} if isinstance(base, list) else {}
        current = {item["id"]: item for item in local}
        latest = {item["id"]: item for item in disk} if isinstance(disk, list) else {}
        order = list(latest)
        for ident in before.keys() - current.keys():
            latest.pop(ident, None)
        for ident, item in current.items():
            if ident in before and ident not in latest and item != before[ident]:
                raise OSError("Another Helper window deleted this base. Disk settings were preserved; reload before continuing")
            merged = _merge(before.get(ident, _MISSING), item, latest.get(ident, _MISSING), path + (ident,))
            if merged is _MISSING:
                latest.pop(ident, None)
            else:
                if ident not in order:
                    order.append(ident)
                latest[ident] = merged
        return [latest[ident] for ident in order if ident in latest]
    if isinstance(local, dict) and (base is _MISSING or isinstance(base, dict)):
        before = base if isinstance(base, dict) else {}
        latest = _copy(disk) if isinstance(disk, dict) else {}
        for key in before.keys() | local.keys():
            merged = _merge(before.get(key, _MISSING), local.get(key, _MISSING), latest.get(key, _MISSING), path + (key,))
            if merged is _MISSING:
                latest.pop(key, None)
            else:
                latest[key] = merged
        return latest
    return _copy(local)


def _legacy_merge(primary, other, conflicts, path=()):
    """Preserve the first (normal AppData) source and recover missing bases."""
    if isinstance(primary, dict) and isinstance(other, dict):
        result = deepcopy(primary)
        for key, value in other.items():
            if key not in result:
                result[key] = deepcopy(value)
            else:
                result[key] = _legacy_merge(result[key], value, conflicts, path + (key,))
        return result
    if path and path[-1] == "bases" and isinstance(primary, list) and isinstance(other, list):
        result = deepcopy(primary)
        by_id = {base["id"]: i for i, base in enumerate(result)}
        for base in other:
            if base["id"] not in by_id:
                by_id[base["id"]] = len(result)
                result.append(deepcopy(base))
            else:
                index = by_id[base["id"]]
                result[index] = _legacy_merge(result[index], base, conflicts, path + (base["id"],))
        return result
    if primary != other:
        conflicts.append({"field": "/".join(path), "kept": deepcopy(primary), "alternate": deepcopy(other)})
    return deepcopy(primary)


def _migrate(path: Path):
    with _file_lock(path):
        if path.exists():
            return
        candidates = legacy_settings_paths()
        if not candidates:
            return
        combined, sources, conflicts, invalid = {}, [], [], []
        payloads = []
        for source in candidates:
            raw = source.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            backup = path.parent / "legacy-backups" / f"settings-{digest}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            try:
                with backup.open("xb") as handle:
                    handle.write(raw)
            except FileExistsError:
                if backup.read_bytes() != raw:
                    raise OSError("Legacy settings backup conflict; migration stopped")
            entry = {"source": str(source), "resolved_source": str(source.resolve()),
                     "sha256": digest, "backup": str(backup)}
            try:
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("Settings file exceeds the supported size")
                data = _validate(json.loads(raw.decode("utf-8-sig")))
                combined = _legacy_merge(combined, data, conflicts)
                payloads.append(data)
            except (ValueError, OSError) as exc:
                entry["error"] = str(exc)
                invalid.append(str(source))
            sources.append(entry)
        if not payloads:
            raise OSError("Legacy settings could not be parsed. Original and backup were preserved; bases were not replaced with empty settings")
        # Keep an existing valid selection; use a recovered source's valid active
        # base only if the preferred source did not have one.
        for world_id, world in combined.get("worlds", {}).items():
            ids = {base["id"] for base in world.get("bases", [])}
            if world.get("active_base") not in ids:
                for data in payloads:
                    active = data.get("worlds", {}).get(world_id, {}).get("active_base")
                    if active in ids:
                        world["active_base"] = active
                        break
        combined["_migration"] = {"completed": True, "sources": sources, "conflicts": conflicts, "invalid_sources": invalid}
        _atomic_write(path, combined)


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else settings_path()
        self.error = ""
        try:
            if path is None and not self.path.exists():
                _migrate(self.path)
            self.data = _read_settings(self.path) if self.path.exists() else {}
        except (ValueError, OSError) as exc:
            self.data = {}
            self.error = f"Could not load settings: {exc}. Original file preserved; check it before saving settings."
        self._baseline = deepcopy(self.data)

    def save(self):
        if self.error:
            raise OSError(self.error)
        try:
            _validate(self.data)
        except (TypeError, ValueError) as exc:
            raise OSError(f"Invalid settings format; original file preserved: {exc}") from exc
        with _file_lock(self.path):
            try:
                disk = _read_settings(self.path) if self.path.exists() else {}
            except (ValueError, OSError) as exc:
                raise OSError(f"Could not read disk settings; original file preserved: {exc}") from exc
            merged = _merge(self._baseline, self.data, disk)
            for world in merged.get("worlds", {}).values():
                if world.get("active_base") and world["active_base"] not in {base["id"] for base in world.get("bases", [])}:
                    world["active_base"] = ""
            try:
                _atomic_write(self.path, merged)
            except (TypeError, ValueError) as exc:
                raise OSError(f"Invalid settings format; original file preserved: {exc}") from exc
            self.data = merged
            self._baseline = deepcopy(merged)

    def world(self, world_id: str) -> dict:
        world = self.data.setdefault("worlds", {}).setdefault(world_id, {})
        world.setdefault("bases", [])
        world.setdefault("checklists", {})
        return world

    def map_display(self) -> dict:
        stored = self.data.get("map_display", {})
        stored = stored if isinstance(stored, dict) else {}
        layers = stored.get("layers", {})
        layers = layers if isinstance(layers, dict) else {}
        return {"layers": {key: layers[key] if isinstance(layers.get(key), bool) else key in DEFAULT_VISIBLE_LAYERS for key in MAP_LAYERS},
                "show_candidates": stored.get("show_candidates") if isinstance(stored.get("show_candidates"), bool) else True}

