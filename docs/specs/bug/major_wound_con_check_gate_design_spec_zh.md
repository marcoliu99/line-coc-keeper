# 重傷 CON 檢定不再被無聲丟棄

[English](major_wound_con_check_gate_design_spec.md)

狀態：**backlog**（等待規格審查）。基準：`main_v2` 的 `a68df95`。

## 問題與證據

兩份既有規格訂下了契約：

> Qualifying major wounds register/resolve CON according to check ownership and autoroll.
> （`docs/specs/feature/combat_design_spec.md:21`）

> Registering a new investigator skill or SAN check must inspect both pending_checks and pending_luck_decisions.
> （`docs/specs/bug/bugfix_duplicate_pending_checks.md:13`）

狀態模型中**每位玩家只能有一個待處理檢定**（`pending_checks[owner_id]`）。這是領域規則，不是限制：一位玩家在一局中只擁有一個調查員（`CONTEXT.md` 的 *Player*、*Pending check*），所以用玩家當鍵就等於用調查員當鍵。`/coc autoroll` 關閉時，如果玩家已經有待處理檢定，這時受到的重傷就沒有地方登記 CON 檢定。五條傷害路徑對這個情況有三種不同處理：

| 入口 | 變更前的防護 | 變更中 |
| --- | --- | --- |
| `adjust_character` 工具（`app/keeper.py:2597`） | 若 `char.owner_id in state.pending_checks` 就拒絕這次傷害，但檢查的是**外層** state，也沒看 `pending_luck_decisions`（`:2607`） | `elif … not in target_state.pending_checks`（`:2659`）沒有 `else`，CON 檢定被無聲略過 |
| `apply_combat_damage`／`apply_final_combat_damage` 工具（`app/keeper.py:2939`、`:2952`） | 無 | `_resolve_major_wound_check` 回傳 `None`（`app/combat.py:347`） |
| `damage_combatant`，`delta < 0` 時（`app/combat.py:477`） | 無 | 同上 |
| `resolve_enemy_action` 的攻擊分支（`app/combat.py:1141`） | 無 | 同上 |
| `process_timing` 的效果傷害（`app/combat.py:770`） | 無 | 同上 |

在四條戰鬥路徑上，扣血會被存檔，`major_wound_triggered` 回傳 `False`，玩家和 Keeper 模型都不會知道發生了重傷，**可能造成的昏迷／倒地後果就此消失**。`adjust_character` 要發生同樣的遺失需要競態：在外層檢查之後、鎖內重新載入之前，另一條路徑登記了檢定。`_reject_if_check_already_pending` 的 docstring（`app/keeper.py:1006`）描述的就是技能檢定上的同一種競態。

五條路徑都沒看 `pending_luck_decisions`，所以玩家還在決定要不要花幸運值時，也可能被登記 CON 檢定。調查員會同時處於兩個未解決的狀態，違反幸運值防護的規格。

## 決策：在任何變更之前，原子性地拒絕這次傷害

`adjust_character` 已經採用這個策略：*「原子性地拒絕，讓 Keeper 在既有檢定解決後重試。」* 這份規格把它套用到所有路徑，並移到鎖內：

- 如果這次傷害**會**觸發重傷 CON 檢定，而玩家有待處理檢定**或**待決定的幸運值、且 autoroll 關閉，就拒絕這次傷害。拒絕代表：不扣血、不登記檢定、不存檔。
- 拒絕必須明確回報，附上 `blocked_by` 代碼（`pending_check`｜`pending_luck_decision`），訊息告訴 Keeper 先處理既有檢定，再重新套用傷害。
- 每次拒絕都發出 `observability.event("combat.major_wound.blocked", blocked_by=…, entry_point=…, check_id=…)`，讓「限制」一節的觸發條件可以量測：如果 Keeper 有重新套用傷害，同一回合或下一回合就會出現對同一調查員的成功傷害。
- autoroll 開啟、非重傷的傷害、以及把 HP 降到 0 的傷害，行為都不變。

**考慮過的替代方案：延後檢定佇列。** 先套用傷害，把欠下的 CON 檢定存起來，等目前的檢定清除後再補登記。這樣永遠不會拒絕傷害，但每個會清除待處理檢定或幸運值決定的地方都要加補登記的掛勾（`/coc check` 處理、按鈕回呼、幸運值決定、sudo 取消、回溯）。審查決定：先上線拒絕的做法；只有當 `combat.major_wound.blocked` 事件顯示被拒絕的傷害一直沒有被重新套用時，才寫佇列的規格。見「限制」。

## 範圍

### 1. 兩層都能匯入的單一歸屬判斷

在 `app/check_identity.py` 新增 `pending_check_blocker(state, owner_id) -> Literal["pending_check", "pending_luck_decision"] | None`。這個模組不匯入任何 `app` 內的東西，所以 `combat.py` 能用它，又不會造成 `keeper` ↔ `combat` 的循環匯入。`_reject_if_check_already_pending` 改用它來產生原本的訊息，技能／SAN 的行為不變。

### 2. 戰鬥傷害在變更前先檢查

`apply_combat_damage` 先算出預計的 `final`／`after`。只在目標是 PC、autoroll 關閉、`after > 0` 且 `final >= hp_max / 2` 時檢查 `pending_check_blocker`。被擋下時，在動到 `combatant.hp`、敵人卡或 `_sync_pc_hp` 之前，就回傳 `{"ok": False, "error": …, "blocked_by": …}`。`_resolve_major_wound_check` 保留自己的檢查，但只作為斷言式的後備，回傳明確的被擋結果，不再回傳 `None`。

這一步涵蓋兩個傷害工具、`damage_combatant` 和 `resolve_enemy_action` 的攻擊分支。它們都會把非 `ok` 的結果原樣回傳；攻擊分支會在 `plan["resolved"] = True` 之前返回，所以行動計畫仍可重試。

### 3. 多目標效果先驗證所有目標

`process_timing` 目前對 `__all__` 效果是一個目標一個目標地扣血。如果第二個目標被拒絕，第一個目標已經扣了血，效果卻還沒標記為已處理，重試時第一個目標會再被扣一次。改成：效果傷害只擲一次，先用步驟 2 的判斷檢查所有目標，只要有任何一個被擋，就**所有目標都不扣**。設定 `timing_failed`，不把效果加入 `processed_timings`，和現在處理無法解析的傷害算式相同（`app/combat.py:761`）。

### 4. `adjust_character` 在鎖內檢查

把 `app/keeper.py:2607` 的防護移進 `_apply_attribute_delta`，針對 `target_state`，改用步驟 1 的判斷。把無聲的 `elif` 換成 `_StateMutation(…, should_save=False)` 的拒絕。外層檢查可保留作為便宜的提早返回；如果只是和內層重複，就刪掉。

## 測試

除非另外註明，每個案例都在 autoroll **關閉**下執行，使用真實的 `GroupState` 和真實的變更路徑，只 patch 骰子。

1. 五個入口各自在玩家有待處理檢定時：結果不是 `ok`、`blocked_by == "pending_check"`、HP 和敵人卡不變、既有待處理檢定不變、沒有存檔。
2. 和案例 1 相同，但改為有待決定的幸運值 → `blocked_by == "pending_luck_decision"`。
3. `process_timing` 對兩位 PC 的 `__all__` 效果，只有第二位被擋：兩位都不扣血、效果不在 `processed_timings` 中；擋下的原因解除後再跑一次 `process_timing`，每位 PC 剛好各扣一次。
4. `adjust_character` 競態：外層 `state` 沒有待處理檢定，但 `target_state` 有 → 被拒絕，而不是無聲略過。
5. 每次拒絕都剛好發出一個 `combat.major_wound.blocked` 事件，`blocked_by` 和 `entry_point` 正確。
6. 迴歸：沒有阻擋時，重傷照舊登記 CON 檢定；autoroll 開啟時，CON 立即判定；非重傷的傷害不受待處理檢定影響。

## 限制

- 這個修正依賴 Keeper 在檢定解決後重新套用傷害。如果它沒有，傷害仍會遺失，但是是可見的遺失：模型和日誌都看得到錯誤，而不是無聲的 `None`。如果 `combat.major_wound.blocked` 事件顯示真的發生，上面的延後檢定佇列就是下一份規格。
- 不在範圍內：改變每位玩家能持有的待處理檢定數量。`/coc combat` handler 這裡不需要改：它不造成傷害，而且 router 讓它和 Keeper 回合在同一把對話鎖下排隊執行。
