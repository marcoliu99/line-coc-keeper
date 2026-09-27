# 共用重試與請求准入

[English](enhancement-llm-rate-limit-and-turn-latency.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 對分類為暫時性失敗採有限指數退避與 jitter，遵守可用 retry-after；不能重試任意程式錯誤或重播工具。

2. OpenAI 並行預設 3；自適應准入已實作且預設啟用，由回應標頭推導的共用 RPM／TPM 預算與冷卻補充 semaphore。

3. 回合期限限制累積等待；重試／排隊診斷區分觀測配額與猜測原因，單一 429 不能證明撞 RPM 或 TPM。

4. 准入限目前程序，不是 Redis／分散式佇列，也未改用 Batch API；不能對所有帳號假設固定 180000 token 配額。

## 流程與介面

```text
請求預算 -> 共用准入／冷卻 -> API -> 分類結果 -> 期限內重試
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/providers/retry.py](../../../app/providers/retry.py)
- [app/providers/admission.py](../../../app/providers/admission.py)
- [app/providers/turn_budget.py](../../../app/providers/turn_budget.py)
- [app/config.py](../../../app/config.py)
- [tests/test_llm_retry.py](../../../tests/test_llm_retry.py)
- [tests/test_adaptive_admission.py](../../../tests/test_adaptive_admission.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-llm-rate-limit-and-turn-latency.md)
