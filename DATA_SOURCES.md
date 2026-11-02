# 資料來源、覆蓋與驗證

本文件記錄截至2026-11-02開發時的本機資料查核。特定存檔的數量是當次唯讀快照結果，不是未來世界的固定值。APP會隨有效存檔重新計算。

## 公開共用地圖

`icarus_helper/data/share_profile.json`只包含 Prometheus 地圖名稱與使用者指定公開的「我們家」位置（X=-38989.4、Y=-189495.1、Z=0 cm）。此位置來自已保存的基地設定；Z=0 是該基地的地圖定位值，沒有額外地形高度驗證。沒有加入世界識別碼、Steam帳號、存檔路徑、世界種子、任務／庫存或完整存檔。

首次啟動顯示獨立 `SharedMapContext`，沒有存檔來源、存檔時間或存檔hash；讀取靜態目錄，不合併私人存檔。共用基地與各真實世界隔離，資源目前狀態未知，任務僅供攻略預覽；實際剩餘量、設備、玩家及庫存仍需主機存檔。第三方資料與圖示的來源不等於再授權，見 `THIRD_PARTY_NOTICES.md`。

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

卡片保留來源範圍：遊戲目前值用實心條，已保存持續上限標`*`，個體基準標`≈`，未知斜紋不代表百分比。動物頁可見時每30秒背景核對，失去遊戲來源即回到明示存檔來源；地圖位置、經驗與其他狀態仍隨有效自動存檔更新，不能將其稱即時位置。鞍具、背包、重型貨物各區以物品圖示及數量呈現，細節可展開。僅支援目前已驗證格式，新增元件需另行核對。

動物圖示由`D_AISetup.BestiaryGroup → D_BestiaryData.Image`的明確引用解析到原生貼圖，精簡對照為`data/animal_icons.json`，縮圖位於`assets/animals/`，來源表與貼圖雜湊、版本及缺圖原因保留於對照中。目前241個種類定義有219個原生圖示、22個缺圖，以問號備援，不用近似物種代替。這些圖是遊戲圖鑑的種類／群組代表，不證明個體毛色、性別或外觀變異；幼獸共用群組圖也不表示個體外觀相同。原始解包貼圖留在本機建置快取，不進入APP。

`animal_map.py`使用所選世界同快照的動物`ActorTransform.Translation`與既有地圖校準，固定像素圖示直接畫在原位，允許重疊，不調整來源座標。卡片與地圖雙向選取、卡片定位、搜尋與範圍篩選使用相同動物清單；缺失或不合法座標保留為位置未知，不加入地圖點。世界切換、共用圖資與缺來源會清除舊卡片及私人位置；資料更新與選取依動物穩定識別維持一致。縮放平移及水平直線距離不提供可步行路線或即時位置證據。
