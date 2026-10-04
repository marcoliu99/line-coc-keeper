# 檢定引擎

[English](check_engine_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**（[四階段架構重構](architecture_refactor_phases_1_4_design_spec_zh.md)的第 2 階段）。基準為 `main_v2` 的 `2affd06`（2026-10-04），疊在[遊戲狀態交易](state_transaction_design_spec_zh.md)之上。

改動前，結算一次檢定有三套各帶一份規則的程式：Keeper 工具（`keeper_tools/checks.py`）、`legacy_commands.py` 的 `/coc check`／`/coc luck` 解析器（約 700 行，按鈕呼叫同一組函式），以及戰鬥管轄的版本。等級文字、Luck 選項、SAN → INT 瘋狂鏈、已結算事件種子與其保存都不只一份，其中事件保存在 `keeper.py` 與 `legacy_commands.py` 各寫了一次。目標是只有一個檢定引擎，無論從哪個入口進來，規則都以相同方式套用。不新增模型呼叫。

## 結構

```text
/coc check、/coc luck、按鈕               Keeper 工具（skill_check、sanity_check）
  app/commands/handlers/checks.py          app/keeper_tools/checks.py   <- 轉接：解析、回覆、敘事
              \                             /
               app/checks/service.py        <- 有狀態：把規則套用到交易交給它的 state
               app/checks/rules.py          <- 骰子與算術（走 DicePort），不碰 GroupState
               app/checks/luck.py           <- 誰能花 Luck（政策）
               app/checks/events.py         <- 已結算事件與其後果來源
               app/checks/narration.py      <- 結果文字
                        |
               app/repositories/state_transaction.py
```

* `app/checks/dice_port.py`：`DicePort`（`skill_check`、`sanity_check`、`roll_madness`）。正式環境使用 `ModuleDice`，在呼叫當下才到 `app.dice` 取函式，所以既有對 `dice.skill_check` 的 patch 仍能抵達引擎；測試注入腳本化的 port。`dice.evaluate_roll` 是 `dice.skill_check` 的純函式部分。
* `service.resolve_player_check(state, user_id, text, ...)` 與 `service.resolve_luck_decision(...)` 取得 `state_transaction.mutate` 給的**最新**狀態，就地修改並回傳 `CheckOutcome`。`outcome.changed` 表示交易有沒有東西要寫；拒絕時待處理項目維持原樣、什麼都不寫。`service.autoroll_skill`／`autoroll_sanity` 是 Keeper 的自動擲骰，由同一組輔助函式（等級、Luck 選項、瘋狂鏈、事件種子）組成，所以玩家擲骰與自動擲骰不會不一致。
* 戰鬥管轄的檢定（待處理項目帶 `combat_context`、`postcombat_context` 或 `medical_context`）交給 `ManagedChecks` 轉接器。`app/checks` 不匯入戰鬥程式；`app/services/managed_checks.py`（`ManagedCombatChecks`）位於兩者之上，第 3 階段併入戰鬥引擎。
* `app/commands/handlers/checks.py` 是唯一的指令轉接：一次 `state_transaction.mutate` 結算擲骰，之後（對已結算的檢定）由 Keeper 敘事，最後記錄已結算事件。擲骰在敘事開始前已提交，所以敘事失敗或重試都不會重擲。

## 保留的規則

1. 意圖 → 資格 → 擲骰 → 等級 → Luck 選項或最終結果 → 已結算事件 → 後果，依此順序。所有拒絕都發生在擲骰之前，被拒絕的請求不會擲任何骰。
2. Luck 決定沿用原骰值。餘額在決定當下從最新狀態讀取，所以期間被其他操作花掉的 Luck 會讓這次花費被拒絕（`Luck 只有 N 點…`），不扣任何點數，決定仍保持開啟。
3. 已結算的檢定以 action id `check-event:<event_id>` 記錄一次（與 Keeper 路徑共用）；後果來源在後續回合之前發布且具冪等性。重試的 continuation 不會寫入任何東西。
4. 由另一個檢定引出的檢定（5 點 SAN 損失後的 INT 檢定、被觸發的 Dodge）有自己的 check id，並以 `caused_by_check_id` 連回原檢定；原事件不會被覆寫，其他玩家的待處理項目不受影響。多位玩家各自保有獨立的待處理項目。
5. Autoroll 預設仍為關閉。關閉時，Keeper 的請求只登記待處理項目，不擲骰、不套用後果。

## Luck 政策

由 `checks.luck.luck_allowed` 一個函式說明。理智檢定與決定瘋狂的 INT 檢定不提供 Luck；強推（Pushed Roll）結果為最終；大失敗不能用 Luck 買掉（`app.luck`）；帶 `allow_luck: False` 的待處理項目（戰鬥引擎為重傷、瀕死與穩定傷勢檢定設定）不提供。Luck 的選擇由伺服器依儲存的選項決定，不讀取模型輸入的 `allow_luck`。

**未解決的衝突，本階段未更動：** 需求文件把戰鬥檢定描述為不可用 Luck，但受管理的戰鬥流程一直對攻擊與防禦擲骰提供 Luck，而且 `tests/test_combat_wiring.py` 釘住了這個行為。改變它會改動遊戲玩法，因此記錄在[第 2 階段報告](../../refactor/phase2-result.md)，附上要讓它成立所需的一行修改，留待產品決定。

## 相容性

工具名稱、schema 與輸出、Discord custom id、指令文字，以及儲存的待處理／Luck／事件結構均不變。新欄位讀取時皆為選用：事件與串接的 INT 待處理項目上的 `caused_by_check_id`，以及被觸發檢定的 Luck 決定上保留的 `source_check_id`／`source_event_id`。`app.commands` 仍匯出 `handle_check_command` 與 `handle_luck_decision`。

## 驗證

`tests/test_check_engine.py`（C1–C11，真實 SQLite 加腳本化骰子）、`tests/test_architecture_checks.py`（依賴方向、每個函式只有一個擁有者、舊的重複碼已刪除），以及改接到新擁有者的既有檢定、Luck、對抗、後果與戰鬥接線測試。報告：[第 2 階段](../../refactor/phase2-result.md)。
