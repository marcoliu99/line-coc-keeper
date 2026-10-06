# 批次戰鬥初始化提案

[English](enhancement-macro-combat-initialization-tool.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已在分支實作**。2026-09-29 已對齊 PR #149 合併後的 `main_v2`。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. `initialize_combat` 已用明確的 `ToolSpec.handler` 註冊。它把開戰與多個敵人加入合併成一次呼叫，減少模型往返次數。

2. 每個敵人須有不同玩家可見識別及完整劇本護甲／攻擊／能力；不能默默丟棄同名陣列項，後備編號須玩家可見。

3. 重用既有戰鬥、重複／別名／HP 檢查，包括逐隻套用權威 NPC 索引的 HP。初始名單全部加入後，由 DEX 最高者開始第一回合；既有戰鬥保留目前行動者，不新增先攻擲骰。

4. **已與 Marco 確認（2026-09-29）：**
   - **欄位對等：** 每個 `enemies` 項目都支援 `armor`／`attacks`／`abilities`，和 `add_npc_to_combat` 完全一致，不是縮減版 schema。既有 static prompt 已經要求 Keeper 在劇本有寫的情況下填這些欄位；巨集工具少了這些，遇到有護甲或特殊能力的敵人就等於功能倒退。
   - **允許部分成功、逐項回報狀態：** 某項驗證失敗不影響其他有效項目；逐項結果要指出成功、錯誤，或沿用本批之前已在戰鬥中的敵人。空名單或全部無效不得啟動戰鬥。
   - **重複身分：** 同批每一項都是不同個體，即使名稱正規化後相同或使用劇本索引別名也不能丟失。已在場的敵人可以沿用，但須明確回報。不新增更廣泛的冪等保證。
   - `add_npc_to_combat` 仍是「已經開戰中，單一 NPC 加入」的路徑；`initialize_combat` 只用於一次開戰同時登場 N 隻敵人的情況。

5. 回合交接應把此工具視為安全的遭遇設置，公開觀察結果不可洩漏敵人角色卡。靜態戰鬥路由在一隻或以上已有劇本依據且已啟動的敵人登場時選用此工具（一隻就是只有一筆的清單，開戰只要一次呼叫，搜尋已用掉大半工具額度的回合也負擔得起）；沉睡敵人要等劇本觸發條件成立。

## 流程與介面

```text
有依據的遭遇 -> initialize_combat(enemies) -> 既有開始／新增邏輯（逐項）-> 先攻
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper_tools/combat.py](../../../app/keeper_tools/combat.py)
- [app/combat.py](../../../app/combat.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/services/turn_delivery.py](../../../app/services/turn_delivery.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_keeper_tool_registry.py](../../../tests/test_keeper_tool_registry.py)
- [tests/test_initialize_combat.py](../../../tests/test_initialize_combat.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/c9c1930ee206c30db067f7dca425b57112f4414d/docs/specs/enhancement-macro-combat-initialization-tool.md)
