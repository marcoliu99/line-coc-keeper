# 從 Discord 上傳進行頁面級劇本修復

[English](discord_page_level_scenario_repair_design_spec.md)

狀態：**待實作**（僅設計，尚未實作）。基準：`main_v2` 的 `3fbec39`。

## 1. 問題

目前的劇本匯入有兩條不同的路徑：

1. 上傳 PDF：
   - 解析整份 PDF；
   - 建立新的劇本庫項目；
   - 可以當成新劇本或更正套用。

2. 上傳 `scenario*.md`：
   - 把整份 Markdown 當成完整且權威的劇本來源；
   - **不會**把選定的頁面補丁進目前的 PDF 劇本。

`app.scenario_source_review` 已經有一個安全的管理員流程：

```text
prepare → edit proposal.md → check → publish
```

這個流程已經正確做到：

- 把審查工作綁定到特定且不可變的 PDF／來源快照；
- 驗證 PDF 實體頁碼；
- 檢查證據區域；
- 回報數值 token 的變化；
- 建立衍生劇本，而不是覆寫原本的劇本；
- 記錄稽核中繼資料；
- 讓可能已過時的衍生產物失效。

不過它目前需要檔案系統／CLI 權限，而且需要涵蓋整份 PDF 的全頁提案，不適合一般的 Discord 流程。一般流程是 KP 看到：

```text
第 2、4、6、7、8、10、14、16、17 頁有解析品質待核對項目
```

並想用外部審查過的 Markdown 檔，只修正這些頁面。

### 期望的使用體驗

KP 應該能夠：

```text
1. 為選定的警告頁匯出 repair 工作檔。
2. 把該檔案連同原 PDF 交給 ChatGPT／其他審查者。
3. 取回 repair_<scenario>_01.md。
4. 把 repair_<scenario>_01.md 直接上傳到 Discord。
5. Bot 以確定性方式驗證 repair。
6. Bot 只替換指定的實體頁。
7. Bot 發布不可變的衍生劇本版本。
8. 如果被修復的來源仍是目前使用中的來源，Bot 以更正的方式套用，
   同時保留目前的遊戲狀態。
```

不需要重跑 PDF OCR。

## 2. 目標

### 2.1 功能目標

實作**必須**：

1. 辨識檔名以 `repair_` 開頭的 Markdown 附件。
2. 把該檔案當成**頁面補丁**，絕不當成完整劇本。
3. 把每一份 repair 綁定到唯一確切的劇本來源版本。
4. 只替換明確列出的 PDF 實體頁。
5. 每一個被替換的頁面都要求完整的修正後文字。
6. 拒絕過時的 repair 檔。
7. 以確定性方式偵測非預期的數值／骰式變化。
8. 重用原始 PDF 位元組與已渲染的頁面證據。
9. 建立新的不可變劇本庫項目。
10. 絕不覆寫父劇本。
11. 記錄父子來源關係與 repair 稽核。
12. 只清除被明確審查過的頁面的解析警告。
13. 保留未被修改的頁面上尚未解決的警告。
14. 把 repair 套用到使用中的劇本時，保留進行中的遊戲。
15. 避免重建原本的 PDF OCR 管線。
16. 具冪等性：重新上傳同一份有效的 repair 不得產生無止盡的重複版本。
17. 與既有的 `scenario_source_review` CLI 流程相容。

### 2.2 使用體驗目標

一般的 KP 流程不應該需要 shell 權限。

偏好的路徑：

```text
/coc repair export warnings
        ↓
repair_<scenario>_<id>.md
        ↓
external review
        ↓
upload repair_*.md to Discord
        ↓
validation
        ↓
published + applied
```

也應該支援指定頁面的匯出：

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

## 3. 非目標

第 1 版**不得**：

- 接受任意的 unified diff；
- 接受行號補丁；
- 以模糊比對替換文字；
- 推測 repair「大概」屬於哪一份使用中的劇本；
- 就地修改父劇本；
- 改變原始 PDF；
- 透過 repair Markdown 替換任意地圖圖形拓撲；
- 把自由格式的 Markdown 文件當成有效的 repair；
- 默默接受缺漏的數值變化；
- 自動改寫翻譯變體；
- 對整份 PDF 重跑 OCR；
- 允許一般玩家修改已發布的劇本來源。

地圖拓撲的修復仍歸既有的地圖管線／`map_*.yaml` 路徑負責，除非之後的規格明確把兩個系統合併。

## 4. 要重用的既有架構

實作應該重用而不是複製下列模組已有的安全性質：

- `app/scenario_source_review.py`
- `app/scenario_library.py`
- `app/trusted_scenario_source.py`
- `app/services/scenario_lifecycle.py`
- `app/services/scenario_ingestion.py`
- `app/commands/handlers/uploads.py`
- `app/scenario_numbers.py`

重要的既有行為：

### `scenario_source_review`

已經提供正確的來源修復概念模型：

- 實體頁來源切分；
- 不可變的來源綁定；
- 全頁審查文字；
- 證據邊界框驗證；
- 數值 token 前後帳目；
- 候選摘要；
- 不可變的衍生發布；
- 父劇本來源紀錄。

### `scenario_lifecycle._repair`

已經定義了來源更正對進行中遊戲想要的語意：

- 更新劇本標題／文字／索引參照；
- 不建立新的遊戲時間線；
- 不像 `_new_upload()` 那樣清除一般的遊戲進度。

新功能不應該發明第二個「repair」概念。

## 5. 架構決定

新增專屬模組：

```text
app/scenario_page_repair.py
```

以及服務轉接層：

```text
app/services/scenario_repair.py
```

職責：

```text
Discord upload router
        ↓
scenario_repair.handle_repair_upload()
        ↓
scenario_page_repair.parse()
        ↓
scenario_page_repair.validate()
        ↓
scenario_page_repair.publish()
        ↓
scenario_lifecycle.activate_repair_version()
```

不要把 repair 的解析或發布邏輯直接放進 `discord_bot.py`。

## 6. 附件路由

修改：

```text
app/commands/handlers/uploads.py
```

目前的順序包括：

```text
PDF
scenario*.md
map_*.yaml
role_*.md
generic .txt/.md comparison
```

在通用 Markdown 比較路徑之前加入 `repair_*.md`。

建議順序：

```text
PDF
scenario*.md
repair_*.md
map_*.yaml
role_*.txt/.md
generic .txt/.md compare
```

範例：

```python
repairs = [
    u for u in uploads
    if u.filename.lower().startswith("repair_")
    and u.filename.lower().endswith(".md")
]
```

規則：

- 第 1 版每則 Discord 訊息只能有一個 repair 檔；
- 超過一個就拒絕並給出明確的訊息；
- repair 檔絕不能落入 `handle_scenario_compare_upload()`。

## 7. 權限模型

這裡刻意與一般的初次劇本上傳不同。

`repair_*.md` 會改變使用中戰役所信任的來源。因此只有符合下列條件的使用者：

```python
permissions.may_manage_scenario_lifecycle(state, user_id)
```

才可以提交。

`handle_uploads()` 目前拿不到上傳者的 ID。把它擴充為：

```python
async def handle_uploads(
    conversation_id: str,
    user_id: str,
    uploads: list[Upload],
    reply: Reply,
    ...
) -> bool:
```

並更新 `discord_bot._handle_message()` 中的呼叫端。

不要信任上傳檔案內的 `reviewer` 欄位。

權威的審查者身分是：

```text
Discord user ID
Discord display name, if available
conversation/channel identity
request ID
timestamp
```

## 8. Repair 工作檔格式

使用恰好包含一個 JSON 內容的 Markdown，與既有的 `authoring.parse_markdown()` 慣例相容。

最上層 schema：

```json
{
  "repair_version": 1,
  "target": {
    "scenario_id": "the-haunting-scenario-trimmed-46c3c49c",
    "content_hash": "<exact parent source content hash>",
    "pdf_sha256": "<exact original PDF sha256>",
    "page_count": 27
  },
  "patches": []
}
```

只有這些最上層鍵有效：

```text
repair_version
target
patches
```

未知的最上層欄位**必須**被拒絕。

## 9. 頁面補丁 schema

每個補丁都是**對一個 PDF 實體頁本文的完整替換**。

範例：

```json
{
  "page": 10,
  "base_page_sha256": "a33d...",
  "text": "THE BASEMENT\n\nROOM 1: Storage\n...",
  "page_kind": "text",
  "review_note": "Checked against PDF page 10; repaired two-column order and retained all dice expressions.",
  "evidence": [
    {
      "bbox": [0.0, 0.0, 504.0, 720.0],
      "note": "Full physical PDF page reviewed."
    }
  ],
  "expected_numeric_delta": {
    "removed": {},
    "added": {}
  }
}
```

允許的頁面鍵：

```text
page
base_page_sha256
text
page_kind
review_note
evidence
expected_numeric_delta
```

第 1 版不允許其他頁面鍵。

## 10. `page` 的語意

`page` 永遠是 **PDF 實體頁碼**，從 1 開始。

它**不是**：

- 書上印的頁碼；
- 頁尾顯示的頁碼；
- 章節內的相對頁碼。

範例：

```text
PDF physical page 7
printed page 23
```

Repair 使用：

```json
"page": 7
```

這與 `scenario_source_review` 的行為相同，可避免不小心替換錯誤的頁面。

## 11. 全頁替換，不是片段合併

補丁的 `text` **必須**包含該實體頁完整的權威來源文字。

不要支援：

```text
append this paragraph
replace lines 30–50
replace this sentence
search-and-replace
unified diff
```

合併演算法：

```python
pages = split_physical_pages(parent_text)

for patch in validated_patches:
    pages[patch.page - 1] = patch.text

candidate_text = join_with_physical_page_markers(pages)
```

這讓結果是確定性的，並避免模糊比對。

## 12. 基準綁定與過時 repair 的防護

Repair **必須**綁定到它所依據的確切來源版本。

驗證下列全部項目：

```text
target.scenario_id
target.content_hash
target.pdf_sha256
target.page_count
```

都要與父劇本的可信快照相符。

每一頁也要驗證：

```text
base_page_sha256
```

其中：

```python
base_page_sha256 = sha256(current_published_page_body.encode("utf-8")).hexdigest()
```

不相符就代表 repair 已過時。

以下列訊息拒絕：

```text
這份 repair 是針對較舊的劇本來源製作的，沒有套用。
請重新匯出 repair 工作檔後再修正。
```

絕不對過時的 repair 做「盡力」合併。

## 13. 數值／機制安全

這是關鍵需求。

來源修復功能明確允許修正 OCR 的錯誤，包括數值錯誤。所以單純拒絕所有數值變化是錯的。

取而代之，每個補丁都帶有明確的預期數值差異：

```json
"expected_numeric_delta": {
  "removed": {
    "1d40": 1
  },
  "added": {
    "1d4": 1
  }
}
```

Bot 計算：

```python
old_counts = scenario_numbers.counts(old_text)
new_counts = scenario_numbers.counts(new_text)

actual_removed = old_counts - new_counts
actual_added = new_counts - old_counts
```

實際差異**必須**與 `expected_numeric_delta` 完全相等。

否則整份 repair 以原子方式被拒絕。

這能抓到對下列內容的意外變更：

- 骰式；
- 百分比；
- HP；
- SAN 損失；
- 技能值；
- 日期；
- 金額；
- 頁碼參照；
- 數值欄；
- 持續時間；
- 攻擊門檻。

重點：數值差異相符，只證明宣告的變更與檔案一致，**不**證明語意正確。外部審查仍是證據來源。

## 14. `page_kind`

支援的值：

```text
text
map
image
```

### `text`

一般的劇本敘述／規則／講義。

要求：

- `text` 不得為空。

### `map`

主要內容是平面圖或示意圖的頁面。

要求：

- 轉錄可讀的標籤；
- 不要捏造頁面上沒有印出的房間描述；
- 文字量少本身不算解析失敗；
- `page_kind=map` 可以解除該頁 `low_text` 類型的警告。

範例：

```text
Corbitt House Map (Keeper Version)
Upper Story
Ground Floor
Basement
Scale: 1/4 inch equals 3 feet.
```

地圖圖形拓撲本身在第 1 版**不**由這個檔案編輯。

### `image`

只在來源頁面真的沒有任何有意義的可讀文字時使用。

如果 PDF 頁面含有原生／可讀的標籤或規則，`image` **必須**被拒絕，與目前 `scenario_source_review.image_only` 的安全規則一致。

## 15. 頁面標記

替換用的 `text` **不得**包含：

```text
--- 第 N 頁 ---
```

或任何符合下列規則的內容：

```python
library.PAGE_MARKER_RE
```

實體頁面標記由系統擁有。

這可防止單一補丁注入或替換相鄰的頁面。

## 16. 證據

`evidence` 使用與 `scenario_source_review` 相同的 PDF 實體座標概念。

每一筆：

```json
{
  "bbox": [x0, y0, x1, y1],
  "note": "Full page verified against the rendered PDF."
}
```

驗證：

- 一到 100 筆；
- 座標是有限的數值；
- 矩形完全位於真實 PDF 頁面邊界內；
- note 不得為空。

對預設匯出的工作檔，Bot 應該自動填入一個全頁矩形。

外部審查者可以縮小範圍，但一般使用不需要。

證據 PNG 不需要上傳回 Discord：伺服器已經擁有原始 PDF，可以自行渲染目標頁面。

## 17. 匯出流程

新增：

```text
/coc repair export warnings
```

以及：

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

說明介面應該提供相同的動作。

### `warnings`

選取目前劇本中尚未解決的解析品質警告頁。

如果沒有警告頁：

```text
目前這份劇本沒有需要人工修復的解析頁面。
```

### 明確的頁碼清單

驗證：

- 只能是整數；
- 1 ≤ page ≤ page_count；
- 不重複；
- 匯出前排序。

### 匯出的檔案

建議的檔名：

```text
repair_the-haunting-scenario-trimmed_<short-hash>.md
```

工作檔應該包含：

- repair 說明；
- 確切的目標來源身分；
- 頁碼；
- 目前的頁面文字；
- 基準頁面 SHA；
- 全頁證據邊界框；
- 目前的解析警告；
- 要填寫的空白替換／審查欄位。

為了讓外部 AI 好用，匯出範本可以包含 `base_text` 與 `current_warnings`，但這些欄位必須：

1. 位於匯入的 JSON 內容之外；或
2. 被專屬的匯出／匯入 schema 剝除。

偏好的實作：讓匯入 JSON 保持嚴格，把既有文字放在 JSON 區塊之外的 Markdown 參考段落。

## 18. Repair 檔範例

````markdown
# Scenario page repair

This file replaces only the listed physical PDF pages.
Do not change target identity fields.

```json
{
  "repair_version": 1,
  "target": {
    "scenario_id": "the-haunting-scenario-trimmed-46c3c49c",
    "content_hash": "abc123...",
    "pdf_sha256": "def456...",
    "page_count": 27
  },
  "patches": [
    {
      "page": 6,
      "base_page_sha256": "111aaa...",
      "text": "LOCATION 9: THE OLD CORBITT PLACE\n\n...",
      "page_kind": "text",
      "review_note": "Checked against physical PDF page 6. Corrected the broken page reference from page @@ to page 33.",
      "evidence": [
        {
          "bbox": [0.0, 0.0, 504.0, 720.0],
          "note": "Full page checked against source image."
        }
      ],
      "expected_numeric_delta": {
        "removed": {},
        "added": {
          "33": 1
        }
      }
    },
    {
      "page": 7,
      "base_page_sha256": "222bbb...",
      "text": "Corbitt House Map (Keeper Version)\nUpper Story\nGround Floor\nBasement\nScale: 1/4 inch equals 3 feet.",
      "page_kind": "map",
      "review_note": "Verified as a floor-plan page. Low source text is intentional; visible labels were transcribed.",
      "evidence": [
        {
          "bbox": [0.0, 0.0, 504.0, 720.0],
          "note": "Full map reviewed."
        }
      ],
      "expected_numeric_delta": {
        "removed": {},
        "added": {}
      }
    }
  ]
}
```
````

## 19. 解析規則

新增：

```python
parse_repair_markdown(data: bytes) -> RepairProposal
```

要求：

- UTF-8 或帶 BOM 的 UTF-8；
- 把 CRLF 正規化為 LF；
- 套用既有的 authoring 檔案大小上限；
- 恰好一個 JSON 內容；
- 頁碼不得重複；
- 嚴格的欄位集合；
- 補丁數量上限：建議 `100`；
- 每頁替換文字的上限：受既有 authoring 檔案大小政策限制；
- 對 Discord 的位元組不做任何符號連結／檔案系統假設。

不要使用寬鬆的 YAML 解析器。

## 20. 驗證階段

驗證必須分成確定性的階段。

### 階段 A — 封包

驗證：

- 檔名；
- UTF-8；
- schema 版本；
- 精確的鍵；
- 補丁數量。

### 階段 B — 目標身分

驗證：

- 劇本存在；
- 劇本來自 PDF；
- 內容雜湊相符；
- PDF SHA 相符；
- 頁數相符。

第 1 版的 repair 不能以只有 Markdown 的劇本為目標，因為那樣沒有權威的 PDF 實體頁身分。

### 階段 C — 頁面身分

驗證：

- 頁碼範圍；
- 唯一性；
- 基準頁面 SHA；
- 沒有注入實體頁面標記。

### 階段 D — 證據

對真實 PDF 的頁面邊界驗證所有矩形。

### 階段 E — 頁面內容

驗證：

- 審查備註不得為空；
- 頁面種類的要求；
- 影像頁規則；
- 替換文字的要求。

### 階段 F — 數值差異

計算並與宣告的數值變化完全比對。

### 階段 G — 候選建構

只替換列出的頁面，建出完整的候選來源。

### 階段 H — 候選不變條件

驗證：

- 實體頁面標記維持 1..N 且各出現一次；
- 未被修改的頁面本文逐位元組不變；
- 被修補的頁數等於要求的補丁數；
- 候選與父劇本不同；
- 候選摘要是確定性的。

任何失敗都會中止整份 repair。

不做部分發布。

## 21. 發布模型

絕不更新父劇本的目錄。

使用與 `scenario_source_review.publish()` 相同的不可變衍生來源模式。

建議的衍生 ID：

```text
<parent-prefix>-repair-<candidate_digest[:16]>
```

如果相同的衍生 ID 已存在，且其稽核摘要相符，就把它當成冪等的成功回傳。

如果它存在但內容不同，則直接失敗。

建議新增到 manifest 的內容：

```json
{
  "source_repair": {
    "version": 1,
    "parent_scenario_id": "...",
    "parent_content_hash": "...",
    "candidate_digest": "...",
    "pages": [2, 4, 6],
    "reviewer_user_id": "...",
    "reviewer_display_name": "...",
    "uploaded_filename": "repair_....md",
    "reviewed_at": "..."
  }
}
```

不要依賴 repair 檔提供的審查者身分。

## 22. Repair 稽核

在衍生劇本旁儲存一份私有的稽核檔：

```text
source_repair_audit.json
```

建議的結構：

```json
{
  "version": 1,
  "parent_scenario_id": "...",
  "source_hash_before": "...",
  "source_hash_after": "...",
  "pdf_sha256": "...",
  "candidate_digest": "...",
  "reviewer": {
    "discord_user_id": "...",
    "display_name": "..."
  },
  "uploaded_filename": "...",
  "pages": [
    {
      "page": 6,
      "before_sha256": "...",
      "after_sha256": "...",
      "review_note": "...",
      "page_kind": "text",
      "evidence": [],
      "numeric_removed": {},
      "numeric_added": {}
    }
  ]
}
```

儲存完整的前後文字是選用的（若顧慮隱私或儲存大小）；頁面雜湊加上不可變的父子劇本，已足以還原差異。

## 23. 解析品質更新

不要用單一的乾淨結果取代所有解析品質歷史。

對未被修改的頁面：

```text
preserve existing page quality metadata and warnings
```

對被修復的頁面：

```text
method = "operator-reviewed-discord"
warnings = []
selected_sha256 = hash(replacement_text)
page_kind = text/map/image
```

最上層：

```json
{
  "version": "source-repair-v1",
  "parent_parse_quality_version": "...",
  "repaired_pages": [2,4,6,...]
}
```

這讓下一次載入時的訊息保持準確。

範例：

修復前：

```text
⚠️ 第 2、4、6、7、8、10、14、16、17 頁有解析品質待核對項目
```

九頁全部修復後：

```text
(no warning)
```

如果只修復第 2 與第 4 頁：

```text
⚠️ 第 6、7、8、10、14、16、17 頁仍有解析品質待核對項目
```

不要隱藏未被修改頁面上的警告。

## 24. 衍生產物

既有的 `scenario_source_review.publish()` 刻意讓下列項目失效：

```text
indexes
pregens
scene_maps
```

因為來源修復可能讓從舊文字萃取出的資料失效。

Discord 功能應該保留同樣的劇本庫安全性質。

### 劇本庫版本

新的衍生劇本**不得**盲目複製舊的：

- NPC 索引；
- 地點索引；
- 預製角色萃取；
- 場景地圖推論。

### 使用中的執行階段

把 repair 套用到目前進行中的遊戲時，連續性很重要。

使用 repair 語意：

- 保留目前的玩家角色；
- 保留已認領的預製角色；
- 保留目前的 HP／SAN／幸運／背包；
- 保留時間線；
- 盡可能保留目前的房間位置；
- 第 1 版保留目前執行階段的 `scene_maps`，因為底層的 PDF 影像沒有改變；
- 把衍生的來源產物標記為需要重建。

然後依新的來源雜湊非同步重建：

```text
NPC/location index
RAG/prewarm
pregen candidates, if required
```

只有在重建完成時，被修復的劇本仍是使用中的來源版本，重建結果才可以取代衍生產物。

絕不允許依舊來源雜湊進行的緩慢背景重建覆寫較新的 repair。

## 25. 地圖頁行為

這個功能必須解決誤報警告的問題，但不能假裝修復了地圖拓撲。

對：

```json
"page_kind": "map"
```

Bot 可以記錄：

```text
low text is expected
visible labels reviewed
```

並清除該頁的文字品質警告。

它**不得**改變：

- 房間圖；
- 鄰接關係；
- 入口房間；
- 祕密連線；
- visual_basis；
- 既有的地圖座標。

如果地圖拓撲有誤，使用既有的地圖修復路徑。

未來的第 2 版可以加入：

```text
logical_map_id
map_variant
paired_page
```

但這些欄位在第 1 版不應被接受。

## 26. 套用到使用中的劇本

新增一個生命週期 API，而不是濫用 `pending_pdf_upload`：

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

在對話鎖之下的前置條件：

1. 操作者仍然有權限；
2. 使用中的 `scenario_library_id == parent_scenario_id`；
3. 使用中的來源雜湊仍等於 repair 目標雜湊；
4. `timeline_id` 沒有改變；
5. `state_revision` 符合 repair 交易的政策；
6. 沒有其他待處理的劇本提交；
7. 沒有待處理的預製角色幸運決定；
8. `resource_bridge.guard_replacement(state)` 允許替換。

以更正語意套用，而不是新劇本語意。

**不要**呼叫 `_new_upload()`。

**不要**：

- 重設 `game_started`；
- 清除時間線；
- 清掉角色；
- 重設目前的房間；
- 清除一般的戰役歷史。

## 27. 兩階段並行模型

外部審查的驗證可能包含檔案解析、PDF 檢查、雜湊與產物發布。不要在整個操作期間持有對話鎖。

使用：

```text
LOCK
  authorize
  capture parent scenario ID/hash
  capture revision/timeline
  verify no conflicting pending operation
UNLOCK

parse + validate + construct candidate
publish immutable derived scenario

LOCK
  re-check authorization
  re-check parent scenario ID/hash
  re-check timeline
  re-check replacement guard
  activate as repair if still valid
UNLOCK
```

如果發布之後狀態已改變：

```text
新版已建立，但遊戲狀態在修復期間已變更，因此沒有自動套用。
```

不可變的衍生劇本可以留在劇本庫裡。

不要僅因為啟用變成過時，就回滾或刪除已有效發布的來源。

## 28. 結果訊息

### 成功：已發布並啟用

```text
✅ 劇本來源修復完成

《The Haunting Scenario trimmed》
修復頁面：2、4、6、7、8、10、14、16、17
新版來源：<new scenario id>

已只替換指定頁面，其餘頁面保持不變。
數值／骰式差異已通過 repair 檔宣告比對。
目前遊戲已切換到修正版，角色、進度與目前位置沒有重置。

NPC／地點索引會依修正版來源重新整理。
```

### 成功：已發布但啟用已過時

```text
✅ 修正版已建立：<new scenario id>

⚠️ 上傳期間目前遊戲狀態已變更，因此沒有自動切換來源。
請由 KP 重新確認後選用這個版本。
```

### 過時的 repair

```text
❌ 沒有套用 repair。

這份檔案是依較舊的劇本來源製作：
expected content hash: ...
current content hash: ...

請重新匯出 repair 工作檔後再修正。
```

### 非預期的數值變化

```text
❌ 第 10 頁的數值差異與 repair 宣告不一致，整份修復沒有套用。

實際新增：1D4+2 × 1
repair 宣告：無

請重新核對原 PDF 後再上傳。
```

## 29. 既有的自由格式 repair Markdown

**不要**嘗試自動匯入這類自由格式的檔案：

```text
# PDF page 2
...
# PDF page 4
...
```

這類檔案是有用的人工審查素材，但沒有安全地綁定到：

- 劇本 ID；
- 父內容雜湊；
- PDF 雜湊；
- 頁面本文雜湊；
- 數值差異；
- 證據。

提供轉換路徑：

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

再把審查過的內容複製進產生的工作檔。

這可避免替某一版 The Haunting 準備的 repair，被默默套用到同標題的另一次匯入。

## 30. 重構 `scenario_source_review`

避免重複實作頁面驗證邏輯。

在可行的地方，把可重用的公開輔助函式從私有函式抽出來。

建議：

```python
scenario_source_review.split_source_pages(...)
scenario_source_review.validate_reviewed_page(...)
scenario_source_review.numeric_delta(...)
scenario_source_review.build_candidate_text(...)
```

或把共同邏輯搬到：

```text
app/scenario_source_repair_common.py
```

CLI 來源審查與 Discord repair 都應該使用相同的：

- 頁面切分；
- 頁面標記保護；
- 邊界框驗證；
- 數值計數；
- 已發布頁面的序列化；
- 候選雜湊。

不要維護兩份有細微差異的驗證器。

## 31. 建議的檔案

### 新增

```text
app/scenario_page_repair.py
app/services/scenario_repair.py
tests/test_scenario_page_repair.py
tests/test_discord_scenario_repair_upload.py
docs/specs/feature/discord_page_level_scenario_repair_design_spec.md
docs/specs/feature/discord_page_level_scenario_repair_design_spec_zh.md
```

如果專案維持英文／繁體中文對照的規格，兩者都要加。

### 修改

可能包括：

```text
app/commands/handlers/uploads.py
app/discord_bot.py
app/services/scenario_lifecycle.py
app/scenario_source_review.py
app/scenario_library.py
app/help_service.py
app/discord_transport/help_ui.py
tests/test_pdf_scenario_lifecycle_integration.py
```

可能還有：

```text
app/trusted_scenario_source.py
app/scenario_templates.py
```

視發布與翻譯失效的處理而定。

## 32. 建議的 API

### `scenario_page_repair`

```python
@dataclass(frozen=True)
class RepairTarget:
    scenario_id: str
    content_hash: str
    pdf_sha256: str
    page_count: int

@dataclass(frozen=True)
class PageRepair:
    page: int
    base_page_sha256: str
    text: str
    page_kind: Literal["text", "map", "image"]
    review_note: str
    evidence: tuple[EvidenceRegion, ...]
    expected_numeric_delta: NumericDelta

@dataclass(frozen=True)
class RepairProposal:
    version: int
    target: RepairTarget
    patches: tuple[PageRepair, ...]

@dataclass(frozen=True)
class RepairCheck:
    ready: bool
    candidate_digest: str
    candidate_text: str
    repaired_pages: tuple[int, ...]
    issues: tuple[str, ...]
    changes: tuple[dict, ...]
```

函式：

```python
parse_markdown_bytes(data: bytes) -> RepairProposal

check(
    proposal: RepairProposal,
    *,
    scenario_id: str | None = None,
) -> RepairCheck

publish(
    check: RepairCheck,
    *,
    reviewer_user_id: str,
    reviewer_display_name: str,
    uploaded_filename: str,
) -> str
```

## 33. 原子性

這個操作有兩個獨立的原子邊界：

### 來源發布

全有或全無。

如果任何被修補的頁面驗證失敗：

```text
publish nothing
```

### 使用中遊戲的啟用

在對話／狀態交易之下全有或全無。

不要在下列更新之前：

```text
scenario_text
```

更新：

```text
scenario_library_id
```

反之亦然。

狀態絕不能指向部分的候選。

## 34. 冪等性

計算：

```text
candidate_digest =
digest(
  parent scenario identity
  normalized repair proposal
  candidate page text
)
```

衍生劇本 ID 由這個摘要決定。

重新上傳時的行為：

### 同一份 repair、已發布、父劇本仍在使用中

回傳同一個衍生 ID，並確保啟用正確。

### 同一份 repair、衍生版本已在使用中

回覆：

```text
這份修復已經套用，沒有重複建立版本。
```

### 同一個檔案、對上較新的父劇本

以過時拒絕。

## 35. 翻譯變體

來源修復會改變正本的來源身分。

綁定到舊來源的既有翻譯／範本變體，不得默默地對修復後的來源變成有效。

行為：

- 在舊劇本之下保留舊的變體資料；
- 把它標記為對新的修復劇本不可用；
- 如果使用中的來源有翻譯變體，通知 KP；
- 之後若要遷移翻譯，使用既有的 `scenario_source_review.rebind()` 流程。

不要依紀錄 ID 自動複製翻譯紀錄。

## 36. RAG 行為

修復後的來源會產生新的來源雜湊。

任何以父雜湊為鍵的 RAG 快取都已過時。

要求：

1. 絕不把來源雜湊為父雜湊的 RAG 索引，當成修復後劇本的權威；
2. 針對新雜湊排程重建／預熱；
3. 重建尚未完成時，用新的正本劇本文字做安全的備援檢索；
4. 不要只因為衍生的 RAG 索引正在重建，就阻擋遊戲開始，這與專案「匯入的增強功能不得妨礙遊玩」的要求一致。

## 37. 效能

預期的 repair 規模很小。

9 頁的補丁不應該重跑：

```text
PaddleOCR
Tesseract
PyMuPDF4LLM full-document extraction
AI PDF repair
```

預期的昂貴工作：

```text
none for source merge
optional asynchronous index/RAG rebuild afterward
```

主要的上傳延遲應該來自：

```text
Markdown parse
hashing
PDF page bound checks
numeric diff
derived publication
state commit
```

目標：在任何選用的背景索引重建之前，通常是不到一秒到數秒。

## 38. 安全／信任邊界

把 repair Markdown 當成不受信任的輸入。

拒絕：

- 路徑穿越；
- 來自檔案內的檔案系統路徑；
- 內嵌的頁面標記；
- 不支援的 schema 鍵；
- 過大的內容；
- 重複的頁碼；
- NaN／Infinity 的證據值；
- 超出來源 PDF 的證據；
- 錯誤的雜湊；
- 錯誤的 PDF；
- 錯誤的頁數；
- 無效的 UTF-8；
- 不支援的 repair 版本。

不要執行 Markdown。

不要使用檔案提供的審查者身分。

不要允許 repair Markdown 參照其他本機檔案。

## 39. 測試

### 解析測試

1. 有效的單頁 repair。
2. 接受 UTF-8 BOM。
3. 拒絕未知的最上層鍵。
4. 拒絕未知的補丁鍵。
5. 拒絕重複的頁碼。
6. 拒絕頁碼 0。
7. 拒絕超過 PDF 頁數的頁碼。
8. 拒絕注入實體頁面標記。
9. 拒絕格式錯誤的證據。
10. 拒絕無效的 `page_kind`。

### 綁定測試

11. 拒絕錯誤的劇本 ID。
12. 拒絕錯誤的內容雜湊。
13. 拒絕錯誤的 PDF SHA。
14. 拒絕錯誤的頁數。
15. 拒絕錯誤的基準頁面 SHA。
16. 針對父劇本 A 製作的 repair，不能套用到子劇本 B。

### 數值安全測試

17. 拒絕未宣告而新增的數字。
18. 拒絕未宣告而移除的數字。
19. 接受已宣告的 `1D40 → 1D4` 變更。
20. 額外的百分比變更會讓整份被拒絕。
21. 一頁上的數值差異不影響其他頁。

### 合併測試

22. 只有被修補的頁面改變。
23. 未被修改的頁面位元組完全相同。
24. 實體標記維持順序且各出現一次。
25. JSON 中的補丁順序不影響候選結果。
26. 拒絕沒有任何變化的 repair。
27. 同一份有效的 repair 具冪等性。

### 地圖測試

28. 有可讀標籤且 `page_kind=map` 的地圖頁被接受。
29. 地圖頁可以清除低文字量的品質警告。
30. 地圖 repair 不會修改場景地圖圖形。
31. 存在原生／可讀文字時，`page_kind=image` 被拒絕。

### Discord 測試

32. `repair_*.md` 路由到 repair 處理器，而不是比較處理器。
33. 一般玩家不能提交 repair。
34. KP 可以提交 repair。
35. 拒絕超過一個 repair 附件。
36. 在待處理的來源替換期間上傳 repair，遵循准入政策。
37. 驗證之後遊戲修訂已過時：發布劇本庫版本，但不自動啟用。

### 生命週期測試

38. 啟用 repair 保留時間線 ID。
39. 啟用 repair 保留 game_started。
40. 啟用 repair 保留已認領的玩家角色。
41. 啟用 repair 保留 HP／SAN／幸運／背包。
42. 啟用 repair 保留目前的地圖位置。
43. 啟用 repair 不呼叫 `_new_upload()`。
44. 新的來源 ID 只在完整交易之後才成為使用中。
45. 舊劇本仍可從劇本庫讀取。

### 解析品質測試

46. 被修復頁面的警告被清除。
47. 未被修改頁面的警告被保留。
48. 載入確認訊息只列出剩下的警告頁。

### 產物測試

49. 父劇本的索引不會被當成權威複製到子劇本。
50. 預熱／重建以子劇本的來源雜湊為鍵。
51. 過時的背景重建不能覆寫較新的 repair。

## 40. 驗收測試：The Haunting

基準：

```text
The_Haunting_Scenario_trimmed
27 physical PDF pages
warning pages:
2, 4, 6, 7, 8, 10, 14, 16, 17
```

步驟：

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

外部審查者完成產生的檔案。

上傳：

```text
repair_the-haunting-scenario-trimmed_01.md
```

預期：

1. 目標來源身分相符；
2. 9 個頁面雜湊相符；
3. 所有證據框有效；
4. 數值差異與宣告的變更相符；
5. 候選包含 27 頁；
6. 只有 9 個目標頁不同；
7. 建立新的不可變劇本 ID；
8. 原劇本維持不變；
9. 被修復的警告頁不再出現在解析品質警告輸出；
10. 使用中的戰役時間線不變；
11. 玩家角色與狀態不變；
12. 目前的房間位置不變；
13. 新的來源雜湊取得新的 RAG／索引建置；
14. 重新上傳同一個檔案不會再建立另一個版本。

## 交付方式

下列每個實作階段各自是一個 pull request、各自審查；在第 5 階段完成之前，狀態維持 `partial`。既有的 `scenario_source_review.publish` 需要涵蓋每一頁的提案，並寫入完全乾淨的解析品質紀錄，所以第 1 階段要抽出共用的頁面邏輯，並新增部分頁面的發布路徑，而不是原封不動地重用 `publish`。

## 41. 實作順序

建議的順序：

### 階段 1 — 確定性的劇本庫修復

1. 從 `scenario_source_review` 抽出共用的頁面驗證。
2. 實作 `scenario_page_repair` 解析器。
3. 實作嚴格的 schema 驗證。
4. 實作來源／雜湊／頁面綁定。
5. 實作數值差異驗證。
6. 實作確定性的候選合併。
7. 實作不可變的衍生發布。
8. 加入單元測試。

### 階段 2 — Discord 上傳

9. 加入 `repair_*.md` 路由。
10. 把 `user_id` 傳進上傳處理。
11. 加入 KP／主持人授權。
12. 加入 repair 服務處理器。
13. 加入成功／錯誤訊息。
14. 加入 Discord 路由測試。

### 階段 3 — 使用中遊戲的更正

15. 加入 `scenario_lifecycle.activate_repair_version()`。
16. 重用更正語意。
17. 加入過時修訂／時間線檢查。
18. 保留地圖位置／玩家狀態。
19. 加入生命週期整合測試。

### 階段 4 — 匯出介面

20. 加入 `/coc repair export warnings`。
21. 加入明確頁碼清單的匯出。
22. 加入說明介面的項目。
23. 在支援的地方把產生的工作檔附加給 KP／DM。
24. 加入匯出測試。

### 階段 5 — 衍生產物更新

25. 針對新的來源雜湊排程索引／RAG 重建。
26. 以雜湊／版本檢查保護重建的提交。
27. 改進修復後的狀態回報。

## 42. 合併條件

只有在下列全部成立時，這個功能才可以合併：

- repair 不會默默地以錯誤的來源為目標；
- 一般玩家不能套用 repair；
- 全頁替換是確定性的；
- 未宣告的數值變化會直接失敗；
- 父劇本不可變；
- 部分的 repair 不能發布；
- 使用中遊戲的更正不會重設戰役狀態；
- 未被修改頁面的警告仍然可見；
- `repair_*.md` 絕不會路由到通用比較；
- 冪等的重新上傳有測試證明；
- 頁面合併本身不需要 OCR／API 呼叫；
- 既有的 `scenario_source_review` 測試維持綠燈；
- 既有的 PDF／Markdown 上傳行為不變。

## 43. 明確的設計選擇

### 為什麼不直接接受自由格式的 repair 檔？

因為自由格式的檔案沒有以密碼學方式綁定到它審查時所依據的來源。

像這樣的標題：

```text
The Haunting Scenario trimmed
```

不足以證明來源身分。

### 為什麼替換整頁，而不是合併段落？

因為實體頁面替換提供確定性的來源紀錄，並避免模糊的文字對齊。

### 為什麼要求數值差異？

因為來源修復正是被 OCR 弄壞的機制數值可能進入正本來源的地方。修復系統必須讓數值變動明確。

### 為什麼建立子劇本，而不是覆寫？

因為 repair 必須可逆、可稽核、可重現，並且對既有戰役安全。

### 為什麼保留目前的遊戲狀態？

因為這個操作修的是劇本來源，並不代表開始一份新劇本。

## 44. 預期的 KP 最終體驗

實作之後，整個流程應該像這樣：

```text
Bot:
⚠️ 第 2、4、6、7、8、10、14、16、17 頁需要核對。

Keeper:
/coc repair export warnings

Bot:
已產生 repair_the-haunting-xxxx.md。
請連同原 PDF 交給外部工具核對，完成後把 repair_*.md 上傳回來。

Keeper:
[uploads repair_the-haunting-xxxx_01.md]

Bot:
✅ 修復完成。
已核對並替換第 2、4、6、7、8、10、14、16、17 頁。
建立新版劇本來源：the-haunting-...-repair-xxxxxxxxxxxxxxxx
目前遊戲已使用修正版；角色、進度與位置未重置。
```

這就是第 1 版預期的契約。
