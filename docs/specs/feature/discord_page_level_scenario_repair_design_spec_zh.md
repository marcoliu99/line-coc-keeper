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
4. 對話裡的回覆只說哪些頁被替換；KP 會以私訊收到每一頁實際改了什麼，包括每個有變動的數字（詳細內容絕不放進對話，見第 10 節）。
```

沒有匯出指令，沒有要複製的雜湊，也沒有要計算的東西。範本寫明每個欄位：劇本標題與頁數（兩者都在 Bot 的載入訊息裡），以及每個被修復的頁面的實體頁碼、完整的修正後文字、頁面種類（`text`、`map` 或 `image`）與簡短的審查備註。其他一切，包括雜湊與數值變動，都由 Bot 算出來。不需要重跑 PDF OCR。

## 2. 目標

### 2.1 功能目標

實作**必須**：

1. 辨識檔名以 `repair_` 開頭的 Markdown 附件。
2. 把該檔案當成**頁面補丁**，絕不當成完整劇本。
3. 套用到對話中已載入、來自 PDF 的劇本，頁數不同時拒絕。
4. 只替換明確列出的 PDF 實體頁，每頁都是完整的修正後文字。
5. 以確定性方式計算每個被替換頁面上有變動的數字，私下回報給 KP 並記入稽核，絕不放在對話的回覆裡。
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

`handle_uploads()` 目前拿不到上傳者。在它的簽名加入 `user_id` 與上傳者的顯示名稱（`message.author.display_name`，由傳輸層解析），並更新 `discord_bot._handle_message()` 中的呼叫端；服務絕不回頭去問 Discord，所以稽核資料絕不會是捏造的。權威的審查者身分是 Discord 使用者 ID 與顯示名稱、對話、請求 ID 與時間戳，絕不是檔案內的欄位。

**私下送出的埠。** 數值報告需要 `delivery.send_dm()`，它在 `app/discord_transport/delivery.py`。服務不匯入它：`discord_bot` 用新的原始操作 `delivery.send_dm_message()` 建出 `send_private(user_id: str, text: str) -> Awaitable[None]` 回呼；該操作只送出恰好一則訊息：不做顯示別名展開、不切塊，文字超過一則 Discord 訊息就丟出例外（所以一頁絕不會變成多次送出，也不會被 `_chunk_text()` 的十塊上限靜默截斷）。報告引用的是劇本文字而不是角色顯示名，所以略過 `shown()` 沒有損失；日後若需要轉換，必須在分頁**之前**執行。回呼由該操作建出，而不是 `send_dm`，經由路由器與 `handle_uploads()` 傳進 `services/scenario_repair`，做法與既有指令處理器取得送出回呼的方式相同。服務把這個回呼的失敗交給送達狀態邏輯（第 10 節），不做其他事。測試注入會記錄訊息或丟出例外的假回呼。

**載入確認顯示頁數。** `target.page_count` 被承諾會顯示在 Bot 的載入訊息裡，但 `_pdf_upload_confirmation_text()` 目前只印標題與字數。它新增 `page_count` 參數（儲存文字中的實體頁面標記數），並在標題旁印出 `共 N 頁（實體頁數）`；立即與延後兩條上傳路徑都傳入。`/coc scenario use <id>` 的確認訊息（`app/commands/handlers/system.py`）對 PDF 衍生的條目也顯示同樣的 `共 N 頁（實體頁數）`，取自條目儲存的頁數，因為從劇本庫開始的 KP 同樣必須能填 `target.page_count`。測試為 27 頁的文字建立確認訊息並檢查 `27` 出現，另一個測試選用一個劇本庫條目並檢查同一行，範本的說明也指向那一行。

## 7. Repair 檔格式

Markdown，恰好包含一個有圍欄的 `json` 區塊（`authoring.parse_markdown()` 的慣例）。任何地方都只有下列鍵有效：

```json
{
  "repair_version": 1,
  "target": { "title": "The Haunting Scenario trimmed", "page_count": 27 },
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
- `target.title` 是已載入劇本的標題，照載入訊息顯示的寫（`已載入劇本《…》`），`target.page_count` 是 PDF 的實體頁數。兩者合起來用來確認這個檔案是針對這份 PDF 製作的：碰巧頁數相同的另一份劇本會因為標題不符而被拒絕。**已知限制，專案負責人已接受：**標題與頁數都相同的兩個 PDF 劇本（例如同一模組的兩個版本），無法靠這兩個欄位區分，所以為其中一個版本準備的 repair 會被另一個版本接受。檔案刻意不帶來源指紋：審查者依據的是 PDF 與載入訊息，兩者都不顯示雜湊。風險受到設計既有機制限制：數值報告私下送給 KP，顯示每個被修復頁面改了什麼；稽核記錄 PDF SHA 與頁面雜湊；重疊提示會指出先前的 repair；錯誤的 repair 可再上傳修正後的檔案更正。
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
spans = locate_page_body_spans(parent_text)      # lossless offsets of each physical page body, all taken from the ORIGINAL text

pieces, cursor = [], 0
for patch in sorted(patches, key=lambda p: p.page):          # ascending source offsets
    start, end = spans[patch.page - 1]
    pieces += [parent_text[cursor:start], published_body(patch)]   # untouched text up to the span, then the replacement
    cursor = end
pieces.append(parent_text[cursor:])
candidate = "".join(pieces)                                  # one pass: no offset is ever recomputed or invalidated
```

`published_body(patch)` 是實際寫進頁面的文字：`text` 與 `map` 頁用 `patch.text`；`image` 頁（`text` 是空的）則用公開的 `scenario_source_review.published_page_text()` 產生的既有圖片占位文字（`[SOURCE_IMAGE page_N.png: reviewed image-only page]`），絕不是空字串。候選、摘要、稽核雜湊與數值報告都使用這個發布後的本文。所有偏移量都來自原始文字，結果是從左到右一次走過「被複製的間隔」與「替換內容」組出來的，所以比原頁面更長或更短的替換都不會讓後面的區段位移，檔案裡補丁的順序也不影響結果（先依頁碼排序）。不允許「在原文上就地修改，卻繼續使用只算過一次的偏移量」。測試會在打亂的檔案順序下，對三個不相鄰的頁面做更長、更短與以空行補齊的替換，並與獨立組出的預期文字比對；另一個案例修補 `image` 頁，斷言發布的本文是占位文字而不是空白。

頁面本文是該頁標記行到下一個標記之間的文字，只去掉恰好一個開頭換行，以及（後面還有標記時）恰好一個 `\n\n` 分隔。不要重用會把每頁本文 strip 再重建所有標記的切分器：既有來源審查流程發布的未修改頁面會刻意保留審查過的前後空白。合併是確定性的、對未被修改的頁面逐位元組保留，也不做模糊比對。

## 9. 綁定到已載入的劇本

repair 套用到對話已載入（`scenario_library_id`）、來自 PDF 的劇本。下列情況會被拒絕：

- 沒有載入劇本，或已載入的劇本沒有 PDF 來源（只有 Markdown 的劇本沒有實體頁面身分）；
- `target.title` 與已載入劇本的標題不符（與載入訊息顯示的完全一致，只容許大小寫不同、連續空白折成一個空格，以及劇本庫替衍生版本加上的後綴，例如 `[page repaired]` 與 `[source reviewed]`，所以 repair 檔對修復後的子劇本也仍然有效；標點是有意義的，`Scenario: Alpha` 與 `Scenario Alpha` 是不同的標題，不做任何模糊比對）；
- `target.page_count` 與 PDF 頁數不同；
- 某筆補丁的 `page` 超出 PDF。

父劇本的身分（劇本 ID、內容雜湊、PDF SHA）在接受上傳時由伺服器擷取，並在發布前與啟用前再檢查一次，所以中間改變的來源絕不會被套用一半。

### 9.1 與先前 repair 的重疊

檔案沒有基準雜湊，所以在較新的 repair 改過同一頁之後，舊的 repair 檔還是可以再上傳：repair A 然後 repair B 都改了第 10 頁，在 B 已載入時上傳 A，會把 A 的第 10 頁放回去。對已修復頁面的新更正，看起來和過時的檔案一模一樣，所以檢查不拒絕，而是讓覆蓋被看見。Bot 會沿著已載入劇本的世系，走每個衍生子劇本都有的 `source_review.parent_scenario_id` 一路往上（不論 repair 或完整審查，所以夾在兩個 repair 之間的完整審查節點會被穿過，不會讓走訪中斷），只在自己的稽核類型為 `page_repair` 且 `source_repair.scenario_id` 等於自己 ID 的節點上讀取 `source_repair.pages`，當被補丁的頁面在這個世系中被先前的 repair 改過時，回覆會指出頁碼與先前的 repair：`第 10 頁先前已被另一份修復改過（<scenario id>），這次的內容覆蓋了它`，稽核也記錄同樣的內容。把先前的文字放回去是帶著同樣提示的刻意動作：再上傳先前的檔案，上面的冪等檢查會辨識出已經套用的檔案。

## 10. 數值與機制報告

來源修復正是被 OCR 弄壞的機制數值可能進入正本來源的地方，所以每個變動的數字都要被呈現，而不是被信任。對每個被替換的頁面，Bot 計算：

```python
old = scenario_numbers.mechanics_contexts(old_page)   # ordered list of (context, token)
new = scenario_numbers.mechanics_contexts(new_page)

removed, added = ordered_diff(old, new)               # difflib opcodes over the two sequences
```

並在稽核與**私下報告**中列出，例如 `第 10 頁：移除 damage 1d40、18；新增 damage 1d4`。數字與語境都沒變的頁面會明說沒變。

報告會把舊來源回送出去：被移除的 token 連同語境就是原頁面的數字與標籤。因為任何人都能上傳（第 6 節），如果放在對話回覆裡，玩家只要送出結構有效、內容任意的補丁，就能讀回只給 KP 看的數值，例如怪物 HP、SAN 損失與傷害骰。所以詳細報告只用私訊傳給 KP Assistant，每頁一次 `send_private` 呼叫（第 5 節那個原始的單則訊息操作，絕不是 `send_dm`），對話裡的回覆只是不含敏感內容的確認：被修復的頁碼、新版本 ID、其中幾頁有數值變動，以及 KP 已收到詳細內容。沒有登記 KP Assistant 時，詳細內容只留在稽核裡，回覆會這樣說明。不能假設一定送得到：KP 關閉私訊時 `send_private` 回呼會丟出例外。所以報告先連同送達狀態 `pending` 寫進稽核，再傳送；repair 本身絕不會因為私訊失敗而被擋下或回滾，但回覆會說私下報告沒有送達，請 KP 開啟私訊後再上傳同一個檔案。上傳同一個檔案會走到「已套用」的回答，而它會先讀「`patches_digest` 與檔案相同的那一筆世系」的稽核（D2 讀目前載入子劇本自己的稽核，F 讀較早祖先的稽核）：尚未 `delivered` 的報告會先保留、重新傳送並標成已送達，之後回覆才說 repair 已經套用。稽核檔屬於 `trusted_scenario_source`，所以重送透過第 1 階段在那裡新增的五個公開函式進行，絕不直接碰 `source_review.json`：`read_audit(scenario_id)`、`reserve_report(scenario_id, patches_digest, token, lease_seconds, recipient_id, pages_total, pages_digest)`、`advance_report(scenario_id, patches_digest, token, pages_sent, recipient_id, pages_digest)`、`mark_report_delivered(scenario_id, patches_digest, token, recipient_id, pages_digest)` 與 `release_report(scenario_id, patches_digest, token)`。**報告分頁傳送，絕不當成一則私訊。** `delivery.send_dm()` 用 `_chunk_text()` 切分文字，最多只保留十段 1,900 字元的區塊，其餘靜默丟棄，所以很長的報告會被截斷卻看起來已送達。因此服務自己把報告切成每頁最多 1,800 字元（每頁有編號標頭 `(i/N)`，在換行處切分，絕不切在 token 中間）。每一行報告都有上限，讓這件事一定做得到：絕不截斷任何 token 或上下文單字，因為 KP 必須分得出兩個很長的值：任何超過 1,700 字元的行都在該長度硬換行，每次換行處標上 `↩`，各段再像其他行一樣分頁，因此任何報告都不會變成送不出去而永遠停在 `pending`，每頁用自己的 `send_private` 呼叫送出，所以一定只佔一個區塊；頁數沒有上限。送出每一頁之前，送出者呼叫 `advance_report(..., pages_sent)`，帶入已送出的頁數。它在鎖之下一次完成：檢查呼叫者的 token 仍持有保留、**續約租約**（新到期時間 = 現在 + `lease_seconds`）並記下頁數；token 若已遺失就回傳 false，送出者在送出任何東西之前就停止。`lease_seconds` 至少是 Discord 請求逾時的兩倍，所以單次送出不會比它開始時的租約活得更久，長報告也會逐頁維持保留，而不是固定時間後就能被同時的重新上傳保留。**每次送出成功之後**會再用加一的頁數呼叫同一個函式（這第二次呼叫也會續約），所以最後一頁送出之後、`mark_report_delivered` 執行之前，記下的頁數就等於總頁數；送出前的呼叫只做續約與確認持有者。這個呼叫因此記下已送出的頁數，所以失敗或當機之後，下一次保留會從第一個未送出的頁面繼續，而不是重送整份報告，且只有 `pages_sent` 等於總頁數時才設為 `delivered`。送達狀態也會存下它所計數的已排版頁面集合：`pages_total` 與 `pages_digest`（依序對排版後各頁取的 SHA-256），由 `reserve_report` 提供。存下的 `pages_digest` 與現在排版出的不同時（兩次嘗試之間部署改變了換行或分頁），保留會把 `pages_sent` 重設為 0 並存下新的總數與摘要，所以存下的偏移量絕不會套用到切法不同的頁面上。因此只有在分頁沒有改變時送達才是恰好一次；重新分頁之後，新的頁面集合從第 1 頁完整送出，KP 可能看到舊頁面集合已經送過的內容（這是接受的重複，第一行會標示 `（重新分頁後重送）`），因為舊的排版沒有持久化；`advance_report` 與 `mark_report_delivered` 會帶入摘要，不相符就失敗，而 `mark_report_delivered` 只有在 `pages_sent` 等於存下的 `pages_total` 時才成功。送達狀態也會存下它正在送達的 KP 的 `recipient_id`，因為進度只對單一收件者有意義：`reserve_report` 會收到目前 KP Assistant 的 ID，若存下的收件者不同（兩次嘗試之間 `/coc kp transfer` 讓另一個使用者成為 KP），就把 `pages_sent` 重設為 0 並記下新收件者，所以新的 KP 收到的是完整報告而不是只有尾段；`advance_report` 與 `mark_report_delivered` 也帶入目前的收件者 ID，不相符時回傳 false（兩者都在持有對話鎖、且於同一次持鎖中重新讀取 KP 的情況下呼叫，所以同樣取對話鎖的 `/coc kp transfer` 不可能在重新讀取與稽核轉換之間提交；最終標記在不相符時還會把存下的狀態重設為屬於新收件者的 `pending`、`pages_sent` 為 0，所以在最後一次送出與最終標記之間發生 `/coc kp transfer`，不會留下標成已送達給前任 KP 的報告），所以多頁送出途中發生移交時，舊的送出者在下一頁之前就停止，新的保留從第 1 頁重新開始。送達狀態是 `pending`、`sending`（帶有保留者的 `token` 與租約到期時間）或 `delivered`。`reserve_report` 在 `scenario_library.publication_lock()` 之下，把 `pending`（或租約已到期的 `sending`）改成屬於呼叫者 token 的 `sending`；別的呼叫者握有未到期的租約時回傳 false，而且**只有保留成功的呼叫者才會送出報告**，所以兩個同時的重新上傳只會送出一次，輸的那一方回覆「正在送達中」。一次 `send_private` 呼叫成功後，送出者呼叫 `mark_report_delivered`（只限自己的 token）；送出失敗就呼叫 `release_report`，讓下一次重新上傳可以立刻重試；如果程序在送出與標記之間死掉，租約到期後報告可能再送一次（只有在當機時才是至少一次，對私下通知可以接受）。每次寫入都是暫存檔加更名。世系的走訪使用資料庫公開的 `source_manifest()`。測試讓第一次私訊失敗，再上傳同一個檔案，確認報告只送達一次；一個過長行的測試送入單一個長達數千字元的 mechanics token 與單一個上下文單字，確認報告在不遺失任何字元的情況下換行、在上限內分頁並送達，包括兩個只在第 40 個字元之後才不同的 token；一個別名測試把 `CHARACTER_DISPLAY_ALIASES` 設成會展開的別名，送出含有該別名的一頁，確認恰好送出一則訊息且沒有被截斷；一個重新分頁測試送出部分頁面後改變換行寬度、重試，確認送達從新頁面集合的第 1 頁重新開始，新集合的每一頁依序送達而且沒有缺漏，並顯示重送標示（重複舊內容是接受的結果）；一個移交測試先把部分頁面送給 KP A，執行 `/coc kp transfer` 給 KP B，重試，確認 B 從第 1 頁起收到每一頁，且 A 先前收到的頁數不算數；第二個移交測試在最後一頁送出之後、`mark_report_delivered` 之前執行移交，確認報告沒有被標成已送達，而且 B 之後完整收到它；一個過大報告的測試建出超過 19,000 字元的報告，在中途讓送出失敗、重新上傳，確認每一頁恰好按順序送達一次，而且只有最後一頁之後才變成 `delivered`；一個並行測試同時啟動兩個重新上傳，斷言只有一個保留成功、私訊只送出一次；第三個測試讓 repair A 的私訊失敗、套用不重疊的 repair B、再上傳 A，確認 A 的報告從 A 自己的稽核紀錄送達一次，而不是 B 的。拒絕訊息也同樣處理：說明問題，絕不引用頁面文字。不會因為數值變動而拒絕：檔案本來就是審查者的更正，報告讓 KP 能在下一場遊戲前看到 `1D4` 變成了 `1D6`。錯誤的 repair 要靠上傳修正後的 repair 檔來更正，它以同樣的更正語意套用；父劇本保留在劇本庫，作為來源紀錄，也可用來刻意開新遊戲，但從劇本庫選用它（`/coc scenario use`）會開新的時間線，不是還原。

`mechanics_counts` 是比 `scenario_numbers.counts` 更嚴格的 token 切分：`counts` 會丟掉單獨的正負號與分隔符（`counts("Bonus +10%") == counts("Bonus -10%")`，`SAN 1/1d6` 與 `SAN 1 1d6` 的 token 計數也相同）。機制 token 會保留直接寫在數字前面的正負號（`+`、`-`、`−`），**包括緊貼在字詞後面的情況**（`STR+10` 與 `STR-10` 是 token `+10` 與 `-10`），並把以 `/`、`-`、`–` 或 `−` 相連的數值運算元合成一個 token（`1/1d6`、`1-3`）。token 內的空格與 tab 不重要。像 `A-10` 這樣帶連字號的標籤會得到 token `-10`；只有那段文字被改動時才有影響，文字相同就不會有變化。整頁的 token 計數看不到在不同機制之間互換的數值（`HP 10, SAN 40` → `HP 40, SAN 10` 的 token 相同），所以每個 token 都配上它的語境：同一行中緊接在它前面的最多兩個詞（字母或 CJK 字元），並做大小寫折疊。`HP 10, SAN 40` 是 (`hp`, `10`) 與 (`san`, `40`) 兩組。多重集合還是看不到重複出現的同一個標籤，例如屬性表（`Rat / HP 10`、`Ogre / HP 20` 兩個數值互換），所以配對**依出現順序**保留，報告是兩個序列的有序差異（`difflib.SequenceMatcher` 的 opcodes）：被取代、刪除或插入的區塊內的一切，連同語境都列為移除與新增。數值互換會改變序列，因此會被回報。數字只是搬移位置的頁面（修正雙欄閱讀順序）也會被回報為有變動，這是刻意的：KP 看得到那些數字移動了，可以確認它們仍然在正確的標籤旁邊。現有的 `counts` 不變，其他使用者不受影響。

報告只證明有變動的 token 被看見了，並不證明改得對；外部審查仍是證據來源。

## 11. 驗證階段

任何失敗都會中止整份 repair，什麼都不發布。

- **A，封包：** 檔名、UTF-8、schema 版本、精確的鍵、補丁數量。
- **B，目標：** 已載入來自 PDF 的劇本，且 `page_count` 相符。
- **C，頁面：** 頁碼範圍、唯一性、沒有注入頁面標記。
- **D，內容：** `review_note` 不為空；頁面種類對 `text` 的要求；`image` 對原生文字的規則。
- **D2，是否已套用：** 如果已載入的劇本本身就是 repair 子劇本（它的 manifest 有 `source_repair`，**而且其中的 `scenario_id` 等於已載入劇本的 ID**；CLI 的來源審查流程會深拷貝父 manifest，所以完整審查的後代可能帶著 repair 的 `source_repair` 的過時副本，因此第 1 階段由每個衍生發布都會經過的那個函式 `trusted_scenario_source.publish_derived()` 移除它（所以 `scenario_source_review.publish()` 與外部 AI 的 `scenario_source_authoring._publish()` 都涵蓋，不需要每個發布者各自記得；只有會自己設定這些欄位的 repair 模式保留它們），連同複製來的 `artifacts` 標記與 `artifacts_generation` 指標（完整審查的發布者寫的是根層級的索引與預製角色檔，沒有自己的世代目錄，所以這樣的子劇本讀起來是世代 `legacy`，讀取者絕不會跟著指標走到不存在的世代），並對先前已發布的後代以 ID 不符忽略它），就對這個子劇本記錄的父劇本重建這些補丁的候選，並把摘要與子劇本的 `source_repair.candidate_digest` 比對。相符表示同一個檔案就是最後一次套用的 repair，回覆 `這份修復已經套用，沒有重複建立版本。` 並停止：不發布新東西（額外只有上面的報告重送，以及實作階段第 2 項的 `inherited` 產物重試）。不相符表示這個檔案與最後一次 repair 不同，照常對已載入的劇本繼續。在世系較早處套用過的檔案，在較晚、互不重疊的 repair 之後再上傳，這裡同樣不會相符，但它的候選會與已載入的劇本完全相同，F 階段用同樣的方式回答，而不是把它當成無效的 repair。
- **E，候選：** 只拼接列出的頁面，建出候選。
- **F，不變條件：** 實體標記仍是 `1..N` 且各出現一次；未被修改的頁面本文逐位元組不變（指第 8 節的原始本文）；被替換的頁數等於補丁數；候選摘要是確定性的。候選的文字與已載入的劇本相同不是錯誤，而且**只有在解析品質中繼資料也已就位時**才是無操作：每個被要求的頁面已經正好是提議的文字，*而且*它的解析品質列已記錄 `operator-reviewed-discord`，`selected_sha256` 與 `page_kind` 相同（第 13 節）。只改中繼資料的 repair（例如文字量低、標籤已正確擷取的頁面，以 `page_kind: map` 送出來清除警告）會**照常發布**為只含中繼資料的子劇本：文字與父劇本相同，品質列與 `review_pages` 依第 13 節，`candidate_digest` 涵蓋中繼資料所以有自己的 ID，父劇本的衍生產物逐字複製（沒有文字改變所以沒有重建；標記為 `inherited`，因此啟用不裝入任何產物）。文字與中繼資料都已就位時，已載入的劇本是 repair 世系成員時回覆 `這份修復已經套用，沒有重複建立版本。`，否則回覆 `這些頁面的內容已經與修復檔相同，沒有變動。`，並且什麼都不發布。在世系較早處套用過的檔案，也是這樣回答。為了這個回答，機器人會算出檔案的 `patches_digest`，沿世系一路往上，走 `source_review.parent_scenario_id`（`publish_derived()` 為每個衍生子劇本都會寫入，不論 repair 或完整審查），找到 `patches_digest` 相同的那一筆。沒有任何一筆相符時（例如檔案只改了 `review_note`，因品質列不存放備註而被歸為無操作，但它的 `patches_digest` 與所有已記錄的都不同），就沒有報告可重送：回答只是一般的已套用，什麼都不讀、不送。有相符的一筆時，只有在自己的稽核類型為 `page_repair` 且 `source_repair.scenario_id` 等於自己 ID 的節點上才使用 `source_repair`（中間的完整審查節點只是被穿過，絕不讀它的稽核來找 repair 報告）：下面「私下報告重送」讀的是那一筆自己的稽核，而不是目前載入子劇本的稽核，所以 repair A 卡住的報告在之後不重疊的 repair B 之後仍能補送。

## 12. 發布

絕不更新父劇本的目錄。透過 `trusted_scenario_source.publish_derived`，使用 `scenario_source_review.publish()` 的不可變衍生來源模式。

- 衍生 ID：`<parent-prefix>-repair-<candidate_digest[:16]>`。已存在且稽核摘要相符時，當成冪等的成功回傳；已存在但內容不同則失敗。
- `candidate_digest` 涵蓋父劇本身分、正規化後的補丁（依頁碼排序）與候選文字。`patches_digest` 只是正規化補丁（依頁碼排序，與任何父劇本無關）的 SHA-256，用來在世系中辨識「這份修復檔」，存在 `source_repair` 與稽核裡。
- manifest 新增 `source_repair`（版本、自己的 `scenario_id`、父劇本 ID 與內容雜湊、候選摘要、被修復的頁面、審查者使用者 ID 與顯示名稱、上傳的檔名、時間）。
- `trusted_scenario_source.publish_derived()` 對既有目的地驗證的身分（`candidate_digest` 與 `parent_scenario_id`）要寫在它的驗證器讀取的地方，也就是稽核與 `manifest["source_review"]`，和來源審查的子劇本完全一樣；`source_repair` 帶上面那些 repair 專屬的細節。兩者都寫，所以輔助函式冪等的 `target.exists()` 路徑會回傳既有的 ID，而不是把目的地當成已改變而拒絕，這樣一般的重試與狀態過時後的復原才能運作。
- 稽核存在既有的稽核位置（`source_review.json`），帶 `kind: page_repair`：對話 ID 與請求 ID（由上傳路由明確傳進 `publish`，絕不從可觀測性的語境回推，因為關閉日誌時它是空的）、父劇本與候選的摘要、前後內容雜湊、PDF SHA、審查者，以及每頁的前後 SHA-256、審查備註、頁面種類與移除／新增的數值 token。完整的前後文字是選用的；頁面雜湊加上不可變的父子劇本就能還原差異。

## 13. 解析品質更新

不要用單一的乾淨結果取代解析品質歷史。未被修改的頁面保留既有的品質列與警告。被修復的頁面得到 `method = "operator-reviewed-discord"`、`warnings = []`、新本文的 `selected_sha256` 與它的 `page_kind`。最上層變成 `version: "source-repair-v1"`，帶 `parent_parse_quality_version`、`repaired_pages`，且 `review_pages` 會扣掉被修復的頁面。從 `2、4、6、7、8` 修復第 2、4 頁之後，載入訊息只列出 `6、7、8`。

## 14. 衍生產物

repair 只改頁面文字，其他都不變，所以子劇本保留來自 PDF 圖片的東西，只讓從舊文字萃取的內容失效：

- **失效：** `indexes`（NPC 與地點索引）與 `pregens`，它們是從文字萃取的，下面會重建。
- **原樣從父劇本複製：** `scene_maps`（地圖拓撲來自頁面圖片，圖片沒有改變，而且第 15 節說 repair 絕不編輯它）、頁面圖片檔（原本的位元組與解析度），以及圖片資源的中繼資料：哪些頁面被標成公開講義或地圖，以及它們的類型與章節分類。唯一的例外是**被修復頁面**的每個資源的 `description`：`scenario_library` 從頁面文字推導它，`search_images()` 會搜尋它，所以從修正後的文字重新產生，而可見性、類型與章節仍沿用父劇本的。否則圖片查詢會繼續比對到已被移除的詞，找不到修正後的詞。這個推導目前在私有的 `_build_image_assets()` 裡，其他模組不得呼叫（私有名稱規則，也會觸發 SLF001），所以第 1 階段在 `scenario_library` 新增公開的 `image_asset_description(text, page)`，回傳單一頁面的描述（與 `_build_image_assets()` 相同的頁面範圍擷取與 500 字元截斷，並讓 `_build_image_assets()` 改為呼叫它，使定義只有一份），repair 發布路徑對每個被修復的頁面只呼叫它；類型、可見性與章節欄位絕不重新計算。所以之後 `/coc scenario use <repaired-id>` 載入的子劇本，房間導覽仍然正常，地圖也一樣清楚。

`trusted_scenario_source.publish_derived()` 不夠用：它會以 110 DPI 重新渲染每一頁、把圖片資源換成 `kp_only_image_assets`，並寫入空的 `scene_maps`。repair 的發布路徑改成複製父劇本的 `images/`、`image_assets` 與 `scene_maps`（該輔助函式的 `reuse_parent_assets` 模式，既有的來源審查呼叫者不變）。

對進行中的遊戲，連續性很重要：保留目前的玩家角色、已認領的預製角色、HP、SAN、幸運、背包、時間線、房間位置與**目前章節**（`active_chapter_id` 與 `context_chapter_ids`），並保留執行階段的 `scene_maps`。把衍生的來源產物標記為需要重建，並依新的來源雜湊重建。NPC 與地點索引、預製角色池在**上傳流程內重建並等待完成**，之後才取得啟用鎖，所以正常的第一次上傳不會在它們還是 `pending` 時就走到啟用，也沒有任何東西依賴一次永遠不會來的重試；只有 RAG 預熱可以維持非同步。重建有兩層防護。把重建好的產物提交到**劇本庫項目**，由子劇本自己的 manifest 與來源雜湊把關（並且 `artifacts` 標記從 `pending` 到 `ready` 是一次 compare-and-set），所以第一次上傳、父劇本仍是使用中的劇本時也能運作，針對舊雜湊的緩慢重建也絕不會覆寫較新的 repair。只有把重建好的產物**裝進進行中遊戲的狀態**，才要求子劇本在那一刻是使用中的來源。

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

在對話鎖之下：操作者仍有權限；使用中的 `scenario_library_id` 等於父劇本；使用中的來源雜湊仍等於父劇本的；`timeline_id` 沒有改變；`state_revision` 符合 repair 交易的政策；沒有其他待處理的劇本提交或預製角色幸運決定；子劇本的 `artifacts` 標記是 `ready` 或 `inherited`（`pending` 會被拒絕）。`ready` 會把子劇本重建好的 NPC 與地點索引、預製角色池裝進進行中的遊戲。`inherited` **不裝入任何**衍生產物：遊戲維持目前的執行期索引與預製角色原樣（它們可能已另外重建過，是唯一確定可用的），只切換來源文字與資料庫 ID。所以 fail-soft 的空擷取絕不會取代進行中遊戲裡的任何東西；而且 `resource_bridge.guard_replacement(state)` 允許替換。以目前的章節 ID 載入修復後的子劇本：沒給章節時 `scenario_library.load_context()` 會預設為第一章，`scenario_activation.install_context_fields()` 隨後會覆寫這兩個欄位。repair 子劇本沿用父劇本的章節，所以 ID 仍能解析。以更正語意套用。**不要**呼叫 `_new_upload()`、重設 `game_started`、清除時間線、清掉角色、重設房間或清除戰役歷史。`scenario_library_id` 與 `scenario_text` 在同一個交易中改變，絕不能一個先、一個後。

**已啟用子劇本的產物更新。** 上面的啟用交易要求使用中的劇本是父劇本，所以無法處理之後的 `inherited` → `ready` 更新（那時子劇本已經是使用中的劇本）。這個情況有自己有守門的交易 `scenario_lifecycle.refresh_repair_artifacts()`，它**只**更換執行期的 NPC 與地點索引和預製角色池。它以**目前使用中的章節 ID**（`state.active_chapter_id`，與啟用交易相同）載入子劇本，因為 `scenario_library.load_context()` 沒給章節時會預設為第一個可玩章節，否則會把執行期索引換成第一章的切片；測試更新一個正在後面章節的遊戲，確認裝入的索引是該章節的。在對話鎖之下它要求：操作者仍有權限；使用中的 `scenario_library_id` 等於子劇本；使用中的來源雜湊等於子劇本的內容雜湊；`timeline_id` 與重建前記下的值相同；`state_revision` 符合 repair 交易的政策；子劇本的標記現在是 `ready`；而且 `resource_bridge.guard_replacement(state)` 允許。它不動劇本文字、章節、角色、已認領的預製角色、HP、SAN、幸運、背包、時間線或房間；玩家已認領的預製角色維持已認領，只替換尚未認領的池。任何條件不成立時什麼都不裝入，子劇本在資料庫中維持 `ready`，下次載入或更新時使用。一個測試讓第一次更新因 `state_revision` 改變而失敗，確認之後重新上傳同一個檔案會裝入子劇本的世代並記錄下來；一個測試在 `inherited` 啟用之後用 provider 重新上傳檔案，確認執行期索引與預製角色池被替換而狀態不變，並確認時間線改變或使用中的劇本不同時會拒絕更新。

## 17. 兩階段並行

解析、雜湊、頁面檢查與發布不得持有對話鎖。

```text
LOCK: authorize, capture parent id/hash and revision/timeline, check no conflicting pending operation   UNLOCK
parse, validate, build candidate, publish the immutable derived scenario, await the index and pregen rebuild until `artifacts` is `ready` or `inherited` (either is activatable; `pending` is the only state that waits, and a rebuild always ends in one of the two)
LOCK: re-check authorization, parent id/hash, timeline, replacement guard; activate if still valid      UNLOCK
```

如果發布之後狀態已改變，新版本留在劇本庫，但不套用：

```text
新版已建立，但遊戲狀態在修復期間已變更，因此沒有自動套用。
請重新上傳同一份 repair 檔，會以更正的方式套用，不會重置遊戲。
```

復原方式是**在原本的父劇本仍是已載入的劇本時**重新上傳同一個檔案：衍生 ID 是確定性的，所以上傳時會找到已發布的版本，只以 repair 語意啟用它。狀態過時的通知依「什麼變了」選擇：

- 只是修訂版本前進，已載入的劇本與它的來源雜湊仍是父劇本的：請 KP 再上傳同一個檔案；
- 已載入的劇本或它的來源雜湊變了（另一次上傳、`/coc scenario use`、另一份 repair）：不建議重新上傳，因為那時檔案會對著新載入的劇本以標題與頁數檢查；改說新版本留在劇本庫，沒有套用到目前的劇本。

標題檢查就是讓誤傳到另一份劇本的上傳失敗的機制。不要叫 KP 從劇本庫選用這個版本：`/coc scenario use`（`activate_existing_scenario()`）會建立新的時間線並清掉待處理與已結算的檢定。

已有效發布的來源絕不會因為啟用變成過時就被回滾或刪除。

## 18. 結果訊息

每個拒絕訊息都說明下一步怎麼做，檔案無法被讀成 repair 時要指出範本。

```text
✅ 劇本來源修復完成

《The Haunting Scenario trimmed》
修復頁面：2、4、6、7、8、10、14、16、17
新版來源：<new scenario id>

其中 2 頁的數值有變動，詳細內容已私訊給 KP。

已只替換指定頁面，其餘頁面保持不變。
目前遊戲已切換到修正版，角色、進度與目前位置沒有重置。
舊版仍保留在劇本庫。
```

已發布但未套用：版本 ID 加上第 17 節的狀態過時通知，依什麼變了選擇內容。標題或頁數不符時，說明檔案預期的是哪份劇本、目前載入的又是哪份。冪等的重新上傳：`這份修復已經套用，沒有重複建立版本。` 頁數不符、沒有載入來自 PDF 的劇本、頁碼無效、含有可讀文字的 `image` 頁，或無法讀取的檔案，各自得到一則明確的訊息，並且什麼都不套用。

## 19. 自由格式的 repair Markdown

**不要**自動匯入像 `# PDF page 2 ... # PDF page 4 ...` 這類自由格式的檔案。它們是有用的已審查素材，但沒有頁面種類、審查備註或頁數檢查。範本就是轉換路徑：把審查過的頁面文字複製進去。

## 20. 重構 `scenario_source_review`

避免兩份有細微差異的驗證器。抽出可重用的公開輔助函式：`split_source_pages`、`published_page_text`、`candidate_text` 與 `validate_evidence`（審查 CLI 保留它的證據矩形；頁面 repair 不使用它們）。兩處刻意的差異：頁面 repair 以無損方式切分父劇本（第 8 節），並用機制 token 回報數字（第 10 節）。會 strip 的切分器與 `scenario_numbers.counts` 保留給既有的呼叫者。

## 21. 檔案

新增：`app/scenario_page_repair.py`、`app/services/scenario_repair.py`、`tests/test_scenario_page_repair.py`、`tests/test_discord_scenario_repair_upload.py`、`docs/references/scenario_page_repair_template(.md|_zh.md)`，以及這份規格。修改：`app/commands/handlers/uploads.py`、`app/discord_bot.py`、`app/commands/handlers/system.py`（劇本庫選用的確認訊息）、`app/commands/router.py`（`handle_uploads()` 接收並轉送 `user_id`、顯示名稱與 `send_private`）、`app/discord_transport/delivery.py`、`app/services/scenario_lifecycle.py`、`app/scenario_source_review.py`、`app/scenario_numbers.py`、`app/scenario_index.py`、`app/pregen_extractor.py`、`app/models.py`（持久化的 `GroupState` 欄位 `installed_artifacts_generation`，連同序列化與舊存檔的預設值）、`app/scenario_activation.py`（凡是裝入產物的地方都設定該欄位，所以經由 `activate_existing_scenario()` 的一般劇本庫選用也會設定它）、`app/scenario_library.py`、`app/trusted_scenario_source.py`、`app/help_registry.py`、`app/services/scenario_ingestion.py` 中的載入確認，以及參考與指南文件。

## 22. API

```python
@dataclass(frozen=True)
class RepairTarget:
    title: str
    page_count: int

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
publish(check: RepairCheck, *, reviewer_user_id, reviewer_display_name, uploaded_filename,
        conversation_id, request_id) -> str
```

## 23. 原子性與冪等性

來源發布全有或全無；任何一頁失敗就什麼都不發布。啟用在狀態交易之下全有或全無；狀態絕不指向部分的候選。衍生 ID 由候選摘要決定。對同一個父劇本重新上傳同一個檔案，會回傳同一個 ID 並確保啟用正確；衍生版本已在使用中時會明說；對較新的父劇本上傳時，頁數與頁面檢查與任何檔案相同，而頁面文字已與 repair 相同時，會被回報為沒有變動。

## 24. 翻譯變體與 RAG

來源修復會改變正本的來源身分。綁定到舊來源的翻譯或範本變體留在舊劇本之下，對修復後的劇本不可用，使用中的來源有這種變體時要通知 KP；之後要遷移翻譯就用 `scenario_source_review.rebind()`。絕不依紀錄 ID 複製翻譯紀錄。任何以父雜湊為鍵的 RAG 快取都已過時，絕不能當成子劇本的權威；針對新雜湊排程重建，期間用新的正本文字做安全的備援檢索，不要因為重建而阻擋遊戲。

## 25. 效能

9 頁的補丁不會重跑 PaddleOCR、Tesseract、PyMuPDF4LLM 擷取或 AI PDF 修復。上傳延遲來自解析、雜湊、頁面檢查、數值報告、衍生發布與狀態提交，通常是數秒，加上要等待完成的 NPC 與地點索引和預製角色池重建，那是一次模型呼叫，可能比其他步驟都久；只有 RAG 預熱之後非同步執行。

## 26. 安全／信任邊界

把檔案當成不受信任的輸入：拒絕路徑穿越與檔案路徑、內嵌的頁面標記、不支援的鍵或版本、過大的內容、重複的頁碼、無效的 UTF-8 與錯誤的頁數。不執行 Markdown、不參照其他本機檔案、不使用檔案提供的審查者身分，也絕不讓 repair 修改父劇本。

## 27. 測試

- **解析：** 有效的單頁 repair；接受 BOM；未知的最上層、target 或補丁鍵；重複、0 或超出範圍的頁碼；注入標記；無效的 `page_kind`；缺少審查備註；沒填的範本會被拒絕。
- **綁定：** 解析器保留 `target.title` 並在檢查時比對，標題不符在解析與綁定測試中被拒絕；沒有載入劇本；只有 Markdown 的劇本；頁數不符；頁數相同的另一份劇本會因標題不符被拒絕；標題對修復後的子劇本仍然相符。
- **數值報告（私下）：** 詳細報告只用私訊傳給 KP Assistant，對話裡的回覆不含任何移除或新增的 token；沒有登記 KP Assistant 時留在稽核裡；玩家送出內容任意的補丁，無法從任何回覆讀回只給 KP 看的數字；互換的數值（`HP 10, SAN 40` → `HP 40, SAN 10`）與互換的重複標籤（`Rat / HP 10; Ogre / HP 20`）會被回報；`1D40 → 1D4` 的變動會以移除與新增的 token 列出；`+10% → -10%`、`SAN 1/1d6 → SAN 1 1d6` 與 `STR+10 → STR-10` 都會被列出；沒變的文字什麼都不報；沒改動的帶連字號標籤不產生變動；一頁的報告不影響另一頁。
- **重疊：** repair A 然後 repair B 改同一頁，再上傳 A：回覆指出第 10 頁被 repair B 改過而這次覆蓋了它，稽核也記錄；先前沒有 repair 碰過的頁面沒有這個提示。
- **世系：** 兩個 repair 之間夾著完整審查節點時，重疊提示仍會指出較早的 repair；從 repair 子劇本發布的完整來源審查子劇本與外部 AI（`/coc scenario source import`）子劇本不帶 `source_repair`、`artifacts` 標記與 `artifacts_generation` 指標，選用它會無錯誤地載入自己的根層級檔案（較早發布的副本以 ID 不符被忽略），完整審查改過那些頁面時，對它重新上傳該 repair 不會被回答為已套用，而且報告重送會略過完整審查節點的稽核、讀 repair 自己的稽核；
- **合併：** 只改中繼資料的 repair（文字相同，以 `page_kind: map` 清除低文字量警告）會發布帶有更新後品質列與複製產物的子劇本，而文字與品質列都已就位的檔案什麼都不發布；在世系較早處套用過的檔案，在較晚、互不重疊的 repair 之後再上傳，會被回答為已套用，而不是被當成沒有變化而拒絕；只有列出的頁面改變；未被修改的頁面保留完全相同的位元組（含空白）；標記維持順序且各出現一次；補丁順序不影響結果；頁面文字與品質列都已是補丁內容的 repair 不是錯誤：得到第 F 階段的回答（在 repair 世系內是 `這份修復已經套用，沒有重複建立版本。`，否則是 `這些頁面的內容已經與修復檔相同，沒有變動。`），測試斷言沒有發布任何東西、沒有建立稽核紀錄或私訊；同一份 repair 具冪等性：套用到已載入的劇本之後再上傳同一個檔案，回覆「已經套用」，絕不走到沒有變化的拒絕。
- **地圖與影像：** 有標籤的地圖頁被接受並清除低文字量警告；地圖 repair 不改變場景地圖圖形；存在原生文字時 `image` 被拒絕，沒有時被接受。
- **Discord：** 上傳者的顯示名稱、對話 ID 與請求 ID 從路由器一路傳到 `publish` 與稽核（關閉日誌時稽核仍有這三者），不回頭呼叫 Discord；`repair_*.md` 路由到 repair 處理器、絕不到比較處理器；預設任何使用者都能上傳，`SCENARIO_LIFECYCLE_KP_ONLY` 會限制為 KP Assistant；拒絕超過一個 repair 附件；待處理的來源替換遵循准入政策；發布之後狀態已改變時會發布但不啟用。
- **生命週期：** 啟用在多章節戰役中保留目前章節；狀態過時的啟用在父劇本仍載入時靠重新上傳同一個檔案以 repair 語意復原，絕不靠 `/coc scenario use`，切換劇本之後不建議重新上傳；啟用保留時間線、`game_started`、已認領的玩家角色、HP／SAN／幸運／背包與房間位置；絕不呼叫 `_new_upload()`；新來源只在完整交易之後才成為使用中；舊劇本仍可讀取。
- **解析品質：** 被修復頁面的警告清除、未被修改頁面的保留、載入訊息只列出剩下的頁面。
- **產物：** 被修復頁面的圖片描述會從修正後的文字重新產生，可見性、類型與章節仍沿用父劇本的，圖片搜尋找得到修正後的詞、找不到已被移除的詞；子劇本保留父劇本的 `scene_maps`、圖片位元組與圖片資源中繼資料（公開講義仍然公開），`/coc scenario use <child>` 載入的地圖正常運作；重建成功（`ready`）後子劇本的索引與預製角色來自它自己的文字，不複製父劇本的；`inherited` 時子劇本的檔案是父劇本的逐字複本，而且不會裝進任何東西到進行中的遊戲；重建以子劇本的雜湊為鍵；過時的重建不能覆寫較新的 repair。

## 28. 驗收測試：The Haunting

基準：`The_Haunting_Scenario_trimmed`，27 個 PDF 實體頁，警告頁為 2、4、6、7、8、10、14、16、17。審查者對照 PDF 為這九頁填寫範本，檔案以 `repair_the-haunting-trimmed_01.md` 上傳。預期：頁數相符；27 頁的候選中只有那九頁不同；建立新的不可變劇本 ID 且原劇本不變；KP 以私訊收到每頁的數值變動，對話裡的回覆不含其中任何一項；那九個警告頁不再出現在解析品質警告；時間線、角色、狀態與房間位置不變；新的來源雜湊取得新的 RAG 與索引建置；再次上傳同一個檔案不會建立另一個版本。

## 29. 範本與說明

新的上傳格式要連同文件一起，與它所描述的行為放在同一個 pull request：

- **範本。** `docs/references/scenario_page_repair_template(.md|_zh.md)` 是撰寫範本，在 `docs/README.md` 與 `docs/README_zh.md` 的「References」下連結，和 `role_card_template` 一樣。它寫明規則並附 JSON 骨架。測試讓它的鍵與解析器接受的鍵完全一致，所以不會漂移，也檢查沒填的範本會被拒絕。
- **說明。** 對話說明（`app/help_registry.py`，由 `help_service` 與 Help UI 呈現）新增上傳 `repair_*.md` 的項目，已載入劇本時顯示：任何人都能上傳、檔案的內容，以及 KP 會私下收到數值變動。`docs/references/player_command_reference(.md|_zh.md)` 與 `docs/guides/gameplay(.md|_zh.md)` 加上對應的文字，列出解析品質警告頁的載入確認訊息也要指向 repair 流程與範本。
- **訊息。** 每個拒絕訊息都說明下一步怎麼做，檔案無法被讀取時要指出範本。

## 交付方式

下列每個實作階段各自是一個 pull request、各自審查；在第 4 階段完成之前，狀態維持 `partial`。既有的 `scenario_source_review.publish` 需要涵蓋每一頁的提案，並寫入完全乾淨的解析品質紀錄，所以第 1 階段要抽出共用的頁面邏輯，並新增部分頁面的發布路徑，而不是原封不動地重用 `publish`。

## 30. 實作順序

這個順序確保遊戲永遠不會被切到衍生產物缺失的子劇本：重建在啟用之前完成，而在啟用上線之前，上傳只做發布。

1. **確定性核心。** 先新增 `trusted_scenario_source.read_audit()`、`reserve_report()`、`advance_report()`、`mark_report_delivered()` 與 `release_report()`（都在發布鎖之下做 compare-and-set）。從 `scenario_source_review` 抽出共用的頁面輔助函式；實作解析器、嚴格 schema、無損合併、數值報告、候選摘要、公開的 `scenario_library.image_asset_description()`，以及會複製父劇本圖片、圖片資源與場景地圖的不可變部分頁面發布，附單元測試與範本。發布的子劇本在 manifest 記錄 `artifacts: "pending"`。
2. **衍生產物重建。** `scenario_library` 先提供公開 API 處理資料庫條目的衍生產物，讓 repair 模組絕不伸手進資料庫的目錄結構：`read_derived_artifacts(scenario_id)`（未過濾的 `indexes`、`pregens` 檔案與 `artifacts` 標記），以及唯一的寫入者 `commit_derived_artifacts(scenario_id, source_hash, indexes, pregens, marker)`。沒有另外的複製操作：`inherited` 的退路是用 `read_derived_artifacts(parent_id)` 讀出父劇本的檔案，再透過同一個寫入者以標記 `inherited` 提交給子劇本。寫入者是 compare-and-set，只有在條目內容雜湊等於 `source_hash` 且轉換被允許時才套用：`pending → ready`、`pending → inherited`、`inherited → ready`，以及 `inherited → inherited`（冪等的無操作，什麼都不寫）。已經是 `ready` 的子劇本絕不會被取代，所以失敗的並行重建不能覆寫成功的那個。產物以**世代**發布，所以當機絕不會留下混合的一組：`commit_derived_artifacts()` 先把新的 `indexes.json` 與 `pregens.json` 寫進全新的暫存目錄 `derived/<generation_id>/`，之後才用 manifest 的一次原子更名切換**一個**指標，也就是 manifest 的 `artifacts_generation` 連同 `artifacts` 標記。讀取者透過這個指標載入索引與預製角色，所以切換之前看到完整的舊世代，切換之後看到完整的新世代；程序在切換前死掉，新目錄只是孤兒，下一次提交或啟動時的清掃會移除它；在切換後死掉，切換已經完成。這個功能之前發布的條目沒有 `artifacts_generation`，照舊讀取既有檔案，視為世代 `legacy`。提交在 `publication_lock()` 之下進行，而讀取者取同一把鎖，讓即時讀取者也不會看到只套用一半的切換：`scenario_library.load_context()`（與 `read_derived_artifacts()`）在鎖內讀取 manifest、索引與預製角色，所以同時進行的 `/coc scenario use <child>` 看到的是舊的整組產物或新的整組產物，絕不會是新索引搭配舊預製角色。只有這些小的 JSON 讀取在鎖內執行。測試在 `inherited → ready` 提交進行中選用該子劇本，確認載入的索引與預製角色來自同一代；當機測試在新檔案暫存完成後，以及只更名部分檔案之後（在步驟之間丟出例外來模擬）中止提交，重新啟動，確認子劇本載入的是完整的舊世代，而且孤兒目錄被清掃。重建需要知道擷取是否**成功**，而擷取器目前不會回報（`extract_scenario_index()` 對沒有 provider、呼叫失敗與真的是空結果都回傳同樣的空清單，`extract_pregens()` 同樣回傳 `[]`），所以 `scenario_index` 與 `pregen_extractor` 各新增會回傳狀態的版本，`extract_scenario_index_with_status()` 與 `extract_pregens_with_status()`，回傳 `(result, status)`，狀態為 `ok`、`unavailable`（沒有 provider 或文字為空）或 `failed`（呼叫失敗或沒有可用的回傳）；既有函式變成只回傳結果的薄包裝，所以它們的呼叫者不變。只有兩者狀態都是 `ok` 時，重建才算成功。以既有的建構器，依子劇本的來源雜湊，從它的新文字建出 NPC 與地點索引和預製角色池，以雜湊檢查提交，並設為 `artifacts: "ready"`；RAG 預熱另外排程。既有的建構器是 fail-soft 的：沒有設定分析 provider 或呼叫失敗時，`scenario_index.extract_scenario_index()` 回傳空清單、`pregen_extractor.extract_pregens()` 回傳空池。所以重建要回報擷取是否**成功**，並把來自失敗或不可用 provider 的空結果當成沒建好：此時子劇本的資料庫檔案是父劇本索引與預製角色檔的逐字複本（不假設父劇本的檔案非空，也不對它們的內容做任何宣稱），標記是 `inherited` 而不是 `ready`。`inherited` 可以啟用，但啟用不會把任何衍生產物裝進進行中的遊戲（第 16 節），所以遊戲維持目前的執行期索引與預製角色；回覆會說索引沒有重建。重試的方式是**重新上傳同一個檔案**：已套用的分支（階段 D2 與 F）會先讀子劇本的標記，若為 `inherited` 而且現在有分析 provider，就再跑一次重建，以子劇本的來源雜湊做 compare-and-set，把檔案與標記從 `inherited` 設為 `ready`，並且如果子劇本是目前載入的劇本，就透過第 16 節只處理產物的 `scenario_lifecycle.refresh_repair_artifacts()` 交易（重新檢查權限、使用中的子劇本與雜湊、時間線、修訂與替換守門）把重建好的產物裝進進行中的遊戲；回覆會說索引已更新。沒有 provider 時，回答仍是一般的已套用，並註明索引仍未重建。子劇本已經是 `ready`、但進行中的遊戲一直沒收到更新時（被等待的重建完成時，一般的回合推進了 `state_revision`，所以 `refresh_repair_artifacts()` 拒絕了），重新上傳同一個檔案也會重試：遊戲狀態記錄它最後裝入的 `artifacts_generation`（由 repair 啟用、更新交易與一般的劇本庫選用設定），已套用的路徑把它與使用中子劇本的 `artifacts_generation` 比對；兩者不同而且子劇本是 `ready` 時，再呼叫一次 `refresh_repair_artifacts()`，所以之後上傳同一個檔案一定會收斂，不依賴標記的轉換。擷取成功而且真的是空的才是 `ready`。`artifacts: "pending"` 的子劇本不能被選去啟用。`inherited` 的保證屬於 repair 啟用。從劇本庫一般選用 `inherited` 的子劇本（`/coc scenario use <child>`）是透過 `activate_existing_scenario()` 開**新**遊戲，裝入子劇本的資料庫檔案（那是父劇本的逐字複本），所以行為與選用父劇本完全相同，不會取代任何進行中遊戲的狀態；不另外加守門，測試選用 `inherited` 的子劇本，確認它載入的索引與預製角色和父劇本相同。測試：父劇本仍是使用中的劇本時，重建也會填入索引與預製角色並把子劇本標成 `ready`；沒有 provider 時子劇本是 `inherited`，檔案是父劇本的逐字複本，即使父劇本的檔案是空的，啟用它也不會動到進行中遊戲的索引與預製角色；之後有 provider 時重新上傳同一個檔案會重建、把標記改為 `ready` 並把重建好的產物裝進進行中的遊戲，沒有 provider 的重新上傳什麼都不改；過時的重建不能覆寫較新的 repair；裝進進行中的遊戲要求子劇本是使用中的；pending 的子劇本被拒絕。
3. **Discord 上傳。** `repair_*.md` 路由、`handle_uploads` 的 `user_id`、劇本生命週期權限檢查、repair 服務、第 2 階段的重建、結果與拒絕訊息、路由測試、說明項目與指南文字。在第 4 階段之前，回覆只說新版本已建立、尚未套用到進行中的遊戲，所以上傳不會改變遊戲。
4. **使用中遊戲的更正。** 要求 `artifacts` 為 `ready` 或 `inherited`（只有 `ready` 才裝入衍生產物）的 `scenario_lifecycle.activate_repair_version()`，以及 `scenario_lifecycle.refresh_repair_artifacts()`、更正語意、過時修訂／時間線檢查、保留地圖位置、目前章節與玩家狀態、整合測試，以及說明遊戲已切換的結果訊息。

## 31. 合併條件

- repair 不能套用到頁數不同的劇本、只有 Markdown 的劇本，或沒有載入劇本時；
- 全頁替換是確定性的，未被修改的頁面保留完全相同的位元組；
- 每個有變動的數字都會記入稽核（稽核是權威紀錄）；以私訊送給 KP 是嘗試並在重新上傳時重試，沒有登記 KP Assistant 或私訊關閉時 repair 仍會發布，報告留在稽核裡；沒有任何數字出現在對話的回覆裡；
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
- **為什麼綁定已載入的劇本，而不是雜湊？** 檔案由外部審查者撰寫，他有 PDF 與範本，但沒有 Bot 內部的雜湊。已載入的劇本、頁數與私下回報的數值變動，提供了真正重要的安全性，而不需要 KP 複製任何東西；不可變的父劇本讓錯誤的 repair 可以還原。
- **為什麼替換整頁？** 這提供確定性的來源紀錄，並避免模糊對齊。
- **為什麼回報數字而不是拒絕？** 來源修復本來就可以修數字，更正依定義就是數值變動；不該發生的是沒人注意到的變動。報告是讓人看見的工具，不是正確性的證明。
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
其中 1 頁的數值有變動，詳細內容已私訊給 KP。
建立新版劇本來源：the-haunting-...-repair-xxxxxxxxxxxxxxxx
目前遊戲已使用修正版；角色、進度與位置未重置。
```
