# 以實測結果決定的回合延遲優先序

[English](measured_turn_latency_priorities_design_spec.md)

狀態：**提案中，等待設計審查**。基底：`main_v2` 的 `5961f2b`。

## 0. 這份文件為什麼存在

已經有三份規劃文件描述本專案的延遲工作：`main_v2_detailed_fix_plan.md`、`main_v2_detailed_fix_plan_ux_latency_v2.md`、`main_v2_python_optimization_safety_addendum.md`。三份都明確聲明未執行測試套件、未進行正式 Discord 測試、未做真實 LLM 效能評估。它們訂下的約束是正確的，本文件不重新檢討那些約束。它們缺的是量測，因此其優先序沒有資料支撐。

本文件補上量測並據此重排工作順序。不新增 Agent、不新增資料庫、不引入提前結束規則，也不更動那三份文件建立的任何正確性契約。

### 0.1 證據基礎

兩次 session，皆取自 `~/coc_v2_log`。

**改動前**：17 份 log，涵蓋 2026-09-25 至 2026-09-27，止於事故那次 session —— 323 筆帶 request id 的請求、156 筆帶 usage 的模型請求、175 次鎖取得。這是 `edd2fd6` 之前的部署狀態。

**改動後**：`20260927-132223-807874`，2026-09-27 13:04–13:22 UTC，`edd2fd6` 合併 #102、#103、#104 之後的第一次執行 —— 15 個 executor 回合、77 筆帶 usage 的模型請求、22 次鎖取得、1,068 秒剖析。

本地 CPU 數字是對真實 85 KB 持久化狀態的直接 benchmark，不是剖析器歸因：本專案把 token 估算丟進 `asyncio.to_thread`，主執行緒剖析器不會歸因到那些工作。

### 0.2 改動後的 session 確定了什麼

已合併的修正在正確性上生效，但完全沒有碰到延遲。

| | 改動前 | 改動後 |
|---|---|---|
| executor 裁決為 `incomplete` | 9 次中 5 次（55%）| **15 次中 1 次（7%）** |
| `utf8_bytes_fallback` 事件 | 46/46 | **0** |
| `llm.tokenizer.unavailable` | — | **0** |
| `complete_for_action` | 6 次中 0 次 | 2 次中 1 次 |
| executor 中位／p90／max | 9.5s／17.9s／21.2s | **15.7s／35.3s／60.6s** |
| 端到端 p90 | 17.3s | **36.6s** |
| 快取命中：回合第 1 次請求 | 4.6% | **0.0%** |
| 快取命中：第 2 次 | 71.4% | 77.3% |
| 快取命中：第 3 次以後 | 64.7% | 79.0% |
| 快取命中：Narrator 型請求 | 18.3% | **5.1%** |
| conversation lock p99 | 54,706 ms | 56,948 ms |

15 個回合有 14 個通過驗證，分布於 `no_mechanics`（5）、`await_check`（5）、`resolved_without_check`（2）、`blocked`（2）。`await_check` 與 `blocked` 在改動前完全無法通過驗證。`retrieval_budget_exceeded` 實際觸發一次，誠實回報預算耗盡，而不是把它呈現為劇本依據缺失。

### 0.3 量測改變了計畫的哪些部分

| 受檢視的主張 | 來源 | 實測 | 判定 |
|---|---|---|---|
| 本地 CPU 工作應列入第一批 | addendum P3/P4/P5 | 每回合 6–10 ms；1,068 秒剖析中最高的應用層 frame 是 `get_async_client` 0.38s、`run_turn` 0.13s、`estimate` 0.01s | 降級 |
| `select_history()` 重複呼叫 `estimate()`，值得快取 | addendum P4 | 整份 log 只需 0.07 ms | 降級 |
| `_encoding()` 目前使用 `lru_cache` | addendum P4 | 已於 #102 換成帶重試期限的顯式快取 | 已過期 |
| 正常工具回合是 `b + 1` 次 Executor 加一次 Narrator | v2 UX.3 | 每回合中位 4 次、最多 10 次模型請求 | 確認正確 |
| 靜態 prompt 穩定、動態資料後置、以實際 usage 驗證快取 | v2 UX.5-D | 未實作；#97 與 #99 沒有動到 `prompt_config.py` 的 static 組裝 | 採納為 WP2 |
| 同團 gameplay 維持序列化，唯讀指令繞過鎖 | v1 F8.1 | 實測到的等待發生在 gameplay 回合之間，唯讀繞過碰不到 | 缺口 —— WP3 |
| —— | —— | 檢索現在主導延遲：`search_scenario` 佔 43 次工具呼叫中的 34 次 | 新增 —— WP4 |

每回合本地 CPU，直接 benchmark：

```text
keeper._build_static_prompt        0.01 ms
keeper._build_dynamic_prompt       0.10 ms
input_budget.estimate(static)      0.84 ms
input_budget.estimate(tools)       1.22 ms
input_budget.select_history(log)   0.07 ms
GroupState.from_dict               0.13 ms
state.to_dict() + json.dumps       0.14 ms
                        每回合合計   6–10 ms
```

## 1. WP1 —— 讓基準可重現

§0.2 的改動後基準已經存在，因此 WP2 與 WP3 不再被阻擋。缺的是重現能力：上述每個數字都是臨時算出來的，這讓後續工作包的前後對照無法重複執行。

交付物：`scripts/analyze_turn_latency.py`，對 runtime log 目錄唯讀，輸出的正是本文件各項判斷所依據的指標 —— 依請求位置與階段的快取命中率、conversation lock 等待分位數、每回合模型請求數與 input tokens、各 agent 耗時、工具呼叫組成、executor 裁決分布、劇本投影完整性。

不需要任何 runtime 改動：`observability.usage_fields` 已在記錄 `cached_input_tokens`，`lock.wait` span 也已帶有耗時。這加的是讀取器，不是觀測點。

驗收：該腳本能從 §0.1 指名的兩次 session 重現 §0.2 的每一個數字，兩次皆可，且不需手動編輯。

## 2. WP2 —— 把動態資料移到快取邊界之後

### 2.1 改動後的 session 把這一項從假設升級為結論

在 `edd2fd6` 之前，每回合第一次請求的命中率是 4.6%，而「路由散掉」是合理的解釋：沒有 `prompt_cache_key` 時，請求會落在任意快取節點，只有 `previous_response_id` 把同一回合的鏈綁在一起。

`prompt_cache_key` 已隨 #103 上線。回合**內**命中率因此升到 77.3% 與 79.0%。**但每回合第一次請求降到 0.0% —— 19 筆請求、387,171 input tokens，一個也沒有重用。** 因此路由不是障礙，邊界是結構性的。

`app/providers/openai_provider.py` 組成 `instructions = f"{static_system}\n\n{dynamic_system}"`，而 `dynamic_system` 帶有 HP/SAN、位置與檢索 context，每回合都不同。前綴比對必然在它開始處中斷，其後的一切——包含 9,527 tokens 的工具 schema——都無法跨回合重用。

「快取前綴始於 `instructions`」這點由改動前的 session 支持：當時第一次之後的請求快取量中位為 19,810 tokens，超過 `static + dynamic + tools`（7,972 + 1,284 + 9,527 = 18,783），因此已延伸到工具定義之後的 input items。

### 2.2 改動

`instructions` 只放 `static_system`，`dynamic_system` 改為第一則 input item。

```text
改前  [static 7,972 + dynamic 1,284][tools 9,527][input]
                          ^ 每回合改變，其後全部無法快取

改後  [static 7,972][tools 9,527][dynamic 1,284][user]
                                    ^ 邊界移到這裡
```

跨回合可快取上限從 7,972 提高到 17,499 tokens。

### 2.3 讓這件事不只是搬字的限制

`run_conversation` 有兩種 input 形態。沒有 `previous_response_id` 時送出選取後的 history 加使用者訊息；有 `previous_response_id` 時只送 `[{"role": "user", "content": new_message}]`，靠 `instructions` 帶當前狀態。

把 `dynamic_system` 移出 `instructions` 就拿掉了這個保證。鏈式分支必須一併修改，讓動態區塊在回合第一次請求時以 input item 送出並沿鏈繼承；`_commit_turn_result` 本來就會在回合之間讓 response chain 失效，所以新回合會重新送出。**只搬動區塊而沒有處理鏈式分支的實作，會讓模型讀到過期的 HP/SAN —— 那是正確性失敗，不是效能退步。**

### 2.4 本工作包排除的項目

把 `build_executor_static_prompt` / `build_narrator_static_prompt` 從 `INSTRUCTION + keeper_static_prompt` 改為 `keeper_static_prompt + INSTRUCTION`，讓 Narrator 能重用 Executor 剛暖好的 7,131 tokens 區塊，是另一個更小的改動。改動後的 session 讓它更有吸引力——Narrator 型請求從 18.3% 掉到 5.1%——但仍刻意延後，一是讓兩個效果維持可歸因，二是它把角色指令移到 7,131 tokens 的內容之後，屬於行為改動，需要自己的評估。

### 2.5 驗收

- 離線：動態區塊在兩種 input 形態下都確實送達模型，包含鏈式回合的每一次請求；以假 provider 斷言組成後的請求，而非某個 helper 的回傳值。
- 量測：以 WP1 的腳本對改動後的實際遊戲執行，對照 §0.2 的「改動後」欄。
- 正確性：先前帶有當前 HP/SAN/位置的每一次請求，改動後都必須仍然帶有。**丟失狀態的快取改善是失敗。**
- 可還原：以單一旗標即可回到原本的組成方式。

## 3. WP3 —— 界定並量測回合排隊

### 3.1 已合併的修正沒有改變這個問題

| conversation lock 等待 | 改動前 | 改動後 |
|---|---|---|
| 中位 | 0 ms | 0 ms |
| p90 | — | 15,886 ms |
| p99 | 54,706 ms | 56,948 ms |
| max | 63,343 ms | 56,948 ms |
| 超過 1 秒 | 175 次中 11 次 | 22 次中 6 次 |

鎖從收到訊息一路握到 Executor、Narrator、Guard、防雷掃描與 log 提交結束，兩次模型往返都包含在內。`_conversation_lock_with_notice` 在 10 秒後送一次通知，之後就沒有了，所以在 p99 的情況下，玩家還要再坐 45 秒，沒有任何後續訊號，也不知道自己是第一個還是第三個。改動後的 session 實際上讓情況更糟，因為 Executor 中位升到 15.7 秒、p90 升到 35.3 秒，而那正是下一位玩家排隊的時間。

現有 spec 與三份規劃文件都沒有針對這件事。`enhancement-conversation-lock-and-tool-loop-latency.md` 刻意不縮小鎖範圍。v1 F8.1 維持同團 gameplay 序列化、只把唯讀指令移出，碰不到這個發生在 gameplay 回合之間的等待。v2 UX.4 定義了 `T_turn_queue`、UX.5 要求量測 player starvation；兩者都沒有實作。

### 3.2 本工作包要做的事

1. 發出 `turn.queue` 事件，帶上實測等待時間、前方等待者數量、route 與 speaker role。這實作了 v2 的 `T_turn_queue`，並讓 starvation 可以按玩家而非按對話量化。
2. 在 `_ObservableConversationLock` 追蹤等待者深度，把位置帶進排隊通知，並以有上限的間隔更新，而不是在 p99 等待的其餘時間完全沉默。

只有觀測與訊息。不縮小任何鎖、不平行化任何回合、不更動任何狀態契約。

### 3.3 本工作包不做什麼，以及為什麼

在 Executor 提交後釋放 conversation lock，讓 Narrator 中位 5.0 秒的時間落在鎖外，是顯而易見的結構性收益，本文件**暫不提案**。一般回合的 Narrator 是 `tools=[]`，不會有變更逃出鎖外；`_commit_turn_result` 也本來就會在 state lock 下重新載入並拒絕過期 timeline。真正的阻擋風險是**順序**而非變更：下一位玩家的 Executor 會對著「敘事尚未貼出」的狀態裁決，於是玩家可能讀到一個引用了他還沒被告知的事件的結果。

這個風險正是 #97 的 mutation admission 與 #99 的 delivery envelope 要界定的範圍。因此本工作包只記錄提案與前提，等那兩者落地、且 WP1 的排隊指標能顯示是否有幫助之後再做。

### 3.4 驗收

- 每個有等待的排隊回合都出現 `turn.queue`，帶有等待時間、深度與 route；無爭用的取得則完全不出現。
- 通知回報位置並以有上限的間隔更新；在第一次通知前就解除的等待仍然不送任何訊息。
- 通知送出失敗仍然不能影響鎖的釋放——既有實作已防住，本次不得退步。
- WP1 的腳本能依 route 與 speaker role 分別輸出排隊等待分位數。

## 4. WP4 —— 交代修正新增的檢索往返

### 4.1 實測問題

改動後的 session 是靠「搜尋更多」換回正確性的。`search_scenario` 佔 **43 次工具呼叫中的 34 次（79%）**；其餘為 `skill_check` 4、`get_character_sheet` 3、`purchase_items` 1、`npc_skill_check` 1。

```text
executor 每回合 tool_call_count：  0:4  1:2  2:3  3:3  5:1  7:1  8:1
超過 30 秒的回合：  60.6s，8 次工具呼叫
                    35.3s，7 次工具呼叫
```

每多一輪工具就是多一次帶著整段前綴的模型請求。這次 session 每回合中位 4 次請求、最多 10 次，單一回合最多 263,484 input tokens。部署的 `MAX_TOOL_ITERATIONS` 是 12，所以回合還有成長空間。

**這是真實的權衡，不是缺陷**：排名候選的工作與「聚焦補查」的引導，正是讓 `incomplete` 從 55% 降到 7% 的原因。但它現在是延遲的主導項，而且三份規劃文件都沒有涵蓋——它們的基準早於這些合併。

### 4.2 範圍

本工作包是**先調查，再設計**。必須先從 WP1 的工具呼叫組成輸出，確認多出來的搜尋屬於哪一類：

- **同一份依據因為查詢換句話而重複取得** —— `79514ec` 的 delivered-fragment 追蹤本來就要防這個，若成立屬於缺陷；
- **先前行為跳過的真正新依據** —— 這是預期中的正確性收益，不得移除；
- **預算驅動的重試** —— 會顯示為 `retrieval_budget_exceeded`，WP2 透過釋出前綴預算可部分緩解。

在這個分類被量出來之前，本工作包不提出任何改動。不知道搜尋落在哪一類就去減少它，等於把這次剛證明的正確性換回去。

### 4.3 驗收

- WP1 的腳本能逐回合輸出送出的搜尋查詢、回傳的 record ids，以及同一回合內連續搜尋之間的重疊。
- 在指定任何改動之前，至少再用一次 session 把上述三類量化。

## 5. 執行順序

```text
WP1  可重現的基準      -> 無 runtime 改動
WP2  快取邊界          -> 證據最強；可用旗標還原
WP3  排隊可見度        -> 與 WP2 獨立；縮小鎖範圍維持延後
WP4  檢索往返          -> 在分類量出來之前只做調查
```

WP2、WP3、WP4 動到不同檔案，可分開審查。

## 6. 限制

- 改動後的 session 只有 15 個 executor 回合、77 筆帶 usage 的請求、22 次鎖取得，涵蓋一場遊戲的 18 分鐘。p99 分位數建立在少數幾個觀測值上，`complete_for_action` 的 1/2 樣本太小，不足以稱為趨勢。
- 兩次 session 的內容與程式碼都不同。延遲上升與工具呼叫組成一致，但未執行同劇本對照。
- WP2 建立在「供應商快取前綴從哪裡開始」的推論上，依據是快取 token 總量超過 `instructions + tools`。這與資料一致，但未對照供應商文件確認，這也正是 WP2 必須可用旗標還原、且必須實測而非假定的原因。
- 本文件未審查 `combat.py`、`dice.py`、`luck.py` 的規則正確性，也未審查 PDF 匯入路徑與 Discord UI 層。
