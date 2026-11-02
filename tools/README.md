# 地圖與物品資料建置工具

桌面 APP 執行時只讀取本機快取或封裝內的精簡 atlas，不依賴此工具資料夾、舊分析專案、repak、UAssetAPI、網路或 Steam 寫入權限。原始遊戲檔與存檔均維持唯讀。

原始碼隨附 `icarus_helper/atlas/26313227/` 的精簡圖資。安裝 `requirements.txt` 後即可用 `python main.py` 啟動，不需先解包遊戲或準備個人快取；資料來源及目前覆蓋限制仍以 `DATA_SOURCES.md` 為準。以下工具僅供重新建置圖資／物品目錄，不是使用APP的必要步驟。

目前已驗證遊戲版本為 Steam build `26313227`。版本不符時，APP 停用舊版資產的普通礦還原；建置工具也拒絕覆寫舊快取。支援新版前必須重新驗證格式、座標轉換、FLOD索引與原生亂數規則。

## 來源與快取

- 遊戲安裝來源：以 `ICARUS_CONTENT` 指定 ICARUS 的 `Icarus\Content` 資料夾，未指定時使用預設 Steam 安裝位置。
- 本機快取：`%LOCALAPPDATA%\IcarusHelper\catalog_cache\26313227`。
- Prometheus 普通礦／地穴：64個 `T019_Generated_x*_y*.umap` 與配套 `.uexp`。
- 植被與地表資源分布：另64個 `heightmap_x*_y*.umap` 的 FLOD 實例群組；透過527種 `D_FLODDescriptions`／FoliageType資產確認可採集對應與生成實例數。
- 礦物生成：目前安裝版本的 `D_VoxelDistributionRegion`、`D_VoxelSetupData`，結合當前世界 `GameStateSeed` 與各礦點 `VoxelInstanceSeed`。
- 社群地穴編號、候選點與底圖：[Icarus Intel](https://www.icarusintel.com/)。取回時間、URL與各分類數量記錄在各地圖 `*_public_provenance.json`；潛在生成點不代表本世界確實存在。

## 重建

需要 Python 3.12、Pillow、PowerShell 7、repak與UAssetAPI（Unreal 4.27支援）。repak/UAssetAPI是額外建置工具，沒有包入APP。

請先準備合法取得的工具並設定來源，工具本身不隨此專案提供：

- `ICARUS_REPAK`：repak 執行檔的完整路徑；未指定時從 `PATH` 尋找 `repak`。
- `ICARUS_UASSET_LIBRARY`：`UAssetAPI.dll` 的完整路徑，同層相依DLL需保留；未指定時使用 `tools/vendor/UAssetAPI/UAssetAPI.dll`。
- `ICARUS_CONTENT`：遊戲的 `Icarus\Content` 資料夾。
- `ICARUS_STEAM_MANIFEST`：遊戲的 `appmanifest_1149460.acf` 路徑；未指定時依 `ICARUS_CONTENT` 推得 Steam manifest 位置。

例如在 PowerShell 設定（請換成自己的安裝位置）：

```powershell
$env:ICARUS_REPAK = 'D:\Tools\repak\repak.exe'
$env:ICARUS_UASSET_LIBRARY = 'D:\Tools\UAssetAPI\UAssetAPI.dll'
$env:ICARUS_CONTENT = 'D:\SteamLibrary\steamapps\common\Icarus\Icarus\Content'
$env:ICARUS_STEAM_MANIFEST = 'D:\SteamLibrary\steamapps\appmanifest_1149460.acf'
```

APP不需要 repak 或 UAssetAPI，也不讀取這兩項工具路徑；`ICARUS_STEAM_MANIFEST` 同時可用於APP的遊戲版本查核。重建前仍會核對已驗證build，不因更換路徑而略過版本限制。

從專案根目錄依序執行：

```powershell
python tools/build_map_catalog.py
python tools/build_foliage_lookup.py
python tools/refresh_atlas_sources.py
python -m unittest tests.test_catalog -v
```

`build_map_catalog.py` 自動讀取data.pak的實際掛載前綴，抽取必要資料表，再解碼64個generated與64個heightmap區塊。未支援的旋轉／縮放群組不提供猜測座標。FoliageType變更後需重新執行第二步。

背景圖目前包含 Prometheus、Olympus、Styx。Elysium圖層目錄可取得但使用不同貼圖路徑；目前沒有宣稱其底圖與座標已支援。

Prometheus 預設取用含 Null Sector 地形的 `prometheus2` tileset，其他地圖保留各自的來源。只更新 Prometheus 底圖、不重新下載候選資源或其他地圖：

```powershell
python tools/refresh_atlas_sources.py --map prometheus --background-only
```

圖塊快取按 tileset 隔離，避免重用舊 `prometheus` 黑底圖。工具核對全部64張512×512 JPEG後縮放拼成2048×2048，完整成功才原子替換成品；取回時間、hash與實際來源保存在原 provenance 的 `background`，不把更新底圖時間寫成候選資源清單的更新時間。重新封裝前，將驗證過的 `prometheus.jpg` 及 `prometheus_public_provenance.json` 同步至原始碼內 `icarus_helper/atlas/26313227/`。

## 精簡封裝白名單

只能封裝以下檔案，不應把整個快取加入APP：

- `prometheus_nodes.json`、`foliage_lookup.json`。
- `prometheus`、`olympus`、`styx` 的 `*_public.json`、`*_public_provenance.json` 與各自 `.jpg`。
- `game_data/World/D_VoxelDistributionRegion.json`、`game_data/World/D_VoxelSetupData.json`。

公開原始碼及封裝均只在 `icarus_helper/atlas/26313227/` 保留上述13個檔案，維持上述相對位置。APP優先使用同版本較新的本機精簡快取，否則讀取隨附副本。`game_assets/`、`tiles/`、`height_tiles/`、`image_tiles/`、完整私人存檔及其他大型中間檔不應封裝或提交。來源URL、遊戲build、資料hash與座標保留；這些來源紀錄不代表授予新的第三方資料授權。

## 資料含義與驗證

- 普通礦生成位置及礦種與剩餘量分開處理；無採掘記錄仍是狀態未知。
- 分布矩形是生成群組範圍，中心點不是單株位置。只納入能對應可採集代理物件的群組；裝飾幾何不當作資源。
- 群組使用完整 component/attach-parent 與 `WorldTileInfo.Position` 平移鏈；不可以直接把 `BuiltInstanceBounds` 當世界座標。2026-10-31唯讀快照比對2,969個群組均落於對應存檔 `FLODTile.Transform` 範圍內。
- 只有mesh唯一對應FLOD資料列、存檔區塊及RecordIndex，破壞索引皆合法，且不存在尚未解釋的動態／狀態實例時，才計算「未列為已破壞的生成實例」；單位不是物品產量，也不保證動態再生狀態。
- 現有快照驗證329筆非預設密度礦種全部吻合，2,505個群組能唯一對應破壞索引；這些是特定快照驗證結果，不能用作未來存檔的固定數量。

## 多階段任務位置目錄

`build_mission_location_catalog.py`讀取已準備好的目前版本資料，產生`icarus_helper/data/mission_locations.json`。這份精簡JSON隨`data/`封裝；不需額外封裝完整地圖或任務藍圖。

來源快取為`%LOCALAPPDATA%\IcarusHelper\cache\mission_source_26313227`，需有：

- `Quests/D_Quests.json`與`Quests/D_QuestQueries.json`，從同版本`Content/Data/data.pak`唯讀解包。
- 主地圖`Terrain_019.umap/.uexp`，位於既有`catalog_cache/.../game_assets`；`extract_mission_location_assets.ps1`只取帶GameplayTags的任務標記及RootComponent位置。
- `location_assets/`保留D_Quests中PRO／DYN任務Class引用的`.uasset/.uexp`。依各pak索引唯讀抽取，Windows命令列每批最多約60個路徑，避免超過命令列長度。`extract_mission_query_refs.ps1`只輸出名稱表中的已知查詢引用，不序列化整個資產。

準備完上述來源後執行（`$taskLibrary`指定UAssetAPI.dll）：

```powershell
$taskMissionSource = Join-Path $env:LOCALAPPDATA 'IcarusHelper\cache\mission_source_26313227'
$taskMainMap = Join-Path $env:LOCALAPPDATA 'IcarusHelper\catalog_cache\26313227\game_assets\Icarus\Content\Maps\Terrain_019_DLC\Terrain_019.umap'
pwsh -NoProfile -File tools/extract_mission_location_assets.ps1 -AssetPath $taskMainMap -OutputPath "$taskMissionSource\Terrain_019_locations_raw.json" -UAssetLibrary $taskLibrary
pwsh -NoProfile -File tools/extract_mission_query_refs.ps1 -AssetRoot "$taskMissionSource\location_assets" -QueriesPath "$taskMissionSource\Quests\D_QuestQueries.json" -OutputPath "$taskMissionSource\blueprint_location_queries.json" -UAssetLibrary $taskLibrary
python tools/build_mission_location_catalog.py --source $taskMissionSource --build-id 26313227
python -m pytest tests/test_mission_locations.py -q
```

資料保留來源hash與build。查詢位置僅是候選／搜尋區域；只有當前世界的明確關聯或位置旗標才提升為已知執行位置。執行階段不讀所有存檔備份，也不猜測尚未生成的動態投放艙。
- Prometheus校準221個資產入口，其中216個距社群點小於30m；Olympus另外以測試世界11入口比對，10個小於30m。Styx尚無本機校準證據，因此不顯示已校準格位。

## 家中物資目錄

`build_inventory_catalog.py`唯讀擷取目前安裝版本的三份資料表，再產生`icarus_helper/data/inventory_items.json`。需要Python 3.12、repak及`pefile`；這個工具不需要UAssetAPI，也不讀取私人存檔或啟動遊戲。repak位置沿用前述`ICARUS_REPAK`設定，遊戲來源沿用`catalog_inventory.py`的`GAME`。

從專案根目錄執行：

```powershell
python tools/build_inventory_catalog.py
python -m pytest tests/test_inventory.py -q --junitxml=artifacts/inventory-tests.xml
```

建置會先檢查Steam manifest的build必須是已驗證的`26313227`，從`Content/Data/data.pak`自動辨識實際掛載前綴，僅解包：

- `Items/D_ItemsStatic.json`：物品與Itemable對應、分類標籤。
- `Traits/D_Itemable.json`：官方英文名稱及堆疊上限。
- `Inventory/D_InventoryID.json`：庫存類型與列舉索引。

原始資料表留在`%LOCALAPPDATA%\IcarusHelper\catalog_cache\26313227\inventory_source\`；不放入APP或提交完整解包來源。工具另唯讀分析`Content`同層`Binaries/Win64/Icarus-Win64-Shipping.exe`的UE反射列舉表，依名稱指標及int64值核對`EDynamicItemProperties::ItemableStack=7`、`Durability=6`、`InventoryContainer_LinkedInventoryId=13`；不同或有歧義時中止，不以觀察到的數量猜列舉值。

目前build產生3,037個具有Itemable關聯的精簡物品定義，記錄來源資料表SHA-256、EXE SHA-256、build與查核日期。輸出只保留物品識別碼、名稱、分類、材質、形式與堆疊上限；精簡JSON隨既有`icarus_helper/data/`一起封裝，不需要新增atlas檔案，也不封裝遊戲EXE、repak或`inventory_source/`。查核日期及金屬分類對照目前是已驗證版本的程式資料；支援新版時須更新build、日期與對照，重新核對列舉並執行測試，不能只改版本號繞過檢查。

執行時`inventory.py`只從有效世界快照及這份精簡目錄計算，不在切換統計類別時解包遊戲。`summarize_inventory(snapshot, base, radius_m=100)`預設依基地水平100m納入部署容器／設備的儲存、加工、燃料、種子、肥料與砲塔彈藥槽；範圍可調整，並不是遊戲基地所有權邊界。排除玩家／坐騎背包、掉落物、其他來源庫存、全域物品池及已裝備／設備升級槽；普通儲存槽內的裝備可計數，但內嵌配件／袋內物品尚未展開。不按樓層、所有權或可取用性篩選。

數量只讀取已驗證的堆疊欄位；`Location`、耐久、重量與堆疊上限都不是目前物品件數。原礦與錠在同材質使用不同數列，不換算熔煉比例；鋼不換算成鐵。未知物品識別碼保留，未知數量保留`None`及已確認的部分，重複記錄去重並保留衝突／缺漏資訊。

真實唯讀逐槽對帳摘要位於`artifacts/inventory-current.json`，包含快照時間、hash、原檔未變檢查與獨立對帳結果，不包含完整私人存檔。2026-11-01當次基地100m驗證為46個容器、457個堆疊、126種／19,339件物品；數字只屬該快照，並非工具的固定預期值。21項合成測試與此真實對帳分開記錄，均不代稱遊戲內物品轉移或Windows桌面互動驗證。

## 助手應用程式圖示

應用程式本身的圖示與物品縮圖分開：`extract_app_icon.py`唯讀擷取遊戲來源到`assets/game-native.ico/png/json`；助手使用`app-master.png`的金色標誌加綠色H角標編輯成果。執行`python tools/export_app_icon.py`可重建`app.ico`六種Windows尺寸、`app.png`預覽與`app.json`輸出hash，不重新生成或修改圖案。`app.json`保留built-in image_gen模式與完整prompt。

`build_app.ps1 -Shortcut`會把助手ICO另存到成品資料夾的`.icons/helper-<hash16>.ico`（預設為`dist/ICARUS Helper/.icons/`）並更新桌面捷徑的IconLocation。圖示保存在實際成品路徑，避免Store應用程式的LocalAppData虛擬化讓Explorer讀不到檔案；新內容路徑也可避開舊快取。不刪系統快取或重啟Explorer。

## 物品原生圖示

先建好上述物品目錄與來源表，再執行：

```powershell
python tools/build_item_icons.py
```

工具使用同一已驗證build的`D_ItemsStatic.Itemable`與`D_Itemable.Icon`關聯，從pak中取出明確的貼圖引用。需要既有repak與Pillow；原始貼圖封裝及pak索引留在`%LOCALAPPDATA%/IcarusHelper/catalog_cache/26313227/item_icon_source/`，不放入成品。

輸出`icarus_helper/data/item_icons.json`與`icarus_helper/assets/items/*.png`；PNG最大128px、保留alpha，以貼圖路徑hash命名並共用去重。manifest保存來源資料表hash、貼圖來源pak／路徑／hash、像素格式、原圖與縮圖尺寸、PNG hash，以及缺圖原因。解碼只接受可驗證尺寸、inline bulk offset與bytecount的貼圖，未支援格式不偽造圖片。重建後檢查manifest的`coverage`與`missing`，再執行物資UI／圖示測試。

`build_app.ps1`明確封裝`assets/items/`和`data/`。封裝EXE自我檢查會驗證目前基地物品的原生圖示路徑及Qt解碼，並在`artifacts/packaged-startup.json`記錄可用圖示數與缺圖物品；執行時不需要遊戲檔、repak、Pillow或網路。


## 動物等級曲線重建

`read_animal_assets.ps1`以PowerShell 7及既有repak／UAssetAPI，唯讀擷取兩條經驗曲線及相關藍圖到指定的私人工作目錄。參數為`-Repak`、`-Content`、`-UAssetLibrary`與`-OutputRoot`；不啟動遊戲，完整藍圖輸出不得提交Git。

`build_animal_level_catalog.py --repak <工具> --content <Icarus/Content> --curve-dir <上述輸出> --catalog <既有animal_catalog.json> --output <新目錄>`可重建整數經驗門檻。工具先驗證目前遊戲EXE雜湊與build，再依已核對的單精度非加權cubic運算重現原生門檻；來源、版本或曲線型態不同就停止。新版本仍須重新核對原生與介面契約，不能只更新版本號。

執行時`animals.py`以同一組門檻計算等級及本級經驗條的`experience_progress`，保留零級、滿級、未知與曲線驗證。狀態容量另依下節目錄與原生規則計算；生命／耐力的個體重建仍不能稱為完整目前上限。

## 動物狀態容量與加成目錄

下列工具需要已驗證build26313227、對應EXE雜湊、repak、PowerShell 7及UAssetAPI；沿用本文件的`ICARUS_CONTENT`、`ICARUS_REPAK`與`ICARUS_UASSET_LIBRARY`環境設定，不使用私人分析路徑，也不啟動遊戲或寫入存檔：

```powershell
python tools/build_animal_capacity_catalog.py
python tools/build_animal_capacity_effects.py
python -m pytest tests/test_animal_capacity_catalog.py tests/test_animal_capacity_effects.py -q
```

`build_animal_capacity_catalog.py`產生`data/animal_capacities.json`，保存`D_AISetup.AIGrowth → D_AIGrowth.Base/Health/CustomStats`的物種欄位與明確引用的原生曲線鍵值。`build_animal_capacity_effects.py`產生`data/animal_capacity_effects.json`，保存遺傳／血統曲線、394個動物天賦的逐rank完整stat、779個modifier的最大值影響及NPC／保存旗標、118個equippable定義與154個item→trait關聯，以及主人stat轉換與原生規則。兩份目錄包含來源SHA-256，完整表與原始曲線留在本機建置快取。需要改用其他工具位置時，先讀各工具的`--help`；PowerShell參數名稱分別為`--pwsh`與`--powershell`。

加成目錄明確區分已知無直接影響的列與未知列；`known_ineffective`僅表示該已知資料列沒有直接五種容量stat，不能忽略自訂行為、間接效果、保存物品的`AdditionalStats`、`Alterations`與未展開配件。不存在的列不得默認為0。遺傳輸入是保存的遺傳值，血統輸入是原生等級；曲線結果逐條向零截斷，天賦選`Rewards[rank-1]`且不累加低rank。生命／耐力最終計算最低1，三種補給容量無最低1規則；衍生`Maximum*_+`快取不再另加一次。

目前可精確處理已驗證物種的食物、水分及氧氣容量；牛的存檔氧氣與耐力尚未驗證，保留未知。生命與可計算耐力提供個體遺傳、血統與天賦，並透過`owner_stats.py`核對精確主人角色／帳號圖鑑／裝備效果；完整持續來源以`*`標記，主人來源未完整則為`≈`。原生程式已證實recorder會過濾`SaveToDatabase=false`的臨時效果，因此持續上限仍不是完整即時上限。`build_owner_stat_catalog.py`沿用上述ICARUS環境設定，可重建2319個天賦、107個圖鑑及完整物品／裝備等來源索引。獨立角色／帳號檔各保留hash及時間，不強行當作同次世界資料；缺隊友檔不當作零加成。

使用目前所選世界做唯讀核對：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python tools/verify_animal_capacities.py
```

驗證輸出位於`artifacts/animal-capacities-20261102/`，包含來源／設定未變檢查、同快照狀態、容量分類及元件畫面。工具的實際成功結果才算真實存檔唯讀驗證；來源必須有受支援且可辨識的動物，不能把某份案例數量寫成所有世界的固定預期。原生容量運算另有470例離線Unicorn仿真，證據為該目錄的`native-capacity-contract.json`，僅執行有限函式位元組與合成記憶體，不啟動遊戲。離線仿真、真實存檔唯讀／Qt元件、封裝啟動與Windows桌面互動是不同驗收，須各自保留結果。

## 動物原生種類圖示

準備上述合法遊戲來源、repak、Pillow及`data/animal_catalog.json`後，執行：

```powershell
python tools/build_animal_icons.py
```

工具先核對build26313227，再從`data.pak`讀取`AI/D_AISetup.json`及`Bestiary/D_BestiaryData.json`，依`BestiaryGroup → Image`明確關聯擷取對應原生貼圖。索引缺失或不唯一不猜圖；貼圖解碼沿用物品圖示的尺寸、bulk offset及bytecount驗證。透明邊界裁切後等比例縮至最大128px，按貼圖路徑hash命名並去重。

輸出為`icarus_helper/data/animal_icons.json`及`icarus_helper/assets/animals/*.png`，目前涵蓋241個種類定義中的219個，另22個記錄缺圖原因並使用問號。manifest保存資料表／原始貼圖／PNG雜湊、pak引用、裁切範圍與尺寸。原生圖屬圖鑑種類／群組代表，不能用來判斷個體毛色、性別或幼獸的精確外觀。原始貼圖封裝保留於`%LOCALAPPDATA%/IcarusHelper/catalog_cache/26313227/animal_icon_source/`，pak索引沿用物品圖示建置快取；不提交完整解包資產。

`build_app.ps1`已納入`assets/animals/`及既有`data/`白名單。執行時`animal_icons.py`直接用Qt讀取縮圖，沒有遊戲、repak、Pillow或網路依賴；來源未知、檔案缺失或無法解碼時使用問號備援。

## 馴養動物卡片與地圖驗證

`python tools/verify_animal_owner_live.py --output artifacts/animal-owner-live-20261102`核對目前所選世界的飼養者來源與遊戲／存檔狀態成對值，並以隔離設定渲染寬／窄畫面。遊戲可自行執行；工具不操作角色。來源hash前後變動只記錄為可能的外部存檔活動，使用者設定則必須未變。當次12隻及本機主人30%生命／10%耐力是案例前提，工具可調整動物數量；完整報告與畫面屬私人artifacts，不提交。原生UI同源getter與限制詳見DATA_SOURCES；合成記憶體測試不代稱遊戲實讀或桌面互動。

合成資料的Qt測試可獨立執行，不操作遊戲：

```powershell
python -m pytest tests/test_animal_progress.py tests/test_animal_owner_groups.py tests/test_animal_board.py tests/test_animal_map.py -q
```

主人欄位以有效平台ID與角色slot的穩定SHA-256鍵分組，欄名僅顯示角色名稱；同名不同角色分開，無效主人歸入未知欄。動物卡片與地圖使用同一次存檔，地圖標記維持原位；測試涵蓋分欄、經驗門檻、未知值、卡片／地圖雙向選取、定位、篩選、刷新選取與世界切換清除。

若目前已選真實來源含可辨識動物及至少一隻離基地100m以上的動物，可執行唯讀元件驗證：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python tools/verify_animal_board.py
```

工具讀取固定使用者設定的目前來源與基地，將設定放入獨立暫存目錄，背景讀取有效存檔，驗證原生動物圖、飼養者分欄、地圖點數、離家動物定位、篩選同步、小視窗與清除。這份驗證要求當次動物均有圖示，屬有明確前提的案例檢查，不是所有世界必然通過的範例。結果及畫面位於`artifacts/animals-refined-20261102/`，結束前核對原始存檔hash及使用者設定位元組未變。畫面與結果含私人世界資訊，不提交Git或公開Release。

上述為Qt元件及唯讀真實快照驗證，不能代稱Windows桌面滑鼠操作、封裝交付或遊戲內動物行為驗證；這些驗收須另外記錄。
