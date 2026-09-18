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
pending_scenario_upload: dict | None = None
```

`scenario_library_id` 指向目前載入的劇本庫項目。現有 `scenario_text`、`scene_maps`、`pregens` 等欄位維持為當前團的執行快照，以避免一次大規模重構 Keeper、RAG、地圖與舊資料遷移；`/coc scenario use` 會從劇本庫複製資料到這些既有欄位。


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

`/coc scenario list` 除了劇本 ID 外，必須顯示每個劇本的章節 ID 與名稱。`/coc scenario use <劇本ID> [章節ID]` 未指定章節時：只有一章則自動載入；有多章則列出選項，不應猜測要玩的章節。

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

`chapter-01` 是 `/coc scenario use` 可遊玩的預設章節；手卡與預製角色是附錄資產，仍會與主冒險關聯，但不能單獨被選為遊戲起點。書籤扁平時，章節切分器需識別 `handout`、`character sheet`、`appendix`、`credits` 等資產類標題，把它們從主劇情文字範圍中排除；其他冒險書籤保留為主章節的 `sections`。

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

附錄使用 `kind: "asset"`；`/coc scenario list` 會顯示它們，但 `/coc scenario use` 只能接受 `kind: "playable"` 的章節 ID。
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
        [/coc scenario use <劇本ID> [章節ID]]
                       |
                       v
 [載入該章節到 GroupState，開始一個新團]
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
| `/coc scenario list` | 列出劇本庫所有項目：ID、標題、更新時間、頁數、章節 ID／名稱，以及目前是否被此群組使用。 |
| `/coc scenario use <劇本ID> [章節ID]` | 載入指定劇本及其章節到目前群組；多章劇本未指定章節時列出可選章節。重設劇情進度、地圖位置、戰鬥、待處理檢定與 OpenAI 對話鏈；保留群組既有玩家角色與 persona。 |
| `/coc scenario clean <劇本ID>` | 從劇本庫永久移除指定目錄及其圖片、原始 PDF 與解析結果。若該劇本正被任何活躍群組使用，拒絕清除並提示先 `/coc scenario use` 切換；不使用模糊標題刪除。 |
| `/coc scenario reparse` | 對本次「相似 PDF」暫存檔執行完整重新解析與二次比對。 |
| `/coc scenario cancel` | 放棄本次待確認的上傳。 |

`/coc scenario list` 的輸出需以 ID 為操作依據，避免同名劇本、中文／英文別名造成誤用或誤刪。

## `/coc scenario use` 的狀態契約

載入是「開始以某劇本進行新的一團」，不是回復某一團的舊存檔。它必須：

- 從劇本目錄恢復指定章節的文字、NPC／地點索引、地圖、預製角色與頁面圖片。
- 清除 `log`、`campaign_summary`、`openai_previous_response_id`、戰鬥、地圖座標、待檢定、KP OOC 歷史、`game_started`。
- 設定 `active=True` 與 `scenario_library_id`。
- 保留該群已建立的玩家角色及 `keeper_persona`、`era`。
- 不修改劇本庫內容；所有遊戲中的狀態只寫入 `GroupState`／既有資料庫。

這能讓劇本庫保持唯讀、可重用，而團務進度仍與聊天室綁定。

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
7. `/coc scenario use` 正確重設團務進度、保留玩家角色與 persona、恢復劇本資料及圖片。
8. `/coc scenario clean` 只接受劇本 ID，不刪除目前正在被使用的項目，也不影響其他劇本。
9. 解析失敗、取消與同時上傳時，不損壞既有劇本庫或群組狀態。

## 實作順序

1. 建立 `scenario_library.py`、目錄格式與單元測試。
2. 加入 `extract_preview()` 與相似度／pending 流程；先保留現有完整解析流程不變。
3. 將完整解析輸出改由劇本庫持久化，再回填目前 `GroupState`。
4. 實作 `list`、`use`、`clean`、`pdf reparse`、`pdf cancel`。
5. 補上圖片載入、群組切換、失敗復原與並行測試。

