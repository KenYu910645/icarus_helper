# Unreleased source changes — 2026-11-04

- The interface, resource/item labels, messages, mission guides and operating guide use English. User-created world, base, character and animal names are preserved.
- Base inventory supports item/material search with a clear button, case-insensitive matching and queries retained across save refreshes. Each material uses one stacked bar with orange lower segments and teal upper segments, preserving actual counts, separate form icons, details and unknown quantities.
- Mission lists and the overview show all confirmed unlocked or active Operations/NPC missions, excluding completed, locked, unconfirmed and SIMPLE/SMPL3 entries. Account evidence is bound to the selected host/world. A busy Operation slot leaves other unlocked missions waiting; nearby groups retain candidate and unresolved-stage distinctions.
- Same-context inventory refresh failures retain the last valid chart and details with a stale/error message. Invalid results are not emitted as successful loads, and world/base/radius changes remain isolated.
- Code cleanup removes unused imports and corrects the symbolic-link rejection test to expect the English error.

The retained local build is `dist/inventory-search-20261103/ICARUS Helper/ICARUS Helper.exe`. This source update does not publish a new Windows ZIP or replace existing desktop/background processes. Earlier builds and public release history remain available.

# v0.2.1

Added Chinese labels beneath every resource icon, including Exotics and geysers. Labels remain visible when zoomed out and can be clicked for details. Coordinates, candidate status and base/drill drawing priority are unchanged. The English update above supersedes this display language.

- Includes v0.2.0's animals, full Null Sector map, base persistence and inventory sorting.
- Empty settings open the Prometheus shared map and Home, with coordinates identical to v0.1.0/v0.2.0; existing settings are not reset.
- Download `ICARUS-Helper-v0.2.1-Windows-x64.zip`, extract completely, open `ICARUS Helper.exe`, and retain `_internal`.
- Actual resources, inventory, animals and mission progress require a host save. Species continue to use native English names.

Sources/rights are in `DATA_SOURCES.md`, `THIRD_PARTY_NOTICES.md` and `licenses/`. The ZIP has a per-file SHA-256 manifest, with `SHA256SUMS.txt` beside the download.

# v0.2.0

Added tamed-animal management, complete Prometheus Null Sector terrain, base-persistence fixes and icon fixes.

## Download and update

1. Download `ICARUS-Helper-v0.2.0-Windows-x64.zip` and extract completely into a new folder.
2. Open `ICARUS Helper.exe` and retain `_internal`. Python and separate map downloads are not required.
3. Empty settings open the Prometheus shared map and Home at v0.1.0's coordinates. Existing users retain their world, bases and layers.

## Changes

- **Animals:** owner columns with native images, level/experience, health/supply bars, inventory and two-way card/map selection. English species names retain Chinese search aliases.
- **Capacities/sources:** current species, genetics, bloodlines, talents and verified owner evidence. When running-game version/world/animal checks succeed, read-only health/applicable stamina current/max values use native UI sources. Incomplete evidence retains known baselines or unknown values.
- **Complete map:** Null Sector's southern Ice Sheet and southeastern Ashlands appear in resource/mission/animal maps. Old caches cannot hide the full map. All coordinates and shared-base locations are unchanged.
- **Base persistence:** fixed `%USERPROFILE%/.icarus-helper/settings.json`, initial migration with originals/backups retained, and per-window change merging prevent losses caused by launch methods or stale windows.
- **Display:** exotic candidates use category images with small question marks, preserving candidate status. Inventory charts/details sort by descending actual counts. Shortcut icon sources were fixed.

## Data and scope at this release

- Shared maps contain no private animals, accounts, saves, inventory or progress. Actual-world data needs a host save. This release did not synchronize saves between computers; later public sync is documented in the current README.
- Animal positions use saves. Only verified health/stamina game sources show separate reading times. Unknown values are not zero. Cow oxygen/stamina and milk capacity were not fully verified.
- Assets/native rules target Steam build 26313227. Unsupported versions warn and fall back conservatively. Prometheus has the most complete support.
- Original game files/saves are read-only in this release; the app does not control characters, write to game processes or launch original worlds. Later explicitly confirmed restoration is documented in the README.

Sources/rights are in `DATA_SOURCES.md`, `THIRD_PARTY_NOTICES.md` and `licenses/`. `SHA256SUMS.txt` accompanies the download, with a per-file manifest inside the ZIP.

# v0.1.0

The first public portable Windows x64 release.

## Download and open

1. Download `ICARUS-Helper-v0.1.0-Windows-x64.zip`.
2. Extract into a writable folder, open `ICARUS Helper.exe`, and retain `_internal`.
3. First launch shows the Prometheus shared map and Home, without Python or separate map downloads.

## Shared map and host saves

- Static map data/base location exclude private saves, Steam accounts, players, inventory and progress.
- Without host data, resources/drill placement remain unknown. This release offered guide previews and never invented inventory counts.
- **Choose host save…** selects a valid source. A one-time copy does not automatically synchronize; subsequent valid changes to the selected file are tracked.
- Shared/actual-world bases are separate. Default coordinates are not assigned to other worlds.

## Known scope

- Verified development/packaging: Windows x64, Python 3.12 and PySide6 6.11.1; not tested on every user's computer.
- Assets target Steam build 26313227. Updates may disable mineral reconstruction/eligibility rules until reverified.
- Prometheus has the most complete support; Olympus/Styx have less coverage/calibration.
- Original game files/saves remain read-only in this release. The app does not control or launch the game.

Third-party sources/rights are in `THIRD_PARTY_NOTICES.md` and `licenses/`. `SHA256SUMS.txt` verifies the downloaded ZIP.
