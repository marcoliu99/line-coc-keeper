# 進入戰鬥與傷害工具契約

[English](bug-combat-trigger-prompt-and-damage-tool-ambiguity.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 實際攻擊或危險戰鬥事件觸發 start_combat；無害推擠不必開戰，敘事懷疑本身也不能建立敵人。

2. roll_weapon_damage 與 roll_impaling_damage 計算權威傷害；apply_combat_damage 接收原始傷害並套護甲，apply_final_combat_damage 接收已減免數值。

3. 不能重複扣護甲，也不能以通用角色調整跳過敵人傷害語意；提示詞與工具 schema 必須一致。

## 流程與介面

```text
戰鬥事件 -> 開始戰鬥 -> 檢索敵人規則 -> 擲傷害 -> 僅套用一次
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/combat.py](../../../app/combat.py)
- [app/dice.py](../../../app/dice.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-combat-trigger-prompt-and-damage-tool-ambiguity.md)
