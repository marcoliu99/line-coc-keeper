# 移除舊 Keeper 迴圈後的輸出保護

[English](bug-legacy-run-turn-missing-guard.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已由後續設計取代**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 原修正替 keeper.run_turn 補上輸出保護；之後統一回合重構已移除整個舊對話入口。

2. 目前玩家輸出在 Supervisor 保護，獨立 KP Assistant 保護自己的輸出；測試應呼叫現行 agent，不能恢復或 mock 已刪除迴圈。

## 流程與介面

```text
玩家 Supervisor／KP Assistant -> 敘事 Guard -> 劇透政策 -> 提交
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/agents/guard.py](../../../app/agents/guard.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)
- [tests/test_guard_agent.py](../../../tests/test_guard_agent.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-legacy-run-turn-missing-guard.md)
