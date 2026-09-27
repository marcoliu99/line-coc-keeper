# Executor 動態工具範圍

[English](enhancement-executor-dynamic-tool-scoping.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**待實作提案**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 保留分支只有設計；完整依狀態分層工具尚未實作，現有戰鬥狀態新鮮度 gate 是較小功能。

2. 可行設計必須回合內重算可用工具，包含 start_combat 後加入 NPC；只在回合開始過濾可能隱藏必要後續工具。

3. 每次迭代須維持唯讀劇本查詢、pending／Luck 與角色 callback 權限正確；少暴露工具不能取代 runtime 授權。

4. 舊模型／工具數假設须依目前統一流程重訂；以相同案例、provider 設定與資料比較全量／限縮工具，納入輸入、請求、重試、延遲與正確率。

## 流程與介面

```text
提案：分類當前狀態 -> 選擇工具 -> 執行 -> 每次狀態變更後重新計算
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_combat_status_tool_gate.py](../../../tests/test_combat_status_tool_gate.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/b50b2385c4219cd7b92ffe8c18a8be0bd4690f00/docs/specs/enhancement-executor-dynamic-tool-scoping.md)
