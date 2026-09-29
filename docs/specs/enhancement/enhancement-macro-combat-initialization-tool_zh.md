# 批次戰鬥初始化提案

[English](enhancement-macro-combat-initialization-tool.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已在分支實作**。2026-09-29 已對齊 `refactor/keeper-tool-registry-final-cleanup`（PR #141）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. `initialize_combat` 已用明確的 `ToolSpec.handler` 註冊。它把開戰與多個敵人加入合併成一次呼叫，減少模型往返次數。

2. 每個敵人須有不同玩家可見識別及完整劇本護甲／攻擊／能力；不能默默丟棄同名陣列項，後備編號須玩家可見。

3. 重用既有戰鬥、重複／別名／HP 檢查，包括逐隻套用權威 NPC 索引的 HP；DEX 先攻已存在，不能新增不相干的先攻擲骰機制。

4. **已與 Marco 確認（2026-09-29）：**
   - **欄位對等：** 每個 `enemies` 項目都支援 `armor`／`attacks`／`abilities`，和 `add_npc_to_combat` 完全一致，不是縮減版 schema。既有 static prompt 已經要求 Keeper 在劇本有寫的情況下填這些欄位；巨集工具少了這些，遇到有護甲或特殊能力的敵人就等於功能倒退。
   - **允許部分成功、逐項回報狀態：** 陣列裡某一項驗證失敗（例如觸發重複名稱防護）不會讓整批呼叫失敗；有效的項目照樣加入，回應裡列出每一項各自的結果，讓 Keeper 可以照實敘事，需要的話只針對失敗的項目重試。
   - **冪等：** 不新增機制。`add_npc_to_combat` 本身現在就沒有——重複呼叫真的會加入第二個戰鬥角色，這是刻意的（見上方「不能默默丟棄」規則）——所以 `initialize_combat` 沿用這個既有行為，不去發明單筆工具本身都沒有的保證。
   - `add_npc_to_combat` 仍是「已經開戰中，單一 NPC 加入」的路徑；`initialize_combat` 只用於一次開戰同時登場 N 隻敵人的情況。

## 流程與介面

```text
有依據的遭遇 -> initialize_combat(enemies) -> 既有開始／新增邏輯（逐項）-> 先攻
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper_tools/combat.py](../../../app/keeper_tools/combat.py)
- [app/combat.py](../../../app/combat.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_keeper_tool_registry.py](../../../tests/test_keeper_tool_registry.py)
- [tests/test_initialize_combat.py](../../../tests/test_initialize_combat.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/c9c1930ee206c30db067f7dca425b57112f4414d/docs/specs/enhancement-macro-combat-initialization-tool.md)
