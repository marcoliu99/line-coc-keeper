# 設計規格：清掉 `_execute_tool` 裡重複的戰鬥工具分支

> 適用範圍：`main_v2` 分支。純粹的死碼清理，不影響任何執行期行為。

## 背景

在 `docs/npc_attack_latency_design_spec.md`（`investigate/npc-attack-latency`
分支）調查 NPC 攻擊延遲問題時，順手發現 `app/keeper.py` 的 `_execute_tool`
裡有一段完整的分支區塊被貼了兩次。跟延遲調查本身無關，另外開這張票單獨處理。

## 現況

`app/keeper.py` 的 `_execute_tool` 函式裡，以下 4 個 `if name == ...:` 分支
**各自出現兩次**：

- `plan_enemy_turn`（第一份：1481-1484 行；第二份：1524-1527 行）
- `resolve_enemy_action`（第一份：1486-1493 行；第二份：1529-1532 行）
- `apply_combat_damage`（第一份：1495-1506 行；第二份：1534-1545 行）
- `add_combat_effect`（第一份：1508-1522 行；第二份：1547-1561 行）

四個分支連續出現、整塊被貼了兩次（第一份 1481-1522 行，第二份 1524-1561 行，
中間沒有夾雜其他分支）——不是巧合的個別重複，看起來像是某次 merge/rebase
衝突處理時，兩邊的版本都被保留下來，沒有清乾淨。

**系統性檢查過，確認沒有漏掉別的重複分支**：用
`grep -n 'if name == "' app/keeper.py | sed ... | sort | uniq -c` 掃過整個
`_execute_tool` 的每一個 `if name == "..."` 字串，確實只有這 4 個工具名稱各
出現兩次。掃描結果裡還有 `record_established_fact` 也顯示出現兩次，但那是
誤報——`app/keeper.py:1427` 是 `if name in ("record_established_fact",
"record_clue"):` 這一個分支同時處理兩個工具名稱，分支內部合法地比較了兩次
`name == "record_established_fact"`（用來決定要讀 `fact` 還是 `clue`、寫進
哪個欄位），不是兩個獨立的 `if` 分支，不需要處理。

### 為什麼是死碼，不只是重複

`_execute_tool` 是一連串 `if name == "X": ... return ...`的線性檢查，每個
分支命中就直接 `return`。因為第一份（1481-1522 行）在程式碼順序上排在前面，
只要 `name` 是這 4 個字串之一，一定會先命中第一份、直接 `return`，**永遠不會
執行到第二份**（1524-1561 行）。第二份是完全執行不到的死碼。

### 兩份不是逐字元相同——第二份還跟目前的行為不一致

第一份的 `resolve_enemy_action`（1486-1493 行）：

```python
if name == "resolve_enemy_action":
    def _mutate_resolve_enemy_action(target_state: GroupState) -> dict:
        return combat.resolve_enemy_action(
            target_state,
            tool_input["plan_id"],
            outcome=tool_input.get("outcome"),
        )
    return _mutate_and_save_state(state, _mutate_resolve_enemy_action)
```

第二份（1529-1532 行）**少了 `outcome=tool_input.get("outcome")` 這個參數**：

```python
if name == "resolve_enemy_action":
    def _mutate_resolve_enemy_action(target_state: GroupState) -> dict:
        return combat.resolve_enemy_action(target_state, tool_input["plan_id"])
    return _mutate_and_save_state(state, _mutate_resolve_enemy_action)
```

這證實第二份是**舊版**留下的殘留，不是刻意保留的替代邏輯——如果哪天有人不小心
把第一份刪掉、留下第二份，`resolve_enemy_action` 就會悄悄失去 `outcome`
參數，而且不會有任何測試或執行期錯誤提醒，因為死碼本身編譯、執行都正常，
只是「刪錯份」這件事沒有任何保護機制。

## 設計：直接刪掉第二份（1524-1561 行）

- 保留第一份（1481-1522 行）不動——這是目前實際生效、行為正確的版本
  （含 `resolve_enemy_action` 的 `outcome` 參數）。
- 整段刪除第二份（1524-1561 行）。
- 刪除後，`end_combat` 分支（現在的 1563 行）緊接在 `add_combat_effect`
  第一份（現在的 1522 行）後面，跟刪除前的程式碼順序邏輯一致（`end_combat`
  本來就只有一份，不受影響）。

不需要改動 `app/combat.py`、tool schema、prompt 文字、任何測試對這 4 個工具
既有行為的斷言——這純粹是刪掉永遠執行不到的程式碼，第一份（唯一實際生效的
版本）逐字元不變。

## 測試計畫

- 刪除前後，跑一次全套既有測試，確認 `plan_enemy_turn`／`resolve_enemy_action`／
  `apply_combat_damage`／`add_combat_effect` 相關的既有測試（`tests/
  test_combat_cards.py` 等）行為不變（regression，預期本來就會過，因為改動
  的是永遠執行不到的程式碼）。
- 不需要新增測試——沒有新行為，只是刪除不可達的程式碼。可以考慮加一個簡單的
  static check（例如 grep 或 AST 檢查 `_execute_tool` 裡沒有重複的
  `if name == ...:` 字串），防止未來又發生同樣的合併疏漏，但這是加分項，
  不是這次範圍的必要條件——先跟你確認要不要順便加。

## 待確認

要不要順便加一個簡單的測試/腳本，防止 `_execute_tool` 裡未來又出現重複的
`if name == "..."` 分支（例如掃描這個函式的原始碼、斷言同一個工具名稱只出現
一次）？這個小工具本身不難寫，但屬於「順便做」，不是這次清理的必要部分，先問過
你再決定要不要放進這次範圍。

## 結論

已實作：刪除第二份死碼（原 1524-1561 行），第一份（含 `resolve_enemy_action`
的 `outcome` 參數）逐字元不變。刪除後 `plan_enemy_turn`／
`resolve_enemy_action`／`apply_combat_damage`／`add_combat_effect` 各只剩
一份，`end_combat` 緊接在後，跟預期一致。`py_compile` 通過，全套測試
240 題跑過（本 worktree 沒有 `investigate/npc-attack-latency` 分支才有的
`test_npc_attack_latency.py`），只有 `test_logging_completion.py` 既有、
與此改動無關的 2 個失敗（`reply_message_count`／`reply_edit_count`
KeyError），跟改動前的基準線一致，沒有新增回歸。

「待確認」的防重複 static check 後續確認要加，已補上
`tests/test_execute_tool_no_duplicate_branches.py`：用 `ast` 掃
`_execute_tool` 的原始碼，統計每個 `if name == "...":` 分支的工具名稱，斷言
沒有任何名稱出現超過一次（`if name in (...)` 這種合法共用分支的形式不算）。
有用一段合成的重複程式碼手動驗證過這個檢查真的抓得到這次清掉的那種重複模式。
