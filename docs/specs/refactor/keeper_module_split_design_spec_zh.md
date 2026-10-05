# 依職責拆分 `app/keeper.py`

[English](keeper_module_split_design_spec.md)

狀態：**implemented**——三步全部完成，`app/keeper.py` 已不存在。基準：`main_v2` 於 `b54c986`。

## 問題

`app/keeper.py` 是 1,500 行的樞紐，扇出 31 個模組。同一個檔案裡有：提示建構、狀態變更包裝、回合提交、回合後記憶維護、工具分派與戰鬥狀態閘。`keeper_tools/*` 以延遲 import 回呼它（12 個模組的環），其他 9 個檔案約 35 處 `keeper._*` 靠 `SLF001` 豁免。見 `docs/architecture/main_v2_architecture_review_zh.md`（F9）。

## 限制：玩家路徑不變

這是搬移，不是重新設計。函式保留本體、參數與在呼叫順序中的位置，只有所在模組改變。特別是：不新增或移除任何 `await`、不提早或延後取放任何鎖、不改變任何玩家看得到的文字。

## 步驟

1. **`app/prompt_builder.py`**（本步）：`build_static_prompt`、`build_dynamic_prompt`、`correction_context_message`、`format_turn_message`、`format_kp_canonical_history_message`、人格與 KP Assistant 提示常數，以及劇本預算與防劇透／隱私規則輔助函式。私有名稱改為公開，所有呼叫端一併更新。`KP_OOC_LOG_MAX_MESSAGES` 移到 `app/config.py`，因為提示與回合提交都會讀它。
2. **`app/turn_commit.py` 與 `app/memory_maintenance.py`**（已實作）：`ensure_turn_timeline`、`commit_turn_result`、`commit_kp_ooc_turn_result` 與 `OpeningStartRejected`；回合後維護（`run_post_turn_maintenance`、場景摘要、記憶寫入步驟與 `summarize_log_chunk`）。原本 patch `keeper.MAX_LOG_TURNS`、`keeper.conversation_provider` 或 `keeper.scene_digest` 來控制維護的測試，改為 patch `memory_maintenance`。
3. **`app/tool_dispatch.py`**、**`app/keeper_tools/support.py`** 與新閘門（已實作）：`execute_tool`、`tools_for_speaker_role`、KP Assistant 的工具定義、戰鬥狀態工具閘與工具復原標記搬到 `tool_dispatch`；各 handler 共用的部分（`ToolStateMutation`／`mutate_tool_state`、角色與 NPC 索引查找、檢定結果快取、公開戰鬥傷害過濾）搬到 `keeper_tools/support`。`TOOLS`、`READ_ONLY_TOOL_NAMES`、`RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES` 這些別名改為直接使用 `keeper_tools.registry`。搬完後 `app/keeper.py` 已無內容，直接刪除。`tests/test_architecture_keeper_tools.py` 在任何 handler import 了分派器或其上層時失敗（含函式內的 import）。`app/agents/*`、`commands/handlers/system.py`、`services/movement.py` 與 `keeper.py` 的 `SLF001` 豁免因不再需要而移除。`search_scenario` 寫入的日誌仍使用 logger 名稱 `app.keeper`，既有的日誌過濾不受影響。

`keeper.py` 不留相容轉出；測試與呼叫端在同一個變更中改用新名稱。

## 驗證

- 空狀態與進行中狀態、劇本檢索開／關、玩家與 KP Assistant 兩種身分的靜態與動態提示（共 16 組）在搬移前後的雜湊完全相同。
- `ruff check .`、`mypy app` 與完整 `pytest` 通過；原本 patch 被搬移名稱的測試改為 patch 它現在所在的位置。
- 原本 patch `keeper.SCENARIO_RAG_ENABLED` 來改變提示的測試，現在同時 patch `prompt_builder.SCENARIO_RAG_ENABLED`；兩個模組在 import 時讀的是同一個 `app.config` 值。

## 不在範圍內

不改提示文字、快取行為、工具暴露或任何鎖。這些屬於架構審查的延遲工作（P4），需要先有量測。
