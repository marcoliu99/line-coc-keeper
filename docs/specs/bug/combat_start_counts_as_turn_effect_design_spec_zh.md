# 開戰算作該回合的有效結果

## 問題
玩家威脅休眠的敵人（例如 Corbitt 的屍體），守密人用 `initialize_combat` 開戰並寫了起身敘事，但該回合被判為 `resolved` 卻沒有可核對的效果（`missing_resolved_effect`）。玩家只看到「工具的結果沒有通過核對」，看不到敵人起身。

## 修改
`turn_resolution._mutation_evidence` 中，決策有引用且成功的 `initialize_combat` 或 `add_npc_to_combat`，且之後戰鬥為進行中，視為通過核對的變更。這樣的 `resolved` 決策會變成 `resolved_without_check`，敘事會送出。

## 不變
失敗或未被引用的呼叫仍不算；不強制 `sanity_check`。
