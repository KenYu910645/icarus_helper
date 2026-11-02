"""Build compact mission query coordinates from read-only extracted game data.

Preparation of map and blueprint metadata is documented in tools/README.md.
No game save is included; the runtime joins the currently selected world only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build(source: Path, output: Path, build_id: str = "26313227") -> dict:
    quests = read(source / "Quests/D_Quests.json")["Rows"]
    queries = read(source / "Quests/D_QuestQueries.json")["Rows"]
    refs = {r["blueprint"]: r["queries"] for r in read(source / "blueprint_location_queries.json")}
    markers = read(source / "Terrain_019_locations_raw.json")
    catalog = read(ROOT / "icarus_helper/data/missions.json")
    by_id = {q["Name"]: q for q in quests}
    ordered = {q["Name"]: index for index, q in enumerate(quests)}
    missions = {}
    for mission in catalog["missions"]:
        qids = sorted(mission.get("quest_ids", []), key=lambda name: ordered.get(name, 100000))
        stages = []
        for qid in qids:
            quest = by_id.get(qid, {})
            found = []
            query = quest.get("LocationQuery", {}).get("RowName")
            if query and query != "None":
                found.append(query)
            blueprint = quest.get("Class", "").split("/")[-1].split(".")[0]
            found.extend(refs.get(blueprint, []))
            for query in found:
                stage = next((s for s in stages if s["query"] == query), None)
                if stage is None:
                    stages.append({"query": query, "quest_ids": [qid]})
                elif qid not in stage["quest_ids"]:
                    stage["quest_ids"].append(qid)
        missions[mission["id"]] = {"map_name": mission["map_name"], "quest_ids": qids, "query_stages": stages}
    source_files = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ("Quests/D_Quests.json", "Quests/D_QuestQueries.json", "Terrain_019_locations_raw.json", "blueprint_location_queries.json")}
    data = {"schema_version": 1, "source_build": build_id, "map_name": "Prometheus", "generated_at": datetime.now(timezone.utc).isoformat(), "source_files": source_files, "markers": markers,
            "queries": {row["Name"]: [tag["TagName"] for tag in row.get("Query", {}).get("TagDictionary", [])] for row in queries}, "missions": missions}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markers": len(markers), "prometheus_missions_with_queries": sum(m["map_name"] == "Prometheus" and bool(m["query_stages"]) for m in missions.values()), "output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "icarus_helper/data/mission_locations.json")
    parser.add_argument("--build-id", default="26313227")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.build_id), ensure_ascii=False))
