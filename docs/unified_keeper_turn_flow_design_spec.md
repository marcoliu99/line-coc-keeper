# 設計規格草案：統一玩家 Keeper 回合流程

## 狀態與目標

討論稿。工作分支：`refactor/unify-keeper-turn-flow`，整合目標：`main_v2`。本期把**玩家劇情**的兩個剩餘入口接到 Supervisor 協調流程：已結算檢定後續，以及 `/coc start` 沒有現成開場白時的生成。KP Assistant 是獨立的主持 agent，保留自己的場外對話與正式事件升格流程，不納入玩家管線。

「統一流程」指玩家回合由同一協調入口處理上下文、模型模式、輸出保護與提交；依情境仍可有不同的模型呼叫方式。這是**行為等價的重構**：維持既有檢定敘事、必要工具與公開文字。現況的檢定後續會進 `keeper.run_turn`，啟動一個模型對話，遇到工具迭代時可能有多次 API 請求；從檢定處理器「跳回 Keeper」本身只是程式路由，並非另一個 API 請求。統一後量測往返次數與延遲，可能因去除重複處理而下降，但不把新增零模型回覆或刪減敘事當作本期目標，也不能無條件變成 Executor + Narrator 兩次呼叫。PR #86 的劇情邊界改動在另一分支；實作前須與合併後的 `main_v2` 對齊，保留其正典、RAG 與劇情提示詞。

## 現況與剩餘範圍

一般玩家文字及 KP **代玩家行動**已走 `app/agents/supervisor.py`；它依意圖走純敘事快路徑或 Executor → Narrator，並共用 `keeper._execute_tool` 的權威工具。玩家劇情仍有 **2 個直接呼叫 `keeper.run_turn` 的入口**：

| 入口 | 位置 | 需要保持的行為 |
| --- | --- | --- |
| 已結算檢定後續 | `app/legacy_commands.py` 的檢定結果階段 | 骰子與角色數值已由程式提交；敘事不得重骰或重扣。部分結果可能需要後續工具，且仍須保留 Luck、待處理狀態、時間線驗證及公開／私密回覆。 |
| `/coc start` 開場後備 | `app/commands/handlers/system.py` | 劇本已有開場時繼續使用現成抽取結果與可能的開場檢定；只有缺少現成開場時才由模型依劇本資料生成，必要時可 `search_scenario`，再正確標記 `game_started`。 |

另外，`app/agents/assistant.py` 仍直接呼叫 `keeper.run_turn(..., speaker_role="kp_assistant")`。這是**刻意保留的獨立 agent**，不是本期剩餘玩家流程。它有 `kp_ooc_log`、明確 `!` 正典指令、成功工具結果的正典升格、KP 工具白名單與 OpenAI 對話鏈隔離；本期不得為了消除函式引用而搬進玩家回合。

玩家管線與 KP Assistant 已共用靜態／動態 Keeper 提示詞、工具 schema 與 `_execute_tool`。工作重點是兩個玩家入口的協調與狀態提交，不重寫骰子或戰鬥。

## 方案

1. 在 Supervisor 的玩家路徑提供明確模式：`player_action`、`resolved_check_followup`、`opening_fallback`。指令處理器傳入已驗證的角色、原行動脈絡與已結算事件，不能把後兩者送進一般文字意圖分類。
2. 檢定後續與開場後備維持現有可使用**既有工具**的模型回合，不新增固定模型階段，也不縮減原本的敘事或工具能力。把現有 `keeper.run_turn` 的共用模型／工具生命週期抽成可由 Supervisor 呼叫的元件；KP Assistant 繼續透過自己的 agent 使用同一底層能力。不能只把舊呼叫包一層 Supervisor 而仍保留兩套玩家提交邏輯。
3. 共用時間線檢查、Guard、劇透保護、工具副作用追蹤、失敗時的安全回覆、正式歷史提交與私訊／圖片請求。檢定後續不得重新執行原骰子；後續工具若已改狀態而模型失敗，不能要求玩家重做原行動。
4. 先遷移 `/coc start` 後備，再遷移檢定後續；保留現有一般玩家與 KP 代玩家路徑。完成後，玩家劇情不再直接呼叫 `keeper.run_turn`；KP Assistant 仍可使用其獨立 agent。

### 關鍵流程

- **檢定後續**：程式先完成骰子／Luck／狀態提交 → Supervisor 收到已結算事件與原行動脈絡 → 沿用現有模型回合處理敘事及必要後續工具 → 驗證輸出與時間線 → 正式歷史只追加一次 → 交付公開與私密內容。
- **開場**：角色與劇本就緒 → 優先使用現成開場抽取結果 → 缺少現成開場才進 Supervisor 的 `opening_fallback` → 依劇本／RAG 生成 → 標記 `game_started` 並公開。不可把開場當成玩家行動，也不能讓意圖分類憑空建立檢定。
- **KP Assistant**：留在獨立 agent。純場外討論只進 `kp_ooc_log`；明確主持指令或正式工具結果才升格 `state.log`。本期只跑回歸測試確保無變化。

## 資料結構與相容性

預期不新增 `GroupState` 欄位或資料庫 schema。沿用 `log`、`pending_checks`、已結算檢定事件、時間線與 `openai_previous_response_id`；KP Assistant 的 `kp_ooc_log` 保持原樣。若現有事件不足以無歧義表示檢定後續，先提出最小資料變更及舊存檔預設值，不能用對話文字反推權威骰子結果。

## 非目標

- 不把 KP Assistant 併入玩家 Executor／Narrator 管線。
- 不讓所有情境固定呼叫 Executor → Narrator 兩次模型。
- 不在本次重構加入「純機械結果零模型回覆」等會改變玩家敘事體驗的新功能。
- 不重寫 `_execute_tool`、骰子、戰鬥或既有 RAG 檢索策略。
- 不把 `/coc check` 已完成的骰子再交給模型重新擲。

## 驗證門檻

- 兩個入口都要有實際路由測試，確認玩家劇情不再直接呼叫 `keeper.run_turn`；一般玩家與 KP 代玩家路徑仍工作。
- 檢定：技能、SAN、Luck、多人待處理、回溯後舊按鈕、模型失敗前後有／無工具副作用；確認不重骰、不中途重扣，正式歷史只有一次結果。
- 開場：有現成開場（含 opening_check）、無現成開場需 RAG、模型失敗、重複 `/coc start`、同時請求與時間線切換；`game_started` 與公開文字一致。
- KP Assistant 現有 OOC／正式事件升格／工具權限／對話鏈測試保持通過，證明獨立 agent 未受重構影響。
- 以現況「需後續敘事的 `/coc check` 會啟動一次 `keeper.run_turn` 模型對話，可含多次工具迭代」作基準，量測重構前後的平均 API 請求、RAG 查詢及延遲；不得為了省請求漏掉劇本後果，也不得無條件增加一次 LLM 往返。跑完整測試套件，PR 前重新對齊最新 `main_v2`。

## 待確認

`scenario_intro.extract_opening_narration` 是獨立的劇本資料抽取，不是舊 Keeper 回合。本期保留「有現成開場先使用」及其呼叫時機，只統一缺少現成開場時的模型生成流程。
