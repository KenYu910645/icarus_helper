"""Shared, UI-independent records. All world coordinates use Unreal centimetres."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class SaveDescriptor:
    path: Path
    name: str
    map_name: str
    mtime: float
    prospect_key: str = ""


@dataclass
class Resource:
    id: str
    kind: str
    name: str
    resource_type: str
    x: float
    y: float
    z: float
    remaining: float | None = None
    remaining_unit: str = ""
    remaining_status: str = "Quantity unknown"
    drill_status: str = ""
    confidence: str = "Confirmed"
    source: str = "Game save"
    detail: str = ""
    actor_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def position(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z


@dataclass
class WorldSnapshot:
    source_path: Path
    world_id: str
    name: str
    map_name: str
    saved_at: datetime
    loaded_at: datetime
    sha256: str
    resources: list[Resource] = field(default_factory=list)
    deployables: list[Resource] = field(default_factory=list)
    players: list[Resource] = field(default_factory=list)
    mission_records: list[dict[str, Any]] = field(default_factory=list, repr=False)
    raw_records: list[dict[str, Any]] = field(default_factory=list, repr=False)
    prospect_info: dict[str, Any] = field(default_factory=dict, repr=False)
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_mtime: float = 0.0
    source_mtime_ns: int = 0
    source_size: int = 0
    map_key: str = ""
    prospect_key: str = ""
    difficulty: str = ""
    mission_state: dict[str, Any] = field(default_factory=dict, repr=False)
    base_candidates: list[Resource] = field(default_factory=list)
    cache_path: Path | None = None
    game_state_seed: int | None = None
    operation_account: dict[str, Any] = field(default_factory=dict, repr=False)

