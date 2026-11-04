"""In-memory object locators only: no sampled game values or persistent files."""
from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time


FULL_SCAN_INTERVAL = 300.0


@dataclass(frozen=True)
class ObjectLocator:
    kind: str
    identity: str
    index: int
    item: bytes
    header: bytes
    actor: int
    recorder: int = 0
    state: int = 0
    world: int = 0


@dataclass(frozen=True)
class Inventory:
    key: tuple
    created: float
    locators: tuple[ObjectLocator, ...]
    object_count: int
    object_chunks: tuple[bytes, ...]


class ActorLocatorCache:
    """A single active process/world/target set, guarded across background callers.

    Every locator must be revalidated by the reader before use. A completed full
    inventory may be reused for five minutes; its age is never extended by warm
    sampling. Each sample compares every slot's generation metadata and checks
    new/changed objects; a changed requested target set invalidates immediately.
    """
    def __init__(self, interval=FULL_SCAN_INTERVAL, clock=time.monotonic):
        self.interval, self.clock = interval, clock
        self.lock = threading.RLock()
        self.entry = None

    def get(self, key):
        with self.lock:
            entry = self.entry
            age = self.clock() - entry.created if entry is not None else -1
            if entry is None or entry.key != key or not 0 <= age < self.interval:
                self.entry = None
                return None
            return entry

    def put(self, key, locators, object_count, object_chunks):
        with self.lock:
            self.entry = Inventory(key, self.clock(), tuple(locators), object_count, tuple(object_chunks))

    def clear(self):
        with self.lock:
            self.entry = None

    def advance(self, previous, object_count, object_chunks):
        """Update generation metadata without extending the full-inventory TTL."""
        with self.lock:
            if self.entry is previous:
                self.entry = replace(previous, object_count=object_count, object_chunks=tuple(object_chunks))
