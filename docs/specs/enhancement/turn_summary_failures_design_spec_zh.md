# 回合摘要與它的報表會計入失敗的模型請求

[English](turn_summary_failures_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `0de529b`。

## 問題

`scripts/summarize_turn_log.py`（#202）讀的是每個回合寫的那一行 `turn.summary`。對 Dead Boarder 200 回合的日誌（`1fd3ccb`）執行時，它列出 `executor_no_action` 31、`unsupported_action` 14、`unresolved_pending_state` 3、`state_conflict` 2，**沒有 `internal_error`**。同一次測試的 `api-events.jsonl` 有七筆 `llm.turn.failed`（`TimeoutError`／`PermissionError`／`CodexError`，在 52 到 120 秒之後，其中四筆發生在已執行一到四次工具呼叫之後），玩家可見的回覆也出現過七次。報表漏掉玩家看到的失敗，就無法用來判斷一次測試。

程式裡有兩個缺口可以解釋，而且都不是報表的解析問題：

- 一個 `run_turn` **丟出例外**的回合不會走到 `turn_fallback.record`，而那是唯一把原因附到摘要行的地方。摘要行還是會寫（時間線在 `finally` 裡回報），只是沒有原因。
- 摘要行完全沒有提到模型請求：哪些失敗、怎麼失敗、花了多久，以及失敗之前有沒有執行過工具。這些在 `llm.turn.failed` 事件裡，而它只有開啟事件日誌時才存在。

## 變更

- `turn_phases.timeline` 會標記因例外離開的回合：除非已經記錄了原因，否則 `fallback=internal_error`，並加上 `error=<例外型別>`。摘要行多一個 `error` 欄位（正常回合為空）。例外仍然原樣向外傳。
- `scripts/summarize_turn_log.py` 也會從結構化（JSON）日誌行讀 `llm.turn.failed` 事件，並多印一段：有多少請求失敗、依 `agent/error_type` 分類、其中有多少是**已執行過工具之後**才失敗（玩家可能什麼都沒被告知，而變更已經提交）、耗時分佈，以及當標成 `internal_error` 的回合摘要比失敗請求少時的警告，讓缺漏的標記看得見而不是悄悄不見。`--json` 把同樣的數字放在 `failed_requests`。沒有這類事件時（文字日誌，或 `LOG_ENABLED=false`）不會多印任何東西。

## 沒做的

- Dead Boarder 的摘要為什麼沒有顯示 Executor 自己接住的失敗（`classify` 應該會判成 `internal_error`）。這條路徑這次沒有改。新增的警告行會顯示它是否再次發生。
- 用 `turn_id` 把失敗和玩家輸入接起來；輸入刻意不在日誌裡。

## 驗證

`tests/test_summarize_turn_log.py`：從結構化日誌解析失敗事件並忽略其他行、與摘要分開計數、有無這一段的報表、對同時含兩種行的目錄執行 CLI、丟出例外的回合得到 `fallback=internal_error` 與 `error=`、例外之前已記錄的原因會保留。`ruff check .` 與完整 `pytest` 通過。
