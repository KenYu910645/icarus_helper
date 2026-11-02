"""Build-bound, read-only cached animal stats; never starts or calls the game.

Offsets are verified against build 26313227's native getters/reflection metadata.
Synthetic decoding and one real selected world were verified read only. Values
have not yet been compared one by one against the visible game UI.
No raw memory, names, account identifiers or process addresses are returned.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import re
import struct
import sys
import time

BUILD = "26313227"
EXE_HASH = "195e4883deda64be9dfe0bbbd876adce359c93c083512995bcc4a0383ba86c98"
PROCESS_NAME = "Icarus-Win64-Shipping.exe"
GOBJECT_CHUNKS = 0x63AACF0
GOBJECT_COUNT = 0x63AAD04
FNAME_POOL = 0x636E980
FNAME_READY = 0x636E978
ITEM_SIZE = 24
CHUNK_SIZE = 65536
MAX_OBJECTS = 1_000_000
MAX_READ_BYTES = 64 * 1024 * 1024
MAX_READ_SECONDS = 12.0


class Unavailable(ValueError):
    pass


class BudgetExceeded(Unavailable):
    """Abort the entire scan; incomplete duplicate detection is never success."""


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _result(status, reason):
    return {"status": status, "reason": reason, "sample_time": datetime.now(timezone.utc).isoformat(),
            "world_verified": False, "animals": {},
            "coverage": {"source": "遊戲程序的唯讀狀態快取", "runtime_validation": "原生規則／合成記憶體／真實選定世界唯讀已通過；未與遊戲UI逐項比對",
                         "snapshot_positions_are_live": False}}


def _identity(snapshot):
    info = _get(snapshot, "prospect_info", {})
    seed, map_key = _get(snapshot, "game_state_seed"), _get(snapshot, "map_key", "")
    if not isinstance(info, dict) or not isinstance(seed, int) or isinstance(seed, bool) or not isinstance(map_key, str) or not map_key:
        raise Unavailable("缺少可核對的世界識別／地圖／種子。")
    fields = tuple(info.get(key) for key in ("ClaimedAccountID", "ProspectID", "ProspectDTKey"))
    if any(not isinstance(value, str) or not value for value in fields):
        raise Unavailable("缺少可核對的世界識別。")
    expected = hashlib.sha256(("|".join(fields) + "|" + str(seed)).encode("utf-8")).hexdigest()[:24]
    if _get(snapshot, "world_id") != expected or _get(snapshot, "is_shared_map", False):
        raise Unavailable("世界識別與快照不一致。")
    return (*fields, seed, map_key)


def _targets(snapshot):
    from .animals import ANIMAL_COMPONENTS, _identity as saved_identity, _ownership, load_animal_catalog
    from .animal_capacities import load_capacity_catalog
    species = load_animal_catalog().get("species", {})
    static, _ = load_capacity_catalog()
    rows, conflicts = {}, set()
    for record in _get(snapshot, "raw_records", []):
        if record.get("_component") not in ANIMAL_COMPONENTS or _ownership(record) not in ("owned", "explicit_tamed"):
            continue
        ident = saved_identity(record)
        definition = species.get(record.get("AISetupRowName"), {})
        actor_class = definition.get("actor_class", "")
        if not ident or not ident.isdigit() or not 0 < int(ident) < 2**32 or "." not in actor_class:
            continue
        capacity = static.get("species", {}).get(record.get("AISetupRowName"), {})
        row = {"class": actor_class.rsplit(".", 1)[-1],
               "stamina_applicable": bool(capacity.get("stamina_curve") or "stamina" in capacity.get("base_capacities", {}))}
        if ident in rows:
            conflicts.add(ident)
        rows[ident] = row
    for ident in conflicts:
        rows.pop(ident, None)
    return rows


class _View:
    def __init__(self, memory, base):
        self.memory, self.base = memory, base
        self.names, self.classes = {}, {}
        if self.read(base + FNAME_READY, 1) != b"\x01":
            raise Unavailable("遊戲名稱索引尚未初始化。")

    def read(self, address, size):
        if not isinstance(address, int) or not 0x10000 <= address < 0x800000000000 or not 0 < size <= 2 * 1024 * 1024:
            raise Unavailable("無效的遊戲資料位置或大小。")
        data = self.memory.read(address, size)
        if len(data) != size:
            raise Unavailable("遊戲資料尚未穩定。")
        return data

    def u64(self, address): return struct.unpack("<Q", self.read(address, 8))[0]
    def i32(self, address): return struct.unpack("<i", self.read(address, 4))[0]

    def fname(self, raw):
        index, number = struct.unpack("<II", raw)
        if index not in self.names:
            block, offset = index >> 16, (index & 65535) * 2
            current, cursor = struct.unpack("<II", self.read(self.base + FNAME_POOL + 8, 8))
            if not 0 <= block <= current < 8192 or not 0 <= cursor <= 0x20000:
                raise Unavailable("名稱索引超出範圍。")
            address = self.u64(self.base + FNAME_POOL + 0x10 + block * 8) + offset
            header = struct.unpack("<H", self.read(address, 2))[0]
            length, wide = header >> 6, bool(header & 1)
            size = length * (2 if wide else 1)
            if not 0 < length <= 1023 or offset + 2 + size > (cursor if block == current else 0x20000):
                raise Unavailable("名稱格式未知。")
            self.names[index] = self.read(address + 2, size).decode("utf-16le" if wide else "ascii")
        return self.names[index] + (f"_{number - 1}" if number else "")

    def object_name(self, obj): return self.fname(self.read(obj + 0x18, 8))

    def class_names(self, obj, cls=None):
        cls = self.u64(obj + 0x10) if cls is None else cls
        if cls not in self.classes:
            # Native ClassIsChildOf uses UStructBaseChain, not a guessed linked list.
            array, depth = struct.unpack("<Qi", self.read(cls + 0x30, 12))
            if not 0 <= depth < 64:
                raise Unavailable("遊戲類別繼承資訊未知。")
            chain = struct.unpack("<" + "Q" * (depth + 1), self.read(array, 8 * (depth + 1)))
            if chain[-1] != cls + 0x30:
                raise Unavailable("遊戲類別繼承資訊不一致。")
            self.classes[cls] = tuple(self.object_name(ptr - 0x30) for ptr in chain)
        return self.classes[cls]

    def string(self, address):
        pointer, length, capacity = struct.unpack("<Qii", self.read(address, 16))
        if not 1 <= length <= capacity <= 512:
            raise Unavailable("世界識別字串未知。")
        raw = self.read(pointer, length * 2)
        if raw[-2:] != b"\0\0":
            raise Unavailable("世界識別字串尚未穩定。")
        return raw[:-2].decode("utf-16le")

    def world(self, actor, identity):
        account, prospect, row, seed, map_key = identity
        level = self.u64(actor + 0x20)
        if "Level" not in self.class_names(level):
            raise Unavailable("動物未連接有效世界區域。")
        world = self.u64(level + 0xB8)
        if "World" not in self.class_names(world):
            raise Unavailable("動物世界類別無法確認。")
        world_name = self.object_name(world)
        if world_name.casefold() not in {map_key.casefold(), (map_key + "_DLC").casefold()}:
            raise Unavailable("遊戲地圖與所選快照不同。")
        state = self.u64(world + 0x120)
        if "IcarusGameStateSurvival" not in self.class_names(state):
            raise Unavailable("遊戲尚未進入可確認的生存世界。")
        info = state + 0x498
        live = (self.string(info + 0x10), self.string(info), self.string(info + 0x28), self.i32(state + 0x360), map_key)
        if live != identity:
            raise Unavailable("遊戲世界識別或種子與所選快照不同。")
        return world


def _read_snapshot_from_memory(snapshot, memory, base, *, targets=None):
    """Decode one bounded read source; also used with synthetic memory in tests."""
    result = _result("unavailable", "尚無可確認的遊戲動物資料。")
    identity = _identity(snapshot)
    targets = _targets(snapshot) if targets is None else targets
    result["coverage"]["expected_animals"] = len(targets)
    if not targets:
        result.update(status="unavailable", reason="這份快照沒有可唯一配對的受支援動物。")
        return result
    view = _View(memory, base)
    count = view.i32(base + GOBJECT_COUNT)
    chunks = view.u64(base + GOBJECT_CHUNKS)
    if not 0 < count <= MAX_OBJECTS:
        raise Unavailable("遊戲物件索引超出已支援範圍。")
    objects, duplicates, worlds = {}, set(), set()
    target_classes = {row["class"] for row in targets.values()}
    rejected, visited = Counter(), 0
    matched_classes = matched_guids = 0
    started = time.monotonic()
    for start in range(0, count, CHUNK_SIZE):
        chunk = view.u64(chunks + (start // CHUNK_SIZE) * 8)
        length = min(CHUNK_SIZE, count - start)
        items = view.read(chunk, length * ITEM_SIZE)
        for slot in range(length):
            if time.monotonic() - started > MAX_READ_SECONDS:
                raise BudgetExceeded("唯讀檢查逾時；沒有套用不完整的動物資料。")
            visited += 1
            memory.object_slots_checked = visited
            obj, flags = struct.unpack_from("<QI", items, slot * ITEM_SIZE)
            if not obj or flags & 0x30000000:
                continue
            try:
                header = view.read(obj, 0x28)
                if struct.unpack_from("<i", header, 0xC)[0] != start + slot:
                    continue
                classes = view.class_names(obj, struct.unpack_from("<Q", header, 0x10)[0])
                if "IcarusCharacter" not in classes or classes[-1] not in target_classes:
                    continue
                matched_classes += 1
                if view.object_name(obj).startswith("Default__"):
                    continue
                recorder, state = view.u64(obj + 0x578), view.u64(obj + 0x5A8)
                if "ActorStateRecorderComponent" not in view.class_names(recorder) or view.u64(recorder + 0x20) != obj:
                    raise Unavailable("動物存檔識別來源不明。")
                ident = str(struct.unpack("<I", view.read(recorder + 0x140, 4))[0])
                if ident not in targets or classes[-1] != targets[ident]["class"]:
                    continue
                matched_guids += 1
                if "CharacterState" not in view.class_names(state) or view.u64(state + 0x20) != obj:
                    raise Unavailable("動物狀態來源不明。")
                world = view.world(obj, identity)
                stable = None
                for _ in range(3):
                    # All current/max fields come from the same short repeated sample.
                    a = (view.read(state + 0x1D8, 8), view.read(state + 0x278, 8))
                    b = (view.read(state + 0x1D8, 8), view.read(state + 0x278, 8))
                    current_item = view.read(chunk + slot * ITEM_SIZE, ITEM_SIZE)
                    if current_item != items[slot * ITEM_SIZE:(slot + 1) * ITEM_SIZE] or struct.unpack_from("<I", current_item, 8)[0] & 0x30000000:
                        raise Unavailable("動物物件已更新或移除，保留原顯示。")
                    if a == b and view.read(obj, 0x28) == header and view.u64(obj + 0x5A8) == state and view.u64(obj + 0x578) == recorder:
                        if str(struct.unpack("<I", view.read(recorder + 0x140, 4))[0]) == ident and view.world(obj, identity) == world:
                            stable = a
                            break
                if stable is None:
                    raise Unavailable("動物狀態持續變動，保留原顯示。")
                current_health, maximum_health = struct.unpack("<ii", stable[0])
                current_stamina, maximum_stamina = struct.unpack("<ii", stable[1])
                if not 0 <= current_health <= 1_000_000_000 or not 0 < maximum_health <= 1_000_000_000:
                    raise Unavailable("生命狀態格式未知。")
                values = {"health": {"value": current_health, "maximum": maximum_health}}
                if targets[ident]["stamina_applicable"]:
                    if not 0 <= current_stamina <= 1_000_000_000 or not 0 < maximum_stamina <= 1_000_000_000:
                        raise Unavailable("耐力狀態格式未知。")
                    values["stamina"] = {"value": current_stamina, "maximum": maximum_stamina}
                if ident in objects:
                    duplicates.add(ident)
                objects[ident] = values
                worlds.add(world)
            except BudgetExceeded:
                raise
            except (Unavailable, OSError, UnicodeError, struct.error) as error:
                rejected[str(error) if isinstance(error, Unavailable) else "unknown_object_layout"] += 1
    result["coverage"].update(object_slots_checked=visited, matching_actor_classes=matched_classes,
                              matching_saved_guids=matched_guids, rejected_objects=dict(rejected), scan_complete=True)
    # A process can retain stale worlds during travel. Never merge multiple worlds.
    if len(worlds) != 1:
        result["reason"] = "遊戲世界無法唯一核對；沒有套用即時數值。"
        return result
    for ident in duplicates:
        objects.pop(ident, None)
    result.update(status="ok" if len(objects) == len(targets) else "partial",
                  reason="已核對所選世界的遊戲狀態快取。" if objects else "沒有可唯一配對的動物狀態。",
                  world_verified=bool(objects), animals=objects)
    result["coverage"].update(sampled_animals=len(objects), object_slots_checked=visited,
                              duplicate_guids=len(duplicates), rejected_objects=dict(rejected), scan_complete=True,
                              missing_animals=len(targets) - len(objects))
    return result


# This import deliberately stays below the pure decoder for portable synthetic tests.
from collections import Counter


def _kernel():
    if sys.platform != "win32":
        raise Unavailable("此來源僅支援 Windows。")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    api.ReadProcessMemory.restype = wintypes.BOOL
    return api


def _find_game_processes():
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("name", wintypes.WCHAR * 260)]
    api = _kernel()
    api.Process32FirstW.argtypes = api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32FirstW.restype = api.Process32NextW.restype = wintypes.BOOL
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise Unavailable("無法查詢遊戲是否執行。")
    try:
        entry, result = Entry(), []
        entry.dwSize = ctypes.sizeof(entry)
        success = api.Process32FirstW(handle, ctypes.byref(entry))
        while success:
            if entry.name.casefold() == PROCESS_NAME.casefold():
                result.append(entry.pid)
            success = api.Process32NextW(handle, ctypes.byref(entry))
        return result
    finally:
        api.CloseHandle(handle)


def _module(pid):
    class Module(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("module_id", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("global_usage", wintypes.DWORD), ("process_usage", wintypes.DWORD),
                    ("base", ctypes.c_void_p), ("size", wintypes.DWORD), ("module", wintypes.HMODULE),
                    ("name", wintypes.WCHAR * 256), ("path", wintypes.WCHAR * 260)]
    api = _kernel()
    api.Module32FirstW.argtypes = api.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Module)]
    api.Module32FirstW.restype = api.Module32NextW.restype = wintypes.BOOL
    handle = api.CreateToolhelp32Snapshot(0x18, pid)
    if handle == ctypes.c_void_p(-1).value:
        raise Unavailable("無法取得遊戲版本資訊。")
    try:
        entry = Module()
        entry.dwSize = ctypes.sizeof(entry)
        success = api.Module32FirstW(handle, ctypes.byref(entry))
        while success:
            if entry.name.casefold() == PROCESS_NAME.casefold():
                return Path(entry.path), entry.base
            success = api.Module32NextW(handle, ctypes.byref(entry))
        raise Unavailable("找不到已驗證的遊戲主程式。")
    finally:
        api.CloseHandle(handle)


@lru_cache(maxsize=4)
def _file_hash(path, modified, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validate_executable(path):
    stat = path.stat()
    if _file_hash(str(path), stat.st_mtime_ns, stat.st_size) != EXE_HASH:
        raise Unavailable("遊戲版本尚未通過唯讀狀態來源驗證。")
    manifest = path.parents[5] / "appmanifest_1149460.acf"
    match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8"))
    if not match or match[1] != BUILD:
        raise Unavailable("Steam 遊戲版本與已驗證來源不同。")


class _WindowsMemory:
    def __init__(self, pid, expected_path):
        self.api = _kernel()
        # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ; no write/debug rights.
        self.handle = self.api.OpenProcess(0x1000 | 0x10, False, pid)
        if not self.handle:
            raise Unavailable("遊戲不允許唯讀查詢；保留存檔資料。")
        try:
            # A PID may be reused between Toolhelp enumeration and OpenProcess.
            self.api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            self.api.QueryFullProcessImageNameW.restype = wintypes.BOOL
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not self.api.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(length)) or Path(buffer.value).resolve() != expected_path.resolve():
                raise Unavailable("遊戲程序識別已改變，未讀取其他程序。")
        except Exception:
            self.close()
            raise
        self.started, self.bytes, self.calls, self.object_slots_checked = time.monotonic(), 0, 0, 0

    def read(self, address, size):
        self.bytes += size
        self.calls += 1
        if self.bytes > MAX_READ_BYTES or time.monotonic() - self.started > MAX_READ_SECONDS:
            raise BudgetExceeded("遊戲唯讀查詢已達本次上限。")
        buffer = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        if not self.api.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(received)) or received.value != size:
            raise Unavailable("遊戲資料無法穩定讀取。")
        return buffer.raw

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def read_live_animal_stats(snapshot):
    """Read only the named ICARUS process, after exact version and world gates."""
    memory = None
    try:
        _identity(snapshot)
        pids = _find_game_processes()
        if not pids:
            return _result("not_running", "遊戲未執行；目前顯示存檔資料。")
        if len(pids) != 1:
            return _result("unavailable", "偵測到多個遊戲程序，無法唯一確認來源。")
        path, base = _module(pids[0])
        _validate_executable(path)
        memory = _WindowsMemory(pids[0], expected_path=path)
        result = _read_snapshot_from_memory(snapshot, memory, base)
        result["coverage"].update(read_bytes=memory.bytes, read_calls=memory.calls,
                                  read_seconds=round(time.monotonic() - memory.started, 3))
        return result
    except (Unavailable, OSError, ValueError, IndexError, TypeError, AttributeError, struct.error) as error:
        reason = str(error) if isinstance(error, Unavailable) else "遊戲唯讀狀態來源暫時無法核對。"
        result = _result("unavailable", reason)
        if memory is not None:
            result["coverage"].update(read_bytes=getattr(memory, "bytes", 0), read_calls=getattr(memory, "calls", 0),
                                      object_slots_checked=getattr(memory, "object_slots_checked", 0))
        return result
    finally:
        if memory is not None:
            memory.close()
