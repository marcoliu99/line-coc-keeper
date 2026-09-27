# 原生非同步 provider 與 I/O 契約

[English](async_provider_performance_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`refactor`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 所有對話 provider 提供 agent 使用的原生 async 契約；阻塞的本地工具／狀態工作在 gateway 邊界卸載，權威變更仍依序執行。

2. 明確限制請求／工具／embedding／Discord timeout 並傳遞取消；預設請求 60 秒、工具 30、embedding 20、Discord 10。

3. 共用 async client 在設定的寬限期內關閉（預設 5 秒）；取消任務不能轉成成功正典提交。

4. 劇本 embedding 預熱為可選且受限，預設關閉、並行 1；這是索引預熱，不是背景劇本翻譯。

5. 舊 legacy 對話路徑已不存在；共用重試、自適應准入與回合期限透過現行 adapter 套用，async 本身不保證加速。

## 流程與介面

```text
非同步 Agent -> 有界非同步 Provider 請求 -> 依序執行工具回呼 -> 有界後續處理 -> 關閉
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/providers/anthropic_provider.py](../../../app/providers/anthropic_provider.py)
- [app/providers/gemini_provider.py](../../../app/providers/gemini_provider.py)
- [app/providers/retry.py](../../../app/providers/retry.py)
- [app/providers/client_lifecycle.py](../../../app/providers/client_lifecycle.py)
- [app/async_utils.py](../../../app/async_utils.py)
- [tests/test_async_provider_contract.py](../../../tests/test_async_provider_contract.py)
- [tests/test_embedding_timeout.py](../../../tests/test_embedding_timeout.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/async_provider_performance_design_spec.md)
