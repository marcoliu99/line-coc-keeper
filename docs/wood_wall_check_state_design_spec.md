# 木板牆行動的檢定狀態一致性設計規格

## 狀態

- 狀態：待審閱，尚未實作
- 整合分支：`main_v2`
- 工作分支：`bug/wood-wall-check-state`
- 基底：`0bed067ab48250be9fe305fada3e5d11606a086c`
- 問題紀錄：2026-09-25 `15:03–15:08` Corbitt 屋地下室木板牆互動

## 問題與目標

玩家連續嘗試進入地下室木板牆後方的空間。系統在一段互動中混淆了待擲檢定、已擲骰結果、Luck 選擇和戰鬥順序，令玩家不知道上一個行動是否已結算，並反覆描述新的破牆嘗試。

目標是讓 Keeper／Executor／Narrator 對同一行動遵守單一、可追溯的機制狀態：工具建立待擲檢定後，回覆必須承認它已建立並指引玩家擲骰；擲骰結算後，所有後續敘述都必須採用該結果；等待 Luck 決定時，先處理該決定，不得把同一行動當成新行動或已完成行動；只有具備規則或劇情依據時才能開始戰鬥或引入敵人。

## Log 觀察

以下時間為 runtime log 中的時間：

- **15:03:55–15:04:19**：玩家要求進入木板牆後方。請求先處理道具轉交，最後回覆牆完整、目前不能進入，沒有明確指出下一個可執行步驟。
- **15:04:20–15:05:04**：Marco 撞牆。Executor 的 `skill_check` 回傳指出 Marco 正等待 Luck 決定；Narrator 卻描述 Marco 蹬地撞牆，並說沒有判定結果，未指示處理既有 Luck 選擇。
- **15:05:05–15:05:51**：Mick 揮大槌。Executor 建立 `STR 40` 的 pending 檢定；Narrator 仍說結果未知，沒有通知玩家檢定已建立或如何擲骰。
- **15:06:09–15:06:34**：Mick 擲出 69，STR 失敗。Keeper 隨後以戰鬥先攻為由宣稱行動尚未結算，並引入 Marco 與鼠群的戰鬥敘述，與已完成的檢定結果衝突。
- **15:07:18–15:07:52**：後續再次進入 Luck／檢定處理。Keeper 才回覆木牆沒有破、STR 檢定失敗；玩家已需要追問戰鬥與破牆結果。
- **15:04–15:08**：同一對話多個請求排隊，系統多次回覆已排入佇列。玩家在前一個慢回合完成前持續送出新指令，增加了新行動與舊 pending 狀態交錯的機會。

這些現象顯示，鎖序列化保護了同一對話的寫入順序，但沒有保證不同 Agent 的文字敘述忠實反映工具與 persisted state 的狀態。

## 範圍

- 修正 Executor 工具回傳與 Narrator／Keeper 回覆不一致的行為，特別是 `skill_check` 建立 pending、已有 `pending_luck_decisions`、`/coc check` 已結算，以及戰鬥狀態與檢定狀態同時存在的情境。
- 在 Executor 完成後整理本回合已建立、已更新或已結算的機制狀態，並將其作為 Narrator 的權威輸入；不得只靠自由文字摘要重建狀態。
- 讓 Narrator 在 pending check 或 Luck 決定等待中時，明確指出等待中的角色、技能／屬性或 Luck 決定及玩家可採取的下一步。此時不得敘述該行動已成功、失敗或產生尚未結算的後果。
- 將已結算結果視為該行動的最終機制結果。後續 Keeper 回覆不得以先攻、敘事合理性或新檢索內容覆寫已結算結果。
- 限制敵人現身、角色受攻擊、戰鬥開始等結果必須有 scenario 依據、已存在的遊戲狀態或本回合工具變更支持。
- 保留現有玩家主動擲骰、Luck 選擇、對話鎖及 state persistence 流程。

## 明確不在範圍

- 不調整 CoC7e 的技能檢定、困難度、Luck 花費、孤注一擲或戰鬥先攻規則。
- 不更改木板牆本身的 HP／硬度、敲擊次數、破壞方式或劇本內容；現有場景依據不足以新增此類規則。
- 不因這段 log 推定鼠群應現身、應攻擊，或木牆應被破壞。
- 不縮小或移除 `conversation_lock`，不允許同一對話的行動並行結算。
- 不新增 campaign state schema，除非實作檢視證明現有狀態無法表達必要的 pending／resolved 事實；若需要 schema 變更，先更新本規格再實作。
- 不以等待提示取代機制狀態修正。排隊提示只告知正在等待，不能把未處理的檢定或 Luck 選擇說成已結算。

## 權威狀態與資料結構

預期沿用既有資料：

- `GroupState.pending_checks`：待玩家擲骰或完成指定選擇的檢定。
- `GroupState.pending_luck_decisions`：已擲骰但仍等玩家決定是否花 Luck 的結果。
- 本回合 Executor tool gateway 的工具呼叫與回傳收據，以及 Executor 後重新載入的 persisted state。
- 既有 combat state（若有），只用於描述戰鬥目前狀態與合法先攻；不能拿它推翻已結算的非戰鬥檢定。

不新增資料庫欄位。若需組裝傳給 Narrator 的機制摘要，使用有界、結構化的 turn-local context，至少區分：

| 欄位 | 語意 |
|---|---|
| `pending_checks_created` | 本回合新建立，仍待玩家處理的檢定 |
| `pending_luck_decisions` | 本回合可見／新增，仍待玩家決定的 Luck 選項 |
| `checks_resolved` | 本回合已擲骰且已定案的結果，包括技能、目標值、骰值、難度、成功層級及是否已套用 Luck |
| `tool_effects` | 本回合成功寫入的相關機制效果；以工具收據／重新載入的 state 為準 |
| `combat_state` | 本回合前後的戰鬥狀態變化；不得推測尚未開始的戰鬥 |

摘要只提供和目前行動相關的資料，依現有角色權限及玩家隱私規則過濾；不得把 tool error 當成成功變更。Turn-local 摘要不得跨回合冒充歷史紀錄。若回答跨回合詢問，沿用既有 resolved-check history 機制。

## 主要流程

```text
玩家描述行動
  -> Executor 呼叫機制工具
  -> 收集工具收據並讀取 persisted pending/resolved state
       |
       +-- 有 pending check ------> Narrator 明確要求玩家按鈕或 /coc check
       |
       +-- 有 pending Luck --------> Narrator 明確要求處理 Luck 決定
       |
       +-- 有 resolved check ------> Narrator 依定案結果描述，不重開或延後同一行動
       |
       +-- 無機制結果 ------------> 只描述已確認事實，不捏造檢定／傷害／戰鬥
```

若同一調查員已有 pending Luck 或 pending check，後續同一行動的請求應優先解析為「處理現有 pending」；不可另建會被既有 guard 拒絕的檢定，也不可敘述為新的敲擊已發生。若是明確且不同的新調查員／新行動，仍遵守既有 owner-specific pending policy；是否允許其他玩家在同一對話繼續行動，按現有系統規則處理，不在本次擴大。

當 check resolved 之後，Keeper 的延後回合只能根據該 resolved 結果及確實成功的 state mutation 敘述後果。若戰鬥次序使某個後續 combat action 尚未輪到，必須清楚區分「已結算的檢定」和「尚未執行的下一個戰鬥行動」，不得將前者改寫成未結算。除非場景／工具狀態明確建立 combat，否則不可引用 initiative 或敵人回合擱置一般破牆檢定。

## 錯誤與重試處理

- `skill_check` 回傳 `pending=True`：玩家檢定已建立。輸出要指引玩家擲骰，不可稱檢定未建立或「尚無判定結果」而不給下一步。
- 工具回報既有 Luck 選擇阻擋新檢定：不建立新的檢定。回覆指出目前待處理的 Luck 決定並提供處理方式；暫停同一行動的結果敘述。
- `/coc check` 回傳失敗：Keeper 必須採用該骰值與 outcome。若失敗結果有待處理 Luck 修正，清楚標成「骰已擲出，仍待 Luck 決定」；Luck 最終處理後再定案敘述。
- 工具呼叫失敗、被 guard 拒絕或回傳狀態不完整：不得從預期行為推論成功。回覆已知狀態及必要的下一步，並避免敘述破牆、傷害或戰鬥已發生。
- Discord queue acknowledgement：只報告排隊狀態。實際輪到請求處理後仍要重新讀取最新 pending/combat state，再決定是否接受它為新行動或提示完成舊選擇。

## 整合慣例

- 檢視並沿用 `app/agents/executor.py`、`app/agents/tool_gateway.py`、`app/agents/state_reducer.py`、`app/agents/supervisor.py` 現有的 Executor 工具收據與 state reload 邊界。
- Narrator 的 system/dynamic prompt 應包含明確的優先序規則：persisted state／成功工具結果 > Executor 的自然語言摘要 > 模型推論；不得讓自然語言結果覆寫結構化狀態。
- Keeper 的 prompt 和工具描述應強化 pending check、pending Luck、resolved result、combat turn 的不同語意；避免在 pending 狀態中呼叫重複的 skill check 或宣稱行動結果。
- 將機制摘要維持精簡且有界，避免把大型 RAG OCR 或整段 scenario 檢索結果塞進 Narrator 狀態欄位。
- 不在本次更動 Discord API、鎖策略或 `GroupState` schema。

## 驗證計畫

新增／調整回歸測試，使用 stubbed 工具收據和 persisted state，不呼叫外部 LLM 或 Discord：

1. `skill_check` 回傳 `pending=True` 時，Narrator context 有對應 pending check；輸出不得說檢定未建立或沒有下一步。
2. 同一 investigator 有 pending Luck 時，新行動不得建立第二個同類 pending check；回覆須指引完成既有 Luck 決定，且不能敘述新行動結果。
3. `/coc check` 回傳特定失敗骰值後，Keeper 不能因戰鬥先攻重新標成未結算，也不能變更失敗結果。
4. 成功／失敗檢定本身不得自動導出傷害、牆破裂或 combat；只有成功的對應工具變更或已檢索到的明確劇本結果能支持這些敘述。
5. 已有 combat 時，檢定結果與尚未輪到的 combat action 必須保留為兩個不同狀態；無 combat 時不應敘述 initiative／敵人回合。
6. 等候鎖期間送入的行動，在取得鎖後須讀取最新狀態，不得使用排隊前的 stale pending snapshot。
7. 對原始木板牆序列做整合級狀態回歸：Marco 的待處理 Luck、Mick 的 STR pending、骰出 69 失敗、後續等待 Luck；所有回覆都維持單一一致的行動狀態，不引入無依據的鼠群戰鬥或重複破牆結果。
8. 保留既有玩家擲骰與 Luck 行為的測試；執行相關 pytest、Ruff、mypy 和 compileall。

## 決策與待確認事項

- **不改變玩家擲骰所有權**：預設仍由玩家按按鈕或使用 `/coc check` 擲骰；Luck 仍由玩家決定。依據既有 `keeper-deterministic-check-resolution` 設計規格。
- **不採用新 schema**：優先從工具收據和每回合最新 state 建立結構化摘要。如實作發現 check 的「骰已擲出但等待 Luck」無法和最終 resolved 結果區分，先提出最小 state/schema 修訂並更新本規格，不可默默用文字補狀態。
- **壞掉的木板／場景細節不由程式補完**：本修正只保證狀態一致性，不裁決木板需幾次命中或何種工具能破壞。是否需要補充 scenario 資料，留待另案處理。
