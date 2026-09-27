# 持續效果傷害與幸運數值隔離

[English](bug-continuing-damage-rolls-corrupt-luck-stat.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 持續火焰、中毒或流血屬於戰鬥效果或正式傷害工具；通用擲骰結果不能變成對其他角色 Luck 的任意調整。

2. Luck 等專用屬性保留各自所有權與修改規則；敘事不能靠猜測調整欄位把 NPC 效果轉到玩家身上。

3. 歷史 none／low／medium 試驗樣本少且限特定情境；戰鬥專用 reasoning 覆寫仍是 backlog，目前正式流程使用共用 provider 設定。

## 流程與介面

```text
已成立效果 -> 權威效果／傷害工具 -> 只修改受影響目標
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/combat.py](../../../app/combat.py)
- [app/models.py](../../../app/models.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-continuing-damage-rolls-corrupt-luck-stat.md)
