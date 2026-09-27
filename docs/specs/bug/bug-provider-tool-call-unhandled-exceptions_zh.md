# Provider 格式錯誤與部分失敗處理

[English](bug-provider-tool-call-unhandled-exceptions.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. OpenAI function-call JSON 格式錯誤必須受控處理，不能直接使整個工具迴圈崩潰；截斷回應中的工具在執行前就應被攔截。

2. 工具成功後 API 失敗，仍須保留已提交狀態及排隊的私訊／圖片；重試模型請求不代表可重播已提交工具。

3. 舊 run_turn 例外處理屬歷史；目前失敗邊界為 Executor、Narrator／Supervisor 與獨立 KP Assistant。

## 流程與介面

```text
Provider 輸出 -> 解析／驗證 -> 工具結果或安全失敗 -> 保留已提交效果
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [tests/test_async_provider_contract.py](../../../tests/test_async_provider_contract.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-provider-tool-call-unhandled-exceptions.md)
