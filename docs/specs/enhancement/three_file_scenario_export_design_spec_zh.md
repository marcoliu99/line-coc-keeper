# 劇本整備最多匯出三個檔案

[English](three_file_scenario_export_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`enhancement`。狀態：**proposed；檔案政策已確認，待實作核准**。基準：`main_v2` 的 `8e32683`（PR #94 已合併）。分支：`enhancement/three-file-scenario-export`。

目前匯出器把每約 8,000 字來源的翻譯批次，各存成一個 MD。先前核對的 Haunting 有 19 個單元、10 個批次，因此產生 10 個檔案。使用者希望減為最多三個，長期劇本也一樣。

建議契約：**每次匯出一至三個 MD**，不產生空檔案。符合支援資源範圍的長劇本也最多三個；保留全部來源、規則與依據。三個檔案不保證外部 AI 三次回覆或三個翻譯結果檔就完成；必要時分次回傳，匯入累積進度，不洗掉先前成果。

本調整與回合正確性／延遲重構分開，不改遊戲檢索預算或來源忠實度規則。本次只有設計。

## 設計：檔案包與工作批次分開

保留段落 source units，以及約 8,000 字的邏輯批次，將批次放入最多三個實體檔案包。不只是提高 `BATCH_CHARS`，把長劇本變成三個不能拆開完成的工作。

```text
完整劇本 -> 可信來源單元 -> 小型邏輯批次
  -> 依序打包 -> p1.md / p2.md / p3.md（最多）
  -> 使用者將檔案交給外部 AI
  -> AI 每次回傳一個 MD，包含已完成單元/批次
  -> 驗證並合併同一份 export draft
       +-> 未完整：保存進度 + 尚缺 IDs，沿原檔繼續
       `-> 完整：驗依賴/覆蓋 -> 待審候選
                -> 原 preview / approve / select
```

檔案數上限指 export 提供的來源工作檔，不包含原有伺服器 registry 或匯入錯誤報告。不能將十個散檔藏進 ZIP 就當作一個。匯出不刪 `imports` 中無關或先前的檔案。

## 打包演算法與資源界線

1. 沿用 PR #94 有順序的可信來源單元及批次清單；不在檔案邊界拆 unit，不改 source parent、offsets、pages、hashes。
2. 檔案數為 `min(3, 非空批次數)`。將有序批次分成非空連續群組，依預估輸出 UTF-8 bytes，確定性地選取接近剩餘均分量的邊界，且為後續每個檔案保留至少一批。大小相當時優先章節邊界，不保證等大。
3. 每檔各渲染一次可複製指引／範例、package 清單、authoring JSON、所屬全部單元原文。保留相鄰 context 標示，context-only 不算翻譯覆蓋。
4. 發布前檢查實際檔案、registry 及總匯出儲存大小。保留既有限制：20,000 records/units、10 MB 來源文字、20 MB parsed JSON/檔案或 aggregate draft、100 組／200 MB 匯出保存限制。本提案不暗中提高限制。
5. 若資源檢查下無法有效分成最多三檔，先回私人資源報告，不發布半套、不加第四檔、不截文、不假成功。超出支援範圍的巨大輸入另做限制／儲存設計；檔案打包不能保證任意長劇本。
6. 從使用者角度，package files 與 immutable registry 必須全部 ready 才發布。中斷不發成功訊息，staging 檔清理或標記未完成；`files.json` 只列 ready packages。

一批短文一檔，兩批兩檔，十批或數百批在資源檢查通過時三檔。舊十檔 export 繼續可匯入；重新匯出產生獨立 export ID，不偷偷搬移舊進度。

## Authoring v2 與向後相容

新增 authoring envelope v2，runtime schema v4、legacy template v3 不變：

```json
{
  "authoring_version": 2,
  "export_id": "export-<server ID>",
  "package_id": "p1",
  "batches": [
    {"batch_id": "b1", "records": ["same record objects as authoring v1"]}
  ]
}
```

上方 records 字串只是 envelope 示意，正式匯出必須產生合法空白 record objects，另附完整有效的合成範例。每個回傳 MD 只有一個頂層 JSON 區塊，其餘範例用 text fence，原文維持縮排。

伺服器 registry 增加 `packages: {p1: [b1, b2, ...]}`、明確 authoring/packaging versions，沿原 checksum 保護。Package、batch、unit 歸屬及來源 metadata 以伺服器為準；未知／跨 package 的 batch/unit 拒絕。Record ID 跨全部檔案唯一，跨批依賴保留到整體驗證。

繼續接受 authoring v1 及原 `replace_batch` 語意，不把 v1 batch ID 當 package ID，不為打包變更修改 segmentation constant 使舊 export 失效。Runtime records、approval、active variant、PR #94 retrieval 不變。

## 大檔案內的增量匯入

外部 AI 每次可只回完成批次，或一個批次內已完整翻譯的單元。結果省略未完成空白 records，並於進度說明列出。半翻的 unit 不能宣稱已覆蓋；結構覆蓋也不證明忠實度，quote、numeric、privacy、uncertainty、人工審閱仍保留。

V2 依 logical batch 與 stable record ID 合併 draft：

- 新的非重疊 records 追加；相同 records 重送 no-op。
- 更動既有 record，要於該 batch 的 `replace_record_ids` 明列，且每個 ID 必須已存在、在本次附上。未列的衝突拒絕；沒回傳的舊 records 保留。
- 對整份候選 aggregate 驗唯一性、unit ownership/coverage、source-parent，不只驗本次輸入。替換可使 draft 不完整，但不改已 approved/selected variant。
- 先驗完本次全部 batches 才存任何內容；其中一批有錯，舊 draft/candidate 完全保留。抽出共用準備／驗證階段，不能逐批呼叫現有 `import_batch()`，先發布前幾批才發現後面失敗。
- 整次輸入有效後只存一份 candidate draft；全覆蓋且整體驗證通過才發布待審 variant。保留 deterministic content identity 及 draft/variant 寫入間的 crash/replay 保護。
- 完成識別使用確定性的 batch/record 排序，同內容不同到達順序應得到同 variant。

私人回報已完成／全部 units、各 package 進度、尚缺 batch/unit IDs；以驗證後保存資料計算，不信 AI 自報完成。Draft 允許暫缺跨批依賴，完整 candidate 仍須拒絕必要 target 缺失。

## 匯出訊息與網頁 AI 指引

Help 與文字指令共用 message builder，最多列 **三個檔名**、package 編號、邏輯批次數及進度。保留原提示詞，追加明確的可下載檔案要求；以下整段須在匯出訊息中可直接複製：

```text
請依附件內的整備指引完成繁體中文翻譯，回傳可匯入的 Markdown 檔；若需分批，請列出尚未完成的部分。
請實際產生並提供可下載的 .md 檔案。若分次完成，每次都請提供包含本次已完成內容、可直接匯入的 .md 檔，並在回覆中列出尚未完成的 batch_id／unit_id。
```

加一句：「來源最多三檔；長劇本可能需 AI 分次回覆。每檔內有編號批次，可先回傳已完成單元，再依未完成 IDs 繼續。」V2 格式、續做／替換範例放進每份 workbook，不只藏在 bot 文件。沿用現有私人檔案交付，本範圍不新增瀏覽器自動化或平台附件服務。

每份匯出 MD 內的整備指引也要明確要求「產生可下載的 UTF-8 `.md` 檔案」，不能只在匯出訊息提醒。完整或部分成果均使用同一可匯入格式，每檔恰好一個 authoring JSON 區塊；檔名建議保留來源檔名並加 `_zh_partNN.md`，ID 仍以內容與 registry 為準。回覆中的進度說明放在 JSON 外。驗收需同時檢查 Help／文字指令訊息與實際匯出檔內的下載、`.md`、分次交付要求；此測試驗證 bot 的指引，不代表能保證外部網站提供附件。

指引要求保留 package/export/batch/unit IDs、原文引句、機制及保密，不為單次回覆容量摘要。後續結果沿用 export/package，只附新完成 records 或明確 replacements；使用者不需要自己改 JSON 或拆來源檔案。

## 接口與驗證計畫

| 元件 | 修改 |
| --- | --- |
| `app/scenario_authoring.py` | 分開 unit/batch/package 產生、v2 envelope、原子候選合併；保留 v1 |
| `app/scenario_templates.py` | V1/v2 分派、package 訊息／進度、既有 diagnostics/candidate save |
| `app/commands/handlers/system.py` | Help/text 共用私人匯出／匯入進度 |
| `tests/test_scenario_authoring.py` | 打包、增量合併、相容、故障、完整性回歸 |
| 外部整備 references/spec | 更新中英 v2 範例，保留明確標記的 v1 指引 |

必測：1/2/3/10/數百批都不超過三檔；原文串接及 hashes 完整；非空連續分配；含多位元文字的穩定分包；過大 unit/全來源 preflight；失敗不產第四檔；中斷不暴露 ready 半套；UI 最多三路徑及可見提示詞；偽造 package、跨 batch unit 拒絕；同 package 多次部分匯入不需 replacement 就累積；相同重送 no-op；衝突／明確替換；本次一批錯誤不改任何舊資料；亂序 packages／dependencies；全覆蓋不跳過 uncertainty/review；v1/v3 匯入與 v4 retrieval 仍通過；同內容不同順序結果一致；candidate save 中斷保持冪等。

Haunting fixture 應由十來源檔變三檔，19 units 全保留。另外用數百邏輯批次的合成長劇本，驗實際檔案數及來源完整覆蓋，不只 mock 批次數。測試使用暫存 library/imports，不呼叫真實 API。核准實作後執行 authoring/template/help 及完整 regression suites。本次 spec 沒有實作或宣稱測試新 runtime 行為。

## 審查建議

建議採 **最多三個實體檔案包**，保留內部小批次與可續做的 v2 import。資源失敗明確說明，舊 export 繼續可用。實作需一起改 importer／提示詞，不能只改檔案數，否則大檔續做容易覆蓋先前成果。
