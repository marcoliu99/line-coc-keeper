# 依職責拆分 `app/keeper.py`

[English](keeper_module_split_design_spec.md)

狀態：**partial（部分完成）**——第 1 步（提示建構）已實作，第 2、3 步待做。基準：`main_v2` 於 `b54c986`。

## 問題

`app/keeper.py` 是 1,500 行的樞紐，扇出 31 個模組。同一個檔案裡有：提示建構、狀態變更包裝、回合提交、回合後記憶維護、工具分派與戰鬥狀態閘。`keeper_tools/*` 以延遲 import 回呼它（12 個模組的環），其他 9 個檔案約 35 處 `keeper._*` 靠 `SLF001` 豁免。見 `docs/architecture/main_v2_architecture_review_zh.md`（F9）。

## 限制：玩家路徑不變

這是搬移，不是重新設計。函式保留本體、參數與在呼叫順序中的位置，只有所在模組改變。特別是：不新增或移除任何 `await`、不提早或延後取放任何鎖、不改變任何玩家看得到的文字。

## 步驟

1. **`app/prompt_builder.py`**（本步）：`build_static_prompt`、`build_dynamic_prompt`、`correction_context_message`、`format_turn_message`、`format_kp_canonical_history_message`、人格與 KP Assistant 提示常數，以及劇本預算與防劇透／隱私規則輔助函式。私有名稱改為公開，所有呼叫端一併更新。`KP_OOC_LOG_MAX_MESSAGES` 移到 `app/config.py`，因為提示與回合提交都會讀它。
2. **`turn_commit.py` 與 `memory_maintenance.py`**：`_commit_turn_result`、`_commit_kp_ooc_turn_result`、時間線保證；回合後維護與日誌摘要。
3. **`tool_dispatch.py`** 與新閘門：`keeper_tools` 不得 import `keeper`。

`keeper.py` 不留相容轉出；測試與呼叫端在同一個變更中改用新名稱。

## 驗證

- 空狀態與進行中狀態、劇本檢索開／關、玩家與 KP Assistant 兩種身分的靜態與動態提示（共 16 組）在搬移前後的雜湊完全相同。
- `ruff check .`、`mypy app` 與完整 `pytest` 通過；原本 patch 被搬移名稱的測試改為 patch 它現在所在的位置。
- 原本 patch `keeper.SCENARIO_RAG_ENABLED` 來改變提示的測試，現在同時 patch `prompt_builder.SCENARIO_RAG_ENABLED`；兩個模組在 import 時讀的是同一個 `app.config` 值。

## 不在範圍內

不改提示文字、快取行為、工具暴露或任何鎖。這些屬於架構審查的延遲工作（P4），需要先有量測。
