# 交接指名了不存在的檢定時，記錄狀態裡實際有什麼

[English](handoff_mismatch_log_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `3565bed`。來自 2026-10-12 的 Lightless random rerun1（第 74–81、97–100 輪）。

## 問題

同一場戰鬥裡有十二輪以「這次行動尚未完整處理。目前的狀態和這次行動不一致」收場：執行器的 `declare_combat_action` 結算了 Rafael 的攻擊，引擎接著的敵方回合回傳了防禦選擇（`interaction.check_id`），但執行器用 `await_check` 交接那個檢定時被判為 `pending_identity_mismatch`（「檢定／Luck 決定不存在或已過期」），之後每次重試又都重播同一份收據。執行期記錄看得到被拒絕，卻看不出原因：是檢定從未登記、已經不在，還是指錯了持有人。

## 變更

`await_check` 或 `await_luck` 交接指名的編號不在待處理清單時，`turn_resolution.validate_resolution` 會寫一行只含識別碼的文字記錄（持有人與 `waiting_for` 經過 `observability.safe_identifier` 處理，與其他記錄欄位一致）：收到的 `waiting_for`、解析出的持有人、指名的編號、狀態中該持有人預期的編號，以及所有待處理檢定（持有人 → 檢定編號、時間線）和 Luck 決定。行為不變。

## 驗證

`tests/test_turn_consistency_handoff.py`：指名不存在檢定的交接仍是 `pending_identity_mismatch`，記錄行包含指名的編號、預期的編號和待處理項目。
