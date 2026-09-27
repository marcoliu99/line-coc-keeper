# 確定性驗證與可選敘事修復

[English](enhancement-guard-agent.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 總是執行低成本規則驗證器；GUARD_ENABLED 預設 true，控制 LLM 修復階段，不是控制是否驗證。

2. 啟用修復時，修復輸出必須再驗證；用盡或失敗時依定義的安全回覆封閉失敗。

3. GUARD_ENABLED=false 時，目前實作會記 log 並放行無效輸出；不能宣稱停用設定也會封閉失敗。

4. 驗證器針對特定洩漏／格式模式，不判定完整劇本真實性；正典邊界與狀態驗證是不同控制。

## 流程與介面

```text
敘事 -> 確定性驗證器 -> 可選且有上限的修復 -> 再驗證 -> 安全結果
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/guard.py](../../../app/agents/guard.py)
- [app/agents/rule_validator.py](../../../app/agents/rule_validator.py)
- [app/config.py](../../../app/config.py)
- [tests/test_guard_agent.py](../../../tests/test_guard_agent.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-guard-agent.md)
