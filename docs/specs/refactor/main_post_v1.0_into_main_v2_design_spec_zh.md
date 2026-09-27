# 歷史 v1.0 後整合紀錄

[English](main_post_v1.0_into_main_v2_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`refactor`。狀態：**歷史紀錄**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 此文件記錄已完成的 main 選定變更移入 main_v2；整合清冊是歷史，不是要求再次合併目前分支。

2. 目前 runtime 為 Discord-only，使用統一 Supervisor 與獨立 KP Assistant；舊 app/commands.py 與舊整回合流程圖不是有效介面。

3. 下方不可變來源版本可追溯原 baseline 與來源／目標清冊；目前行為以後續功能規格為準。

## 流程與介面

```text
歷史來源 commit -> main_v2 整合 -> 後續統一流程變更
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/commands/router.py](../../../app/commands/router.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/main_post_v1.0_into_main_v2_design_spec.md)
