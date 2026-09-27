# 無歧義的參戰者定位

[English](bug-find-combatant-substring-collision.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 先精確比對，再考慮子字串後備；名字重疊時不能把傷害或效果套到串列中恰好較早的個體。

2. 後備比對歧義時明確失敗，不能任選目標；戰鬥中保留不同顯示名稱與穩定角色識別。

## 流程與介面

```text
目標名稱 -> 精確識別／名稱／別名 -> 唯一後備比對 -> 目標或錯誤
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/combat.py](../../../app/combat.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-find-combatant-substring-collision.md)
