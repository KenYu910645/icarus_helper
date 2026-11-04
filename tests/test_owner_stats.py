"""Persistent owner profiles: exact account/slot, native progression and scope."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from icarus_helper import owner_stats
from icarus_helper.owner_stats import load_owner_stat_catalog, owner_key, summarize_owner_stats
from icarus_helper.saves import SaveChangedError


ACCOUNT = "11111111111111111"
OTHER = "22222222222222222"


def tame(account=ACCOUNT, slot=1, name="同名Owner"):
    return {"_component": "/Script/Icarus.IcarusMountCharacterRecorderComponent", "OwnerCharacterID": {"PlayerID": account, "ChrSlot": slot},
            "OwnerName": name, "BoolVariables": [{"VariableName": "bIsWildTame", "bVariable": False}]}


def player(account=ACCOUNT, slot=1, inventories=None, modifiers=None):
    return {"_component": "/Script/Icarus.PlayerStateRecorderComponent", "PlayerCharacterID": {"PlayerID": account, "ChrSlot": slot},
            "SavedInventories": [] if inventories is None else inventories, "Modifiers": [] if modifiers is None else modifiers}


def snapshot(root, records=None, world="世界甲"):
    return {"world_id": world, "map_name": "Prometheus", "source_path": root / ACCOUNT / "Prospects/世界.json",
            "sha256": "a" * 64, "saved_at": datetime(2026, 11, 2, tzinfo=timezone.utc),
            "raw_records": [tame(), player()] if records is None else records}


def profiles(root, *, account=ACCOUNT, slot=1, talents=None, points=1300, dog=None, account_talents=None):
    path = root / account
    path.mkdir(parents=True, exist_ok=True)
    character = {"CharacterName": "測試角色", "ChrSlot": slot, "Talents": talents if talents is not None else
                 [{"RowName": "Husbandry_TameHealth", "Rank": 3}, {"RowName": "Husbandry_Stamina", "Rank": 1}]}
    (path / "Characters.json").write_bytes(json.dumps({"Characters.json": [json.dumps(character, ensure_ascii=False)]}, ensure_ascii=False).encode("utf16"))
    (path / "Profile.json").write_text(json.dumps({"UserID": account, "Talents": account_talents or []}), encoding="utf8")
    tracking = [{"BestiaryGroup": {"RowName": "Horse"}, "NumPoints": points}]
    if dog is not None:
        tracking.append({"BestiaryGroup": {"RowName": "Dog"}, "NumPoints": dog})
    (path / "BestiaryData.json").write_text(json.dumps({"BestiaryTracking": tracking}), encoding="utf8")
    return path


def only(result, account=ACCOUNT, slot=1):
    return result["owners"][owner_key((account, slot))]


def test_complete_persistent_sources_include_character_and_account_bestiary_but_are_not_live(tmp_path):
    root = tmp_path / "中文Owner"
    account = profiles(root)
    before = {path.name: path.read_bytes() for path in account.iterdir()}
    result = summarize_owner_stats(snapshot(root))
    owner = only(result)
    assert owner["known_percent"] == owner["percent"] == {"health": 30, "stamina": 10}
    assert owner["persistent_complete"] == {"health": True, "stamina": True}
    assert owner["live_complete"] is False
    assert [component["stats"] for component in owner["components"]] == [{"health": 30}, {"stamina": 5}, {"stamina": 5}]
    independent = [source for source in owner["sources"] if not source["same_snapshot"]]
    assert {source["kind"] for source in independent} == {"Characters.json", "Profile.json", "BestiaryData.json"}
    assert all(source["mtime"].endswith("+00:00") and len(source["sha256"]) == 64 for source in independent)
    assert next(source for source in owner["sources"] if source["same_snapshot"])["sha256"] == "a" * 64
    assert {path.name: path.read_bytes() for path in account.iterdir()} == before
    public = json.dumps(result, ensure_ascii=False)
    assert ACCOUNT not in public and str(root) not in public


@pytest.mark.parametrize("points,dog,expected", [(519, None, 5), (520, None, 10), (1300, 599, 10), (1300, 600, 20), (0, 0, 5), (-1, None, 5)])
def test_bestiary_native_thresholds_and_absent_groups(tmp_path, points, dog, expected):
    profiles(tmp_path, points=points, dog=dog)
    result = only(summarize_owner_stats(snapshot(tmp_path)))
    assert result["percent"]["stamina"] == expected
    assert result["persistent_complete"]["stamina"]


def test_same_name_different_accounts_and_slots_never_inherit_another_profile(tmp_path):
    profiles(tmp_path)
    world = snapshot(tmp_path, [tame(), player(), tame(OTHER), player(OTHER), tame(slot=0), player(slot=0)])
    result = summarize_owner_stats(world)
    assert only(result)["percent"] == {"health": 30, "stamina": 10}
    for account, slot in ((OTHER, 1), (ACCOUNT, 0)):
        owner = only(result, account, slot)
        assert owner["known_percent"] == {"health": None, "stamina": None}
        assert not any(owner["persistent_complete"].values())


def test_explicit_world_bound_profile_roots_and_exact_profile_identity(tmp_path):
    original = tmp_path / "來源"
    selected = profiles(tmp_path / "另選")
    world = snapshot(original)
    missing = summarize_owner_stats(world, profile_roots=[{"world_id": "其他世界", "path": selected}])
    assert only(missing)["percent"]["health"] is None
    assert missing["coverage"]["warnings"]
    found = summarize_owner_stats(world, profile_roots=[{"world_id": world["world_id"], "path": selected}])
    assert only(found)["percent"]["stamina"] == 10
    # The profile's own account identity wins over a conveniently named folder.
    (selected / "Profile.json").write_text(json.dumps({"UserID": OTHER, "Talents": []}), encoding="utf8")
    mismatch = summarize_owner_stats(world, profile_roots=[selected])
    assert only(mismatch)["known_percent"]["health"] is None


def test_profile_read_changes_and_unknown_talent_never_become_zero_or_complete(tmp_path, monkeypatch):
    profiles(tmp_path)
    stable = owner_stats._read_stable
    def changing(path):
        if path.name == "Characters.json":
            raise SaveChangedError("synthetic partial write")
        return stable(path)
    monkeypatch.setattr(owner_stats, "_read_stable", changing)
    result = only(summarize_owner_stats(snapshot(tmp_path)))
    assert result["percent"] == {"health": None, "stamina": None}
    assert any(source["status"] == "unstable" for source in result["sources"])
    monkeypatch.setattr(owner_stats, "_read_stable", stable)
    profiles(tmp_path, talents=[{"RowName": "UNKNOWN_FUTURE_TALENT", "Rank": 1}])
    unknown = only(summarize_owner_stats(snapshot(tmp_path)))
    assert not any(unknown["persistent_complete"].values())
    assert unknown["percent"] == {"health": None, "stamina": None}


def test_complete_talent_lists_are_scanned_and_native_rank_is_not_accumulated(tmp_path):
    catalog = deepcopy(load_owner_stat_catalog())
    base = catalog["targets"]["health"]["base"]
    catalog["talents"]["Unrelated_Name_Still_Grants_Owner_Stat"] = [{base: 3}, {base: 9}]
    profiles(tmp_path, talents=[{"RowName": "Unrelated_Name_Still_Grants_Owner_Stat", "Rank": 2}], account_talents=[])
    result = only(summarize_owner_stats(snapshot(tmp_path), catalog=catalog))
    assert result["percent"]["health"] == 9
    assert result["persistent_complete"]["health"]


def test_casefold_fname_matching_is_exact_and_unknown_effect_stays_unknown(tmp_path):
    profiles(tmp_path, talents=[{"RowName": "husbandry_stamina", "Rank": 1}])
    known = only(summarize_owner_stats(snapshot(tmp_path, [tame(), player(modifiers=[{"RowName": "well_rested"}])])))
    assert known["percent"]["stamina"] == 10
    unknown = only(summarize_owner_stats(snapshot(tmp_path, [tame(), player(modifiers=[{"RowName": "unknown_new_effect"}])])))
    assert unknown["known_percent"]["stamina"] == 10
    assert unknown["percent"]["stamina"] is None and not unknown["live_complete"]


def test_diminishing_equipment_only_blocks_affected_channel(tmp_path):
    profiles(tmp_path)
    item = {"Location": 0, "ItemStaticData": "Meta_Module_Taming", "AdditionalStats": [], "Alterations": [], "LivingItemSlots": []}
    world = snapshot(tmp_path, [tame(), player(inventories=[{"InventoryID": 11, "Slots": [item]}])])
    owner = only(summarize_owner_stats(world))
    assert owner["percent"]["health"] == 30
    assert owner["percent"]["stamina"] is None
    assert owner["candidate_components"][0]["trait"] == "Module_Taming"


def test_negative_owner_percentage_is_not_subtracted_from_animal(tmp_path):
    catalog = deepcopy(load_owner_stat_catalog())
    catalog["talents"]["NegativeOwner"] = [{catalog["targets"]["health"]["base"]: -10}]
    profiles(tmp_path, talents=[{"RowName": "NegativeOwner", "Rank": 1}], points=0)
    result = only(summarize_owner_stats(snapshot(tmp_path), catalog=catalog))
    assert result["percent"]["health"] == -10
    assert result["animal_percent"]["health"] == 0


def test_conflicting_world_player_records_and_profiles_are_not_arbitrarily_selected(tmp_path):
    selected = profiles(tmp_path / "第一份")
    conflict = profiles(tmp_path / "第二份", points=0)
    result = only(summarize_owner_stats(snapshot(tmp_path), profile_roots=[selected, conflict]))
    assert result["known_percent"] == {"health": None, "stamina": None}
    original = player()
    second = deepcopy(original)
    second["Modifiers"] = [{"RowName": "Well_Rested"}]
    world = snapshot(tmp_path, [tame(), original, second])
    duplicate = only(summarize_owner_stats(world, profile_roots=[selected]))
    assert duplicate["known_percent"] == {"health": 30, "stamina": 10}
    assert duplicate["percent"] == {"health": None, "stamina": None}


def test_shared_map_does_not_read_or_return_private_owner_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_stats, "_read_profile", lambda path: pytest.fail("shared map must not read profiles"))
    result = summarize_owner_stats({**snapshot(tmp_path), "is_shared_map": True})
    assert result["owners"] == {} and result["coverage"]["status"] == "unavailable"


def test_owner_catalog_has_primary_hashes_and_complete_current_input_keys():
    catalog = load_owner_stat_catalog()
    assert len(catalog["sources"]) == 12
    assert all(len(source["sha256"]) == 64 for source in catalog["sources"])
    assert len(catalog["talents"]) == 2319 and len(catalog["items"]) == 3090
    assert catalog["starting_stats"]["Base_Stats"] == {}
    assert catalog["rules"]["owner_derived"]["mode"] == "base_passthrough"


def test_unverified_owner_rules_never_produce_a_complete_persistent_percentage(tmp_path):
    profiles(tmp_path)
    catalog = deepcopy(load_owner_stat_catalog())
    catalog["rules"]["bestiary"]["verified"] = False
    result = summarize_owner_stats(snapshot(tmp_path), catalog=catalog)
    assert result["owners"] == {} and result["coverage"]["status"] == "unavailable"


def test_owner_stat_sum_overflow_is_unknown_for_only_the_affected_channel(tmp_path):
    catalog = deepcopy(load_owner_stat_catalog())
    stat = catalog["targets"]["health"]["base"]
    catalog["talents"]["HugeOne"] = [{stat: 2**31 - 1}]
    catalog["talents"]["HugeTwo"] = [{stat: 1}]
    profiles(tmp_path, talents=[{"RowName": "HugeOne", "Rank": 1}, {"RowName": "HugeTwo", "Rank": 1}], points=0)
    result = only(summarize_owner_stats(snapshot(tmp_path), catalog=catalog))
    assert result["percent"]["health"] is None and not result["persistent_complete"]["health"]
    assert result["percent"]["stamina"] == 0 and result["persistent_complete"]["stamina"]
