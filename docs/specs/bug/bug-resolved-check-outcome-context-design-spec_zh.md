# 持久化已結算檢定脈絡

[English](bug-resolved-check-outcome-context-design-spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 保存已結算檢定的原行動脈絡、擁有者／角色識別、結果與時間線；後續敘事使用紀錄，不能從對話反推結果。

2. 自動擲骰結果也需要歷史；角色範圍脈絡不能因另一玩家檢定較新就借用該結果。

3. 最新 pending／Luck／背包／戰鬥狀態優先於摘要；歷史用來解釋已完成事件，不能授權重做。

## 流程與介面

```text
結算骰子／Luck -> 持久事件 -> 當前角色上下文 -> 後續敘事
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/services/turn_context.py](../../../app/services/turn_context.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [tests/test_resolved_check_events.py](../../../tests/test_resolved_check_events.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-resolved-check-outcome-context-design-spec.md)
