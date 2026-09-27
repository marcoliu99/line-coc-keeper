# 依 RAG 與角色提供 Executor 工具

[English](bug-executor-tool-list-missing-search-scenario.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 每回合由 tools_for_speaker_role 建立工具，不能使用模組載入時凍結的清單；啟用 RAG 的玩家回合必須有 search_scenario。

2. KP Assistant 的限制與描述獨立套用；即使 schema 未列禁止工具，callback 仍須驗證權限。

3. 目前搜尋接受 query 與可選 source=auto|original；中文命中但缺少必要事實時，仍保留原稿補查能力。

## 流程與介面

```text
回合 -> 目前角色／設定 -> 工具定義 -> 回呼授權檢查
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_tool_gateway_speaker_role.py](../../../tests/test_tool_gateway_speaker_role.py)
- [tests/test_executor_rag_reuse_prompt.py](../../../tests/test_executor_rag_reuse_prompt.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-executor-tool-list-missing-search-scenario.md)
