"""Rollback tests use synthetic worlds only, never production save replacement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from icarus_helper import world_backups as backups
from icarus_helper.saves import load_snapshot
from test_saves import save_fixture

UTC = timezone.utc


def world(path, marker=1, seed=919):
    save_fixture(path, records=[
        ('/Script/Icarus.GameModeStateRecorderComponent', {'GameModeRecord': {'GameStateSeed': seed}, 'Marker': marker})])
    return load_snapshot(path)


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, 'game_processes', lambda: [])
    source = tmp_path / '使用者' / '帳號' / 'Prospects' / '世界.json'
    snapshot = world(source)
    store = backups.BackupStore(tmp_path / 'backed up')
    store.register(snapshot)
    return store, source, snapshot.world_id


def meta(path, **changes):
    sidecar = Path(path).with_suffix('.metadata.json')
    value = json.loads(sidecar.read_text(encoding='utf-8'))
    value.update(changes)
    sidecar.write_text(json.dumps(value), encoding='utf-8')


def test_capture_deduplicates_complete_original_bytes_and_preserves_source(context):
    store, source, world_id = context
    before = source.read_bytes()
    first = store.capture(world_id)
    assert first['created']
    assert Path(first['path']).read_bytes() == before
    assert not store.capture(world_id)['created']
    summary = store.summary(world_id)
    assert summary['version_count'] == 1
    assert summary['total_bytes'] == len(before)
    assert summary['versions'][0]['verified']
    assert summary['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before


def test_retention_defaults_to_forever_and_only_deletes_expired_captured_versions(context):
    store, source, world_id = context
    first = store.capture(world_id)
    moment = datetime.now(UTC)
    meta(first['path'], captured_at_utc=(moment - timedelta(days=40)).isoformat())
    world(source, marker=2)
    second = store.capture(world_id)
    meta(second['path'], captured_at_utc=(moment - timedelta(days=20)).isoformat())
    world(source, marker=3)
    third = store.capture(world_id)
    assert store.prune(world_id, moment)['deleted'] == []
    store.set_retention(world_id, 7)
    assert store.retention_preview(world_id, 7)['count'] == 2
    deleted = store.prune(world_id, moment)['deleted']
    assert set(deleted) == {first['id'], second['id']}
    assert Path(third['path']).exists()
    assert not Path(first['path']).with_suffix('.metadata.json').exists()


def test_retention_keeps_latest_valid_unknown_time_corrupt_and_safety_backups(context):
    store, source, world_id = context
    first = store.capture(world_id)
    meta(first['path'], captured_at_utc='not-a-date')
    world(source, marker=2)
    second = store.capture(world_id)
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    meta(second['path'], captured_at_utc=old)
    corrupt = Path(first['path']).parent / 'bad.json'
    corrupt.write_bytes(b'{broken')
    safety = store.root / 'Restores' / 'keep.json'
    safety.parent.mkdir()
    safety.write_bytes(b'untouched')
    store.set_retention(world_id, 1)
    assert store.prune(world_id)['deleted'] == []
    assert Path(first['path']).exists() and Path(second['path']).exists()
    assert corrupt.read_bytes() == b'{broken' and safety.read_bytes() == b'untouched'


def test_register_legacy_guard_keeps_existing_directory_and_versions(context):
    store, source, world_id = context
    legacy_root = store.root.parent / '舊版'
    directory = legacy_root / 'Backups' / 'GuoGuoGaGa'
    directory.mkdir(parents=True)
    snapshot = load_snapshot(source)
    (legacy_root / 'config.json').write_text(json.dumps({'source': str(source), 'world': snapshot.name,
                                                       'backup_root': str(directory)}), encoding='utf-8')
    payload = source.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    target = directory / ('old_' + sha[:16] + '.json')
    target.write_bytes(payload)
    target.with_suffix('.metadata.json').write_text(json.dumps({'world': snapshot.name, 'source': str(source),
        'bytes': len(payload), 'sha256': sha, 'captured_at_utc': datetime.now(UTC).isoformat()}), encoding='utf-8')
    upgraded = backups.BackupStore(legacy_root)
    registered = upgraded.register(snapshot)
    assert Path(registered['backup_dir']) == directory
    assert upgraded.summary(world_id)['versions'][0]['verified']
    assert not upgraded.capture(world_id)['created']


def test_restore_creates_immutable_safety_copy_and_receipt(context):
    store, source, world_id = context
    old = source.read_bytes()
    first = store.capture(world_id)
    world(source, marker=2)
    before = source.read_bytes()
    receipt = store.restore(world_id, first['id'], hashlib.sha256(before).hexdigest())
    assert source.read_bytes() == old
    assert Path(receipt['safety_backup']).read_bytes() == before
    assert receipt['status'] == 'restored'
    assert json.loads(Path(receipt['receipt_path']).read_text(encoding='utf-8')) == receipt


def test_restore_refuses_game_running_before_any_mutation(context, monkeypatch):
    store, source, world_id = context
    version = store.capture(world_id)
    monkeypatch.setattr(backups, 'game_processes', lambda: ['IcarusServer-Win64-Shipping.exe'])
    with pytest.raises(backups.BackupError, match='close'):
        store.restore(world_id, version['id'])
    assert not (store.root / 'Restores').exists()


def test_restore_refuses_unknown_process_state_and_game_start_during_preparation(context, monkeypatch):
    store, source, world_id = context
    version = store.capture(world_id)
    before = source.read_bytes()
    calls = iter([[], ['Icarus.exe']])
    monkeypatch.setattr(backups, 'game_processes', lambda: next(calls))
    with pytest.raises(backups.BackupError, match='started'):
        store.restore(world_id, version['id'])
    assert source.read_bytes() == before
    def uncertain():
        raise backups.BackupError('無法核對')
    monkeypatch.setattr(backups, 'game_processes', uncertain)
    with pytest.raises(backups.BackupError, match='無法核對'):
        store.restore(world_id, version['id'])


def test_world_seed_changes_are_not_accepted_as_same_world(context):
    store, source, world_id = context
    version = store.capture(world_id)
    world(source, seed=920)
    before = source.read_bytes()
    with pytest.raises(backups.BackupError, match='seed'):
        store.capture(world_id)
    with pytest.raises(backups.BackupError, match='different world'):
        store.restore(world_id, version['id'])
    assert source.read_bytes() == before


def test_restore_rejects_wrong_seed_payload_even_with_forged_sidecar(context, tmp_path):
    store, source, world_id = context
    version = store.capture(world_id)
    evil = tmp_path / 'wrong.json'
    world(evil, seed=920)
    Path(version['path']).write_bytes(evil.read_bytes())
    meta(version['path'], sha256=hashlib.sha256(evil.read_bytes()).hexdigest(), bytes=evil.stat().st_size)
    with pytest.raises(backups.BackupError, match='different world'):
        store.restore(world_id, version['id'])
    assert not store.summary(world_id)['versions'][0]['verified']


@pytest.mark.parametrize('name', ['../世界.json', '..\\世界.json', 'x.metadata.json', 'x.txt'])
def test_restore_rejects_path_traversal(context, name):
    store, _, world_id = context
    with pytest.raises(backups.BackupError):
        store.restore(world_id, name)


def test_restore_refuses_changed_source_after_user_confirmation(context):
    store, source, world_id = context
    version = store.capture(world_id)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    world(source, marker=2)
    before = source.read_bytes()
    with pytest.raises(backups.BackupError, match='saved again'):
        store.restore(world_id, version['id'], digest)
    assert source.read_bytes() == before


def test_source_change_during_safety_backup_aborts_replacement(context, monkeypatch):
    store, source, world_id = context
    version = store.capture(world_id)
    original_write = backups._write
    changed = []
    def write(path, data):
        original_write(path, data)
        if Path(path).name == 'world_before.json':
            world(source, marker=7)
            changed.append(source.read_bytes())
    monkeypatch.setattr(backups, '_write', write)
    with pytest.raises(backups.BackupError, match='changed'):
        store.restore(world_id, version['id'])
    assert source.read_bytes() == changed[0]


def test_partial_corrupt_or_missing_integrity_never_replace_good_archive(context):
    store, source, world_id = context
    good = store.capture(world_id)
    before = Path(good['path']).read_bytes()
    valid = source.read_bytes()
    for payload in (b'{partial', valid.replace(b'"Hash":', b'"OtherHash":')):
        source.write_bytes(payload)
        with pytest.raises((backups.BackupError, ValueError)):
            store.capture(world_id)
    assert Path(good['path']).read_bytes() == before
    assert len(store.summary(world_id)['versions']) == 1


def test_independent_store_updates_merge_under_lock(context):
    store, source, world_id = context
    other = backups.BackupStore(store.root)
    second = source.with_name('second.json')
    snapshot = world(second, seed=313)
    other.register(snapshot)
    store.set_retention(world_id, 9)
    assert len(other.worlds()) == 2
    assert next(v for v in other.worlds() if v['world_id'] == world_id)['retention_days'] == 9


def test_cached_legacy_version_cannot_cross_world_seed(context):
    store, source, world_id = context
    version = store.capture(world_id)
    old = store.worlds()[0]
    store._inspect_file(Path(version['path']), old, force=False)
    snapshot = world(source, seed=44)
    new = store.register(snapshot)
    assert new['backup_dir'] != old['backup_dir']
    for force in (False, True):
        with pytest.raises(backups.BackupError, match='different world'):
            store._inspect_file(Path(version['path']), new, force=force)


def test_permanent_retention_never_reads_archive_contents(context, monkeypatch):
    store, _, world_id = context
    def unexpected(*_):
        pytest.fail('Permanent retention must not enumerate historical payloads')
    monkeypatch.setattr(store, '_versions', unexpected)
    assert store.prune(world_id)['deleted'] == []
    assert store.retention_preview(world_id, 0)['count'] == 0


def test_metadata_rechecked_immediately_before_expired_delete(context, monkeypatch):
    store, source, world_id = context
    version = store.capture(world_id)
    meta(version['path'], captured_at_utc=(datetime.now(UTC) - timedelta(days=40)).isoformat())
    world(source, marker=2)
    store.capture(world_id)
    store.set_retention(world_id, 1)
    preview = store._preview
    def replace_metadata(*args):
        result = preview(*args)
        meta(version['path'], sha256='0' * 64)
        return result
    monkeypatch.setattr(store, '_preview', replace_metadata)
    assert store.prune(world_id)['deleted'] == []
    assert Path(version['path']).exists()


@pytest.mark.parametrize('field,error', [('last_error', '備份失敗'), ('prune_error', '清理失敗')])
def test_summary_exposes_per_world_daemon_failure(context, field, error):
    store, _, world_id = context
    (store.root / 'daemon-status.json').write_text(json.dumps({
        'heartbeat_at_utc': datetime.now(UTC).isoformat(),
        'worlds': {world_id: {field: {'message': error}}}}), encoding='utf-8')
    status = store.summary(world_id)['status']
    assert status['state'] == 'error' and status['message'] == error


def test_restore_return_warns_if_completion_receipt_fails_after_replace(context, monkeypatch):
    store, source, world_id = context
    version = store.capture(world_id)
    old = source.read_bytes()
    world(source, marker=2)
    write = backups._write_json
    def fail_receipt(path, value):
        if Path(path).name == 'restoration_receipt.json' and value.get('status') == 'restored':
            raise OSError('synthetic disk error')
        write(path, value)
    monkeypatch.setattr(backups, '_write_json', fail_receipt)
    receipt = store.restore(world_id, version['id'])
    assert receipt['status'] == 'restored_warning' and receipt['warning']
    assert source.read_bytes() == old


def test_link_escaping_backup_directory_is_never_deleted(context, tmp_path):
    store, source, world_id = context
    store.capture(world_id)
    other = tmp_path / 'unrelated.json'
    other.write_bytes(source.read_bytes())
    directory = Path(store.worlds()[0]['backup_dir'])
    linked = directory / 'escape.json'
    try:
        linked.symlink_to(other)
    except OSError:
        pytest.skip('This Windows account cannot create symlinks')
    store.set_retention(world_id, 1)
    store.prune(world_id, datetime.now(UTC) + timedelta(days=100))
    assert other.exists() and linked.exists()
    assert not next(v for v in store.summary(world_id)['versions'] if v['id'] == linked.name)['verified']
