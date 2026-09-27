# 權威回合狀態交接

[English](log_backed_turn_consistency_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 提供目前待檢定、Luck 決定、背包、戰鬥／先攻及近期已結算事件；摘要不能覆蓋當前狀態。

2. Executor 在既有回應回傳結構化裁決與證據引用；Python 以真實狀態和工具事件核對完成、等待檢定、暫緩、取消與未完成。

3. 完成變更須有可觀測工具／狀態證據；物品交接若先加給接收者再從交付者移除，最終背包與事件一致時也應通過。

4. 成功唯讀查詢只有在觀測遊戲狀態未變時才能支持 no_mechanics；失敗、未知、骰子或交付工具不自動算唯讀證據。

5. Provider 失敗仍保留已提交效果與排隊輸出；擲骰指示須有成功結果，固定驗證原因碼支援診斷且不記錄劇本文字。

6. 不新增固定 LLM 審稿；語意是否足夠仍依劇本證據與模型判斷，狀態一致不代表劇本解讀必然正確。

## 流程與介面

```text
當前狀態 -> Executor 工具 -> 結構化裁決 -> Python 驗證 -> Narrator -> 下一步指示驗證
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/services/turn_context.py](../../../app/services/turn_context.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/log_backed_turn_consistency_design_spec.md)
