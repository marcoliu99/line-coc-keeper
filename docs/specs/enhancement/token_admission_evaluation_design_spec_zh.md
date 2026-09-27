# 輸入預算、自適應准入與截斷

[English](token_admission_evaluation_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 估算輸入組成，依 OPENAI_HISTORY_TOKEN_BUDGET（預設 4000）選近期歷史，保留 OPENAI_HISTORY_MIN_TURNS（預設 2）；這是只限歷史的軟預算，權威狀態與儲存歷史不變。

2. 自適應准入預設開啟，從 provider 標頭取得可用 RPM／TPM；保留預估需求、共用冷卻並遵守 LLM_TURN_DEADLINE_SECONDS（預設 180）。

3. OpenAI 各階段輸出上限預設 0，代表這些設定不另設明確上限；不能宣稱已採固定 1000／2000 token 上限。

4. 在執行呼叫前拒絕未完整／截斷工具回應；保留先前已提交狀態與輸出佇列，不重播舊行動。

5. 逐項評估輸入組成、429 次數、排隊時間、請求數、完整回合耗時、完成率及機制／工具正確率；單元測試不會更新歷史 benchmark 數字。

## 流程與介面

```text
量測輸入組成 -> 選取歷史 -> 預留額度 -> 期限內請求 -> 拒絕截斷回應的工具
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/services/input_budget.py](../../../app/services/input_budget.py)
- [app/providers/admission.py](../../../app/providers/admission.py)
- [app/providers/turn_budget.py](../../../app/providers/turn_budget.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/config.py](../../../app/config.py)
- [tests/test_token_admission_experiment.py](../../../tests/test_token_admission_experiment.py)
- [tests/test_adaptive_admission.py](../../../tests/test_adaptive_admission.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/token_admission_evaluation_design_spec.md)
