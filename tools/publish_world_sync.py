"""Publish the explicitly approved GuoGuoGaGa world to its public data release.

Only complete, verified copies are uploaded. This host-side tool uses the
current user's GitHub CLI authentication; it never reads or stores a token.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from icarus_helper.saves import SaveChangedError, SaveError, _json_bytes, _read_stable, load_snapshot
from icarus_helper.settings import _file_lock

DEFAULT_REPOSITORY = "KenYu910645/icarus_helper"
RELEASE_TAG = "world-sync-guoguogaga"
INDEX_ASSET = "world-index.json"
POLL_SECONDS = 5
MIN_UPLOAD_SECONDS = 120


class PublishError(RuntimeError):
    def __init__(self, message, code="publish_failed"):
        super().__init__(message)
        self.code = code


class ReleaseNotFound(PublishError):
    pass


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class GitHubCLI:
    """Small injected transport; all argument lists avoid a command shell."""

    def __init__(self, repository, tag, executable=None):
        self.repository, self.tag = repository, tag
        self.executable = executable or shutil.which("gh")
        if not self.executable and os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "GitHub CLI/gh.exe"
            if candidate.is_file():
                self.executable = str(candidate)
        if not self.executable:
            raise PublishError("GitHub CLI is not installed on this host.", "github_cli_missing")

    def _run(self, arguments, *, binary=False):
        try:
            result = subprocess.run([self.executable, *arguments], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=180, check=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PublishError("GitHub connection or command timed out; it will be retried later.", "github_unavailable") from exc
        if result.returncode:
            # Never echo raw CLI output: authentication errors may contain
            # account or environment details unrelated to the approved world.
            if b"HTTP 404" in result.stderr:
                raise ReleaseNotFound("The world data release was not found.", "release_not_found")
            raise PublishError(f"GitHub operation failed (exit code {result.returncode}).", "github_failed")
        if binary:
            return result.stdout
        try:
            return json.loads(result.stdout)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PublishError("The GitHub response format is unrecognized.", "github_response_invalid") from exc

    def release(self):
        return self._run(["api", f"repos/{self.repository}/releases/tags/{self.tag}"])

    def create_release(self):
        return self._run(["api", "--method", "POST", f"repos/{self.repository}/releases",
                          "-f", f"tag_name={self.tag}", "-f", "name=GuoGuoGaGa World Sync Data",
                          "-f", "body=Complete world save explicitly approved for public distribution by the user. Helper automatically reads the latest valid version; this is world data, not an application package.",
                          "-F", "prerelease=true", "-f", "make_latest=false"])

    def upload(self, path, *, replace=False):
        arguments = ["release", "upload", self.tag, str(path), "--repo", self.repository]
        if replace:
            arguments.append("--clobber")
        self._run(arguments, binary=True)

    def asset_bytes(self, asset):
        ident = asset.get("id")
        if isinstance(ident, bool) or not isinstance(ident, int) or ident < 1:
            raise PublishError("Invalid remote asset identifier.", "asset_invalid")
        return self._run(["api", f"repos/{self.repository}/releases/assets/{ident}",
                          "-H", "Accept: application/octet-stream"], binary=True)

    def delete_asset(self, asset):
        ident = asset.get("id")
        if isinstance(ident, bool) or not isinstance(ident, int) or ident < 1:
            raise PublishError("Invalid remote asset identifier.", "asset_invalid")
        self._run(["api", "--method", "DELETE", f"repos/{self.repository}/releases/assets/{ident}"], binary=True)


class Publisher:
    def __init__(self, source, state_dir=None, repository=DEFAULT_REPOSITORY,
                 tag=RELEASE_TAG, *, gh=None, clock=time, keep_versions=0):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise PublishError("Invalid GitHub repository name.", "repository_invalid")
        if not re.fullmatch(r"world-sync-[a-z0-9-]+", tag):
            raise PublishError("World data must use a separate world-sync release tag.", "tag_invalid")
        if isinstance(keep_versions, bool) or not isinstance(keep_versions, int) or keep_versions < 0:
            raise PublishError("The retained sync version count must be zero or a positive integer.", "retention_invalid")
        self.source = Path(source).resolve()
        self.state_dir = Path(state_dir or Path.home() / ".icarus-helper/world-publisher").resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.repository, self.tag, self.clock = repository, tag, clock
        self.keep_versions = keep_versions
        self.gh = gh
        self._signature = None
        self._stable_rounds = 0
        self._next_attempt = 0.0
        self._failures = 0
        self._locked = False
        self._verified_signature = None
        self._verified_digest = ""
        self._verified_world = ""
        self._verified_at = 0.0

    @contextmanager
    def lock(self):
        if self._locked:
            yield
            return
        with _file_lock(self.state_dir / "publisher", timeout=0.15):
            self._locked = True
            try:
                yield
            finally:
                self._locked = False

    def _state(self):
        path = self.state_dir / "state.json"
        if not path.exists():
            return {"schema_version": 1}
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError()
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError()
            for key, expected in (("source", str(self.source)), ("repository", self.repository), ("tag", self.tag)):
                if key in data and data[key] != expected:
                    raise PublishError("The sync source or release target differs from its registration; upload rejected.", "binding_mismatch")
            return data
        except (OSError, UnicodeError, ValueError) as exc:
            raise PublishError("Sync registration is corrupt; original settings were preserved and uploads stopped.", "state_invalid") from exc

    def _status(self, code, *, sha256="", world_id="", **extra):
        result = {"schema_version": 1, "checked_at": _utc(self.clock.time()),
                  "status": code, "sha256": sha256, "world_id": world_id, **extra}
        _write_json(self.state_dir / "status.json", result)
        return result

    def _validate_copy(self, directory):
        raw, stat = _read_stable(self.source)
        actual_signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if actual_signature != self._signature:
            self._signature, self._stable_rounds = actual_signature, 1
            raise SaveChangedError("The source changed after stability checks; waiting again for a complete save.")
        envelope = _json_bytes(raw)
        info, blob = envelope.get("ProspectInfo"), envelope.get("ProspectBlob")
        if not isinstance(info, dict) or not str(info.get("ClaimedAccountID") or "").strip():
            raise PublishError("The world has no reliable host identity; publication rejected.", "owner_missing")
        if not isinstance(blob, dict) or not re.fullmatch(r"[0-9a-fA-F]{40}", str(blob.get("Hash", ""))):
            raise PublishError("The world has no integrity hash; publication rejected.", "integrity_missing")
        if isinstance(blob.get("UncompressedLength"), bool) or not isinstance(blob.get("UncompressedLength"), int):
            raise PublishError("The world has no reliable package length; publication rejected.", "integrity_missing")
        digest = hashlib.sha256(raw).hexdigest()
        path = Path(directory) / f"GuoGuoGaGa-{digest}.json"
        path.write_bytes(raw)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        snapshot = load_snapshot(path)
        if snapshot.sha256 != digest:
            raise PublishError("The independent copy changed; publication rejected.", "copy_changed")
        if snapshot.name != "GuoGuoGaGa" or snapshot.map_name != "Prometheus":
            raise PublishError("Only the GuoGuoGaGa world on Prometheus is approved for synchronization.", "world_not_approved")
        if any(warning.startswith(("World save format version", "存檔世界格式版本")) for warning in snapshot.warnings):
            raise PublishError("The current world package version has not been verified; publication stopped.", "save_version_unknown")
        return path, snapshot, raw

    @staticmethod
    def _asset(release, name):
        matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
        if len(matches) > 1:
            raise PublishError("Duplicate remote asset names were found; upload stopped.", "asset_conflict")
        return matches[0] if matches else None

    def _verify_asset(self, asset, snapshot, raw):
        if asset.get("state") != "uploaded" or asset.get("size") != len(raw):
            raise PublishError("The remote world asset upload is incomplete or its size conflicts.", "asset_conflict")
        digest = asset.get("digest")
        if digest:
            valid = digest == "sha256:" + snapshot.sha256
        else:
            valid = hashlib.sha256(self.gh.asset_bytes(asset)).hexdigest() == snapshot.sha256
        if not valid:
            raise PublishError("A remote asset with the same name has a conflicting hash; upload stopped.", "asset_conflict")

    def _remember_upload(self, state, asset, snapshot, raw):
        """Record only uploads this publisher performed and verified itself."""
        receipts = state.get("managed_assets", [])
        if not isinstance(receipts, list):
            raise PublishError("The sync asset record format is unrecognized; publication stopped.", "state_invalid")
        receipt = {"id": asset["id"], "name": asset["name"], "sha256": snapshot.sha256,
                   "bytes": len(raw), "uploaded_at": _utc(self.clock.time())}
        if receipt["id"] not in [row.get("id") for row in receipts if isinstance(row, dict)]:
            receipts.append(receipt)
        state["managed_assets"] = receipts
        _write_json(self.state_dir / "state.json", state)

    def _prune(self, state, index):
        """Bound only this publisher's verified raw assets, after index commit.

        Unknown or conflicting assets are never adopted for cleanup. Private
        save-guard history and other releases are outside this method's scope.
        """
        if not self.keep_versions:
            return []
        receipts = state.get("managed_assets", [])
        if not isinstance(receipts, list):
            return ["history_cleanup_state_invalid"]
        valid = [row for row in receipts if isinstance(row, dict)
                 and isinstance(row.get("id"), int) and not isinstance(row.get("id"), bool)
                 and row["id"] > 0 and re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))
                 and row.get("name") == f"GuoGuoGaGa-{row['sha256']}.json"
                 and isinstance(row.get("bytes"), int) and not isinstance(row.get("bytes"), bool)
                 and row["bytes"] > 0]
        # Preserve the current committed version, plus the most recently
        # uploaded preceding versions. Receipt order survives process restart.
        previous = [row for row in valid if row["name"] != index["asset_name"]]
        keep = {row["id"] for row in previous[-max(0, self.keep_versions - 1):]} if self.keep_versions > 1 else set()
        keep.update(row["id"] for row in valid if row["name"] == index["asset_name"])
        warnings = []
        for receipt in valid:
            if receipt["id"] in keep or receipt["name"] == index["asset_name"]:
                continue
            try:
                release = self.gh.release()
                if release.get("tag_name") != self.tag or release.get("immutable") or release.get("draft"):
                    warnings.append("history_cleanup_release_changed")
                    break
                remote_index = self._asset(release, INDEX_ASSET)
                if remote_index is None or json.loads(self.gh.asset_bytes(remote_index)) != index:
                    warnings.append("history_cleanup_index_changed")
                    break
                asset = self._asset(release, receipt["name"])
                if asset is None:
                    state["managed_assets"] = [row for row in state.get("managed_assets", []) if row != receipt]
                    continue
                if (asset.get("id") != receipt["id"] or asset.get("state") != "uploaded"
                        or asset.get("size") != receipt["bytes"]):
                    warnings.append("history_cleanup_asset_conflict")
                    continue
                digest = asset.get("digest")
                if digest:
                    verified = digest == "sha256:" + receipt["sha256"]
                else:
                    raw = self.gh.asset_bytes(asset)
                    verified = len(raw) == receipt["bytes"] and hashlib.sha256(raw).hexdigest() == receipt["sha256"]
                if not verified:
                    warnings.append("history_cleanup_asset_conflict")
                    continue
                self.gh.delete_asset(asset)
                state["managed_assets"] = [row for row in state.get("managed_assets", []) if row != receipt]
            except Exception:
                warnings.append("history_cleanup_failed")
        try:
            _write_json(self.state_dir / "state.json", state)
        except Exception:
            warnings.append("history_cleanup_state_write_failed")
        return list(dict.fromkeys(warnings))

    def _publish(self, force=False):
        state = self._state()
        with tempfile.TemporaryDirectory(prefix="verified-", dir=self.state_dir) as directory:
            path, snapshot, raw = self._validate_copy(directory)
            if state.get("world_id") and state["world_id"] != snapshot.world_id:
                raise PublishError("The source world identity changed; cross-world upload rejected.", "binding_mismatch")
            if not state.get("world_id"):
                state.update(source=str(self.source), repository=self.repository, tag=self.tag,
                             world_id=snapshot.world_id)
                _write_json(self.state_dir / "state.json", state)
            if state.get("sha256") == snapshot.sha256 and not force:
                return self._status("unchanged", sha256=snapshot.sha256, world_id=snapshot.world_id,
                                    last_published_at=state.get("last_published_at"))
            now = self.clock.time()
            if now - state.get("last_published_epoch", 0) < MIN_UPLOAD_SECONDS:
                return self._status("throttled", sha256=snapshot.sha256, world_id=snapshot.world_id,
                                    retry_at=_utc(state["last_published_epoch"] + MIN_UPLOAD_SECONDS))
            if self.gh is None:
                self.gh = GitHubCLI(self.repository, self.tag)
            try:
                release = self.gh.release()
            except ReleaseNotFound:
                release = self.gh.create_release()
            if release.get("immutable"):
                raise PublishError("The world sync release is immutable; its index cannot be updated.", "release_immutable")
            if release.get("tag_name") != self.tag or release.get("draft"):
                raise PublishError("GitHub returned a mismatching world sync release.", "release_invalid")
            asset = self._asset(release, path.name)
            uploaded_here = asset is None
            if asset is None:
                self.gh.upload(path)
                release = self.gh.release()
                asset = self._asset(release, path.name)
                if asset is None:
                    raise PublishError("The remote world asset is not confirmed; retaining the original index.", "upload_unconfirmed")
            self._verify_asset(asset, snapshot, raw)
            if uploaded_here:
                self._remember_upload(state, asset, snapshot, raw)
            now = self.clock.time()
            index = {"schema_version": 1, "world_id": snapshot.world_id, "name": snapshot.name,
                     "map_name": snapshot.map_name, "saved_at": snapshot.saved_at.astimezone(timezone.utc).isoformat(),
                     "published_at": _utc(now), "sha256": snapshot.sha256, "bytes": len(raw), "asset_name": path.name,
                     "source_mtime_ns": snapshot.source_mtime_ns, "source_mtime": snapshot.source_mtime}
            index_path = Path(directory) / INDEX_ASSET
            _write_json(index_path, index)
            self.gh.upload(index_path, replace=True)
            # Confirm the last commit point before recording success. A dropped
            # response after upload is safe to retry against the same raw asset.
            release = self.gh.release()
            uploaded_index = self._asset(release, INDEX_ASSET)
            if uploaded_index is None:
                raise PublishError("The remote index is not confirmed; pending data is retained for retry.", "index_unconfirmed")
            if self.gh.asset_bytes(uploaded_index) != index_path.read_bytes():
                raise PublishError("Remote index verification failed; it will be retried later.", "index_unconfirmed")
            state.update(sha256=snapshot.sha256, index=index, last_published_epoch=now,
                         last_published_at=index["published_at"])
            _write_json(self.state_dir / "state.json", state)
            cleanup_warnings = self._prune(state, index)
            self._failures = 0
            return self._status("published", sha256=snapshot.sha256, world_id=snapshot.world_id,
                                saved_at=index["saved_at"], last_published_at=index["published_at"],
                                warnings=cleanup_warnings)

    def tick(self, *, force=False):
        with self.lock():
            if self.clock.monotonic() < self._next_attempt:
                return self._status("retry_wait")
            try:
                stat = self.source.stat()
                signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                if signature == self._signature:
                    self._stable_rounds += 1
                else:
                    self._signature, self._stable_rounds = signature, 1
                if self._stable_rounds < 2:
                    return self._status("waiting_for_stable_save")
                if (not force and signature == self._verified_signature
                        and self.clock.monotonic() - self._verified_at < MIN_UPLOAD_SECONDS):
                    state = self._state()
                    if state.get("sha256") == self._verified_digest and state.get("world_id") == self._verified_world:
                        return self._status("unchanged", sha256=self._verified_digest, world_id=self._verified_world,
                                            last_published_at=state.get("last_published_at"))
                result = self._publish(force=force)
                if result["status"] in {"published", "unchanged"}:
                    self._verified_signature = signature
                    self._verified_digest, self._verified_world = result["sha256"], result["world_id"]
                    self._verified_at = self.clock.monotonic()
                return result
            except Exception as exc:
                self._failures += 1
                self._next_attempt = self.clock.monotonic() + min(300, 5 * 2 ** min(self._failures, 6))
                self._status(getattr(exc, "code", "source_invalid"), retry_at=_utc(
                    self.clock.time() + min(300, 5 * 2 ** min(self._failures, 6))))
                raise

    def run_once(self, force=False):
        with self.lock():
            result = self.tick(force=force)
            if result["status"] == "waiting_for_stable_save":
                self.clock.sleep(POLL_SECONDS)
                result = self.tick(force=force)
            if result["status"] == "waiting_for_stable_save":
                raise SaveChangedError("The save is still changing; nothing was uploaded. Try again later.")
            return result

    def run_forever(self):
        with self.lock():
            while True:
                try:
                    self.tick()
                except Exception:
                    # tick() records a bounded retry without copying exception
                    # text into diagnostics. A malformed remote response must
                    # not terminate the independent source watcher.
                    pass
                self.clock.sleep(POLL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Automatically publish the approved GuoGuoGaGa world save")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--tag", default=RELEASE_TAG)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--keep-versions", type=int, default=0, help="Number of this publisher's remote versions to retain; 0 disables cleanup")
    args = parser.parse_args(argv)
    try:
        publisher = Publisher(args.source, args.state_dir, args.repository, args.tag,
                              keep_versions=args.keep_versions)
        if args.once:
            print(json.dumps(publisher.run_once(), ensure_ascii=False))
        else:
            publisher.run_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except PublishError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, SaveError):
        print("The world source could not be read stably or verified; no unconfirmed save was uploaded.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
