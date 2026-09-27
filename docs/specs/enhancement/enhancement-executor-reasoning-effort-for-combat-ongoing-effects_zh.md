# 持續戰鬥效果的推理設定提案

[English](enhancement-executor-reasoning-effort-for-combat-ongoing-effects.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**待實作提案**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 目前未實作戰鬥專用 reasoning 覆寫；專用分級撤回後，原本 Executor-only none 預設前提已過時。

2. 歷史事故比較 none（4 次）、low（7 次）、medium（1 次）；小樣本只支援假說，不足制定可靠政策或代表現行 benchmark。

3. 需定義涵蓋既有效果、新增效果或兩者；在現行 prompt／模型下量測狀態／機制正確率及延遲、token 成本。

4. 不能以 reasoning effort 取代確定性效果生命週期與權威傷害工具；未來覆寫须保留目前 provider 後備與設定行為。

## 流程與介面

```text
提案：識別需處理效果的回合 -> 選擇經驗證的推理策略 -> 比較機制與耗時
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/config.py](../../../app/config.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/combat.py](../../../app/combat.py)
- [tests/test_config_defaults.py](../../../tests/test_config_defaults.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/0a448e157da82d1ae97355b9399106e8991f4060/docs/specs/enhancement-executor-reasoning-effort-for-combat-ongoing-effects.md)
