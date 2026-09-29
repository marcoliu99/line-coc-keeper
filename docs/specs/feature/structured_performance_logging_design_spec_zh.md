# 結構化請求與回合可觀測性

[English](structured_performance_logging_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。最初對照 `main_v2` 的 `afe8ace`（2026-09-27）；各 provider 的推理強度欄位契約於 PR #140 更新。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 跨 async 工作與 thread offload 保留 request／turn／agent／provider 脈絡；在實際邊界記錄結果、耗時、請求次數、usage、RAG 與工具 metrics。

2. LOG_ENABLED 預設 false，LOG_TEXT_ENABLED 預設 true；結構化 metrics 與自由文字 log 獨立，關閉 metrics 避免不必要量測工作。

3. 使用白名單安全錯誤／限流欄位，識別預設 hash；診斷拒絕時不能把模型／劇本／玩家正文塞進結構化事件。

4. 回報實際交付完成與工具次數，包含失敗；生成時間、排隊時間與完整回合耗時不同，不能混為一談。

5. 目前 Executor 裁決 log 含固定驗證原因碼；劇本搜尋記錄來源／後備診斷，但不能把命中升格為完整保證。

6. `llm.turn` 和 `llm.request` 依目前對話 provider 記錄設定的 `reasoning_effort`。OpenAI 使用 `KEEPER_REASONING_EFFORT`；Codex 使用 `CODEX_REASONING_EFFORT`，與其 `codex.conversation` 事件一致。沒有推理強度設定的 provider 記錄 `null`。此欄位表示送給 provider 的設定，不是實測的推理 token 數。

## 流程與介面

```text
請求／回合上下文 -> 巢狀 span 與計數器 -> 結果／傳送 -> 有界結構化事件
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/observability.py](../../../app/observability.py)
- [app/logging_config.py](../../../app/logging_config.py)
- [app/providers/retry.py](../../../app/providers/retry.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [app/providers/codex_provider.py](../../../app/providers/codex_provider.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_observability.py](../../../tests/test_observability.py)
- [tests/test_logging_completion.py](../../../tests/test_logging_completion.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/structured_performance_logging_design_spec.md)
