"""Build a small derived mission catalogue from the installed, read-only data.pak.

Extracted raw tables stay in the user's local cache. This repository stores only
selected gameplay metadata and original English preparation guides.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MAPS = {"Terrain_016": "Olympus", "Terrain_017": "Styx", "Terrain_019": "Prometheus", "Terrain_021": "Elysium"}
TABLES = ["Prospects/D_ProspectList.json", "Factions/D_FactionMissions.json", "Quests/D_Quests.json", "Quests/D_DynamicQuests.json", "Talents/D_Talents.json"]
REQUIREMENTS_PATH = ROOT / "icarus_helper/data/mission_requirements.json"


def _requirement_amount(requirement: dict) -> str:
    variants = requirement.get("quantity_by_difficulty")
    if variants:
        return "; ".join(f"{name}: {amount}" for name, amount in variants.items())
    return str(requirement["quantity"])


def _apply_objective_flow(catalog: dict, overrides: list[dict]) -> None:
    """Correct prefix discovery using verified, mission-specific call graphs."""
    source_rows = {row["id"]: row for mission in catalog["missions"]
                   for key in ("objectives", "source_objectives", "catalog_only_objectives")
                   for row in mission.get(key, [])}
    by_mission = {row["mission_id"]: row for row in overrides}
    for mission in catalog["missions"]:
        if mission["id"] == "PRO_C_Research":
            continue  # Its conflicting item labels are handled below.
        rule = by_mission.get(mission["id"], {})
        had_catalog_only = bool(mission.get("catalog_only_objectives"))
        rows = {row["id"]: row for row in mission.get("source_objectives", [])
                + mission.pop("catalog_only_objectives", [])}
        for qid in rule.get("include_ids", []):
            if qid in source_rows:
                rows[qid] = deepcopy(source_rows[qid])
            elif qid in catalog["quest_texts"]:
                rows[qid] = {"id": qid, "text": catalog["quest_texts"][qid], "info": ""}
        excluded = {qid for qid in rows if qid in rule.get("exclude_ids", [])
                    or (rule.get("exclude_prefix") and qid.startswith(rule["exclude_prefix"]))}
        if rule or had_catalog_only:
            mission["source_objectives"] = [row for qid, row in rows.items() if qid not in excluded]
            mission["catalog_only_objectives"] = [row for qid, row in rows.items() if qid in excluded]
            for row in mission["source_objectives"]:
                row.pop("exclusion_evidence", None)
            for row in mission["catalog_only_objectives"]:
                row["exclusion_evidence"] = deepcopy(rule)
            mission["quest_ids"] = sorted((set(mission.get("quest_ids", [])) | set(rows)) - excluded)


def _apply_catalog_requirements(catalog: dict, requirements: dict) -> None:
    """Use only audited count pairs, with explicit difficulty variants.

    Timer, health and generated-item placeholders are not inferred as counts.
    MIASMIC's legacy-row and preparation rules are handled separately below.
    """
    pair = re.compile(r"\{0\}\s*/\s*\{1\}")
    for mission in catalog["missions"]:
        if mission["id"] == "PRO_C_Research":
            continue
        additions = {}
        # Remove our generated entries before reapplying a different source.
        mission["checklist"] = [row for row in mission.get("checklist", [])
                                if not row.get("verified_objective_target")]
        for key in ("objectives", "source_objectives"):
            for row in mission.get(key, []):
                template = row.get("source_text", row["text"])
                requirement = requirements.get(row["id"])
                if requirement and (requirement["source_text"] != template
                                    or mission["id"] not in requirement["evidence"].get("missions", [])):
                    requirement = None
                if requirement is None and "quantity_source" not in row:
                    continue
                previous = row["text"]
                row["source_text"] = template
                for field in ("required_quantity", "required_quantity_by_difficulty", "quantity_source"):
                    row.pop(field, None)
                row["text"] = template
                display = requirement.get("display_template", template) if requirement else template
                if requirement and pair.search(display):
                    label = _requirement_amount(requirement)
                    row["text"] = pair.sub("Required: " + label, display).strip()
                    row["quantity_source"] = deepcopy(requirement["evidence"])
                    if requirement.get("quantity_by_difficulty"):
                        row["required_quantity_by_difficulty"] = deepcopy(requirement["quantity_by_difficulty"])
                    else:
                        row["required_quantity"] = requirement["quantity"]
                    if requirement.get("prep_item") and not requirement.get("reuse_preparation_from"):
                        action = requirement.get("action", "objective")
                        additions[row["id"]] = {**prep(requirement["prep_item"], label, True,
                            requirement.get("preparation_note", f"{action} target for this stage; obtain during the mission as appropriate. Separate stage targets are not a combined departure total.")),
                            "quest_id": row["id"], "verified_objective_target": True}
                catalog["quest_texts"][row["id"]] = row["text"]
                # Generated fallback guides also quote the source objective.
                mission["guide"] = [step.replace(previous, row["text"]).replace(template, row["text"])
                                    for step in mission.get("guide", [])]
        for requirement in requirements.values():
            original = requirement.get("reuse_preparation_from")
            if original in additions and mission["id"] in requirement["evidence"].get("missions", []):
                additions[original]["note"] = requirement["evidence"]["preparation_note"]
        mission["checklist"] = list(additions.values()) + mission["checklist"]


def apply_verified_requirements(catalog: dict, evidence: dict | None = None) -> None:
    """Join audited fixed targets, never an invented current progress count.

    Only audited two-argument count templates are supported here. Other
    argument semantics must not be blindly formatted.
    The original template is retained for diagnostics and repeatable rebuilds.
    """
    if evidence is None:
        evidence = json.loads(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    compatible = (catalog.get("source_build") == evidence["source_build"]
                  and catalog.get("source_files", {}).get("Quests/D_Quests.json", {}).get("sha256")
                  == evidence["quest_table_sha256"])
    requirements = evidence["quests"] if compatible else {}
    _apply_objective_flow(catalog, evidence.get("mission_objective_overrides", []) if compatible else [])
    _apply_catalog_requirements(catalog, requirements)
    pair = re.compile(r"\(\{0\}\s*/\s*\{1\}\)")

    def objective(qid: str, template: str) -> tuple[str, dict | None]:
        requirement = requirements.get(qid)
        if requirement and requirement["source_text"] != template:
            requirement = None
        label = f"Required: {requirement['quantity']}" if requirement else "Quantity unconfirmed"
        display = requirement.get("display_template", template) if requirement else template
        return pair.sub(f"({label})", display).strip(), requirement

    for mission in catalog["missions"]:
        if mission["id"] != "PRO_C_Research":
            continue
        # Prefix-based discovery also finds unused legacy rows. Keep them for
        # diagnostics without presenting them as required deliveries.
        excluded = evidence.get("excluded_objectives", {}) if compatible else {}
        source_rows = mission.get("source_objectives", []) + mission.pop("catalog_only_objectives", [])
        mission["source_objectives"] = [row for row in source_rows if row["id"] not in excluded]
        mission["catalog_only_objectives"] = [row for row in source_rows if row["id"] in excluded]
        for row in mission["catalog_only_objectives"]:
            row["exclusion_evidence"] = deepcopy(excluded[row["id"]])
            template = row.get("source_text", row["text"])
            row["source_text"] = template
            catalog["quest_texts"][row["id"]] = (pair.sub("", template).strip()
                + " (Legacy catalog row; not a required delivery in the verified mission flow)")
        accepted = {}
        for key in ("objectives", "source_objectives"):
            for row in mission.get(key, []):
                template = row.get("source_text", row["text"])
                if not pair.search(template):
                    continue
                row["source_text"] = template
                row["text"], requirement = objective(row["id"], template)
                row["required_quantity"] = requirement["quantity"] if requirement else None
                row.pop("quantity_source", None)
                if requirement:
                    row["quantity_source"] = deepcopy(requirement["evidence"])
                    accepted[row["id"]] = requirement
                catalog["quest_texts"][row["id"]] = row["text"]

        # Quantities in Preparation and Objectives come from the same match.
        facilities = []
        for qid, item in (
            ("PRO_C_Research_Build_Solar", "Solar Panel"),
            ("PRO_C_Research_Build_Furnace", "Electric Furnace"),
            ("PRO_C_Research_Build_Alteration_Bench", "Advanced Alteration Bench"),
            ("PRO_C_Research_Build_Chemisry_Bench", "Chemistry Bench"),
            ("PRO_C_Research_Build_Cleaning", "Organic Residue Cleanser"),
            ("PRO_C_Research_Build_Foundry", "Foundry"),
        ):
            requirement = accepted.get(qid)
            facilities.append({**prep(item, str(requirement["quantity"]) if requirement else "Unconfirmed",
                                      True, "Deploy at the mission research site; T4 crafting capability required."),
                               "quest_id": qid})
        mission["checklist"] = facilities + [row for row in mission["checklist"]
            if row.get("item") != "Six specified research-station facilities and T4 crafting capability"
            and row.get("quest_id") not in {entry["quest_id"] for entry in facilities}]
        mission["guide"][1] = ("Build the research station at the specified site. Required equipment: "
            + "; ".join(f"{row['item']} x {row['quantity']}" for row in facilities) + ".")
        mission["guide"][4] = "Use the coating to craft the Miasmic tools in the quantities listed under Objectives. Verify unconfirmed targets in the game."
        mission["guide"][5] = "Test gathering with the matching miasmic tools. Verify the required delivery items and quantities in the current game objectives."
        if compatible and excluded:
            mission["guide"][4] = "Use the coating to craft the Miasmic Axe, Knife, Pickaxe and Sickle in the quantities listed under Objectives."
            deliveries = []
            for qid, item in (("PRO_C_Research_Test_Axe", "Infected Bark"),
                              ("PRO_C_Research_Test_Knife", "Acidic Glands")):
                quantity = accepted.get(qid, {}).get("quantity", "Unconfirmed")
                deliveries.append(f"{item} x {quantity}")
            mission["guide"][5] = ("Test gathering with the matching miasmic tools. Deliver "
                + " and ".join(deliveries) + " to the Sinotai Retrieval Pod.")
        mission["guide_note"] = ("Fixed objective quantities are joined from verified blueprint defaults for this catalog build. "
                                 "Required quantities are not saved progress or inventory counts; unverified targets remain unknown.")


def text(value):
    if not isinstance(value, str):
        return ""
    match = re.match(r'NSLOCTEXT\("[^"\\]*",\s*"[^"\\]*",\s*"(.*)"\)$', value, re.S)
    result = match.group(1) if match else value
    result = result.replace(r"\'", "'").replace(r'\"', '"').replace(r"\n", "\n")
    return re.sub(r"<[^>]*>", "", result).replace("[Map]", "map marker")


def prep(item, quantity="1", required=True, note=""):
    return {"item": item, "quantity": quantity, "required": required, "note": note}


COMMON = [
    prep("Oxygen, water and sufficiently durable containers", "1 set per person", False, "Recommended; fill before leaving and carry reserves for the journey"),
    prep("Food, Bandages and healing medicine", "Adjust for party size and difficulty", False, "Recommended; the app has not checked actual inventory"),
    prep("Weapons, spare ammunition / arrows and repair supplies", "According to equipment configuration", False, "Recommended; defense and boss missions need extra reserves"),
]


def guide(title, summary, steps, equipment=(), risks=(), time="Depends on travel and current stage; no verified total duration", risk=2, mechanics=()):
    return {"title": title, "summary": summary, "guide": list(steps), "checklist": list(equipment) + COMMON,
            "risks": list(risks) or ["Plan supplies, bedrolls and evacuation for the current difficulty and terrain. Follow the game's actual mission markers."],
            "time_estimate": time, "risk_rank": risk, "mechanics": list(mechanics)}


GUIDES = {
"PRO_Story_1": guide("NOMAD | Establish communications", "Investigate the distress signal. Build a base and communications equipment to begin the Prometheus story.", [
    "Investigate the distress location using the map and dialogue. A small shelter, Bedroll and basic supplies are recommended for the journey.",
    "Unlock the T2 C0NT4CT device, craft it at the Crafting Bench and deploy it.",
    "Collect the SMPL3 chip / upgrade from the Sinotai supply pod and interact with C0NT4CT. Killing the guarding creature is optional.",
    "Complete the SMPL3 farming tutorial: collect the tomato seed and crop plot, plant, wait for maturity, harvest, deliver and collect the reward.",
    "Rescue Daisy: visit the last known location and guide her safely to the owner's base, watching her health. Search the owner's base as directed afterward."],
    [prep("C0NT4CT Device", "1", True, "T2 technology; craft at the Crafting Bench"), prep("Shelter, Bedroll and basic survival supplies", "According to travel plans", False, "Recommended; the old base-facility objective is not part of the current mission flow"), prep("Tomato seed, crop plot and SMPL3 chip", "According to the supply pod", True, "Collect during the mission")], risk=2),
"PRO_Story_2": guide("OFFENDER | Track the thief", "Investigate the rumored base and recover evidence about the suspect.", [
    "Confirm T3 crafting capability and supplies before accepting at C0NT4CT.",
    "Enter the cave containing the rumored base, clear threats, then search for stolen technology and documents.",
    "Deliver stolen supplies to the Sinotai Retrieval Pod. Keep mission documents; do not discard them or place them in ordinary production equipment.",
    "Collect the Infrasonic Relay Upgrade from the new supply pod, install it in C0NT4CT and confirm the upgrade objective."],
    [prep("T3 crafting capability", "1 set", True, "Current mission table: RequiredTech=Tier3"), prep("Inventory space for mission evidence", "Reserve several slots", False, "Adjust to the items actually obtained")]),
"PRO_Story_3": guide("SHADOWED | Locate the hidden base", "Triangulate the suspect's hidden base using the clues, then continue collecting intelligence.", [
    "Complete OFFENDER and upgrade C0NT4CT. Prepare T4 technology and supplies for a longer journey.",
    "Collect the special equipment from the Sinotai supply pod, then scan at Alpha, Beta and Delta to locate the hidden base.",
    "Search the base for stolen technology and documents, then place the mission Beacon inside.",
    "Collect the marked Encrypted Satellite Upgrade and install it in C0NT4CT. Confirm later missions unlock. If mission equipment is lost, request Mission Resupply from the game's Escape menu."],
    [prep("T4 crafting capability", "1 set", True, "Mission table: RequiredTech=Tier4"), prep("Scanning equipment and Beacon", "According to supply and mission prompts", True, "Collect the special equipment before visiting the scan sites"), prep("Temporary bedrolls and materials for long journeys", "1 set", False, "Recommended")]),
"PRO_Story_4": guide("TEMPEST | Find the missing agents", "Find the missing Group 15 agents and deliver the recovered data.", [
    "Confirm SHADOWED is complete and C0NT4CT has its T4 upgrade installed.",
    "Start at the final broadcast location. Examine the first agent's body for the next signal, then track the second and third agents in sequence.",
    "Clear the creature guarding the final body, inspect the remains and take the Audio Recording.",
    "Follow the delivery branch that appears: interact with the equipment at Sol's old cache to upload, or deliver the recording to the Sinotai Retrieval Pod / Group 15. These branches are alternatives."],
    [prep("Storage space for mission data", "Several slots", False, "Reserve spare capacity")]),
"PRO_Story_5": guide("DISPATCH | Outpost data transmission and evacuation", "Enter the mined outpost, supply power, defend the transmission, place the beacon and evacuate in time.", [
    "Bring generators with at least 10000 W total output, matching fuel and an Electricity Tool. Complete power and defense preparations before starting transmission.",
    "Find the outpost and watch for explosive mines on entry. Reach the data-storage communicator.",
    "Connect 10000 W of power. Check repairs, supplies and evacuation directions, then interact to start transmission.",
    "Stay near the equipment and defend during transmission. Protect the communicator and keep checking its durability.",
    "After transmission, craft the required Beacon at the Machining Bench, place it inside the outpost and report to Group 15.",
    "Evacuate when the destruction warning appears. Looting is optional; do not miss evacuation for extra supplies."],
    [prep("Reliable power totaling 10000 W and wiring", "≥10000 W", True, "Power requirement verified in current D_Quests; choose your own equipment combination"),
     prep("Generator fuel / backup power", "Cover transmission and setup time", False, "Keep a power reserve; do not assume continuous sunlight"),
     prep("Mission Beacon", "1", True, "Craft at the Machining Bench at the relevant mission stage"),
     prep("Defense materials, Repair Hammer, spare weapons and ammunition", "According to party size and difficulty", False, "Recommended; establish supply points and an evacuation path first")],
    ["The outpost contains explosive mines. Transmission attracts waves of enemies and requires the communicator to survive.", "A nearby player is required for transmission progress. Mission transmission time does not include the whole journey.", "The maximum number of simultaneous enemies is not the total number of kills for the encounter."],
    time="600 seconds of transmission; travel, power setup, combat and evacuation are additional", risk=4,
    mechanics=["Decoded local Story5 blueprint: transmission target 600 seconds; player proximity approximately 38 m.",
               "Medium / Normal blueprint configuration: Ubis 14 s / cap 5, Drac 15 s / cap 5, Viscid 27 s / cap 2, Dreadwing 35 s / cap 5. These are configured values, not a guarantee of observed spawning.",
               "At 0-5%, 35-40% and 65-70% transmission progress, the Ubis / Drac / Viscid spawn intervals are halved. Dreadwing does not receive this acceleration."]),
"PRO_Story_6": guide("FRACTURE | Track down the traitor", "Collect Group 15 supplies and track down the traitor.", [
    "Accept after DISPATCH. Collect the marked Group 15 supplies and inspect the received items and mission instructions.",
    "Visit the last known location, examine the dead agent, follow the tracks through the cave and proceed to the nearby camp.",
    "Search the camp, clear creatures and collect intelligence. Follow the current suspect / waiting branch. If the facial-recognition delivery objective appears, deliver the specified object to OES; not every branch is simultaneously required.",
    "Use the hacked data to locate the base and collect more recordings. Start the data-storage hack, defend until it finishes, then leave the area.",
    "Follow Mo's clues to find evidence. Defeat the Giant Lava Creature, find the cave entrance, explore the settlement inside and discuss the evidence with Sol."],
    [prep("Group 15 mission supplies", "According to the supply pod", True, "Collect after accepting; do not purchase beforehand"), prep("Cave lighting, defense ammunition, heat protection and boss-fight supplies", "According to party size and difficulty", False, "Recommended; later stages include hacking defense and a Giant Lava Creature")], risk=4),
"PRO_A_Extraction": guide("CRYSTAL | Super Cooled Ice extraction", "Collect drilling equipment, extract Super Cooled Ice from an ice vein and establish a small camp.", [
    "Prepare T3 technology and arctic supplies. Collect the drilling equipment from the Sinotai drop pod.",
    "Follow the mission marker to the ice vein, place the Ice Borer and verify its supply requirements and operating state.",
    "Place the Cement Mixer, Concrete Furnace and Forge required by the camp checklist. Gather the required Super Cooled Ice.",
    "Optional preparations include a Cold Steel Pickaxe for Frozen Ore, a Shovel for ice and Cocoa for warmth.",
    "Deliver Ice, Super Cooled Ice, Frozen Ore and Cold Steel Ingots according to the Sinotai Retrieval Pod list. Verify each quantity; do not place the Ice Borer on an ordinary deposit."],
    [prep("Ice Borer and mission equipment", "According to the drop pod", True, "Provided by the mission; collect as directed"), prep("Warm clothing, heat sources and camp materials", "1 set per party", False, "Recommended; warm clothing is needed in the arctic"), prep("Cold Steel Pickaxe, Shovel, Cocoa", "According to the gathering plan", False, "The source lists crafting as optional; verify delivery requirements separately")], risk=3),
"PRO_B_Extermination": guide("MAGMATIC | Volcanic hunting and fishing", "Clear creatures and catch fish in the designated volcanic area.", [
    "Prepare T2 technology, hunting weapons and fishing equipment. Collect the mission equipment from the Sinotai supply pod first.",
    "Check the Lava Broodling, Drifter, Ashen Drake and Stonejaw targets inside the designated volcanic area. Kills outside the boundary may not count.",
    "Catch Sunset Ripjaw, Void Triggerfish, Coral Darter and Neon Dory in the designated waters. Check quantities in the game before reporting."],
    [prep("Fishing Rod and matching tackle", "1 set", True, "The mission includes fishing in a specified area"), prep("Heat protection, protective equipment and medicine", "Per participant", False, "Recommended; avoid crossing dangerous terrain directly")], risk=3),
"PRO_C_Construction": guide("TREEHUT | Treetop home", "Build a qualifying home at the designated treetop.", [
    "Confirm that the site is inside the mission area. Bring T2 construction materials and tools for working at height.",
    "Build supports and a safe return to ground at the specified treetop, then fulfill each housing condition.",
    "Place Beds, Anvil Bench, Textiles Bench, Skinning Bench, Oxidizer, Potbelly Stove and Stone Furnace. Check each housing objective. Keep supports until all requirements are confirmed."],
    [prep("Ladders, platforms, Repair Hammer and building materials", "1 set", False, "Recommended; protects against falls and structural damage")], risk=1),
"PRO_C_Research": guide("MIASMIC | Swamp miasma research", "Build a research station, refine miasmic coating and craft miasmic tools.", [
    "Prepare T4 technology, swamp protection and materials to transport the workstations. Confirm access to a Chemistry Bench.",
    "Build the research station at the specified site. Place the Organic Residue Cleanser, Solar Panel, Foundry, Electric Furnace, Advanced Alteration Bench and Chemistry Bench listed in the objective.",
    "Hunt and skin creatures in the swamp to collect Crystallised Miasma.",
    "Refine the crystals into Distilled Miasmic Coating at the Chemistry Bench. Verify the recipe and required quantities before processing batches.",
    "Use the coating to craft the Miasmic Sickle, Knife, Axe and Pickaxe. Where an objective ID and display name are swapped, follow the displayed item and actual completion check.",
    "Test gathering with the matching miasmic tools. Deliver Acidic Glands, Thorned Plants, Infected Bark and Noxious Crust to the Sinotai Retrieval Pod, verifying each quantity."],
    [prep("Six specified research-station facilities and T4 crafting capability", "Each item according to the checklist", True, "Cleanser, Solar Panel, Foundry, Electric Furnace, Advanced Alteration Bench, Chemistry Bench"), prep("Miasmic tool ingredients and coating", "According to the four tool recipes", True, "Check Knife, Sickle, Axe and Pickaxe")], risk=3),
"PRO_A_Stockpile": guide("CELEBRITY CHEF | Refrigerated food deliveries", "Place the listed ingredients in refrigerated cargo and complete the required supply list.", [
    "Check the Alpine / Marshlands refrigerated cargo lists first. Identified items include Fruit Salad, Berry Jam, Beer, Bread Dough, Soy Bean Stir Fry, Fish Pie, Freshwater Fish Curry, Battered Saltwater Fillet, Wine and Saltwater Fish Sashimi; allocate them using the current lists.",
    "Prepare T3 processing and preservation. Prioritize perishable ingredients and keep oxygen and return supplies for each transport trip.",
    "Place the specified prepared products in refrigerated mission cargo and verify progress. Raw ingredients do not substitute for prepared items."],
    [prep("Refrigeration and transport space", "Per batch", False, "Recommended; reduces spoilage and overload risks")], risk=1),
"PRO_Ashlands_Story": guide("ASHLANDS | Ashlands beacon chain", "Use a continuous beacon chain to mark the Null Sector entrance and exploration path.", [
    "Complete DISPATCH first. Prepare T3 crafting capability, a Trail Beacon Tool and long-distance supplies.",
    "Find the Null Sector entrance and destroy the barrier. The Trail Beacon Tool and Trail Beacons are on the T3 technology tree; bring both. Supplies are scarce there, so prepare ahead.",
    "Place the first Trail Beacon at the entrance and confirm that the game accepts the location.",
    "Extend the beacon chain as directed. Check coverage and map progress along each segment; straight-line distance does not establish a safe path across terrain.",
    "Follow signal strength to the lost outpost and search for the science team's evidence. Prepare for wildlife encountered along the route.",
    "Place the final beacon near the Icesheet entrance. Confirm the unlock and return markers."],
    [prep("Trail Beacon Tool", "1", True, "T3 technology; the separate beacon target is listed below. Additional route supplies depend on travel plans"), prep("Tools for breaking barriers, heat protection and long-distance supplies", "Adjust for travel distance", False, "Recommended; resources are scarce in Null Sector")], risk=3),
"PRO_Icesheet_Story": guide("ICE SHEET | Icesheet beacon chain", "Extend the beacon chain from the Icesheet entrance to the scientists' outpost.", [
    "Complete ASHLANDS. Bring a Trail Beacon Tool, warm clothing and arctic supplies.",
    "Find the Icesheet entrance from Ashlands. Break the entrance cave-in with a Pickaxe and place the first beacon.",
    "Extend the chain to the scientists' outpost. Keep checking warmth, oxygen and a connected return path.",
    "Follow signal strength to the outpost, search for the science team's evidence, then investigate the nearby cave for the missing scientist."],
    [prep("Trail Beacon Tool and Pickaxe", "1 of each", True, "Pickaxe for clearing the entrance cave-in; the separate beacon target is listed below"), prep("Warm clothing, heat sources and temporary bedrolls", "1 set per party", False, "Recommended")], risk=3),
"PRO_D_Transport": guide("CARAVAN | Ashlands supply transport", "Stockpile supplies first, then transport the containers and supplies to the specified Ashlands location.", [
    "Read the starting stockpile list: Wood, Fiber, Oxite and multiple seeds. Allocate containers and party roles by material and weight.",
    "Stock the required storage equipment and confirm that the first stage is accepted.",
    "Transport equipment and supplies to the designated Ashlands site. Place storage containers and deposit the listed items.",
    "Deal with mission ambushes before checking destination supply requirements. Keep equipment deployed until the game confirms completion."],
    [prep("Containers, Wood, Fiber, Oxite and several seed types", "According to both stage lists", True, "Use the mission / container requirements after accepting"), prep("Transport mount / vehicle, repair and combat supplies", "According to weight", False, "Recommended; prepare for ambushes")], risk=3),
"PRO_D_Research": guide("COMPOSITION | Ashlands environmental tests", "Complete the Alpha and Beta tests and eliminate the mutated wildlife.", [
    "Complete ASHLANDS. Craft the required Liquid Solution at the Chemistry Bench and Aerosol Emitters at the Fabricator; prepare power and combat supplies.",
    "Deploy and power the device at Alpha. Keep it operating until the emission cycle ends, then return and deal with the mutated wildlife.",
    "At Beta, follow the objectives for the liquid test, deployment, power and specified creature encounter.",
    "When the mission reaches the Chi test, deploy power again and maintain the emission cycle. Stay near the Emitter to collect data.",
    "Check the current Alpha, Beta and Chi objectives and hunting progress individually. Use the cycle durations displayed in the game."],
    [prep("Power supplies for the Aerosol Emitters", "According to each deployment site", True, "Connect and verify power during each test"), prep("Durable combat equipment and medicine", "According to party size", False, "Recommended; includes boss-level risks")], risk=4),
"PRO_E_Research": guide("DROVER | Drifter monitoring", "Craft and deploy a Monitoring Pylon, then complete monitoring.", [
    "Complete ICE SHEET. Craft a Monitoring Pylon at the Machining Bench first.",
    "Bring it to the designated rock formation beneath the isolated Drifter. Confirm the location and a safe way up and down.",
    "Deploy the Monitoring Pylon and supply Stone, Gold Ore and Sulfur at their respective stages. Select the requirement matching the mission difficulty.",
    "Check all monitoring stages and the mission-completion message."],
    [prep("Monitoring Pylon", "1", True, "Craft at the Machining Bench"), prep("Materials for elevated construction, repair tools and provisions", "1 set", False, "Recommended")], risk=2),
"PRO_D_Recovery": guide("CRISIS | Medical rescue", "Find the Ashlands construction site and stabilize the rescue team using the HEAL Device.", [
    "Complete ASHLANDS. Craft the HEAL Device and bring sufficient medicine and provisions.",
    "Investigate the Ashlands Terraforming Construction Site, clear threats and speak to Captain Church. Watch the rescue time limit displayed in the game.",
    "Scan each team member with the HEAL Device and supply medicine and provisions according to each diagnosis.",
    "Stabilize Church, Tucker, Griff, Simmons and Washington, then speak to Captain Church to report."],
    [prep("HEAL Device", "1", True, "T3 unlock; craft at the Machining Bench"), prep("Food, water, oxygen and medicine stronger than pastes", "According to the five diagnoses", True, "The source requires medicine stronger than pastes; types and quantities depend on the scan")],
    risks=["This rescue has a time limit. Prepare the HEAL Device, advanced medicine and supplies for five people before accepting and leaving.", "Pastes alone may not treat the team. Supply medicine according to each scan diagnosis."], risk=3),
"PRO_D_Resupply": guide("STRANDED | Resupply and repair the rescue truck", "Refill the truck's resources, repair the engine and replenish equipment.", [
    "Accept after CRISIS. Inspect each truck component and confirm the delivery points and actual materials list.",
    "Refill water and Biofuel. Portable Water / Biofuel Tanks crafted at the Fabricator can transport them.",
    "Craft the Radiator and Alternator at the Fabricator and deliver them to the truck engine. Craft Rope at the Crafting Bench and deliver it to truck storage; follow the listed quantities.",
    "Check resource slots, engine and storage objectives separately. Do not remove delivered supplies before completion."],
    [prep("Water and Biofuel", "According to the truck resource slots", True, "Verify fill status in the game; item counts and fluid fill levels are separate"), prep("Portable Water / Biofuel Tanks", "According to transport volume", False, "Recommended; craft at the Fabricator")], risk=2),
"PRO_D_Rescue": guide("RESCUE | Rescue Simmons and Washington", "Track and rescue the two specified team members.", [
    "Complete STRANDED. Craft two Stasis Bags at the Advanced Textiles Bench, reserve a Utility Slot and plan the return to the Medevac Ship.",
    "Find Simmons at the marker. Clear the threats, place him in a Stasis Bag and transport him to the marked Medevac Ship.",
    "Find Washington, use the second Stasis Bag and deliver him to the rescue ship. Check each rescue's progress separately.",
    "An occupied Stasis Bag uses a Utility Slot. Arrange carriers or multiple trips for your party; verify both agents are delivered before reporting."],
    [prep("Stasis Bags", "2", True, "The source explicitly requires two; craft at the Advanced Textiles Bench"), prep("Utility Slot", "1 slot per occupied bag", True, "Remove conflicting equipment and assign transport roles first"), prep("Medicine, food, water and lighting", "Adjust for distance and party size", False, "Recommended; locate the rescue ship first")], risk=3),
"PRO_Nullsector_Story": guide("NULL SECTOR | Explore the cave near the Icesheet", "Investigate the specified cave near the Icesheet outpost to advance the regional story.", [
    "Complete ICE SHEET. Check the mission cave markers and actual entrance near the outpost first.",
    "If a cave-in blocks the entrance, search the outpost and the area below the cave for mission explosives. Place them at the cave-in and move away to trigger the blast.",
    "Bring lighting and medicine into the cave. Continue deeper and follow the sounds to the cavern to hear the dialogue.",
    "Examine the Exotic Formation and confirm the story and mission completion. Keep a usable return path."],
    [prep("Mission explosives", "According to available supplies", True, "Search the outpost and area below the cave; ordinary explosives are not assumed to be substitutes"), prep("Lighting and spare fuel", "1 set", False, "Recommended; cave entrance coordinates differ from mission object coordinates")], risk=3),
"PRO_SQ_Farmer": guide("CULTIVATION SPECIALIST | Crop trading specialist", "Help the specialist establish a trading post, prepare the first fruit and vegetables, then sell the crops.", [
    "Accept this mission at the mission device. If an Operation is active, complete or end it first, then follow the marker to the specialist.",
    "Deliver Glass, Sulfur, Aluminium Ingots and Beeswax according to the specialist's current list.",
    "Leave the area as directed so the specialist can build the trading post. Trading may be unavailable until construction is complete.",
    "Prepare the first fruit and vegetables. Sell crops for Ren once the trading post is established."],
    [prep("Growing and preservation supplies", "According to crop plans", False, "Recommended for the first fruit and vegetable objectives")], risk=1),
"PRO_SQ_Hunter": guide("PREDATOR SPECIALIST | Hunting trade specialist", "Supply the Hunting Specialist, clear nearby creatures and sell Vestiges.", [
    "Accept this mission at the mission device. If an Operation is active, complete or end it first, then follow the marker to the specialist.",
    "Deliver Gunpowder, Shell Casings, Flares and Smoke Grenades; use the specialist's current list for quantities.",
    "Hunt within the specified area until the specialist's supply and safety conditions are met.",
    "Skin creatures with the Taxidermy Knife to collect Vestiges, then sell them when the specialist is ready."],
    [prep("Taxidermy Knife", "1", True, "Unlock and craft at the T2 Anvil; gather Vestiges")], risk=3),
"PRO_SQ_Norex": guide("BLACK MARKET | Norex contact", "Defeat a world boss for Biomass, signal from the designated location and trade.", [
    "Accept this mission at the mission device. If an Operation is active, complete or end it first, then follow the marker to the specialist.",
    "Fight the Blighted Landshark at the marker. Prepare a bedroll, combat supplies and room to retreat first.",
    "Collect Biomass from the boss corpse and visit the designated site on the Theta plateau.",
    "Fire a flare into the sky, approach the Norex contact and exchange Biomass for creature parts."],
    [prep("Single Barrel Launcher and compatible Flares", "1 set of each", True, "T2 unlock; craft at the Crafting Bench"), prep("Biomass", "According to trade requirements", True, "Collect from the designated boss corpse during the mission")], risk=4),
"PRO_SQ_Fisher": guide("FISHERIES OFFICER | Fishing trade specialist", "Rescue the Fisheries Officer, retake and repair the outpost, then complete the first fish trade.", [
    "Accept this mission at the mission device. If an Operation is active, complete or end it first, then follow the marker to the specialist.",
    "Visit the Fishing Outpost, stabilize the specialist with the HEAL Device and defeat the Tide-Gorged Bear to retake the outpost.",
    "Repair the shed with a Hammer or Nailgun, then place the benches, traps and equipment listed in the game.",
    "Leave as directed while the specialist rebuilds. Catch the first fish with a rod or trap, then sell them for Ren."],
    [prep("HEAL Device", "1", True, "T3 unlock; craft at the Machining Bench"), prep("Repair Hammer or Nailgun", "1", True, "For shed repairs"), prep("Fishing Rod and tackle for the first fish trade", "According to fishing plans", False, "Recommended; acquire and deploy the listed outpost equipment as one shared set")], risk=4),
}

DYNAMIC_GUIDES = {
"Boss": ("SMPL3 | Enraged creature", "Hunt the specified creatures driven mad by terraforming.", ["Check the target creature, area and difficulty after accepting. Do not assume ordinary wildlife strength.", "Prepare weapons, ample ammunition and healing supplies. Establish a safe bedroll before entering the marked area.", "Kill the specified target and follow updated prompts to report or collect rewards. Ordinary creatures of the same species may not count."], [prep("Weapons and ammunition suited to the target creature", "According to difficulty", False, "Recommended")], 4),
"Mine": ("SMPL3 | Mining delivery", "Gather the specified ores and deliver them to UDA.", ["Check ore types, quantities and delivery points after accepting.", "Choose a matching deposit with recoverable resources. Bring a suitable Pickaxe and spare repair materials.", "Deliver the specified ores to the mission equipment, verify quantities and report."], [prep("Pickaxe of the required tier", "1", True), prep("Specified ores", "According to the acceptance list", True)], 1),
"Grow": ("SMPL3 | Crop delivery", "Grow and deliver the specified crops.", ["Confirm the required crops, products and quantities before planting.", "Prepare seeds, crop plots and irrigation. Protect and preserve the crops while waiting for growth.", "Deliver the harvested items to UDA. Check whether cooked products are required instead of raw crops."], [prep("Specified seeds, crop plots and crops", "According to the acceptance list", True)], 1),
"Cache": ("SMPL3 | Hidden supply crates", "Search for hidden supplies left by other prospectors.", ["Visit the mission search area and identify its boundary and a usable return entrance.", "Search supply crates and interact with mission objects. Reserve inventory space.", "Report or collect rewards according to the updated objective. Ordinary containers are not mission targets."], [prep("Lighting and inventory space", "Several slots", False, "Recommended")], 1),
"Collect": ("SMPL3 | Recover identification", "Find the dead prospectors and deliver their identification.", ["Search for the team members' bodies at the mission markers and watch for nearby predators.", "Collect identification from each specified body and keep mission items separate.", "Deliver the required identification to UDA and confirm that every team member objective is complete."], [prep("Mission identification documents", "According to the accepted objective", True, "Collect when searching the bodies")], 2),
"Hunt": ("SMPL3 | Area hunting", "Hunt the specified nearby creatures to reduce the threat.", ["Confirm the area, species and required count / progress after accepting.", "Kill targets inside the designated boundary and check progress. Kills outside may not count.", "Report completion, treat injuries and replenish ammunition before taking another mission."], [prep("Weapons and sufficient arrows / ammunition", "According to mission difficulty", False, "Recommended")], 3),
"Scan": ("SMPL3 | Radar scan", "Collect the Radar and complete the UDA scans.", ["Collect the Radar from the mission drop pod and check the designated scan locations.", "Deploy at the target location. Set up defenses before starting the scan.", "Maintain or retrieve the radar as directed, then verify the report and reward."], [prep("Mission Radar", "According to the drop pod", True, "Collect after accepting"), prep("Defense and repair supplies", "1 set", False, "Recommended")], 3),
"Tools": ("SMPL3 | Craft tools", "Craft tools for UDA to distribute to prospectors needing help.", ["Check tool types, tiers and quantities first.", "Craft at the correct bench. Verify materials and technology unlocks; lower-tier substitutes may not qualify.", "Deposit in the specified delivery equipment and verify quantities."], [prep("Specified tools and crafting materials", "According to the delivery list", True)], 1),
"Build": ("SMPL3 | Build an observation tower", "Build an observation tower and place a beacon to mark the site.", ["Check the construction area and structural requirements before transporting materials.", "Build an observation tower with reliable supports and access up and down.", "Place the beacon at the required site, check the building conditions and report."], [prep("Tower building materials and beacon", "According to the construction list", True), prep("Repair Hammer, ladders and lighting", "1 set", False, "Recommended")], 2),
"Drone": ("SMPL3 | Crashed drone", "Find the drone within the time limit and protect its data upload.", ["Prepare supplies and combat equipment before accepting because this mission has a time limit.", "Reach the crash site quickly. Inspect the drone and nearby threats first.", "Start the upload as directed and defend it. Check durability and progress until complete; use the time limit shown in the game."], [prep("Defense weapons, ammunition, repair supplies and medicine", "According to difficulty", False, "Prepare before accepting")], 4),
"Lost": ("SMPL3 | Search an abandoned base", "Locate and search the abandoned base for UDA.", ["Visit the marked abandoned base, checking the environment and predators first.", "Search each area and interact with the specified objects. Reserve mission-item space.", "Complete the required deliveries or reports. Verify that no checklist items remain before leaving."], [prep("Lighting, repair supplies and inventory space", "1 set", False, "Recommended")], 2),
}


def build(source: Path, build_id: str, pak: Path | None = None):
    tables = {Path(name).stem: json.loads((source / name).read_text(encoding="utf-8-sig")) for name in TABLES}
    prospects = tables["D_ProspectList"]["Rows"]
    factions = {r["Name"]: r for r in tables["D_FactionMissions"]["Rows"]}
    quests = {r["Name"]: r for r in tables["D_Quests"]["Rows"]}
    talents = {r["Name"]: r for r in tables["D_Talents"]["Rows"]}
    talent_for = {r.get("ExtraData", {}).get("RowName"): r for r in talents.values() if r.get("ExtraData", {}).get("DataTableName") == "D_ProspectList"}

    def prerequisites(talent, seen=None):
        seen = set() if seen is None else seen
        found = []
        for req in talent.get("RequiredTalents", []):
            name = req.get("RowName")
            if name in seen:
                continue
            seen.add(name)
            row = talents.get(name, {})
            mission_id = row.get("ExtraData", {}).get("RowName")
            if mission_id and mission_id != "None":
                found.append(mission_id)
            else:
                found += prerequisites(row, seen)
        return list(dict.fromkeys(found))

    missions = []
    for row in prospects:
        availability = row.get("OnProspectAvailability", "None")
        if availability == "None" or row.get("bDisabled"):
            continue
        mid = row["Name"]
        faction = factions.get(row.get("FactionMission", {}).get("RowName", mid), {})
        initial = faction.get("InitialQuest", {}).get("RowName", mid)
        obj = []
        quest_ids = {mid, initial}
        for objective in faction.get("MissionObjectives", []):
            qid = objective.get("QuestRow", {}).get("RowName")
            if qid and qid not in {o["id"] for o in obj}:
                q = quests.get(qid, {})
                obj.append({"id": qid, "text": text(q.get("Description", qid)), "info": text(q.get("InfoText", ""))})
                quest_ids.add(qid)
        # Include children only within exact mission/root prefixes, never another mission.
        prefixes = {mid + "_", initial + "_"} | {o["id"] + "_" for o in obj}
        if re.fullmatch(r"PRO_Story_\d+", mid):
            prefixes.add(mid.replace("PRO_Story_", "PRO_Story") + "_")
        elif mid in {"PRO_Ashlands_Story", "PRO_Icesheet_Story", "PRO_Nullsector_Story"}:
            prefixes.add(mid.removesuffix("_Story") + "_")
        quest_ids.update(qid for qid in quests if any(qid.startswith(prefix) for prefix in prefixes))
        terrain = row.get("Terrain", {}).get("RowName", "")
        feature = row.get("Metadata", {}).get("RequiredFeatureLevel", {}).get("RowName", "")
        kinds = [t.get("RowName") for t in faction.get("Types", [])]
        normal = {
            "id": mid, "title": text(row.get("DropName", mid)), "map_name": MAPS.get(terrain, terrain),
            "kind": "NPC" if "NPC" in kinds else "Operation", "types": kinds,
            "summary": text(row.get("Description", "")), "objectives": obj,
            "source_objectives": [{"id": qid, "text": text(quests[qid].get("Description", "")), "info": text(quests[qid].get("InfoText", ""))} for qid in sorted(quest_ids) if qid in quests and quests[qid].get("Description")],
            "terminal_level": {"Base": 1, "Upgrade1": 2, "Upgrade2": 3, "Upgrade3": 4}.get(availability, 99),
            "prerequisite_ids": prerequisites(talent_for.get(mid, {})), "feature": feature,
            "tech": row.get("RequiredTech", "None"), "required_level": row.get("RequiredLevel", 0),
            "required_flags": row.get("RequiredFlags", []), "quest_ids": sorted(quest_ids),
            "acquisition": "Open Operations at this world's C0NT4CT device. Verify prerequisites, upgrades and access before accepting.",
            "source": "Installed-version data.pak: D_ProspectList, D_FactionMissions, D_Quests, D_Talents. Recommended supplies in the guides are authored by the app.",
            "order": len(missions), "guide_note": "Original objectives retain the game's English wording. Preparation recommendations are separate from mandatory game requirements.",
        }
        base_steps = ["Verify prerequisite missions, C0NT4CT upgrades and required technology. Accept at the device before setting out."]
        base_steps += ["Follow the game objective: " + o["text"] + ("; " + o["info"] if o["info"] else "") for o in obj]
        base_steps += ["Verify new quantities and location prompts at every stage, then confirm mission completion and rewards."]
        normal.update(guide(normal["title"], normal["summary"], base_steps,
                           [prep("Mission equipment and supplies", "According to the list after accepting", True, "A complete dedicated guide is not yet available; verify against the local game's objectives")]))
        normal["guide_coverage"] = "Source objectives and general preparation; mission details are incomplete"
        if mid in GUIDES:
            normal.update(GUIDES[mid])
            normal["guide_coverage"] = "Dedicated mission guide complete; verify dynamic quantities and branches using the list after accepting"
        rewards = []
        for reward in faction.get("CurrencyRewarded", []):
            key = reward.get("Meta", {}).get("RowName", "Unknown currency")
            label = {"Credits": "Ren", "Exotic_Red": "Red Exotics", "Exotic1": "Exotics"}.get(key, key)
            rewards.append(f"{label}: {reward.get('Amount', 'Unknown')} (base mission-table value; actual difficulty and multipliers follow the game)")
        unlocks = [r.get("RowName") for r in faction.get("AccountFlagsRewarded", []) + faction.get("CharacterFlagsRewarded", [])]
        rewards += ["Unlock flag: " + u for u in unlocks if u]
        normal["rewards"] = rewards or ["This table lists no fixed currency reward; use the game's final reward and subsequent unlocks."]
        missions.append(normal)
    for row in tables["D_DynamicQuests"]["Rows"]:
        key = row["Name"]
        if key not in DYNAMIC_GUIDES:
            continue
        title, summary, steps, equipment, risk = DYNAMIC_GUIDES[key]
        mid = row["Quest"]["RowName"]
        obj = [{"id": qid, "text": text(q.get("Description", "")), "info": text(q.get("InfoText", ""))} for qid, q in quests.items() if qid.startswith(mid + "_")]
        missions.append({"id": mid, "map_name": "All", "kind": "SMPL3", "order": len(missions),
            **guide(title, summary, steps, equipment, risk=risk), "objectives": obj,
            "quest_ids": [mid] + [o["id"] for o in obj], "prerequisite_ids": [], "terminal_level": 0,
            "acquisition": "Check the current selection in the SMPL3 random-assignment interface. The catalog lists types; it does not mean every type is currently offered.",
            "rewards": ["Rewards depend on the selected difficulty and mission reward pool. Check the device before accepting; no fixed amount is assumed."],
            "source": "Installed-version data.pak: D_DynamicQuests, D_FactionMissions, D_Quests.",
            "guide_coverage": "Assignment-type guide complete; verify variant quantities after accepting", "guide_note": "SMPL3 species, quantities, difficulty and rewards vary with the selected assignment variant."})
    manifest = {"schema_version": 1, "source_build": build_id, "source_version": f"ICARUS Steam build {build_id}", "source_date": datetime.now().astimezone().isoformat(),
        "source_files": {name: {"sha256": hashlib.sha256((source / name).read_bytes()).hexdigest(), "bytes": (source / name).stat().st_size} for name in TABLES},
        "missions": missions, "quest_texts": {qid: text(q.get("Description", "")) for qid, q in quests.items()},
        "enum_evidence": {"source": "Installed Icarus-Win64-Shipping.exe reflected enumerator pointer/value table", "EMissionState": {"InProgress": 0, "Completed": 1, "Abandoned": 2, "Failed": 3}},
        "coverage": {"operations": len(missions) - len(DYNAMIC_GUIDES), "smpl3_types": len(DYNAMIC_GUIDES), "prometheus_custom_guides": len(GUIDES), "other_map_guides": "Basic guidance follows source objectives; dedicated guides are not yet complete for every mission"}}
    if pak:
        manifest["data_pak"] = {"bytes": pak.stat().st_size, "sha256": hashlib.sha256(pak.read_bytes()).hexdigest()}
        exe = pak.parents[2] / "Binaries/Win64/Icarus-Win64-Shipping.exe"
        if exe.is_file():
            manifest["enum_evidence"]["exe_sha256"] = hashlib.sha256(exe.read_bytes()).hexdigest()
    verified = source / "blueprint_verification.json"
    if verified.is_file():
        manifest["blueprint_verification"] = json.loads(verified.read_text(encoding="utf-8"))
    apply_verified_requirements(manifest)
    target = ROOT / "icarus_helper/data/missions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "coverage": manifest["coverage"]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="Cache directory containing extracted data tables")
    parser.add_argument("--build-id", default="unknown")
    parser.add_argument("--pak", type=Path)
    parser.add_argument("--repak", type=Path)
    args = parser.parse_args()
    if args.repak and args.pak:
        args.source.mkdir(parents=True, exist_ok=True)
        command = [str(args.repak), "unpack", str(args.pak), "-o", str(args.source), "-s", "C:/BA/work/92bbbfa44df12262/Temp/Data/", "-f", "-q"]
        for name in TABLES:
            command += ["-i", name]
        subprocess.run(command, check=True)
    build(args.source, args.build_id, args.pak)


if __name__ == "__main__":
    main()
