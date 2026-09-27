# 效果與參戰者的防禦式反序列化

[English](bug-effectstate-combatant-missing-defensive-parsing.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. EffectState 與 Combatant 沿用護甲／攻擊／能力規則的已知欄位過濾，未支援中繼資料不得使讀檔崩潰。

2. 保留支援欄位、預設值與巢狀規則轉換；防禦式解析不代表可以編造缺少的能力或數值規則。

## 流程與介面

```text
存檔／工具字典 -> 過濾已知欄位 -> EffectState 或 Combatant
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/models.py](../../../app/models.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-effectstate-combatant-missing-defensive-parsing.md)
