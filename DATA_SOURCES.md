# 資料來源、覆蓋與驗證

本文件記錄截至2026-11-02開發時的本機資料查核。特定存檔的數量是當次唯讀快照結果，不是未來世界的固定值。APP會隨有效存檔重新計算。

## GitHub Releases 公開世界來源

2026-11-02世界持有人明確同意GuoGuoGaGa／Prometheus完整存檔公開，接受任何人下載。`world-sync-guoguogaga`為獨立資料Release，不影響APP最新安裝包；索引`world-index.json`保存schema_version、精確world_id、地圖、存檔／發布UTC時間、原始bytes大小及SHA-256，原始附件使用完整hash檔名。索引不含本機檔案路徑或上傳憑證；完整世界檔自身原有的玩家與帳號識別屬本次明確授權內容。其他世界、獨立角色／帳號檔、私人設定與診斷未分享。

`release_sync.py`限定正式GitHub repo／tag與官方附件轉址、下載大小／URL／索引版本，驗證原始hash、完整壓縮封裝及來源world_id／名稱／地圖／存檔時間。帶UTC epoch的索引查詢避免固定檔名舊快取，只有帶SHA的原始版本可被索引採用。有效資料才原子成為獨立last-good快取，失敗保留上次並標過期。朋友每120秒背景檢查，不把下載時間當主機存檔時間；不對接收端遊戲程序或帳號檔取值，也不允許下載副本登記備份或還原。

主機發布器限定已綁定來源、完整世界身分與兩輪穩定signature，先驗證獨立bytes副本再上傳，最後提交並重讀核對索引；成功後才記錄已發布hash。GitHub CLI只在主機沿用既有登入，不擷取或將憑證封裝給朋友。正式啟用最近3版的清理，只處理由此發布器實際上傳且可再次核對的資產；不影響本機備份歷史。主機與接收端仍須符合現版存檔／圖資相容性；未分享的主人持續來源仍保留缺口。

## Deep Mining Drill 產出庫存

鑽機庫存由同份有效世界快照的`DrillRecorderComponent.SavedInventories`取得，現版`InventoryID=1`對應`D_InventoryID.General`產出儲存槽；`ItemStaticData`提供物品，`DynamicData.Index=7`為`ItemableStack`件數。沿用物資解析的數量衝突處理，不能用槽位、耐久、重量或最大堆疊量推測件數。`InventoryID=7`的生質燃料罐及設備升級槽排除，明確空陣列與資料缺失分開。

`drill_inventory.py`按鑽機身分核對紀錄與槽位，並沿用`FoundationActorIcarusUID`優先／三維雙向唯一的礦床關聯；有衝突時不把舊庫存掛到其他礦床。摘要包含世界識別、同次存檔hash及時間，地圖顯示庫存第二行，點選可核對逐物品／槽位。數量是保存的物品件數，與`ResourceRemaining`、鑽機是否運轉及預期採礦產出不同。未知或不支援版本維持未知；共用圖不提供私人庫存。

## 世界備份的玩家保存值

`backup_players.py`與`world_backups.py`從每份已核對壓縮封裝、長度、內容hash及完整世界識別的備份，僅解碼`PlayerStateRecorderComponent`紀錄。已驗證`PlayerStateRecorderVersion=1`及`ActorStateRecorderVersion=3`；以`PlayerCharacterID.PlayerID/ChrSlot`對應同備份`ProspectInfo.AssociatedMembers.UserID/ChrSlot`，顯示`CharacterName`，不以同名或帳號單獨配對、不向介面輸出平台識別。

血量使用頂層`Health`，存活使用明確的`bIsAlive`，另提供`FoodLevel`、`WaterLevel`、`OxygenLevel`、`RadiationLevel`、`RespawnCount`與`Location`。數值零與False保留；未知、重複衝突及未支援版本不變成零。存活旗標未知時不能只依血量判斷死亡；血量與旗標不一致則保留兩者並提示。

成員的`IsCurrentlyPlaying`是同備份的參與旗標，False仍可能附完整玩家紀錄；該角色血量與位置應稱最後保存值，不是備份當時實際在線的證明。沒有逐人更新時間及可驗證MaxHealth，不顯示百分比或混用目前遊戲／角色資料。備份選版及還原確認使用該版本的摘要，候選hash在實際還原前再次核對，不能用新世界或另一份合法同世界內容取代確認時看到的資料。

2026-11-02以三份真實歷史備份逐玩家比對完整`load_snapshot`解碼，血量、存活及位置吻合；10/31 02:19例為563／488且存活旗標均True，22:45例包含0／False。這些是當次備份案例，不能當成目前玩家狀態。摘要只在APP的記憶體快取，不修改歷史存檔或metadata；解析摘要失敗降級未知，不改原有完整性核對與備份保存規則。

## 公開共用地圖

`icarus_helper/data/share_profile.json`只包含 Prometheus 地圖名稱與使用者指定公開的「我們家」位置（X=-38989.4、Y=-189495.1、Z=0 cm）。此位置來自已保存的基地設定；Z=0 是該基地的地圖定位值，沒有額外地形高度驗證。沒有加入世界識別碼、Steam帳號、存檔路徑、世界種子、任務／庫存或完整存檔。

首次啟動顯示獨立 `SharedMapContext`，沒有存檔來源、存檔時間或存檔hash；讀取靜態目錄，不合併私人存檔。共用基地與各真實世界隔離，資源目前狀態未知，任務清單與總覽為空；實際剩餘量、設備、玩家及庫存仍需主機存檔。第三方資料與圖示的來源不等於再授權，見 `THIRD_PARTY_NOTICES.md`。

## 目前地圖覆蓋

| 地圖 | 普通礦生成資產 | 地穴 | 背景圖 | 座標校準 |
| --- | --- | --- | --- | --- |
| Prometheus | 已解析64/64個generated區塊，24,159個礦點；另64/64個heightmap區塊提供可採集物件分布 | 221個安裝資產入口；276筆社群地穴位置／內部洞穴編號，合併後保留未配對入口 | 2048×2048，含 Null Sector 完整地形，封裝／本機快取 | 221入口中216個距社群位置小於30m；中位誤差3.48m，P90為8.73m |
| Olympus | 尚未建立全圖普通礦生成資產；仍顯示存檔可讀資源 | 157筆社群目錄，另合併存檔入口 | 2048×2048，本機快取 | 獨立測試世界11入口中10個小於30m；中位誤差18.44m |
| Styx | 尚未建立全圖普通礦生成資產 | 181筆社群目錄 | 2048×2048，本機快取 | 尚無本機校準證據，顯示未校準，不把候選點當世界事實 |

其他地圖尚未完成支援；不能沿用其他地圖的礦種或把未知資料顯示為零。Elysium社群圖層端點存在，但背景使用不同貼圖路徑，尚未列為完整支援。

## 資料來源

1. **目前存檔副本**：Windows `%LOCALAPPDATA%\Icarus\Saved\PlayerData\<帳號>\Prospects\` 中選定的世界。APP以穩定讀取、獨立快照、解壓縮長度及封裝hash驗證後解析；原始檔維持唯讀。
2. **安裝版本的遊戲資產**：目前驗證的 Steam build為 `26313227`，Unreal 4.27。解析 generated、heightmap、FoliageType與必要資料表，保留來源套件名稱、大小、修改時間及Steam manifest SHA-256。這些是本機資料，不將完整遊戲資產放進專案／APP。
3. **[Icarus Intel](https://www.icarusintel.com/)**：社群地穴編號、候選深層礦床／特殊資源與背景圖。各地圖 `*_public_provenance.json` 保存來源URL、查核時間與分類數量。候選點不證明該世界有生成資源。

Prometheus社群目錄當次取回包含276地穴、412深層礦床候選、29酵素泉、207特殊資源候選與21石油泉。Olympus與Styx的分類數量可在APP「資料覆蓋與來源」查看。實際世界礦床可能多於或少於社群候選數，以目前存檔為準。

### Null Sector 完整底圖（2026-11-02）

Prometheus 改用 Icarus Intel「Show map of NULL sector」所採用的 `prometheus2` 圖塊，來源為 `https://www.icarusintel.com/maps/prometheus2/3/{x}/{y}.jpg`。8×8 張原生512×512圖塊各縮為256×256後拼成2048×2048；沿用原本全圖範圍、方向與格位，不更動基地、資源或任務座標。遊戲原本刻意不提供 Null Sector 衛星圖，助手顯示的是額外地形參考，不代表探索進度、可通行性或當前資源生成。

`prometheus_public_provenance.json` 的 `background` 獨立保存底圖 tileset、實際網址、取回時間與 SHA-256，原資源清單的取回日期及數量保持原義。執行時以 hash 核對完整底圖，獨立於普通礦資產目錄的版本選擇；本機舊黑底圖不會遮掉封裝的新完整圖。實際採用資訊可在「資料覆蓋與來源」的地圖覆蓋 `background` 查閱。

## 普通礦種與剩餘量

- 生成位置依當前安裝地圖ActorPath。礦種結合該點 `VoxelInstanceSeed`、當前世界 `GameStateSeed`、`D_VoxelDistributionRegion` 與目前版本的原生FRandomStream加權規則還原。
- 存檔的明確 `VoxelResourceOverride` 優先。沒有覆寫值不能直接把所有礦點當成同一礦種。
- 當次真實快照中329筆非預設密度記錄全部吻合，0筆不符；這是礦種還原的交叉驗證，不代表所有礦點剩餘量都能精確取得。
- 無採掘記錄仍顯示採集狀態未知。只有來源足以證明耗盡、剩餘資源單位或已採集狀態時才顯示相應結果，工具加成後的最終物品產量不等同於原始資源單位。
- 洞內礦點與最近洞口的空間關聯另標示推定，不以距離宣稱確切洞內隸屬或可通行路線。

## 植物、樹木與地表資源

- 解析64個heightmap與generated區塊中的FLOD實例群組，透過527種FoliageType／`D_FLODDescriptions`確認可採集代理物件。沒有可採集證據的裝飾幾何不當作資源。
- 分布矩形是生成群組的範圍，中心標記不是單株位置；原始生成實例數不是目前剩餘量，也不是物品產量。
- 世界座標由 `BuiltInstanceBounds`、完整component／attach-parent的 `RelativeLocation` 與UMAP `WorldTileInfo.Position`合成。若旋轉／縮放尚未支援，或座標範圍無法證實，資料不得使用猜測修正。
- 已以62份存檔FLODTile Transform核對2,969個群組，全部落在對應區塊範圍；修正後沒有資源群組中心超出已校準全圖。
- 只有mesh唯一對應FLOD資料列、同區塊RecordIndex，且破壞索引合法、沒有待解釋的狀態／動態實例時，才顯示「未列為已破壞的生成實例」。當次快照有2,505群組符合條件；其餘維持未知。
- 已確認的生成分布包括樹木、植物／農作、地表礦石。完整水域邊界、動態再生及逐株採集物產量尚未還原。

## 水源與特殊資源

- Prometheus目前有312個地圖水體錨點；代表遊戲地圖的湖泊／河流Actor位置，不保證點位可到達、可飲用，也不代表完整水域邊界。
- 深層礦床／鑽機狀態以當前存檔識別關聯優先；社群地圖點若沒有存檔佐證，仍是候選。
- 已放置鑽機不等於正在運作；無法取得燃料／電力／運作證據時維持未知。

## 家中物資統計（2026-11-01）

`icarus_helper/inventory.py`的`summarize_inventory(snapshot, base, radius_m=100)`使用與地圖相同的有效存檔快照，依所選基地與可調整的水平範圍統計容器內物品。預設100m是APP的統計範圍設定，不是遊戲中的基地邊界、所有權或建築規則。

| 來源 | 使用欄位與含義 |
| --- | --- |
| 存檔的`DeployableRecorderComponent`、`DrillRecorderComponent` | `IcarusActorGUID`／`ActorPathName`識別部署容器及設備，`ActorTransform.Translation`決定是否在基地水平範圍內，`SavedInventories`提供庫存 |
| `SavedInventories[].Slots[]` | `ItemStaticData`識別物品，`DynamicData`中的`ItemableStack`提供物品堆疊件數；`Location`是槽位索引，不是數量 |
| `Content/Data/data.pak`中的`Items/D_ItemsStatic.json` | 物品識別碼、`Itemable`關聯與遊戲分類標籤；FName識別碼以不分大小寫方式對應 |
| `Traits/D_Itemable.json` | 官方英文顯示名稱與物品堆疊上限；上限只作目錄資料，不拿來代替目前堆疊件數 |
| `Inventory/D_InventoryID.json` | 辨識儲存、加工、燃料等庫存類型，與角色裝備、設備升級等槽位分開 |
| 目前安裝的`Icarus-Win64-Shipping.exe` | 唯讀查核UE反射列舉`EDynamicItemProperties`的名稱指標與整數值；不執行或修改遊戲 |

Steam build `26313227`已核對`EDynamicItemProperties::ItemableStack=7`、`Durability=6`及`InventoryContainer_LinkedInventoryId=13`。因此只取堆疊欄位，不把耐久、槽位編號、重量或轉換單位當成物品數量。精簡目錄`icarus_helper/data/inventory_items.json`在這個版本含3,037個有Itemable關聯的物品定義，保存三份資料表與EXE的SHA-256、build及查核日期；不是所有未來版本固定都有3,037種物品。常用物資與金屬使用繁中名稱，其餘保留可對照的遊戲英文名稱；來源中未辨識的物品保留原始識別碼及可讀數量。

金、銅、鐵、鋁、鉑、鈦等原礦與錠歸入同一材質的不同數列。鋼與冷鋼各自獨立，不折算成鐵；數量均為實際物品件數，沒有套用熔煉比例或把不同加工階段換算成等量金屬。分類依目前版本的明確物品對照與標籤，例如`Bauxite`為鋁礦、`Aluminium`為鋁錠；`Raw_Iron_Wood`實為結晶瘴氣，不因識別碼含Iron就算作鐵礦。

目前範圍納入一般儲存、加工、燃料、種子、肥料及砲塔彈藥槽（這個版本的InventoryID為1、6、7、15、16、24）。排除玩家與坐騎背包、掉落物、火箭／其他來源的庫存、全域ContainerManager物品池，以及裝備、設備插槽、升級與改裝槽。存放在普通儲存槽內的工具、武器與裝備仍是家中庫存；其內嵌配件及袋內物品目前不展開，也不把全域物品池直接加總，避免把其他地點或角色的物品重複計入。基地水平範圍不判定樓層、容器可取用性或所有權，因此不能宣稱已盤點家中所有可能的物品。

容器、庫存及槽位依識別去重；內容衝突的容器不計入，衝突槽的候選物品保留未知數量。缺少／不合法的堆疊數量顯示未知，並保留可確認的部分；不把未知當0。未指定基地、基地屬於其他世界、來源缺少容器庫存或偵測到不同安裝build時，回傳原因而非呈現已確認的零庫存。未知槽位類型與缺少位置另列於覆蓋資訊。

真實存檔唯讀驗證見`artifacts/inventory-current.json`：2026-11-01 02:07:30.664（Asia/Taipei）的當次快照，在當時所選基地100m內得到46個容器／設備、457個堆疊、126種物品、19,339件；另有10個內嵌庫存引用未展開、4個排除槽位。這些全是當次驗證數字，之後以新快照與所選範圍重新計算。獨立直接逐槽計數與彙總結果完全吻合，讀取前後原始存檔SHA-256相同。此證據是離線唯讀對帳，不代表已在遊戲中測試物品搬入／搬出後的完整操作流程。

`tests/test_inventory.py`目前21項合成測試涵蓋原礦／錠分組、耐久與數量分離、未知數量、半徑邊界、排除來源／裝備槽、重複與衝突、FName大小寫、空槽、未知物品、未指定／不同世界基地、版本變更及內嵌庫存引用。結果見`artifacts/inventory-tests.xml`；不以合成測試代稱真實存檔或桌面互動驗證。

## 助手識別圖示

助手圖示以已唯讀擷取的金色ICARUS標誌為參考，透過built-in image_gen加上右下角綠色H角標，區別助手與遊戲。`assets/game-native.*`保留原圖與遊戲來源hash；`app-master.png`保留編輯成果，`app.json`記錄工具、完整prompt與輸出hash。`export_app_icon.py`僅輸出ICO六尺寸及PNG預覽，不能把新版助手圖示描述為與遊戲payload完全相同。變更不寫入遊戲安裝檔。

## 物品圖示

`data/item_icons.json`保留明確的`D_ItemsStatic.Itemable → D_Itemable.Icon → cooked texture package`來源鏈，按完整物品識別碼對應原生圖片，不以名稱相似度猜圖。原礦、錠、鋼坯及其他物品各自使用來源指定的圖示；共用同一貼圖的物品可共用縮圖檔。

`tools/build_item_icons.py`唯讀讀取目前安裝版本的pak索引並擷取明確引用的貼圖，原始`.uasset/.uexp`保存在APP本機快取`item_icon_source/`。解碼核對UE4.27的尺寸、像素格式、mip數、inline bulk offset、bytecount與尾端尺寸；不相符的資料列為缺圖，不猜偏移。DDS轉換及PNG縮圖僅在建置時使用Pillow；保留原始alpha，透明像素的RGB不當作可見背景修補。

APP只納入最大128px的PNG及來源／hash對照，執行時由Qt載入並快取，不需要repak、Pillow、遊戲安裝檔或網路。manifest的`coverage`記錄實際圖示覆蓋，`missing`保留來源未指定、資產不存在或不支援格式的原因；缺圖以問號呈現，不改動物品名稱與數量。這些遊戲衍生縮圖供目前使用者本機助手使用，不將完整原始遊戲資產或安裝檔納入成品。

2026-11-01的build `26313227`有3,037個物品定義，其中3,035個對應原生圖示，去重為2,780張PF_DXT5縮圖，全部檢查PNG hash、尺寸與非空alpha。Nesting_Box未指定Icon，DEV_Transform_Tool的開發者圖示不在目前pak，兩者保持缺圖；這不是目前存檔的缺漏。目前基地126種物品全部有原生圖示，證據見`artifacts/item-icon-assets.json`與`artifacts/ui/20261101-item-icons/verification.json`。

2026-11-02地圖礦物圖示沿用同一原生圖庫。`map_icons.mineral_item_id()`按已確認的礦種對應採集物，例如深層`Aluminium → Bauxite`、`Iron → Metal_Ore`、`Silicon → Silica`，不誤用精煉錠；紅色原始異質與穩定異質分圖，`Exotic_Raw_Uranium → Uranium_Raw`。11/02 01:58:47唯讀快照的18種深層礦全部有原圖。未知礦種維持問號，候選框／鑽機角標／紅色外圈不改動座標；來源檢查與預覽見`artifacts/map-native-icon-tests.xml`、`artifacts/ui/20261102-map-native/`。

## 快取、版本相容性與封裝

- 精簡快取位於 `%LOCALAPPDATA%\IcarusHelper\catalog_cache\26313227`；原始碼與封裝內均隨附 `icarus_helper/atlas/26313227`，只含建置工具白名單的13份精簡檔案。
- 同版本本機快取與封裝副本以schema及產生時間比較，優先使用較新資料。本機快取不存在時仍可直接開啟封裝APP。
- 偵測到安裝build不符時，停用舊資產的普通礦生成還原並提示相容性問題，不靜默套用舊資料。新版本必須重新查核格式與規則。
- APP執行不依賴舊分析專案、repak、UAssetAPI或網路；相關工具只用於開發時重建資料。建置方式與精簡封裝白名單見 [tools/README.md](tools/README.md)。

## 自動化驗證

`tests/test_catalog.py`涵蓋：靜態點缺少採掘記錄、ActorPath精確合併、覆寫礦種、耗盡狀態保留、缺少世界種子、遊戲更新停用舊資產、跨世界資料隔離、超出範圍的分布隔離、唯一FLOD關聯及非法破壞索引。桌面互動與存檔監看驗證另由APP整合測試負責，不以這些資料測試代稱。

## 多階段任務地點（2026-11-01）

- `data/mission_locations.json`是目前build的精簡衍生資料：Prometheus主地圖中142個帶GameplayTags的`BP_QuestMarker_C`位置、`D_QuestQueries`的標籤關係、`D_Quests`的LocationQuery，以及314個PRO／DYN任務藍圖中18個含額外查詢引用的藍圖。沒有加入完整私人存檔或原始遊戲二進位檔。
- 所有24個Prometheus操作任務均有至少一組靜態查詢位置。SHADOWED含3個掃描點、隱藏基地與後續補給共5處；TEMPEST含搜尋區／探員／後續交付共7處；FRACTURE可列13個位置階段，其中1個查詢尚無對應標記，維持待定位。
- CRYSTAL列出領取、前往冰區、採集、建立營地及交付5個階段。目前固定冰區查詢可供3個相關階段預覽，投放／回收艙未出現時不假造其座標。RESCUE分別列出兩名隊員及兩次返回救援船的往返地點。
- 查詢標記是遊戲預設的候選／搜尋區域，並非已選定的動態艙、礦脈或生物位置；UI必須以候選形式區分。對目前任務，優先依明確RelevantActorIcarusUID／RelevantCharacterIcarusUID解析唯一物件，或使用明確LocationSet／Reached旗標的執行區域中心。未初始化的ActorTransform及原點不當成目標。
- 以同world_id及GameStateSeed的歷史DISPATCH快照唯讀交叉核對，已保存的調查位置與現版QM_MoOutpost完全一致（X=126307cm、Y=-45723.2695cm）。這是離線座標核對；執行時不讀取舊快照或所有備份。
- 執行時只做選定世界的記憶體關聯與快取JSON讀取，不在切換任務時重新解包遊戲或掃描備份。安裝build不符時停止使用舊靜態候選點，仍可保留當前存檔明確UID證實的地點。
- `tests/test_mission_locations.py`有12項測試，涵蓋多階段／往返、UID優先、重複UID衝突、其他任務／地圖隔離、明確完成旗標、未初始化／非有限座標、版本變更與動態任務父子區域關聯。


## 任務同區規劃（2026-11-03）

目前安裝build26313227的`D_ProspectList`有24項Prometheus表列操作（含4項NPC）及11種SMPL3委派類型；這只是內部目錄，不是目前可接清單。其中CARAVAN（PRO_D_Transport）沒有`D_Talents`任務板入口，並已檢查現版Prometheus關卡與NPC藍圖，不能因前置為空就列為可接。3個舊探索、1個Open World入口、11個Great Hunt prospect的OnProspectAvailability為None也不混入。正常介面只列全部已確認解鎖及進行中的Operation／NPC，排除完成、鎖定、待確認與SIMPLE／SMPL3，不以推薦分數限制筆數。

`operation_eligibility.py`以同版EXE及C0NT4CT／任務板藍圖驗證：ProspectTalentController使用Account儲存，RequiredTalents為任一正rank（OR），default reroute依前置計算closure，不是全部無條件開啟。`Profile.json`的UserID與來源帳號目錄、世界ClaimedAccountID三方核對，僅保留解鎖資料與獨立hash／時間；它不冒稱與世界同一時間。公開同步／共用圖不使用接收端Profile。世界完成歷史不能替代帳號解鎖，C0NT4CT模組需實際裝在裝置內。缺帳號資料則維持待確認並不列可接。規則精簡於`data/operation_rules.json`，以`tools/build_operation_rules.py`從已驗證三表可逐位元組重建；完整原始遊戲資產不提交。

現版Profile的UnlockedFlags可為非負數字ID；D_AccountFlags的GenerateEnum表列順序與ID映射尚未證實，不猜零起或一起索引。Prometheus26個任務／reroute節點均無required／forbidden旗標需求，因此這不影響本次解鎖判定；其他具名稱旗標門檻的任務若只有未映射數字，保守維持未知。真存檔格式查核見本機`feature-audit/account-flag-shape-contract.json`。

原生`UpdateLockedMissions`忽略Abandoned=2／Failed=3，其他歷史用同一世界`LevelTimeElapsedSec`核對157680000秒重接期限，不用現實經過天數猜測。`Mission_In_Progress`及DynamicQuestDelay由所有Operation／NPC共用的任務板UI阻擋開始，故已解鎖與`can_start_now`分開；當前有操作時顯示「已解鎖待接」，不可聲稱可同時接取。`RequiredFeatureLevel`由原生版本階段索引判斷，非Steam DLC擁有權；DangerousHorizons階段的跨圖NPC任務屬免費Kepler內容，不虛構付費授權缺口。原生／藍圖證據保留於本機`artifacts/mission-clusters-20261103/native-gates/`與`artifacts/operation-eligibility-20261103/feature-audit/`，來源hash綁定build26313227。

`mission_overview.py`在一張圖保留全部已解鎖／進行中任務的原位階段點，點圖標開對應攻略但不移除其他任務。共點使用選單，沒有位置不造原點；未生成目標保留候選或待定位，已完成階段不顯示。Profile及世界檔分別監看並在背景更新，切世界清除舊任務投影。

`mission_clusters.py`僅使用同一有效快照判定為可接／進行中的任務及`mission_locations.py`既有位置證據。已確認點與現版候選錨點分開做有界完整連結分群；群內任兩點的水平直線距離不超過所選500／1200／2000 m。群組至少含兩個不同任務，各任務可出現在不同區域；群外已定位階段、待定位階段及同階段替代候選分開，完成階段排除。同階段多個已確認目標仍保留群外需求，候選多錨點不視為必訪清單。群中心只供規劃及距離參考，不新增或偏移遊戲任務座標。

群組不證實地形可通行或一趟可完成所有任務；Operation不能並行，接取後以新快照重核。世界／地圖／基地隔離沿用既有world_id；共用離線地圖沒有可接或私人進度推論。SMPL3歷史次數僅保留在內部資料，不進正常任務清單／地圖／分組。Unreal的None/null任務名與完成manager不構成進行中操作。

## 玩家馴養動物（2026-11-02）

`animals.py`從與地圖相同的已驗證世界快照讀取Mount、Cow及Juvenile recorder。有效主人角色、明確非野生旗標及幼獸的已馴服狀態分開核對；名稱、Leader與距離不構成所有權證據。以動物GUID去重，衝突保留警告，平台帳號ID不進入輸出。鞍具／貨物依實際SavedInventories分區，件數使用現版ItemableStack；動物物品不併入家中部署容器統計。

飼養者分欄使用`owner_key`：有效`OwnerCharacterID.PlayerID`及`ChrSlot`經固定版本前綴與SHA-256產生穩定分組鍵，摘要不輸出平台ID或完整角色識別。欄名只顯示`owner_name`，不同角色即使同名仍分開；缺失或無效主人使用明確的`unknown`群組及「未知」名稱，不能用Leader或殘留名字代替主人。此鍵只供介面分組與配色，不展示於欄名或提示。

`data/animal_catalog.json`綁定build26313227及各原始來源SHA-256，包含D_AISetup→D_AICreatureType的名稱、狀態、血統、遺傳、天賦與原生列舉。性別經原生序列化鏈確認：0未知、1雌性、2雄性。等級依D_Mounts→D_CharacterGrowth→動物經驗曲線，以UCharacterState::GetLevel的整數門檻和UMG_CharacterInfo顯示上限計算；不對LastLevelAchieved猜測加一。AI_Mounts有51個門檻，AI_Pets有26個，零級合法。原生函式、藍圖節點及曲線來源雜湊保留在目錄；完整原生藍圖與私人世界資料不封裝或提交。

`experience_progress`重用同一組已驗證整數門檻，提供本級已獲得經驗、本級升級需求、下一級及其累積經驗門檻；達目前可顯示上限時明確標示滿級，不虛構下一級需求。零級及本級零進度合法，缺失、負值、不合法經驗或未驗證曲線保留未知，不畫成零進度。經驗條因此可顯示本級真實進度。

存檔來源的生命、食物、水、氧氣、耐力及乳量來自同次有效快照；遊戲唯讀來源成功時，僅生命／適用耐力成對改用有獨立讀取時間的遊戲值，見下文。存檔容量以`data/animal_capacities.json`及`data/animal_capacity_effects.json`提供可追溯的版本化計算輸入，不能把物種基礎值直接當作完整目前上限。兩份目錄均綁定build26313227、遊戲EXE雜湊、來源表與曲線雜湊；完整資產留在建置快取，不封裝遊戲EXE或私人角色資料。

| 狀態 | 計算來源與呈現範圍 |
| --- | --- |
| 食物、水分 | `D_AISetup.AIGrowth → D_AIGrowth.Base/CustomStats`的已驗證物種配置。現版NPC可用modifier、動物天賦、遺傳、血統、鞍具與主人轉換表沒有這兩種最大容量加成，可在來源與裝備檢查完整時顯示容量比例。 |
| 氧氣 | 相同物種配置。現版唯一直接增加氧氣容量的`Oxygen_Buff`明確排除NPC；其他已核對的動物加成來源沒有氧氣最大容量加成。牛的氧氣配置／應用尚未驗證，仍未知。 |
| 生命、耐力 | 物種成長曲線加上該動物的遺傳、血統與天賦，核對保存效果及裝備後產生個體重建值。再以精確帳號／角色slot核對飼養者角色天賦、帳號圖鑑、世界裝備及保存效果；完整持續來源可計算時以`*`標記，主人來源未完整則保留`≈`。未保存即時加成仍有缺口，不能稱為完整目前上限；牛的存檔耐力仍未知。 |
| 乳量、未支援來源 | 保留原始存檔值，無可靠容量時顯示問號；不猜單位、百分比或零值。 |

遺傳曲線以保存的`GeneticValue.Value`為輸入，血統成長曲線以原生經驗門檻得到的等級為輸入，不加一；每條曲線先依原生規則向零截斷為整數。天賦只使用`Rewards[rank-1]`，不累加較低rank。最終容量由Base值與百分比進行整數乘除及向零截斷；生命／耐力最低為1，食物／水／氧氣沒有這個最低1規則。已註冊的衍生`Maximum*_+`是計算後覆寫的快取，不再額外相加一次。來源缺列、未知rank、未驗證曲線範圍或格式衝突保留未知，不以缺列視為無加成。

原生存檔序列化已確認會依`D_ModifierStates.SaveToDatabase`過濾效果。`Animal_Feed`、`Animal_Feed_Bean`、部分糊糊、光環與其他即時效果可改生命／耐力，但不一定寫入存檔；因此`Modifiers=[]`不能證明沒有加成。`D_TamedCreatureModifiers`將主人正值百分比直接加入動物Base百分比，零與負值不轉移，不是乘二，也不對已截斷的個體容量再次相乘。

`owner_stats.py`與`data/owner_stat_catalog.json`以完整已知列索引核對角色／帳號天賦、107個圖鑑、物品→裝備、護甲與套裝、保存效果。只按精確帳號與角色slot配對，名稱不能代替。圖鑑第一階段門檻為`trunc(float32(TotalPointsRequired * 0.4))`，第二階段為總點數；只對已保存tracking entry給獎勵，不能把目錄中所有物種當已解鎖。Horse圖鑑第一階段的耐力5%是帳號獎勵，不限於馬；角色Husbandry耐力5%與其相加為10%。角色Husbandry生命rank3為30%。各獨立檔保留hash、UTC時間及`same_snapshot=False`，不因時間接近稱為一致快照。`persistent_complete`與`live_complete`分開，缺隊友檔、未知加成或衝突不當零；此小計亦不從目前生命數字反推。

原生容量規則的470例離線仿真證據保留於`artifacts/animal-capacities-20261102/native-capacity-contract.json`，以合成stat陣列執行有限函式位元組，沒有啟動或讀取遊戲程序。真實存檔唯讀核對由`tools/verify_animal_capacities.py`另行記錄快照、來源未變、已確認容量、個體重建及未知欄位；Qt元件、封裝與Windows桌面互動驗收亦分開，不能用470例仿真代稱全部整合通過。

`live_animal_stats.py`提供Windows專用、build26313227及EXE SHA-256閉鎖的遊戲狀態唯讀來源；僅使用程序查詢與VM_READ，不啟動／控制遊戲、不寫記憶體、不注入、不呼叫遊戲函式、不輸出記憶體dump。以原生反射／getter確認Actor.Recorder與ActorState、Recorder.GUID及Class，再核對完整ProspectID、ClaimedAccountID、ProspectDTKey、種子、地圖及快照world_id；任何缺失或不吻合均不套用。使用有限時間／位元組預算掃描物件索引，讀取前後重核身分及短重複樣本，拒絕變動、衝突或重複GUID。

生命對應`UActorState.GetHealth/GetMaxHealth`（getter RVA0x1466490／0x1468aa0，state+0x1d8／0x1dc）；耐力對應`UCharacterState.GetStamina/GetMaxStamina`（RVA0x146ff10／0x1469390，state+0x278／0x27c）。已獨立核對`UMG_MountSurvival`、`UMG_MountTooltip`、`UMG_Stamina`呼叫相同getter，所以讀取的是遊戲介面已套用加成的快取，不是離線估計。每個欄位同時替換目前值與上限，不拿舊存檔目前值配即時分母；保留原存檔值、持續來源及讀取時間。只有通過核對的欄位為`maximum_status=live`，其餘保留存檔範圍；牛耐力未有適用證據而不輸出。

`live_world_fields.py`補上SurvivalCharacterState原生current/max補給快取：Food+0x330/+0x348、Water+0x32c/+0x340、Oxygen+0x328/+0x338，逐對重複讀取與重核State世代，沒有寫死300。牛氧氣／耐力缺適用證據，仍不輸出。位置依K2_GetActorLocation相同來源Actor.RootComponent+0x130→ComponentToWorld.Translation+0x1d0，不讀RelativeLocation；另核UWorld.OriginLocation+0x5b8，目前僅原點(0,0,0)採用，非零不猜存檔座標的重基準語義。玩家以IcarusPlayerState+0x498精確帳號／slot配對所選快照，PawnPrivate+0x280與Pawn.PlayerState+0x240雙向核對，Pawn／RootComponent／PlayerState均重核世代與完整世界。對外只保留匿名player key及角色名稱，缺成員資料不以同名代替。

`live_actor_cache.py`只在記憶體保留物件定位及索引世代，綁程序PID／建立時間／EXE路徑、hash、module base、世界與目標指紋；不保存取樣數值或寫記憶體dump。使用快取時仍重核索引slot、flags／serial、header／class、Recorder／State及世界，再讀新值；失效、世界／程序／目標變更或300秒盤點到期會重搜。每輪另比對FUObjectItem索引世代，相關新增或重用槽位回完整搜尋，確保不略過GUID／玩家重複與多世界檢查；一般無關索引更新沿用定位，不延長完整盤點期限。預算耗盡整輪不套值，舊數值不能因命中快取而續用。

`live_positions.py`將同次遊戲值投影到動物及玩家展示，保留saved_position與UTC取樣時間，不修改原WorldSnapshot；玩家身分只使用匿名key。動物距離／在家篩選依採用的位置重算，主資源圖只替換已核對玩家標記。`AnimalPanel.loaded`按world_id與snapshot hash交由主視窗更新；切換世界、共用圖或舊背景工作不得回灌。卡片遊戲值實心、持續上限`*`、個體基準`≈`，位置實線為遊戲取樣、虛線為存檔，介面時間轉本地。30秒更新不受分頁可見性限制；失去來源回存檔並明示。未載入／未同步／離線玩家不保證有遊戲資料。經驗、背包、血統與任務／資源狀態仍依存檔更新。

動物圖示由`D_AISetup.BestiaryGroup → D_BestiaryData.Image`的明確引用解析到原生貼圖，精簡對照為`data/animal_icons.json`，縮圖位於`assets/animals/`，來源表與貼圖雜湊、版本及缺圖原因保留於對照中。目前241個種類定義有219個原生圖示、22個缺圖，以問號備援，不用近似物種代替。這些圖是遊戲圖鑑的種類／群組代表，不證明個體毛色、性別或外觀變異；幼獸共用群組圖也不表示個體外觀相同。原始解包貼圖留在本機建置快取，不進入APP。

`animal_map.py`使用所選世界同快照的動物`ActorTransform.Translation`與既有地圖校準，固定像素圖示直接畫在原位，允許重疊，不調整來源座標。卡片與地圖雙向選取、卡片定位、搜尋與範圍篩選使用相同動物清單；缺失或不合法座標保留為位置未知，不加入地圖點。世界切換、共用圖資與缺來源會清除舊卡片及私人位置；資料更新與選取依動物穩定識別維持一致。縮放平移及水平直線距離不提供可步行路線或即時位置證據。
