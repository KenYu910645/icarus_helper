"""Remote release checks use synthetic saves and injected transport only."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import urllib.request

import pytest

from icarus_helper import release_sync as sync
from icarus_helper.saves import load_snapshot
from test_saves import save_fixture


UTC = timezone.utc
SAVED = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CHECKED = SAVED + timedelta(minutes=3)


def package(tmp_path, *, marker=1, seed=919, claim="synthetic-host", published=None):
    source = tmp_path / "主機來源" / "synthetic-host" / "Prospects" / "GuoGuoGaGa.json"
    envelope = save_fixture(source, records=[
        ("/Script/Icarus.GameModeStateRecorderComponent", {"GameModeRecord": {"GameStateSeed": seed}, "Marker": marker})])
    envelope["ProspectInfo"]["ProspectID"] = "GuoGuoGaGa"
    if claim is None:
        envelope["ProspectInfo"].pop("ClaimedAccountID")
    else:
        envelope["ProspectInfo"]["ClaimedAccountID"] = claim
    source.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    mtime_ns = round(SAVED.timestamp() * 1_000_000_000) + 123400
    os.utime(source, ns=(mtime_ns, mtime_ns))
    snapshot = load_snapshot(source)
    raw = source.read_bytes()
    index = {"schema_version": 1, "world_id": snapshot.world_id, "name": snapshot.name, "map_name": snapshot.map_name,
             "saved_at": snapshot.saved_at.astimezone(UTC).isoformat(), "published_at": (published or SAVED + timedelta(minutes=1)).isoformat(),
             "sha256": snapshot.sha256, "bytes": len(raw), "asset_name": f"GuoGuoGaGa-{snapshot.sha256}.json",
             "source_mtime_ns": mtime_ns}
    release = {"tag_name": sync.RELEASE_TAG, "draft": False, "assets": []}
    data = {sync.RELEASE_API: b"", sync.DOWNLOAD_BASE + index["asset_name"]: raw}

    def rebuild():
        encoded = json.dumps(index, ensure_ascii=False).encode("utf-8")
        data[sync.DOWNLOAD_BASE + sync.INDEX_ASSET] = encoded
        release["assets"] = [{"name": sync.INDEX_ASSET, "state": "uploaded", "size": len(encoded), "browser_download_url": sync.DOWNLOAD_BASE + sync.INDEX_ASSET},
                             {"name": index["asset_name"], "state": "uploaded", "size": index["bytes"], "browser_download_url": sync.DOWNLOAD_BASE + index["asset_name"]}]
        data[sync.RELEASE_API] = json.dumps(release).encode("utf-8")

    rebuild()
    calls = []

    def transport(url, *, max_bytes):
        calls.append((url, max_bytes))
        return data[url.partition("?")[0]]

    return {"index": index, "release": release, "data": data, "source": source,
            "raw": raw, "snapshot": snapshot, "transport": transport, "calls": calls, "rebuild": rebuild}


def offline(*_args, **_kwargs):
    raise OSError("network unavailable")


def test_complete_original_bytes_identity_timestamp_and_no_host_mutation(tmp_path):
    network = package(tmp_path)
    source = network["source"]
    before = source.read_bytes(), source.stat().st_mtime_ns
    snapshot = sync.load_remote_snapshot(tmp_path / "朋友快取", transport=network["transport"], now=CHECKED)
    assert snapshot.is_remote and not snapshot.remote_stale
    assert snapshot.remote_repository == sync.DEFAULT_REPOSITORY and snapshot.remote_tag == sync.RELEASE_TAG
    assert snapshot.remote_source == sync.REMOTE_SOURCE and snapshot.remote_checked_at == CHECKED
    assert snapshot.source_path.parent == tmp_path / "朋友快取"
    assert snapshot.source_path.read_bytes() == network["raw"]
    assert snapshot.source_mtime_ns == before[1] == snapshot.source_path.stat().st_mtime_ns
    assert snapshot.saved_at == network["snapshot"].saved_at
    assert snapshot.sha256 == network["snapshot"].sha256 and snapshot.world_id == network["snapshot"].world_id
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert sync.is_remote_cache_path(snapshot.source_path)
    assert not sync.is_remote_cache_path(source)


def test_offline_reloads_only_verified_last_good_and_preserves_times(tmp_path):
    network = package(tmp_path)
    cache = tmp_path / "cache"
    first = sync.load_remote_snapshot(cache, transport=network["transport"], now=CHECKED)
    disk_before = {p.name: p.read_bytes() for p in cache.iterdir() if p.is_file()}
    last = sync.load_remote_snapshot(cache, transport=offline, now=CHECKED + timedelta(minutes=2))
    assert last.remote_stale and "stale" in last.remote_status
    assert last.remote_checked_at > first.remote_checked_at
    assert last.saved_at == first.saved_at and last.source_mtime_ns == first.source_mtime_ns
    assert last.sha256 == first.sha256 and last.source_path == first.source_path
    assert {p.name: p.read_bytes() for p in cache.iterdir() if p.is_file()} == disk_before


def test_first_offline_start_raises_and_never_creates_fake_world(tmp_path):
    cache = tmp_path / "cache"
    with pytest.raises(sync.RemoteSyncError):
        sync.load_remote_snapshot(cache, transport=offline)
    assert not (cache / sync.RECEIPT_NAME).exists()
    assert not list(cache.glob("GuoGuoGaGa-*.json"))


def test_same_verified_index_skips_redundant_full_world_download(tmp_path):
    network = package(tmp_path)
    cache = tmp_path / "cache"
    sync.load_remote_snapshot(cache, transport=network["transport"])
    network["calls"].clear()
    second = sync.load_remote_snapshot(cache, transport=network["transport"])
    assert not second.remote_stale
    assert [url.partition("?")[0] for url, _ in network["calls"]] == [sync.RELEASE_API, sync.DOWNLOAD_BASE + sync.INDEX_ASSET]
    assert network["calls"][1][0].startswith(sync.DOWNLOAD_BASE + sync.INDEX_ASSET + "?check=")


def test_index_cache_bust_is_only_utc_check_time_and_changes_each_poll(tmp_path):
    network = package(tmp_path)
    cache = tmp_path / "cache"
    sync.load_remote_snapshot(cache, transport=network["transport"], now=CHECKED)
    first_index_url = sync.DOWNLOAD_BASE + sync.INDEX_ASSET + f"?check={int(CHECKED.timestamp())}"
    assert (first_index_url, sync.MAX_INDEX_BYTES) in network["calls"]
    assert sync._safe_url(first_index_url) == first_index_url
    assert all("?" not in url for url, _ in network["calls"] if sync.INDEX_ASSET not in url)
    network["calls"].clear()
    later = (CHECKED + timedelta(seconds=120)).astimezone(timezone(timedelta(hours=8)))
    sync.load_remote_snapshot(cache, transport=network["transport"], now=later)
    second_index_url = sync.DOWNLOAD_BASE + sync.INDEX_ASSET + f"?check={int(later.timestamp())}"
    assert second_index_url != first_index_url
    assert (second_index_url, sync.MAX_INDEX_BYTES) in network["calls"]


@pytest.mark.parametrize("failure", ["partial", "hash", "world", "name", "map", "saved_at", "schema", "size", "path", "uploading", "duplicate", "url", "tag", "draft"])
def test_invalid_remote_update_keeps_last_good(tmp_path, failure):
    good = package(tmp_path)
    cache = tmp_path / "cache"
    first = sync.load_remote_snapshot(cache, transport=good["transport"])
    new = package(tmp_path, marker=2, published=SAVED + timedelta(minutes=2))
    index = new["index"]
    if failure == "world":
        index["world_id"] = "0" * 24
    elif failure == "name":
        index["name"] = "another world"
    elif failure == "map":
        index["map_name"] = "Olympus"
    elif failure == "saved_at":
        index["saved_at"] = (SAVED + timedelta(hours=1)).isoformat()
    elif failure == "schema":
        index["schema_version"] = True
    elif failure == "size":
        index["bytes"] = sync.MAX_SAVE_BYTES + 1
    elif failure == "path":
        index["asset_name"] = "../GuoGuoGaGa.json"
    new["rebuild"]()
    release = new["release"]
    if failure == "partial":
        new["data"][sync.DOWNLOAD_BASE + index["asset_name"]] = new["raw"][:100]
    elif failure == "hash":
        new["data"][sync.DOWNLOAD_BASE + index["asset_name"]] = b" " + new["raw"][1:]
    elif failure == "uploading":
        release["assets"][1]["state"] = "new"
    elif failure == "duplicate":
        release["assets"].append(deepcopy(release["assets"][1]))
    elif failure == "url":
        release["assets"][1]["browser_download_url"] = "https://evil.example/world.json"
    elif failure == "tag":
        release["tag_name"] = "v0.2.1"
    elif failure == "draft":
        release["draft"] = True
    new["data"][sync.RELEASE_API] = json.dumps(release).encode("utf-8")
    last = sync.load_remote_snapshot(cache, transport=new["transport"])
    assert last.remote_stale and last.sha256 == first.sha256
    assert json.loads((cache / sync.RECEIPT_NAME).read_bytes())["sha256"] == first.sha256
    assert not any(p.name.startswith(".candidate") for p in cache.iterdir())
    assert first.source_path.read_bytes() == good["raw"]


@pytest.mark.parametrize("claim", [None, "", " ", 123, False])
def test_missing_or_nonstring_claimed_account_is_rejected(tmp_path, claim):
    network = package(tmp_path, claim=claim)
    with pytest.raises(sync.RemoteSyncError, match="host identity"):
        sync.load_remote_snapshot(tmp_path / "cache", transport=network["transport"])
    assert not (tmp_path / "cache" / sync.RECEIPT_NAME).exists()


def test_different_world_even_with_matching_new_index_is_not_silently_adopted(tmp_path):
    first = package(tmp_path)
    cache = tmp_path / "cache"
    original = sync.load_remote_snapshot(cache, transport=first["transport"])
    different = package(tmp_path, seed=920)
    last = sync.load_remote_snapshot(cache, transport=different["transport"])
    assert last.remote_stale and last.world_id == original.world_id


def test_initial_index_world_must_match_decoded_world(tmp_path):
    network = package(tmp_path)
    network["index"]["world_id"] = "0" * 24
    network["rebuild"]()
    with pytest.raises(sync.RemoteSyncError, match="identity"):
        sync.load_remote_snapshot(tmp_path / "cache", transport=network["transport"])


def test_hashed_but_corrupt_envelope_is_not_accepted(tmp_path):
    network = package(tmp_path)
    raw = b'{"ProspectInfo":{}}'
    sha = hashlib.sha256(raw).hexdigest()
    network["index"].update(sha256=sha, bytes=len(raw), asset_name=f"GuoGuoGaGa-{sha}.json")
    network["data"][sync.DOWNLOAD_BASE + network["index"]["asset_name"]] = raw
    network["rebuild"]()
    with pytest.raises(sync.RemoteSyncError):
        sync.load_remote_snapshot(tmp_path / "cache", transport=network["transport"])


@pytest.mark.parametrize("field", ["Hash", "UncompressedLength"])
def test_complete_envelope_integrity_fields_are_mandatory(tmp_path, field):
    network = package(tmp_path)
    envelope = json.loads(network["raw"])
    envelope["ProspectBlob"].pop(field)
    raw = json.dumps(envelope).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    network["index"].update(sha256=sha, bytes=len(raw), asset_name=f"GuoGuoGaGa-{sha}.json")
    network["data"][sync.DOWNLOAD_BASE + network["index"]["asset_name"]] = raw
    network["rebuild"]()
    with pytest.raises(sync.RemoteSyncError, match="package"):
        sync.load_remote_snapshot(tmp_path / "cache", transport=network["transport"])


def test_corrupt_cached_world_cannot_be_used_as_offline_result(tmp_path):
    network = package(tmp_path)
    cache = tmp_path / "cache"
    first = sync.load_remote_snapshot(cache, transport=network["transport"])
    first.source_path.write_bytes(b"corrupt")
    with pytest.raises(sync.RemoteSyncError):
        sync.load_remote_snapshot(cache, transport=offline)


def test_schema_without_optional_mtime_uses_original_saved_time(tmp_path):
    network = package(tmp_path)
    network["index"].pop("source_mtime_ns")
    network["rebuild"]()
    snapshot = sync.load_remote_snapshot(tmp_path / "cache", transport=network["transport"])
    assert abs((snapshot.saved_at - network["snapshot"].saved_at).total_seconds()) < .001
    assert abs(snapshot.source_mtime - SAVED.timestamp()) < .001


def test_older_release_cannot_roll_cache_back(tmp_path):
    current = package(tmp_path, marker=2, published=SAVED + timedelta(minutes=2))
    cache = tmp_path / "cache"
    original = sync.load_remote_snapshot(cache, transport=current["transport"])
    old = package(tmp_path, marker=1, published=SAVED + timedelta(minutes=1))
    result = sync.load_remote_snapshot(cache, transport=old["transport"])
    assert result.remote_stale and result.sha256 == original.sha256


@pytest.mark.parametrize("url", [
    "http://api.github.com/repos/KenYu910645/icarus_helper/releases/tags/world-sync-guoguogaga",
    "https://api.github.com/repos/other/icarus_helper/releases/tags/world-sync-guoguogaga",
    "https://github.com/KenYu910645/icarus_helper/releases/download/v0.2.1/world-index.json",
    sync.DOWNLOAD_BASE + "../world-index.json", sync.DOWNLOAD_BASE + "%2e%2e/world-index.json",
    sync.DOWNLOAD_BASE + "world-index.json?token=anything", sync.RELEASE_API + "#fragment",
    sync.DOWNLOAD_BASE + "world-index.json?check=1&token=anything", sync.DOWNLOAD_BASE + "world-index.json?check=",
    sync.DOWNLOAD_BASE + "world-index.json?check=-1", sync.DOWNLOAD_BASE + "world-index.json?check=1.0",
    sync.DOWNLOAD_BASE + "world-index.json?check=%31", sync.RELEASE_API + "?check=123",
    sync.DOWNLOAD_BASE + "GuoGuoGaGa-" + "0" * 64 + ".json?check=123",
    "https://user:pass@api.github.com/repos/KenYu910645/icarus_helper/releases/tags/world-sync-guoguogaga",
    "https://release-assets.githubusercontent.com.evil.example/file",
    "https://evil.example/world.json", "file:///world.json",
])
def test_initial_url_is_restricted_even_with_injected_transport(url):
    called = []
    with pytest.raises(sync.RemoteSyncError):
        sync._get(lambda *args, **kwargs: called.append(args), url, 100)
    assert not called


def test_redirects_allow_only_official_asset_hosts():
    allowed = "https://release-assets.githubusercontent.com/github-production-release-asset-1/file?signature=synthetic"
    handler = sync._RestrictedRedirect()
    request = urllib.request.Request(sync.DOWNLOAD_BASE + sync.INDEX_ASSET)
    redirected = handler.redirect_request(request, None, 302, "found", {}, allowed)
    assert redirected.full_url == allowed
    for url in ("https://evil.example/file", "http://release-assets.githubusercontent.com/file", "https://raw.githubusercontent.com/other/repo/main/file", "https://github.com/other/repo/releases/download/tag/file"):
        with pytest.raises(sync.RemoteSyncError):
            handler.redirect_request(request, None, 302, "found", {}, url)


@pytest.mark.parametrize("directory", ["PlayerData", "Prospects", "playerdata"])
def test_game_directories_are_rejected_before_any_write(tmp_path, directory):
    target = tmp_path / directory / "cache"
    with pytest.raises(sync.RemoteSyncError):
        sync.load_remote_snapshot(target, transport=offline)
    assert not target.exists()


def test_unmarked_nonempty_cache_and_foreign_marker_are_rejected(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "settings.json"
    sentinel.write_bytes(b"do not touch")
    with pytest.raises(sync.RemoteSyncError):
        sync.load_remote_snapshot(target, transport=offline)
    assert sentinel.read_bytes() == b"do not touch"
    (target / sync.MARKER_NAME).write_text('{}')
    with pytest.raises(sync.RemoteSyncError, match="marker"):
        sync.load_remote_snapshot(target, transport=offline)


def test_recognition_of_default_cache_has_no_side_effects(tmp_path, monkeypatch):
    local = tmp_path / "empty-local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = local / "IcarusHelper" / "remote-worlds" / "guoguogaga" / "anything.json"
    assert sync.is_remote_cache_path(expected)
    assert not sync.is_remote_cache_path(tmp_path / "local-world.json")
    assert not local.exists()


def test_linked_cache_is_rejected_without_touching_target(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Windows 無建立符號連結權限")
    with pytest.raises(sync.RemoteSyncError, match="symbolic links"):
        sync.load_remote_snapshot(link, transport=offline)
    assert list(actual.iterdir()) == []


def test_duplicate_index_keys_and_oversized_transport_are_rejected(tmp_path):
    network = package(tmp_path)
    network["data"][sync.RELEASE_API] = b'{"tag_name":"a","tag_name":"b"}'
    with pytest.raises(sync.RemoteSyncError):
        sync.load_remote_snapshot(tmp_path / "cache", transport=network["transport"])
    with pytest.raises(sync.RemoteSyncError):
        sync._get(lambda *_args, **_kwargs: bytes(101), sync.RELEASE_API, 100)


def test_http_reader_rejects_oversized_or_partial_content_and_untrusted_final_url(monkeypatch):
    class Response(io.BytesIO):
        headers = {"Content-Length": "5"}

        def geturl(self):
            return sync.DOWNLOAD_BASE + sync.INDEX_ASSET

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Cache-control") == "no-cache"
            assert request.get_header("Pragma") == "no-cache"
            return Response(b"123")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(sync.RemoteSyncError):
        sync.http_get(sync.DOWNLOAD_BASE + sync.INDEX_ASSET, max_bytes=10)
    with pytest.raises(sync.RemoteSyncError):
        sync.http_get(sync.DOWNLOAD_BASE + sync.INDEX_ASSET, max_bytes=4)
    monkeypatch.setattr(Response, "geturl", lambda _: "https://evil.example/anything")
    with pytest.raises(sync.RemoteSyncError):
        sync.http_get(sync.DOWNLOAD_BASE + sync.INDEX_ASSET, max_bytes=10)


def test_publisher_output_is_consumed_end_to_end_without_network(tmp_path):
    from test_publish_world_sync import Clock, FakeGitHub, world
    from tools.publish_world_sync import MIN_UPLOAD_SECONDS, Publisher

    source = tmp_path / "host" / "Prospects" / "GuoGuoGaGa.json"
    world(source)
    original, original_mtime = source.read_bytes(), source.stat().st_mtime_ns
    gh, clock = FakeGitHub(), Clock()
    publisher = Publisher(source, tmp_path / "publisher", gh=gh, clock=clock)
    assert publisher.run_once()["status"] == "published"

    def transport(url, *, max_bytes):
        url = url.partition("?")[0]
        if url == sync.RELEASE_API:
            release = gh.release()
            for asset in release["assets"]:
                asset["browser_download_url"] = sync.DOWNLOAD_BASE + asset["name"]
            return json.dumps(release).encode("utf-8")
        return gh.assets[url.removeprefix(sync.DOWNLOAD_BASE)][1]

    cache = tmp_path / "friend-cache"
    friend = sync.load_remote_snapshot(cache, transport=transport)
    assert friend.source_path.read_bytes() == original
    assert friend.source_mtime_ns == original_mtime
    assert friend.world_id == load_snapshot(source).world_id
    assert not friend.remote_stale
    world(source, extra="second save")
    clock.value += MIN_UPLOAD_SECONDS + 1
    assert publisher.run_once()["status"] == "published"
    updated = sync.load_remote_snapshot(cache, transport=transport)
    assert updated.world_id == friend.world_id
    assert updated.sha256 != friend.sha256 and updated.source_path.read_bytes() == source.read_bytes()
    assert not updated.remote_stale
