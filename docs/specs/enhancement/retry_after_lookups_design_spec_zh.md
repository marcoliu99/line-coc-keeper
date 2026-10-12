# 只做過查詢就失敗的回合會再跑一次，未完成的回合會記下原因

[English](retry_after_lookups_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c46e341`。來自 2026-10-12 的測試重跑（Camp Sunny rerun7）。

## 問題

Camp Sunny 第 81 輪：玩家對 Billy 開槍，卻收到「系統發生內部錯誤」。執行器先呼叫了 `get_enemy_stat_block`（只是查詢），接著它的決定連兩次被判無效（`codex.decision.rejected invalid_schema`，然後 `codex_invalid_decision`），於是回合以 `internal_error` 退回，`recovery_attempted` 是 false。

`turn_fallback.recoverable` 本來就會在「再跑一次執行器不會讓任何事發生兩次」時，把失敗的回合重試一次；但 `internal_error` 只允許「還沒跑任何工具就失敗」的請求。查詢不會改變遊戲，不該因此放棄重試。

## 變更

- `_failed_before_any_tool` 改為 `_failed_without_a_change`：`execution_health` 為 `failed` 或 `partial` 的 `internal_error` 可以重試。`recoverable` 其他條件都留著（沒有狀態變化、沒有擲骰、待處理檢定與 Luck 維持原樣、沒有事件），另外多要求 `tool_calls` 裡每個工具名稱（不只是觀察到的結果）都是唯讀工具，所以跑過會改東西的工具就不會重試。`recovery_required`（狀態已變）永遠不重試。
- 回合以 `incomplete` 或 `blocked` 結束時，`executor.resolution` 會記下模型自己給的 `reason`（截到 240 字）。重跑的紀錄裡看不出這些回合為什麼放棄（Camp Sunny 第 67、76、80 輪，Scritch-Scratch 第 57、69、81、93 輪），下次就看得到了。

## 沒有改的

- 執行器在查過劇本後判 `blocked` 的回合（Scritch-Scratch 四次要求村裡沒有的圖書館）仍回固定的「目前無法繼續」。同一個要求之前已經在故事裡被回答過四次；固定文字是守密人不編造答案的設計。

## 驗證

`tests/test_turn_fallback.py`：只做過查詢就失敗的回合會再跑一次並記為 recovered；跑過會改東西的工具（物品、擲骰、狀態、事件、非唯讀工具）後失敗的回合永不重跑。
