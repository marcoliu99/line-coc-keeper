# 戰鬥回合延遲：提示只帶本回合，讀取狀態離開事件迴圈

[English](combat_prompt_and_router_latency_design_spec.md) | [文件索引](../../README.md)

分類：`enhancement`。狀態：**已實作**。基於 `main_v2` 的 `ac6c943` 加上 PR #241。

## 問題

[量測後的回合延遲優先順序](measured_turn_latency_priorities_design_spec_zh.md) 已確認 Executor 依序的請求主導一個回合，而且每次請求都帶整份提示。有兩件事讓戰鬥回合比量測過的一般回合更糟：

- `turn_context.authority_block` 把 `CombatState.to_dict()` 整個貼進每次 Executor 與敘事者請求：每筆事件連同資料、每筆擲骰紀錄、每一輪的每個行動連同控制回執、每個計畫。全部每輪都在長大，而守密人只需要目前這一輪。
- 五個守密人工具（`damage_combatant`、`apply_combat_damage`、`apply_final_combat_damage`、`resolve_enemy_action`、`add_combat_effect`）在管理式戰鬥中會被拒絕、在戰鬥外也會失敗，但 `adjust_character` 的說明卻叫守密人用它們處理敵人傷害。每次嘗試都是一次浪費掉的請求。
- 路由在事件迴圈上同步讀取整列狀態（含劇本全文），一則訊息最多四次，期間所有頻道都停住。

## 修改

- `turn_context.combat_projection(state)`：這一回合需要的戰鬥。完整保留：`order`、`current_index`、`round_number`、`phase`、`enemy_cards`、`effects`、`working_resources`、`interaction`、`settlement`、`range_bands`、`combat_id`、`revision`。精簡：`actions` 只留未完成、需要裁定、本回合與待履行事項，且不含傳遞回執；`plans` 只留本回合未解決者；`events` 只留最近八筆的 `event_id`／`kind`／`revision`／`reason` 並附 `event_count`。移除：`roll_receipts`、`baseline_resources`、`processed_timings`。附註指向 `get_combat_status` 查完整歷史。守密人要抄進工具的東西（戰鬥、互動、行動、計畫的 id）都還在。
- 管理式戰鬥會拒絕、戰鬥外也用不了的五個工具（`damage_combatant`、`apply_combat_damage`、`apply_final_combat_damage`、`resolve_enemy_action`、`add_combat_effect`）連同處理函式與公開投影一起從註冊表移除；`adjust_character` 把敵人傷害指向戰鬥流程。它們背後的引擎動作保留給管理式流程，傷害由流程自己套用。
- `commands/router.py` 在一般訊息路徑取得對話鎖之後用 `asyncio.to_thread` 讀取狀態（每回合一次而不是兩次），help 頁面亦同。鎖之前的排程快照刻意維持同步：兩位玩家幾乎同時送出的訊息必須按到達順序排隊，鎖前換執行緒會讓後到的超車（`test_slow_prefetch_cannot_reorder_player_turns`）。

## 未做

- 這裡不動鎖的範圍、敘事者的第二次請求、或串流。
- 沒有對實際跑局量測；減少的是每次戰鬥請求的提示位元組與事件迴圈阻塞，兩者都在測試與程式碼裡看得到。

## 測試

`tests/test_turn_latency_trims.py`。
