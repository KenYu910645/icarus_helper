# ICARUS Helper

An English Windows desktop companion that reads local or host world saves. The map and missions use the same integrity-verified snapshot and update automatically when the selected save changes. User-created world, base, character and animal names are preserved.

## Download the Windows app

Open [GitHub Releases](https://github.com/KenYu910645/icarus_helper/releases/latest) and download `ICARUS-Helper-<version>-Windows-x64.zip`. For v0.4.0, choose `ICARUS-Helper-v0.4.0-Windows-x64.zip`. Extract the entire archive, open `ICARUS Helper.exe`, and retain `_internal`. Python and separate map downloads are not required. GitHub's `Source code.zip` contains development sources; choose the Windows application ZIP to run the app. Check [RELEASE_NOTES.md](RELEASE_NOTES.md) for changes and the accompanying `SHA256SUMS.txt` to verify the ZIP.

With empty settings, the app opens the **Prometheus shared map and Home**, without requiring a local save. The shared map is marked **Host save not connected**. Its bundled base can be managed through the map's right-click menu and is stored separately from actual-world bases. The app remembers your selection after you switch to your own world.

The shared map contains static map data. Mission lists and locations require valid host data; the shared map contains no private host save, accounts, player positions, inventory or mission progress. Remaining resources and drill placement stay unknown. For GuoGuoGaGa, select **Public sync** to receive the complete world save explicitly approved for public distribution through GitHub Releases. Other worlds require **Choose host save…** and a valid source. A one-time copy represents only the save from which it was made.

## GuoGuoGaGa public sync

After downloading and extracting the current complete Windows ZIP, select **Public sync · GuoGuoGaGa · Prometheus** once. The app remembers the choice, checks every two minutes in the background, and also offers **Refresh**. No GitHub account, token or manual save search is required. The map, drill output, base inventory, missions and animals use the same host save, identified by save time and public-sync source. This is not a live game connection. If the host has not saved, is offline or has not completed an upload, the last valid data remains visible with the relevant status.

On 2026-11-02, the world owner explicitly approved publishing the complete save. Anyone can download its world data, player records and original account identifiers. Data is in a separate [World Sync Release](https://github.com/KenYu910645/icarus_helper/releases/tag/world-sync-guoguogaga), outside the application ZIP. Approval covers only this world; other worlds, Characters, Profile, BestiaryData, private Helper settings and diagnostics are excluded.

Receivers store downloads only in the Helper's private cache. The app does not replace game files, register downloads for local backups, restore the host world from them, or combine them with readings from the receiver's local game. Initial selection imports the publicly shared Home location; saved or explicitly deleted bases are not overwritten. Owner character/account files are not shared, so persistent owner bonuses remain incomplete. Animal values and positions use the host save. Game-version compatibility is still checked.

The host publisher uses the host's existing GitHub CLI login and reads only the explicitly selected source. Initial registration binds the exact world and seed. It observes every five seconds and publishes after two stable observations and full verification, with at least 120 seconds between publications. The original SHA-256-identified version is uploaded before the index. Receivers verify integrity and world identity before updating. The independent `IcarusHelperWorldShare` task starts at sign-in and receives a recovery start every minute. Remote cleanup keeps the latest three versions uploaded by this publisher only when receipts and content can be reverified. Local backup rules are unchanged. Disable the task in Task Scheduler to stop sharing.

The app is portable Windows x64. Prometheus has the most complete support; other maps have less coverage. Other computers may still need compatibility checks. Third-party assets/packages are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No separate open-source license has been granted for this project's code.

## Open and use

1. Open the **ICARUS Helper** desktop shortcut or the extracted EXE. In the source checkout, `launch.ps1` opens the local objective-counter update at `dist/objective-counters-20261105/ICARUS Helper/ICARUS Helper.exe` when present and otherwise starts `main.py` using your installed Python environment. Earlier folders are retained and running backup processes are not overwritten. The public v0.4.0 Release is unchanged.
2. The first launch selects the shared map. Select a local world or **Choose host save** to track a read-only host copy/source. An old local copy does not automatically become current host data.
3. Right-click the map and select **Add base here** to set Home, or choose a candidate inferred from deployed-object clusters. Right-click a base to edit, delete or make it primary. Right-click empty space to move the selected base there or switch saved bases. Settings are isolated by world.
4. Zoom with the mouse wheel and drag to pan. The right-click menu offers full-map and primary-base positioning. Filters select minerals, layers and drill placement. Click a marker for details.
5. Open **Missions & guides** for every confirmed unlocked or active Operation/NPC in the selected world. Completed, locked, unconfirmed and SIMPLE/SMPL3 missions are excluded. Status distinguishes missions that can start now, unlocked missions waiting to start, and active missions. Only one Operation can run at once; finish it in the game before starting another. The overview retains all eligible mission points. A marker opens its guide, preparation and progress. Overlapping points provide a mission choice. Search filters the list without hiding other overview markers. Not-yet-generated points remain candidates; unresolved stages appear in **Stage map**. **Nearby groups** offers maximum point separations of 500/1,200/2,000 m and distinguishes stages inside/outside groups and unlocated stages. Groups use horizontal straight-line distances and do not guarantee a single-trip completion or passable terrain.

   **Objectives** uses `0/N` for verified fixed targets, labeled as initial counts. Saved progress is listed separately; unknown and difficulty-dependent targets retain their labels.
6. Open **Base inventory** for saved items in containers/equipment around the selected base. The horizontal radius defaults to 100 m and can be changed, as can category. Each material, such as gold, copper or iron, has one stacked bar: ore/raw forms and other items/mineral fuels use orange at the bottom, with ingots and processed forms in teal above. There is no legend. Tooltips and the detail table retain original forms, quantities and containers. Bars sort by actual combined counts and detail rows by individual quantity, both descending. Unknown or partially unknown totals are last; confirmed partial counts are not presented as complete totals.
7. Open **Tamed animals** for owner-column cards with native species icons, level and health/food/water/oxygen/stamina/experience bars. Select a card for saddle, backpack and heavy-cargo item icons/counts. Hover or expand item/quantity/slot details to inspect records. Full values, bloodlines, genetics and talents can also be expanded. All confirmed player-tamed animals appear by default; search and filters select near-home, away-from-home or unknown-position animals.

The animal map shows circular native-image animal markers and square person markers. Solid outlines mean verified game positions, dashed outlines mean saved positions; each has its reading time. Zoom, pan, full map, all positions and base positioning are supported. Animal selection synchronizes its card; player selection does not select an animal. Search/range filters affect animals while retaining players. Verified player positions also appear on the resource map. Unknown positions do not create invented points. Shared maps contain no private animals or players.

Experience bars use verified species growth curves. While the game runs and identity checks succeed, health, food, water, applicable oxygen and stamina use paired current/maximum readings, including bonuses already applied by the game. Hover for reading times. Without a game source, saved values retain scope: `*` includes verified persistent owner sources; `≈` is an individual baseline without complete owner sources. Cow oxygen/stamina remain unverified. Unknown values are never zero or full bars. Owners use character names only; different characters sharing a name have separate columns. Unconfirmed owners have an unknown-owner column.

For actual worlds, these values and positions update every 30 seconds, including while another Helper tab is selected or you return to the game. Initial discovery searches all objects; later samples use a location cache with identity/generation rechecks, never old values disguised as new samples. Complete object inventories run less frequently; new/invalid objects trigger rediscovery. Unloaded, offline or unsynchronized players retain saved sources. Missing data does not prove absence. Experience, inventory, missions and harvesting still update with valid saves.

The window, taskbar and shortcut share a gold ICARUS icon with a green **H** at the lower right. Species use native English names such as Terrenus, Moa, Buffalo, Ubis and Cow. Custom names are preserved; Chinese species aliases remain searchable.

Inventory has five categories: **Ores, Metals & Fuel**, **Materials, Parts & Construction**, **Food & Medical Supplies**, **Tools, Equipment & Ammunition**, and **Other Items**. Ores, ingots and mineral fuels share one chart with two bar colors. Other items/mineral fuels use the bottom color without being classified as unprocessed. Charts scroll horizontally when needed; unrecognized items appear separately when present. Native icons appear beside names; each material's available form icons sit side by side below its stacked bar, with distinct ore/ingot images and hover details. They are bundled, requiring no network or game installation at runtime. Missing icons retain names with question marks. Small windows can scroll to icons and axis labels.

Use the search bar in **Base inventory** to filter the selected category by item or material name, regardless of letter case. Both the stacked chart and detail table update as you type. Searching a material such as `iron` shows its available forms; searching an item such as `iron ore` shows that item. Clear the field with its clear button to restore the category. The query stays in place when a new save updates the inventory.

Default map layers show caves, deep deposits, geysers, special resources, oil, drills, players and candidates. Dense layers such as ordinary ore are hidden. Changes are saved; **Restore default layers** resets them. Caves use entrance symbols, ore/deep deposits native mineral icons, and candidates dashed question marks. Installed drills add a red deposit ring, red name plate and green badge. All points stay at their actual coordinates and may overlap, without offsets or leader lines. Worlds with a saved home initially center nearby. World boundaries clip overflowing icon pixels without moving coordinates.

The Exotics layer and unconfirmed exotic candidates use the native Exotics category image, with a small candidate question mark/dashed frame. That image does not confirm mineral subtype or generation. Confirmed Red Exotics/Uranium use their own images. Every resource icon has an English label, including Exotics, geysers, oil, water, plants and deployables; labels stay visible when zoomed out and are clickable. User-created names keep their original spelling.

Installed drills show **Stock: … items**, the saved count in their output slots. Unpaired drills can be inspected directly. Click a mineral/stock label for item icons, quantities, slots and save time. Fuel containers and upgrade slots are excluded. Confirmed empty output is zero; incomplete data is unknown or a known count plus an unknown portion. Drill inventory updates with valid saves and is separate from remaining underground resources.

Prometheus includes Null Sector, the southern Ice Sheet and southeastern Ashlands. Resource, mission and animal maps share this terrain background. It is not evidence of in-game exploration or passable routes. The full map is bundled and updates do not require clearing old caches.

The top row's left side shows world/map, selected base and save time, beside a compact world selector. Small windows wrap text; hover for full source/update details. **Refresh** shows reading/retry status. Incomplete or corrupt writes retain the last valid view and mark it stale. The large title/logo row, statistic cards, green explanatory strip, map base toolbar, bottom resource table and status bar have been removed as requested.

## Current features and coverage

- **Prometheus:** 64 generated regions, 64 surface regions, 24,159 original ordinary-ore spawn points, 221 cave entrances, community cave numbers and candidate resources. Save merging prioritizes exact ActorPath. Spawn counts are not remaining ore quantities.
- **Vegetation/surface resources:** generated distribution regions for trees/plants/surface resources and game-asset water anchors. Counts of instances not recorded as destroyed require unique generation-descriptor/destruction-index matches; these are not final yields.
- **Deep deposits:** mineral type, remaining fields and drill placement. Explicit UID matches take priority. Inference requires no explicit association and a mutually unique three-dimensional match. Placement and operation are separate.
- **Missions:** an internal catalog of 128 Operations and 11 SMPL3 types, with only confirmed unlocked/active Operations/NPCs in the normal interface. All 24 Prometheus Operations have dedicated English guides. Prerequisites use local Profile account mission trees only when identity matches the world host. History/C0NT4CT modules come from the selected save. Profile has its own timestamp, and changes refresh missions. Public sync excludes host Profile and never borrows a friend's account; missions missing required evidence stay outside the actionable list.
- **Mission stages:** the same primary base as resources/inventory supplies horizontal straight-line distances. Unlocated stages stay unresolved; candidate distances do not become confirmed distance rankings.
- **Inventory:** deployed container/equipment records from the valid snapshot and 3,037 installed-version item definitions. Original form counts remain separate in tooltips and details, while each material uses one stacked bar with two colors and no legend. Heights use actual item counts without smelting conversions. Valid saves/base changes trigger background updates.
- **Animals:** explicit taming/owner evidence, owner columns, native animal/item icons, status bars and maps. Of 241 species, 219 have native icons and 22 retain question marks. Images represent species/bestiary groups, not individual coat, sex or appearance variation.
- **Other maps:** Olympus has a background, cave catalog and local-save calibration. Styx has a background/community catalog; missing calibration prevents grid conversion/candidate merging. Complete ordinary-ore reconstruction and dedicated guides for other maps are unfinished; coverage details show the gaps.

## Data limitations

- Saves are not complete live lists of every world object. Missing harvesting records do not prove untouched resources, missing quantities are not zero, and negative values are not automatically infinite.
- Ore-counter differences are baseline resource estimates, not guaranteed yields. Some dynamic special-resource types stay unknown without static-asset associations.
- Community points remain candidates until save-confirmed generation. Proximity-only cave associations are inferred.
- Saved player positions are last saved locations unless a verified game source is shown. Mission-record points are not verified entrances. Displayed distances are horizontal straight-line distances.
- Game positions cover only locally loaded/synchronized and precisely matched animals/players. Thirty-second samples are not continuous live tracking. Only verified zero-world-origin coordinates are accepted; other cases retain saved positions. Cow oxygen/stamina and milk capacity remain unknown. Missing owner files do not justify guessed bonuses. **Owner data…** connects read-only backups for saved-value calculations; game readings do not depend on them.
- SMPL3 random selection, DLC rights and character technology are not fully reconstructed. Missing required evidence stays unconfirmed. Inventory does not automatically complete mission preparation checklists.
- Inventory uses horizontal radius, not ownership, floor or accessibility. It excludes player/mount inventories, ground items, duplicate global pools, worn items and equipment upgrades. Attachments/nested bags inside stored equipment are not expanded. Missing/conflicting counts remain unknown.
- Undecoded non-core Unreal properties produce warnings. Incomplete deployment data cannot prove drill absence. Version mismatches disable unsupported mineral rules/eligibility conclusions.
- **Data coverage & sources** shows sources, versions, calibration and coverage; build details are in `DATA_SOURCES.md`.

## Settings and safety

The app does not launch or control the game. Original saves/game installation files are normally read-only. Its sole world-file write is restoration from **World saves**, after version selection and explicit confirmation, with the game/server closed and a current-world safety copy preserved first.

Base/world/layer settings use `%USERPROFILE%/.icarus-helper/settings.json` across installation locations and launch methods. Initial migration recovers old LocalAppData/Codex LocalCache bases while retaining originals and `legacy-backups`. It does not repeat or resurrect deleted bases. Multiple windows merge only their own changes.

## World saves and rollback

Select an actual world and open **World saves** for each version's world-save time, backup-creation time, actual size and verification. **Back Up Now** and **Open Backup Folder** are available. The shared map has no host save; select an actual source first.

Backups use `%USERPROFILE%/IcarusSaveGuard/`. Existing GuoGuoGaGa versions are reused without moving/deleting them. New worlds are isolated by complete world identity. Only the selected file is registered; other-host paths are not inferred. `worlds.json` stores per-world settings. Private data is excluded from the project/application Release.

**Backup retention** of **0 days / Keep forever** disables expiration. A positive value plus **Apply Retention** first shows the number/size to remove. Expiration uses backup creation time and always keeps the latest valid version. Corrupt versions and unknown/conflicting capture times are not automatically deleted. `Restores` safety copies are kept forever. GuoGuoGaGa's currently authorized retention is 14 days; new worlds default to 0.

**Saved Player Status** and selected-version details are parsed from that backup itself: names, saved health, survival/playing flags and available positions, food, water, oxygen and respawn counts. Offline characters may retain last saved values; these do not prove they were online at backup time. Missing individual update times/verified health maxima do not become invented live timestamps/percentages. Missing, conflicting, unmatched and unsupported values remain unknown.

List/detail health bars show actual values. Stripes mean an unknown maximum, not a percentage. Known zero has an empty track; unknown values use question marks. Alive/dead still follows explicit saved flags; offline/last-saved indicators remain.

Select a version, choose **Restore Selected Version…**, check the world, time and actual file path, then confirm. Fully close ICARUS and its dedicated server first. Running or unverifiable process states block restoration; the app never closes the game for you. It permanently preserves the current world and receipt before replacement and full verification. Buildings, animals and world items roll back together; this is not a complete account/character rollback.

Independent backup mode monitors registered sources after the window closes and checks retention every minute. The limited-permission `IcarusHelperWorldBackups` task starts/restarts it at sign-in and through a recovery trigger. Each complete, valid changed save is preserved once; identical content is deduplicated. Five-minute intervals come from game settings and do not guarantee five-minute restore points in every situation. The page shows background status/errors, with details in `daemon-status.json`.

Last valid copies, map caches and diagnostics remain at `%LOCALAPPDATA%/IcarusHelper/`. **Data coverage & sources** shows the actual settings path. The source project contains no complete private saves/account credentials. Tests use synthetic saves or read-only snapshots; no building, mining or mission actions were performed in the original world for testing.

## Development and builds

Verified environment: Windows, Python 3.12, PySide6 6.11.1, PyInstaller 6.21.0. Unicode/OneDrive paths are supported.

```powershell
python -m pip install -r requirements-dev.txt
python main.py
python -m pytest -q
```

Compact assets under `icarus_helper/atlas/26313227/` allow a clone to run/build without private caches. To regenerate assets, configure your own legitimate game sources using `DATA_SOURCES.md` and `tools/README.md`. Packaging includes only runtime compact JSON/backgrounds, excluding large extracted assets:

```powershell
powershell -NoProfile -File tools/build_app.ps1 -Shortcut
```

Default output: `dist/ICARUS Helper/`. Keep the entire folder. Runtime requires no old analysis directory, Python, repak or UAssetAPI.

Builds isolate native-tool lookup paths, check ICU exports required by Qt, and run the packaged EXE with independent empty settings to verify shared map/base, background and icons. `--self-test-report artifacts/packaged-startup.json --self-test-reference-settings <your-settings-file>` copies source/base settings read-only into temporary isolated settings and checks actual inventory without modifying original bases. Startup logging begins before Qt loads.

Separate output directories preserve an app already in use:

```powershell
powershell -NoProfile -File tools/build_app.ps1 -OutputRoot dist/releases/v0.4.0
python tools/package_release.py --bundle "dist/releases/v0.4.0/ICARUS Helper" --output releases/v0.4.0 --version v0.4.0
```

First-launch checks without user settings are mandatory, recorded in `artifacts/packaged-shared-startup.json`. Personal-world checks use `--self-test-reference-settings` and are not public Release assets. Unused Qt PDF/Virtual Keyboard/QML/Quick components are removed only after native dependency checks. ZIPs include third-party licenses and per-file SHA-256 manifests, with `SHA256SUMS.txt` alongside.

`artifacts/`, `build/`, `dist/`, `releases/`, settings and save snapshots are excluded from Git. ZIPs are distributed through GitHub Releases.

Offline UI verification uses isolated settings and read-only actual saves:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python tools/verify_desktop.py
```

This writes `artifacts/ui/verification.json` and layout images, checking zoom/pan/filters, base isolation/persistence and guides. `--reference-settings "$env:USERPROFILE/.icarus-helper/settings.json"` copies current base settings read-only and adds inventory checks. Only temporary settings are modified. These are Qt component checks, not in-game actions.

`python tools/verify_animal_board.py` isolates the selected source and checks owner columns, icons, positioning, filters and small windows, writing `artifacts/animals-refined-20261102/`. These actual-save Qt checks are separate from Windows mouse/gameplay checks. Identifiable animals and an away-from-home case are required; see `tools/README.md`.

`python tools/verify_animal_capacities.py` records read-only snapshot checks, exact supply capacities, individual baselines and unknowns. The 470 native-rule offline cases execute limited native function bytes with synthetic inputs, without launching the game. They do not establish real-save integration, packaged startup or Windows interaction. Validation categories are recorded separately.

## Modules

- `models.py`: UI-independent world/resource models.
- `saves.py`: discovery, stable read-only snapshots, package/Unreal decoding, resources and drill matching.
- `catalog.py`: static maps, mineral-generation rules, dynamic merging, vegetation and coverage.
- `missions.py`, `data/missions.json`: mission status, recommendations, version checks and guides.
- `operation_eligibility.py`, `data/operation_rules.json`: version-bound native gates and account/world evidence for unlocked and active Operations.
- `mission_locations.py`, `mission_map.py`: exact-stage locations, candidates, unresolved targets and the selected mission's map.
- `mission_overview.py`, `mission_clusters.py`, `mission_planner_view.py`: all eligible mission locations and bounded nearby groups.
- `controller.py`: file watching, debounce, background work, recovery and world isolation.
- `map_widget.py`, `ui.py`: maps, details, right-click bases and missions.
- `inventory.py`, `data/inventory_items.json`: same-snapshot containers, classification and quantities.
- `inventory_view.py`: background totals, two-color stacked material bars without a legend, per-form icons/tooltips and container details.
- `drill_inventory.py`: verified General output slots, counts and uncertainty, separate from fuel and remaining mineral resources.
- `item_icons.py`, `data/item_icons.json`, `assets/items/`: native icon mapping, transparent thumbnails and fallback.
- `animals.py`, `data/animal_catalog.json`: taming evidence, state/inventory, anonymous owner keys and verified level/experience.
- `animal_capacities.py`, `data/animal_capacities.json`, `data/animal_capacity_effects.json`: version-bound capacities/bonuses, supply maxima and individual health/stamina reconstruction with explicit gaps.
- `animals_view.py`, `animal_widgets.py`, `animal_map.py`: owner-column cards, status bars, item icons and animal maps.
- `live_animal_stats.py`, `live_world_fields.py`, `live_actor_cache.py`, `live_positions.py`: verified read-only game samples, generation-bound locators and saved-position fallbacks.
- `animal_icons.py`, `data/animal_icons.json`, `assets/animals/`: native bestiary images, source mapping and question-mark fallback.
- `settings.py`: app settings and atomic writes.
- `world_backups.py`, `world_backups_view.py`: history, size/time, per-world retention and confirmed restoration.
- `backup_players.py`: names, health/survival, supplies and positions from selected backups only, retaining offline/unknown states.
- `backup_health_bars.py`: bars distinguishing known zero, unknown value and unknown maximum without invented percentages.
- `backup_daemon.py`: window-independent backups/cleanup and sign-in/recovery task.
- `release_sync.py`, `tools/publish_world_sync.py`: verified public-world downloads and the separately authorized host publisher.

Read `AGENTS.md` before code changes and check it against actual supported behavior afterward.
