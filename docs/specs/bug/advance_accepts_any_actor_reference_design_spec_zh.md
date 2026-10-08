# advance_combat_turn 接受任何角色指稱；拒絕訊息說明該怎麼做

## 問題
實際跑局中，`advance_combat_turn` 被拒 12 次，原因是守密人傳了角色 ID 或名字而不是戰鬥者 ID；`declare_combat_action` 在角色已行動後被拒 39 次，訊息只寫「advance initiative」。每次拒絕都浪費一次工具呼叫，常常整輪都沒完成。

## 修改
- `combat_flow.advance_combat` 用 `combat.find_combatant` 解析 `actor_id`（戰鬥者 ID、角色 ID 或名字）再與當前行動者比對。指稱錯誤時，訊息會指出當前行動者與應傳入的 ID。
- `declare_action` 的「本回合已完成」拒絕訊息，改成要求守密人用該行動者的戰鬥者 ID 和新的 event ID 呼叫 `advance_combat_turn`。

## 不變
仍然只有當前行動者能推進；skip 規則與擁有者檢查不變。
