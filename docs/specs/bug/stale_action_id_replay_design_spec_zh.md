# 前幾輪已完成的行動 ID 不再被當成重播

[English](stale_action_id_replay_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `f063965`。來自 2026-10-13 的 Lightless Beacon 隨機 rerun3（第 69、70、88、118、119、128 輪）。

## 問題

`declare_combat_action` 的 `action_id` 是執行器自己取的，而且它照自己數的回合來命名，例如 `arthur:round6:melee-youngling`。戰鬥中途它替 Arthur 的攻擊數成第 6 輪，下一輪又數成第 6 輪。引擎認得這個 ID，發現行動者、目標、武器和類型都相同，就重播先前存的收據（同一輪內重試呼叫靠這個維持安全）。但收據是上一輪已完成的攻擊，結果：

- 什麼都沒擲、也沒有待處理檢定（`completed=True`、`dice_rolled=false`，`hit=False` 還是舊的擲骰結果）；
- Greta 那次重播的收據帶著上一輪給 Louis 的防禦選擇，那個選擇早已不在待處理清單，所以交接以 `pending_identity_mismatch` 失敗（#299 的 log 指出缺的是哪個檢定，並顯示 `pending_checks={}`）；
- 該輪以「這次行動尚未完整處理」收場，玩家得重新說一次攻擊。

那次 200 輪裡有 6 輪是這樣丟掉的，全在那場長戰鬥裡；只要執行器數的回合和引擎的對不上，就會一再重用 ID。

## 變更

`combat_flow.declare_action` 先經過 `_id_for_this_round`：ID 若指向一個已完成、而且是更早的回合做的行動（`action['round'] != combat.round_number`），這次宣告就是新行動，以 `<id>:r<回合>` 存放。同一回合內重試這個呼叫會找到新行動並重播它，所以回合內的重試冪等性不變。若別的行動已經佔用那個衍生 ID（呼叫端自己寫了 `<id>:r2`），就改加計數（`<id>:r2-2`），不會搶走別人的紀錄。指向未完成行動、或本回合已完成行動的 ID，行為照舊。工具結果會帶回實際存放的 `action_id`，更早的那個行動原封不動。

## 驗證

`tests/test_combat_state_machine_integration.py`：

- 已完成攻擊的 ID 在下一輪再次使用時，宣告的是新攻擊：有待處理檢定、先前的行動沒變、新行動以 `…:r2` 存放；
- 在那一輪重複同一個舊 ID，會重播新行動，且只留下一個未完成行動。

兩個測試在沒有這項變更時都會失敗。
