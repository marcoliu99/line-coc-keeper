# 無論日誌設定為何，每回合一行摘要

[English](turn_summary_log_design_spec.md)

狀態：**implemented**。基準：`main_v2` 於 `27a53e2`。

## 問題

玩家等了多久、時間花在哪裡，原本只記在結構化事件 `turn.phases`，而 `LOG_ENABLED` 為 false 時 `observability.event` 會直接丟掉它。`LOG_ENABLED` 預設為 false，是因為它會讓每次呼叫多出計時器、計數器與 JSON 內容。所以用預設的 `.env.example` 部署時，根本看不到玩家等了多久（架構審查 F2）。通用的「內部錯誤」訊息也沒有給 KP 任何可以在日誌裡搜尋的線索（F7）。

## 變更

- `app/services/turn_phases.py` 在玩家等待的回合結束時（`turn` 與 `continuation`；背景維護不算）對 `app.turn` logger 輸出一行純文字：

  ```text
  turn.summary turn_id=… kind=turn route=gameplay_action short_circuit=… campaign=… wall_ms=… queue_wait_ms=… retrieval_ms=… memory_ms=… executor_ms=… tool_ms=… continuation_ms=… narrator_ms=… other_ms=… fallback=…
  ```

  各 `*_ms` 是 `turn.phases` 已經算好的「獨占時間」，每個階段都有歸類（`retrieval` 與 `memory` 各自合併同類階段），所以加總等於 `wall_ms`。`route` 來自 supervisor 的路由決定（兩種在路由之前就返回的回合也會標記：被更正擋下的回合，以及直接從狀態回答持有中 Luck 決定的回合，後者另外帶 `short_circuit=pending_luck`）、`fallback` 來自 `turn_fallback.record`，兩者經由 `turn_phases.note` 附上（沒有時間軸時是空操作）。這一行只有時間與識別碼，沒有玩家 id 也沒有任何文字。它以 INFO 寫到文字通道，所以在預設的 `LOG_TEXT_ENABLED=true` 下就會出現，不需要新設定。寫入失敗會與其他時間軸回報一樣被吞掉，不會影響回合。
- 通用的內部錯誤回覆結尾加上 `（代碼 xxxxxx）`，是該次請求 id 的最後六個字元（這次請求的每一行日誌都帶有該 id），KP 可以直接搜尋。日誌完全關閉時沒有 id，文字維持不變。

## 成本與玩家路徑

`_log_summary` 每回合約 4 µs（實測）；它格式化的摘要本來就會被算出來給事件使用。沒有新增 `await`、鎖、模型請求，除了內部錯誤回覆多六個字元的代碼外，也沒有改變任何玩家看得到的文字。

## 驗證

`tests/test_turn_summary_log.py` 與 `tests/test_turn_phases.py` 中一個 supervisor 層級的測試：`LOG_ENABLED=false` 時仍有這一行、寫出路由與 fallback 原因、不含玩家 id、不處理維護、續擲標為 continuation，寫入失敗不會讓回合失敗。
