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

1. **一個步驟。** 調查員之間的交接是單一個工具呼叫 `transfer_item(from, to, item, quantity?, source_event_id?)`，先驗證全部條件，再在同一個 `mutate_tool_state` 裡改兩邊的背包。要嘛全部發生，要嘛完全不發生。動作 id **不是工具參數**：模型在收據遺失後重送呼叫時，可能自己編出不同的 id。由回合擁有的程式碼為每個不同的操作**配發**一個邏輯操作 id（`<回合 id>:transfer:<n>`，由 `tool_gateway` 的每回合計數器產生），與任何參數文字無關。為了分辨重新發出與新的操作，gateway 為每回合保存驗證後參數的*指紋*：兩端都解析成標準角色 id，加上物品與 `quantity`；指紋已登記的呼叫沿用那個操作的 id，所以用 id 指名角色的呼叫和用名字指名的呼叫是同一個操作。不使用 provider 的工具呼叫 id，也不使用呼叫的順序，因為重新發出的呼叫會有新的呼叫 id 與較後的順序。它與 `actor_id` 一起經由 `tool_gateway` 傳給狀態交易帳本（`run_snapshot` 的 `action_id`）。同一回合、同一個邏輯操作的呼叫被重新發出時，不論呼叫 id 或文字為何，都回傳原本的收據，而不是「沒有持有該物品」這類拒絕。`quantity`（預設 1；處理函式在變更之前會拒絕任何不是至少為 1 的整數的值，因為 gateway 把模型產生的輸入直接交給處理函式，沒有 schema 驗證）在同一個原子步驟裡移動那麼多筆同名項目，並且是邏輯操作的一部分，所以持有兩份的給出者要全部交出時，在單一呼叫裡要求 `quantity` 2。同一回合內第二個完全相同的呼叫（相同的 `from`、`to`、`item`、`quantity`）依定義就是重播，會得到原本的收據；收據會寫明移動了幾筆、給出者還持有幾筆。`quantity` 是那次呼叫移動的數量，不是累計目標，所以要告訴 Executor 把玩家要交出的所有份數都放進第一個呼叫。`quantity` 不同的呼叫指紋不同，是一個新的操作，會再多移動剛好那麼多筆，給出者沒有那麼多筆時則被拒絕。**重播適用的範圍：**Codex provider 在進入 gateway 之前有一道刻意設計的防護（`codex_provider.py`），同一回合內位元組完全相同的 `(name, arguments)` 呼叫會丟出 `codex_duplicate_tool_attempt`，因為工具丟出例外之後的相同狀態重試並不安全；這次變更不動這道防護。所以在 Codex 之下，原樣重新發出的呼叫會讓回合以降級收場，降級訊息依 `observed_outcomes`（規則 6）說出已提交的交接；帳本重播涵蓋能到達 gateway 的呼叫：參數形式不同但意義相同的重新發出（第一次用 id 指名角色、第二次用名字），以及 adapter 為每次發出配新呼叫 id 的各 provider 的所有呼叫。規則 4 的批次 `remove_carried_item` 也一樣：在 Codex 之下，原樣重新發出會丟出 `codex_duplicate_tool_attempt`，回合以降級收場，降級訊息依 `observed_outcomes` 說出已提交的移除（所以 `observe_tool` 也要把 `remove_carried_item` 的收據連同操作 id 一起投影），而帳本重播涵蓋等價但拼法不同、以及換了新呼叫 id 的情況。`source_event_id` 仍是可選的紀錄用中繼資料。
2. **先驗證再改動。** 下列情況會拒絕交接，附上 Keeper 可以使用的原因，而且什麼都不寫入：給出者不是行動玩家的角色（發言者是 KP Assistant 時除外）；接收者不存在、不是現役角色、或與給出者相同；物品不在給出者的清單中（要求正規化後完全相符，不退回子字串）。**兩端都不用模糊比對解析**：`from` 與 `to` 必須是標準的角色 id，或是沒有歧義的、正規化後完全相符的名字；交接不使用 `support.find_character` 的子字串退路，歧義或只有一部分的名字會被拒絕，並列出候選者。
3. **只改自己的角色，除非用交接。** `add_carried_item` 與 `remove_carried_item` 只作用於行動玩家自己的角色（欄位是 `ToolCall.actor_id`，但**目前沒有填**：一般的背包呼叫走 `tool_gateway`（`app/agents/tool_gateway.py`）的 `else` 分支，呼叫 `execute_tool` 時沒帶 `actor_id`，兩條更正路徑建立 `ToolCall` 時也用空的預設值）。所以把 `actor_id` 經由 gateway 與兩個更正呼叫點傳下去是實作的**第一步**，要在加上檢查之前完成；沒有它就啟用檢查，會拒絕每個一般玩家的 add／remove。別的調查員的背包只能透過 `transfer_item` 改變，或發言者是 KP Assistant。更正的路徑（`correction_adjudication`、`natural_corrections`）仍可運作，因為它們帶著可信的 `system_origin="correction"` 標記，不受這道「只改自己的角色」檢查約束：`correction_adjudication._apply` 把物品加到已驗證主張指名的調查員，不論是誰提出報告，所以豁免是依標記判斷，絕不假設目標是提出者自己的角色。
4. **移除要說明原因。** `remove_carried_item` 多一個 `quantity`（預設 1，驗證方式和交接相同：至少為 1 的整數），在同一個原子步驟裡移除那麼多筆同名項目，並像規則 1 一樣有回合配發的操作 id 與參數指紋，所以重新發出的移除會重播存下的收據，而不是再刪掉一份；以及 `reason`：`consumed`、`lost`、`dropped` 或 `destroyed`。`given`／交出會被拒絕，並指向 `transfer_item`，所以單獨的移除不能再冒充交接。原因是模型選的，所以它不是唯一的防線：規則 7 加上一道由伺服器端判斷的檢查，玩家自己的訊息正把某樣物品交給某人時，不論模型給什麼原因，都拒絕移除那樣物品。`consumed_or_removed_items` 保留原因；交接寫入另一份 `inventory_transfers` 紀錄（id、回合、from、to、物品、quantity、source_event_id），不再算成消耗。
5. **交接守恆物品，但不決定獨特性。** `transfer_item` 剛好把 `quantity` 筆清單項目從給出者移到接收者，所以各調查員清單的項目總數不變。重複取得仍然合法（兩位調查員可以各找到一支手電筒）。要把物品視為獨一無二的道具，需要每樣物品的身分或來源紀錄，而字串清單沒有這些；見「沒做的」。
6. **回覆與狀態一致。** 當回合以降級收場，訊息會說出已經提交了什麼，來源是工具事件而不是模型文字（「已提交：D 把〈古書〉交給 B」），讓做到一半的回合不再讀起來像什麼都沒發生。這擴充 `turn_fallback.guidance`。

7. **伺服器端的意圖檢查在第一個工具之前執行。** 由回合程式碼（不是模型）用保守的確定性偵測器分類玩家的訊息（交出／交給之類的動詞、說話者持有的物品、另一位調查員的名字，風格與其他回合意圖偵測器相同），把結果記在回合上，形式是**每樣物品一筆意圖的清單**，每筆是*交接*（物品、數量、接收者）、*接收交接*（物品、數量、給出者：另一位調查員把東西交給行動玩家）、有種類的*移除*（物品、數量、種類：用掉、遺失、丟棄或毀壞）或*多接收者發放*（物品、接收者們），其中 `quantity` 預設為 1，表示訊息分配給那筆意圖的同名項目數量，所以像「把書給 B，然後丟掉鑰匙」這樣的複合訊息，同時產生書的交接與鑰匙的丟棄，下面每一項保證都只對那樣物品自己的意圖檢查。給出者持有數筆同名項目時，每筆意圖是項目的*分配*而不是對這樣物品的禁令：同一樣物品交接 1 筆並丟棄 1 筆，只要分配加起來不超過持有數就都被接受，超過該意圖數量的呼叫則被拒絕。工具 gateway 先把操作指紋已登記的呼叫分類為**重播**（規則 1）：回傳存下的收據、不消耗任何分配，所以用完的分配永遠不會讓提交之後重新發出的呼叫到不了帳本。只有指紋是新的呼叫，才會在任何背包變更之前對照這份清單檢查：交接意圖對某樣物品的分配用完之後，超出移除意圖分配的那樣物品的 `remove_carried_item`，不論模型給什麼 `reason`，都被拒絕並指向 `transfer_item`；玩家發起的 `transfer_item` 只有在符合已明確偵測到的交接意圖（同一樣物品、同一個標準接收者）時才會被接受，否則失敗時關閉並附確認問題，所以模型的錯誤呼叫無法把玩家的物品移給訊息從未提到的另一位調查員（KP Assistant 與更正路徑透過 `system_origin` 標記不受此限）；接收交接意圖指出某樣物品時，單獨把那樣物品 `add_carried_item` 到行動玩家自己角色會被拒絕，並說明要由給出者在自己的回合交出（或由 KP 透過 KP Assistant 套用），因為規則 2 要求給出者是行動玩家，這個新增只會讓物品重複，並重現回合 56 的有新增沒有移除的失敗；多接收者發放意圖指出某樣物品時，這個回合中那樣物品的每一次 `add_carried_item` 都在第一次執行前就被拒絕，所以發放不會做一半（包括對行動玩家自己的角色）。意圖檢查**兩個方向都判斷，而且失敗時關閉**。單獨的 `remove_carried_item` 只有在檢查已經明確把訊息分類為該物品的移除意圖（用掉、遺失、丟棄或毀壞，且對象不是另一位調查員）時才會被接受。偵測器同時分類移除的*種類*，記錄下來的 `reason` 就是偵測到的種類：模型自選而不同的 `reason`（例如玩家只是丟棄，模型卻填 `destroyed`）會被拒絕並指出偵測到的種類，所以持久的移除歷史不會把只是丟棄的物品記成毀壞；無法判斷時，移除被拒絕，並要 Executor 問玩家是什麼意思，所以模型自選的、不可信的 `reason` 永遠不會是唯一讓移除通過的東西。KP Assistant 或更正路徑的移除是系統擁有的，不受此限，**但只能透過伺服器擁有的准入標記**：回合程式碼在 `ToolCall` 上設定 `system_origin`（更正路徑與 KP Assistant 各設自己的值），由 `tool_gateway` 與 `actor_id` 一起往下傳。公開的 `source_event_id` 參數以及任何模型提供的欄位都不被信任。劇本事件沒有豁免：`event_obligations` 與 `obligation_gate` 目前不處理背包移除，所以移除物品的劇本後果會以一般模型工具呼叫的形式到達，和其他移除一樣要通過意圖檢查（見「沒做的」）。因此偵測器漏掉的說法只會多一個確認問題，不會弄丟物品。全隊發放的保證要等批次發放（見「沒做的」）。

## 變更（給實作用）

- 新的處理函式 `inventory.transfer_item` 與工具 schema（`keeper_tools/registry.py`）。`ToolSpec.kp_assistant` 預設為 `False`，`tools_for_speaker_role` 會省略沒有它的工具，`tool_dispatch.execute_tool` 也會拒絕它們，所以規則 2、3、7 對 KP Assistant 的豁免，只有在 registry 把 `transfer_item`、`add_carried_item`、`remove_carried_item` 標成 `kp_assistant=True`，同時也要標成 `kp_canonical_game=True`（不標的話，沒有開頭 `!` 的呼叫其 `kp_tool_result_creates_canon` 是 false，`commit_kp_ooc_turn_result` 會把回應記成非權威的 OOC 討論，儘管背包已改變，工具收據卻不進正式歷史）才能用；這次變更兩者都做，並測試 KP Assistant 的呼叫（有或沒有開頭 `!`）被接受並記為正式遊戲內容、玩家若想借用 KP 專屬的豁免（偽造或缺少 `system_origin`）就拿不到豁免，而玩家符合自己角色或意圖的呼叫則照常被接受；`add_carried_item`／`remove_carried_item` 的描述說明交接時不要用它們。
- 封閉值集（`CODING_STANDARDS.md`：封閉值集是型別）：`app/domain/models.py` 定義 `SystemOrigin = Literal["kp_assistant", "correction"]`、`InventoryIntentKind = Literal["handoff", "incoming", "removal", "grant"]` 與 `RemovalKind = Literal["consume", "lose", "drop", "destroy"]`（`remove_carried_item` 的 `reason` 參數透過同一個對照表，對照 `RemovalKind` 的過去式拼法來驗證），`ToolCall.system_origin` 與意圖紀錄都使用它們，所以拼錯的值會在型別檢查時失敗，而不是悄悄失去可信豁免或繞過原因比對。准入檢查用這些成員比較 `system_origin`，絕不用真值判斷。
- 回合交接契約：意圖清單是 `TurnPayload` 的鍵 `inventory_intents`，先在 `app/domain/models.py` 宣告，只由 `context_builder`（填入模型呼叫前事實的階段）寫入，由 `tool_gateway` 讀取；`tests/test_turn_payload_contract.py` 加上這個鍵與它的擁有者寫入端，其他模組寫入時測試會失敗。
- `tool_gateway` 與兩個更正呼叫點：把 `actor_id` 與 `system_origin` 准入標記傳進背包呼叫（第一步，在任何檢查之前）；`tool_gateway` 同時推導回合擁有的動作 id 並傳給帳本。
- `inventory.py`：依 `ToolCall.actor_id` 檢查是否為自己的角色；物品要完全相符；移除加上 `reason`；狀態新增交接紀錄（`inventory_transfers`）。`GroupState` 目前沒有這個欄位，而 `GroupState.to_dict()`／`from_dict()` 逐一列舉要存檔的欄位（`app/models.py`），所以欄位與它的序列化、反序列化要加在那裡，舊存檔缺少該欄位時預設為空。
- `turn_resolution._mutation_evidence`：成功的 `transfer_item` 事件就是一次已驗證的交接；先移除再加入的配對比對在遷移期間保留，等提示不再產生成對呼叫之後移除。
- 提示文字中要 Executor 用 add／remove 做交接的地方（`turn_context.py`、`prompt_builder.py` 的 "Equipment Consistency"）改成交接時呼叫 `transfer_item`。
- 可觀測性：`inventory.transfer`（已提交）與 `inventory.transfer.refused`（含原因）事件；`scripts/summarize_turn_log.py` 統計被拒絕的次數。
- 重播：`mutate_tool_state` 只回傳 `outcome.value`，重複時它是 `None`（`state_transaction._replay` 把持久化的資料放在 `TxResult.result`）。交接用 `ctx.set_result` 存下收據，重複的路徑回傳那份存下的結果，做法是用 `commit_for_snapshot` 處理重複，或擴充 adapter 回傳 `TxResult.result`。
- 收據投影：`turn_delivery.observe_tool`（每次呼叫都會記錄，包括冪等重播）把 `transfer_item` 投影進 `MechanicResult.observed_outcomes`（目前只有 `add_carried_item` 與 `remove_carried_item`），executor 結果保留它，這樣 `turn_fallback.guidance` 與 `prompt_config` 插入已提交細節的地方，才能說出在回合失敗前已經移動的交接。投影帶著邏輯操作 id，建構降級訊息之前依該 id 去除重複的結果，所以重播的呼叫不會讓同一次已提交的交接被報告兩次。
- 工具額度：一次交接用掉四個工具中的一個，而不是兩個。
- 回合意圖偵測器與 gateway 的准入檢查（規則 7），以及指出意圖的 `inventory.admission.refused` 事件。

## 實作必須包含的驗證

- 每個觀察到的案例都是回歸測試：回合 47 與 199（沒有接收者的移除必須不可能發生）、56（沒有對應移除的加入）、140（交接之後逾時的四工具回合：物品在接收者手上，回覆也這麼說）。
- 原子性：在兩次背包寫入之間注入失敗，兩邊背包都不變。
- 拒絕情況：給出者錯誤、接收者不存在、接收者等於給出者、沒有持有該物品、物品只有子字串相符、給出者或接收者的名字只有子字串相符或有歧義（另一位現役調查員的部分名字不能收到物品）；每一種都什麼都不寫入。
- 份數：持有兩筆同名項目的給出者用 `quantity` 2 全部交出，在一次提交裡變成沒有，接收者有兩筆；同一回合第二個完全相同的單份呼叫回傳第一份收據且什麼都不移動。
- Codex 重複防護，移除：在 Codex provider 之下，原樣重新發出的 `remove_carried_item` 同樣會丟出 `codex_duplicate_tool_attempt`，產生的降級訊息只說一次已提交的移除。
- Codex 重複防護：在 Codex provider 之下，原樣重新發出的 `transfer_item` 仍會丟出 `codex_duplicate_tool_attempt`，產生的降級訊息只說一次已提交的交接；拼法不同但等價的呼叫會到達 gateway 並重播存下的收據。
- 重播內容：第一次嘗試已提交之後，重新發出的呼叫回傳存下的收據（不是 `None`），包括模擬回應遺失的情況。
- 守恆：交接後各調查員清單的項目總數不變，接收者已有的同名物品會變成第二筆。
- 冪等：同一回合內重新發出的同一個呼叫，即使有新的 provider 呼叫 id、較後的順序與不同的模型文字，也會回傳原本的收據且什麼都不改；模型不能自選 id。
- 接收交接：「B 把書交給我」會在任何變更之前拒絕行動玩家對書的 `add_carried_item`，行動玩家發起的、從 B 出發的 `transfer_item` 也被拒絕（給出者不是行動玩家），並告知桌上書要等 B 交出才會移動。
- 重播與分配：在只有一份物品的交接意圖下，第一次數量 1 的 `transfer_item` 已提交，同一個呼叫在模擬收據遺失之後重新發出，會回傳存下的收據，不會因超出分配而被拒絕；而對這樣物品另一次不同的交接仍然被拒絕。
- 移除數量：「兩支手電筒都丟掉」是一個 `quantity` 2、`reason=dropped` 的 `remove_carried_item`；收據遺失後重新發出的同一個呼叫會回傳存下的收據，不再多移除任何東西，而 `quantity` 為 0 或非整數會在任何變更之前被拒絕。
- 重複項目：持有兩支手電筒時，「給 B 一支，另一支丟掉」接受一次交接與一次 `reason=dropped` 的移除，並拒絕超出所述分配的第二次交接或第二次丟棄。
- 複合訊息：「把書給 B，然後丟掉鑰匙」在同一個回合接受書的 `transfer_item` 與鑰匙 `reason=dropped` 的 `remove_carried_item`，並拒絕丟棄書、或交接鑰匙。
- 交接准入：訊息沒有交接意圖、或指名的物品或接收者與偵測到的不同時，`transfer_item` 被拒絕並附確認提示；相符的則被接受。
- 原因綁定：玩家訊息是丟棄物品而模型填 `reason=destroyed` 時被拒絕並指出 `dropped`；相符的原因被接受並記錄。
- 降級訊息中的重播：第一次呼叫及其重播之後的降級訊息，只提到這次交接一次。
- 數量：省略 `quantity` 時視為 1（單純的三個參數的呼叫）；`0`、負數與非整數在任何變更之前都被拒絕；`source_event_id` 在存檔重新載入後仍在紀錄中。
- 存檔：`inventory_transfers` 在存檔後重新載入仍在，沒有該欄位的舊快照載入後是空清單。
- 失敗時關閉的移除：訊息無法被檢查分類時，單獨的移除被拒絕並附確認提示；訊息被分類為移除意圖時則被接受。
- 意圖准入：訊息把物品交給指名的調查員時，即使模型給 `reason=lost`，移除那樣物品也被拒絕；訊息把物品發給多位調查員時，這個回合的每一次 `add_carried_item` 都被拒絕，包括對行動玩家自己角色的那一次；同一回合中無關的移除仍然可用。
- `actor_id` 經由 gateway 與更正路徑到達背包工具；一般玩家對自己角色的 add／remove 仍然可用。
- `reason=given` 的移除被拒絕；`consumed` 被接受並記錄。
- 更正的路徑仍然修復已驗證主張指名的調查員，包括提出者以外的人，靠的是 `system_origin="correction"`；沒有標記的同一個呼叫會被所有權檢查拒絕。
- 降級訊息只列出已提交的交接，不多列別的；回合之後逾時時也一樣（交接出現在 `observed_outcomes`）。

## 沒做的

- 劇本造成的移除（劇本規則從角色身上拿走物品）。帶有可信劇本來源的移除，需要在 `event_obligations`／`obligation_gate` 加上背包移除的擷取與確定性執行，而它們目前只處理檢定、傷害與理智。在那之前，這種移除在意圖檢查無法分類的訊息之下會被拒絕，由 Executor 詢問玩家，或由 KP 透過 KP Assistant 套用。

- 單一道具，以及沒有人攜帶時物品在哪裡（留在房間、在 NPC 手上，例如回合 199 的房東）。這需要每樣物品的身分或來源紀錄、每樣物品的位置，以及劇本端哪些物品是道具的清單；這是較大的變更，應等到有證據顯示發生頻率再做。
- 已存檔遊戲中做到一半的交接，以及先前測試遺失的物品如何找回。
- 全隊發放（「每人各拿一份」）。不能用重複的一般 `add_carried_item`：規則 3 會拒絕對隊友的新增，而且四個工具蓋不了五位調查員，做到一半就會重現背包只更新一部分的問題。在有可信的、由系統擁有的批次發放（先驗證所有接收者，再於同一個 `mutate_tool_state` 套用到全部，並有自己由回合推導的動作 id）之前，玩家回合的訊息被意圖檢查（規則 7）認出是這種請求時，會在第一次變更之前整個被拒絕；它漏掉的說法則不保證原子。這個操作需要另外的規格。
- Executor 逾時後重試回合；這是另一個決定（已提交任何東西的回合不能安全重播）。
