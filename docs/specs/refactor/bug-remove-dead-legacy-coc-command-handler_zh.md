# 移除未使用的舊指令分派器

[English](bug-remove-dead-legacy-coc-command-handler.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`refactor`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. commands.router 負責指令分派；過時的單體 legacy handler 不得保留成另一條命令路線。

2. legacy_commands.py 仍有有效的確定性檢定、Luck、上傳與相容 helper；檔名不代表整個模組都已失效。

3. keeper.run_turn 的移除屬另一項統一玩家回合重構；應區分指令分派與模型對話迴圈的移除。

## 流程與介面

```text
Discord -> commands.router -> 已登錄處理器 -> 共用確定性輔助函式
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/commands/router.py](../../../app/commands/router.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)
- [tests/test_kp_assistant_v2.py](../../../tests/test_kp_assistant_v2.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-remove-dead-legacy-coc-command-handler.md)
