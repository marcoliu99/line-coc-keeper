# 把物品交給另一位調查員是一個不可分割的步驟

[English](inventory_transfer_design_spec.md)

狀態：**待辦（backlog）**（只有設計，這次變更沒有程式）。基準：`main_v2` 的 `0de529b`。

## 問題

兩份真實五人測試（`1fd3ccb`）都看到物品在交接時消失，而玩家只收到一句泛用訊息。

Dead Boarder 200 回合，`NARRATION_OUTSIDE_MUTATION_LOCK=true`（`turns.jsonl`、`turn.fallback` 事件與 `api-events.jsonl`）：

| 回合 | 已提交的內容 | 回合怎麼結束 |
|---:|---|---|
| 47 | 只有 `remove_carried_item`（D 的古書被移除，接收者 B 沒有收到） | `executor_no_action`，泛用回覆 |
| 50 | `search_scenario`、`remove_carried_item` | `executor_no_action` |
| 56 | `add_carried_item`、`search_scenario` | `llm.turn.failed`，兩次工具呼叫後 120 秒 `TimeoutError` |
| 94 | `search_scenario` ×2、`remove_carried_item`、`add_carried_item`（四個工具） | `executor_no_action` |
| 140 | `remove_carried_item`、`add_carried_item`、`search_scenario` ×2（四個工具） | `llm.turn.failed`，四次工具呼叫後 120 秒 `TimeoutError` |
| 199 | 只有 `remove_carried_item`（A 的偽造收據被移除，D 沒有收到） | `unsupported_action` |

測試報告把這件事列為 DB200-F3（可能的問題，「根因未確認」）。讀過程式之後，我確認了一個不需要模型「夠小心」就會發生的原因。

## 為什麼會發生（從程式讀到的）

- **一次交接是兩次獨立提交。** `remove_carried_item` 與 `add_carried_item`（`app/keeper_tools/inventory.py`）各自執行 `mutate_tool_state` 並立刻存檔，沒有任何東西保證第二個會接著第一個。如果回合在中間停下（模型沒有收尾、Codex 逾時、`CodexError`、工具額度用完），物品就離開了一個角色，卻沒有到任何人手上。
- **已提交的變更刻意比回合活得久。** `codex_provider` 在逾時時不會取消已在工作執行緒中的變更（程式註解："Do not cancel a committed/worker-thread mutation on LLM timeout"），玩家也會被告知「已提交的變更會保留」。這個做法是對的，但它讓只做了一半的交接變成永久的。
- **回合期限涵蓋整個 Executor 執行。** `CODEX_TIMEOUT`（120 秒）在每次 `run_conversation` 設定一次（`request_owner.deadline()`），不是每個模型決定一次。回合 56、133、140 在兩到四次工具呼叫之後，剛好在 120.017 秒逾時，所以有好幾輪工具呼叫的複合動作，可能在做完交接的後半之前就用光整個預算。
- **一次交接用掉一個回合最多四個工具中的兩個**（`MAX_TOOLS_PER_TURN=4`）。回合 81、94、140、145、148 剛好用了四個工具，最後都是未完成或失敗。
- **交接被記成「消耗」。** `remove_carried_item` 會寫進 `consumed_or_removed_items`，Narrator 收到的是 `historical_removals`。所以交給隊友的物品，對模型來說是用掉或弄丟了。
- **「交接是否完整」的檢查發生在事後。** `turn_resolution._mutation_evidence` 在兩個都提交之後才辨認「先移除再加入」這一對，否則回傳未完成。它沒辦法復原前半。
- **沒有任何東西限制工具能改誰的背包。** 兩個處理函式都接受任意的 `investigator`，並用 `find_character` 解析，而它會退回子字串比對。玩家 A 的回合可以改到玩家 B 的角色，名稱相近也可能選錯人。
- **沒有任何東西讓交接保持完整。** 物品只是沒有身分的字串，所以沒有東西把離開一份清單的那一筆和應該到另一份清單的那一筆連起來；交接之後可能兩邊都有或兩邊都沒有。

## 規則

1. **一個步驟。** 調查員之間的交接是單一個工具呼叫 `transfer_item(from, to, item, action_id, source_event_id?)`，先驗證全部條件，再在同一個 `mutate_tool_state` 裡改兩邊的背包。要嘛全部發生，要嘛完全不發生。`action_id` 是由呼叫端擁有的穩定 id，會傳給狀態交易帳本（`run_snapshot` 的 `action_id`）；第一次已提交的呼叫若被重送，回傳原本的收據，而不是「沒有持有該物品」這類拒絕。`source_event_id` 仍是可選的紀錄用中繼資料。
2. **先驗證再改動。** 下列情況會拒絕交接，附上 Keeper 可以使用的原因，而且什麼都不寫入：給出者不是行動玩家的角色（發言者是 KP Assistant 時除外）；接收者不存在、不是現役角色、或與給出者相同；物品不在給出者的清單中（要求正規化後完全相符，不退回子字串）。
3. **只改自己的角色，除非用交接。** `add_carried_item` 與 `remove_carried_item` 只作用於行動玩家自己的角色（欄位是 `ToolCall.actor_id`，但**目前沒有填**：一般的背包呼叫走 `tool_gateway`（`app/agents/tool_gateway.py`）的 `else` 分支，呼叫 `execute_tool` 時沒帶 `actor_id`，兩條更正路徑建立 `ToolCall` 時也用空的預設值）。所以把 `actor_id` 經由 gateway 與兩個更正呼叫點傳下去是實作的**第一步**，要在加上檢查之前完成；沒有它就啟用檢查，會拒絕每個一般玩家的 add／remove。別的調查員的背包只能透過 `transfer_item` 改變，或發言者是 KP Assistant。更正的路徑（`correction_adjudication`、`natural_corrections`）仍可運作，因為它們在把提出更正者的 id 當作 `actor_id` 傳入之後，作用於提出更正者自己的角色。
4. **移除要說明原因。** `remove_carried_item` 多一個 `reason`：`consumed`、`lost`、`dropped` 或 `destroyed`。`given`／交出會被拒絕，並指向 `transfer_item`，所以單獨的移除不能再冒充交接。`consumed_or_removed_items` 保留原因；交接寫入另一份 `inventory_transfers` 紀錄（id、回合、from、to、物品），不再算成消耗。
5. **交接守恆物品，但不決定獨特性。** `transfer_item` 剛好把一筆清單項目從給出者移到接收者，所以各調查員清單的項目總數不變。重複取得仍然合法（兩位調查員可以各找到一支手電筒，全隊發放就是重複的 `add_carried_item`）。要把物品視為獨一無二的道具，需要每樣物品的身分或來源紀錄，而字串清單沒有這些；見「沒做的」。
6. **回覆與狀態一致。** 當回合以降級收場，訊息會說出已經提交了什麼，來源是工具事件而不是模型文字（「已提交：D 把〈古書〉交給 B」），讓做到一半的回合不再讀起來像什麼都沒發生。這擴充 `turn_fallback.guidance`。

## 變更（給實作用）

- 新的處理函式 `inventory.transfer_item` 與工具 schema（`keeper_tools/registry.py`）；`add_carried_item`／`remove_carried_item` 的描述說明交接時不要用它們。
- `tool_gateway` 與兩個更正呼叫點：把 `actor_id` 傳進背包呼叫（第一步，在任何檢查之前）。
- `inventory.py`：依 `ToolCall.actor_id` 檢查是否為自己的角色；物品要完全相符；移除加上 `reason`；狀態新增交接紀錄（`inventory_transfers`，與 `consumed_or_removed_items` 同樣會存檔）。
- `turn_resolution._mutation_evidence`：成功的 `transfer_item` 事件就是一次已驗證的交接；先移除再加入的配對比對在遷移期間保留，等提示不再產生成對呼叫之後移除。
- 提示文字中要 Executor 用 add／remove 做交接的地方（`turn_context.py`、`prompt_builder.py` 的 "Equipment Consistency"）改成交接時呼叫 `transfer_item`。
- 可觀測性：`inventory.transfer`（已提交）與 `inventory.transfer.refused`（含原因）事件；`scripts/summarize_turn_log.py` 統計被拒絕的次數。
- 工具額度：一次交接用掉四個工具中的一個，而不是兩個。

## 實作必須包含的驗證

- 每個觀察到的案例都是回歸測試：回合 47 與 199（沒有接收者的移除必須不可能發生）、56（沒有對應移除的加入）、140（交接之後逾時的四工具回合：物品在接收者手上，回覆也這麼說）。
- 原子性：在兩次背包寫入之間注入失敗，兩邊背包都不變。
- 拒絕情況：給出者錯誤、接收者不存在、接收者等於給出者、沒有持有該物品、只有子字串相符；每一種都什麼都不寫入。
- 守恆：交接後各調查員清單的項目總數不變，接收者已有的同名物品會變成第二筆。
- 冪等：同一個 `action_id` 重複呼叫會回傳原本的收據，且什麼都不改。
- `actor_id` 經由 gateway 與更正路徑到達背包工具；一般玩家對自己角色的 add／remove 仍然可用。
- `reason=given` 的移除被拒絕；`consumed` 被接受並記錄。
- 更正的路徑仍然把物品加到提出更正者的角色。
- 降級訊息只列出已提交的交接，不多列別的。

## 沒做的

- 單一道具，以及沒有人攜帶時物品在哪裡（留在房間、在 NPC 手上，例如回合 199 的房東）。這需要每樣物品的身分或來源紀錄、每樣物品的位置，以及劇本端哪些物品是道具的清單；這是較大的變更，應等到有證據顯示發生頻率再做。
- 已存檔遊戲中做到一半的交接，以及先前測試遺失的物品如何找回。
- 全隊發放（「每人各拿一份」）：各自對每個角色做一般的 `add_carried_item`。
- Executor 逾時後重試回合；這是另一個決定（已提交任何東西的回合不能安全重播）。
