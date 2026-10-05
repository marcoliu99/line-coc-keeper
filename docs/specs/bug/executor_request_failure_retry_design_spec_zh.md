# 沒執行任何東西的 Executor 請求失敗會重試一次，清理動作不再蓋掉原本的失敗

[English](executor_request_failure_retry_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`bug`。狀態：**已實作**（重試與清理）；工具額度的發現**僅為提案**。來源：五人 200 輪 Dead Boarder 跑測（`NARRATION_OUTSIDE_MUTATION_LOCK=true`；7 筆 `llm.turn.failed`、7 次玩家可見內部錯誤）。基於 `main_v2` 的 `0de529b`。

七次 Executor 請求直接失敗。其中三次**完全沒呼叫工具**：什麼都沒做，再跑一次不可能重複任何事，玩家卻只看到「系統發生內部錯誤」，得重打一次行動。另有一次顯示的是 `PermissionError`，而不是實際發生的逾時。

## 證據

| 事件 | 事實 |
| --- | --- |
| `llm.turn.failed` | 7 筆：4 筆發生在 1–4 次工具呼叫之後、3 筆沒有工具呼叫；錯誤類型 TimeoutError、PermissionError、CodexError；耗時 52 秒到 120 秒 |
| 第 122 輪 `PermissionError` | Executor 等 Codex 120 秒後逾時。清理時 `os.killpg(pid, SIGTERM)` 收到 `EPERM`，而 `codex_transport.stop_process` 只抑制 `ProcessLookupError`，於是清理的錯誤蓋掉了逾時 |

## 規則

1. **清理不蓋掉失敗。** `stop_process` 對行程群組發訊號；遇到 `ProcessLookupError` 或 `PermissionError` 就改對子行程本身發訊號，並同樣忽略這兩種錯誤。最後等待子行程結束有上限（5 秒）；完全無法對它發訊號的子行程會記為 `codex.process.unkillable` 後放著不管，所以清理既不會讓回合卡住，也不會掩蓋導致它的失敗。
2. **沒執行任何東西的請求重試一次。** 原因為 `internal_error` 的回合，**同時**滿足以下條件才可復原：`execution_health == "failed"`（Executor 只有在遊戲狀態也沒變時才會設成這個值）、沒有任何工具呼叫紀錄、沒有觀察到的結果，且既有守門都通過（狀態未變、沒擲骰、沒有事件、待處理檢定與 Luck 不變）。任何工具呼叫之後的失敗仍是 fallback，因為那份狀態不能重做。
3. **剩餘時間夠才重試。** 重試與原請求共用回合期限（`LLM_TURN_DEADLINE_SECONDS`，180 秒）。剩餘不足 `TURN_RETRY_MIN_REMAINING_SECONDS`（預設 45）就跳過；跑不完的重試只會讓玩家對同樣的失敗等更久。重試啟動前會再檢查一次，因為復原搜尋可能已經用掉這段餘裕。因此 120 秒的 Codex 逾時通常不重試（剩 60 秒、需要 45 秒），52 秒的失敗會重試。
4. **同一個開關、同一個上限。** `TURN_FALLBACK_RECOVERY_ENABLED` 可關閉；沿用「每回合最多重試一次、不重複」的上限。`turn.fallback` 列和其他復原一樣記錄 `recovery_attempted` 與 `recovery_result`。

## 工具額度的發現（本次不改）

`CODEX_TIMEOUT=120` 是**整個回合**的期限，在 `run_conversation` 設定一次，不是單次請求的逾時；每次 Codex 請求約 25–30 秒。`MAX_TOOLS_PER_TURN=4` 與 `MAX_TOOL_ITERATIONS=5`（最後一輪沒有工具）會在四次查詢後強制給出不含工具的最終答案，Executor 因而回 `model_incomplete` → `executor_no_action`。四個額度都花在 `search_scenario` 的回合，就沒有額度留給執行行動的工具。

後續提案（需要決定）：把最後一個工具額度與最後一輪保留給會改變遊戲的工具（`READ_ONLY_TOOL_NAMES` 以外的全部），讓查詢不會擠掉行動。這次不做，因為它會改變一回合可查詢的次數，應先量測。

## 測試

- `tests/test_codex_transport.py`：群組收到 `EPERM` 時改對子行程發訊號；兩種錯誤都消失時被忽略；清理被拒時，逾時仍能從 `Process.close()` 傳出。
- `tests/test_turn_fallback.py`：任何工具之前失敗的請求會再跑並可復原；失敗的重試不重複；工具呼叫之後的失敗、已有觀察結果、`partial` / `recovery_required` 健康狀態、狀態改變或有事件時都不再跑；期限剩餘太少不重試；復原開關有效。
