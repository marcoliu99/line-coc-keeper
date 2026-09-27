# 戰鬥快照重用與批次搜尋提案

[English](enhancement-batch-scenario-search-and-combat-snapshot.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**部分實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 已實作：OpenAI 每次請求的工具 callback 在動態戰鬥快照仍新鮮時限制多餘 get_combat_status；真實變更包含敵人規劃效果，會重新開放刷新。

2. 每次模型請求間重新計算 gate；只在回合開始固定一次清單，無法正確跟隨同回合戰鬥變更。

3. 尚未實作：queries 陣列、批次 query embeddings 及交錯合併多查詢結果；目前工具接受單一 query 與可選 source。

4. 歷史試驗太小，無法證明普遍降低延遲；未來批次搜尋須保留覆蓋並量測額外 embedding 工作，不能假設工具少就請求少。

## 流程與介面

```text
最新戰鬥快照 -> 省略重複狀態工具 -> 變更使快照失效 -> 允許刷新
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_combat_status_tool_gate.py](../../../tests/test_combat_status_tool_gate.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-batch-scenario-search-and-combat-snapshot.md)
