# 設計規格：劇本庫與 PDF 重新解析流程

## 目標

將目前「每個聊天室只保存一份已解析劇本」的模式，改成可重複使用的**劇本庫（Scenario Library）**。

每個劇本在磁碟上有自己的目錄，保存完整解析結果、頁面圖片與劇本內建角色；聊天室只保存「目前使用哪一個劇本」與該團的進度、玩家角色和對話紀錄。

此設計要解決三件事：

1. 同一份 PDF 不因重複上傳而被重複、昂貴地完整解析。
2. 同一份劇本可被不同聊天室／不同團重複使用。
3. 管理者可列出、選用或明確清除劇本，而不是讓上傳行為隱性覆蓋資料。

## 範圍與非目標

本期包含：劇本目錄結構、前頁快速比對、完整重新解析、`list`／`use`／`clean` 指令、與現有 PDF 載入流程的串接。

本期不包含：跨劇本搬移玩家角色、多人權限角色、雲端物件儲存、版本歷史瀏覽 UI，或「自動判定兩份劇本絕對相同」的保證。相似度只用來提示與避免多餘解析，最終仍保留管理者的明確指令。

## 名詞

| 名詞 | 定義 |
| --- | --- |
| 劇本庫項目 | 一個可被載入與重用的劇本資料夾。 |
| 劇本 ID | 系統生成、穩定且檔案系統安全的識別碼，例如 `lightless-beacon-a12f34bc`。 |
| 預覽 | PDF 前 `N` 頁（預設 3 頁）的低成本文字抽取結果。 |
| 快速比對 | 使用檔名、標題與預覽文字，將本次上傳與劇本庫項目比較。 |
| 完整解析 | 現有 `pdf_loader.extract_text()` 的完整流程：文字層、OCR／Vision、地圖、圖片等。 |
| 重新解析 | 對判定為既有劇本的原始 PDF 跑完整解析，完成後更新既有劇本庫項目；不建立重複項。 |

## 資料結構

預設根目錄為 `data/scenarios/`（新增 `SCENARIO_LIBRARY_DIR` 環境變數可覆寫）。每個劇本使用一個資料夾：

```text
data/scenarios/
└── <scenario-id>/
    ├── manifest.json
    ├── source.pdf
    ├── preview.txt
    ├── scenario.txt
    ├── indexes.json
    ├── pregens.json
    ├── scene_maps.json
    └── images/
        ├── page_3.png
        └── page_12.png
```

`manifest.json` 是目錄的唯一索引來源，至少包含：

```json
{
  "id": "lightless-beacon-a12f34bc",
  "title": "The Lightless Beacon",
  "source_filename": "The Lightless Beacon.pdf",
  "created_at": "2026-09-18T00:00:00Z",
  "updated_at": "2026-09-18T00:00:00Z",
  "parser_version": 1,
  "preview_hash": "sha256:...",
  "content_hash": "sha256:...",
  "page_count": 24,
  "low_text_pages": [3, 12],
  "truncated": false
}
```

`indexes.json` 儲存 `{ "npcs": [], "locations": [] }`；`pregens.json` 儲存劇本內建預製調查員。這裡的「角色」是劇本附帶的預製角色與其抽取結果，**不保存特定群組玩家的實際角色卡**，避免同一劇本被第二團使用時帶入第一團的 HP、SAN、背包與劇情進度。

聊天室的 `GroupState` 新增以下欄位：

```python
scenario_library_id: str = ""
active_chapter_id: str = ""
context_chapter_ids: list[str] = field(default_factory=list)
pending_scenario_upload: dict | None = None
```

`scenario_library_id` 指向目前 KP 選定的 PDF 劇本。章節不是聊天室命令的選項，而是劇本庫內部的檢索索引；Agent 會從選定 PDF 的相關章節取回少量內容，而不把整份 PDF 放進 `GroupState` 或 prompt。


## 章節模型

一份 PDF 可包含單次冒險、連續戰役或多個獨立章節；劇本庫項目因此必須支援章節，而不是只把整份 PDF 視為不可分割的文字。

`chapters.json` 保存章節清單；每一章至少有穩定 ID、名稱、頁面範圍與文字範圍：

```json
{
  "chapters": [
    {
      "id": "chapter-01",
      "title": "第一章：失蹤者",
      "start_page": 1,
      "end_page": 12,
      "text_start": 0,
      "text_end": 18420,
      "summary": "可選的短摘要",
      "scene_map_pages": [4],
      "pregen_ids": ["investigator-01"]
    }
  ]
}
```

章節產生優先順序如下：

1. 解析文字內明確的章節標題與頁碼標記。
2. PDF 書籤／目錄（若 PyMuPDF 可取得）。
3. 找不到可靠結構時，建立唯一的 `chapter-01`，範圍為整份劇本；不得自行猜測章節邊界。

章節是**同一劇本庫項目內的載入範圍**，不是新的劇本副本。原始 PDF、完整文字、圖片與抽取索引只存一次；載入章節時，系統只將該章頁面文字、章節關聯地圖、索引項目和預製角色複製到目前團的執行快照。

`/coc scenario list` 列出每一份 PDF 對應的劇本 ID、標題與章節摘要。`/coc scenario use <劇本ID>` 只選定一份 PDF；章節由 Agent 的檢索結果按需使用，不要求 KP 在指令中指定章節。

### 實檔驗證：The Lightless Beacon

以 `The Lightless Beacon - Call of Cthulhu.pdf` 驗證：檔案有 **43 個實體頁面**，具 PDF 書籤，但所有書籤都是同一層級。書籤包含 `Introduction`、`Scenario Overview`、`Background`、`Start: Choppy Waters`、`Dead Beacon`、`Amphibious Assault`、`Conclusion`、`Rewards`、`Epilogue`，以及 `Collected Handouts`、`Pre-Generated Character Sheets`。

因此不能將每個書籤一律視為可獨立遊玩的章節。此樣本應建立下列兩層結構：

```text
劇本：The Lightless Beacon
├── chapter-01  主冒險（實體頁 6–23）
│   ├── introduction / scenario-overview / background
│   ├── start-choppy-waters
│   ├── dead-beacon
│   ├── amphibious-assault
│   └── conclusion / rewards / epilogue
├── appendix-handouts  手卡（實體頁 25–30）
└── appendix-pregens   預製角色（實體頁 31–43）
```

`chapter-01` 是此 PDF 的主冒險章節；手卡與預製角色是附錄資產，仍會與主冒險關聯，但不會被一般劇情檢索主動選取。書籤扁平時，章節切分器需識別 `handout`、`character sheet`、`appendix`、`credits` 等資產類標題，把它們從主劇情文字範圍中排除；其他冒險書籤保留為主章節的 `sections`。

章節資料格式因此擴充為：

```json
{
  "id": "chapter-01",
  "title": "主冒險",
  "kind": "playable",
  "start_page": 6,
  "end_page": 23,
  "sections": [
    {"id": "dead-beacon", "title": "Dead Beacon", "start_page": 11, "end_page": 20}
  ],
  "asset_chapter_ids": ["appendix-handouts", "appendix-pregens"]
}
```

附錄使用 `kind: "asset"`；`/coc scenario list` 可在劇本摘要中顯示它們，但 `/coc scenario use` 永遠只接受 PDF 劇本 ID，不接受章節 ID。
## 上傳與比對流程

### 一般上傳

```text
收到 PDF
  │
  ├─ 1. 擷取前 3 頁預覽（只用 PyMuPDF 文字層；不呼叫 OCR/Vision/LLM）
  ├─ 2. 以檔名、標題、預覽與劇本庫 manifest/preview 比對
  │
  ├─ 沒有相似項目
  │    └─ 3. 執行完整解析 → 再以完整內容比對 → 建立新劇本目錄 → 載入該團
  │
  └─ 有相似項目
       └─ 暫存 source.pdf，要求管理者確認是否重新解析
```

快速比對只讀前幾頁，不執行圖片 OCR、Vision、NPC 索引或預製角色抽取，因此能在昂貴解析前及早攔截重複上傳。


### 上傳、比對與載入流程圖

```text
[使用者上傳 PDF]
        |
        v
[抽取前 3 頁預覽]
  PyMuPDF text layer only
  不做 OCR / Vision / LLM
        |
        v
[劇本庫快速比對]
  檔名 + 標題 + preview hash + 文字相似度
        |
        +------------------------------+
        |                              |
     無相似                         有相似
        |                              |
        v                              v
[完整解析]                       [暫存原始 PDF]
文字 / OCR / Vision /             回覆候選劇本與相似度
地圖 / 圖片 / 索引 / 角色                 |
        |                              v
        |                    [/coc scenario reparse]
        |                              |
        +--------------+---------------+
                       v
                [完整內容二次比對]
                       |
          +------------+------------+
          |                         |
      對應既有劇本               是新劇本
          |                         |
          v                         v
 [原子更新既有劇本目錄]    [建立新劇本目錄]
          |                         |
          +------------+------------+
                       v
        [/coc scenario use <劇本ID>]
                       |
                       v
 [選定此 PDF，建立章節化 RAG 索引]
```
### 相似度規則

比對採可解釋的本地規則，不要求外部 embedding API：

1. 正規化檔名／標題完全相同：直接視為高度相似。
2. 預覽內容 SHA-256 相同：直接視為相同來源版本。
3. 否則計算預覽的 token Jaccard 與 `SequenceMatcher` 分數；達預設門檻（例如 0.82）才列為相似。

回覆必須列出候選劇本 ID、標題與分數，例如：

```text
偵測到可能已存在的劇本：
・lightless-beacon-a12f34bc《The Lightless Beacon》（預覽相似度 94%）

若這是修正版或想重新抽取，請輸入 /coc scenario reparse。
若要放棄本次上傳，請輸入 /coc scenario cancel。
```

### 重新解析

`/coc scenario reparse` 只在有 `pending_scenario_upload` 時可用：

1. 從暫存檔讀取 PDF，執行完整解析。
2. 取得完整文字、圖片、地圖、NPC／地點索引、預製角色。
3. 用完整文字 hash 與完整內容相似度再次比對劇本庫。
4. 若再次判為既有劇本，更新該既有目錄（原子替換各解析產物），不建立第二個副本。
5. 若完整內容顯示不是同一劇本，建立新目錄。
6. 將最後決定的劇本載入目前團。

因此「重新解析」不是單靠前頁猜測覆寫；完整解析完成後仍會再次做比對。只有 `/coc scenario clean` 會從劇本庫永久移除資料夾。

`/coc scenario cancel` 刪除本次暫存檔與 pending 狀態，不影響任何既有劇本。

### 原子性與失敗處理

- 完整解析全部成功後，才用暫存目錄取代目標劇本目錄中的解析產物。
- 解析失敗時保留目前已使用的劇本與庫中舊版本；只回報失敗原因。
- 同一聊天室同時只能有一筆待確認上傳；第二份上傳必須先 `reparse` 或 `cancel` 前一筆。
- 劇本庫目錄操作需有全域 library lock；群組狀態仍使用既有 conversation lock/state lock。

## 指令規格

| 指令 | 行為 |
| --- | --- |
| `/coc scenario list` | 列出每一份已解析 PDF 的劇本：ID、標題、更新時間、頁數、章節摘要，以及目前 KP 是否正在使用它。 |
| `/coc scenario use <劇本ID>` | 僅限目前登記的 KP Assistant 使用。從 `list` 選定一份 PDF 劇本給 Keeper 使用；保留本團角色、劇情紀錄、位置、戰鬥、待處理檢定與 KP OOC 指示。系統只透過章節化 RAG 把相關片段放入 prompt，不載入整份 PDF，並清除 OpenAI 對話鏈。 |
| `/coc scenario clean <劇本ID>` | 從劇本庫永久移除指定目錄及其圖片、原始 PDF 與解析結果。若該劇本正被任何活躍群組使用，拒絕清除並提示先 `/coc scenario use` 切換；不使用模糊標題刪除。 |
| `/coc scenario reparse` | 對本次「相似 PDF」暫存檔執行完整重新解析與二次比對。 |
| `/coc scenario cancel` | 放棄本次待確認的上傳。 |

`/coc scenario list` 的輸出需以 ID 為操作依據，避免同名劇本、中文／英文別名造成誤用或誤刪。

## `/coc scenario use` 的狀態契約

`/coc scenario use <劇本ID>` 是 KP 的 **PDF 劇本選擇器**。它用於從許多已解析的 PDF 中，選出目前 Keeper 可以參考的那一份；它不是章節選擇器、不是開新團，也不是重置遊戲。它只在目前有登記 KP Assistant 且呼叫者為該 KP 時可執行。

它必須：

- 從劇本庫驗證劇本 ID，設定 `scenario_library_id` 與顯示用 title；不將完整 `scenario.txt` 複製到 `GroupState`。
- 建立或取得該 PDF 的章節化 Scenario RAG 索引。每個 chunk 都帶有 `chapter_id`、頁碼與 `kind`（`playable`／`asset`）標記。
- 清除 `openai_previous_response_id`，讓下一輪模型以新選定 PDF 的 system context 建立對話鏈。
- **保留** `log`、`campaign_summary`、Memory RAG、玩家角色、地圖位置、戰鬥、待處理檢定、`game_started`、`keeper_persona`、`era` 與 `kp_ooc_log`。這些都是同一團的連續遊戲狀態。
- 不修改劇本庫內容；遊戲進度仍只寫入此團的 `GroupState`／既有資料庫。

每一輪 `ContextBuilder` 以玩家行動與 KP 指示查詢**目前選定 PDF 的兩章 Context 視窗**，只把最相關 chunks 放入 `AgentMessage`。例如 KP 選定《The Lightless Beacon》後，Keeper 不會一次讀完 43 頁；進入燈塔的行動只會取回 `Dead Beacon` 與下一章的必要銜接段落，而不會看到更後面的結局內容。

## 章節滑動 Context 視窗

章節的目的不是讓 KP 手動挑選，而是讓開團後的 Keeper **不要一次載入整份 PDF**。每個 PDF 的 playable 章節依 `chapters.json` 的頁面順序排列；KP 用 `/coc scenario use <劇本ID>` 選定 PDF 後，系統自動把第一個 playable 章節設為目前章節。

`GroupState` 內部保存：

```python
scenario_library_id: str             # KP 選定的 PDF
active_chapter_id: str               # 劇情目前所在的 playable 章節
context_chapter_ids: list[str]       # Context 視窗，最多兩章：[目前章節, 下一章]
```

### 兩章視窗規則

```text
章節序列： [第一章] -> [第二章] -> [第三章] -> [第四章]

開團：       active=第一章，Context=[第一章, 第二章]
進入第二章： active=第二章，Context=[第二章, 第三章]
進入第三章： active=第三章，Context=[第三章, 第四章]
最終章：     active=第四章，Context=[第四章]
```

每輪 `ContextBuilder` 只可從 `context_chapter_ids` 檢索；因此正常情況下最多載入目前章節與下一章的少量相關 chunks，不會把整份 PDF 或更後面的章節交給 Keeper。下一章作為 lookahead，目的是讓過場、伏筆與角色移動不會因 Context 邊界突然中斷。

Prompt 必須清楚標記 chunk 的角色：

- **目前章節**：可作為當下的敘事事實與機制依據。
- **下一章預讀**：只可用於銜接、準備過場與避免矛盾；在目前章節的轉場條件尚未成立前，不得直接敘述、揭露或要求玩家面對下一章的秘密／事件。

### 依劇本自動推進章節

完整解析完成後，章節器除了頁面邊界外，必須為每個 playable 章節抽取 `entry_conditions`、`exit_conditions` 與 `transition_summary`。這些是結構化的劇本進度條件，例如「調查員抵達燈塔」、「發現屍體」、「襲擊開始」或「逃離島嶼」，而不是單純依頁數跳轉。

每一輪敘事與工具執行完成後，`ChapterProgressor` 以目前章節、近期 log、已完成的工具事實與該章 `exit_conditions` 判斷：

```text
[本輪工具事實 + 最新劇情 log]
                 |
                 v
[ChapterProgressor]
  ├─ continue：維持目前的兩章 Context 視窗
  ├─ advance：active 指向下一 playable 章，視窗向後滑動一章
  └─ uncertain：不切換，保守維持目前視窗
```

`advance` 只在劇本定義的轉場條件已明確滿足時發生；無法確定時一律 `uncertain`，不可因玩家只提到下一地點名稱就跳章。這避免劇情被提早推進或錯過仍未解決的場景。

章節推進只更新 `active_chapter_id`、`context_chapter_ids`、Scenario RAG 的版本鍵，以及 `openai_previous_response_id`；它**不**重設角色、log、地圖位置、戰鬥、檢定、Memory RAG 或 KP OOC 指示。
## Agentic Keeper 整合契約

劇本庫不新增一個用來決定「要載入哪個劇本」的 LLM Agent。上傳去重、章節識別與 `/coc scenario` 指令全部維持在確定性的 Python 指令層，避免 AI 對檔案是否重複做不可驗證的判斷，也避免在每次 PDF 上傳時增加 LLM 成本。

載入完成後，劇本庫以既有 `GroupState` 作為 Agentic Keeper 的唯一執行期資料來源；不讓 `ContextBuilder`、`Executor` 或 `Narrator` 直接讀取劇本庫資料夾。這可保留目前的鎖定、SQLite 快照、測試替身與舊資料相容性。

```text
[/coc scenario use <劇本ID>]
                    |
                    v
[Scenario Library: 唯讀劇本資產]
 scenario.txt / indexes.json / maps / pregens / images
                    |
                    | 設定選定 PDF；不複製完整劇本文字
                    v
[GroupState]
 scenario_library_id + scenario_title + scenario_text
 scenario_npc_index + scenario_location_index + scene_maps + pregens
                    |
                    v
[ContextBuilder]
 ├─ Scenario RAG：僅查詢目前章節與下一章的相關 chunks
 ├─ Memory RAG：仍只讀取本團 conversation_id 的遊戲記憶
 └─ 封裝 AgentMessage（含目前角色、位置與少量檢索內容）
                    |
                    v
[KeeperSupervisor]
 ├─ PURE_ROLEPLAY ───────────────────────────> Narrator
 └─ GAMEPLAY_ACTION -> Executor -> StateReducer -> Narrator
                    |
                    v
[GroupState + 團務存檔]
 遊戲進度只寫入此團；絕不回寫 Scenario Library
```

### 各 Agent 的資料邊界

| 元件 | 可讀資料 | 不可讀／不可寫資料 | 契約 |
| --- | --- | --- | --- |
| `scenario_library`／system handler | 劇本目錄、manifest、章節、暫存上傳 | 團務對話、玩家 HP/SAN、記憶 | 負責列出 PDF、選定 PDF、清除與比對；不呼叫 LLM。 |
| `ContextBuilder` | 目前選定 PDF 的檢索 chunks、目前角色與位置、該團 Memory RAG | 其他 PDF、未命中的章節、原始 PDF | Scenario RAG 的 key 必須包含劇本 ID、章節 ID 與內容 hash，避免切換 PDF 或章節後命中舊索引。 |
| `Executor` | `AgentMessage`、當前 `GroupState`、Keeper 現有工具 | 劇本庫檔案系統 | 所有 HP/SAN、檢定、戰鬥與物品變動只透過既有 `keeper._execute_tool` 寫回團務狀態。 |
| `Narrator` | 指定章節 RAG 結果、機制 facts、目前劇情 log | 其他章節原文、完整 PDF | 不得以未載入章節的資訊敘事或劇透；沒有檢索到資料時應表達不確定，不自行補寫劇本事實。 |
| `KP Assistant` | 同一團的 `kp_ooc_log` 與目前 PDF 的檢索 Context | 劇本庫寫入權、其他團 OOC 記錄 | 僅此角色可執行 `scenario use`；維持 OOC 隔離，切換 PDF 時保留同一團的主持指示。 |

### 章節切換的快取與狀態規則

1. `scenario_library_id`、`active_chapter_id`、`context_chapter_ids`、`content_hash` 是檢索 Context 的版本鍵。
2. `scenario_rag.get_index()` 的持久化快取 key 必須由上述版本鍵組成；禁止只以 `conversation_id` 或舊文字快取判斷。
3. `/coc scenario use` 只清除 `openai_previous_response_id` 並切換目前 PDF；`log`、Memory RAG、位置、戰鬥、待處理檢定與 `kp_ooc_log` 都必須保留。
4. 玩家角色與所有團務進度保持不變；KP 選擇 PDF 是控制資訊來源的動作，不是重置。
5. 章節載入後的 NPC／地點索引只包含目前章與下一章及其宣告共用資產的條目；避免 Narrator 看見更後面的敵人或結局。
6. PDF 切換必須由 KP 明確指定劇本 ID；同一 PDF 的章節推進只可由 `ChapterProgressor` 在轉場條件滿足時自動進行。

### Agent 整合驗收

1. 選定某一 PDF 後，`ContextBuilder` 的 Scenario RAG 無法檢索其他 PDF 的文字。
2. 視窗從第一章推進到第二章後，RAG 不會回傳第一章以外或第四章以後的快取結果。
3. Executor 的工具呼叫仍能正確寫回目前 `GroupState`，且不修改劇本庫目錄。
4. Narrator 收到的 mechanic facts 與既有 Agentic Keeper 流程一致。
5. KP Assistant 的 OOC 指示在 PDF 或章節推進後仍維持同一團隔離，且不污染其他團。
## 與現有模組的整合

| 模組 | 調整責任 |
| --- | --- |
| `app/pdf_loader.py` | 新增低成本 `extract_preview()`；保留 `extract_text()` 作完整解析。 |
| `app/scenario_library.py`（新增） | 目錄讀寫、manifest、章節切分、相似度、暫存、原子更新、列出、刪除、載入資料。 |
| `app/legacy_commands.py` | PDF 上傳改為「preview → 比對 → 確認／完整解析」；完整解析結果交給 library 儲存層。 |
| `app/commands/handlers/system.py` | 增加 `scenario list`、`scenario use`、`scenario clean`、`scenario reparse`、`scenario cancel` 的命令處理。 |
| `app/commands/router.py` | 將新增的系統子指令導向 system handler。 |
| `app/models.py` | 為 `GroupState` 新增劇本庫 ID 與 pending upload 的可向後相容序列化欄位。 |
| `app/repositories/group_state.py` | 將劇本庫圖片複製／連結到目前群組的既有圖片服務位置。 |

## 測試驗收

至少應涵蓋：

1. 新 PDF 沒有相似項目時建立目錄，且目錄包含 manifest、文字、索引、角色與圖片。
2. 同一 PDF 再上傳時只做預覽比對，完整解析不在確認前執行。
3. `/coc scenario reparse` 會執行完整解析並更新既有劇本，不建立副本。
4. 前頁相似但完整內容不同時，二次比對建立新劇本。
5. `/coc scenario list` 能列出多個項目、其章節，且標示目前使用項目。
6. 多章劇本的 `/coc scenario use <ID>` 未指定章節時會要求選擇；指定章節時只載入該範圍。
7. `/coc scenario use` 僅更新選定章節 Context，保留團務進度與角色，並使 Keeper/RAG 無法讀到未選章節。
9. `/coc scenario clean` 只接受劇本 ID，不刪除目前正在被使用的項目，也不影響其他劇本。
10. 解析失敗、取消與同時上傳時，不損壞既有劇本庫或群組狀態。

## 實作順序

1. 建立 `scenario_library.py`、目錄格式與單元測試。
2. 加入 `extract_preview()` 與相似度／pending 流程；先保留現有完整解析流程不變。
3. 將完整解析輸出改由劇本庫持久化，再回填目前 `GroupState`。
4. 實作 `scenario list`、KP 專用的 `scenario use`、`scenario clean`、`scenario reparse`、`scenario cancel`。
5. 補上圖片載入、群組切換、失敗復原與並行測試。

