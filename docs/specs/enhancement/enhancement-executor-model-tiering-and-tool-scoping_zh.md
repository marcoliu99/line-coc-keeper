# 歷史 Executor 分級與延後工具範圍設計

[English](enhancement-executor-model-tiering-and-tool-scoping.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已由後續設計取代**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 舊 Executor 專用模型／reasoning 覆寫已在 PR68 撤回；目前 Executor 用共用 provider 設定，OpenAI 對話的 KEEPER_REASONING_EFFORT 預設 medium。

2. 歷史 none／low／模型比較只代表當時組態，不是現行預設，也不能當作仍啟用模型分級的證據。

3. 通用依戰鬥狀態分類工具仍是獨立未實作 spec；目前 OpenAI 戰鬥狀態新鮮度 gate 範圍較小，不能稱作完整動態範圍。

4. 若重提方案，需在現行程式上以足夠樣本及限流紀錄，比較正確率、工具選擇、請求數、輸入量與完整回合耗時。

## 流程與介面

```text
共用 Provider 設定 -> 當前角色工具 -> 有限的狀態限制 -> Executor
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/config.py](../../../app/config.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_config_defaults.py](../../../tests/test_config_defaults.py)
- [tests/test_combat_status_tool_gate.py](../../../tests/test_combat_status_tool_gate.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md)
