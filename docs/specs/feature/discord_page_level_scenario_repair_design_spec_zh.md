# 從 Discord 上傳進行頁面級劇本修復

[English](discord_page_level_scenario_repair_design_spec.md)

狀態：**待實作**（僅設計，尚未實作）。基準：`main_v2` 的 `3fbec39`。

## 1. 問題

目前的劇本匯入有兩條路徑：

1. 上傳 PDF 會解析整份 PDF 並建立新的劇本庫項目，可當成新劇本或更正套用。
2. 上傳 `scenario*.md` 會把整份 Markdown 當成完整的劇本來源，**不會**把選定的頁面補丁進目前載入的 PDF 劇本。

`app.scenario_source_review` 已經有一個安全的管理員流程（`prepare → edit proposal.md → check → publish`）：把工作綁定到不可變的 PDF 來源快照、驗證實體頁碼、回報數值變化、發布衍生劇本而不是覆寫原劇本，並記錄稽核。但它需要檔案系統／CLI 權限，也需要涵蓋整份 PDF 的提案，不適合一般的 Discord 流程：

```text
⚠️ 第 2、4、6、7、8、10、14、16、17 頁有解析品質待核對項目
```

KP 手上有只針對這幾頁、由外部審查過的 Markdown 檔，想直接上傳。

### 期望的使用體驗

像上傳 `role_*.md` 一樣：KP（或任何人）上傳一個檔案，Bot 直接融合。

```text
1. 依照頁面修復範本（docs/references/scenario_page_repair_template.md），
   對照原 PDF，用 ChatGPT 或其他審查者填寫。
2. 把 repair_<name>.md 上傳到對話。
3. Bot 檢查後，只替換目前載入的 PDF 劇本中列出的實體頁，發布新的不可變劇本版本；
   如果該劇本仍是正在進行的那一份，就把進行中的遊戲切換過去，不重置任何東西。
4. 回覆會列出每一頁實際改了什麼，包括每個有變動的數字。
```

沒有匯出指令，也沒有要複製的雜湊：檔案只需要頁碼與修正後的頁面文字。不需要重跑 PDF OCR。

## 2. 目標

### 2.1 功能目標

實作**必須**：

1. 辨識檔名以 `repair_` 開頭的 Markdown 附件。
2. 把該檔案當成**頁面補丁**，絕不當成完整劇本。
3. 套用到對話中已載入、來自 PDF 的劇本，頁數不同時拒絕。
4. 只替換明確列出的 PDF 實體頁，每頁都是完整的修正後文字。
5. 以確定性方式計算每個被替換頁面上有變動的數字，並在回覆中列出。
6. 重用原始 PDF 位元組與頁面影像。
7. 建立新的不可變劇本庫項目，絕不覆寫父劇本。
8. 記錄父子來源關係與稽核。
9. 只清除被明確替換的頁面的解析警告，未被修改的頁面保留警告。
10. 套用到使用中的劇本時，保留進行中的遊戲。
11. 具冪等性：重新上傳同一個檔案不得產生更多版本。
12. 與既有的 `scenario_source_review` CLI 流程相容。

### 2.2 非目標

第 1 版**不得**：

- 接受 diff、行號補丁或搜尋取代；
- 以模糊比對替換文字，或猜測檔案「大概」屬於哪份劇本或哪一頁；
- 就地修改父劇本或改變原始 PDF；
- 透過 repair 檔替換地圖圖形拓撲（歸既有的地圖管線與 `map_*.yaml` 負責）；
- 把自由格式的 Markdown 文件當成 repair；
- 自動改寫翻譯變體；
- 對整份 PDF 重跑 OCR；
- 提供匯出指令，或要求 KP 從 Bot 複製雜湊。

## 3. 要重用的既有架構

`app/scenario_source_review.py`（頁面切分、全頁審查、不可變的衍生發布、稽核）、`app/scenario_library.py`、`app/trusted_scenario_source.py`（`publish_derived`）、`app/services/scenario_lifecycle.py`（`_repair` 就是來源更正對進行中遊戲的語意）、`app/services/scenario_ingestion.py`、`app/commands/handlers/uploads.py` 與 `app/scenario_numbers.py`。新功能不得發明第二個「repair」的意思。

## 4. 架構決定

```text
Discord upload router
        ↓
scenario_repair.handle_repair_upload()       app/services/scenario_repair.py
        ↓
scenario_page_repair.parse_markdown_bytes()  app/scenario_page_repair.py
scenario_page_repair.check()
scenario_page_repair.publish()
        ↓
scenario_lifecycle.activate_repair_version()
```

解析與發布邏輯不放進 `discord_bot.py`。

## 5. 附件路由

在 `app/commands/handlers/uploads.py` 的通用 Markdown 比較路徑之前加入 `repair_*.md`：

```text
PDF → scenario*.md → repair_*.md → map_*.yaml → role_*.txt/.md → generic .txt/.md compare
```

第 1 版每則訊息只能有一個 repair 檔，超過一個就以明確的訊息拒絕。repair 檔絕不能落入 `handle_scenario_compare_upload()`。

## 6. 權限模型

對話中的任何人都可以上傳 `repair_*.md`，和現在上傳 PDF 或 `scenario*.md` 一樣。檢查沿用既有的劇本生命週期政策：

```python
permissions.may_manage_scenario_lifecycle(state, user_id)
```

預設對所有人為真；開啟 `SCENARIO_LIFECYCLE_KP_ONLY` 時只限 KP Assistant，拒絕訊息用 `permissions.kp_only(...)`。開放上傳是安全的，因為 repair 以頁面為界、回報它改動的每個數字、建立新的不可變版本，並且讓父劇本保持不變且可再選用。

`handle_uploads()` 目前拿不到上傳者。在它的簽名加入 `user_id`，並更新 `discord_bot._handle_message()` 中的呼叫端。權威的審查者身分是 Discord 使用者 ID 與顯示名稱、對話、請求 ID 與時間戳，絕不是檔案內的欄位。

## 7. Repair 檔格式

Markdown，恰好包含一個有圍欄的 `json` 區塊（`authoring.parse_markdown()` 的慣例）。任何地方都只有下列鍵有效：

```json
{
  "repair_version": 1,
  "target": { "page_count": 27 },
  "patches": [
    {
      "page": 10,
      "text": "THE BASEMENT\n\nROOM 1: Storage\n...",
      "page_kind": "text",
      "review_note": "Checked against PDF page 10; repaired two-column order and the dice expression."
    }
  ]
}
```

- `repair_version` 是 `1`。
- `target.page_count` 是 PDF 的實體頁數，用來確認這個檔案是針對這份 PDF 製作的。
- `patches` 有 1 到 100 筆，每頁一筆，順序不拘。
- `page` 是 **PDF 實體頁碼**，從 1 開始，絕不是書上印的頁碼。
- `text` 是該頁**完整**的修正後文字，會取代整頁。不得包含實體頁面標記（`library.PAGE_MARKER_RE`），標記由系統擁有。前後空白會被修剪。
- `page_kind` 是 `text`、`map` 或 `image`。
- `review_note` 寫明對照該頁檢查了什麼、修正了什麼，不能是空的。

未知的鍵、重複的頁碼、超出 `1..page_count` 的頁碼與錯誤的型別都會被拒絕。檔案是 UTF-8 或帶 BOM 的 UTF-8，CRLF 會被正規化，大小受 authoring 檔案大小上限限制，不使用 YAML 或其他寬鬆的解析器。格式連同範本一起提供（見「範本與說明」）。

### 7.1 `page_kind`

- `text`：一般的敘述、規則或講義，`text` 不得為空。
- `map`：主要內容是平面圖或示意圖的頁面。轉錄可讀的標籤，不要捏造房間描述；文字量少不算解析失敗，所以這個種類可以清除 `low_text` 警告。地圖圖形本身在這裡永遠不會被編輯。
- `image`：只在該頁真的沒有可讀文字時使用。此時 `text` 必須是空的，而且 PDF 頁面含有原生可讀文字時會被拒絕（既有的 `scenario_source_review.image_only` 規則）。發布的本文是既有的影像佔位文字。

## 8. 全頁替換，無損

不支援局部修改。合併只拼接被選頁面的本文：

```python
spans = locate_page_body_spans(parent_text)      # lossless offsets of each physical page body

for patch in patches:
    splice(parent_text, spans[patch.page - 1], patch.text)   # only the selected spans are re-serialized
```

頁面本文是該頁標記行到下一個標記之間的文字，只去掉恰好一個開頭換行，以及（後面還有標記時）恰好一個 `\n\n` 分隔。不要重用會把每頁本文 strip 再重建所有標記的切分器：既有來源審查流程發布的未修改頁面會刻意保留審查過的前後空白。合併是確定性的、對未被修改的頁面逐位元組保留，也不做模糊比對。

## 9. 綁定到已載入的劇本

repair 套用到對話已載入（`scenario_library_id`）、來自 PDF 的劇本。下列情況會被拒絕：

- 沒有載入劇本，或已載入的劇本沒有 PDF 來源（只有 Markdown 的劇本沒有實體頁面身分）；
- `target.page_count` 與 PDF 頁數不同；
- 某筆補丁的 `page` 超出 PDF。

父劇本的身分（劇本 ID、內容雜湊、PDF SHA）在接受上傳時由伺服器擷取，並在發布前與啟用前再檢查一次，所以中間改變的來源絕不會被套用一半。

## 10. 數值與機制報告

來源修復正是被 OCR 弄壞的機制數值可能進入正本來源的地方，所以每個變動的數字都要被呈現，而不是被信任。對每個被替換的頁面，Bot 計算：

```python
old_counts = scenario_numbers.mechanics_counts(old_page)
new_counts = scenario_numbers.mechanics_counts(new_page)

removed = old_counts - new_counts
added   = new_counts - old_counts
```

並在回覆與稽核中列出，例如 `第 10 頁：移除 1d40 ×1、18 ×1；新增 1d4 ×1`。數字沒變的頁面會明說沒變。不會因為數值變動而拒絕：檔案本來就是審查者的更正，報告讓 KP 能在下一場遊戲前看到 `1D4` 變成了 `1D6`。父劇本仍可選用，所以錯誤的 repair 只要重新選用父劇本就能還原。

`mechanics_counts` 是比 `scenario_numbers.counts` 更嚴格的 token 切分：`counts` 會丟掉單獨的正負號與分隔符（`counts("Bonus +10%") == counts("Bonus -10%")`，`SAN 1/1d6` 與 `SAN 1 1d6` 的 token 計數也相同）。機制 token 會保留直接寫在數字前面的正負號（`+`、`-`、`−`），**包括緊貼在字詞後面的情況**（`STR+10` 與 `STR-10` 是 token `+10` 與 `-10`），並把以 `/`、`-`、`–` 或 `−` 相連的數值運算元合成一個 token（`1/1d6`、`1-3`）。token 內的空格與 tab 不重要。像 `A-10` 這樣帶連字號的標籤會得到 token `-10`；只有那段文字被改動時才有影響，文字相同就不會有變化。現有的 `counts` 不變，其他使用者不受影響。

報告只證明有變動的 token 被看見了，並不證明改得對；外部審查仍是證據來源。

## 11. 驗證階段

任何失敗都會中止整份 repair，什麼都不發布。

- **A，封包：** 檔名、UTF-8、schema 版本、精確的鍵、補丁數量。
- **B，目標：** 已載入來自 PDF 的劇本，且 `page_count` 相符。
- **C，頁面：** 頁碼範圍、唯一性、沒有注入頁面標記。
- **D，內容：** `review_note` 不為空；頁面種類對 `text` 的要求；`image` 對原生文字的規則。
- **E，候選：** 只拼接列出的頁面，建出候選。
- **F，不變條件：** 實體標記仍是 `1..N` 且各出現一次；未被修改的頁面本文逐位元組不變（指第 8 節的原始本文）；被替換的頁數等於補丁數；候選與父劇本不同；候選摘要是確定性的。

## 12. 發布

絕不更新父劇本的目錄。透過 `trusted_scenario_source.publish_derived`，使用 `scenario_source_review.publish()` 的不可變衍生來源模式。

- 衍生 ID：`<parent-prefix>-repair-<candidate_digest[:16]>`。已存在且稽核摘要相符時，當成冪等的成功回傳；已存在但內容不同則失敗。
- `candidate_digest` 涵蓋父劇本身分、正規化後的補丁（依頁碼排序）與候選文字。
- manifest 新增 `source_repair`（版本、父劇本 ID 與內容雜湊、候選摘要、被修復的頁面、審查者使用者 ID 與顯示名稱、上傳的檔名、時間）。
- 稽核存在既有的稽核位置（`source_review.json`），帶 `kind: page_repair`：父劇本與候選的摘要、前後內容雜湊、PDF SHA、審查者，以及每頁的前後 SHA-256、審查備註、頁面種類與移除／新增的數值 token。完整的前後文字是選用的；頁面雜湊加上不可變的父子劇本就能還原差異。

## 13. 解析品質更新

不要用單一的乾淨結果取代解析品質歷史。未被修改的頁面保留既有的品質列與警告。被修復的頁面得到 `method = "operator-reviewed-discord"`、`warnings = []`、新本文的 `selected_sha256` 與它的 `page_kind`。最上層變成 `version: "source-repair-v1"`，帶 `parent_parse_quality_version`、`repaired_pages`，且 `review_pages` 會扣掉被修復的頁面。從 `2、4、6、7、8` 修復第 2、4 頁之後，載入訊息只列出 `6、7、8`。

## 14. 衍生產物

`scenario_source_review.publish()` 會讓 `indexes`、`pregens` 與 `scene_maps` 失效，因為它們是從舊文字萃取的。repair 子劇本也一樣：不複製父劇本的 NPC 與地點索引、預製角色萃取或場景地圖推論。

對進行中的遊戲，連續性很重要：保留目前的玩家角色、已認領的預製角色、HP、SAN、幸運、背包、時間線與房間位置，並保留執行階段的 `scene_maps`（PDF 影像沒有改變）。把衍生的來源產物標記為需要重建，並依新的來源雜湊非同步重建：NPC 與地點索引、RAG 預熱、必要時的預製角色候選。只有在重建完成時，被修復的劇本仍是使用中的來源，重建結果才可以取代衍生產物；針對舊雜湊的緩慢重建絕不能覆寫較新的 repair。

## 15. 地圖頁

對 `page_kind: map`，Bot 可以記錄文字量少是預期的、可見的標籤已審查，並清除該頁的文字品質警告。它絕不改變房間圖、鄰接關係、入口房間、祕密連線、`visual_basis` 或地圖座標。地圖拓撲有誤時，使用既有的地圖修復路徑。

## 16. 套用到進行中的遊戲

新增生命週期 API，而不是濫用 `pending_pdf_upload`：

```python
async def activate_repair_version(
    conversation_id: str,
    parent_scenario_id: str,
    repaired_scenario_id: str,
    *,
    authorized: Callable[[GroupState], bool],
    expected_revision: int,
    expected_timeline: str,
) -> LifecycleResult:
```

在對話鎖之下：操作者仍有權限；使用中的 `scenario_library_id` 等於父劇本；使用中的來源雜湊仍等於父劇本的；`timeline_id` 沒有改變；`state_revision` 符合 repair 交易的政策；沒有其他待處理的劇本提交或預製角色幸運決定；而且 `resource_bridge.guard_replacement(state)` 允許替換。以更正語意套用。**不要**呼叫 `_new_upload()`、重設 `game_started`、清除時間線、清掉角色、重設房間或清除戰役歷史。`scenario_library_id` 與 `scenario_text` 在同一個交易中改變，絕不能一個先、一個後。

## 17. 兩階段並行

解析、雜湊、頁面檢查與發布不得持有對話鎖。

```text
LOCK: authorize, capture parent id/hash and revision/timeline, check no conflicting pending operation   UNLOCK
parse, validate, build candidate, publish the immutable derived scenario
LOCK: re-check authorization, parent id/hash, timeline, replacement guard; activate if still valid      UNLOCK
```

如果發布之後狀態已改變，新版本留在劇本庫，但不套用：

```text
新版已建立，但遊戲狀態在修復期間已變更，因此沒有自動套用。
```

已有效發布的來源絕不會因為啟用變成過時就被回滾或刪除。

## 18. 結果訊息

每個拒絕訊息都說明下一步怎麼做，檔案無法被讀成 repair 時要指出範本。

```text
✅ 劇本來源修復完成

《The Haunting Scenario trimmed》
修復頁面：2、4、6、7、8、10、14、16、17
新版來源：<new scenario id>

第 2 頁：數值沒有變動
第 10 頁：移除 1d40 ×1、18 ×1；新增 1d4 ×1
…

已只替換指定頁面，其餘頁面保持不變。
目前遊戲已切換到修正版，角色、進度與目前位置沒有重置。
舊版仍在劇本庫，必要時可切回。
```

已發布但未套用：版本 ID 加上第 17 節的狀態過時通知。冪等的重新上傳：`這份修復已經套用，沒有重複建立版本。` 頁數不符、沒有載入來自 PDF 的劇本、頁碼無效、含有可讀文字的 `image` 頁，或無法讀取的檔案，各自得到一則明確的訊息，並且什麼都不套用。

## 19. 自由格式的 repair Markdown

**不要**自動匯入像 `# PDF page 2 ... # PDF page 4 ...` 這類自由格式的檔案。它們是有用的已審查素材，但沒有頁面種類、審查備註或頁數檢查。範本就是轉換路徑：把審查過的頁面文字複製進去。

## 20. 重構 `scenario_source_review`

避免兩份有細微差異的驗證器。抽出可重用的公開輔助函式：`split_source_pages`、`published_page_text`、`candidate_text` 與 `validate_evidence`（審查 CLI 保留它的證據矩形；頁面 repair 不使用它們）。兩處刻意的差異：頁面 repair 以無損方式切分父劇本（第 8 節），並用機制 token 回報數字（第 10 節）。會 strip 的切分器與 `scenario_numbers.counts` 保留給既有的呼叫者。

## 21. 檔案

新增：`app/scenario_page_repair.py`、`app/services/scenario_repair.py`、`tests/test_scenario_page_repair.py`、`tests/test_discord_scenario_repair_upload.py`、`docs/references/scenario_page_repair_template(.md|_zh.md)`，以及這份規格。修改：`app/commands/handlers/uploads.py`、`app/discord_bot.py`、`app/services/scenario_lifecycle.py`、`app/scenario_source_review.py`、`app/scenario_numbers.py`、`app/help_registry.py`、`app/services/scenario_ingestion.py` 中的載入確認，以及參考與指南文件。

## 22. API

```python
@dataclass(frozen=True)
class RepairTarget: page_count: int

@dataclass(frozen=True)
class PageRepair:
    page: int
    text: str
    page_kind: Literal["text", "map", "image"]
    review_note: str

@dataclass(frozen=True)
class RepairProposal:
    version: int
    target: RepairTarget
    patches: tuple[PageRepair, ...]

@dataclass(frozen=True)
class RepairCheck:
    ready: bool
    scenario_id: str
    candidate_digest: str
    candidate_text: str
    repaired_pages: tuple[int, ...]
    issues: tuple[RepairIssue, ...]
    changes: tuple[dict, ...]      # per page: hashes, kind, note, removed and added numeric tokens

parse_markdown_bytes(data: bytes) -> RepairProposal
check(proposal: RepairProposal, scenario_id: str) -> RepairCheck
publish(check: RepairCheck, *, reviewer_user_id, reviewer_display_name, uploaded_filename) -> str
```

## 23. 原子性與冪等性

來源發布全有或全無；任何一頁失敗就什麼都不發布。啟用在狀態交易之下全有或全無；狀態絕不指向部分的候選。衍生 ID 由候選摘要決定。對同一個父劇本重新上傳同一個檔案，會回傳同一個 ID 並確保啟用正確；衍生版本已在使用中時會明說；對較新的父劇本上傳時，頁數與頁面檢查與任何檔案相同，而頁面文字已與 repair 相同時，會被回報為沒有變動。

## 24. 翻譯變體與 RAG

來源修復會改變正本的來源身分。綁定到舊來源的翻譯或範本變體留在舊劇本之下，對修復後的劇本不可用，使用中的來源有這種變體時要通知 KP；之後要遷移翻譯就用 `scenario_source_review.rebind()`。絕不依紀錄 ID 複製翻譯紀錄。任何以父雜湊為鍵的 RAG 快取都已過時，絕不能當成子劇本的權威；針對新雜湊排程重建，期間用新的正本文字做安全的備援檢索，不要因為重建而阻擋遊戲。

## 25. 效能

9 頁的補丁不會重跑 PaddleOCR、Tesseract、PyMuPDF4LLM 擷取或 AI PDF 修復。上傳延遲來自解析、雜湊、頁面檢查、數值報告、衍生發布與狀態提交，通常是數秒，加上選用的非同步索引與 RAG 重建。

## 26. 安全／信任邊界

把檔案當成不受信任的輸入：拒絕路徑穿越與檔案路徑、內嵌的頁面標記、不支援的鍵或版本、過大的內容、重複的頁碼、無效的 UTF-8 與錯誤的頁數。不執行 Markdown、不參照其他本機檔案、不使用檔案提供的審查者身分，也絕不讓 repair 修改父劇本。

## 27. 測試

- **解析：** 有效的單頁 repair；接受 BOM；未知的最上層、target 或補丁鍵；重複、0 或超出範圍的頁碼；注入標記；無效的 `page_kind`；缺少審查備註；沒填的範本會被拒絕。
- **綁定：** 沒有載入劇本；只有 Markdown 的劇本；頁數不符。
- **數值報告：** `1D40 → 1D4` 的變動會以移除與新增的 token 列出；`+10% → -10%`、`SAN 1/1d6 → SAN 1 1d6` 與 `STR+10 → STR-10` 都會被列出；沒變的文字什麼都不報；沒改動的帶連字號標籤不產生變動；一頁的報告不影響另一頁。
- **合併：** 只有列出的頁面改變；未被修改的頁面保留完全相同的位元組（含空白）；標記維持順序且各出現一次；補丁順序不影響結果；沒有任何變化的 repair 被拒絕；同一份 repair 具冪等性。
- **地圖與影像：** 有標籤的地圖頁被接受並清除低文字量警告；地圖 repair 不改變場景地圖圖形；存在原生文字時 `image` 被拒絕，沒有時被接受。
- **Discord：** `repair_*.md` 路由到 repair 處理器、絕不到比較處理器；預設任何使用者都能上傳，`SCENARIO_LIFECYCLE_KP_ONLY` 會限制為 KP Assistant；拒絕超過一個 repair 附件；待處理的來源替換遵循准入政策；發布之後狀態已改變時會發布但不啟用。
- **生命週期：** 啟用保留時間線、`game_started`、已認領的玩家角色、HP／SAN／幸運／背包與房間位置；絕不呼叫 `_new_upload()`；新來源只在完整交易之後才成為使用中；舊劇本仍可讀取。
- **解析品質：** 被修復頁面的警告清除、未被修改頁面的保留、載入訊息只列出剩下的頁面。
- **產物：** 父劇本的索引不會被複製；重建以子劇本的雜湊為鍵；過時的重建不能覆寫較新的 repair。

## 28. 驗收測試：The Haunting

基準：`The_Haunting_Scenario_trimmed`，27 個 PDF 實體頁，警告頁為 2、4、6、7、8、10、14、16、17。審查者對照 PDF 為這九頁填寫範本，檔案以 `repair_the-haunting-trimmed_01.md` 上傳。預期：頁數相符；27 頁的候選中只有那九頁不同；建立新的不可變劇本 ID 且原劇本不變；回覆列出每頁的數值變動；那九個警告頁不再出現在解析品質警告；時間線、角色、狀態與房間位置不變；新的來源雜湊取得新的 RAG 與索引建置；再次上傳同一個檔案不會建立另一個版本。

## 29. 範本與說明

新的上傳格式要連同文件一起，與它所描述的行為放在同一個 pull request：

- **範本。** `docs/references/scenario_page_repair_template(.md|_zh.md)` 是撰寫範本，在 `docs/README.md` 與 `docs/README_zh.md` 的「References」下連結，和 `role_card_template` 一樣。它寫明規則並附 JSON 骨架。測試讓它的鍵與解析器接受的鍵完全一致，所以不會漂移，也檢查沒填的範本會被拒絕。
- **說明。** 對話說明（`app/help_registry.py`，由 `help_service` 與 Help UI 呈現）新增上傳 `repair_*.md` 的項目，已載入劇本時顯示：任何人都能上傳、檔案的內容，以及回覆會列出數值變動。`docs/references/player_command_reference(.md|_zh.md)` 與 `docs/guides/gameplay(.md|_zh.md)` 加上對應的文字，列出解析品質警告頁的載入確認訊息也要指向 repair 流程與範本。
- **訊息。** 每個拒絕訊息都說明下一步怎麼做，檔案無法被讀取時要指出範本。

## 交付方式

下列每個實作階段各自是一個 pull request、各自審查；在第 4 階段完成之前，狀態維持 `partial`。既有的 `scenario_source_review.publish` 需要涵蓋每一頁的提案，並寫入完全乾淨的解析品質紀錄，所以第 1 階段要抽出共用的頁面邏輯，並新增部分頁面的發布路徑，而不是原封不動地重用 `publish`。

## 30. 實作順序

1. **確定性核心。** 從 `scenario_source_review` 抽出共用的頁面輔助函式；實作解析器、嚴格 schema、無損合併、數值報告、候選摘要與不可變的部分頁面發布，附單元測試與範本。
2. **Discord 上傳。** `repair_*.md` 路由、`handle_uploads` 的 `user_id`、劇本生命週期權限檢查、repair 服務、結果與拒絕訊息、路由測試、說明項目與指南文字。
3. **使用中遊戲的更正。** `scenario_lifecycle.activate_repair_version()`、更正語意、過時修訂／時間線檢查、保留地圖位置與玩家狀態、整合測試。
4. **衍生產物。** 針對新的來源雜湊排程索引與 RAG 重建、以雜湊檢查保護重建的提交，並在修復後回報狀態。

## 31. 合併條件

- repair 不能套用到頁數不同的劇本、只有 Markdown 的劇本，或沒有載入劇本時；
- 全頁替換是確定性的，未被修改的頁面保留完全相同的位元組；
- 每個有變動的數字都會出現在回覆與稽核中；
- 父劇本不可變，部分的 repair 不能發布；
- 使用中遊戲的更正不會重設戰役狀態；
- 未被修改頁面的警告仍然可見；
- `repair_*.md` 絕不會路由到通用比較；
- 上傳檢查遵循劇本生命週期政策；
- 冪等的重新上傳有測試證明；
- 頁面合併本身不需要 OCR 或 API 呼叫；
- 既有的 `scenario_source_review` 測試維持綠燈，既有的 PDF 與 Markdown 上傳行為不變；
- 範本、說明項目與指南文字隨它們所描述的行為一起提供。

## 32. 明確的設計選擇

- **為什麼不接受任意自由格式的 repair 檔？** 它沒有頁面種類、審查備註或頁數檢查。
- **為什麼綁定已載入的劇本，而不是雜湊？** 檔案由外部審查者撰寫，他有 PDF 與範本，但沒有 Bot 內部的雜湊。已載入的劇本、頁數與回報的數值變動，提供了真正重要的安全性，而不需要 KP 複製任何東西；不可變的父劇本讓錯誤的 repair 可以還原。
- **為什麼替換整頁？** 這提供確定性的來源紀錄，並避免模糊對齊。
- **為什麼回報數字而不是拒絕？** 來源修復本來就可以修數字，更正依定義就是數值變動；不該發生的是沒人注意到的變動。
- **為什麼建立子劇本而不是覆寫？** repair 必須可逆、可稽核，並且對既有戰役安全。
- **為什麼保留遊戲狀態？** 這修的是來源，不是新劇本。

## 33. 預期的 KP 體驗

```text
Bot:
⚠️ 第 2、4、6、7、8、10、14、16、17 頁需要核對。

Keeper:
（用 ChatGPT 依原 PDF 填寫 docs/references/scenario_page_repair_template.md）
[uploads repair_the-haunting_01.md]

Bot:
✅ 修復完成。
已核對並替換第 2、4、6、7、8、10、14、16、17 頁。
第 10 頁：移除 1d40 ×1、18 ×1；新增 1d4 ×1
建立新版劇本來源：the-haunting-...-repair-xxxxxxxxxxxxxxxx
目前遊戲已使用修正版；角色、進度與位置未重置。
```
