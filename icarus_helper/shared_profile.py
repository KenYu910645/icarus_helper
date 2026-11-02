"""Public map/base preset, separate from private saves and dynamic world state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path


SHARED_SOURCE = "shared-map:prometheus-home"
PROFILE_PATH = Path(__file__).resolve().parent / "data" / "share_profile.json"


def load_shared_profile(path: Path | None = None) -> dict:
    """Read only allowlisted public fields; never import user settings or a save."""
    data = json.loads((path or PROFILE_PATH).read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1 or data.get("map_name") != "Prometheus":
        raise ValueError("不支援的共用地圖設定")
    bases = []
    for index, row in enumerate(data.get("bases", [])):
        coordinates = {axis: float(row.get(axis, 0)) for axis in ("x", "y", "z")}
        if not all(math.isfinite(value) and abs(value) <= 100000000 for value in coordinates.values()):
            raise ValueError("共用基地座標無效")
        if any(abs(coordinates[axis]) > 403200 for axis in ("x", "y")):
            raise ValueError("共用基地不在已校準地圖範圍內")
        bases.append({"id": f"shared-base-{index + 1}", "name": str(row.get("name") or "我們家")[:80],
                      "kind": "base", **coordinates, "source": "使用者公開共用的基地位置",
                      "confidence": "使用者指定", "detail": "共用地圖預設位置；不代表其他世界的基地。"})
    if not bases:
        raise ValueError("共用地圖缺少基地位置")
    return {"schema_version": 1, "map_name": "Prometheus", "id": "prometheus-home", "bases": bases,
            "active_base": bases[0]["id"]}


@dataclass
class SharedMapContext:
    """An offline map context; deliberately has no save time, path or records."""
    map_name: str = "Prometheus"
    world_id: str = "shared-map:prometheus-home"
    name: str = "共用地圖（未連接主機存檔）"
    is_shared_map: bool = True
    source_path: None = None
    saved_at: None = None
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).astimezone())
    sha256: str = ""
    coverage: dict = field(default_factory=lambda: {"source_kind": "shared_map", "host_save_connected": False})
    warnings: list[str] = field(default_factory=lambda: ["未連接主機存檔；資源為靜態／候選點，沒有目前礦量、鑽機、玩家或庫存狀態。"])
    deployables: list = field(default_factory=list)
    players: list = field(default_factory=list)


def seed_shared_bases(settings, profile: dict, context: SharedMapContext):
    """Seed once in a shared-only namespace; later edits/deletions remain local."""
    config = settings.world(context.world_id)
    if not config.get("shared_profile_initialized"):
        config["bases"] = deepcopy(profile["bases"])
        config["active_base"] = profile["active_base"]
        config["shared_profile_initialized"] = True


def build_shared_payload() -> dict:
    from .catalog import load_catalog
    from .missions import build_missions

    profile = load_shared_profile()
    context = SharedMapContext(map_name=profile["map_name"])
    catalog = load_catalog(context.map_name)
    # Keep original static/candidate confidence and all unknown quantities. No
    # merge with a private save, no world seed and no device/player assertions.
    points = list(catalog.get("caves", [])) + list(catalog.get("resources", []))
    return {"snapshot": context, "catalog": catalog, "points": points,
            "missions": build_missions(context), "shared_profile": profile}
