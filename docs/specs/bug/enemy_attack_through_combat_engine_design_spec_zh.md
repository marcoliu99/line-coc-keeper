# 敵人的攻擊要走戰鬥引擎，不要用舊的防守檢定

[English](enemy_attack_through_combat_engine_design_spec.md) | [文件索引](../../README.md)

類別：`bug`。狀態：**已實作**。基準：`main_v2` 的 `87413ae`。

## 問題

在一次《The Haunting》實跑中，敵人甦醒並攻擊。守密人先呼叫 `offer_npc_attack_defense_choice`（較舊的 NPC 攻擊與防守檢定），之後才開始戰鬥。玩家選擇反擊，攻擊命中，守密人自己擲了傷害；但戰鬥已經開始後，系統拒絕套用這筆傷害，所以玩家被告知「被打中」，HP 卻沒有變。

`offer_npc_attack_defense_choice` 在 managed 戰鬥中本來就會拒絕執行。問題是順序：攻擊在戰鬥還不存在時就先結算了，傷害無處可套。

## 修改

只改文字：

- `app/prompt_builder.py` 的開戰規則補上：任何敵人攻擊之前先 `initialize_combat`；戰鬥開始後，敵人只在輪到它時用 `plan_enemy_turn` 再 `run_enemy_combat_plan` 攻擊；不要用單獨的防守檢定開啟戰鬥，也不要自己擲敵人的傷害。提示刻意不點名那個較舊的工具：既有測試不讓它出現在路由文字裡，以免把模型導向它。
- `offer_npc_attack_defense_choice` 的工具說明（`app/keeper_tools/registry.py`）加上：戰鬥已開始或即將開始時不要用它。

## 不做

不移除這個工具，也不移除舊版戰鬥模式；在 managed 流程之前存下的戰鬥仍然需要它們，要不要移除是另一個決定。引擎不變。

## 測試

沒有：這是提示用字。驗證方式是實跑：敵人甦醒就攻擊時，會先 `initialize_combat`，傷害確實落在目標的 HP 上。

## 後續：移除兩個較舊的工具

在 managed 流程之前存下的戰鬥已經沒有需要保留的（專案負責人確認），所以上面的文字修正，對造成問題的兩個工具改成直接移除：

- `offer_npc_attack_defense_choice`（較舊的 NPC 攻擊與防守檢定）和 `close_legacy_combat` 不再出現在 Keeper 的工具表裡，它們的處理函式，以及只測它們的測試都已刪除。Keeper 不會再在戰鬥還不存在時就結算敵人攻擊，也不會再去關閉舊版戰鬥。
- `offer_check_choice`、`npc_skill_check`、`clear_pending_check` 的說明和提示，不再指向被移除的工具。
- 保留：檢定引擎裡的閃避與反擊機制（managed 戰鬥自己的防守選擇也在用）、`offer_check_choice` 對大成功攻擊者的過濾，以及 `combat.py`、`combat_engine.py` 裡的舊版戰鬥模式。移除那個模式會牽動 `combat.py`（1,765 行）和許多測試，是另一項工作，另以報告評估。
