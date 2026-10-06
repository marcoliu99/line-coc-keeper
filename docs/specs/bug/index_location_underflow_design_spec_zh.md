# 不完整的地點索引重建結果，不會取代有效的索引

[English](index_location_underflow_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `6de8b31`。

## 問題

NPC／地點索引由 LLM 抽取，結果並不穩定：《The Haunting》的文字有 `LOCATION 1:` 到 `LOCATION 9:`，重複執行 `/coc index` 卻依序得到 8、9、8 個地點。每次結果都直接寫入並覆蓋上一版，一次少抽就把完整的索引降級了。

## 修改

- `scenario_index.detect_numbered_location_sequence` 讀取以 `LOCATION n:` 開頭的行（可有 `#` 前綴、不分大小寫）的編號。`Proceed to Location 2, 3 or 4`、`see Location 5` 這類內文不算標題。只有編號剛好是 `1..N`（至少兩個）時才啟用；有缺號、重複、不是從 1 開始或沒有標題時略過，行為與以前相同。
- `scenario_index.location_index_underflow`：抽出的地點少於 `N` 就拒絕，`N` 個以上通過。記錄 `scenario.index.validation` 事件（`status` pass/reject/skip、期望／抽出／舊索引數量、標題編號、`reason`），不記錄劇本內文。
- `/coc index` 在賦值前檢查 `GroupState.scenario_text`，也就是守密人實際讀到的文字（已套用頁面修復）。被拒絕時不 commit：保留上一版 NPC 與地點索引、回覆說明，指令仍算成功。沒有舊索引時不寫入不完整索引，也不虛構地點。commit 仍是依載入時 revision 與 timeline 的一般快照 commit。
- 上傳 PDF／Markdown 對解析後的文字做同樣檢查。抽取不完整時索引存為空並提示未寫入；匯入與開局不受影響。`scenario_lifecycle._repair`（重新解析的修正路徑）不讓空索引取代執行中劇本的索引。

## 不做

不重試、不投票、不加第二個抽取器、不改 prompt、不做確定性的地點解析，也沒有新增 LLM 呼叫。`ROOM n` 不是地點標題。

## 測試

`tests/test_location_index_guard.py`：標題偵測（各種格式、內文、缺號、重複）、通過／拒絕／略過與記錄欄位、`/coc index` 有無舊索引及套用頁面修復的情況、上傳提示，以及修正路徑保留執行中的索引。
