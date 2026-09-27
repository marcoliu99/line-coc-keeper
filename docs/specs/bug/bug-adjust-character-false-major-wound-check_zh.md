# 如實回報重傷檢定狀態

[English](bug-adjust-character-false-major-wound-check.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 符合重傷傷害後，區分新建 CON 檢定與阻擋建立的既有檢定／Luck 決定；若未建立，不得宣稱已建立。

2. 一般玩家檢定等待明確觸發；只有啟用 autoroll 才代擲，敘事與下一步按鈕必須符合回傳狀態。

## 流程與介面

```text
傷害 -> 重傷觸發資格 -> 待處理檢定檢查 -> 如實回傳工具結果
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/combat.py](../../../app/combat.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-adjust-character-false-major-wound-check.md)
