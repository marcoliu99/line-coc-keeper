# 區分 NPC 個體與限制工具對話

[English](bug-add-npc-to-combat-duplicate-name-guard.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 拒絕意外重複加入仍存活的同一敵人；同種多隻生物仍是不同個體，必須使用玩家可辨識的不同顯示名稱。

2. 加入敵人前查詢劇本護甲、攻擊、能力、觸發與使用限制，存入戰鬥卡，不能依賴後續敘事記憶。

3. Provider 工具迴圈有有限次數；一般對話可要求停用工具的收尾，現行 Executor 明確停用 provider 收尾並交給 Narrator，不能記載成每回合固定多一次收尾呼叫。

## 流程與介面

```text
敵人登場宣告 -> 檢查現有名稱／別名 -> 新增獨立個體 -> 有上限的 Provider 迴圈
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/combat.py](../../../app/combat.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_llm_turn_wrapup.py](../../../tests/test_llm_turn_wrapup.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-add-npc-to-combat-duplicate-name-guard.md)
