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
        raise ValueError("Unsupported shared map profile")
    bases = []
    for index, row in enumerate(data.get("bases", [])):
        coordinates = {axis: float(row.get(axis, 0)) for axis in ("x", "y", "z")}
        if not all(math.isfinite(value) and abs(value) <= 100000000 for value in coordinates.values()):
            raise ValueError("Invalid shared base coordinates")
        if any(abs(coordinates[axis]) > 403200 for axis in ("x", "y")):
            raise ValueError("Shared base is outside calibrated map bounds")
        bases.append({"id": f"shared-base-{index + 1}", "name": str(row.get("name") or "Home")[:80],
                      "kind": "base", **coordinates, "source": "User-published shared base location",
                      "confidence": "User-designated", "detail": "Default shared map location; does not identify a base in another world."})
    if not bases:
        raise ValueError("Shared map has no base location")
    return {"schema_version": 1, "map_name": "Prometheus", "id": "prometheus-home", "bases": bases,
            "active_base": bases[0]["id"]}


@dataclass
class SharedMapContext:
    """An offline map context; deliberately has no save time, path or records."""
    map_name: str = "Prometheus"
    world_id: str = "shared-map:prometheus-home"
    name: str = "Shared map (no host save connected)"
    is_shared_map: bool = True
    source_path: None = None
    saved_at: None = None
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).astimezone())
    sha256: str = ""
    coverage: dict = field(default_factory=lambda: {"source_kind": "shared_map", "host_save_connected": False})
    warnings: list[str] = field(default_factory=lambda: ["No host save connected; resources are static or candidate locations. Current quantities, drills, players and inventory are unavailable."])
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
