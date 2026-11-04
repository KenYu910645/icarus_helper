"""Small isolated settings files; never use real home/AppData preferences."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
from pathlib import Path

import pytest

from icarus_helper import settings
from icarus_helper.settings import Settings


def base(ident, x=1):
    return {"id": ident, "name": ident, "x": x, "y": 2, "z": 3}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def isolated_default(tmp_path, monkeypatch):
    home, local = tmp_path / "home", tmp_path / "Local"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return home, local


def test_default_location_is_stable_across_launch_environments(isolated_default, monkeypatch):
    home, local = isolated_default
    first = Settings()
    first.world("one").update(bases=[base("home")], active_base="home")
    first.save()
    monkeypatch.setenv("LOCALAPPDATA", str(local / "Packages" / "different-process" / "LocalCache" / "Local"))
    reopened = Settings()
    assert reopened.path == home / ".icarus-helper" / "settings.json"
    assert reopened.world("one")["bases"] == [base("home")]
    assert settings.app_dir() != reopened.path.parent


def test_legacy_migration_preserves_sources_backups_conflicts_and_worlds(isolated_default):
    home, local = isolated_default
    normal = local / "IcarusHelper" / "settings.json"
    virtual = local / "Packages" / "OpenAI.Codex_test" / "LocalCache" / "Local" / "IcarusHelper" / "settings.json"
    first = {"selected_source": "normal-world.json", "worlds": {"real": {"bases": [base("same", 10)], "active_base": "same"}}}
    second = {"selected_source": "shared-map:prometheus-home", "worlds": {
        "real": {"bases": [base("same", 20), base("recovered")], "active_base": "recovered"},
        "shared-map:prometheus-home": {"bases": [base("shared-home")], "active_base": "shared-home"}}}
    write(normal, first)
    write(virtual, second)
    originals = {p: p.read_bytes() for p in (normal, virtual)}
    result = Settings()
    assert not result.error
    assert result.data["selected_source"] == "normal-world.json"
    assert result.world("real")["bases"] == [base("same", 10), base("recovered")]
    assert result.world("real")["active_base"] == "same"
    assert result.world("shared-map:prometheus-home")["bases"] == [base("shared-home")]
    assert result.data["_migration"]["conflicts"]
    for path, raw in originals.items():
        assert path.read_bytes() == raw
        backup = home / ".icarus-helper" / "legacy-backups" / f"settings-{hashlib.sha256(raw).hexdigest()}.json"
        assert backup.read_bytes() == raw
    result.save()
    assert Settings().data == result.data


def test_migration_recovers_valid_active_base_and_never_reimports_deleted_home(isolated_default):
    _, local = isolated_default
    write(local / "IcarusHelper/settings.json", {"worlds": {"real": {"bases": [], "active_base": ""}}})
    write(local / "Packages/OpenAI.Codex_test/LocalCache/Local/IcarusHelper/settings.json",
          {"worlds": {"real": {"bases": [base("recover")], "active_base": "recover"}}})
    result = Settings()
    assert result.world("real")["active_base"] == "recover"
    result.world("real").update(bases=[], active_base="")
    result.save()
    assert Settings().world("real")["bases"] == []


def test_migration_uses_exact_backed_up_bytes_if_legacy_changes_during_read(isolated_default, monkeypatch):
    home, local = isolated_default
    legacy = local / "IcarusHelper/settings.json"
    original = {"worlds": {"real": {"bases": [base("original", 10)], "active_base": "original"}}}
    changed = {"worlds": {"real": {"bases": [base("changed", 20)], "active_base": "changed"}}}
    write(legacy, original)
    raw_before = legacy.read_bytes()
    read = Path.read_bytes
    calls = []
    def racing_read(path):
        raw = read(path)
        if path == legacy:
            calls.append(path)
            write(legacy, changed)
        return raw
    monkeypatch.setattr(Path, "read_bytes", racing_read)
    result = Settings()
    assert len(calls) == 1
    assert result.world("real")["bases"] == [base("original", 10)]
    assert result.world("real")["active_base"] == "original"
    backup = home / ".icarus-helper/legacy-backups" / f"settings-{hashlib.sha256(raw_before).hexdigest()}.json"
    assert backup.read_bytes() == raw_before


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b'{"worlds":[]}', b'{"worlds":{"w":{"bases":[{"id":"a","x":"bad"}]}}}'])
def test_invalid_existing_canonical_is_never_overwritten_or_replaced_from_legacy(isolated_default, raw):
    home, local = isolated_default
    target = home / ".icarus-helper/settings.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    write(local / "IcarusHelper/settings.json", {"worlds": {"w": {"bases": [base("recover")]}}})
    result = Settings()
    assert result.error
    with pytest.raises(OSError):
        result.save()
    assert target.read_bytes() == raw


def test_invalid_legacy_is_backed_up_without_creating_empty_canonical(isolated_default):
    home, local = isolated_default
    legacy = local / "IcarusHelper/settings.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'{"worlds":')
    result = Settings()
    assert result.error and not result.path.exists()
    assert list((home / ".icarus-helper/legacy-backups").glob("*.json"))[0].read_bytes() == legacy.read_bytes()


def test_explicit_path_does_not_migrate_or_create_default_folder(isolated_default, tmp_path):
    home, local = isolated_default
    write(local / "IcarusHelper/settings.json", {"worlds": {"private": {"bases": [base("private")]}}})
    explicit = tmp_path / "isolated/settings.json"
    result = Settings(explicit)
    assert result.data == {}
    result.data["test"] = True
    result.save()
    assert Settings(explicit).data == {"test": True}
    assert not (home / ".icarus-helper").exists()


def test_existing_world_without_optional_collections_can_add_and_persist_home(tmp_path):
    path = tmp_path / "settings.json"
    write(path, {"worlds": {"world": {"inventory_radius": 100}}})
    result = Settings(path)
    result.world("world")["bases"].append(base("home"))
    result.save()
    assert Settings(path).world("world")["bases"] == [base("home")]
    assert result.world("world")["checklists"] == {}


def test_stale_writer_unrelated_change_does_not_erase_new_base(tmp_path):
    path = tmp_path / "settings.json"
    first, stale = Settings(path), Settings(path)
    first.world("world").update(bases=[base("new-home")], active_base="new-home")
    first.save()
    stale.data["selected_source"] = "shared-map:prometheus-home"
    stale.world("world")  # A read-like setdefault must not clear remote bases.
    stale.save()
    assert Settings(path).world("world")["bases"] == [base("new-home")]


def test_parallel_world_and_base_additions_and_field_edits_are_merged(tmp_path):
    path = tmp_path / "settings.json"
    initial = Settings(path)
    initial.world("world").update(bases=[base("home")], active_base="home")
    initial.save()
    first, second = Settings(path), Settings(path)
    first.world("world")["bases"].append(base("a"))
    first.world("world")["bases"][0]["x"] = 100
    first.world("other")["bases"].append(base("other"))
    first.save()
    second.world("world")["bases"].append(base("b"))
    second.world("world")["bases"][0]["name"] = "renamed home"
    second.save()
    final = Settings(path)
    assert {b["id"] for b in final.world("world")["bases"]} == {"home", "a", "b"}
    assert final.world("world")["bases"][0]["x"] == 100
    assert final.world("world")["bases"][0]["name"] == "renamed home"
    assert final.world("other")["bases"] == [base("other")]


def test_explicit_deletion_is_kept_and_stale_unrelated_writer_cannot_resurrect_it(tmp_path):
    path = tmp_path / "settings.json"
    initial = Settings(path)
    initial.world("world").update(bases=[base("home"), base("outpost")], active_base="home")
    initial.save()
    deleting, stale = Settings(path), Settings(path)
    deleting.world("world").update(bases=[base("outpost")], active_base="outpost")
    deleting.save()
    stale.data["map_display"] = {"layers": {"deep": True}}
    stale.save()
    assert Settings(path).world("world")["bases"] == [base("outpost")]
    assert Settings(path).world("world")["active_base"] == "outpost"


def test_disk_corruption_after_open_does_not_get_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    result = Settings(path)
    result.data["selected_source"] = "test"
    path.write_bytes(b"broken external change")
    with pytest.raises(OSError):
        result.save()
    assert path.read_bytes() == b"broken external change"


def test_edit_of_base_deleted_by_another_writer_is_reported_without_resurrection(tmp_path):
    path = tmp_path / "settings.json"
    initial = Settings(path)
    initial.world("world").update(bases=[base("home")], active_base="home")
    initial.save()
    deleting, editing = Settings(path), Settings(path)
    deleting.world("world").update(bases=[], active_base="")
    deleting.save()
    previous = path.read_bytes()
    editing.world("world")["bases"][0]["name"] = "stale rename"
    with pytest.raises(OSError, match="deleted"):
        editing.save()
    assert path.read_bytes() == previous
    assert Settings(path).world("world")["bases"] == []


def _concurrent_save(path, ident, ready, release):
    writer = Settings(Path(path))
    writer.world("world")["bases"].append(base(ident))
    ready.put(ident)
    release.wait(8)
    writer.save()


def test_separate_processes_save_bases_without_temp_or_replace_collisions(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Event()
    path = tmp_path / "settings.json"
    writers = [context.Process(target=_concurrent_save, args=(str(path), ident, ready, release)) for ident in ("a", "b")]
    try:
        for writer in writers:
            writer.start()
        assert {ready.get(timeout=8), ready.get(timeout=8)} == {"a", "b"}
        release.set()
        for writer in writers:
            writer.join(8)
            assert writer.exitcode == 0
        assert {b["id"] for b in Settings(path).world("world")["bases"]} == {"a", "b"}
        assert not list(tmp_path.glob(".settings.json.*.tmp"))
    finally:
        release.set()
        for writer in writers:
            if writer.is_alive():
                writer.terminate()
                writer.join(3)
