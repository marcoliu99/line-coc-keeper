# 設計規格草案：統一 Keeper 回合流程

## 狀態與目標

討論稿。工作分支：`refactor/unify-keeper-turn-flow`，從最新 `main_v2` 建立並已推送。目標是讓所有會產生 Keeper 回覆的遊戲入口由同一個回合協調流程管理，不再由指令處理器各自選擇 `keeper.run_turn` 或 Supervisor。**統一入口不代表每回合固定增加 Executor、Narrator 或審稿 LLM 呼叫。**

目前 PR #86 的劇情邊界改動在另一分支；實作前須與合併後的 `main_v2` 對齊，確認所有回合模式沿用同一份正典、RAG 與劇情提示詞。

## 現況與剩餘範圍

一般玩家文字及 KP 代玩家行動已走 `app/agents/supervisor.py`；它依意圖走純敘事快路徑或 Executor → Narrator，並共用 `keeper._execute_tool` 的權威工具。KP Assistant 的訊息表面上也進 Supervisor，但 `OOC_ASSISTANT` 分支立刻交回 `app/agents/assistant.py` → `keeper.run_turn`。因此目前並非單一回合流程。

尚有 **3 個直接呼叫 `keeper.run_turn` 的入口**：

| 入口 | 位置 | 必須保留的行為 |
| --- | --- | --- |
| 已結算檢定後續 | `app/legacy_commands.py` 的檢定結果階段 | 骰子與角色狀態已先由程式提交；後續敘事不得重骰或重扣，但部分結果可能需要後續工具。包含 Luck 決策、待處理狀態、時間線驗證與公開回覆。 |
| `/coc start` 開場白後備路徑 | `app/commands/handlers/system.py` | 若劇本已有現成開場，沿用抽取結果與可能的開場檢定；只有沒有現成開場時才請模型依劇本資料生成，可能需要 `search_scenario`，之後原子地標記 `game_started`。 |
| KP Assistant | `app/agents/assistant.py` | 場外問答只進 `kp_ooc_log`；明確 `!` 主持正典指令或成功的正式遊戲工具結果才升格進 `state.log`。保留 KP 工具白名單、私訊／圖片、劇透處理、OpenAI 對話鏈隔離與時間線檢查。 |

兩套流程目前已共用靜態／動態 Keeper 提示詞、工具 schema 與 `_execute_tool`；因此工作重點是**回合協調、模型呼叫模式與提交邊界**，不需要重寫骰子或戰鬥規則。

## 方案

1. 提供單一、明確的回合入口與模式：`player_action`、`resolved_check_followup`、`opening_fallback`、`kp_assistant`。指令處理器只提供已驗證的角色、情境及已結算事件，不自行選模型管線。現有一般玩家與 KP 代玩家操作先維持行為一致。
2. 抽出共用回合生命週期：建立時間線、準備上下文與正典提示、依模式提供工具、追蹤成功工具與副作用、套用 Guard／劇透保護、選擇正式或 KP 場外紀錄、提交 OpenAI response chain、交付私訊／圖片請求。共享的 `_execute_tool` 不另做一份。
3. 保留低成本模式。一般純角色扮演仍只需 Narrator；已結算檢定的後續以及無現成開場白時，可用一個有受限工具的模型回合處理敘事與必要的後續動作，避免機械地串成 Executor + Narrator 兩次模型呼叫。KP Assistant 的場外討論也維持單次模型回合；必要工具可在同一回合內迭代。
4. 先遷移較單純的開場後備及檢定後續，再遷移 KP Assistant。待三個入口與既有特殊行為都經回歸測試後，才移除或降為相容轉接的 `keeper.run_turn`。不得只改呼叫名稱、留下兩份平行的提交與錯誤處理。

### 關鍵流程

- **檢定後續**：程式先完成骰子／Luck／狀態提交 → 統一入口收到已結算事件與原行動脈絡 → 只提供後續必要工具 → 驗證輸出與時間線 → 正式歷史只追加一次 → 交付公開與私密內容。若後續工具已改狀態而模型失敗，回覆不可請玩家重做原行動。
- **開場**：角色與劇本就緒 → 優先使用現有開場抽取結果 → 缺少現成開場才進統一入口的 `opening_fallback` → 依劇本／RAG 生成 → 標記 `game_started`、建立有來源的待擲檢定並公開。不可把開場當成玩家行動，也不能讓預設意圖分類憑空建立檢定。
- **KP Assistant**：保留專用 OOC 模式與一輪工具迭代；無正式事件時僅提交有上限的 `kp_ooc_log`，不觸發公開歷史維護；明確 `!` 或成功的正式工具結果才提交 `state.log` 並正確處理 OpenAI 對話鏈。KP 仍可依既有白名單指示正式骰子或檢定，不須作為玩家角色。

## 資料結構與相容性

預期不新增 `GroupState` 欄位或資料庫 schema。沿用 `log`、`kp_ooc_log`、`pending_checks`、已結算檢定事件、時間線與 `openai_previous_response_id`。若實作發現現有事件資料不足以無歧義表示檢定後續，先提出最小資料變更及舊存檔預設值，不能用對話文字反推權威骰子結果。

## 非目標

- 不把所有情境改為固定的 Executor → Narrator 兩次模型呼叫。
- 不重寫 `_execute_tool`、骰子、戰鬥或既有 RAG 檢索策略。
- 不在這個重構中改變 KP Assistant 哪些工具結果會成為正式正典。
- 不把 `/coc check` 已完成的骰子再交給模型重新擲。

## 驗證門檻

- 為三個入口建立實際路由測試，斷言沒有路徑再直接依賴舊單一路徑；一般玩家與 KP 代玩家路徑仍工作。
- 檢定：技能、SAN、Luck、多人待處理、回溯後舊按鈕、模型失敗前後有／無工具副作用；確認不重骰、不中途重扣，正式歷史只有一次結果。
- 開場：有現成開場（含 opening_check）、無現成開場需 RAG、模型失敗、重複 `/coc start`、同時請求與時間線切換；`game_started` 與公開文字一致。
- KP Assistant：純 OOC、明確 `!`、正式骰子／檢定、OOC 隨機骰、失敗工具、私訊／圖片、工具白名單、劇透修復、`kp_ooc_log` 上限、OpenAI response chain 升格與隔離。
- 量測各模式的模型請求、RAG 查詢及延遲；原本單次模型呼叫的模式不得因統一協調流程無條件變成兩次。跑完整測試套件，並在 PR 前重新對齊最新 `main_v2`。

## 待確認

1. 單一入口可保留數種受控模型呼叫模式；這是為了維持現有延遲及 KP 工具能力。若「一個流程」意指**每種回合都只能一次 LLM 呼叫**，一般玩家目前的 Executor + Narrator 也需重新設計，範圍會明顯擴大。
2. 開場抽取 `scenario_intro.extract_opening_narration` 是一個獨立資料抽取步驟，不是舊 Keeper 回合。是否也要合併到統一協調器，可在保留現有「有現成開場先使用」規則下決定。
