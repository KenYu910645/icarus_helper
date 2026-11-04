"""Versioned world archives and explicitly requested, guarded world restores.

Only this module's restore() may replace a registered game save.  Metadata is
never sufficient authority to restore or delete a file: payloads are checked
again, and every managed path must remain inside the independent backup root.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import time
import uuid
import zlib

from .saves import (MAX_ARRAY_ITEMS, MAX_BLOB_BYTES, Reader, SaveError,
                    _json_bytes, _read_stable, load_snapshot, map_name_for, read_props, read_value)
from .backup_players import summarize_backup_players

UTC = timezone.utc


class BackupError(ValueError):
    """A backup operation cannot be completed without risking unrelated data."""


def _now():
    return datetime.now(UTC)


def _iso(moment):
    return moment.astimezone(UTC).isoformat() if moment else None


def _date(value):
    try:
        result = datetime.fromisoformat(str(value))
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _same_path(first, second):
    return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))


def _no_links(path):
    """Reject symlinks and Windows junctions, including existing ancestors."""
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        # Cloud placeholders are reparse points too, but do not redirect paths.
        tag = getattr(info, 'st_reparse_tag', 0)
        if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400 and tag & 0x20000000):
            raise BackupError("The backup path contains a link or junction; operation stopped")
    return path


@contextmanager
def _staged(path, payload):
    path = Path(path)
    _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _write(path, payload):
    with _staged(path, payload) as temporary:
        os.replace(temporary, path)


def _write_json(path, value):
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))


@contextmanager
def _lock(path):
    _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b', buffering=0)
    acquired = False
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0')
        deadline = time.monotonic() + 20
        while not acquired:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise BackupError("Another window is processing world saves. Try again later")
                time.sleep(.04)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _properties(reader):
    """Bounded UE property iterator; large recorder byte arrays stay as bytes."""
    while reader.p < reader.n:
        name = reader.fstr()
        if name == 'None':
            return
        kind = reader.fstr()
        size, index = reader.i32(), reader.i32()
        if size < 0 or size > MAX_BLOB_BYTES:
            raise BackupError("Invalid world property length")
        struct_type = inner_type = None
        if kind == 'StructProperty':
            struct_type = reader.fstr()
            reader.take(17)
        elif kind == 'ArrayProperty':
            inner_type = reader.fstr()
            reader.byte()
        elif kind == 'BoolProperty':
            reader.take(2)
        elif kind in ('ByteProperty', 'EnumProperty'):
            reader.fstr()
            reader.byte()
        elif kind == 'MapProperty':
            reader.fstr()
            reader.fstr()
            reader.byte()
        else:
            reader.byte()
        yield name, kind, Reader(reader.take(size)), struct_type, inner_type, index
    raise BackupError("World properties are incomplete")


def _game_seed(body):
    for name, kind, value, stype, inner, index in _properties(body):
        if name == 'GameModeRecord' and kind == 'StructProperty':
            for child, ckind, cvalue, _, _, cindex in _properties(value):
                if child == 'GameStateSeed' and cindex == 0 and ckind in ('IntProperty', 'Int64Property', 'UInt32Property'):
                    return read_value(cvalue, ckind, cvalue.n, None, None)
    return None


def _inspect(data, source):
    envelope = _json_bytes(data)
    info, blob = envelope.get('ProspectInfo'), envelope.get('ProspectBlob')
    if not isinstance(info, dict) or not isinstance(blob, dict):
        raise BackupError("Unsupported world save")
    checksum, expected = blob.get('Hash'), blob.get('UncompressedLength')
    if (not isinstance(checksum, str) or len(checksum) != 40
            or not isinstance(expected, int) or isinstance(expected, bool) or expected < 0):
        raise BackupError("World content hash/length is missing; a restorable version cannot be created")
    try:
        compressed = base64.b64decode(blob['BinaryBlob'], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_BLOB_BYTES + 1)
    except (KeyError, TypeError, binascii.Error, zlib.error) as exc:
        raise BackupError("Compressed world data could not be verified") from exc
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(raw) != expected or len(raw) > MAX_BLOB_BYTES
            or hashlib.sha1(raw).hexdigest().lower() != checksum.lower()):
        raise BackupError("Compressed world content, length or hash does not match")
    top, seeds, player_records = {}, [], []
    for name, kind, body, stype, inner, index in _properties(Reader(raw)):
        if index != 0:
            continue
        if name == 'StateRecorderBlobs' and kind == 'ArrayProperty' and inner == 'StructProperty':
            count = body.i32()
            if not 0 <= count <= MAX_ARRAY_ITEMS:
                raise BackupError("Invalid world record count")
            body.fstr()
            if body.fstr() != 'StructProperty':
                raise BackupError("World record format does not match")
            body.take(8)
            body.fstr()
            body.take(17)
            for _ in range(count):
                component, binary = '', None
                for field, fkind, content, _, finner, findex in _properties(body):
                    if field == 'ComponentClassName' and fkind in ('StrProperty', 'NameProperty'):
                        component = content.fstr()
                    elif field == 'BinaryData' and fkind == 'ArrayProperty' and finner == 'ByteProperty':
                        length = content.i32()
                        if not 0 <= length <= MAX_BLOB_BYTES:
                            raise BackupError("Invalid world record length")
                        binary = content.take(length)
                        if content.p != content.n:
                            raise BackupError("World record contains extra content")
                if binary is None:
                    raise BackupError("World record content is missing")
                if component.endswith('GameModeStateRecorderComponent'):
                    seeds.append(_game_seed(Reader(binary)))
                elif component.endswith('PlayerStateRecorderComponent'):
                    # Decode only players, not the many unrelated actor payloads.
                    # A preview failure must not change archive integrity semantics.
                    try:
                        player_records.append(read_props(Reader(binary)))
                    except (SaveError, ValueError, TypeError, struct.error, UnicodeError):
                        player_records.append(None)
            if body.p != body.n:
                raise BackupError("World record list contains extra content")
            top['records'] = count
        elif name in ('Version', 'ProspectMapName', 'LastSavedDateTime'):
            top[name] = read_value(body, kind, body.n, stype, inner)
    if top.get('Version') != 2 or 'records' not in top:
        raise BackupError("The world format version has not been verified; backup/restoration stopped")
    if len(seeds) != 1 or not isinstance(seeds[0], int):
        raise BackupError("A unique world seed cannot be confirmed; backup/restoration stopped")
    identity = {'account': str(info.get('ClaimedAccountID') or Path(source).parent.parent.name),
                'prospect_id': str(info.get('ProspectID') or ''),
                'prospect_key': str(info.get('ProspectDTKey') or ''),
                'map_key': str(top.get('ProspectMapName') or ''), 'seed': seeds[0]}
    if not all(identity[key] for key in ('account', 'prospect_id', 'prospect_key', 'map_key')):
        raise BackupError("World identity data is incomplete")
    token = '|'.join(str(identity[key]) for key in ('account', 'prospect_id', 'prospect_key', 'seed'))
    saved_at = None
    ticks = top.get('LastSavedDateTime')
    if isinstance(ticks, int) and 0 < ticks < 3_155_378_975_999_999_999:
        saved_at = datetime(1, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
    try:
        preview = summarize_backup_players(info, player_records, _iso(saved_at), identity['map_key'])
    except Exception:
        # Extra presentation data must never reject an archive accepted by the
        # independent envelope/world checks above, even on unforeseen formats.
        preview = {'schema_version': 1, 'status': 'unknown', 'players': [],
                   'warnings': ['Player summary could not be parsed; backup integrity has been checked separately.'],
                   'source': 'backup', 'sample_time': _iso(saved_at)}
    return {'world_id': _sha(token.encode('utf-8'))[:24], 'identity': identity,
            'saved_at': _iso(saved_at), 'sha256': _sha(data), 'size': len(data),
            'map_name': map_name_for(identity['prospect_key'], identity['map_key']),
            'player_preview': preview}


def game_processes():
    """Enumerate native processes without loading, controlling or opening a game."""
    if os.name != 'nt':
        raise BackupError("Game processes cannot be checked on this system; restoration is blocked")
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    api.Process32FirstW.restype = wintypes.BOOL
    api.Process32NextW.argtypes = api.Process32FirstW.argtypes
    api.Process32NextW.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise BackupError("Cannot verify that the game is closed; restoration is blocked")
    result = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not api.Process32FirstW(handle, ctypes.byref(entry)):
            raise BackupError("Cannot list game processes; restoration is blocked")
        while True:
            name = entry.szExeFile.lower()
            if name.startswith('icarus') and name not in ('icarus helper.exe', 'icarushelper.exe'):
                result.append(entry.szExeFile)
            if not api.Process32NextW(handle, ctypes.byref(entry)):
                if ctypes.get_last_error() not in (0, 18):
                    raise BackupError("Game process verification was interrupted; restoration is blocked")
                break
    finally:
        api.CloseHandle(handle)
    return result


class BackupStore:
    def __init__(self, root=None):
        self.root = _no_links(Path(root) if root is not None else Path.home() / 'IcarusSaveGuard').resolve()
        self.registry_path = self.root / 'worlds.json'
        self.lock_path = self.root / 'operations.lock'
        self._cache = {}

    def _managed(self, path):
        absolute = _no_links(path).resolve()
        if not absolute.is_relative_to(self.root) or absolute == self.root:
            raise BackupError("Backup path is outside the managed directory")
        return absolute

    def _registry(self):
        if not self.registry_path.exists():
            return {'schema_version': 1, 'worlds': {}}
        try:
            value = json.loads(self._managed(self.registry_path).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise BackupError("World backup settings are unreadable; the original file was preserved") from exc
        if value.get('schema_version') != 1 or not isinstance(value.get('worlds'), dict):
            raise BackupError("Unsupported world backup settings version")
        return value

    def _world(self, world_id):
        context = self._registry()['worlds'].get(world_id)
        if not isinstance(context, dict) or context.get('world_id') != world_id:
            raise BackupError("Backup settings have not been created for this world")
        from .release_sync import is_remote_cache_path
        if is_remote_cache_path(context['source']):
            raise BackupError("The public sync cache is not a host world source that can be backed up or restored")
        self._managed(context['backup_dir'])
        _no_links(context['source'])
        return context

    def worlds(self):
        with _lock(self.lock_path):
            return list(self._registry()['worlds'].values())

    def register(self, snapshot):
        from .release_sync import is_remote_cache_path
        if getattr(snapshot, 'is_remote', False) or (getattr(snapshot, 'source_path', None) and is_remote_cache_path(snapshot.source_path)):
            raise BackupError("Public sync copies are read-only and cannot register backups or restore the host world")
        if getattr(snapshot, 'is_shared_map', False) or not getattr(snapshot, 'source_path', None):
            raise BackupError("The shared map has no host world save to back up")
        source = _no_links(snapshot.source_path).resolve()
        if source.is_relative_to(self.root):
            raise BackupError("Select the original host world file; a backup folder cannot be a restore target")
        data, _ = _read_stable(source)
        identity = _inspect(data, source)
        if identity['world_id'] != snapshot.world_id:
            raise BackupError("The world has changed since loading. Refresh again")
        with _lock(self.lock_path):
            registry = self._registry()
            existing = registry['worlds'].get(snapshot.world_id)
            if existing:
                if existing.get('identity') != identity['identity'] or not _same_path(existing['source'], source):
                    raise BackupError("This world is registered to a different source; retain the original source to prevent an incorrect restoration")
                return existing
            directory = self.root / 'Backups' / snapshot.world_id
            # Preserve the deployed guard's directory; never import by world name alone.
            legacy = self.root / 'config.json'
            if legacy.is_file():
                try:
                    old = json.loads(legacy.read_text(encoding='utf-8-sig'))
                    if _same_path(old['source'], source) and old.get('world') == identity['identity']['prospect_id']:
                        directory = self._managed(old['backup_root'])
                except (OSError, ValueError, KeyError):
                    pass
            if any(_same_path(item['backup_dir'], directory) for item in registry['worlds'].values()):
                directory = self.root / 'Backups' / snapshot.world_id
            context = {'world_id': snapshot.world_id, 'name': identity['identity']['prospect_id'],
                       'map_name': identity['map_name'], 'source': str(source), 'identity': identity['identity'],
                       'backup_dir': str(self._managed(directory)), 'retention_days': 0,
                       'registered_at': _iso(_now())}
            registry['worlds'][snapshot.world_id] = context
            _write_json(self.registry_path, registry)
            return context

    def _inspect_file(self, path, context, *, force=True):
        path = self._managed(path)
        cached = self._cache.get(str(path))
        if not force and cached:
            info = path.stat()
            signature = (info.st_mtime_ns, info.st_size, info.st_ino)
            if cached[0] == signature:
                if cached[1]['identity'] != context['identity'] or cached[1]['world_id'] != context['world_id']:
                    raise BackupError("The backup belongs to a different world, seed or account")
                return cached[1], None
        data, info = _read_stable(path)
        signature = (info.st_mtime_ns, info.st_size, info.st_ino)
        if cached and cached[0] == signature and cached[1]['sha256'] == _sha(data):
            if cached[1]['identity'] != context['identity'] or cached[1]['world_id'] != context['world_id']:
                raise BackupError("The backup belongs to a different world, seed or account")
            return cached[1], data
        result = _inspect(data, context['source'])
        if result['identity'] != context['identity'] or result['world_id'] != context['world_id']:
            raise BackupError("The backup belongs to a different world, seed or account")
        self._cache[str(path)] = (signature, result)
        return result, data

    def _versions(self, context):
        directory = self._managed(context['backup_dir'])
        if not directory.exists():
            return []
        versions = []
        for path in directory.glob('*.json'):
            if path.name.endswith('.metadata.json'):
                continue
            entry = {'id': path.name, 'path': str(path), 'saved_at': None, 'captured_at': None,
                     'size': None, 'sha256': '', 'verified': False, 'error': '', 'protected': False,
                     'player_preview': None}
            try:
                entry['size'] = path.lstat().st_size
                result, _ = self._inspect_file(path, context, force=False)
                entry.update({key: result[key] for key in ('saved_at', 'size', 'sha256')})
                entry['player_preview'] = result.get('player_preview')
                entry['verified'] = True
                sidecar = path.with_suffix('.metadata.json')
                if sidecar.exists():
                    meta = json.loads(self._managed(sidecar).read_text(encoding='utf-8-sig'))
                    if (meta.get('sha256') == result['sha256']
                            and int(meta.get('bytes', meta.get('size', -1))) == result['size']
                            and meta.get('world', context['name']) == context['name']
                            and _same_path(meta.get('source', ''), context['source'])):
                        captured = _date(meta.get('captured_at_utc', meta.get('captured_at')))
                        entry['captured_at'] = _iso(captured)
                    else:
                        entry['error'] = 'Backup content is valid, but capture time records disagree; automatic deletion is disabled'
                if entry['captured_at'] is None:
                    entry['error'] = entry['error'] or 'No reliable capture time is available; automatic deletion is disabled'
            except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
                entry['verified'] = False
                entry['error'] = str(exc)
            versions.append(entry)
        versions.sort(key=lambda item: (item['captured_at'] or item['saved_at'] or '', item['id']), reverse=True)
        newest = next((entry for entry in versions if entry['verified']), None)
        if newest:
            newest['protected'] = True
        return versions

    def _status(self, world_id):
        path = self.root / 'daemon-status.json'
        result = {'state': 'idle', 'message': 'Waiting for the backup service', 'last_success': None, 'heartbeat': None}
        if not path.exists():
            return result
        try:
            state = json.loads(self._managed(path).read_text(encoding='utf-8-sig'))
            heartbeat = state.get('heartbeat_at_utc', state.get('heartbeat'))
            date = _date(heartbeat)
            world = state.get('worlds', {}).get(world_id, {})
            error = world.get('last_error') or world.get('prune_error') or state.get('error') or state.get('last_error')
            installation = self.root / 'service-installation.json'
            if installation.exists():
                install = json.loads(self._managed(installation).read_text(encoding='utf-8-sig'))
                error = error or install.get('error')
            result.update(heartbeat=heartbeat, last_success=world.get('last_success', state.get('last_success')))
            if error:
                result.update(state='error', message=str(error.get('message', error) if isinstance(error, dict) else error))
            elif not date or (_now() - date).total_seconds() > 45:
                result.update(state='stale', message='The backup service has not responded recently')
            else:
                result.update(state='running', message='Backup monitoring active')
        except (ValueError, OSError):
            result.update(state='error', message='Backup service status is unreadable')
        return result

    def summary(self, world_id):
        with _lock(self.lock_path):
            context = self._world(world_id)
            versions = self._versions(context)
            try:
                data, _ = _read_stable(Path(context['source']))
                inspected = _inspect(data, context['source'])
                source_hash = inspected['sha256'] if inspected['identity'] == context['identity'] else None
            except (OSError, ValueError):
                source_hash = None
            return {'world': context, 'context': context, 'versions': versions,
                    'retention_days': context['retention_days'], 'total_bytes': sum(v['size'] or 0 for v in versions),
                    'version_count': len(versions), 'source_sha256': source_hash, 'status': self._status(world_id)}

    def _capture(self, context):
        source = _no_links(context['source']).resolve()
        data, _ = _read_stable(source)
        result = _inspect(data, source)
        if result['identity'] != context['identity']:
            raise BackupError("The source world/seed has changed; backup stopped")
        directory = self._managed(context['backup_dir'])
        directory.mkdir(parents=True, exist_ok=True)
        # A restored old world must reuse its archive instead of consuming new space.
        for candidate in directory.glob('*_' + result['sha256'][:16] + '.json'):
            checked, _ = self._inspect_file(candidate, context)
            if checked['sha256'] == result['sha256']:
                return dict(result, path=str(candidate), id=candidate.name, created=False)
        captured = _now()
        stamp = (_date(result['saved_at']) or captured).astimezone().strftime('%Y%m%d_%H%M%S_%f')
        target = self._managed(directory / f"{stamp}_{result['sha256'][:16]}.json")
        if target.exists():
            raise BackupError("Backup content conflicts with an existing filename; the existing file was preserved")
        _write(target, data)
        if _sha(target.read_bytes()) != result['sha256']:
            raise BackupError("Backup verification failed after writing")
        _write_json(target.with_suffix('.metadata.json'),
                    {'world': context['name'], 'world_id': context['world_id'], 'identity': context['identity'],
                     'source': str(source), 'bytes': len(data), 'sha256': result['sha256'],
                     'internal_saved_at_utc': result['saved_at'], 'captured_at_utc': _iso(captured),
                     'backup_file': str(target), 'envelope_verified': True})
        return dict(result, path=str(target), id=target.name, created=True, captured_at=_iso(captured))

    def capture(self, world_id):
        with _lock(self.lock_path):
            return self._capture(self._world(world_id))

    @staticmethod
    def _days(days):
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 36500:
            raise BackupError("Retention must be 0 to 36500 days; 0 keeps backups forever")
        return days

    def set_retention(self, world_id, days):
        days = self._days(days)
        with _lock(self.lock_path):
            registry = self._registry()
            self._world(world_id)
            registry['worlds'][world_id]['retention_days'] = days
            _write_json(self.registry_path, registry)
        return days

    def _preview(self, context, days, now):
        if not days:
            return {'count': 0, 'total_bytes': 0, 'versions': [], 'days': 0}
        cutoff = now - timedelta(days=days)
        selected = [v for v in self._versions(context) if days and v['verified'] and not v['protected']
                    and _date(v['captured_at']) is not None and _date(v['captured_at']) < cutoff]
        return {'count': len(selected), 'total_bytes': sum(v['size'] for v in selected),
                'versions': [v['id'] for v in selected], 'days': days}

    def retention_preview(self, world_id, days):
        with _lock(self.lock_path):
            return self._preview(self._world(world_id), self._days(days), _now())

    def prune(self, world_id, now=None):
        moment = now or _now()
        if moment.tzinfo is None:
            raise BackupError("Cleanup time must include a time zone")
        with _lock(self.lock_path):
            context = self._world(world_id)
            preview = self._preview(context, self._days(context['retention_days']), moment)
            deleted = []
            for name in preview['versions']:
                target = self._managed(Path(context['backup_dir']) / name)
                sidecar = self._managed(target.with_suffix('.metadata.json'))
                # Re-read both immediately before the irreversible mutation.
                checked, _ = self._inspect_file(target, context, force=True)
                metadata = json.loads(sidecar.read_text(encoding='utf-8-sig'))
                checked_time = _date(metadata.get('captured_at_utc', metadata.get('captured_at')))
                if checked_time is None or checked_time >= moment - timedelta(days=context['retention_days']):
                    continue
                if (metadata.get('sha256') != checked['sha256']
                        or int(metadata.get('bytes', metadata.get('size', -1))) != checked['size']
                        or metadata.get('world', context['name']) != context['name']
                        or not _same_path(metadata.get('source', ''), context['source'])):
                    continue
                target.unlink()
                sidecar.unlink()
                self._cache.pop(str(target), None)
                deleted.append(name)
            return dict(preview, deleted=deleted)

    def restore(self, world_id, version_id, expected_source_sha256=None, expected_backup_sha256=None):
        if not isinstance(version_id, str) or Path(version_id).name != version_id or not version_id.endswith('.json') or version_id.endswith('.metadata.json'):
            raise BackupError("Select a complete backup version from this world’s list")
        with _lock(self.lock_path):
            context = self._world(world_id)
            if game_processes():
                raise BackupError("Fully close ICARUS and its dedicated server before restoring the world")
            target = self._managed(Path(context['backup_dir']) / version_id)
            candidate, candidate_bytes = self._inspect_file(target, context, force=True)
            if expected_backup_sha256 is not None and candidate['sha256'] != expected_backup_sha256:
                raise BackupError("The backup content changed after confirmation; select and confirm it again")
            # The normal complete decoder validates every recorder before a restore.
            load_snapshot(target)
            source = _no_links(context['source']).resolve()
            before, signature = _read_stable(source)
            original = _inspect(before, source)
            if original['identity'] != context['identity']:
                raise BackupError("The source is now a different world; restoration cancelled")
            load_snapshot(source)
            if expected_source_sha256 and original['sha256'] != expected_source_sha256:
                raise BackupError("The world was saved again after confirmation; select and confirm it again")
            folder = self._managed(self.root / 'Restores' / (_now().astimezone().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]))
            folder.mkdir(parents=True)
            preserved = folder / 'world_before.json'
            _write(preserved, before)
            if _sha(preserved.read_bytes()) != original['sha256']:
                raise BackupError("Safety backup verification failed; the original world was not changed")
            receipt = {'world_id': world_id, 'source': str(source), 'version_id': version_id,
                       'requested_at_utc': _iso(_now()), 'before_sha256': original['sha256'],
                       'after_sha256': candidate['sha256'], 'safety_backup': str(preserved),
                       'receipt_path': str(folder / 'restoration_receipt.json'), 'status': 'prepared'}
            _write_json(folder / 'restoration_receipt.json', receipt)
            # Complete and fsync the staging file *before* the final source/process
            # gates. No lengthy disk write remains between those gates and replace.
            with _staged(source, candidate_bytes) as temporary:
                fresh, freshstat = _read_stable(source)
                if ((signature.st_mtime_ns, signature.st_size, signature.st_ino) !=
                        (freshstat.st_mtime_ns, freshstat.st_size, freshstat.st_ino)
                        or _sha(fresh) != original['sha256']):
                    raise BackupError("The original world changed while creating the safety backup; restoration cancelled")
                if game_processes():
                    raise BackupError("The game started; restoration cancelled and the original world is unchanged")
                _no_links(source)
                os.replace(temporary, source)
            try:
                if _sha(source.read_bytes()) != candidate['sha256']:
                    raise OSError("The replacement content hash does not match the selected version")
            except OSError as exc:
                receipt.update(status='restored_warning', warning="The world was replaced, but post-restore verification did not complete: " + str(exc)
                               + "; refresh to verify. Safety backup: " + str(preserved))
                try:
                    _write_json(folder / 'restoration_receipt.json', receipt)
                except OSError:
                    pass
                return receipt
            receipt.update(status='restored', completed_at_utc=_iso(_now()))
            try:
                _write_json(folder / 'restoration_receipt.json', receipt)
            except OSError as exc:
                receipt.update(status='restored_warning', warning="The world was replaced and verified, but the completion receipt could not be written: "
                               + str(exc) + "; safety backup: " + str(preserved))
            return receipt
