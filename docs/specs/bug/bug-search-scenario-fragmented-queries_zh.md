# 以目前事件完整搜尋劇本

[English](bug-search-scenario-fragmented-queries.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 將可預期的 NPC、地點、攻擊、能力、觸發與後果需求合併查詢；優先使用具體名稱和原文別名，避免空泛對話式問題。

2. 每回合沒有硬性一次搜尋上限；足夠的主動檢索結果直接重用，缺少事實仍須補查，中文非空結果不代表完整。

3. 玩家查詢遵守允許章節／事件範圍；KP Assistant 描述允許合理備團問題，仍受實際 context 與隱私政策約束。

## 流程與介面

```text
當前事件 -> 具體的合併查詢 -> 檢查依據 -> 只補查缺少的事實
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/scenario_templates.py](../../../app/scenario_templates.py)
- [tests/test_executor_rag_reuse_prompt.py](../../../tests/test_executor_rag_reuse_prompt.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-search-scenario-fragmented-queries.md)
