"""Local, versioned game item thumbnails; never opens the game at runtime."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load_icon_catalog() -> dict:
    try:
        result = json.loads((ROOT / "data" / "item_icons.json").read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _item_paths() -> dict[str, str]:
    return {key.casefold(): value for key, value in load_icon_catalog().get("items", {}).items() if isinstance(key, str) and isinstance(value, str)}


def item_icon_path(item_id: str) -> Path | None:
    filename = _item_paths().get(str(item_id).casefold(), "")
    # Catalog data can select only a thumbnail inside this module's asset folder.
    if not re.fullmatch(r"[a-f0-9]{20,64}\.png", filename):
        return None
    path = ROOT / "assets" / "items" / filename
    return path if path.is_file() else None


def has_item_icon(item_id: str) -> bool:
    return item_icon_path(item_id) is not None


@lru_cache(maxsize=2048)
def _pixmap(item_id: str, size: int) -> QPixmap:
    path = item_icon_path(item_id)
    picture = QPixmap(str(path)) if path else QPixmap()
    if not picture.isNull():
        return picture.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    picture = QPixmap(size, size)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#9eb1c0"), max(1, size / 28)))
    painter.setBrush(QColor("#253848"))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size / 6, size / 6)
    font = QFont("Segoe UI")
    font.setPixelSize(max(8, int(size * 0.72)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#e6edf4"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "?")
    painter.end()
    return picture


def item_pixmap(item_id: str, size: int = 32) -> QPixmap:
    return _pixmap(str(item_id).casefold(), max(8, min(256, int(size))))


def item_icon(item_id: str) -> QIcon:
    return QIcon(item_pixmap(item_id, 64))
