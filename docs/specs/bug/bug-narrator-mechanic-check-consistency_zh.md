# Narrator 指示必須符合檢定狀態

[English](bug-narrator-mechanic-check-consistency.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. Narrator 消費權威機制事實，不能以編造成功、失敗或另一個檢定取代工具結果。

2. 核對所有受影響擁有者的 pending 與 Luck，不能只看發言玩家；後續指示必須對應實際待處理擁有者。

3. 未完成行動必須依真實狀態變更與成功擲骰證據，決定是否提及保留變更或禁止重骰。

## 流程與介面

```text
MechanicResult -> 權威事實 -> Narrator -> 確定性的指示檢查
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/services/prompt_config.py](../../../app/services/prompt_config.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [tests/test_narrator_check_consistency.py](../../../tests/test_narrator_check_consistency.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-narrator-mechanic-check-consistency.md)
