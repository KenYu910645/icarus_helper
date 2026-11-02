"""Build a small derived mission catalogue from the installed, read-only data.pak.

Extracted raw tables stay in the user's local cache. This repository stores only
selected gameplay metadata and original Traditional Chinese preparation guides.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MAPS = {"Terrain_016": "Olympus", "Terrain_017": "Styx", "Terrain_019": "Prometheus", "Terrain_021": "Elysium"}
TABLES = ["Prospects/D_ProspectList.json", "Factions/D_FactionMissions.json", "Quests/D_Quests.json", "Quests/D_DynamicQuests.json", "Talents/D_Talents.json"]


def text(value):
    if not isinstance(value, str):
        return ""
    match = re.match(r'NSLOCTEXT\("[^"\\]*",\s*"[^"\\]*",\s*"(.*)"\)$', value, re.S)
    result = match.group(1) if match else value
    result = result.replace(r"\'", "'").replace(r'\"', '"').replace(r"\n", "\n")
    return re.sub(r"<[^>]*>", "", result).replace("[Map]", "地圖標記")


def prep(item, quantity="1", required=True, note=""):
    return {"item": item, "quantity": quantity, "required": required, "note": note}


COMMON = [
    prep("氧氣、水與耐久足夠的容器", "每人 1 組", False, "建議；出發前補滿，按路程增加備量"),
    prep("食物、繃帶與恢復藥品", "依人數與難度", False, "建議；APP 尚未核對實際庫存"),
    prep("武器、備用彈藥／箭與修理用品", "依裝備配置", False, "建議；防守與首領任務需增加儲備"),
]


def guide(title, summary, steps, equipment=(), risks=(), time="依路程與目前階段；尚無可靠總耗時", risk=2, mechanics=()):
    return {"title": title, "summary": summary, "guide": list(steps), "checklist": list(equipment) + COMMON,
            "risks": list(risks) or ["依目前難度與地形安排補給、床點與撤離方向；任務目標地點以遊戲實際標記為準。"],
            "time_estimate": time, "risk_rank": risk, "mechanics": list(mechanics)}


GUIDES = {
"PRO_Story_1": guide("NOMAD｜建立聯絡據點", "調查求救訊號，建立基地與聯絡設備，開始 Prometheus 故事。", [
    "依地圖與任務對話調查求救位置，建立小型據點，逐一放置 Bedroll、Oxidizer、Crafting Bench 與 Campfire。",
    "解鎖 T2 的 C0NT4CT 裝置，在 Crafting Bench 製作並放置。",
    "前往 Sinotai 補給艙，收集 SMPL3 晶片／升級物並與 C0NT4CT 互動；守衛生物為可選擊殺目標。",
    "完成 SMPL3 種植教學：領取番茄種子與種植床、種下番茄、等候成熟、採收、交付收穫並領獎。",
    "完成救援：前往 Daisy 最後出現位置，一路引導並保護牠返回飼主基地，留意 Daisy 血量；再依提示搜索飼主基地。"],
    [prep("C0NT4CT Device", "1", True, "T2 科技；Crafting Bench 製作"), prep("Bedroll、Oxidizer、Crafting Bench、Campfire", "依任務檢查表", True, "建立基地的指定設施"), prep("番茄種子、種植床與 SMPL3 晶片", "依補給艙", True, "在任務中領取")], risk=2),
"PRO_Story_2": guide("OFFENDER｜追查竊賊", "調查傳聞中的基地並回收嫌疑人相關證據。", [
    "先確認 T3 製作能力與補給，再從 C0NT4CT 接取任務。",
    "前往傳聞基地所在洞穴，先清除洞內威脅，再搜索遭竊科技與文件。",
    "把遭竊物資交付 Sinotai Retrieval Pod；任務文件不要丟棄或投入一般生產設備。",
    "前往新的補給艙領取 Infrasonic Relay Upgrade，將升級模組放進 C0NT4CT 裝置，確認升級目標完成。"],
    [prep("T3 製作能力", "1 套", True, "目前版本任務表 RequiredTech=Tier3"), prep("任務證據的背包空間", "預留數格", False, "依實際取得物品調整")]),
"PRO_Story_3": guide("SHADOWED｜追蹤隱藏基地", "利用線索三角定位嫌疑人的隱藏基地，繼續回收情報。", [
    "完成 OFFENDER 並升級 C0NT4CT，準備 T4 科技及較長途的補給。",
    "先從 Sinotai 補給艙領取專用設備，再於 Alpha、Beta、Delta 三點完成掃描，定位隱藏基地。",
    "前往基地搜索遭竊科技與文件，並將任務 Beacon 放在基地內。",
    "按新標記領取 Encrypted Satellite Upgrade，安裝進 C0NT4CT，確認後續任務解鎖。任務設備遺失時，遊戲 Escape 選單可請求 Mission Resupply。"],
    [prep("T4 製作能力", "1 套", True, "任務表 RequiredTech=Tier4"), prep("掃描設備與 Beacon", "依補給與任務提示", True, "先領取專用設備再前往掃描點"), prep("長途臨時床點與建材", "1 組", False, "建議")]),
"PRO_Story_4": guide("TEMPEST｜尋找失聯探員", "尋找失聯的 Group 15 探員並交付取得的資料。", [
    "確認 SHADOWED 已完成，且 C0NT4CT 已安裝 T4 升級模組。",
    "從最後廣播位置開始搜尋，檢查第一位探員屍體取得下一段訊號，再依序追蹤第二與第三位。",
    "清除守護最後一具屍體的生物，檢查遺體並取走 Audio Recording。",
    "依當下出現的交付分支回報：前往 Sol 舊補給點與設備互動上傳，或將錄音交給 Sinotai Retrieval Pod／Group 15；不要把不同分支都當成必走步驟。"],
    [prep("任務資料存放空間", "數格", False, "建議預留")]),
"PRO_Story_5": guide("DISPATCH｜前哨資料傳輸與撤離", "進入布有地雷的前哨，供電、防守傳輸、放置信標並及時撤離。", [
    "先攜帶可提供合計至少 10000 W 的發電設備、對應燃料及電線工具；在開啟傳輸前完成供電與防守補給。",
    "調查前哨位置，進入時注意爆炸地雷；到達資料儲存通訊器。",
    "將設備接上 10000 W 電力，先檢查維修、補給、逃生方向，再互動開始傳輸。",
    "傳輸期間留在設備附近並防守；保護通訊器，持續檢查其耐久。",
    "傳輸完成後在 Machining Bench 製作任務要求的信標，帶回前哨內放置並回報 Group 15。",
    "出現基地即將摧毀警告時優先撤離；搜刮為可選目標，不要為額外物資錯過撤離。"],
    [prep("合計 10000 W 的可靠電力與配線", "≥10000 W", True, "電力需求由目前 D_Quests 確認；設備組合自行選擇"),
     prep("發電燃料／備援電力", "覆蓋傳輸及準備時間", False, "建議額外留備量；不假定全天日照"),
     prep("任務信標 Beacon", "1", True, "依任務階段在 Machining Bench 製作"),
     prep("防守建材、修理錘、備用武器彈藥", "依隊伍與難度", False, "建議；先安排補給點與撤離通道")],
    ["前哨有爆炸地雷；傳輸會引來敵潮，且通訊器有耐久條件。", "附近有玩家才能推進傳輸；任務時間不是整段旅行的耗時。", "不可把同時敵人上限當成整場擊殺總數。"],
    time="傳輸進度 600 秒；旅行、供電、戰鬥與撤離另計", risk=4,
    mechanics=["已解碼的本機 Story5 藍圖：傳輸目標時間 600 秒，玩家接近範圍約 38 m。",
               "Medium／Normal 藍圖設定：Ubis 14 秒／上限 5、Drac 15 秒／5、Viscid 27 秒／2、Dreadwing 35 秒／5；屬配置而非實測出怪保證。",
               "傳輸進度 0–5%、35–40%、65–70% 時，Ubis／Drac／Viscid 間隔減半；Dreadwing 不套用此加速。"]),
"PRO_Story_6": guide("FRACTURE｜追查叛徒", "領取 Group 15 補給並追蹤叛徒。", [
    "完成 DISPATCH 後接取，依標記取回 Group 15 補給，先檢查領取物品及任務提示。",
    "前往最後已知位置，檢查死亡探員並沿足跡進入洞穴、穿過洞穴，再前往附近營地。",
    "檢查營地、清除生物並搜索情報；依遊戲當下分支處理嫌犯／等待指令。出現人臉辨識交付目標時，將指定任務物件送入 OES；不要把所有分支視為同時必做。",
    "依駭取資料找到基地，搜索更多錄音；啟動資料儲存設備駭入並防守，進度完成後離開區域。",
    "依 Mo 的提示尋找證據，迎戰 Giant Lava Creature 後找出洞穴入口，深入洞穴探索聚落並與 Sol 討論證據。"],
    [prep("Group 15 任務補給", "依補給艙", True, "接取後先領取，非出發前自行購買"), prep("洞穴照明、防守彈藥、耐熱與首領戰補給", "依隊伍與難度", False, "建議；後段包含駭入防守與大型熔岩生物")], risk=4),
"PRO_A_Extraction": guide("CRYSTAL｜超冷冰開採", "領取鑽探設備，在冰脈開採 Super Cooled Ice，建立小型營地。", [
    "準備 T3 科技與極寒地區補給，前往 Sinotai 投放艙領取鑽探設備。",
    "依任務標記找到冰脈，放置 Ice Borer 並確認設備供應需求及可運作狀態。",
    "營地須按檢查表放置 Cement Mixer、Concrete Furnace 與 Forge；採集任務要求的 Super Cooled Ice。",
    "可選準備包括製作 Cold Steel Pickaxe 採 Frozen Ore、鏟子採冰與 Cocoa 保暖。",
    "依 Sinotai Retrieval Pod 清單交付 Ice、Super Cooled Ice、Frozen Ore、Cold Steel Ingots，逐項核對數量；不要把 Ice Borer 放到一般礦床。"],
    [prep("Ice Borer 與任務設備", "依投放艙", True, "任務提供，依提示領取"), prep("Cement Mixer、Concrete Furnace、Forge", "依營地檢查表", True, "可預製或攜帶材料"), prep("保暖衣物、熱源及營地建材", "每隊 1 組", False, "建議；冰區需要保暖"), prep("Cold Steel Pickaxe、鏟子、Cocoa", "依採集計畫", False, "來源目標列為可選製作；交付項目仍須核對")], risk=3),
"PRO_B_Extermination": guide("MAGMATIC｜火山區狩獵與釣魚", "在指定火山區清除生物並捕捉魚類。", [
    "準備 T2 科技、狩獵武器與釣魚裝備，先領取 Sinotai 補給艙提供的任務裝備。",
    "在指定火山區核對 Lava Broodling、Drifter、Ashen Drake、Stonejaw 的獵殺目標；範圍外擊殺不保證計入。",
    "到指定水域捕捉 Sunset Ripjaw、Void Triggerfish、Coral Darter、Neon Dory，依遊戲清單核對各項數量後回報。"],
    [prep("釣竿與對應釣具", "1 組", True, "任務含指定區域釣魚目標"), prep("耐熱、防護與醫療用品", "依人數", False, "建議；避免直接跨越危險地形")], risk=3),
"PRO_C_Construction": guide("TREEHUT｜樹頂住宅", "在指定樹頂建造符合條件的住宅。", [
    "先確認所選位置在任務指定區域，攜帶 T2 建築與高處施工所需工具。",
    "在指定樹頂搭建支撐與可返回地面的通路，再逐一完成任務檢查的房屋條件。",
    "放置 Beds、Anvil Bench、Textiles Bench、Skinning Bench、Oxidizer、Potbelly Stove 與 Stone Furnace，逐項確認房屋檢查進度。所有條件確認前不要拆除支撐。"],
    [prep("床、鐵砧台、紡織台、剝皮台、氧化器、腹爐、石爐", "各項數量依遊戲檢查表", True, "包含所有已辨識的設施種類；不猜測表中文字佔位符數量"), prep("梯子、平台、修理錘與建材", "1 組", False, "建議，防止墜落與結構損壞")], risk=1),
"PRO_C_Research": guide("MIASMIC｜沼澤瘴氣研究", "建立研究站，提煉瘴氣塗層並製作瘴氣工具。", [
    "準備 T4 科技、沼澤防護與工作站運輸材料；確認 Chemistry Bench 可使用。",
    "在指定地點建立研究站，按清單放置 Organic Residue Cleanser、Solar Panel、Foundry、Electric Furnace、Advanced Alteration Bench 與 Chemistry Bench。",
    "在沼澤獵殺並剝皮生物，採集 Crystallised Miasma。",
    "在 Chemistry Bench 將結晶提煉為 Distilled Miasmic Coating，先確認配方與需求數量再批次處理。",
    "使用塗層製作 Miasmic Sickle、Knife、Axe、Pickaxe；遊戲部分目標 ID 與顯示名稱對調時，以顯示物品與實際完成判定為準。",
    "用相應瘴氣工具測試採集，將 Acidic Glands、Thorned Plants、Infected Bark、Noxious Crust 交到 Sinotai Retrieval Pod，逐項核對數量。"],
    [prep("研究站指定六種設施與 T4 製作能力", "各項依檢查表", True, "清潔器、太陽能板、鑄造爐、電爐、進階改裝台、化學台"), prep("瘴氣工具原料與塗層", "依四種工具配方", True, "刀、鐮刀、斧、鎬均需核對")], risk=3),
"PRO_A_Stockpile": guide("CELEBRITY CHEF｜冷藏食材交付", "將列出的食材投入冷藏貨艙，完成指定的補給清單。", [
    "先查看 Alpine／Marshlands 冷藏貨艙清單；已辨識項目包含水果沙拉、莓果醬、啤酒、麵包麵團、黃豆炒菜、魚派、淡水魚咖哩、裹粉鹹水魚片、葡萄酒及鹹水魚生魚片，按當下清單分配。",
    "準備 T3 加工／保存能力，優先處理易腐食材；分批運輸時保留足夠氧氣與回程補給。",
    "把指定成品投入任務冷藏貨艙，逐項核對進度；不要把原料當作成品交付。"],
    [prep("任務指定食材／成品", "以接取後貨艙清單為準", True, "實際需求量尚未從靜態表還原"), prep("冷藏保存與運輸空間", "依批次", False, "建議，降低腐敗與超重風險")], risk=1),
"PRO_Ashlands_Story": guide("ASHLANDS｜灰燼地信標鏈", "用連續信標標記 Null Sector 入口與區域探索路線。", [
    "先完成 DISPATCH，準備 T3 製作能力、Trail Beacon Tool 與長途補給。",
    "找到 Null Sector 入口並摧毀阻擋的路障；工具與 Trail Beacons 都在 T3 科技樹，需同時準備。該區資源稀少，先帶足補給。",
    "在入口放置第一個 Trail Beacon，確認遊戲接受位置。",
    "按任務要求延伸信標鏈，逐段檢查範圍與地圖進度；不要只憑直線距離越過危險地形。",
    "依訊號強度找到失落前哨，清除周圍 Stonejaw，再搜索科學隊證據。",
    "在 Icesheet 入口附近放置最後一個信標，確認解鎖與回程標記。"],
    [prep("Trail Beacon Tool 與 Trail Beacons", "工具 1；信標依路線", True, "兩者均需 T3 科技"), prep("破除路障工具、耐熱裝備與長途補給", "依路程", False, "建議；Null Sector 資源稀少")], risk=3),
"PRO_Icesheet_Story": guide("ICE SHEET｜冰原信標鏈", "從冰原入口延伸信標，找到科學家前哨。", [
    "完成 ASHLANDS，攜帶 Trail Beacon Tool、保暖裝備與冰區補給。",
    "從 Ashlands 找到 Icesheet 入口，用鎬打通阻擋入口的塌方，放置第一個信標。",
    "持續延伸至科學家前哨，隨時確認保暖、氧氣與可回程的連接路線。",
    "依訊號強度找到前哨，搜索科學隊證據，再調查附近洞穴尋找失去理智的科學家去向。"],
    [prep("Trail Beacon Tool、Trail Beacons 與鎬", "各 1 組；信標依路線", True, "鎬用於打通入口塌方"), prep("保暖衣物、熱源與臨時床點", "每隊 1 組", False, "建議")], risk=3),
"PRO_D_Transport": guide("CARAVAN｜灰燼地物資運輸", "先儲備大量物資，再把容器與物資送至灰燼地指定位置。", [
    "接取後先讀取起點儲備清單：Wood、Fiber、Oxite 與多種種子；按種類、重量安排容器與隊伍分工。",
    "在任務要求的儲存設備中備足物資，確認第一階段被接受。",
    "把設備與物資運到灰燼地指定地點，放置儲存容器並投入清單物品。",
    "途中遇到任務伏擊時先處理威脅，再核對目的地物資條件；不要在遊戲確認完成前拆掉設備。"],
    [prep("容器、木材、纖維、氧礦與多種類種子", "依兩階段清單", True, "數量以接取後任務／容器要求為準"), prep("運輸坐騎／載具與修理戰鬥補給", "依重量", False, "建議；需防範伏擊")], risk=3),
"PRO_D_Research": guide("COMPOSITION｜灰燼地環境測試", "完成 Alpha、Beta 位置測試，並消滅變異野生生物。", [
    "完成 ASHLANDS，先在 Chemistry Bench 製作 Liquid Solution，在 Fabricator 製作 Aerosol Emitter；備妥供電與戰鬥補給。",
    "到 Alpha 部署並供電，維持裝置運轉直到排放週期完成，再回到裝置處理變異野生生物。",
    "到 Beta 依任務提示完成液體測試、部署／供電及指定生物戰鬥。",
    "任務進入 Chi 測試時，再部署供電並維持排放週期；保持在 Emitter 附近收集資料。",
    "逐一核對 Alpha、Beta、Chi 當下出現的目標與獵殺進度；實際週期秒數以遊戲顯示為準。"],
    [prep("Liquid Solution、Aerosol Emitter 與供電", "依階段清單", True, "分別於 Chemistry Bench、Fabricator 製作"), prep("高耐久戰鬥裝備與藥品", "依隊伍", False, "建議，包含首領類風險")], risk=4),
"PRO_E_Research": guide("DROVER｜漂浮生物監測", "製作並架設 Monitoring Pylon，完成監測。", [
    "完成 ICE SHEET，先在 Machining Bench 製作 Monitoring Pylon。",
    "帶到孤立 Drifter 下方指定石陣頂部，確認位置與可安全上下的通路。",
    "部署監測柱，按每一階段顯示的物品與數量提供分析樣本；靜態文字 {0}/{1} 沒有列出實際材料，不預填猜測值。",
    "檢查所有監測階段及任務完成訊息。"],
    [prep("Monitoring Pylon", "1", True, "在 Machining Bench 製作"), prep("監測柱分析樣本", "依各監測階段", True, "需接取後核對實際材料與數量"), prep("登高建材、修理工具與補給", "1 組", False, "建議")], risk=2),
"PRO_D_Recovery": guide("CRISIS｜醫療救援", "找到灰燼地施工現場，以 HEAL 裝置穩定救援隊員。", [
    "完成 ASHLANDS，製作 HEAL Device，帶足醫療與補給物資。",
    "追查灰燼地 Terraforming Construction Site，排除周圍威脅並與 Captain Church 交談；留意遊戲顯示的救援時限。",
    "使用 HEAL Device 掃描每位隊員，按各人的診斷結果提供藥品與物資。",
    "逐一穩定 Church、Tucker、Griff、Simmons、Washington 五位隊員，再與 Captain Church 交談完成回報。"],
    [prep("HEAL Device", "1", True, "T3 解鎖、Machining Bench 製作"), prep("食物、水、氧氣與強於藥膏的藥品", "依五位隊員診斷", True, "來源明示需要比 pastes 更強的藥品；未掃描前不猜種類與數量")],
    risks=["具有救援時限；應先備好 HEAL Device、進階藥品與五人的補給，再接取／出發。", "只有藥膏可能不足以治療隊員，依掃描診斷提供相應藥品。"], risk=3),
"PRO_D_Resupply": guide("STRANDED｜救援卡車補給與維修", "補滿卡車資源、修理引擎並補充裝備。", [
    "完成 CRISIS 後接取；到卡車逐部件檢查，確認交付位置與實際材料清單。",
    "補充水與生物燃料，可使用 Fabricator 製作的 Portable Water／Biofuel Tanks 搬運。",
    "在 Fabricator 製作 Radiator 與 Alternator，交到卡車引擎；在 Crafting Bench 製作 Rope 並交入卡車儲存區，數量按清單。",
    "分別核對資源槽、引擎與儲存目標，完成前不要移走已交付物資。"],
    [prep("水、生物燃料、Radiator、Alternator、Rope", "依卡車清單", True, "散熱器／發電機在 Fabricator，繩索在 Crafting Bench"), prep("Portable Water／Biofuel Tanks", "依運輸量", False, "建議；Fabricator 製作")], risk=2),
"PRO_D_Rescue": guide("RESCUE｜營救 Simmons 與 Washington", "追蹤兩位指定隊員並完成救援。", [
    "完成 STRANDED，在 Advanced Textiles Bench 製作兩個 Stasis Bags；預留 Utility Slot，先規劃前往 Medevac Ship 的回程。",
    "依標記找到 Simmons，處理現場威脅後使用 Stasis Bag 裝載，運送到地圖標示的 Medevac Ship。",
    "找到 Washington，同樣使用另一個 Stasis Bag 並送到救援船；兩名隊員的交付進度分開核對。",
    "攜帶裝有隊員的袋子會占用 Utility Slot，按隊伍運力安排同行／分趟；確認兩人都已交付再回報。"],
    [prep("Stasis Bags", "2", True, "來源明示兩個，Advanced Textiles Bench 製作"), prep("Utility Slot", "每個滿袋占 1 格", True, "先移開衝突裝備並安排運送人員"), prep("醫療、食物、水與照明", "依路程與人數", False, "建議，先查救援船位置")], risk=3),
"PRO_Nullsector_Story": guide("NULL SECTOR｜探索冰原附近洞穴", "調查冰原前哨附近指定洞穴，推進區域故事。", [
    "完成 ICE SHEET，先查閱前哨附近的任務洞穴標記與實際入口。",
    "洞口有塌方時，搜索前哨與洞穴下方區域取得任務炸藥，將炸藥放在塌方處，再遠離以觸發爆破。",
    "帶照明與醫療進入洞穴、繼續深入，依聲音指引前往洞窟並聆聽對話。",
    "檢查 Exotic Formation，確認後續故事與任務完成狀態；保留可返回的路徑。"],
    [prep("任務炸藥", "依現場補給", True, "從前哨與洞穴下方區域搜尋，非假定一般炸藥可替代"), prep("照明與備用燃料", "1 組", False, "建議；洞穴入口不等於任務物件座標")], risk=3),
"PRO_SQ_Farmer": guide("CULTIVATION SPECIALIST｜農作交易專員", "協助專員設立交易站，準備首批蔬果並出售作物。", [
    "先在任務裝置確認 Dangerous Horizons 任務可接，再前往專員位置。",
    "按專員清單交付玻璃、硫、鋁錠與蜂蠟；數量以遊戲當下清單為準。",
    "依提示離開區域，讓專員建立交易站；不要在建造未完成前期待交易功能。",
    "準備首批水果與蔬菜，待交易站成立後出售作物取得 Ren。"],
    [prep("玻璃、硫、鋁錠、蜂蠟", "以專員清單為準", True, "任務文字已確認四種材料；未還原固定數量"), prep("水果與蔬菜", "依首批交付目標", True, "建議先安排種植與保存")], risk=1),
"PRO_SQ_Hunter": guide("PREDATOR SPECIALIST｜狩獵交易專員", "補給狩獵專員、清理周圍生物，再出售 Vestiges。", [
    "確認任務權限與可接狀態，攜帶補給前往專員。",
    "交付火藥、彈殼、照明彈與煙霧彈；數量依專員當下清單。",
    "在指定狩獵區內清除生物，讓專員補足與安全狀態達標。",
    "使用 Taxidermy Knife 剝皮取得 Vestiges，待專員準備好後出售。"],
    [prep("火藥、彈殼、照明彈、煙霧彈", "依專員清單", True, "先查需求再批量生產"), prep("Taxidermy Knife", "1", True, "T2 Anvil 解鎖與製作；採集 Vestiges")], risk=3),
"PRO_SQ_Norex": guide("BLACK MARKET｜Norex 接頭", "擊敗世界首領取得生物質，在指定位置發射信號並交易。", [
    "確認 Dangerous Horizons 使用權與任務可接；準備 Single Barrel Launcher 及對應照明彈。",
    "到標記位置迎戰 Blighted Landshark，先準備床點、戰鬥補給與撤退空間。",
    "從首領屍體取得 Biomass，前往 Theta 高地的指定位置。",
    "朝天空發射照明彈，接近 Norex 聯絡人並以 Biomass 換取生物部件。"],
    [prep("Single Barrel Launcher 與相容 Flares", "各 1 組", True, "T2 解鎖；Crafting Bench 製作"), prep("Biomass", "依交易需求", True, "在任務中從指定首領屍體取得")], risk=4),
"PRO_SQ_Fisher": guide("FISHERIES OFFICER｜漁業交易專員", "救助漁業專員、奪回修復前哨並完成首批漁獲交易。", [
    "先備妥 HEAL Device、修理錘或釘槍、釣竿；另依任務清單準備要部署的漁業設備。",
    "前往 Fishing Outpost，用 HEAL Device 穩定專員，並擊敗 Tide-Gorged Bear 奪回前哨。",
    "使用錘或釘槍修復小屋，依遊戲清單放置所需工作台、陷阱與其他設備。",
    "依提示離開讓專員完成重建；用釣竿或陷阱取得首批魚，再出售取得 Ren。"],
    [prep("HEAL Device", "1", True, "T3 解鎖，Machining Bench 製作"), prep("修理錘或釘槍", "1", True, "用於修復小屋"), prep("釣竿與漁業設備", "依清單", True, "不猜測動態物品欄位 {item} 的種類與數量")], risk=4),
}

DYNAMIC_GUIDES = {
"Boss": ("SMPL3｜狂暴生物", "獵殺受地球化影響而發狂的指定生物。", ["接取後先讀取目標生物、區域與難度，不以普通野獸強度估算。", "準備武器、充足彈藥與恢復物品，先設安全床點再進入標記區。", "擊殺指定目標並依更新後提示回報／領獎；普通同類生物不保證計入。"], [prep("適合目標生物的武器與彈藥", "依難度", False, "建議")], 4),
"Mine": ("SMPL3｜採礦交付", "採集指定礦石並交付 UDA。", ["接取後先確認礦種、數量與交付點。", "選擇仍有可採狀態的相應礦點，攜帶能採該礦的鎬與備用修理材料。", "將指定礦石投入任務交付設備，核對數量後完成回報。"], [prep("對應等級的鎬", "1", True), prep("指定礦石", "依接取清單", True)], 1),
"Grow": ("SMPL3｜農作交付", "種植並交付指定作物。", ["先確認作物種類、成品與數量，避免種錯。", "安排種子、種植床與灌溉，等候成熟期間做好保存與防護。", "收成後將要求物品交付 UDA，核對是否要求烹調而非原始作物。"], [prep("指定種子、種植床與作物", "依接取清單", True)], 1),
"Cache": ("SMPL3｜隱藏補給箱", "搜尋其他探勘者遺留的隱藏物資。", ["前往任務搜尋區，先確認區域邊界與可回程入口。", "搜索補給箱並與任務物件互動，預留背包空間。", "依更新目標回報或領取獎勵，勿把普通容器當作任務目標。"], [prep("照明與背包空間", "數格", False, "建議")], 1),
"Collect": ("SMPL3｜回收身分證件", "尋找死亡探勘隊員並交付其身分證件。", ["依任務標記搜尋隊員遺體，注意周圍掠食者。", "從每個指定遺體取得身分證件，任務物品分開保管。", "將要求證件交付 UDA，確認所有隊員目標已完成。"], [prep("任務身分證件", "依接取目標", True, "搜索遺體時取得")], 2),
"Hunt": ("SMPL3｜區域狩獵", "獵殺附近區域指定生物，降低威脅。", ["接取後確認區域、物種與數量／進度。", "在指定範圍內擊殺，隨時核對進度；移動出界可能不計入。", "完成後回報，清理傷勢並補足彈藥再接下一個任務。"], [prep("武器與足夠箭／彈藥", "依任務難度", False, "建議")], 3),
"Scan": ("SMPL3｜雷達掃描", "領取雷達並完成 UDA 掃描。", ["先從任務投放設備領取 Radar，查看指定掃描位置。", "在目標位置部署，先安排周圍防護再啟動掃描。", "依任務要求維持或回收雷達，完成後確認回報與獎勵。"], [prep("任務 Radar", "依投放艙", True, "接取後領取"), prep("防守與維修用品", "1 組", False, "建議")], 3),
"Tools": ("SMPL3｜製作工具", "製作工具供 UDA 交給需要協助的新探勘者。", ["先查看工具種類、等級與數量。", "在相應工作台製作，預先確認材料及解鎖科技，勿以較低等級代替。", "投入指定交付設備並核對數量。"], [prep("指定工具與製作材料", "依交付清單", True)], 1),
"Build": ("SMPL3｜建造瞭望塔", "建造瞭望塔並放置信標標記位置。", ["先確認建造區域與遊戲結構檢查條件，再帶建材。", "建立有可靠支撐與可上下通路的瞭望塔。", "在要求位置放置信標，核對各項建造條件後回報。"], [prep("塔體建材與信標", "依建造清單", True), prep("修理錘、梯子與照明", "1 組", False, "建議")], 2),
"Drone": ("SMPL3｜墜毀無人機", "在時限內找到無人機，保護資料上傳。", ["先完成補給與戰備再接取，因任務標示具有時限。", "迅速前往墜毀點，先觀察無人機及周邊威脅。", "依提示啟動上傳並防守，檢查耐久與進度直到完成；時限以遊戲實際顯示為準。"], [prep("防守武器、彈藥、維修與醫療用品", "依難度", False, "建議在接取前備齊")], 4),
"Lost": ("SMPL3｜廢棄基地搜索", "定位廢棄基地並為 UDA 搜尋。", ["前往任務標記的廢棄基地，先檢查環境與掠食者。", "逐區搜索並與指定物件互動，預留任務物品空間。", "依目標完成交付／回報，離開前確認清單沒有未完成項目。"], [prep("照明、修理與背包空間", "1 組", False, "建議")], 2),
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
            "acquisition": "在該世界的 C0NT4CT 裝置開啟 Operations；依前置、升級與使用權確認可接。",
            "source": "目前安裝版本 data.pak：D_ProspectList、D_FactionMissions、D_Quests、D_Talents。攻略中的建議補給由 APP 編寫。",
            "order": len(missions), "guide_note": "原始目標保留遊戲英文以便對照；準備建議不等於遊戲硬性要求。",
        }
        base_steps = ["先確認前置任務、C0NT4CT 升級與所需科技，於裝置接取後再出發。"]
        base_steps += ["依遊戲提示完成：" + o["text"] + ("；" + o["info"] if o["info"] else "") for o in obj]
        base_steps += ["依各階段新出現的數量與位置提示核對交付，最後確認任務完成與獎勵。"]
        normal.update(guide(normal["title"], normal["summary"], base_steps,
                           [prep("任務指定設備與物資", "依接取後清單", True, "尚無完整獨立繁中攻略，先以本機遊戲目標核對")]))
        normal["guide_coverage"] = "來源目標與通用準備；個別細節待補"
        if mid in GUIDES:
            normal.update(GUIDES[mid])
            normal["guide_coverage"] = "已撰寫任務專屬繁中攻略；動態數量與分支條件以接取後清單確認"
        rewards = []
        for reward in faction.get("CurrencyRewarded", []):
            key = reward.get("Meta", {}).get("RowName", "未知貨幣")
            label = {"Credits": "Ren", "Exotic_Red": "紅色異質礦", "Exotic1": "異質礦"}.get(key, key)
            rewards.append(f"{label}：{reward.get('Amount', '未知')}（任務表基礎值，實際難度與倍率以遊戲為準）")
        unlocks = [r.get("RowName") for r in faction.get("AccountFlagsRewarded", []) + faction.get("CharacterFlagsRewarded", [])]
        rewards += ["解鎖標記：" + u for u in unlocks if u]
        normal["rewards"] = rewards or ["此表未列出固定貨幣獎勵；以遊戲結算及後續解鎖為準。"]
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
            "acquisition": "在 SMPL3 隨機委派介面查看當下抽選；目錄包含類型，不代表當下所有類型均已出現。",
            "rewards": ["依抽選難度與任務獎勵池決定；接取前在裝置確認，不預填固定數字。"],
            "source": "目前安裝版本 data.pak：D_DynamicQuests、D_FactionMissions、D_Quests。",
            "guide_coverage": "已撰寫委派類型攻略；變體數量於接取後確認", "guide_note": "SMPL3 的物種、數量、難度與獎勵會依抽選變體改變。"})
    manifest = {"schema_version": 1, "source_build": build_id, "source_version": f"ICARUS Steam build {build_id}", "source_date": datetime.now().astimezone().isoformat(),
        "source_files": {name: {"sha256": hashlib.sha256((source / name).read_bytes()).hexdigest(), "bytes": (source / name).stat().st_size} for name in TABLES},
        "missions": missions, "quest_texts": {qid: text(q.get("Description", "")) for qid, q in quests.items()},
        "enum_evidence": {"source": "Installed Icarus-Win64-Shipping.exe reflected enumerator pointer/value table", "EMissionState": {"InProgress": 0, "Completed": 1, "Abandoned": 2, "Failed": 3}},
        "coverage": {"operations": len(missions) - len(DYNAMIC_GUIDES), "smpl3_types": len(DYNAMIC_GUIDES), "prometheus_custom_guides": len(GUIDES), "other_map_guides": "依來源目標提供基礎指引；尚未全部補齊任務專屬繁中攻略"}}
    if pak:
        manifest["data_pak"] = {"bytes": pak.stat().st_size, "sha256": hashlib.sha256(pak.read_bytes()).hexdigest()}
        exe = pak.parents[2] / "Binaries/Win64/Icarus-Win64-Shipping.exe"
        if exe.is_file():
            manifest["enum_evidence"]["exe_sha256"] = hashlib.sha256(exe.read_bytes()).hexdigest()
    verified = source / "blueprint_verification.json"
    if verified.is_file():
        manifest["blueprint_verification"] = json.loads(verified.read_text(encoding="utf-8"))
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
