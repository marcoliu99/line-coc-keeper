# 只做了查詢的降級回合也能得到那一次重試

[English](fallback_recovery_after_lookup_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `0de529b`。

## 問題

以泛用訊息收場的回合（`executor_no_action`、`unsupported_action`、`no_scenario_evidence`、`invalid_tool_plan`）本來會在玩家看到阻擋之前，多搜尋一次、多做一次 Executor 決定（`supervisor._recover_blocked_turn`）。只有在第一次嘗試沒有動到遊戲時，重試才安全，`turn_fallback.recoverable()` 檢查的就是這件事。

在一次 Dead Boarder 200 回合測試（`1fd3ccb`，22 筆 `turn.fallback` 事件與其工具清單）裡，重試幾乎沒有發生：

| 第一次嘗試 | 事件數 | 有重試 |
|---|---:|---:|
| 沒有任何工具呼叫 | 4 | 4 |
| 至少呼叫了一個工具（其中 18 筆只有 `search_scenario`，或寫入工具加上搜尋） | 18 | 1 |

原因是條件 `not result.observed_outcomes`。每次工具呼叫，包括唯讀的 `search_scenario`，都會附加一筆 `ObservedOutcome`（`tool_gateway`），所以搜尋過一次劇本的回合，被當成改變過東西的回合。這個函式自己的 docstring 寫的是工具「只可以查詢」，程式碼卻不允許。既有測試會通過，是因為它們的結果從來不帶 observed outcome。

## 變更

`recoverable()` 現在忽略唯讀工具（`registry.READ_ONLY_TOOL_NAMES`：`search_scenario`、`search_memory`、`get_character_sheet` 等）的 observed outcome。其他工具的 outcome 仍然讓回合不可重試。原本保護重試的其他條件不變，並且各自獨立判斷：

- 遊戲狀態沒有變（`state_changed`）、沒有擲骰（`dice_rolled`，它涵蓋 `roll_dice` 與傷害骰，雖然這些在登記表中列為唯讀）、沒有結算；
- 待處理檢定與 Luck 決定和之前完全一樣；
- 沒有遊戲事件（背包變更會產生事件）；
- 最多一次重試、最多多一次搜尋，還是失敗就給玩家看得到的原因。

registry 在函式裡才匯入，因為工具 registry 會匯入又匯入 `turn_fallback` 的模組。

## 效果與限制

- 更多降級回合會得到第二次 Executor 決定，並附上補查結果。代價是在本來就要以泛用訊息收場的回合上多跑一次 Executor（中位數約 30 秒）。整回合的期限（`LLM_TURN_DEADLINE_SECONDS`）仍然適用。
- 這些回合有多少會被救回來，目前不知道：同一次測試裡實際執行的 5 次重試救回 2 次，樣本太少，無法估計比率。請在下一次測試用 `turn.fallback` 的 `recovery_result`（或 `scripts/summarize_turn_log.py`）量測。
- 如果模型兩次都因為同樣的原因失敗，重試幫不上忙；它不改提示、工具額度或期限。

## 驗證

`tests/test_turn_fallback.py`：只搜尋過的回合會再跑一次，並記錄 `recovery_attempted=True, recovery_result="recovered"`（這個測試在舊程式上會失敗）；outcome 包含 `record_clue`、`add_carried_item`、`remove_carried_item`、`skill_check` 或 `adjust_character` 的回合永遠不會再跑。既有的情況（狀態改變、擲骰、已結算、事件、待處理）仍然成立。`ruff check .` 與完整 `pytest` 通過。
