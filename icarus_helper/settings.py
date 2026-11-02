"""Application-owned settings; never write to game directories."""
from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_VISIBLE_LAYERS = frozenset({"cave", "deep", "geyser", "exotic", "oil", "drill", "player"})
MAP_LAYERS = ("cave", "deep", "ore", "exotic", "geyser", "oil", "water", "plant", "tree", "deployable", "drill", "player", "resource")


def app_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "IcarusHelper"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "settings.json"
        self.error = ""
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if not isinstance(self.data, dict):
                raise ValueError("設定檔格式錯誤")
        except (ValueError, OSError) as exc:
            self.data = {}
            self.error = f"設定未能載入：{exc}；原檔保留，請先確認再儲存設定。"

    def save(self):
        if self.error:
            raise OSError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def world(self, world_id: str) -> dict:
        return self.data.setdefault("worlds", {}).setdefault(world_id, {"bases": [], "checklists": {}})

    def map_display(self) -> dict:
        stored = self.data.get("map_display", {})
        stored = stored if isinstance(stored, dict) else {}
        layers = stored.get("layers", {})
        layers = layers if isinstance(layers, dict) else {}
        return {"layers": {key: layers[key] if isinstance(layers.get(key), bool) else key in DEFAULT_VISIBLE_LAYERS for key in MAP_LAYERS},
                "show_candidates": stored.get("show_candidates") if isinstance(stored.get("show_candidates"), bool) else True}

