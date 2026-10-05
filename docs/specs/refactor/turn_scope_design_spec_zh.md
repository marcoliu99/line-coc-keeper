# 把一個回合的鎖集中在一處、加上鎖序測試與「持鎖過久」報告

[English](turn_scope_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `e4c6044`。

## 問題

一個對話有四種鎖可能被一個回合持有：Keeper 優先閘門、對話鎖、Keeper turn 鎖、旁白鎖。交接給旁白的回合會先放掉前兩者再取旁白鎖，所以沒有任何回合會去等一個「排在自己前面」的鎖。這個順序原本只靠文件維持：`router.py` 有十處 `async with _conversation_lock_with_notice(...)` 和 130 行排隊通知與交接的程式；它防的失敗是頻道死鎖到 bot 重啟。見 `docs/architecture/main_v2_architecture_review_zh.md`（F5、F16）。

## 限制：玩家的回合不變

誰等誰、等多久、排隊的玩家被告知什麼，都不變。這些程式照原樣搬移；排隊通知的延遲與文字相同。

## 變更

- **`app/commands/turn_scope.py`** 放 `router` 原本的內容：`conversation_turn`（原 `_conversation_lock_with_notice`）、`keeper_turn`（原 `_keeper_priority_gate_and_lock_with_notice`）、排隊通知與其常數、`run_post_turn_hook`。兩個 context manager 仍然 yield `locks.TurnHandoff`。router 改為 import 它，不再持有這些機制。原本透過 `router` 取用搬走名稱的測試改用 `turn_scope`。
- **router 剩下直接用鎖的地方被列出來。** `tests/test_architecture_turn_scope.py` 釘住它們（sudo 代操作旁白、sudo 路徑的閘門與鎖、長時間劇本操作前後的 Help 版本檢查、經由 `TurnHandoff.mutation_phase_lock` 加入 mutation 階段），並各附理由。新的路由自己取鎖就會讓測試失敗，直到它改走 `turn_scope` 或附上理由加入清單。
- **`tests/test_lock_order.py` 記錄真實的取鎖**（把閘門、對話鎖、Keeper turn 鎖、旁白鎖換成會記錄的子類別），當某個 task 在持有「排在後面」的鎖時又去取前面的鎖就失敗：閘門 → 對話 → Keeper turn → 旁白。涵蓋一般回合（有交接與無交接）、優先閘門路徑、會旁白的指令路由、sudo 式路徑，且多個回合同時進行。另有一個反向順序的對照組證明檢查器真的會失敗。
- **持鎖過久報告。** `TurnHandoff` 在回合取得鎖時設一個計時器；回合在 `LOCK_HELD_WARNING_SECONDS`（預設 `LLM_TURN_DEADLINE_SECONDS` + 60，即 240 秒；`0` 關閉）之後仍持有任何鎖，就記錄 `lock.held_too_long`，帶回合 id（router 在 handoff 上標明）、階段（`mutation` 或 `narration`）與持有時間。回合最後釋放時，`lock.released_after_warning` 說明總共持有多久。**它不會釋放任何東西**：強制釋放會把「卡住」變成「兩個回合同時改狀態」，比起一個現在看得見、叫得出名字的卡住頻道更糟。

## F16：不再依賴時鐘的時間測試

`test_scenario_and_memory_rag_are_gathered_and_one_failure_is_isolated` 原本斷言兩個各 0.08 秒、必須並行的搜尋 `elapsed < 0.15`，在負載下偶爾失敗。現在每個搜尋都等另一個已開始，並斷言兩者都看到對方；依序執行的實作會失敗，慢的機器不會。

## 驗證

`ruff check .`、`mypy app` 與完整 `pytest` 通過。看門狗測試用很短的門檻只為了看到報告發出、用很長的門檻看到它保持安靜，所以負載只會延後報告，不會憑空造出報告。
