# 查詢額度用完的重試，不取代第一次嘗試的答案

[English](keep_the_first_explanation_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `6e76772`。來自 2026-10-13 的 Scritch-Scratch rerun3（第 22 輪）。

## 問題

玩家要到村裡的圖書館查資料，但劇本裡村子沒有圖書館（圖書館在二十英里外的 Appleford）。第一次執行器嘗試搜了三次後弄清楚了，結論是 `blocked`，旁白會把它說成故事裡的回答（「穆斯科比沒有圖書館，最近的在 Appleford」）。`blocked` 的第一次嘗試屬於可恢復的 fallback，所以這一輪又跑了一次執行器。這次重試和整輪共用同一份查詢額度（五次），第二次搜尋就被拒絕，結果是 `incomplete`，並且取代了第一次的結果：玩家讀到的是固定的「劇本裡沒有足夠的內容可以據以裁決這個行動」，而不是第一次已經得出的答案。

## 變更

在 `_recover_blocked_turn` 中，若第一次是驗證通過的 `blocked`（`unsupported_action`），而重試沒有以 `blocked` 結束、仍屬 fallback，就保留第一次的結果。恢復仍記錄為 `unresolved`，旁白照沒有重試時那樣，依第一次的證據解釋。重試再次 `blocked`、或把這一輪解決掉的情況，行為不變。

## 驗證

`tests/test_turn_fallback.py`：第一次 `blocked`、重試的搜尋被拒絕時，由旁白回答，fallback 記為 `unsupported_action`、恢復為 `unresolved`。這個測試在沒有這項變更時會失敗。
