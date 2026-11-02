"""DLL contract checks catch a same-named ICU from another application."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from tools import check_qt_runtime


def test_incompatible_bundled_icu_fails_even_when_system_icu_is_compatible(tmp_path, monkeypatch):
    bundle = tmp_path / "APP"
    local = bundle / "_internal" / "icuuc.dll"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"synthetic DLL path; export reader is injected below")
    system = tmp_path / "System32" / "icuuc.dll"
    system.parent.mkdir()
    system.write_bytes(b"synthetic system DLL path")
    required = {"ucnv_open", "ucnv_close", "UCNV_TO_U_CALLBACK_SUBSTITUTE"}
    monkeypatch.setattr(check_qt_runtime, "imported_symbols", lambda *_: required)
    monkeypatch.setattr(check_qt_runtime, "exported_symbols", lambda path: required if path == system else {name + "_78" for name in required})
    failed = check_qt_runtime.audit_runtime(bundle, system_icu=system)
    assert not failed["success"]
    assert failed["system_icu"]["missing_symbols"] == []
    assert set(failed["bundled_icu"][0]["missing_symbols"]) == required
    # Removing the shadowing DLL allows the known-compatible system ICU to win.
    local.unlink()
    repaired = check_qt_runtime.audit_runtime(bundle, system_icu=system)
    assert repaired["success"] and repaired["effective_icu"] == "Windows system ICU"


@pytest.mark.skipif(os.name != "nt", reason="Windows system ICU contract")
def test_installed_qtcore_icu_imports_are_satisfied_by_windows_system_icu():
    spec = importlib.util.find_spec("PySide6")
    if not spec or not spec.origin:
        pytest.skip("PySide6 not installed")
    qtcore = Path(spec.origin).parent / "Qt6Core.dll"
    system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "icuuc.dll"
    if not qtcore.exists() or not system.exists():
        pytest.skip("Installed QtCore / Windows ICU unavailable")
    required = check_qt_runtime.imported_symbols(qtcore, "icuuc.dll")
    available = check_qt_runtime.exported_symbols(system)
    assert required
    assert not required - available
