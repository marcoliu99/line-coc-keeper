# 沒指定攻擊類型的射擊，就是射擊

[English](declare_action_kind_default_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `d16bb75`。來自 2026-10-12 的 Camp Sunny rerun8（第 77–83 輪）。

## 問題

Jonah 用 .38 左輪射擊怪物。執行器呼叫 `declare_combat_action` 時沒有帶 `action_kind`（工具 schema 裡這個欄位是選填），處理端就把它預設成 `melee`。左輪是 `single_shot` 武器，引擎因此回「Unsupported attack mode」，把戰鬥停在 NEEDS_RULING。執行器接著嘗試修復，也連續失敗（`Unsupported changed attack mode`、`already completed this turn`），整整七輪玩家都只讀到「這次行動尚未完整處理…處理這個行動的工具失敗了」和「Jonah 尚未射出那一槍」。

## 變更

宣告時沒有指定 `action_kind`，處理端就採用所查到武器的攻擊模式（`combat_rules.resolve_weapon`）：槍械是 `single_shot`，拳頭或小刀是 `melee`；查不到的武器維持 `melee`，仍然送去裁定。有明確指定的類型照用，所以錯誤或不支援的類型（例如 `burst`）仍然進入裁定。工具 schema 也寫明了預設規則。

## 驗證

`tests/test_combat_state_machine_integration.py`：.45 Automatic 沒帶類型時是 `single_shot`、不進 NEEDS_RULING；`unarmed` 則是 `melee`。
