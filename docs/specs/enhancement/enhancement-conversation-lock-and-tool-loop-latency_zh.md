# 對話排隊與受限工具迭代

[English](enhancement-conversation-lock-and-tool-loop-latency.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 顯示 Discord typing 並提示等待鎖；每個對話序列化狀態敏感回合，同時避免同步 provider I/O 阻塞 event loop。

2. MAX_TOOL_ITERATIONS 預設 5，HIGH_ITERATION_WATERMARK 預設 4；watermark 是診斷值，與硬性迭代上限不同。

3. Executor 現在以 enable_wrapup=False 回傳驗證交接；一般 provider 仍支援由呼叫端啟用收尾，但不能每回合固定加審稿請求。

4. 巨集戰鬥初始化與通用動態工具範圍仍是獨立 backlog；只有戰鬥快照 gate 不等於完成那些設計。

## 流程與介面

```text
收到行動 -> 輸入中／排隊通知 -> 對話鎖 -> 有上限的工具執行 -> 傳送結果
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/locks.py](../../../app/locks.py)
- [app/commands/router.py](../../../app/commands/router.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/config.py](../../../app/config.py)
- [tests/test_conversation_lock_with_notice.py](../../../tests/test_conversation_lock_with_notice.py)
- [tests/test_llm_turn_wrapup.py](../../../tests/test_llm_turn_wrapup.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-conversation-lock-and-tool-loop-latency.md)
