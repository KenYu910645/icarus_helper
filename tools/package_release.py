"""Create a complete portable Windows ZIP, file manifest and SHA256SUMS."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(bundle: Path, output: Path, version: str) -> dict:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Use a release version such as v0.1.0")
    bundle = bundle.resolve()
    root = Path(__file__).resolve().parents[1]
    if not bundle.is_relative_to(root / "dist") or not (bundle / "ICARUS Helper.exe").is_file():
        raise ValueError("Expected this project's complete Windows bundle inside dist")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"ICARUS-Helper-{version}-Windows-x64.zip"
    if archive.exists():
        raise FileExistsError(archive)
    entries = {p.relative_to(bundle).as_posix(): p for p in bundle.rglob("*") if p.is_file()}
    for name in ("README.md", "DATA_SOURCES.md", "THIRD_PARTY_NOTICES.md"):
        entries[name] = root / name
    for path in (root / "licenses").rglob("*"):
        if path.is_file():
            entries[path.relative_to(root).as_posix()] = path
    forbidden = {"settings.json", "app.log", ".env", "qtvirtualkeyboardplugin.dll", "Qt6VirtualKeyboard.dll", "Qt6Pdf.dll", "qpdf.dll"}
    for relative, path in entries.items():
        if path.is_symlink() or Path(relative).name in forbidden or any(part in {"artifacts", "snapshots", "PlayerData", "Prospects", ".git"} for part in Path(relative).parts):
            raise ValueError(f"Unexpected private or unused file in release: {relative}")
    manifest = {"version": version, "platform": "Windows x64", "default_map": "Prometheus",
                "default_base": "我們家", "private_save_included": False,
                "files": [{"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)} for name, path in sorted(entries.items())]}
    prefix = "ICARUS Helper/"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_file:
        for name, path in sorted(entries.items()):
            zip_file.write(path, prefix + name)
        zip_file.writestr(prefix + "release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as zip_file:
        if zip_file.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
    checksum = output / "SHA256SUMS.txt"
    checksum.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")
    return {"archive": str(archive), "bytes": archive.stat().st_size, "files": len(entries) + 1, "sha256": sha256(archive)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("releases"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.bundle, args.output, args.version), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
