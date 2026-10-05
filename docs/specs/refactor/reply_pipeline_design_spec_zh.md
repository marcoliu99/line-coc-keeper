# 拆分 `supervisor.run_turn` 並讓回覆步驟的順序可被檢查

[English](reply_pipeline_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `e4c6044`。

## 問題

`supervisor.run_turn` 原本是一個 277 行的函式。其中把旁白文字變成玩家讀到的回覆的那幾步，順序只靠行的先後決定：一致性修正、Guard、再一致性修正、事件義務閘門、隊伍人數更正、交付驗證、顯示轉換。每一步都有放在那個位置的理由（#181、#185 各自插入過一步），但理由只存在註解裡。下一個要加步驟的人可能把它放在「驗證交付」之後，改掉已經驗證過的文字。見 `docs/architecture/main_v2_architecture_review_zh.md`（F4）。

## 限制：玩家的回合不變

這是搬移。每一次模型呼叫、鎖交接、fallback 記錄與提交都以同樣的順序、同樣的輸入發生；沒有任何步驟新增 `await`；回覆文字、日誌與時間都不變。回覆仍需要完整文字，所以仍不能串流——那是安全邊界，本變更不碰它。

## 結構

`run_turn` 的簽章與裝飾器不變，內容改成依序呼叫各階段：

| 階段 | 做什麼 |
| --- | --- |
| `_prepare` | 修正案阻擋回覆、持有 Luck 決定時由狀態直接回答、建立 context、意圖路由；KP Assistant 路線在此結束 |
| `_mechanics` | Executor、至多一次的復原、reducer、把突變鎖交給下一位玩家（旁白交接） |
| `_narrate` | Narrator（每個自動擲骰後果各一次），或重用待決狀態回覆；失敗的開場在此結束 |
| 回覆步驟 | `app/agents/reply_pipeline.py`，見下 |
| `_commit` | 單一交易把這回合寫進紀錄；時間線已過期就不送出 |

送到 Discord 與背景維護仍由呼叫端（`router`）負責，所以這裡沒有對應的階段。各階段共用一個私有的 `_Turn` 物件，取代十幾個區域變數。事件義務閘門用來比對的「回合前待決狀態」仍然在 Narrator 執行前取得，因為帶工具的 Narrator 可能改動那份狀態。

## 回覆步驟

`reply_pipeline.STEPS` 是有序的 tuple：`consistency`、`guard`、`consistency_after_guard`、`obligations`、`party_size`、`finalize`、`player_text`。`ORDER_RULES` 逐條寫明誰必須在誰之前與原因；`validate` 在 import 時執行：順序違反規則、缺少規則所指的步驟、或 `finalize` 之後出現沒有標為 `display_only` 的步驟，都會丟出 `ValueError`。`finalize` 之後唯一允許的步驟是 `player_text`，標為 `display_only`：只做顯示用的難度名稱轉換與移除內部 id。**它不是無損的**：若一行已驗證的內容帶有未轉換的難度名稱或附標籤的 id，驗證之後仍會被改寫。這與拆分前完全一致（`player_text` 本來就在 `finalize` 之後），目前沒有任何投影出的行會這樣（`finalize` 自己會轉換結果標籤，`player_text` 是冪等的）；改成「驗證轉換後的文字」是行為變更，不屬於這次搬移。

只有 Guard 與事件義務閘門會等待；測試斷言這點，所以某一步開始 `await`（等於玩家回合多一次往返）會成為看得見的變更。

## 驗證

`tests/test_turn_stage_sequence.py` 是對拆分前的 `run_turn` 錄下的基準，拆分後不改動就通過：一般回合、帶義務候選的回合、擲骰後續、過期時間線、失敗的開場、KP Assistant 路線，各自的 Executor／交接／Narrator／Guard／義務閘門／人數更正／交付驗證／提交呼叫順序。`tests/test_reply_pipeline.py` 涵蓋順序規則。原本 patch `supervisor.guard`、`supervisor.turn_delivery` 的測試改 patch `reply_pipeline.guard`／`reply_pipeline.turn_delivery`（同一個模組物件）。`ruff check .`、`mypy app` 與完整 `pytest` 通過。
