"""Check shipped native contracts independently of synthetic parser fixtures."""
import math
from pathlib import Path
import re

import pytest

from icarus_helper.animals import _level, load_animal_catalog


@pytest.mark.parametrize("species,xp,expected", [
    ("Mount_Moa", 943, 0), ("Mount_Moa", 944, 1),
    ("Mount_Moa", 162560, 31), ("Mount_Moa", 174799, 31),
    ("Mount_Moa", 174800, 32), ("Mount_Horse", 143925, 30),
    ("Cow", 1236, 0), ("Cow", 1994, 0), ("Cow", 1995, 1),
    ("Cow", 250144, 25), ("Cow", 1000000, 25),
])
def test_native_level_boundaries_and_species_growth(species, xp, expected):
    catalog = load_animal_catalog()
    value, _ = _level({"Experience": xp}, catalog, catalog["species"][species], 999)
    assert value == expected


def test_shipped_catalog_provenance_and_no_personal_source_paths():
    catalog = load_animal_catalog()
    assert catalog["game_build"] == "26313227"
    assert catalog["enums"]["sex"]["0"] == "Unknown"
    assert catalog["enums"]["sex"]["1"] == "Female"
    assert catalog["enums"]["sex"]["2"] == "Male"
    assert catalog["species"]["Mount_Horse"]["english"] == "Terrenus"
    for source in catalog["sources"]:
        assert not Path(source["path"]).is_absolute()
        assert "Users" not in source["path"] and "PlayerData" not in source["path"]
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
    for curve in catalog["level_rule"]["curves"].values():
        assert len(curve["thresholds"]) == curve["max_level"] + 1
        assert all(math.isfinite(value) for value in curve["thresholds"])
