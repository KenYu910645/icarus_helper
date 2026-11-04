"""Read the explicitly public GuoGuoGaGa release into an app-owned cache.

This module never writes to a game directory, publishes an asset, installs a
task, or schedules itself. Call it from a background worker. Only a fully
verified version becomes the last-good cache; offline results are marked stale.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .models import WorldSnapshot
from .saves import MAX_SAVE_BYTES, _json_bytes, _read_stable, load_snapshot
from .settings import _file_lock, app_dir


DEFAULT_REPOSITORY = "KenYu910645/icarus_helper"
RELEASE_TAG = "world-sync-guoguogaga"
REMOTE_SOURCE = "github-world:guoguogaga"
INDEX_ASSET = "world-index.json"
RELEASE_API = f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/tags/{RELEASE_TAG}"
DOWNLOAD_BASE = f"https://github.com/{DEFAULT_REPOSITORY}/releases/download/{RELEASE_TAG}/"
MAX_INDEX_BYTES = 64 * 1024
MAX_RELEASE_BYTES = 2 * 1024 * 1024
MARKER_NAME = ".icarus-remote-world.json"
RECEIPT_NAME = "last-good.json"
_MARKER = {"schema": 1, "source": REMOTE_SOURCE, "repository": DEFAULT_REPOSITORY, "tag": RELEASE_TAG}
_ASSET_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"})


class RemoteSyncError(ValueError):
    """A network, identity, integrity or remote-cache validation failure."""


def _default_cache_path() -> Path:
    # Do not call app_dir(): recognition must not create directories.
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "IcarusHelper" / "remote-worlds" / "guoguogaga"


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _check_unlinked(path: Path) -> None:
    for item in (path, *path.parents):
        if _linked(item):
            raise RemoteSyncError("Remote cache paths cannot use symbolic links or redirected directories")


def is_remote_cache_path(path: str | Path) -> bool:
    """Recognize default or marked custom caches, including their descendants.

    This is a provenance guard for browse/restore flows, not a request to load
    a file. It has no filesystem writes or network effects.
    """
    candidate = Path(path).absolute()
    default = _default_cache_path().absolute()
    try:
        if candidate.is_relative_to(default) or candidate.resolve().is_relative_to(default.resolve()):
            return True
    except (OSError, RuntimeError):
        pass
    for directory in (candidate, *candidate.parents):
        marker = directory / MARKER_NAME
        try:
            if marker.is_file() and not _linked(marker) and _read_json_file(marker, MAX_INDEX_BYTES) == _MARKER:
                return True
        except (OSError, ValueError, RuntimeError):
            continue
    return False


def _safe_url(url: str, *, redirected: bool = False) -> str:
    if not isinstance(url, str) or any(ord(ch) < 32 for ch in url):
        raise RemoteSyncError("Invalid GitHub download URL")
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443) or parts.fragment:
            raise RemoteSyncError("Only GitHub HTTPS sources are allowed")
    except ValueError as exc:
        raise RemoteSyncError("Invalid GitHub download URL") from exc
    if redirected and parts.hostname in _ASSET_HOSTS:
        return url
    if parts.query:
        if url.partition("?")[0] == DOWNLOAD_BASE + INDEX_ASSET and re.fullmatch(r"check=[0-9]{1,12}", parts.query):
            return url
        raise RemoteSyncError("Unsupported GitHub source parameters")
    if url == RELEASE_API:
        return url
    if url.startswith(DOWNLOAD_BASE):
        name = url[len(DOWNLOAD_BASE):]
        if name == INDEX_ASSET or re.fullmatch(r"GuoGuoGaGa-[0-9a-f]{64}\.json", name):
            return url
    raise RemoteSyncError("The source must use the specified repository and fixed world sync release")


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl, redirected=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, *, max_bytes: int) -> bytes:
    """Bounded, unauthenticated GET with a checked initial URL and every redirect."""
    _safe_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "ICARUS-Helper-WorldSync/1", "Accept": "application/vnd.github+json" if url == RELEASE_API else "application/octet-stream",
                                                 "Cache-Control": "no-cache", "Pragma": "no-cache"})
    try:
        with urllib.request.build_opener(_RestrictedRedirect()).open(request, timeout=20) as response:
            _safe_url(response.geturl(), redirected=True)
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or not 0 <= int(length) <= max_bytes):
                raise RemoteSyncError("The GitHub file size is invalid or exceeds the limit")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes or (length is not None and len(body) != int(length)):
                raise RemoteSyncError("The GitHub file download is incomplete or exceeds the size limit")
            return body
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise RemoteSyncError(f"GitHub returned HTTP {code}; synchronization did not complete") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RemoteSyncError("Cannot connect to GitHub. Try again later") from exc


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteSyncError("GitHub metadata contains duplicate fields")
        result[key] = value
    return result


def _json_object(raw: bytes) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeError) as exc:
        raise RemoteSyncError("GitHub metadata is incomplete JSON") from exc
    if not isinstance(value, dict):
        raise RemoteSyncError("GitHub metadata must be an object")
    return value


def _read_json_file(path: Path, limit: int) -> dict:
    _check_unlinked(path)
    if path.stat().st_size > limit:
        raise RemoteSyncError("Remote cache metadata exceeds the size limit")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise RemoteSyncError("Remote cache metadata exceeds the size limit")
    return _json_object(raw)


def _get(transport, url: str, limit: int) -> bytes:
    _safe_url(url)
    raw = transport(url, max_bytes=limit)
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise RemoteSyncError("GitHub response size or format is invalid")
    return raw


def _utc(value, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise RemoteSyncError(f"The world index has no valid {field} UTC timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError()
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise RemoteSyncError(f"The world index has no valid {field} UTC timestamp") from exc


def _validate_index(index: dict) -> dict:
    if type(index.get("schema_version")) is not int or index["schema_version"] != 1:
        raise RemoteSyncError("Unsupported world index version")
    if not isinstance(index.get("world_id"), str) or not re.fullmatch(r"[0-9a-f]{24}", index["world_id"]):
        raise RemoteSyncError("Invalid world index identity")
    if index.get("name") != "GuoGuoGaGa" or index.get("map_name") != "Prometheus":
        raise RemoteSyncError("The world index is not GuoGuoGaGa / Prometheus")
    sha = index.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RemoteSyncError("Invalid world index SHA-256")
    if index.get("asset_name") != f"GuoGuoGaGa-{sha}.json":
        raise RemoteSyncError("The world index filename does not match its hash")
    if type(index.get("bytes")) is not int or not 0 < index["bytes"] <= MAX_SAVE_BYTES:
        raise RemoteSyncError("The world index file size is invalid or exceeds the limit")
    saved_at, published_at = _utc(index.get("saved_at"), "saved_at"), _utc(index.get("published_at"), "published_at")
    mtime_ns = index.get("source_mtime_ns")
    if mtime_ns is None:
        mtime = index.get("source_mtime", saved_at.timestamp())
        if isinstance(mtime, bool) or not isinstance(mtime, (float, int)) or not math.isfinite(mtime):
            raise RemoteSyncError("Invalid source save time")
        mtime_ns = round(mtime * 1_000_000_000)
    if type(mtime_ns) is not int or not 0 < mtime_ns < 9_000_000_000_000_000_000:
        raise RemoteSyncError("Invalid source save time")
    # Retain only the public schema fields; remote paths/URLs are never trusted.
    return {"schema_version": 1, **{key: index[key] for key in ("world_id", "name", "map_name", "sha256", "bytes", "asset_name")},
            "saved_at": saved_at.isoformat(), "published_at": published_at.isoformat(), "source_mtime_ns": mtime_ns}


def _asset(release: dict, name: str, limit: int) -> dict:
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) > 2000:
        raise RemoteSyncError("The GitHub Release asset list is incomplete")
    matches = [row for row in assets if isinstance(row, dict) and row.get("name") == name]
    if len(matches) != 1:
        raise RemoteSyncError("The GitHub Release does not contain a unique world data file")
    row = matches[0]
    if row.get("state", "uploaded") != "uploaded" or type(row.get("size")) is not int or not 0 < row["size"] <= limit:
        raise RemoteSyncError("The GitHub asset upload is incomplete or its size is invalid")
    url = row.get("browser_download_url")
    if url != DOWNLOAD_BASE + name:
        raise RemoteSyncError("The GitHub asset URL does not belong to the specified world version")
    _safe_url(url)
    return row


def _atomic_bytes(path: Path, raw: bytes, *, mtime_ns: int | None = None) -> None:
    _check_unlinked(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if mtime_ns is not None:
            os.utime(temporary, ns=(mtime_ns, mtime_ns))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _cache_root(cache_dir: Path | None) -> Path:
    root = Path(cache_dir).absolute() if cache_dir is not None else app_dir() / "remote-worlds" / "guoguogaga"
    _check_unlinked(root)
    if any(part.casefold() in {"prospects", "playerdata"} for part in root.parts) or root == Path(root.anchor):
        raise RemoteSyncError("The remote cache cannot be inside the game save directory")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER_NAME
    # Lock marker creation so simultaneous first launches can safely share it.
    _check_unlinked(root / "cache-init.lock")
    with _file_lock(root / "cache-init"):
        if marker.exists():
            if _read_json_file(marker, MAX_INDEX_BYTES) != _MARKER:
                raise RemoteSyncError("The remote cache source marker does not match")
        else:
            remaining = [p for p in root.iterdir() if p.name != "cache-init.lock"]
            if remaining:
                raise RemoteSyncError("Use a separate empty directory for the remote cache")
            _atomic_bytes(marker, json.dumps(_MARKER).encode("utf-8"))
    return root.resolve()


def _receipt(root: Path) -> dict | None:
    path = root / RECEIPT_NAME
    return _validate_index(_read_json_file(path, MAX_INDEX_BYTES)) if path.exists() else None


def _validate_snapshot(path: Path, index: dict) -> WorldSnapshot:
    _check_unlinked(path)
    if path.stat().st_size != index["bytes"]:
        raise RemoteSyncError("World snapshot file length does not match")
    raw, _stat = _read_stable(path)
    if hashlib.sha256(raw).hexdigest() != index["sha256"]:
        raise RemoteSyncError("World snapshot SHA-256 verification failed")
    blob = _json_bytes(raw).get("ProspectBlob")
    if not isinstance(blob, dict) or not isinstance(blob.get("Hash"), str) or not re.fullmatch(r"[0-9a-fA-F]{40}", blob["Hash"]):
        raise RemoteSyncError("The world snapshot has no complete package hash")
    if type(blob.get("UncompressedLength")) is not int or blob["UncompressedLength"] <= 0:
        raise RemoteSyncError("The world snapshot has no complete package length")
    snapshot = load_snapshot(path)
    if snapshot.sha256 != index["sha256"]:
        raise RemoteSyncError("World snapshot SHA-256 verification failed")
    claim = snapshot.prospect_info.get("ClaimedAccountID")
    if not isinstance(claim, str) or not claim.strip():
        raise RemoteSyncError("The world snapshot has no verifiable host identity")
    if (snapshot.world_id, snapshot.name, snapshot.map_name) != (index["world_id"], index["name"], index["map_name"]):
        raise RemoteSyncError("The world snapshot identity does not match the public index")
    if any(warning.startswith(("World save format version", "存檔世界格式版本")) for warning in snapshot.warnings):
        raise RemoteSyncError("The world snapshot package version has not been verified")
    if abs((snapshot.saved_at.astimezone(timezone.utc) - _utc(index["saved_at"], "saved_at")).total_seconds()) > 0.001:
        raise RemoteSyncError("The world snapshot save time does not match the public index")
    # Filesystem timestamp precision can differ from the publishing host. After
    # verifying the saved time, retain the source metadata rather than download
    # or cache-touch times in the returned model.
    snapshot.source_mtime_ns = index["source_mtime_ns"]
    snapshot.source_mtime = index["source_mtime_ns"] / 1_000_000_000
    snapshot.saved_at = _utc(index["saved_at"], "saved_at").astimezone()
    return snapshot


def _remote(snapshot: WorldSnapshot, index: dict, checked_at: datetime, error: str = "") -> WorldSnapshot:
    snapshot.is_remote = True
    snapshot.remote_source = REMOTE_SOURCE
    snapshot.remote_repository = DEFAULT_REPOSITORY
    snapshot.remote_tag = RELEASE_TAG
    snapshot.remote_checked_at = checked_at
    snapshot.remote_published_at = _utc(index["published_at"], "published_at")
    snapshot.remote_stale = bool(error)
    snapshot.remote_status = f"Remote sync failed; keeping the last valid save (stale): {error}" if error else "Host public save synchronized; showing the state recorded in that save"
    snapshot.remote_index = dict(index)
    return snapshot


def load_remote_snapshot(cache_dir: Path | None = None, *, transport=None, now: datetime | None = None) -> WorldSnapshot:
    """Fetch the fixed release; on failure return only a validated last-good save.

    ``transport(url, *, max_bytes) -> bytes`` permits deterministic offline tests;
    all initial URLs are validated even when using an injected transport.
    ``now`` only controls the displayed check timestamp, never file/save times.
    """
    root = _cache_root(cache_dir)
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    transport = transport or http_get
    previous, cached = None, None
    try:
        previous = _receipt(root)
        if previous:
            cached = _validate_snapshot(root / previous["asset_name"], previous)
    except (OSError, ValueError):
        # A corrupted local cache may be repaired by a new valid network copy,
        # but it must never be shown as an offline fallback.
        cached = None
    try:
        release = _json_object(_get(transport, RELEASE_API, MAX_RELEASE_BYTES))
        if release.get("tag_name") != RELEASE_TAG or release.get("draft") is not False:
            raise RemoteSyncError("GitHub returned a different or unpublished release")
        index_asset = _asset(release, INDEX_ASSET, MAX_INDEX_BYTES)
        # The mutable index uses a fresh public timestamp to avoid a CDN serving
        # the previous asset after a replace. Immutable SHA-named saves keep
        # their canonical URL; no identity or credential is added to the query.
        index_url = index_asset["browser_download_url"] + f"?check={int(checked_at.timestamp())}"
        index_raw = _get(transport, index_url, MAX_INDEX_BYTES)
        if len(index_raw) != index_asset["size"]:
            raise RemoteSyncError("The world index download is incomplete")
        index = _validate_index(_json_object(index_raw))
        version_asset = _asset(release, index["asset_name"], MAX_SAVE_BYTES)
        if version_asset["size"] != index["bytes"]:
            raise RemoteSyncError("The world data upload is incomplete")
        if previous and index["world_id"] != previous["world_id"]:
            raise RemoteSyncError("The remote source changed to a different world; cache overwrite rejected")
        if cached and index == previous:
            return _remote(cached, index, checked_at)
        raw = _get(transport, version_asset["browser_download_url"], index["bytes"])
        if len(raw) != index["bytes"] or hashlib.sha256(raw).hexdigest() != index["sha256"]:
            raise RemoteSyncError("World data length or SHA-256 verification failed")
        candidate = root / f".candidate-{uuid.uuid4().hex}.json"
        try:
            _atomic_bytes(candidate, raw, mtime_ns=index["source_mtime_ns"])
            snapshot = _validate_snapshot(candidate, index)
            _check_unlinked(root / "remote-sync.lock")
            with _file_lock(root / "remote-sync"):
                latest = _receipt(root)
                if latest and (latest["world_id"] != index["world_id"] or _utc(latest["published_at"], "published_at") > _utc(index["published_at"], "published_at")):
                    raise RemoteSyncError("A newer valid version or a different world already exists; overwrite rejected")
                target = root / index["asset_name"]
                _check_unlinked(target)
                candidate.replace(target)
                _atomic_bytes(root / RECEIPT_NAME, json.dumps(index, ensure_ascii=False).encode("utf-8"))
                snapshot.source_path = target
                snapshot.cache_path = target
            return _remote(snapshot, index, checked_at)
        finally:
            candidate.unlink(missing_ok=True)
    except Exception as exc:
        # Network transports may raise socket/HTTP/library-specific exceptions.
        # Display a bounded local message, never signed redirect URLs or tokens.
        if cached is not None and previous is not None:
            message = str(exc) if isinstance(exc, RemoteSyncError) else "Could not fetch and verify the current GitHub world data"
            return _remote(cached, previous, checked_at, message[:240])
        if isinstance(exc, RemoteSyncError):
            raise
        raise RemoteSyncError("Could not fetch and verify GitHub world data; no valid offline cache is available") from exc
