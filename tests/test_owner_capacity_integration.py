"""Owner source gating, exact handoff to capacities and Qt world isolation."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget, QWidget

from icarus_helper import animal_capacities, animals, owner_stats
from icarus_helper.animals_view import AnimalPanel, _SummaryWorker
from test_animals import CATALOG, ITEMS, tame, world
from test_animals_view import animal, summary


PRIVATE_A = "11111111111111111"
PRIVATE_B = "22222222222222222"


@pytest.fixture
def catalogs(monkeypatch):
    catalog = deepcopy(CATALOG)
    catalog["capacity_rule"] = {"verified": True}
    catalog["owner_capacity_rule"] = {"verified": True}
    monkeypatch.setattr(animals, "installed_game_build", lambda: "26313227")
    monkeypatch.setattr(animals, "load_item_catalog", lambda: deepcopy(ITEMS))
    monkeypatch.setattr(animals, "load_animal_catalog", lambda: deepcopy(catalog))
    return catalog


def owned(ident, account=PRIVATE_A, slot=1):
    return tame(ident, OwnerCharacterID={"PlayerID": account, "ChrSlot": slot}, OwnerName="同名飼養者")


def owner_info(account, slot, value):
    key = owner_stats.owner_key((account, slot))
    return {"owner_key": key, "owner_name": "同名飼養者", "known_percent": {"health": value, "stamina": value},
            "percent": {"health": value, "stamina": value},
            "persistent_complete": {"health": value is not None, "stamina": value is not None},
            "live_complete": False, "sources": [{"kind": "Characters.json", "sha256": "b" * 64,
                                                     "mtime": "2026-11-02T03:36:12+00:00", "same_snapshot": False}]}


def wait_for(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert predicate()


def test_owner_rule_gate_prevents_unrequested_profile_reads(catalogs, monkeypatch):
    catalogs["owner_capacity_rule"]["verified"] = False
    monkeypatch.setattr(owner_stats, "summarize_owner_stats", lambda *args, **kwargs: pytest.fail("unverified owner rule must not read profiles"))
    seen = []
    monkeypatch.setattr(animal_capacities, "reconstruct_capacities", lambda record, level, **kwargs: seen.append(kwargs.get("owner_info")) or {})
    result = animals.summarize_animals(world(owned(1)))
    assert len(result["animals"]) == 1 and "owner_stats" not in result
    assert seen == [None]


def test_owner_matching_uses_exact_account_and_slot_with_missing_remote_unknown(catalogs, monkeypatch):
    primary = owner_info(PRIVATE_A, 1, 30)
    other_slot = owner_info(PRIVATE_A, 2, 10)
    roots = [{"world_id": "synthetic-world", "path": "synthetic-explicit-owner-source"}]
    calls, inputs = [], {}
    def source(snapshot, profile_roots=None):
        calls.append((snapshot["world_id"], deepcopy(profile_roots)))
        return {"owners": {primary["owner_key"]: primary, other_slot["owner_key"]: other_slot}, "coverage": {"status": "partial"}}
    def capacities(record, level, owner_info=None):
        inputs[record["IcarusActorGUID"]] = deepcopy(owner_info)
        return {}
    monkeypatch.setattr(owner_stats, "summarize_owner_stats", source)
    monkeypatch.setattr(animal_capacities, "reconstruct_capacities", capacities)
    result = animals.summarize_animals(world(owned(1), owned(2, slot=2), owned(3, account=PRIVATE_B)), owner_profile_roots=roots)
    assert calls == [("synthetic-world", roots)]
    assert inputs[1]["percent"]["health"] == 30
    assert inputs[2]["percent"]["health"] == 10
    assert inputs[3] is None
    remote = next(row for row in result["animals"] if row["id"] == "3")
    assert remote["owner_capacity"] == {}
    assert "percent" not in remote["owner_capacity"]
    serialized = json.dumps(result)
    assert PRIVATE_A not in serialized and PRIVATE_B not in serialized


def test_shared_or_unsupported_world_never_reads_profiles(catalogs, monkeypatch):
    monkeypatch.setattr(owner_stats, "summarize_owner_stats", lambda *args, **kwargs: pytest.fail("shared or unsupported source must stop before owner reads"))
    shared = animals.summarize_animals(world(owned(1), is_shared_map=True))
    assert shared["animals"] == []
    invalid = animals.summarize_animals(world(owned(1), save_format_version=999))
    assert invalid["coverage"]["status"] == "unsupported_version"


def test_qt_worker_forwards_owner_roots_to_default_parser(monkeypatch):
    app = QApplication.instance() or QApplication([])
    roots = [{"world_id": "owner-world", "path": "synthetic-source"}]
    received = []
    def parser(snapshot, base, radius_m, owner_profile_roots):
        received.append((snapshot, base, radius_m, deepcopy(owner_profile_roots)))
        return summary(animal())
    monkeypatch.setattr(animals, "summarize_animals", parser)
    snapshot = {"world_id": "owner-world", "saved_at": datetime.now(timezone.utc)}
    worker = _SummaryWorker(3, snapshot, {"id": "home"}, 100, None, roots)
    completed = []
    worker.signals.success.connect(lambda generation, payload: completed.append((generation, payload)))
    worker.run()
    assert received == [(snapshot, {"id": "home"}, 100, roots)]
    assert completed[0][0] == 3


def test_qt_owner_information_shows_no_platform_identity_or_private_source_path():
    app = QApplication.instance() or QApplication([])
    panel = AnimalPanel()
    try:
        entry = animal()
        entry["owner_key"] = owner_stats.owner_key((PRIVATE_A, 1))
        entry["owner_capacity"] = owner_info(PRIVATE_A, 1, 30)
        entry["stats"][0].update(maximum=200, maximum_status="persistent", detail="已核對飼養者 +30%；角色資料為獨立時間。")
        panel.resize(1300, 800)
        panel.show()
        panel.set_summary(summary(entry))
        app.processEvents()
        visible = []
        for widget in [panel, *panel.findChildren(QWidget)]:
            visible.extend([widget.toolTip(), widget.accessibleName()])
            if isinstance(widget, QLabel):
                visible.append(widget.text())
            if isinstance(widget, QTableWidget):
                visible.extend(widget.item(row, col).text() for row in range(widget.rowCount()) for col in range(widget.columnCount()) if widget.item(row, col))
        text = "\n".join(visible)
        assert PRIVATE_A not in text and PRIVATE_B not in text and "PlayerData" not in text
        assert "同名" not in text or "飼養者" in text
    finally:
        panel.close()
        app.processEvents()


def test_qt_old_owner_job_cannot_restore_world_a_after_clear_and_world_b(monkeypatch):
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    received = []
    def parser(snapshot, base, radius_m, owner_profile_roots):
        current = snapshot["world_id"]
        received.append((current, deepcopy(owner_profile_roots)))
        if current == "world-a":
            entered.set()
            release.wait(3)
        entry = animal(current)
        entry["owner_key"] = owner_stats.owner_key((PRIVATE_A if current == "world-a" else PRIVATE_B, 1))
        entry["owner_capacity"] = owner_info(PRIVATE_A if current == "world-a" else PRIVATE_B, 1, 30 if current == "world-a" else None)
        return summary(entry)
    monkeypatch.setattr(animals, "summarize_animals", parser)
    panel = AnimalPanel()
    panel.show()
    try:
        first_roots = [{"world_id": "world-a", "path": "source-a"}]
        panel.owner_profile_roots = first_roots
        panel.set_snapshot({"world_id": "world-a", "saved_at": datetime.now(timezone.utc)}, None)
        wait_for(app, entered.is_set)
        panel.set_missing_source()
        assert panel.owner_profile_roots is None and panel.summary is None
        assert not panel.animal_cards and not panel.animal_map.map.points
        second_roots = [{"world_id": "world-b", "path": "source-b"}]
        panel.owner_profile_roots = second_roots
        panel.set_snapshot({"world_id": "world-b", "saved_at": datetime.now(timezone.utc)}, None)
        release.set()
        wait_for(app, lambda: panel.summary is not None and not panel.busy)
        assert [row["id"] for row in panel.summary["animals"]] == ["world-b"]
        assert panel.summary["animals"][0]["owner_capacity"]["percent"]["health"] is None
        assert received == [("world-a", first_roots), ("world-b", second_roots)]
        panel.clear()
        assert panel.owner_profile_roots is None and panel.summary is None
        assert not panel.owner_columns and not panel.animal_cards
    finally:
        release.set()
        panel.close()
        app.processEvents()
