# COC7e 守密人系統規格書：角色、字典檔、跨語言對齊與自動補齊架構

本文件定義了《克蘇魯的呼喚》第七版（Call of Cthulhu 7th Edition）守密人機器人在處理**「GM 單人集中上傳資源、角色卡智慧比對、有缺自動補齊、中英翻譯對齊、裝備彈藥自動識別、以及自學習字典檔」**的完整架構與流程規範。

---

## 目錄
1. [核心痛點與設計哲學](#1-核心痛點與設計哲學)
2. [系統總覽架構圖](#2-系統總覽架構圖)
3. [模組一：智慧檔案分流與增量載入](#3-模組一智慧檔案分流與增量載入)
4. [模組二：「有缺的補上」—— 自動補齊引擎](#4-模組二有缺的補上自動補齊引擎)
5. [模組三：跨語言對齊與「9 大屬性數值指紋」](#5-模組三跨語言對齊與9-大屬性數值指紋)
6. [模組四：重複角色衝突比對與「擇優融合」](#6-模組四重複角色衝突比對與擇優融合)
7. [模組五：自學習動態字典系統（Self-Learning Dictionary）](#7-模組五自學習動態字典系統self-learning-dictionary)
8. [模組六：玩家自動綁定與一鍵認領](#8-模組六玩家自動綁定與一鍵認領)
9. [模組七：整備階段與 /coc start 全員角色裝備就緒閘門](#9-模組七整備階段與-coc-start-全員角色裝備就緒閘門)
10. [附錄：GM 載入完成報告與開團就緒報告範例](#10-附錄gm-載入完成報告與開團就緒報告範例)
11. [附錄：核心模組範例代碼](#11-附錄核心模組範例代碼)

---

## 1. 核心痛點與設計哲學

### 既有架構的實戰痛點
1. **角色卡全空**：上傳角色卡只進了後台預製角色池（`state.pregens`），沒有綁定給玩家，群組狀態永遠顯示「尚無角色」。
2. **同職業覆蓋踩踏**：原本以職業（`occupation`）作為去重主鍵，GM 同時上傳兩張「調查員」或「警察」時，前一張直接被洗掉。
3. **中英翻譯斷鏈**：英文劇本 PDF 抽出的 `Malcolm Carter` 與玩家手修卡的 `卡特`，或 `Spot Hidden` 與 `偵查` 無法自動識別為同一角色或技能。
4. **裝備與手槍漏失**：解析器標題鎖死 `【武器】`、格式鎖死「不可有冒號與空行」，且未把一般道具寫入 `carried_items`，導致槍械未追蹤、手電筒等物品丟失。
5. **靜態代碼無法學習**：字典寫死在 `.py` 代碼中，無法因應新譯名、新別名自動演進。

### 設計核心哲學
* **GM 集中打包上傳**：支援單人一次性上傳劇本、地圖與全團角色卡。
* **上傳後比對 (Match & Reconcile)**：多層次比對既有出戰角色與 PDF 預製角色，不產生幽靈雙胞胎。
* **有缺的自動補上 (Auto-Completion)**：衍生屬性、基礎技能、槍械預設彈藥自動計算補齊。
* **動態遊戲進度保護 (Hot Patch)**：中途修卡只更新屬性上限與技能，嚴格保留當前負傷 HP、扣除 SAN、消耗 Luck 與戰利品。
* **自學習字典檔 (Self-Learning)**：發現高置信度的新中英對應時，自動寫入持久化字典，越跑越聰明。

---

## 2. 系統總覽架構圖

```text
====================================================================================================
           【COC7e 守密人機器人：全自動檔案解析、智能比對與補齊總覽架構】
====================================================================================================

      [ GM / 團長 單人集中上傳檔案 ]
      (包含：劇本 PDF、地圖 YAML、全體玩家角色卡 TXT/MD/PDF)
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 模組一：智慧檔案分流器 (Unified File Router)                                    │
│   ・LINE / Discord 統一副檔名與內容辨識                                         │
│   ・[.pdf]  ➔ 劇本分析 (⚠️ 不洗掉既有角色)                                     │
│   ・[.yaml] ➔ 地圖增量合併 (state.scene_maps.update，不重設位置)               │
│   ・[.txt/.md/角色卡] ➔ 進入「角色卡與裝備智能處理核心」                        │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 模組二：「有缺的補上」自動補齊引擎 (Auto-Completion Engine)                     │
│   ① 衍生數值補齊：HP, MP, SAN, MOV, DB, Build 依公式自動推算                   │
│   ② 基礎技能補齊：官方 BASE_SKILLS (閃避, 母語, 聆聽...) 自動補上官方預設值     │
│   ③ 裝備與武器泛化辨識：槍械自動補齊標準彈藥庫 (左輪6發等)，道具存入 carried_items │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 模組三：跨語言對齊與「9大屬性數值指紋」引擎                                     │
│   ① 詞彙雙向正規化：Spot Hidden ➔ 偵查；Private Investigator ➔ 私家偵探        │
│   ② 9 大屬性數值指紋：STR/CON/SIZ... ≥ 7 項吻合 ➔ 判定為同角色 (無視中英姓名差異)│
│   ③ 自學習字典同步：發現新翻譯詞彙 ➔ 沉澱至 data/dictionary.json                │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 模組四：重複角色衝突比對與「擇優融合」 (Reconciliation)                         │
│   ・屬性與技能：以人工手修卡為主                                                │
│   ・專屬劇情：自動繼承 PDF 抽出的秘密動機 (Secret Goal)                         │
│   ・場上出戰角色：執行【熱補丁】，保留當前負傷 HP、SAN 與道具                   │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 模組五：玩家自動綁定與一鍵認領 (Auto-Binding & Claim System)                    │
│   ・標註「玩家：XXX」且命中成員 ➔ 自動寫入 state.characters[user_id] 立即出戰   │
│   ・未註明玩家 ➔ 列入待認領名單，玩家輸入 /coc claim 角色名 即可秒速接管        │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 模組六：回報透明詳細的比對與載入報告 (Transparent Feedback Report)              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 模組一：智慧檔案分流與增量載入

### 檔案命名規範與型態識別路由（LINE & Discord 統一）

系統採用統一前綴規格，確保 GM 批次上傳或單檔上傳時能 100% 精準識別用途：

* **地圖檔（規範前綴：`map_*.yaml` 或 `map_*.yml`，例如 `map_lighthouse.yaml`）**：
  * **識別機制**：檔名以 `map_` 開頭且為 `.yaml`/`.yml`。
  * **命名 Key**：自動提取檔名作為地圖識別碼（如 `map_lighthouse` 或 `lighthouse`），方便玩家用 `/coc enter map_lighthouse` 進入。
  * **增量合併**：`state.scene_maps[key] = parsed_map`，不影響其他已存在地圖。
  * **位置保護**：絕不清除已在場玩家的當前房間追蹤（`current_map_page` 與 `current_room_id`）。
  * **一次一張**：確認地圖始終一次一張、一則訊息一個檔案上傳，不需要處理同一則訊息多個地圖檔的情況。
* **角色卡檔（規範前綴：`role_*.txt` 或 `role_*.md`，例如 `role_carter.txt`）**：
  * **識別機制**：檔名以 `role_` 開頭。
  * **處理管線**：自動送入補齊引擎、跨語言比對與擇優融合管線。
  * **多檔上傳**：確認 GM 幫全團上傳時，有可能把全團角色卡一次拖選進同一則訊息（Discord 原生支援一則訊息多附件）——現有程式碼只處理該則訊息裡第一個符合的 `role_` 附件（`app/discord_bot.py` 的 `role_attachments[0]`），其餘會被靜默忽略，這是需要修的實際 bug，要改成逐一處理該則訊息裡所有 `role_` 開頭附件。
* **劇本檔（`.pdf`，例如 `scenario_the_haunting.pdf`）**：
  * **識別機制**：所有 `.pdf` 檔案。
  * **處理管線**：解析劇本全文、開場白、NPC/地點索引。
  * **狀態保護**：更新 `scenario_text` 與 `scenario_title` 時，**嚴格保留當前群組所有 `characters`**，絕不洗掉現役角色——已確認現有 `handle_pdf_upload` 本來就符合這條，不用改。
  * **角色卡 PDF**：確認這個情境不會發生，維持現狀（所有 `.pdf` 一律當劇本解析），規格書原本「除角色卡 PDF 外」這句話刪除，不再是這個模組的範圍。

⚠️ 實作備註（2026-09-17 討論定案）：「GM 集中打包上傳」確認是指**劇本→地圖→角色卡依序分開傳**，不是同一則訊息塞多種類型的附件——所以不需要把路由邏輯改成通用的「一則訊息任意混合多檔案、逐一分類」架構，只有角色卡這一種類型需要處理「同一則訊息多檔」。

**PDF 重新上傳的位置保護**：現有 `handle_pdf_upload` 每次上傳新 PDF 都無條件重置 `scene_maps`／`current_map_page`／`current_room_id`／`party_facing`／`pregens`／`openai_previous_response_id`／`game_started`。這對「全新劇本」合理，但如果 GM 是要「重傳修正版的同一份劇本」（確認這個情境有可能發生），就會不小心把玩家的地圖位置、角色卡池全部洗掉。新增流程：偵測到 `state.scenario_text` 已非空（已有劇本在跑）時，先不直接存檔，改成用按鈕讓 GM 選「全新劇本」或「修正目前劇本」——判斷同一份劇本的依據不用猜（檔名／標題都不夠可靠），直接讓 GM 自己選最安全。

- 選「全新劇本」：維持現有全部重置的行為。
- 選「修正目前劇本」：
  - 更新：`scenario_text`、`scenario_title`、`scenario_npc_index`、`scenario_location_index`、頁面圖片（兩種模式都要更新，跟選擇無關，可以立即套用不用等按鈕）
  - 保留不動：`scene_maps`、`current_map_page`、`current_room_id`、`party_facing`、`pregens`、`openai_previous_response_id`（保留對話連貫性）
  - `game_started`：維持原樣，不重置成 `False`（劇本沒有真的重新開始，不該讓 `/coc start` 又能再跑一次）
- 抽取結果（`text`／`title`／`extracted_index`／`page_maps`，不含已立即套用的圖片）在等待 GM 按鈕決定期間，持久化在新增的 `GroupState` 欄位（例如 `pending_pdf_upload`），跟現有 `pending_checks`／`pending_luck_decisions` 一樣的持久化 pending-按鈕模式（這個 bot 幾乎每次部署都重啟，按鈕要撐得過重啟）。首次上傳（`state.scenario_text` 本來就是空的）沒有歧義，跳過按鈕直接照「全新劇本」流程走。

---

## 4. 模組二：「有缺的補上」—— 自動補齊引擎

### 1. 衍生數值自動推算
上傳角色卡若僅填寫 9 大基礎屬性，系統自動依 COC7e 官方規則推算並填補缺失數值：
* **最大生命值 (HP)**：$\lfloor(CON + SIZ) / 10\rfloor$
* **最大魔法值 (MP)**：$\lfloor POW / 5\rfloor$
* **初始理智值 (SAN)**：$\min(POW, 99)$
* **體格 (Build) 與 傷害加值 (DB)**：
  * $STR + SIZ \le 64 \implies Build: -2,\ DB: -2$
  * $STR + SIZ \le 84 \implies Build: -1,\ DB: -1$
  * $STR + SIZ \le 124 \implies Build: 0,\ DB: 0$
  * $STR + SIZ \le 164 \implies Build: 1,\ DB: +1\text{D}4$
  * $STR + SIZ \le 204 \implies Build: 2,\ DB: +1\text{D}6$
* **移動力 (MOV)**：
  * 若 $DEX < SIZ$ 且 $STR < SIZ \implies MOV = 7$
  * 若 $STR \ge SIZ$ 或 $DEX \ge SIZ \implies MOV = 8$
  * 若 $STR > SIZ$ 且 $DEX > SIZ \implies MOV = 9$

⚠️ 實作備註（2026-09-17 討論定案）：核對現有 `app/models.py` 的 `damage_bonus_and_build`／
`move_rate`，逐一級距比對公式，**已經正確實作，這塊不用改**——連本表沒列到的
「STR+SIZ > 204」超大體格情況，現有程式碼都有額外外插處理。`pregen_to_character`
（角色卡上傳路徑）跟 `generate_investigator`（`/coc pc` 快速建角路徑）共用同一組公式，
沒有分歧。

### 2. 官方基礎技能補齊
以 COC7e 官方 `BASE_SKILLS` 為標準庫，角色卡未特別加點的項目，自動補齊預設值：
* `閃避`：$\lfloor DEX / 2\rfloor$
* `母語`：$EDU$
* `急救`：$30\%$、`聆聽`：$20\%$、`偵查`：$25\%$、`心理學`：$10\%$...
* 保證守密人後續執行任何技能檢定時，絕不出現未定義或找不到技能數值的問題。

⚠️ 實作備註（2026-09-17 討論定案）：`app/models.py` 本來就有完整的官方 `BASE_SKILLS`
（47 項），`generate_investigator`（`/coc pc`）也正確用了它（`skills = dict(BASE_SKILLS)`）。
但**角色卡上傳路徑**（`pregen_to_character`，`role_` 上傳跟劇本內建 pregen 都走這條）
**只補了「閃避」「母語」兩項，其他 47 項官方技能完全沒補**——上傳的角色卡如果沒寫到
「聆聽」，這個角色的 `skills` 裡就真的沒有這個 key。修法：比照 `generate_investigator`，
`pregen_to_character` 起手先 `skills = dict(BASE_SKILLS)`，角色卡實際寫的數值疊上去
覆蓋預設值即可。

### 3. 武器與隨身物品泛化解析
```text
┌─────────────────────────────────────────────────────────────┐
│ 區塊泛化掃描                                                │
│ 相容：【武器】/【裝備】/【隨身物品】/【攜帶物品】/【道具】 │
└──────────────────────────────┬──────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼ 槍械類                      ▼ 近戰/隨身道具
┌──────────────────────────────┐ ┌──────────────────────────────┐
│ 彈藥自動偵測與官方庫補齊     │ │ 寫入 Character.carried_items │
│                              │ │                              │
│ ・若已標明 (6發/裝彈6) ➔ 採用 │ │ ・手電筒、急救箱、開山刀...  │
│ ・漏標彈藥 ➔ 查閱官方槍械庫：│ │ ・每回合即時呈報給守密人     │
│   - 左輪手槍 (.38/.45) ➔ 6發 │ └──────────────────────────────┘
│   - 半自動 (Glock/9mm) ➔ 15發│
│   - 霰彈槍 (Shotgun)   ➔ 2發 │
│ ・寫入 Character.weapons     │
└──────────────────────────────┘
```

⚠️ 實作備註（2026-09-17 討論定案）：核對 `app/pregen_extractor.py`，確認兩個子問題都是
真的缺口：(1) 現在只認「武器」這個精確標題，寫「裝備」「隨身物品」「攜帶物品」「道具」
都不會被解析，整段被丟進 `notes` 純文字；(2) 彈藥自動補齊完全沒做——現在只有明確寫出
彈容量的槍才會被記錄進 `weapons`，其餘（沒寫彈容量的槍、近戰武器、任何道具）只會出現在
`notes` 的原文複製，不會變成結構化的 `carried_items`。已確認這造成實際問題（GM 反映玩家
常忘記自己有哪些裝備）——`notes` 是靜態欄位（建角色時寫一次就不變），`carried_items`
才是每回合都重新塞進 Keeper prompt 的動態欄位，東西只寫在 `notes` 裡，長時間跑團後容易
被 Keeper 淡忘。

**區塊分流方式**：討論後選定「全部視為同一種區塊，逐段用內容判斷分流」（而非依標題分成
武器類／道具類兩組）——理由是實務上 GM 常把武器跟道具混寫在同一個模糊標題（例如「裝備」
底下同時列一把手槍的完整資料跟一支手電筒），依標題分組沒辦法處理這種情況，內容判斷才能
貼近實際寫法。分流規則：每一段（以空行分隔）符合以下任一條件就判定為武器——(a) 有明確
「彈容量」／「彈匣」欄位、(b) 有「技能」欄位且數值像戰鬥技能（射擊/格鬥/投擲類）、(c) 名稱
命中官方槍械關鍵字庫（即使完全沒寫任何欄位）；都不符合則當普通道具，整行名稱寫進
`carried_items`。是武器但沒寫彈容量，查官方槍械庫補上預設值；查不到就維持現況（不追蹤
彈藥，但至少會被記錄進 `weapons`，不會整段被吞進 notes）。

**官方槍械彈藥庫的年代問題**：同一個泛稱（例如「半自動手槍」）在不同年代設定下的合理預設
彈容量差很多（1920 年代經典 Colt M1911 常見 7 發，現代 Glock 常見 15-17 發），且已確認
**大部分玩家寫角色卡時用泛稱、很少寫具體型號**，代表年代判斷會被頻繁用到。具體型號名稱
（如有寫）可以不靠年代直接查到準確值。決定：新增「年代」設定（`GroupState` 新欄位，預設
1920s，GM 可用指令切換 modern），槍械庫涵蓋左輪手槍／半自動（手槍）／霰彈槍／步槍／衝鋒槍
（已確認這 5 類是玩家實際常見的泛稱寫法），各年代各準備一套預設彈容量。

---

## 5. 模組三：跨語言對齊與「9 大屬性數值指紋」

當面對英文劇本（例如 `Malcolm Carter`）與中文手修卡（`卡特`）時，系統使用**數值指紋穿透語言障礙**。

```text
[ 英文 PDF 角色：Malcolm Carter ]       [ 中文手修卡：卡特 / 麥爾坎・卡特 ]
       STR: 70, CON: 60, SIZ: 75                STR: 70, CON: 60, SIZ: 75
       DEX: 50, APP: 40, INT: 80                DEX: 50, APP: 40, INT: 80
       POW: 65, EDU: 75, LUCK: 55               POW: 65, EDU: 75, LUCK: 55
                    │                                        │
                    └───────────────────┬────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 數值指紋比對器 (Numeric Fingerprint Matcher)                                    │
│   比對 9 大屬性數字（數字不受語言翻譯影響）：                                   │
│   [STR, CON, SIZ, DEX, APP, INT, POW, EDU, LUCK]                                │
│                                                                                 │
│   ➔ 比對結果：9 項屬性有 ≥ 7 項完全一致！                                       │
│   ➔ 判定結果：100% 為同一位角色！                                               │
│   ➔ 標記別名：中文名「卡特」，別名備註「Malcolm Carter」                        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 【跨語言智能對齊與指紋比對流程圖】

```text
========================================================================================
       【跨語言（中英翻譯落差）智能對齊與數值指紋比對機制 (Cross-Lingual Engine)】
========================================================================================

   [ 英文/翻譯 PDF 角色 A ]                      [ 玩家中文手修卡 B ]
   (Name: Malcolm Carter)                        (姓名：卡特 / 麥爾坎・卡特)
   (Job: Private Investigator)                   (職業：私家偵探)
   (Skills: Spot Hidden 65%, Listen 50%)         (技能：偵查 65%, 聆聽 50%)
              │                                               │
              ▼                                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 步驟 1：全域詞彙中英正規化 (Bilingual Normalization Layer)                           │
│                                                                                      │
│  ✔ 技能字典對齊：Spot Hidden ➔ 偵查；Firearms (Handgun) ➔ 射擊（手槍）...             │
│  ✔ 職業字典對齊：Private Investigator / Detective ➔ 私家偵探                         │
│  ✔ 文字去雜質：去除點號（·、•）、括號別名 (Carter) 提取乾淨比對詞                     │
│                                                                                      │
│  ⚠️ 實作備註（2026-09-17 討論定案）：這一步不是獨立的翻譯機制，直接委派給模組五的     │
│     自學習字典（data/dictionary.json 的 skills/occupations 分類）——查表命中就秒速   │
│     正規化；沒收錄的生字才進 LLM 判斷，判斷成功後自動存回字典，下次直接命中。這樣    │
│     常見的官方技能／職業英文名稱只要學過一次，之後所有劇本都能免費秒速比對，不用     │
│     每次抽取角色卡都重新呼叫 LLM 翻譯。技能字典會在專案初期先「種」一批官方 47 項    │
│     BASE_SKILLS 常見的英文對照（Spot Hidden、Listen、Firearms (Handgun) ...），      │
│     不從零開始學。詳見模組五。                                                       │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 步驟 2：三層跨語言同一性判定 (Multi-Gate Cross-Lingual Matcher)                      │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │
       ┌───────────────────────────────────┼───────────────────────────────────┐
       ▼ (第 1 閘門：括號別名／已學過的姓名字典)  ▼ (第 2 閘門：數值指紋 ★核心)       ▼ (第 3 閘門：職業與專長技能)
┌─────────────────────────────┐     ┌─────────────────────────────┐     ┌─────────────────────────────┐
│ 姓名包含，或命中已學過的    │     │ 9 大屬性數字指紋吻合？      │     │ 職業相同 且 專長技能數值    │
│ character_aliases 配對？    │     │ (超越語言的數字密碼)        │     │ 有 3 項以上完全相同？       │
│                             │     │                             │     │                             │
│ ・"Carter" in "卡特"        │     │ STR/CON/SIZ/DEX/APP/INT...  │     │ 職業：私家偵探              │
│ ・括號英文命中 "卡特(Carter)"│    │ 9 個數字中 ≥ 7 個完全一致！ │     │ 偵查 65% + 聆聽 50% 吻合    │
│ ・字典查到已學過的配對      │     │                             │     │                             │
└──────────────┬──────────────┘     └──────────────┬──────────────┘     └──────────────┬──────────────┘
               │ 命中                              │ 命中                              │ 命中
               └───────────────────┬───────────────┴───────────────────────────────────┘
                                   │
                                   ▼ 任何一項閘門判定通過

⚠️ 實作備註（2026-09-17 討論定案）：第 1 道閘門刻意不做「Malcolm ↔ 馬爾科姆」這種純字串音譯
比對——姓名欄位（見 extract_pregens 的 schema）本來就要求「維持原文，不要翻譯」，兩種語言的
姓名字面上通常沒有任何共同字元，純字串演算法猜不出「Malcolm Carter」跟「卡特」是同一人，
只有「中文名附註括號英文原名」（如「卡特(Carter)」）這種明確寫法才能可靠比對。第一次遇到
「完全沒附註」的跨語言姓名配對，第 1 道閘門不會命中，但這沒關係——真正扛住這種情況的是
第 2 道閘門（數值指紋，語言無關，最可靠）。等第 2 或第 3 道閘門判定成功後，這組姓名配對
會自動存進模組五字典的 character_aliases，下次再遇到同一個姓名就能直接在第 1 道閘門秒速
命中，不用每次都重新跑一次完整的指紋比對。也就是說，第 1 道閘門真正的「智慧推斷通道」就是
第 2、3 道閘門本身，不需要為姓名比對另外開一次 LLM 呼叫。
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 步驟 3：判定成功！認定為【同一位角色】（突破中英文隔閡）                             │
│                                                                                      │
│  ✔ 名稱優選：保留中文手修名「卡特」，並將英文原名標為備註「(Malcolm Carter)」       │
│  ✔ 執行擇優融合：手修數值為主 + 自動繼承英文劇本裡的 Secret Goal 秘密動機             │
│  ✔ 自動消除中英文雙胞胎重複卡                                                        │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 步驟 4：清楚透明的回報報告 (Transparent Feedback)                                    │
│                                                                                      │
│  📋 跨語言角色比對成功：                                                             │
│  ✨【卡特 / Malcolm Carter】（私家偵探）                                             │
│     ・比對依據：9 大屬性數值指紋 100% 吻合（STR 70, CON 60...）                      │
│     ・技能對齊：英文 Spot Hidden 65% 已成功對齊中文「偵查 65%」                      │
│     ・劇情線索：已從英文劇本繼承專屬個人目標                                         │
│     ・狀態：已完成融合，待認領／已綁定！                                             │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 模組四：重複角色衝突比對與「擇優融合」

當系統確認手傳角色卡與 PDF 抽取角色為同一人時，執行**欄位級別擇優融合（Best-of-Both）**：

| 欄位類別 | 採納來源 | 融合邏輯與原因 |
| :--- | :--- | :--- |
| **屬性與衍生值** | **手傳卡優先** | 人工校對版最精準，避免 PDF OCR 誤識；手傳卡漏項由 PDF 補齊。 |
| **技能清單** | **取聯集 (Union)** | 兩者皆有以手傳卡為準；**手傳卡漏填但 PDF 有的劇本專用技能自動補入**。 |
| **秘密動機** | **PDF 繼承** | 自動繼承 PDF 獨有的 `secret_goal`（劇本伏筆），維持劇情張力。 |
| **背景故事** | **手傳卡優先** | 保留玩家或 GM 自行撰寫的生動背景自述。 |
| **已出戰動態進度** | **保護現有進度** | 若角色已在場上，**保留當前負傷 HP、扣除 SAN、花費 Luck**，不重置回滿血。 |

### 【重複角色衝突比對與擇優融合流程圖】

```text
========================================================================================
            【重複角色衝突比對與擇優融合機制 (Smart Reconciliation)】
========================================================================================

    [ PDF 自動抽取角色 A ]                 [ GM 上傳手修角色卡 B ]
    (來源：劇本內文 LLM 抽取)              (來源：GM / 玩家自行整理文字檔)
            │                                       │
            └───────────────────┬───────────────────┘
                                │
                                ▼
         ┌──────────────────────────────────────────────┐
         │ 1. 角色同一性判定 (Identity Match)           │
         │    比對：角色姓名 / 別名 / 職業              │
         │    (例：「卡特」 vs 「卡特·布朗·私家偵探」)  │
         └──────────────────────┬───────────────────────┘
                                │
                   ┌────────────┴────────────┐
                   ▼ 是同一位角色            ▼ 完全不同角色
         ┌───────────────────────┐ ┌───────────────────────┐
         │ 進入【擇優融合引擎】  │ │ 視為兩位獨立角色      │
         └───────────┬───────────┘ │ 分別獨立存入清單      │
                     │             └───────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 欄位級別擇優與補齊 (Field-Level Best Selection)          │
│                                                             │
│  [屬性欄位]                                                 │
│    以「手傳卡 B」為主 (人工校對精準)                         │
│    └─ 遇到 B 缺漏的屬性 ➔ 從「PDF 卡 A」自動補上            │
│                                                             │
│  [技能列表 Skills]                                          │
│    兩者技能取「聯集 (Union)」                               │
│    ├─ 重複技能：以 B 的校訂數值為主                         │
│    └─ B 漏掉但 A 有的劇本專屬技能 ➔ 自動補入 (不遺漏關鍵線索)│
│                                                             │
│  [劇情專屬欄位]                                             │
│    A 有但 B 沒寫的「秘密目標 (Secret Goal)」➔ 自動繼承進來!  │
│    B 額外撰寫的「角色自傳背景」             ➔ 完整保留      │
│                                                             │
│  [玩家身分]                                                 │
│    若 B 有註記「玩家：小明」 ➔ 自動帶入玩家擁有者            │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 輸出單一「最優品質」黃金角色卡 (Golden Character Record) │
│                                                             │
│  ✔ 消除重複條目，群組內永遠只有一份最乾淨的角色             │
│  ✔ 兼具「人工校對的精確數值」+「劇本自帶的原生劇情鉤子」   │
│  ✔ 若有對應玩家，直接綁定出戰；無對應玩家則放入待領池       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. 產生比對與融合報告 (Merge Audit Report)                  │
│                                                             │
│  📋 角色偵測與融合完成：                                   │
│  ✨【卡特（私家偵探）】                                     │
│     ・偵測到手傳卡與劇本預製角色重複，已自動完成「擇優融合」│
│     ・基礎屬性：採用手修校訂版 (STR 70, CON 60...)          │
│     ・技能補齊：自手修版匯入 12 項技能，並自劇本補回「神秘學│
│                 20%」與「克蘇魯神話 5%」                    │
│     ・劇情保留：已繼承劇本自帶的秘密個人動機                │
│     ・已自動綁定玩家：@小明                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. 模組五：自學習動態字典系統（Self-Learning Dictionary）

### 字典檔案結構 (`data/dictionary.json`)
```json
{
  "meta": {
    "version": "1.0",
    "last_updated": "2026-09-17T07:30:00Z"
  },
  "skills": {
    "spot hidden": "偵查",
    "listen": "聆聽",
    "firearms (handgun)": "射擊（手槍）",
    "handgun": "射擊（手槍）",
    "library use": "圖書館使用",
    "dodge": "閃避",
    "occult": "神秘學"
  },
  "occupations": {
    "private investigator": "私家偵探",
    "detective": "私家偵探",
    "antiques dealer": "骨董商",
    "alienist": "精神病學家",
    "physician": "醫生"
  },
  "weapons": {
    "colt m1911": { "ammo": 7, "type": "handgun" },
    "glock 19": { "ammo": 15, "type": "handgun" },
    ".38 revolver": { "ammo": 6, "type": "revolver" }
  },
  "character_aliases": {
    "malcolm carter": "卡特",
    "father michael": "邁克神父"
  }
}
```

⚠️ 實作備註（2026-09-17 討論定案）：`skills` 分類不從空白開始等使用中慢慢學——official
COC7e 的 `BASE_SKILLS`（app/models.py，共 47 項）常見的英文對照是固定、已知、一次性的翻譯
工作，上線前就先「種」一批進 `data/dictionary.json`（Spot Hidden、Listen、Firearms (Handgun)、
Library Use、Dodge、Occult...），不用等第一次真的遇到英文劇本才觸發 LLM 現學。`occupations`
跟 `character_aliases` 沒有這種「固定、有限」的特性（職業／姓名是劇本自訂的開放集合），
維持原設計的「用到才學」即可，不用預先塞資料。

### 字典比對與自學習生命週期
```text
  [ 輸入字詞 (例："Spot Hidden" / "Alienist") ]
                     │
                     ▼
┌──────────────────────────────────────────────┐
│ 1. 查詢 data/dictionary.json                 │
└──────────────────────┬───────────────────────┘
                       │
         ┌─────────────┴─────────────┐
         ▼ 命中快取                  ▼ 未收錄新詞
┌─────────────────────────────┐ ┌─────────────────────────────┐
│ 秒速正規化為標準中文        │ │ 進入智慧推斷通道 (LLM/指紋) │
│ (零 Token 消耗、零延遲)     │ └──────────────┬──────────────┘
└─────────────────────────────┘                │
                                               ▼ 判定成功 (高置信度)
                                ┌─────────────────────────────┐
                                │ 自動沉澱入庫 (Auto-Persist) │
                                │ 寫入 data/dictionary.json   │
                                │ 下次遇到即刻秒速比對！      │
                                └─────────────────────────────┘
```

---

## 8. 模組六：玩家自動綁定與一鍵認領

### 1. 玩家標籤自動匹配
* 角色卡內文若包含 `【角色資料】 姓名：卡特　玩家：小明`：
  * 系統自動比對群組成員的帳號 ID 或顯示名稱。
  * **比對成功** ➔ 直接指派 `state.characters[user_id] = character`，**上傳完成即刻出戰**。

### 2. 一鍵認領未指派角色
* 若卡片未標記玩家，或玩家尚未進群：
  * 角色進入**待認領調查員清單**（依角色姓名為唯一 Key，不再被同職業覆蓋）。
  * 玩家進群後輸入：
    * `/coc claim 角色名`（例如 `/coc claim 瑪莉`）
    * 或 `/coc claim 編號`（例如 `/coc claim 1`）
  * 系統立即將該角色綁定給該玩家，完成出戰交接。

---

## 9. 模組七：整備階段與 /coc start 全員角色裝備就緒閘門

### 1. 遊戲生命週期二元劃分（Phase Separation）

為避免 GM 在上傳劇本、地圖或調整角色卡時，玩家在群組聊天意外觸發守密人提前開跑，系統引入嚴格的**整備階段（Lobby Phase）與出戰階段（In-Play Phase）**隔離：

```text
========================================================================================
                 【遊戲階段狀態機 (Game Phase State Machine)】
========================================================================================

       [ GM 上傳劇本 / 地圖 / 角色卡 ]
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ 階段一：整備階段 (Lobby Phase, game_started = False)        │
│                                                             │
│ ✔ GM 集中上傳劇本、地圖與全團角色卡                         │
│ ✔ 系統完成智能解析、有缺補上、裝備彈藥補滿、自動綁定       │
│ ✔ 玩家自由進群查閱卡片、認領角色 (/coc claim)、微調技能     │
│ 🛡️【防誤觸保護】：此階段一般訊息「不會」觸發 Keeper 推進劇情│
│    避免劇本還沒準備好、守密人就開始自說自話                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
               [ GM / 主持人 輸入 /coc start ]
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 閘門：全員角色卡與裝備 Loading 完整度核驗 (Readiness Gate)  │
│                                                             │
│ 1. 劇本檢查：確認劇本 PDF 全文已載入                        │
│ 2. 全員綁定檢驗：確認至少有一位已綁定調查員                 │
│ 3. 裝備與狀態全面 Loading 完成檢核：                        │
│    ・每個已綁定 user_id 的 9 大屬性、HP/SAN 是否已計算完成  │
│    ・每位角色的武器與槍械彈藥是否已滿彈初始化 (ammo == max) │
│    ・隨身物品 (carried_items) 是否載入就緒                  │
│ 4. 輸出【全團調查員集結就緒名冊】                           │
└──────────────────────────────┬──────────────────────────────┘
                               │ 核驗通過
                               ▼ (state.game_started = True)
┌─────────────────────────────────────────────────────────────┐
│ 階段二：正式開跑階段 (In-Play Phase, game_started = True)   │
│                                                             │
│ ✔ 守密人朗讀開場白，正式揭開冒險序幕                         │
│ ✔ 每位玩家的群組發言正式視為角色行動，推進劇情              │
└─────────────────────────────────────────────────────────────┘
```

### 2. `/coc start` 的就緒檢查規範

當執行 `/coc start` 時，系統執行下列檢查邏輯：

1. **劇本狀態檢查**：若未載入劇本，擋下並提示：「目前尚未載入劇本，請先上傳 PDF 劇本。」
2. **調查員名冊檢查**：若 `state.characters` 為空，擋下並提示：「目前尚無任何已綁定的調查員，請先由玩家認領或建立角色。」
3. **全員角色卡與裝備 Loading 完成度確認**：
   * 遍歷所有已綁定的 `owner_id -> Character`：
     * **屬性與衍生值**：確認 HP、MP、SAN、MOV、DB、Build 皆已推算完畢。
     * **武裝載入**：確認 `weapons` 內槍械彈藥已正確滿彈加載（例如 `.38 左輪手槍 6/6`）。
     * **隨身物品**：確認 `carried_items` 清單載入完備。
4. **未認領名單提示**：
   * 若待認領池中仍有預製角色，在開團名冊中溫馨提示：「尚有 X 位預製角色未被認領，本次以此陣容出戰。」
5. **正式啟動**：
   * 打印「全團調查員集結就緒名冊」。
   * 切換 `state.game_started = True`。
   * 朗讀劇本開場白，正式啟動遊戲。

---

## 10. 附錄：GM 載入完成報告與開團就緒報告範例

### 範例一：GM 集中上傳完成時的回報（整備階段）

```markdown
📋 戰役資源集中載入與智能比對報告（整備階段中）

🗺️【地圖與場景】
・第 12 頁平面圖已載入，共 5 個房間（入口：玄關大廳）

👥【調查員比對與融合結果】
✅【已自動綁定】
・卡特 / Malcolm Carter（私家偵探） ➔ 綁定給 @小明
  （9大屬性指紋 100% 吻合；自劇本補回「秘密動機」；武器「.38左輪」自動補滿 6 發彈藥）

⏳【待認領調查員】
1. 瑪莉（記者） ➔ 隨身物品：照相機、筆記本、防身小刀
2. 湯姆（警察） ➔ 隨身裝備：警棍、.38左輪手槍（彈藥 6/6）

👉 尚未認領角色的玩家，只需在群組輸入「/coc claim 角色名」（例：/coc claim 瑪莉）即可接管！
📢 GM 準備就緒後，輸入「/coc start」正式檢驗陣容並開始跑團！
```

### 範例二：執行 `/coc start` 時的全體就緒呈報與開場

```markdown
🎲 全體調查員裝備檢驗完畢，遊戲正式開始！

【集結調查員名冊】
・卡特（私家偵探，玩家：@小明）：HP 11/11, SAN 65/65, 彈藥：.38左輪手槍 (6/6), 物品：手電筒、開鎖工具
・瑪莉（記者，玩家：@小芳）：HP 9/9, SAN 70/70, 物品：照相機、隨身筆記本、防身小刀
・湯姆（警察，玩家：@阿豪）：HP 13/13, SAN 55/55, 彈藥：.38左輪手槍 (6/6), 物品：警徽、警棍、手銬

════════════════════════════════════════════════
（守密人）：
雨夜的波士頓街頭瀰漫著煤煙與濕冷的泥土氣息。老舊的煤氣路燈在濃霧中閃爍不定，
你們三人在聖瑪麗教堂後方的石板小巷碰頭，手中的委託信已被雨水浸濕了一角……
════════════════════════════════════════════════
👉 調查員們，你們現在打算怎麼做？
```

---

## 11. 附錄：核心模組範例代碼（Reference Implementation Skeleton）

以下提供核心模組的參考範例骨架代碼，展示三大模組如何對接現有的 `GroupState` 與 `Character` 資料結構：

### 範例代碼 1：自學習動態字典管理器 (`app/dictionary_manager.py`)

```python
"""app/dictionary_manager.py
自學習字典管理器：本機快顯查表、中英文詞彙正規化與新詞自動持久化沉澱。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.models import BASE_SKILLS

DICTIONARY_PATH = DATA_DIR / "dictionary.json"

_DEFAULT_DICTIONARY: dict[str, Any] = {
    "skills": {
        "spot hidden": "偵查", "listen": "聆聽", "dodge": "閃避",
        "first aid": "急救", "library use": "圖書館使用", "occult": "神秘學",
        "handgun": "射擊（手槍）", "firearms (handgun)": "射擊（手槍）",
    },
    "occupations": {
        "private investigator": "私家偵探", "detective": "私家偵探",
        "antiques dealer": "骨董商", "alienist": "精神病學家", "physician": "醫生",
    },
    "weapons": {
        "左輪手槍": {"ammo": 6, "type": "revolver"},
        ".38 左輪手槍": {"ammo": 6, "type": "revolver"},
        ".38 revolver": {"ammo": 6, "type": "revolver"},
        "glock 19": {"ammo": 15, "type": "handgun"},
        "colt m1911": {"ammo": 7, "type": "handgun"},
        "雙管霰彈槍": {"ammo": 2, "type": "shotgun"},
        "shotgun": {"ammo": 2, "type": "shotgun"},
    },
    "character_aliases": {
        "malcolm carter": "卡特",
        "carter": "卡特",
    },
}


class DictionaryManager:
    """單例字典管理器，支援熱讀取與自動寫入"""
    def __init__(self, file_path: Path = DICTIONARY_PATH):
        self.file_path = file_path
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._save(_DEFAULT_DICTIONARY)
            return dict(_DEFAULT_DICTIONARY)
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception:
            return dict(_DEFAULT_DICTIONARY)

    def _save(self, data: dict[str, Any]) -> None:
        self.file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def normalize_skill(self, skill_name: str) -> str:
        """查表正規化技能名稱（中英文對齊）"""
        clean = skill_name.strip().lower()
        canonical = self._data.get("skills", {}).get(clean)
        return canonical or skill_name.strip()

    def normalize_occupation(self, occ_name: str) -> str:
        """查表正規化職業名稱"""
        clean = occ_name.strip().lower()
        return self._data.get("occupations", {}).get(clean) or occ_name.strip()

    def get_weapon_ammo_preset(self, weapon_name: str) -> int | None:
        """查表獲取常見槍械預設彈藥量（落實有缺自動補上）"""
        clean = weapon_name.strip().lower()
        for key, info in self._data.get("weapons", {}).items():
            if key in clean:
                return info.get("ammo")
        return None

    def learn_term(self, category: str, source_term: str, standard_term: Any) -> bool:
        """【自學習沉澱】：發現新對應詞時自動寫回檔案"""
        cat = self._data.setdefault(category, {})
        clean_src = source_term.strip().lower()
        if clean_src not in cat:
            cat[clean_src] = standard_term
            self._save(self._data)
            return True
        return False


dict_mgr = DictionaryManager()
```

---

### 範例代碼 2：角色補齊、指紋比對與擇優融合引擎 (`app/character_reconciler.py`)

```python
"""app/character_reconciler.py
角色卡自動補齊、跨語言指紋比對、與重複角色擇優融合引擎。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.dictionary_manager import dict_mgr
from app.models import (
    BASE_SKILLS,
    Character,
    GroupState,
    damage_bonus_and_build,
    move_rate,
)


@dataclass
class ReconcileResult:
    auto_bound: list[str] = field(default_factory=list)      # 已自動綁定玩家的角色清單
    patched: list[str] = field(default_factory=list)         # 既有出戰角色熱更新清單
    claimable: list[str] = field(default_factory=list)       # 待認領調查員清單
    learned_terms: list[str] = field(default_factory=list)   # 字典自學習新收錄清單


class CharacterReconciler:
    # 9 大基礎屬性欄位
    ATTR_KEYS = ("str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck")

    # 1. 「有缺的補上」：衍生值與基礎技能自動推算
    @classmethod
    def auto_complete_stats(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """依 COC7e 官方公式自動補齊所有缺漏的衍生屬性與基礎技能"""
        con = raw.get("con", 50)
        siz = raw.get("siz", 50)
        pow_ = raw.get("pow_", 50)
        str_ = raw.get("str_", 50)
        dex = raw.get("dex", 50)
        edu = raw.get("edu", 50)

        # 衍生數值公式推算補齊
        raw.setdefault("hp_max", (con + siz) // 10)
        raw.setdefault("hp", raw["hp_max"])
        raw.setdefault("mp_max", pow_ // 5)
        raw.setdefault("mp", raw["mp_max"])
        raw.setdefault("san_max", 99)
        raw.setdefault("san", min(pow_, 99))
        
        db, build = damage_bonus_and_build(str_, siz)
        raw.setdefault("damage_bonus", db)
        raw.setdefault("build", build)
        raw.setdefault("move", move_rate(str_, dex, siz))

        # 官方 BASE_SKILLS 基礎技能補齊
        skills = raw.setdefault("skills", {})
        normalized_skills = {}
        for s_name, s_val in skills.items():
            norm_name = dict_mgr.normalize_skill(s_name)
            normalized_skills[norm_name] = s_val
        
        for base_k, base_v in BASE_SKILLS.items():
            normalized_skills.setdefault(base_k, base_v)
        normalized_skills.setdefault("閃避", dex // 2)
        normalized_skills.setdefault("母語", edu)
        raw["skills"] = normalized_skills
        return raw

    # 2. 泛化提取裝備、槍械手槍與彈藥自動補齊
    @classmethod
    def extract_weapons_and_items(cls, sections: dict[str, str]) -> tuple[dict, list[str]]:
        """從各類裝備區塊中，分流提取 weapons (含彈藥) 與 carried_items"""
        weapons: dict[str, dict[str, int]] = {}
        carried_items: list[str] = []

        target_text = "\n".join([
            sections.get(k, "") for k in (
                "武器", "裝備", "隨身物品", "攜帶物品", "個人物品", "道具", "inventory", "equipment"
            ) if k in sections
        ])

        for line in target_text.splitlines():
            line = line.strip().lstrip("-*・").strip()
            if not line:
                continue

            is_firearm = any(kw in line.lower() for kw in ("手槍", "左輪", "槍", "revolver", "gun", "rifle", "glock", "colt"))
            ammo_match = re.search(r"(\d+)\s*(發|rds|rounds|shots)", line, re.I)
            capacity = int(ammo_match.group(1)) if ammo_match else None

            if is_firearm:
                clean_name = line.split("：")[0].split(":")[0].split("（")[0].split("(")[0].strip()
                if capacity is None:
                    capacity = dict_mgr.get_weapon_ammo_preset(clean_name) or 6
                weapons[clean_name] = {"ammo": capacity, "ammo_max": capacity}
            else:
                item_name = line.split("：")[0].split(":")[0].strip()
                if item_name and len(item_name) < 25:
                    carried_items.append(item_name)

        return weapons, carried_items

    # 3. 超越語言的「9 大屬性數值指紋 (Numeric Fingerprint)」比對
    @classmethod
    def is_same_character(cls, a: dict | Character, b: dict | Character) -> bool:
        """判定兩張卡是否為同一人：姓名包含 或 9大屬性數值指紋吻合"""
        def get_attr(obj, k):
            return getattr(obj, k, None) if isinstance(obj, Character) else obj.get(k)

        matched_attrs = 0
        total_valid = 0
        for k in cls.ATTR_KEYS:
            va, vb = get_attr(a, k), get_attr(b, k)
            if va is not None and vb is not None:
                total_valid += 1
                if va == vb:
                    matched_attrs += 1

        if total_valid >= 7 and matched_attrs >= 7:
            return True

        name_a = getattr(a, "name", "") if isinstance(a, Character) else a.get("name", "")
        name_b = getattr(b, "name", "") if isinstance(b, Character) else b.get("name", "")
        if name_a and name_b:
            na, nb = name_a.strip().lower(), name_b.strip().lower()
            if na == nb or na in nb or nb in na:
                return True
            alias = dict_mgr._data.get("character_aliases", {}).get(na)
            if alias and (alias in nb or nb in alias):
                return True

        return False

    # 4. 「重複角色擇優融合 (Best-of-Both)」核心
    @classmethod
    def reconcile(cls, existing_pc: Character | None, manual_card: dict, pdf_pregen: dict | None) -> Character:
        """產出黃金角色卡：手修數值為主 + PDF劇情繼承 + 保護現役扣血進度"""
        base = dict(manual_card)

        if pdf_pregen and pdf_pregen.get("secret_goal") and not base.get("secret_goal"):
            base["secret_goal"] = pdf_pregen["secret_goal"]

        if pdf_pregen and pdf_pregen.get("skills"):
            for s_name, s_val in pdf_pregen["skills"].items():
                base["skills"].setdefault(s_name, s_val)

        char = Character(
            name=base.get("name") or "未命名調查員",
            owner_id=base.get("owner_id", ""),
            occupation=dict_mgr.normalize_occupation(base.get("occupation", "自由人")),
            str_=base.get("str_", 50), con=base.get("con", 50), siz=base.get("siz", 50),
            dex=base.get("dex", 50), app=base.get("app", 50), int_=base.get("int_", 50),
            pow_=base.get("pow_", 50), edu=base.get("edu", 50), luck=base.get("luck", 50),
            hp=base["hp"], hp_max=base["hp_max"],
            mp=base["mp"], mp_max=base["mp_max"],
            san=base["san"], san_max=base["san_max"],
            move=base.get("move", 8), damage_bonus=base.get("damage_bonus", "0"), build=base.get("build", 0),
            skills=base.get("skills", {}),
            weapons=base.get("weapons", {}),
            carried_items=base.get("carried_items", []),
            notes=base.get("notes", ""),
            secret_goal=base.get("secret_goal", ""),
        )

        # 【熱更新保護】：若角色已在場上，保留當前殘餘 HP/SAN/Luck
        if existing_pc is not None:
            char.owner_id = existing_pc.owner_id
            char.hp = existing_pc.hp
            char.san = existing_pc.san
            char.luck = existing_pc.luck
            char.status_tags = list(existing_pc.status_tags)
            combined_items = list(dict.fromkeys(existing_pc.carried_items + char.carried_items))
            char.carried_items = combined_items

        return char
```

---

### 範例代碼 3：中央上傳管線與 `/coc start` 就緒閘門 (`app/game_pipeline.py`)

```python
"""app/game_pipeline.py
集中上傳分流處理器 與 /coc start 全員角色裝備就緒核驗閘門。
"""
from __future__ import annotations

from typing import Any
from app.character_reconciler import CharacterReconciler, ReconcileResult
from app.models import GroupState, Character
from app.state import save_state


class GamePipeline:

    @classmethod
    def process_centralized_upload(
        cls,
        state: GroupState,
        parsed_cards: list[dict[str, Any]],
        group_members: dict[str, str],  # user_id -> display_name
    ) -> ReconcileResult:
        """GM 一人集中上傳多張角色卡後的比對與指派處理"""
        result = ReconcileResult()

        for raw_card in parsed_cards:
            completed_card = CharacterReconciler.auto_complete_stats(raw_card)

            existing_pc = None
            for owner_id, char in state.characters.items():
                if CharacterReconciler.is_same_character(char, completed_card):
                    existing_pc = char
                    break

            matching_pregen = next(
                (p for p in state.pregens if CharacterReconciler.is_same_character(p, completed_card)), None
            )

            golden_char = CharacterReconciler.reconcile(existing_pc, completed_card, matching_pregen)

            player_tag = completed_card.get("player_name", "").strip().lower()
            matched_user_id = None
            if player_tag:
                for uid, dname in group_members.items():
                    if player_tag in dname.lower() or dname.lower() in player_tag or player_tag in uid:
                        matched_user_id = uid
                        break

            if existing_pc is not None:
                state.characters[existing_pc.owner_id] = golden_char
                result.patched.append(f"{golden_char.name}（已比對更新技能，保留目前 HP {golden_char.hp}/{golden_char.hp_max}）")
            elif matched_user_id:
                golden_char.owner_id = matched_user_id
                state.characters[matched_user_id] = golden_char
                result.auto_bound.append(f"{golden_char.name} ➔ 綁定給 @{group_members[matched_user_id]}")
            else:
                existing_idx = next((i for i, p in enumerate(state.pregens) if p.get("name") == golden_char.name), None)
                pregen_dict = golden_char.to_dict() if hasattr(golden_char, "to_dict") else {}
                if existing_idx is not None:
                    state.pregens[existing_idx] = pregen_dict
                else:
                    state.pregens.append(pregen_dict)
                result.claimable.append(golden_char.name)

        save_state(state)
        return result

    # ─────────────────────────────────────────────────────────────
    # 【/coc start 全員角色卡與裝備 Loading 完整度核驗閘門】
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def verify_readiness_for_start(cls, state: GroupState) -> dict[str, Any]:
        """執行 /coc start 時的全面核驗"""
        if not state.scenario_text:
            return {"ok": False, "error": "尚未載入劇本，請先上傳劇本 PDF！"}

        if not state.characters:
            return {"ok": False, "error": "目前群組尚無任何已綁定的調查員角色，請先透過 /coc claim 或建立角色！"}

        roster_report = []
        for owner_id, char in state.characters.items():
            weapons_desc = []
            for w_name, w_info in char.weapons.items():
                ammo = w_info.get("ammo", 0)
                ammo_max = w_info.get("ammo_max", 0)
                weapons_desc.append(f"{w_name} ({ammo}/{ammo_max})")

            roster_report.append({
                "user_id": owner_id,
                "name": char.name,
                "occupation": char.occupation,
                "hp": f"{char.hp}/{char.hp_max}",
                "san": f"{char.san}/{char.san_max}",
                "weapons": "、".join(weapons_desc) or "無槍械",
                "items": "、".join(char.carried_items[:5]) or "一般隨身物品",
            })

        state.game_started = True
        save_state(state)

        return {
            "ok": True,
            "roster": roster_report,
            "unclaimed_count": len(state.pregens),
        }
```

