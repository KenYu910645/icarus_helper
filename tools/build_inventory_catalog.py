"""Build compact inventory metadata from the installed, read-only game files.

Full extracted tables stay in the APP cache. Only item identifiers, display names,
classification and source hashes enter the distributable catalog.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from catalog_inventory import CACHE, GAME, REPAK, require_validated_build

TABLES = ["Items/D_ItemsStatic.json", "Traits/D_Itemable.json", "Inventory/D_InventoryID.json"]
METALS = {
    "Metal_Ore": ("iron", "Iron", "ore"), "Refined_Metal": ("iron", "Iron", "ingot"),
    "Copper_Ore": ("copper", "Copper", "ore"), "Refined_Copper": ("copper", "Copper", "ingot"),
    "Gold_Ore": ("gold", "Gold", "ore"), "Refined_Gold": ("gold", "Gold", "ingot"),
    "Bauxite": ("aluminium", "Aluminium", "ore"), "Aluminium": ("aluminium", "Aluminium", "ingot"),
    "Platinum_Ore": ("platinum", "Platinum", "ore"), "Platinum_Ingot": ("platinum", "Platinum", "ingot"),
    "Titanium_Ore": ("titanium", "Titanium", "ore"), "Titanium_Ingot": ("titanium", "Titanium", "ingot"),
    "Steel_Ingot": ("steel", "Steel", "ingot"), "Steel_Bloom": ("steel", "Steel", "processed"),
    "Cold_Steel_Ingot": ("cold_steel", "Cold Steel", "ingot"),
    "Cold_Steel_Bloom": ("cold_steel", "Cold Steel", "processed"),
    "Lithium_Ore": ("lithium", "Lithium", "ore"),
}


def display_name(value: str, fallback: str) -> str:
    quoted = re.findall(r'"((?:\\.|[^"\\])*)"', value)
    if not quoted:
        return fallback.replace("_", " ")
    return quoted[-1].replace("\\'", "'").replace('\\"', '"').replace("\\n", " ")


def classify(row: dict, english: str) -> tuple[str, str, str, str]:
    key = row["Name"]
    if key in METALS:
        material, label, form = METALS[key]
        return "metals", material, label, form
    tags = [t["TagName"] for t in row.get("Generated_Tags", {}).get("GameplayTags", [])]
    has = lambda *prefixes: any(t.startswith(prefixes) for t in tags)
    if has("Item.Resource.Ore") or key in {"Stone", "Sand", "Ice", "Salt", "Charcoal"}:
        category = "minerals"
    elif has("Item.Consumable.Food", "Item.AnimalFeed"):
        category = "food"
    elif has("Item.Medicine"):
        category = "medical"
    elif has("Item.Tool", "Item.Weapon", "Item.Ammo"):
        category = "tools"
    elif has("Item.Armor", "Item.Backpack", "Item.Envirosuit", "Item.Attachment"):
        category = "equipment"
    elif has("Item.Resource", "Item.Plant"):
        category = "materials"
    elif has("Item.Building", "Item.Decoration", "Item.Deployable", "Item.Bench"):
        category = "building"
    else:
        category = "other"
    return category, key, english, "other"


def main() -> None:
    require_validated_build()
    source = CACHE / "inventory_source"
    pak = GAME / "Data/data.pak"
    paths = subprocess.run([str(REPAK), "list", str(pak), "-s", ""], capture_output=True, text=True, check=True).stdout.splitlines()
    prefix = next(p for p in paths if p.endswith(TABLES[0])).removesuffix(TABLES[0])
    command = [str(REPAK), "unpack", str(pak), "-o", str(source), "-s", prefix, "-f", "-q"]
    for table in TABLES:
        command += ["-i", table]
    subprocess.run(command, check=True)

    # UE's reflected enum table stores pairs (name pointer, int64 value). Verify
    # the real numeric value, rather than inferring it from save slot contents.
    import pefile
    executable = GAME.parent / "Binaries/Win64/Icarus-Win64-Shipping.exe"
    binary = executable.read_bytes()
    pe = pefile.PE(str(executable), fast_load=True)
    enum = {}
    for name in ["Durability", "ItemableStack", "InventoryContainer_LinkedInventoryId"]:
        offset = binary.index(("EDynamicItemProperties::" + name).encode())
        pointer = struct.pack("<Q", pe.OPTIONAL_HEADER.ImageBase + pe.get_rva_from_offset(offset))
        references = [m.start() for m in re.finditer(re.escape(pointer), binary)]
        values = {struct.unpack_from("<q", binary, p + 8)[0] for p in references}
        if len(values) != 1:
            raise ValueError(f"Ambiguous reflected enum {name}: {values}")
        enum[name] = values.pop()
    if enum != {"Durability": 6, "ItemableStack": 7, "InventoryContainer_LinkedInventoryId": 13}:
        raise ValueError(f"Unsupported dynamic item layout: {enum}")

    tables = {name: json.loads((source / name).read_text(encoding="utf-8")) for name in TABLES}
    traits = {r["Name"]: r for r in tables[TABLES[1]]["Rows"]}
    default_stack = tables[TABLES[1]]["Defaults"]["MaxStack"]
    items = {}
    for row in tables[TABLES[0]]["Rows"]:
        key = row["Name"]
        trait = traits.get(row.get("Itemable", {}).get("RowName"), {})
        if not trait:
            continue
        english = display_name(trait.get("DisplayName", ""), key)
        category, material, label, form = classify(row, english)
        name = english
        items[key] = {"name": name, "english": english, "category": category,
                      "material": material, "material_label": label, "form": form,
                      "max_stack": trait.get("MaxStack", default_stack)}
    result = {"schema_version": 1, "game_build": "26313227", "verified_at": "2026-11-01",
              "dynamic_properties": enum,
              "sources": [{"path": name, "sha256": hashlib.sha256((source / name).read_bytes()).hexdigest()} for name in TABLES],
              "executable_sha256": hashlib.sha256(binary).hexdigest(),
              "inventory_ids": {str(i): r["Name"] for i, r in enumerate(tables[TABLES[2]]["Rows"])},
              "items": items}
    output = Path(__file__).resolve().parents[1] / "icarus_helper/data/inventory_items.json"
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(items)} item definitions; verified {enum}")


if __name__ == "__main__":
    main()
