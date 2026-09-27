# 待處理 Luck 阻擋衝突的手動檢定

[English](bug-luck-decision-not-checked-non-autoroll.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 即使 autoroll 關閉，skill_check 與 sanity_check 也須檢查待處理 Luck；手動模式不能繞過尚未完成的擲骰決定。

2. 保留既有決定與原擲骰；玩家先決定花費或放棄 Luck，才可建立衝突的新檢定。

## 流程與介面

```text
新檢定 -> 待處理 Luck 檢查 -> 拒絕或登記
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/luck.py](../../../app/luck.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-luck-decision-not-checked-non-autoroll.md)
