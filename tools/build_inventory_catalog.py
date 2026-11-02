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
    "Metal_Ore": ("iron", "鐵", "ore"), "Refined_Metal": ("iron", "鐵", "ingot"),
    "Copper_Ore": ("copper", "銅", "ore"), "Refined_Copper": ("copper", "銅", "ingot"),
    "Gold_Ore": ("gold", "金", "ore"), "Refined_Gold": ("gold", "金", "ingot"),
    "Bauxite": ("aluminium", "鋁", "ore"), "Aluminium": ("aluminium", "鋁", "ingot"),
    "Platinum_Ore": ("platinum", "鉑", "ore"), "Platinum_Ingot": ("platinum", "鉑", "ingot"),
    "Titanium_Ore": ("titanium", "鈦", "ore"), "Titanium_Ingot": ("titanium", "鈦", "ingot"),
    "Steel_Ingot": ("steel", "鋼", "ingot"), "Steel_Bloom": ("steel", "鋼", "processed"),
    "Cold_Steel_Ingot": ("cold_steel", "冷鋼", "ingot"),
    "Cold_Steel_Bloom": ("cold_steel", "冷鋼", "processed"),
    "Lithium_Ore": ("lithium", "鋰", "ore"),
}
ZH = {
    "Stone": "石材", "Wood": "木材", "Wood_Refined": "精製木材", "Stick": "木棍",
    "Fiber": "纖維", "Leather": "皮革", "Fur": "毛皮", "Bone": "骨頭", "Rope": "繩索",
    "Sulfur": "硫磺", "Oxite": "氧礦", "Silica": "矽礦", "Coal_Ore": "煤礦", "Charcoal": "木炭",
    "Clay": "黏土", "Scoria": "火山渣", "Obsidian": "黑曜石", "Ice": "冰", "Salt": "鹽",
    "Sand": "沙", "Glass": "玻璃", "Concrete_Mix": "混凝土混合物", "Clay_Brick": "黏土磚",
    "Tree_Sap": "樹液", "Epoxy": "環氧樹脂", "Steel_Screw": "鋼螺絲", "Steel_Rebar": "鋼筋",
    "Iron_Nail": "鐵釘", "Copper_Nail": "銅釘", "Copper_Wire": "銅線", "Gold_Wire": "金線",
    "Carbon_Fiber": "碳纖維", "Composites": "複合材料", "Advanced_Leather": "高級皮革",
    "Raw_Iron_Wood": "結晶瘴氣", "Refined_Iron_Wood": "蒸餾瘴氣塗層", "Dirt": "泥土",
    "MetaResource": "異質", "Exotic_Red": "紅色異質", "Ruby_Ore": "結晶紅寶石",
    "Spoiled_Meat": "腐肉", "Spoiled_Plants": "腐敗植物", "Animal_Fat": "動物脂肪",
    "Honey": "蜂蜜", "Flour": "麵粉", "Basic_Fertilizer": "基礎肥料", "Animal_Silage": "動物青貯飼料",
    "Kit_Metal_Cupboard": "金屬櫃", "Metal_Crate_Small": "小型金屬箱",
    "Concrete_Furnace_V2": "混凝土熔爐", "Electric_Furnace_Large": "大型電熔爐",
    "Fabricator": "製造機", "Material_Processor": "材料處理機", "Refrigerator": "冰箱",
    "Cement_Mixer": "水泥攪拌機", "Rustic_Wardrobe": "鄉村衣櫃",
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
    return category, key, ZH.get(key, english), "other"


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
        name = ZH.get(key, english)
        if key in METALS:
            name = label + {"ore": "礦", "ingot": "錠", "processed": "坯"}[form]
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
