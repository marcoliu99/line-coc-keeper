# 戰鬥引擎

[English](combat_engine_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**（[四階段架構重構](architecture_refactor_phases_1_4_design_spec_zh.md)的第 3 階段）。基準為 `main_v2` 的 `2affd06`（2026-10-04），疊在[遊戲狀態交易](state_transaction_design_spec_zh.md)與[檢定引擎](check_engine_design_spec_zh.md)之上。

改動前，`combat.py` 與 `combat_flow.py` 互相匯入（四處函式內的 `from app import combat_flow`，加上頂層的 `from app import combat`），而且 `combat.py` 在 15 個地方反覆詢問「這場戰鬥是不是 managed」，兩種模式的實作混在同一批函式裡。工具、handler、檢定轉接與退出角色流程，各自伸進它們剛好需要的那一個模組。目標是一個只讀一次戰鬥模式、再執行對應實作的入口，並讓兩個戰鬥模組有嚴格的先後順序。不新增模型呼叫，也不新增 tool。

## 結構

```text
Keeper 工具（keeper_tools/combat.py、managed_combat.py）   /coc combat   檢定轉接   退出角色流程
                          \                                    |              |            /
                           app/services/combat_engine.py   CombatEngine.handle(state, action)
                               |  只讀一次模式：IDLE / LEGACY / MANAGED
                               +--> combat_flow.py   （MANAGED_OPS：收據、玩家等待、後續義務）
                               +--> combat.py        （LEGACY_OPS：即時保存；共用基本運算）
                                         \               /
                                          combat_resources.py（葉：暫定資源、結算）   combat_rules.py（葉：純規則）
```

* `app/services/combat_actions.py`：每一種可對戰鬥提出的要求一個不可變 dataclass（`Declare`、`Run`、`Choose`、`Advance`、`ApplyDamage`、`PreviewSettlement`…）。型別參數就是回傳型別。action 只攜帶意圖與穩定識別碼，不帶骰值。
* `app/services/combat_engine.py`：`CombatEngine.handle(state, action)`。每個 action 只呼叫一次 `mode_of(state)`；handler 拿到模式後，需要依模式不同的規則使用 `combat_flow.MANAGED_OPS` 或 `combat.LEGACY_OPS`。只屬於 managed 的 action 在 `IDLE` 會直接拒絕且不寫入；在 `LEGACY` 則丟出一直以來的 admission 錯誤。
* `combat.ModeOps`：回合中依模式不同的步驟——固定時點、傷害、HP 同步、推進與規劃的前置檢查、輪次時鐘、被擋下的推進如何還原。規則以必填參數接收它，不再自己問模式（`combat.py` 對旗標的讀取：15 → 2，一個防止傳錯 ops 的守門，加上明確的舊戰鬥關閉）。
* 分層以匯入圖檢查（含延遲匯入）：`combat_rules`、`combat_resources` 是葉；`combat` 只會到 `combat_resources`；`combat_flow` 建立在 `combat` 上；只有引擎匯入 `combat_flow`。`app.models` 不再到達戰鬥程式碼：`GroupState.retire_active_character` 改由呼叫端傳入回合收尾函式。

## 保留的契約

1. **模式。** 現在開始的戰鬥是 managed。在工作資源管線之前存下來的戰鬥維持 legacy：可以查看、規劃、推進，並以 `close_legacy_combat` 明確關閉；絕不自動轉換，並拒絕 managed action。兩種都能載入、序列化與續玩。
2. **原子 action。** 一個 state 交易包住一個 action，包括它等待的檢定、資源變更與結算項目。需要玩家回答的 action 帶著已保存的等待回傳（`PLAYER_CHOICE`、`PLAYER_ROLL`、`INJURY_CHECK`、`LUCK_DECISION`）；回答以帶同一識別碼的新 action 進來。
3. **不重複結算。** 傷害、彈藥、效果與回合推進都以穩定的 ledger id（`action_id`、`event_id`）為鍵。重試、連點，或同一筆傷害先後經由舊工具與新入口送出，都只重播已存的收據。
4. **不替玩家做選擇**：防禦、Luck、武器與耗材仍屬玩家；autoroll 預設仍為關閉。
5. **待處理控制綁定模式。** 殘留自已消失、被回溯或被轉換的戰鬥的控制，會被拒絕，且不擲骰、不消耗任何東西。

時點（宣告、彈藥、故障、取消、更換武器、參與者倒下、過期防禦）與釘住各自行為的測試，列在[第 3 階段報告](../../refactor/phase3-result.md)。

## 相容性

工具名稱、schema 與輸出、Discord custom id、指令文字與儲存的戰鬥結構均不變；既有存檔不需遷移。原始結果類工具（`apply_combat_damage`、`damage_combatant`…）在戰鬥進行中仍然拒絕。`app.combat.apply_managed_damage`、`managed_single_hit` 與 `is_managed` 已搬走（到 `combat_flow` 與 `combat_resources`），本 repo 內沒有程式碼再從舊位置匯入它們。

## 驗證

`tests/test_combat_engine.py`（B1–B9 與引擎契約，真實 SQLite 加腳本化骰子）、`tests/test_architecture_combat.py`（B10：分層、單一模式讀取、全新 process 匯入），以及呼叫改接引擎的既有戰鬥規則、流程、接線、狀態機與結算測試。報告：[第 3 階段](../../refactor/phase3-result.md)。
