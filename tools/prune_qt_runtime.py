"""Remove unused optional Qt components after checking the native import graph."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pefile


UNUSED = (
    "Qt6Pdf.dll", "Qt6VirtualKeyboard.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll",
    "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll", "Qt6Quick.dll",
    "plugins/imageformats/qpdf.dll",
    "plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
)


def prune(bundle: Path) -> dict:
    bundle = bundle.resolve()
    workspace = Path(__file__).resolve().parents[1]
    if not bundle.is_relative_to(workspace / "dist"):
        raise ValueError("Bundle must be inside this project's dist directory")
    qt = bundle / "_internal" / "PySide6"
    candidates = {qt.joinpath(name).resolve() for name in UNUSED if qt.joinpath(name).is_file()}
    if any(not path.is_relative_to(bundle) for path in candidates):
        raise ValueError("Unexpected path outside bundle")
    removed_names = {path.name.casefold() for path in candidates}
    blockers = []
    for path in bundle.rglob("*"):
        if path.suffix.casefold() not in {".dll", ".pyd", ".exe"} or path.resolve() in candidates:
            continue
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                if entry.dll.decode("ascii").casefold() in removed_names:
                    blockers.append(f"{path.relative_to(bundle).as_posix()} requires {entry.dll.decode('ascii')}")
        finally:
            pe.close()
    if blockers:
        raise RuntimeError("Cannot remove required runtime components: " + "; ".join(blockers))
    removed = [path.relative_to(bundle).as_posix() for path in sorted(candidates)]
    for path in candidates:
        path.unlink()
    return {"success": True, "removed_unused_components": removed, "remaining_import_blockers": blockers}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = prune(args.bundle_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
