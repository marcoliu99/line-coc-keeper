# `repair_` 上傳的頁面修復範本

[English](scenario_page_repair_template.md)

用這個檔案只修正已載入的 PDF 劇本中的部分實體頁。填好後存成 `repair_<scenario>_01.md`，上傳到對話。Bot 會對照它匯出時的
確切來源版本檢查，只替換列出的頁面，並發布新版本；原版不會被修改。任何一項檢查失敗就整份不套用。設計：
`specs/feature/discord_page_level_scenario_repair_design_spec_zh.md`。

Bot 有提供匯出時，最簡單的做法是用它（`/coc repair export warnings` 或 `/coc repair export 2,4,6`，只限 KP，以私訊傳送）：
它會替你填好 `target` 區塊、頁面雜湊與證據矩形。只有要手寫時才複製這份範本。

## 規則

- 只能有**一個** `json` 區塊，區塊以外的內容會被忽略；任何地方出現未知的鍵都會被拒絕。
- 不要改 `repair_version` 與 `target` 底下的任何內容；它們把檔案綁定到唯一的來源版本。
- `page` 是 **PDF 實體頁碼**（1 是 PDF 第一頁），不是書上印的頁碼。
- `text` 是該頁**完整**的修正後文字，會取代整頁；不支援局部修改。不要寫 `--- 第 N 頁 ---`：頁面標記由系統擁有。
- `base_page_sha256` 是你修改前那一頁文字的雜湊，從匯出檔複製。來源之後有變動的話，檔案會被當成過時而拒絕，要重新匯出。
- `page_kind`：`text`（一般頁面）、`map`（平面圖或示意圖：轉錄可讀的標籤，不要捏造房間描述）或 `image`（只在該頁真的沒有可讀文字時使用，
  此時 `text` 必須是空的）。
- `review_note` 寫你對照 PDF 頁面檢查了什麼、修正了什麼，不能是空的。
- `evidence` 列出你檢查過的實體頁面矩形，`[x0, y0, x1, y1]`，必須在頁面範圍內，每個都要有說明。匯出會填入一個全頁矩形，除非要縮小否則保留即可。
- `expected_numeric_delta` 宣告該頁你改過的每個數字，以移除與新增的 token 數量表示。token 會保留正負號，並把用 `/` 或 `-` 相連的運算元合在一起：
  `+10%` 與 `-10%` 不同、`1/1d6` 與 `1 1d6` 不同、`1-3` 是一個 token。寫小寫、不留空格：`1d4+1`。有改卻沒宣告、或宣告了卻沒改，整份 repair 都會被拒絕。
  沒改任何數字就寫 `{}`。

## 範本

```json
{
  "repair_version": 1,
  "target": {
    "scenario_id": "<從匯出檔複製>",
    "content_hash": "<從匯出檔複製>",
    "pdf_sha256": "<從匯出檔複製>",
    "page_count": 0
  },
  "patches": [
    {
      "page": 1,
      "base_page_sha256": "<從匯出檔複製>",
      "text": "<實體第 1 頁完整的修正後文字>",
      "page_kind": "text",
      "review_note": "<你對照 PDF 頁面檢查了什麼、修正了什麼>",
      "evidence": [
        {
          "bbox": [0.0, 0.0, 595.0, 842.0],
          "note": "<例如：整頁已對照渲染後的 PDF>"
        }
      ],
      "expected_numeric_delta": {
        "removed": {"1d40": 1},
        "added": {"1d4": 1}
      }
    }
  ]
}
```

每替換一頁就在 `patches` 加一個物件，最多 100 個，每頁一個。
