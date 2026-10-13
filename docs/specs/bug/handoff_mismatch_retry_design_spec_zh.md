# 交接指名了沒人開過的檢定，視為裁決要重來

[English](handoff_mismatch_retry_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `137f126`。來自 2026-10-13 的 Lightless Beacon 隨機 rerun4（第 183 輪；同樣的失誤在第 105 輪自行恢復了）。

## 問題

Arthur 要搜索觸礁的船骸。執行器查了劇本之後，回答 `await_check`，指名一個它根本沒開的偵查檢定（`check_id` 是 `check-`，任何人都沒有待處理項目）。交接核對以 `pending_identity_mismatch` 失敗，被歸為 `state_conflict`，所以 Arthur 讀到「目前的狀態和這次行動不一致（例如時間線已更新）」，但其實什麼都沒變，而且這一輪沒有第二次機會（`state_conflict` 不可恢復）。下一位玩家做同樣的行動卻立刻成功。

## 變更

`turn_fallback.classify` 在 `pending_identity_mismatch` 且該輪執行的工具都只是查詢、也沒有任何人有待處理檢定或 Luck 決定時，改回 `invalid_tool_plan`。這種回合沒有動到遊戲，所以拿到 `invalid_tool_plan` 本來就有的一次執行器重試，文字也改成請玩家再說一次行動。若該輪跑過可能改變遊戲的工具（開了戰鬥、重播舊紀錄），或有別人的待處理項目，仍是 `state_conflict`；`cancellation_not_verified` 不變。

## 驗證

`tests/test_turn_fallback.py`：只查過劇本、或完全沒跑工具的 mismatch 是 `invalid_tool_plan`；跑過 `initialize_combat`、或別人有待處理檢定時仍是 `state_conflict`。既有的代碼對應原因表，單純情況改為預期 `invalid_tool_plan`。新測試在沒有這項變更時會失敗。
