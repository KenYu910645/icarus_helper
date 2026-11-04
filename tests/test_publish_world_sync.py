"""Synthetic host publishing with an in-memory GitHub transport; no network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_saves import save_fixture
from tools.publish_world_sync import (
    GitHubCLI, INDEX_ASSET, MIN_UPLOAD_SECONDS, Publisher, PublishError,
    RELEASE_TAG, ReleaseNotFound, SaveChangedError,
)


class Clock:
    def __init__(self):
        self.value = 2_000_000_000

    def time(self):
        return self.value

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeGitHub:
    def __init__(self):
        self.exists = False
        self.immutable = False
        self.assets = {}
        self.uploads = []
        self.creations = 0
        self.fail_index = False
        self.fail_release = False
        self.deleted = []
        self.fail_delete = False
        self.next_id = 1

    def release(self):
        if self.fail_release:
            raise PublishError("連線中斷", "github_unavailable")
        if not self.exists:
            raise ReleaseNotFound("404")
        return {"tag_name": RELEASE_TAG, "draft": False, "prerelease": True,
                "immutable": self.immutable,
                "assets": [deepcopy(asset) for asset, _raw in self.assets.values()]}

    def create_release(self):
        self.creations += 1
        self.exists = True
        return self.release()

    def upload(self, path, *, replace=False):
        path = Path(path)
        if path.name == INDEX_ASSET and self.fail_index:
            self.fail_index = False
            raise PublishError("索引中斷", "github_unavailable")
        if path.name in self.assets and not replace:
            raise PublishError("duplicate")
        raw = path.read_bytes()
        if path.name in self.assets:
            ident = self.assets[path.name][0]["id"]
        else:
            ident = self.next_id
            self.next_id += 1
        asset = {"id": ident, "name": path.name, "state": "uploaded", "size": len(raw),
                 "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
        self.assets[path.name] = asset, raw
        self.uploads.append((path.name, replace))

    def asset_bytes(self, asset):
        return self.assets[asset["name"]][1]

    def delete_asset(self, asset):
        if self.fail_delete:
            raise PublishError("delete unavailable")
        assert self.assets[asset["name"]][0]["id"] == asset["id"]
        self.deleted.append(asset["name"])
        self.assets.pop(asset["name"])


def world(path, *, name="GuoGuoGaGa", owner="synthetic-secret", extra=""):
    data = save_fixture(path)
    data["ProspectInfo"].update(ProspectID=name, ClaimedAccountID=owner)
    data["Extra"] = extra
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "私人帳號" / "Prospects" / "GuoGuoGaGa.json"
    world(source)
    gh, clock = FakeGitHub(), Clock()
    publisher = Publisher(source, tmp_path / "state", gh=gh, clock=clock)
    return publisher, source, gh, clock


def test_valid_copy_uploads_raw_before_index_and_keeps_original(setup):
    publisher, source, gh, clock = setup
    original, stat = source.read_bytes(), source.stat()
    result = publisher.run_once()
    assert result["status"] == "published"
    assert gh.creations == 1
    assert gh.uploads[0][0] == "GuoGuoGaGa-" + hashlib.sha256(original).hexdigest() + ".json"
    assert gh.uploads[0][1] is False
    assert gh.uploads[1] == (INDEX_ASSET, True)
    index = json.loads(gh.assets[INDEX_ASSET][1])
    assert set(index) == {"schema_version", "world_id", "name", "map_name", "saved_at",
                          "published_at", "sha256", "bytes", "asset_name", "source_mtime_ns", "source_mtime"}
    assert index["bytes"] == len(original)
    assert gh.assets[index["asset_name"]][1] == original
    assert index["name"] == "GuoGuoGaGa" and index["map_name"] == "Prometheus"
    assert source.read_bytes() == original and source.stat().st_mtime_ns == stat.st_mtime_ns
    assert index["source_mtime_ns"] == stat.st_mtime_ns
    assert not list(publisher.state_dir.glob("verified-*"))
    status_text = (publisher.state_dir / "status.json").read_text(encoding="utf-8")
    assert "synthetic-secret" not in status_text and "私人帳號" not in status_text


def test_unchanged_save_is_not_uploaded_again(setup):
    publisher, source, gh, clock = setup
    publisher.run_once()
    clock.sleep(500)
    assert publisher.tick()["status"] == "unchanged"
    assert len(gh.uploads) == 2


def test_new_save_waits_for_two_rounds_and_minimum_interval(setup):
    publisher, source, gh, clock = setup
    publisher.run_once()
    world(source, extra="next version")
    assert publisher.tick()["status"] == "waiting_for_stable_save"
    assert publisher.tick()["status"] == "throttled"
    assert len(gh.uploads) == 2
    clock.sleep(MIN_UPLOAD_SECONDS)
    assert publisher.tick()["status"] == "published"
    assert len(gh.uploads) == 4


@pytest.mark.parametrize("changes,code", [
    ({"name": "OtherWorld"}, "world_not_approved"),
    ({"owner": ""}, "owner_missing"),
])
def test_only_approved_world_with_explicit_owner_is_published(setup, changes, code):
    publisher, source, gh, clock = setup
    world(source, **changes)
    with pytest.raises(PublishError) as error:
        publisher.run_once()
    assert error.value.code == code
    assert not gh.uploads and gh.creations == 0


@pytest.mark.parametrize("mutation", [
    lambda data: data["ProspectBlob"].update(Hash="0" * 40),
    lambda data: data["ProspectBlob"].update(UncompressedLength=1),
    lambda data: data["ProspectBlob"].pop("Hash"),
])
def test_corrupt_or_unverifiable_save_never_reaches_github(setup, mutation):
    publisher, source, gh, clock = setup
    data = json.loads(source.read_text(encoding="utf-8"))
    mutation(data)
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((ValueError, PublishError)):
        publisher.run_once()
    assert not gh.uploads and gh.creations == 0


def test_partial_json_is_rejected_and_previous_index_retained(setup):
    publisher, source, gh, clock = setup
    publisher.run_once()
    old_index = gh.assets[INDEX_ASSET][1]
    source.write_text('{"ProspectBlob":', encoding="utf-8")
    clock.sleep(130)
    with pytest.raises(ValueError):
        publisher.run_once()
    assert gh.assets[INDEX_ASSET][1] == old_index and len(gh.uploads) == 2


def test_source_binding_survives_restart_and_rejects_other_world(setup):
    publisher, source, gh, clock = setup
    publisher.run_once()
    world(source, owner="different-owner")
    clock.sleep(130)
    restarted = Publisher(source, publisher.state_dir, gh=gh, clock=clock)
    with pytest.raises(PublishError) as error:
        restarted.run_once()
    assert error.value.code == "binding_mismatch"
    assert len(gh.uploads) == 2


def test_cannot_reuse_state_for_different_source(setup, tmp_path):
    publisher, source, gh, clock = setup
    publisher.run_once()
    other = tmp_path / "different" / "GuoGuoGaGa.json"
    world(other)
    with pytest.raises(PublishError) as error:
        Publisher(other, publisher.state_dir, gh=gh, clock=clock).run_once()
    assert error.value.code == "binding_mismatch"


def test_interrupted_index_recovers_without_uploading_raw_twice(setup):
    publisher, source, gh, clock = setup
    gh.fail_index = True
    with pytest.raises(PublishError):
        publisher.run_once()
    assert len(gh.uploads) == 1
    assert "sha256" not in json.loads((publisher.state_dir / "state.json").read_text(encoding="utf-8"))
    restarted = Publisher(source, publisher.state_dir, gh=gh, clock=clock)
    assert restarted.run_once()["status"] == "published"
    assert len(gh.uploads) == 2 and gh.uploads[-1] == (INDEX_ASSET, True)


def test_existing_asset_without_digest_is_fully_hashed_on_resume(setup):
    publisher, source, gh, clock = setup
    gh.fail_index = True
    with pytest.raises(PublishError):
        publisher.run_once()
    raw_asset = next(iter(gh.assets.values()))[0]
    raw_asset.pop("digest")
    restarted = Publisher(source, publisher.state_dir, gh=gh, clock=clock)
    assert restarted.run_once()["status"] == "published"
    assert len(gh.uploads) == 2


def test_existing_asset_hash_conflict_does_not_publish_index(setup):
    publisher, source, gh, clock = setup
    gh.fail_index = True
    with pytest.raises(PublishError):
        publisher.run_once()
    raw_asset = next(iter(gh.assets.values()))[0]
    raw_asset["digest"] = "sha256:" + "0" * 64
    restarted = Publisher(source, publisher.state_dir, gh=gh, clock=clock)
    with pytest.raises(PublishError) as error:
        restarted.run_once()
    assert error.value.code == "asset_conflict" and INDEX_ASSET not in gh.assets


def test_immutable_release_cannot_receive_world_or_index(setup):
    publisher, source, gh, clock = setup
    gh.exists, gh.immutable = True, True
    with pytest.raises(PublishError) as error:
        publisher.run_once()
    assert error.value.code == "release_immutable" and not gh.uploads


def test_unavailable_github_retries_with_backoff(setup):
    publisher, source, gh, clock = setup
    gh.fail_release = True
    with pytest.raises(PublishError):
        publisher.run_once()
    gh.fail_release = False
    assert publisher.tick()["status"] == "retry_wait"
    clock.sleep(11)
    assert publisher.tick()["status"] == "published"


def test_second_process_cannot_publish_with_same_state(setup):
    publisher, source, gh, clock = setup
    second = Publisher(source, publisher.state_dir, gh=gh, clock=clock)
    with publisher.lock():
        with pytest.raises(OSError):
            second.tick()
    assert not gh.uploads


def test_release_creation_never_marks_data_as_latest(monkeypatch):
    commands = []
    def run(arguments, **kwargs):
        commands.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{}', stderr=b"")
    monkeypatch.setattr("tools.publish_world_sync.subprocess.run", run)
    client = GitHubCLI("owner/repo", RELEASE_TAG, executable="gh")
    client.create_release()
    arguments, kwargs = commands[0]
    assert "prerelease=true" in arguments and "make_latest=false" in arguments
    assert isinstance(arguments, list) and kwargs.get("shell") is None
    assert not any("token" in arg.lower() for arg in arguments)


def test_cli_failure_does_not_echo_private_stderr(monkeypatch):
    def run(arguments, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"token=private-secret account-id")
    monkeypatch.setattr("tools.publish_world_sync.subprocess.run", run)
    client = GitHubCLI("owner/repo", RELEASE_TAG, executable="gh")
    with pytest.raises(PublishError) as error:
        client.release()
    assert "private-secret" not in str(error.value) and "account-id" not in str(error.value)


def test_app_release_tag_is_rejected_before_network(setup):
    publisher, source, gh, clock = setup
    with pytest.raises(PublishError):
        Publisher(source, publisher.state_dir, tag="v0.2.1", gh=gh, clock=clock)
    assert not gh.uploads


def publish_versions(publisher, source, clock, count):
    for index in range(count):
        if index:
            world(source, extra=f"version-{index}")
            clock.sleep(130)
        assert publisher.run_once()["status"] == "published"


def test_retention_default_keeps_every_uploaded_version(setup):
    publisher, source, gh, clock = setup
    publish_versions(publisher, source, clock, 4)
    assert len(gh.assets) == 5 and gh.deleted == []


def test_retention_three_preserves_current_and_previous_two(setup):
    publisher, source, gh, clock = setup
    publisher.keep_versions = 3
    publish_versions(publisher, source, clock, 4)
    index = json.loads(gh.assets[INDEX_ASSET][1])
    assert index["asset_name"] in gh.assets
    assert len(gh.assets) == 4 and len(gh.deleted) == 1
    assert INDEX_ASSET not in gh.deleted
    state = json.loads((publisher.state_dir / "state.json").read_text(encoding="utf-8"))
    assert len(state["managed_assets"]) == 3
    assert {row["name"] for row in state["managed_assets"]} == set(gh.assets) - {INDEX_ASSET}


def test_retention_never_adopts_existing_raw_assets(setup):
    publisher, source, gh, clock = setup
    gh.exists = True
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    external = publisher.state_dir / f"GuoGuoGaGa-{digest}.json"
    external.write_bytes(source.read_bytes())
    gh.upload(external)
    publisher.keep_versions = 1
    assert publisher.run_once()["status"] == "published"
    assert not json.loads((publisher.state_dir / "state.json").read_text(encoding="utf-8")).get("managed_assets")
    world(source, extra="new")
    clock.sleep(130)
    assert publisher.run_once()["status"] == "published"
    assert external.name in gh.assets and gh.deleted == []


@pytest.mark.parametrize("conflict", ["id", "digest", "size"])
def test_retention_skips_modified_receipt_asset(setup, conflict):
    publisher, source, gh, clock = setup
    publisher.keep_versions = 1
    publisher.run_once()
    first_name = gh.uploads[0][0]
    asset = gh.assets[first_name][0]
    asset[conflict] = {"id": 999, "digest": "sha256:" + "0" * 64, "size": 1}[conflict]
    world(source, extra="new")
    clock.sleep(130)
    result = publisher.run_once()
    assert result["status"] == "published"
    assert result["warnings"] == ["history_cleanup_asset_conflict"]
    assert first_name in gh.assets and not gh.deleted


def test_retention_missing_digest_downloads_and_checks_complete_bytes(setup):
    publisher, source, gh, clock = setup
    publisher.keep_versions = 1
    publisher.run_once()
    first_name = gh.uploads[0][0]
    gh.assets[first_name][0].pop("digest")
    world(source, extra="new")
    clock.sleep(130)
    assert publisher.run_once()["status"] == "published"
    assert gh.deleted == [first_name]


def test_retention_failure_does_not_turn_publication_into_failure(setup):
    publisher, source, gh, clock = setup
    publisher.keep_versions = 1
    publisher.run_once()
    gh.fail_delete = True
    world(source, extra="new")
    clock.sleep(130)
    result = publisher.run_once()
    assert result["status"] == "published" and result["warnings"] == ["history_cleanup_failed"]
    state = json.loads((publisher.state_dir / "state.json").read_text(encoding="utf-8"))
    assert state["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads(gh.assets[INDEX_ASSET][1])["sha256"] == state["sha256"]


def test_retention_does_not_delete_other_attachment_names(setup):
    publisher, source, gh, clock = setup
    publisher.keep_versions = 1
    gh.exists = True
    unrelated = publisher.state_dir / "APP-installer.zip"
    unrelated.write_bytes(b"unrelated")
    gh.upload(unrelated)
    publish_versions(publisher, source, clock, 3)
    assert unrelated.name in gh.assets
    assert unrelated.name not in gh.deleted and INDEX_ASSET not in gh.deleted


def test_retention_refuses_negative_configuration(setup):
    publisher, source, gh, clock = setup
    with pytest.raises(PublishError):
        Publisher(source, publisher.state_dir, gh=gh, clock=clock, keep_versions=-1)


def test_source_change_between_observation_and_read_restarts_stability(setup, monkeypatch):
    publisher, source, gh, clock = setup
    from tools import publish_world_sync as module
    stable_reader = module._read_stable
    def changing(path):
        world(path, extra="changed just after second observation")
        return stable_reader(path)
    assert publisher.tick()["status"] == "waiting_for_stable_save"
    monkeypatch.setattr(module, "_read_stable", changing)
    with pytest.raises(SaveChangedError):
        publisher.tick()
    assert not gh.uploads and publisher._stable_rounds == 1
    monkeypatch.setattr(module, "_read_stable", stable_reader)
    clock.sleep(11)
    assert publisher.tick()["status"] == "published"


def test_unchanged_signature_skips_repeated_decode_but_rechecks_after_120_seconds(setup, monkeypatch):
    publisher, source, gh, clock = setup
    validator = publisher._validate_copy
    calls = []
    def validate(directory):
        calls.append(directory)
        return validator(directory)
    monkeypatch.setattr(publisher, "_validate_copy", validate)
    publisher.run_once()
    assert len(calls) == 1
    clock.sleep(5)
    assert publisher.tick()["status"] == "unchanged" and len(calls) == 1
    clock.sleep(MIN_UPLOAD_SECONDS)
    assert publisher.tick()["status"] == "unchanged" and len(calls) == 2
