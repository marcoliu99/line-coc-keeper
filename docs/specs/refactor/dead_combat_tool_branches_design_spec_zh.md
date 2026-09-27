# 每個戰鬥工具只有一個分派分支

[English](dead_combat_tool_branches_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`refactor`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. keeper._execute_tool 仍是共用權威工具入口；移除舊 Keeper 對話迴圈並未移除此分派器。

2. 每個字面工具名稱只應出現一個處理分支；重複且無法執行的分支會隱藏修正，使行為依分支順序而異。

3. 傷害、回合推進與存檔沿用既有服務；AST 回歸檢查分支唯一性，戰鬥測試驗證實際行為。

## 流程與介面

```text
工具名稱 -> 單一分派分支 -> 戰鬥服務 -> 持久化結果
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [tests/test_execute_tool_no_duplicate_branches.py](../../../tests/test_execute_tool_no_duplicate_branches.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/dead_combat_tool_branches_design_spec.md)
