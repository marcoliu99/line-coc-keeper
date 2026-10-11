# 前往地點、對 NPC 說話是敘事；擲過的骰就是依據；重複的查詢直接回答

[English](executor_travel_npc_and_evidence_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c85efbd`。

## 問題

2026-10-11 的 Scritch Scratch rerun4（27 次 fallback、6 次錯誤句）和 Camp Sunny rerun3（9 次 fallback、5 次錯誤句、1 次內部錯誤）完全沒有工具失敗。玩家損失的回合落在 Executor 決策和分類程式的五種模式。

1. **「去教堂查」「在酒館問村民」「向保羅確認」被回成 `blocked` 或 `deferred`**（SS 27 次裡 15 次、CS 9 次裡 6 次；9 個 NPC 從沒回話、警長站著等了 8 回合）。沒有人在等的 `deferred` 被驗證退回是對的，但重試提示太籠統，回合最後變 `incomplete`，玩家讀到「這次行動尚未完整處理」。同樣的內容在別的回合明明敘述得好好的。
2. **做了理智檢定、掉了 SAN，完成卻被拒絕**（SS 第 5 回合；第 6、39、77 回合是 `record_clue`）：`resolved_without_check` 一定要有字面上的 `scenario_context` 引用，即使這回合的工具已經擲骰或寫入；骰子擲過就不給重試。
3. **劇本搜尋關閉時（預設）每回合 `rag_status` 都是 `"error"`**，所以每次 blocked 都被記成「查不到劇本依據」，recovery 還白搜一次。
4. **查詢一次之後的 deferral 被當成待處理狀態**（CS 第 45 回合）：「請先完成它」說的是不存在的檢定，而且不重試，因為 `classify` 把任何工具呼叫都當成「不是沒做事」。
5. **同一段對話裡重複同一個唯讀查詢，整回合結束**（CS 第 76 回合）：`CodexError('codex_duplicate_tool_attempt')` 在玩家面前變成「系統發生內部錯誤」，只因為一次什麼都不會改的搜尋。

## 變更

- **Executor 的規則寫明，重試再說一次。** `EXECUTOR_INSTRUCTION`：前往劇本已出現的地點、對 NPC 說話、等 NPC 回應或等場景推進，都是敘事（`no_mechanics`），不是 `blocked` 或 `deferred`；`deferred` 只用於戰鬥順位未到或別人的檢定真的擋住；「如果／若…」依目前狀態直接判斷並回答。`final_feedback` 新增 `nobody_waiting`：deferral 被退回而且沒有戰鬥、沒有待處理檢定、沒有 Luck 決定，且這回合的工具都是成功的查詢或遊戲狀態有改變時，重試會被告知沒有人在等，改交 `no_mechanics`（沒改變）或 `resolved_without_check` 並引用那些變更；只擲了骰或工具失敗的回合維持原本的通用提示。
- **這回合擲過的骰或寫入就是依據。** `validate_resolution` 在被引用的工具有擲骰（`resolved`）或有寫入（非唯讀工具、成功，且那一次呼叫自己的前後快照不同）時接受 `resolved_without_check`，不再只認劇本引用。別的工具改了狀態、引用的卻是一次 no-op，仍然不算。
- **沒搜尋是 `disabled`，不是 `error`。** `build_context` 在沒有劇本搜尋工作時回報 `"disabled"`，分類會說 `unsupported_action`，recovery 不再搜。
- **只有查詢的 deferral 是沒做事。** `classify` 改用 `INFORMATION_QUERY_TOOL_NAMES`（`get_*`、`search_*` 這類查詢；擲骰不算查詢）判斷工具呼叫，而不是有沒有呼叫。
- **重複的查詢從對話紀錄回答。** Codex provider 在同一段對話再遇到相同的唯讀 `(name, arguments)` 時，若中間沒有任何會改狀態或擲骰的工具，就把先前的結果以重播的工具紀錄附上並繼續；中間有的話就再查一次，讓模型讀到最新的角色卡或戰況。迭代上限仍然限制一直問的模型。log 記為 `codex.tool.lookup_replayed`。

## 不變更

- 驗證通過的 `blocked` 仍以固定的「無法繼續」句子給玩家；把 Executor 自己的理由說出來是另一個變更。
- 守密人主動推劇情（Camp Sunny 一直沒遇到威脅）不在這裡處理。

## 驗證

- `tests/test_turn_consistency_handoff.py`：沒有人在等的 deferral 被退回時拿到 `nobody_waiting` 回饋，回合以 `no_mechanics` 完成；已有變更時被告知引用變更完成；只擲了骰時維持通用提示。決策引用的理智檢定或 `record_clue` 能讓 `resolved_without_check` 通過；查無結果的查詢、no-op 的線索、或別的工具改了狀態卻只引用 no-op，都不行。
- `tests/test_turn_fallback.py`：`search_scenario` 之後被退回的 deferral 是 `executor_no_action`；`add_carried_item` 或 `roll_dice` 之後是 `unresolved_pending_state`。
- `tests/test_retrieval_prefetch.py`：劇本搜尋關閉時 `rag_status` 是 `"disabled"`。
- `tests/test_codex_decision_context.py`：同一個 `search_scenario` 兩次，主機工具只跑一次，第二次由對話紀錄回答；`adjust_character` 之後再查 `get_character_sheet` 會重新執行並讀到調整後的角色卡。
