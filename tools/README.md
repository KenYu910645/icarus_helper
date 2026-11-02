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

`build_app.ps1 -Shortcut`會把助手ICO另存到`%LOCALAPPDATA%/IcarusHelper/icons/helper-<hash16>.ico`並更新桌面捷徑的IconLocation，用新內容路徑避開舊快取；舊圖示副本保留，不刪系統快取或重啟Explorer。

## 物品原生圖示

先建好上述物品目錄與來源表，再執行：

```powershell
python tools/build_item_icons.py
```

工具使用同一已驗證build的`D_ItemsStatic.Itemable`與`D_Itemable.Icon`關聯，從pak中取出明確的貼圖引用。需要既有repak與Pillow；原始貼圖封裝及pak索引留在`%LOCALAPPDATA%/IcarusHelper/catalog_cache/26313227/item_icon_source/`，不放入成品。

輸出`icarus_helper/data/item_icons.json`與`icarus_helper/assets/items/*.png`；PNG最大128px、保留alpha，以貼圖路徑hash命名並共用去重。manifest保存來源資料表hash、貼圖來源pak／路徑／hash、像素格式、原圖與縮圖尺寸、PNG hash，以及缺圖原因。解碼只接受可驗證尺寸、inline bulk offset與bytecount的貼圖，未支援格式不偽造圖片。重建後檢查manifest的`coverage`與`missing`，再執行物資UI／圖示測試。

`build_app.ps1`明確封裝`assets/items/`和`data/`。封裝EXE自我檢查會驗證目前基地物品的原生圖示路徑及Qt解碼，並在`artifacts/packaged-startup.json`記錄可用圖示數與缺圖物品；執行時不需要遊戲檔、repak、Pillow或網路。
