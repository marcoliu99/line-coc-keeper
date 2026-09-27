# 重用足夠的主動劇本依據

[English](executor_reuse_existing_rag_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. Executor 先檢查本回合已提供劇本內容；已明確涵蓋的事實不應再做同樣或只是換句話說的搜尋。

2. 真正缺漏仍可用 search_scenario；主動結果失敗或缺少時，不能移除明確查詢工具。

3. 中文有命中仍可能需原稿補查；檢索完成不代表已取得所有護甲、限制與後果。

4. 分別量測生成請求、embedding 請求、輸入大小、延遲與正確率；重用本身不證明普遍加速。

## 流程與介面

```text
主動 RAG -> 必要事實檢查 -> 重用／針對性補查 -> 機制處理
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [app/services/prompt_config.py](../../../app/services/prompt_config.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [tests/test_executor_rag_reuse_prompt.py](../../../tests/test_executor_rag_reuse_prompt.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/executor_reuse_existing_rag_design_spec.md)
