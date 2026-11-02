"""Check Qt's ICU imports without loading any Qt or game DLL into this process.

The Windows loader prefers an app-local ICU over the system ICU. A different
library's same-named ICU can therefore break an otherwise complete Qt bundle.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any

import pefile


def imported_symbols(path: Path, library: str) -> set[str]:
    image = pefile.PE(str(path), fast_load=True)
    try:
        image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        return {
            symbol.name.decode("ascii") if symbol.name else f"#{symbol.ordinal}"
            for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", [])
            if entry.dll.decode("ascii").casefold() == library.casefold()
            for symbol in entry.imports
        }
    finally:
        image.close()


def exported_symbols(path: Path) -> set[str]:
    image = pefile.PE(str(path), fast_load=True)
    try:
        image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        entries = getattr(getattr(image, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])
        return {name for symbol in entries
                for name in ([symbol.name.decode("ascii")] if symbol.name else []) + [f"#{symbol.ordinal}"]}
    finally:
        image.close()


def _toc_sources(toc_path: Path | None) -> list[dict[str, str]]:
    if toc_path is None or not toc_path.exists():
        return []
    try:
        content = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
    except (ValueError, SyntaxError, OSError):
        return []
    sources = []

    def visit(item):
        if not isinstance(item, (list, tuple)):
            return
        if (len(item) == 3 and isinstance(item[0], str) and isinstance(item[1], str)
                and item[0].replace("\\", "/").rsplit("/", 1)[-1].casefold() in {"icuuc.dll", "icudt78.dll"}):
            sources.append({"destination": item[0], "source": item[1], "kind": str(item[2])})
        for child in item:
            visit(child)

    visit(content)
    return sources


def audit_runtime(bundle_dir: str | Path, *, system_icu: Path | None = None,
                  toc_path: Path | None = None) -> dict[str, Any]:
    bundle = Path(bundle_dir).resolve()
    if bundle.suffix.casefold() == ".exe":
        bundle = bundle.parent
    internal = bundle / "_internal"
    qtcore = internal / "PySide6" / "Qt6Core.dll"
    system = system_icu or Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "icuuc.dll"
    report: dict[str, Any] = {
        "success": False,
        "bundle_dir": str(bundle),
        "qtcore": str(qtcore),
        "analysis_icu_sources": _toc_sources(toc_path),
        "errors": [],
    }
    try:
        required = imported_symbols(qtcore, "icuuc.dll")
        report["required_symbols"] = sorted(required)
        report["required_symbol_count"] = len(required)
        if not required:
            report["errors"].append("Qt6Core did not expose the expected ICU import contract; manual verification required.")

        def inspect(path: Path) -> dict[str, Any]:
            entry: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
            if entry["exists"]:
                try:
                    available = exported_symbols(path)
                    entry.update(export_count=len(available), missing_symbols=sorted(required - available))
                except (OSError, pefile.PEFormatError, UnicodeError) as exc:
                    entry.update(missing_symbols=sorted(required), error=f"{type(exc).__name__}: {exc}")
            else:
                entry["missing_symbols"] = sorted(required)
            return entry

        # Either location can shadow Windows' system ICU for an imported Qt DLL.
        bundled_paths = [internal / "icuuc.dll", internal / "PySide6" / "icuuc.dll", bundle / "icuuc.dll"]
        bundled = [inspect(path) for path in bundled_paths if path.exists()]
        report["bundled_icu"] = bundled
        report["system_icu"] = inspect(system)
        if not report["system_icu"]["exists"] or report["system_icu"]["missing_symbols"]:
            report["errors"].append("Windows system ICU does not satisfy Qt6Core's imported symbols.")
        for item in bundled:
            if item["missing_symbols"]:
                report["errors"].append(f"Bundled ICU shadows the system DLL but lacks required symbols: {item['path']}")
        report["effective_icu"] = "bundled" if bundled else "Windows system ICU"
        report["success"] = bool(required) and not report["errors"]
    except (OSError, pefile.PEFormatError, UnicodeError) as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", default="dist/ICARUS Helper")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--toc", type=Path, default=Path("build/ICARUS Helper/Analysis-00.toc"))
    args = parser.parse_args()
    report = audit_runtime(args.bundle_dir, toc_path=args.toc)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
