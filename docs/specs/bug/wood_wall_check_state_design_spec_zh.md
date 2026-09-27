# 檢定敘事與權威狀態一致

[English](wood_wall_check_state_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 敘事必須區分已登記檢定、已結算結果與待處理 Luck 決定；不能因行動看似困難就新增擲骰。

2. 最終指示必須對照目前 pending 狀態；已完成檢定不得要求再骰，真實未完成檢定也不能被敘事抹除。

3. 已結算檢定後續以權威事件與受限工具進入 Supervisor；仍可能需要傷害或回合推進，但已結算骰子不可重做。

## 流程與介面

```text
工具／檢定結果 -> 驗證待處理檢定與 Luck 狀態 -> Narrator -> 指示一致性檢查
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/services/prompt_config.py](../../../app/services/prompt_config.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [tests/test_narrator_check_consistency.py](../../../tests/test_narrator_check_consistency.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/wood_wall_check_state_design_spec.md)
