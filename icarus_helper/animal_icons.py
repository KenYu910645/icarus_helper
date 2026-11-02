"""Exact AISetup -> BestiaryGroup -> native creature portraits, offline at runtime."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

from .item_icons import item_pixmap

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load_animal_icon_catalog() -> dict:
    try:
        value = json.loads((ROOT / "data" / "animal_icons.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _species_paths() -> dict[str, str]:
    rows = load_animal_icon_catalog().get("species", {})
    return {key.casefold(): row.get("file", "") for key, row in rows.items()
            if isinstance(key, str) and isinstance(row, dict)} if isinstance(rows, dict) else {}


def animal_icon_path(species_id: str) -> Path | None:
    filename = _species_paths().get(str(species_id).casefold(), "")
    if not isinstance(filename, str) or not re.fullmatch(r"[a-f0-9]{24}\.png", filename):
        return None
    path = ROOT / "assets" / "animals" / filename
    return path if path.is_file() else None


def has_animal_icon(species_id: str) -> bool:
    return animal_icon_path(species_id) is not None


@lru_cache(maxsize=512)
def _pixmap(species_id: str, size: int) -> QPixmap:
    path = animal_icon_path(species_id)
    picture = QPixmap(str(path)) if path else QPixmap()
    if picture.isNull():
        return item_pixmap("", size)
    return picture.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)


def animal_pixmap(species_id: str, size: int = 64) -> QPixmap:
    return _pixmap(str(species_id).casefold(), max(8, min(256, int(size))))


def animal_icon(species_id: str, size: int = 64) -> QIcon:
    return QIcon(animal_pixmap(species_id, size))
