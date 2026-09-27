# 中文整備前的 PDF 來源修復

[English](scenario_source_review.md)

在管理目標劇本庫的儲存庫及 Python 環境執行。這些 CLI 使用本機 PDF 擷取與
渲染，不呼叫翻譯／OCR API。調查既有遊戲時先在副本操作；工作檔含 KP 私密內容。

## 1. 準備證據

```sh
python3 -m app.scenario_source_review prepare SCENARIO_ID /absolute/new/review-directory
```

目錄必須尚不存在。輸出 proposal.md、每個實體頁的 PNG、舊擷取及原生擷取文字。
伺服器另存私密登錄，綁定來源、PDF、manifest 及頁碼。點陣頁可能沒有原生文字，
須對照圖片或外部 OCR 候選轉錄，不能把原生候選當成已校對。

只編輯 proposal.md，與原始頁面 PNG 放在一起。每頁欄位：

- `page`：PDF 實體頁，不能拿印刷頁腳數字替換。
- `text`：完整修正轉錄，保留日期、價錢、百分比、骰式、限制與規則速查。
- `review_note`：核對／更正內容及理由；仍有語意或圖片疑點時不要發布。
- `evidence`：PDF 頁面座標的矩形 `[x0, y0, x1, y1]`，單位為 point，附理由。
  初始提供整頁矩形，可改為更精確的範圍。
- `image_only`：明確保留沒有原生文字的純圖片頁，不編造描述；有可讀的標籤或
  規則，仍須轉錄。

不能為通過數值檢查補裸數字。刪除裝飾字形／頁腳須記錄理由，正文參考頁碼則
保留完整語境。`1D4` 旁的裝飾字形被擷取為 `0`，不代表骰式是 `1D40`。

## 2. 檢查確切提案

```sh
python3 -m app.scenario_source_review check /absolute/review-directory/proposal.md --report /absolute/new-check-report.md
```

報告包含完整修正前後文字與數值次數。`ready: true` 只表示校對資料結構齊全，
不代表程式能證明人或模型轉錄正確；操作者仍須查看證據。check 不改來源。

## 3. 另存新的來源版本

```sh
python3 -m app.scenario_source_review publish /absolute/review-directory/proposal.md --reviewer REVIEWER_NAME --expected-digest DIGEST_FROM_CHECK
```

提案、來源、PDF、manifest 或證據圖片變動都會被拒絕。成功時建立新劇本 ID，
保存來源稽核；舊劇本、匯出、草稿、目前遊戲選擇保留。中文模板不會自動核准。
相同校對者重試同一提案會回傳同一新 ID；新版來源按實體頁分段。

舊的衍生索引、預設角色物件與推測地圖可能仍依據錯誤擷取，因此不複製。
完整 PDF 圖片保留，預設 KP 專用。依正常整備流程從修正版正文重建（適用時用
`/coc index`、`/coc pregens`）；這些正常指令可能呼叫已設定的 API。
不要為重建衍生資料而重新解析原 PDF，否則會把修正覆蓋回去。

## 4. 重新綁定中文草稿

```sh
python3 -m app.scenario_source_review rebind NEW_SCENARIO_ID --old-scenario-id OLD_SCENARIO_ID --old-export-id OLD_EXPORT_ID
```

建立新的普通整備匯出，最多三個工作檔及成果草稿。只有單一舊單元、全文唯一且
完全相同、沒有可能被拆散的關聯／依賴時，才能重用譯文；來源／單元／紀錄 ID
及引述會重新編譯。變更或歧義內容保留在私密 migration-report.md，供人工重譯
與綁定。

完成待翻譯筆後，透過 Help 選檔匯入。這是新 export 的首次提交，不沿用舊
replace_record_ids；之後更正新草稿中已有的紀錄，才列該批次既有 ID。
最終數值檢查與明確核准仍保留；數值清單不能證明全文語意完整。
