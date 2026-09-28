# 開戰與敵人去重的規則收進 `combat.py`

[English](combat_start_in_combat_module_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `a68df95`。

## 問題

有兩條戰鬥規則，在 Keeper 工具裡寫了一次，又在 `/coc combat` 管理指令裡再寫一次。

**開戰前存檔點。** 從非戰鬥狀態開始戰鬥時，要建立 `開戰前` 存檔點（`reason="auto_combat_start"`），讓回溯能回到開打之前。這條規則寫了三次：

- `keeper._ensure_auto_combat_checkpoint`（`app/keeper.py:1643`），由 `start_combat` 和 `add_npc_to_combat` 工具呼叫（`:2842`、`:2851`）；
- 內嵌在 `app/commands/handlers/combat.py:13-19`（`/coc combat start`）；
- 又內嵌在 `app/commands/handlers/combat.py:51-57`（`/coc combat addnpc|addally`）。

handler 版本用 `conversation_id` 組 `event_id`，Keeper 版本用 `state.group_id`。目前兩者值相同，但只是剛好一致。

**存活敵人的去重防護。** 加入一個已經在戰鬥中、尚未倒下的敵人時，必須沿用原本那份血量；即使是用 `/coc index` 的另一個別名再加一次也一樣。這條規則在 Keeper 工具裡（`app/keeper.py:2868-2890`，在有鎖保護的變更內）。handler 又寫了一次（`handlers/combat.py:35-48`），註解寫著 *"Same duplicate guard as the Keeper's add_npc_to_combat tool"*。它依賴的別名解析 `keeper.find_live_enemy_by_any_alias` 和 `_find_npc_index_entry_exact`（`app/keeper.py:1229`、`:1291`）只用到 `combat._normalize` 和 `state.scenario_npc_index`，其實是放在 `keeper.py` 裡的戰鬥邏輯。

**執行順序本來就安全。** router 在 `_conversation_lock_with_notice` 下執行 `/coc combat`（`app/commands/router.py:707`），Keeper 回合持有的也是同一把對話鎖（`_keeper_priority_gate_and_lock_with_notice`，`:566`）。所以 handler 的 `load_state` → 防護檢查 → `save_state` 不會和 Keeper 工具的狀態變更交錯。這次 refactor 只消除重複，不需要改變上鎖方式。

## 決策

兩條規則都由 `combat.py` 負責。呼叫端只負責解析和回覆，狀態處理方式維持現狀。

```python
# app/combat.py
def begin_combat(state) -> None                     # 非戰鬥中就建立存檔點，再 start_combat
def find_npc_index_entry_exact(state, name) -> dict | None      # 搬過來，改為公開
def find_live_enemy_by_any_alias(state, name) -> Combatant | None  # 搬過來，名稱不變
def add_combatant(state, name, dex, hp, *, is_ally, armor, attacks, abilities) -> Combatant | None
    # 去重防護 + 非戰鬥中就建立存檔點 + add_npc；
    # 回傳因重複而沒有新增的那個存活敵人，新增成功時回傳 None
```

`combat.py` 可以匯入 `checkpoints`（後者匯入的 `db`、`locks`、`observability`、`models`、`mutation_admission` 和 repository 都沒有匯入 `combat`），不會造成循環匯入。`event_id` 一律使用 `state.group_id`。

## 範圍

1. 把 `_find_npc_index_entry_exact` 和 `find_live_enemy_by_any_alias` 搬進 `combat.py`。`find_live_enemy_by_any_alias` 保留原名（`combat.find_live_enemy` 這個單一名稱的精確比對已經存在），精確查詢則改為公開的 `find_npc_index_entry_exact`，因為 `keeper` 會呼叫它。`keeper._find_npc_index_entry`（用於 HP 校正的模糊查詢）留在 `keeper.py`，改為呼叫搬過去的精確查詢。`app/` 以外唯一的引用（`tests/test_state_persistence.py`）直接改掉，不在 `keeper` 保留別名。
2. 新增 `begin_combat` 和 `add_combatant`。Keeper 的 `start_combat`／`add_npc_to_combat` 工具在既有的 mutator 內呼叫它們。依索引校正 HP 和相關提示是 Keeper 特有的步驟，留在 Keeper 工具裡。
3. handler 改為呼叫同一組函式，取代內嵌的副本；原本在 router 對話鎖下的 `load_state`／`save_state` 維持不變。
4. 刪除 `keeper._ensure_auto_combat_checkpoint` 和 handler 裡的兩份內嵌副本。

## 測試

既有測試已涵蓋 Keeper 工具的行為（`tests/test_combat_cards.py`、`add_npc_to_combat` 的去重測試），必須在不修改的情況下通過。新增測試：

1. 非戰鬥中執行 `/coc combat start` 和 `/coc combat addnpc`，都只建立一個 `開戰前` 存檔點，且 `event_id == f"combat-start:{group_id}:{revision}"`。戰鬥中執行則不建立。
2. `/coc combat addnpc` 加入存活中的敵人時（直接用名字或透過索引別名），沿用既有的戰鬥者。`addally` 永遠不去重。已倒下的敵人可以重新加入。

## 限制

- 純搬移，不打算改變任何行為。唯一看得到的差異是 handler 建立存檔點時，`event_id` 改用 `state.group_id` 而不是 `conversation_id`，兩者的值相同。
- 審查修正：先前的草稿認為 handler 路徑不是原子性的，並提議公開 `_mutate_and_save_state`。router 的共用對話鎖讓這件事變得不必要，所以已刪除。
