# 以實測結果決定的回合延遲優先序

[English](measured_turn_latency_priorities_design_spec.md)

狀態：**WP1、WP2、WP3.2–WP3.5、WP5 已實作（WP3.5 以預設關閉的旗標控制）；WP4 提案中**。基底：`main_v2` 的 `5961f2b`。

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

**已實作。** `scripts/analyze_turn_latency.py` 對改動後的 session 精確重現 §0.2，也接受目錄以處理改動前的全集。一次讀入全部 59 份 log 顯示排隊問題比兩個單一 session 呈現的更糟：n=542、p90 38,103 ms、**p99 105,950 ms、max 131,189 ms**、177 次取得超過 1 秒。那是跨不同版本程式碼的彙總，也包含測試用的 log，因此 §0.2 的單一 session 數字仍是比較基準；全集數字界定的是「實際觀測到最糟的排隊有多糟」。

## 2. WP2 —— 把每回合變動的區塊放到所有不變內容之後

### 2.1 改動後的 session 確立了什麼，又沒有確立什麼

在 `edd2fd6` 之前，每回合第一次請求的命中率是 4.6%，而「路由散掉」是合理的解釋：沒有 `prompt_cache_key` 時，請求會落在任意快取節點。

`prompt_cache_key` 已隨 #103 上線。回合內命中率因此升到 77.3% 與 79.0%。**但每回合第一次請求降到 0.0% —— 19 筆請求、387,171 input tokens，一個也沒有重用。** 路由不是障礙，邊界是結構性的。

本工作包的初版據此推論「把 `dynamic_system` 移出 `instructions`」即可。**那是錯的，而且在實作之前就被真實 API 的 A/B 否證。**

### 2.2 對真實 API 的實測

`scripts/experiments/ab_prompt_cache_boundary.py` 以真實 state、真實工具 schema 重放紀錄中的玩家訊息，每回合一次請求，並讓 dynamic 區塊**每輪都不同** —— 真實遊戲不會重複，這正是紀錄中回合第一次請求命中 0.0% 的原因。

50 輪中有 47 筆回報 usage，且 47 筆的 dynamic 區塊全部相異：

| 組別 | 請求形狀 | 命中率 |
|---|---|---|
| A —— 現況 | `instructions = static + dynamic`、`tools`、`[user]` | **2.2%** |
| B —— 本文件初版提案 | `instructions = static`、`tools`、`[dynamic, user]` | **0.0%** |
| C —— 診斷，拿掉區塊 | `instructions = static`、`tools`、`[user]` | 83.2% |
| D —— 採用 | `instructions = static`、`tools`、`[user, dynamic]` | **92.9%** |

B 組以自己的 30 輪量測，結果比什麼都不做還糟。因此「離開 `instructions`」不是關鍵；關鍵是**區塊必須位於玩家訊息之後**，讓所有每回合不變的位元組都排在所有會變的位元組之前。C 組顯示 developer role 本身不是障礙。

50 輪中未命中的 input 從 829,642 tokens 降到 61,194，減少 92.6%。穩定的快取前綴實測為 17,089 tokens，對照本地估算的 `static 8,560 + tools 9,690`。

**B 組為何是 0.0% 而不是仍然快取 `instructions + tools`，本文件沒有解釋。** 資料顯示它就是如此；機制未能確立，也不以任何猜測代替。

### 2.3 改動

```text
改前  [instructions: static + dynamic][tools][history][user]
                              ^ 每回合改變，其後全部無法快取

改後  [instructions: static][tools][history][user][dynamic]
                                                   ^ 移到所有穩定位元組之後
```

兩種 input 形態都要送達。鏈式形態——呼叫端傳入 `previous_response_id` 時——只送新的使用者訊息、靠 `instructions` 帶當前狀態，因此該分支也必須附加此區塊。同一回合的後續迭代會與該回合其他項目一起沿 response chain 繼承。

`OPENAI_DYNAMIC_PROMPT_AFTER_INPUT` 可在不更動提示詞內容的情況下還原原本的組成。

### 2.4 本工作包排除的項目

把 `build_executor_static_prompt` / `build_narrator_static_prompt` 從 `INSTRUCTION + keeper_static_prompt` 改為 `keeper_static_prompt + INSTRUCTION`，讓 Narrator 能重用 Executor 剛暖好的 7,131 tokens 區塊，是另一個更小的改動。改動後的 session 中 Narrator 型請求命中 5.1%。仍刻意延後，一是讓兩個效果維持可歸因，二是它把角色指令移到 7,131 tokens 的內容之後，屬於行為改動，需要自己的評估。

### 2.5 驗收

- 離線：區塊在兩種 input 形態下都送達模型（含鏈式），且位於玩家訊息之後；以假 provider 斷言組成後的請求。**已達成。**
- 組成事件不因區塊改走 input 而重複計算。**已達成。**
- 旗標能完全還原原本的組成。**已達成。**
- 量測：50 輪、dynamic 每輪相異，2.2% → 92.9%。**已達成。**
- 正確性：先前帶有當前 HP/SAN/位置的每一次請求，改動後都仍然帶有。**離線與實跑皆已達成。**

### 2.5.1 實跑驗證

`scripts/experiments/live_narration_ab.py` 以同一組回合跑兩次完整的 Executor、Narrator、Guard 與防雷掃描，對象是 live 資料庫的複本、兩組之間還原，且在 `app.config` 匯入前就改寫儲存路徑，因此完全不碰 live 資料。

新的擺放位置下敘事完好。兩組都正確報出攜帶物品，細到左輪手槍的六發子彈；兩組都在後續行動之前，先把玩家擋在未處理的偵查或聆聽檢定上；兩組都讓角色留在地下室儲藏室，沒有憑空生出房間。四輪的回覆長度中位為 103 對 103 字元，三輪為 80 對 71。

真實 pipeline 也確認了 harness 無法確認的部分，因為它會在回合內以 `previous_response_id` 串接請求：

| | 整體命中 | 回合起始的請求 |
|---|---|---|
| 現況 | 63.2% | 五筆**全部 0.0%** |
| WP2 | **85.8%** | 58–79%，沒有任何一筆為零 |

現況的那些零正好是回合開頭的 Executor 請求與 Narrator 請求；在 WP2 下，該次執行沒有任何一筆請求完全沒命中。

延遲沒有拉開差距：三輪為 20.3 秒對 18.7 秒、四輪為 19.8 秒對 13.7 秒，而且兩組隨著遊玩而狀態分歧——A 組建立了偵查檢定，B 組建立的是聆聽——工具次數因此不同，執行順序也未受控。這裡沒有任何資料支持任一方向的延遲主張。

### 2.6 這項改動買不到什麼

延遲沒有改善：50 輪中 A 組中位 5.44 秒、D 組 5.71 秒，差距落在先前各組之間已可見的執行間波動內。**這是 token 與成本的結果，不是延遲的結果。**

快取命中的 input 是否仍全額計入限制本部署的 rate limit，並未確立，因此不宣稱任何 rate-limit 效益。

harness 每回合只發一次請求、提供工具但不執行，因此它隔離的是跨回合重用，對回合內的工具迴圈（本來就已命中 77–79%）沒有任何說明力。

### 2.7 量測過程中發現的兩個 harness 缺陷

兩者都曾產生錯誤答案才被抓到，現在腳本會自己回報。

第一次跑 C 與 D 組時只用了開頭幾輪，而那些輪次來自沒有 active character 的 state。每輪擾動因此完全沒作用、dynamic 區塊固定不變，arm D 讀出 93% —— 一個什麼都證明不了的數字。腳本現在會統計每組的相異 dynamic 區塊數，並在少於兩個時明講。

第一次跑 50 輪時以 HP 與 SAN 做週期性擾動，區塊每 35 輪重複一次，於是後面的輪次可以命中前面輪次的前綴。在該缺陷下 arm A 讀出 88.7%，而讓每輪唯一之後是 2.2%。腳本現在會回報相異區塊數對可用請求數，並標示重複。

## 3. WP3 —— 界定並量測回合排隊

### 3.1 已合併的修正沒有改變這個問題

| conversation lock 等待 | 改動前 | 改動後 |
|---|---|---|
| 中位 | 0 ms | 0 ms |
| p90 | — | 15,886 ms |
| p99 | 54,706 ms | 56,948 ms |
| max | 63,343 ms | 56,948 ms |
| 超過 1 秒 | 175 次中 11 次 | 22 次中 6 次 |

鎖從收到訊息一路握到 Executor、Narrator、Guard、防雷掃描與 log 提交結束，兩次模型往返都包含在內。以改動後的中位數計，持有時間的組成為：

```text
build_context ~1 s + Executor 15.7 s + Narrator 5.0 s + Guard/防雷 ~0 + commit ~0
                                                              ≈ 21.7 s
```

`_conversation_lock_with_notice` 在 10 秒後送一次通知，之後就沒有了，所以在 p99 的情況下，玩家還要再坐 45 秒，沒有任何後續訊號，也不知道自己是第一個還是第三個。改動後的 session 實際上讓情況更糟，因為 Executor 中位升到 15.7 秒、p90 升到 35.3 秒，而那正是下一位玩家排隊的時間。

現有 spec 與三份規劃文件都沒有針對這件事。`enhancement-conversation-lock-and-tool-loop-latency.md` 刻意不縮小鎖範圍。v1 F8.1 維持同團 gameplay 序列化、只把唯讀指令移出，碰不到這個發生在 gameplay 回合之間的等待。v2 UX.4 定義了 `T_turn_queue`、UX.5 要求量測 player starvation；兩者都沒有實作。

### 3.2 觀測與訊息，現在就能安全進行

1. 發出 `turn.queue` 事件，帶上實測等待時間、前方等待者數量、route 與 speaker role。這實作了 v2 的 `T_turn_queue`，並讓 starvation 可以按玩家而非按對話量化。
2. 在 `_ObservableConversationLock` 追蹤等待者深度，把位置帶進排隊通知，並以有上限的間隔更新，而不是在 p99 等待的其餘時間完全沉默。

不縮小任何鎖、不平行化任何回合、不更動任何狀態契約。

**已實作。** 測試時發現一項必須修正的地方：等待者無法自己重新計算位置，因為純計數器分不出「比我早到」和「比我晚到」，而第一版實作因此把自己也算進去。位置現在改成「進入時的快照」扣掉 `completed` 計數的進度，於是每完成一個回合就剛好減一，排在我後面的回合也不會把數字撐大。

### 3.3 前置缺陷：RAG 索引快取沒有 singleflight

`app/scenario_rag.py` 的 `_index_cache` 是模組層級的裸 dict，沒有任何鎖。`get_index()` 在記憶體命中或磁碟命中時直接回傳，但 miss 時會呼叫 `build_index()`——一次 embeddings 往返——接著透過 `_save_index_to_disk()` 寫入 `scenario_indexes`（`db.set_json`，`app/scenario_rag.py:677`）。`get_record_index()` 結構相同。

因此同一個 key 的兩個並發 miss 會各自付一次 embeddings 呼叫、各自寫入一次。同一個 `group_id` 的 payload 內容相同，所以後寫贏是無害的，但 API 成本翻倍。這件事**今天就已經存在**，因為 conversation lock 只序列化單一對話內部；列在這裡是因為 3.4 會把競爭窗口擴大到同一對話的兩個回合。

與任何鎖改動獨立修正：對 `get_index` 與 `get_record_index` 加上 per-key singleflight，讓第二個呼叫者等待第一次建構完成，而不是自己再建一次。這正是 addendum 那條註記的具體案例——`lru_cache` 式的快取是 thread-safe 但不保證只執行一次，昂貴工作需要明確的 singleflight。

**已實作。** 每個 cache key 一把 `threading.Lock`，只在 miss 時取得，讓記憶體與磁碟路徑維持無鎖；等到鎖的呼叫者會在鎖內重新檢查快取。以四個執行緒競爭同一 key 驗證：一次 `build_index`、一次寫入；移除實作後則是各四次。

### 3.4 把 context 建構移出鎖，在 3.3 之後

`app/agents/context_builder.py` 的 `build_context` 本身不寫入團狀態；預取會讀取已儲存的記憶來綁定來源版本。主要成本是劇本與記憶 RAG 的往返，約 1 秒，可以在取得鎖之前執行，但有兩個條件：

- 3.3 必須先落地，因為 `build_context` 會走到 `get_index`，因此可能觸發那條未受保護的重建路徑。
- 取得鎖後須重新讀取 state 快照，只有檢索結果可列為沿用候選。章節視窗、來源文字及記憶來源的綁定仍須相符；可變狀態衍生的 payload 一律重建。

若檢索在既有排隊期間完成且來源未變，可減少約 1 秒、約持鎖時間的 5%；沒有排隊的回合仍可能在取得鎖後等待檢索。這一項最初被評估為純讀重排，實際上不是，而該次重新評估正是 3.3 存在的原因。

**已實作，而且範圍比標題窄。** `build_context` 的 payload 帶有 `state`、`character`、`resolved_check_events` 與更正投影——全都是可變狀態衍生的，在鎖前建構就會過期。**只有檢索會跨過鎖**：`context_builder.prefetch_retrieval` 走一般路徑，只保留 `rag_context`／`memory_context`，並在搜尋前記下來源綁定，包括劇本變體、章節視窗、來源文字、timeline、戰鬥狀態、當前角色、摘要與實際記憶片段。`build_context` 在鎖內重新核對，來源有變就重新搜尋。

決定權在 `supervisor.prefetch_retrieval` 而非 router，這樣查詢就不會和 `run_turn` 餵給 `build_context` 的內容分歧：IC/OOC 混合訊息只對它的 IC 片段預取。以下情況回傳 `None`，讓搜尋留在鎖內、與改動前完全相同：OOC 路由、發話者持有 WP5 會從 state 回答的 Luck 決定、以及任何失敗——失敗會被記錄並吞掉，因為漏掉一次預取的代價是一秒，永遠不是一個回合。

**有一個既有測試是「卡住」而不是「失敗」**：`test_keeper_priority_integration` 的 `FakeSupervisorRunner` 固定了 `run_turn` 的關鍵字簽名，新參數在回合內拋出 TypeError，阻塞事件因此從未被設定，情境就無限等待。該假物件已更新簽名。

### 3.5 延後：在敘事之前釋放鎖

目前這一把鎖同時保護三件不同的事：

| | 必須序列化 | 一般回合的 Narrator 需要嗎 |
|---|---|---|
| 狀態變更順序 | 是 | **不需要** —— Narrator 是 `tools=[]` |
| 訊息貼出順序 | 是 | 需要 |
| 回合隔離 | 是 | 需要 |

Narrator 需要後兩者，不需要第一項。因此拆法是：

```text
mutation lock    收訊息 -> build_context -> Executor -> 狀態提交 -> 釋放
posting ticket   進入時依到達順序領號；貼文前依號序等待
```

敘事因此能與下一位玩家的 Executor 重疊，而訊息順序由 ticket 保持不變。與 3.4 合計，持有時間中位可從 ~21.7 秒降到 ~15.7 秒，p90 從 ~47 秒降到 ~35 秒。

#### 重讀合併後的程式碼改變了什麼

本節先前延後的理由是：失敗情境——A 回合的 Narrator 在 B 回合的 Executor 已對 A 已提交狀態裁決之後失敗——會由 #97 的 mutation admission 與 #99 的 delivery envelope 界定。**兩者都已合併，而且都沒有界定它。**

`mutation_admission` 的 hold 由 `detach` 設置，而 `detach` 只在 `tool_gateway` 中工具 worker 逾時或被取消時被呼叫。**Narrator 失敗不會留下任何 hold**：Executor 的 worker 早就 settle 了。這套機制涵蓋的是「被放棄的工具 worker」，不是「失敗的敘事」。

而且那個失敗交錯**本來就存在**。今天 B 的 Executor 一樣是對著 A 已提交的狀態裁決，因為 A 在自己的 Executor 階段就提交了；B 只是開始得比較晚。Narrator 失敗今天也已經會在「狀態已提交但沒對任何人描述」的情況下回傳 fallback 文字。提早釋放改變的是 **B 何時開始**，不是 **B 看到什麼**。

#### 兩個真正的阻擋因素

**帶工具的 Narrator 會改狀態。** `narrator.py:44` 對 `resolved_check_followup` 與 `opening_fallback` 設定 `tool_enabled`，而 #99 還在那個迴圈裡加了到達提交。因此提早釋放**只對 Narrator 為 `tools=[]` 的一般 `player_action` 回合成立**。

**log 提交發生在敘事之後。** `_commit_turn_result` 在 Narrator 之後執行，把本回合的玩家訊息與回覆附加到 `state.log`，而那正是後續提示詞會讀的歷史。若 B 先於 A 提交，歷史順序就錯了。因此 posting ticket 必須涵蓋**提交與貼文兩者**，不能只涵蓋貼文。

#### 為什麼本次不實作

對話鎖是在 `router.py` 取得的，包住 `_handle_ordinary_text_message_locked`，而那涵蓋 `run_turn` 以及負責貼出回覆的 post-turn maintenance。要在敘事前釋放，就得**從 `run_turn` 內部釋放**——那裡有十一條 return 路徑——並讓該釋放對 router 自己的 `async with` 具備冪等性。既有程式碼本身就帶著針對這個危害的警告：例外逃出清理程序而洩漏已取得的對話鎖，會「**永久洩漏該鎖，並讓該對話之後的每一個指令死鎖，直到行程重啟**」。

失敗模式是**整個頻道死鎖**，而收益是 Narrator 中位 5.0 秒（約 21.7 秒持有時間的一部分）。

#### 已實作，以 `NARRATION_OUTSIDE_MUTATION_LOCK` 控制，預設關閉

原本草案裡的 posting ticket **不需要**。兩把鎖都是 FIFO，而 mutation 鎖本來就序列化了 Executor，因此回合抵達敘事階段的順序等於它抵達 mutation 的順序——一把普通的第二把鎖就能保住訊息順序：

```text
mutation 鎖(FIFO) -> Executor -> 釋放 -> narration 鎖(FIFO) -> Narrator、提交、貼文
                                    ^ 下一位玩家的 Executor 從這裡開始
```

`locks.TurnHandoff` 負責記住這個回合還持有哪些鎖，router 的兩個 context manager 各 yield 一個，並在 `finally` 呼叫 `close()`。`close()` 只釋放「仍然持有的」，因此 `run_turn` 裡的十一條 return 路徑**不需要逐條處理**：沒交接的回合照舊釋放 mutation，交接過的則釋放 narration。`to_narration()` 具冪等性。

`run_turn` 只在一個地方交接——reducer 之後——且僅限 `turn_kind == "player_action"`。`resolved_check_followup` 與 `opening_fallback` 保留 mutation 鎖到最後，因為 `narrator.py:44` 給它們受限工具集，而 #99 在該迴圈內提交到達。

**旗標預設關閉**，因為收益以秒計，而失敗模式是頻道停擺到重啟為止。已驗證的是**鎖的帳目**——每個測試最後都斷言哪些鎖已釋放，涵蓋交接與不交接、close 兩次、交接兩次、以及交接後拋例外——外加「下一回合的 Executor 與本回合敘事重疊但貼文順序不變」。**未驗證的是真實併發負載下的行為。**

有一個值得記錄的危害：在 narration 鎖被洩漏的變異下，排序測試是**卡死而不是失敗**，因為下一個回合永遠在等。現在它以有上限的時間等待，外洩會在數秒內失敗。同樣形狀在別處咬了兩次——`FakeSupervisorRunner` 固定了 `run_turn` 的關鍵字簽名，新參數在回合內拋錯、阻塞事件從未設定、整個套件卡住；它現在容忍新增參數。

KP 優先 gate 在作用時，gate 仍然跨越敘事期被持有，因此有 KP 助手的對話不會得到重疊效果。這一點維持現狀。

### 3.6 驗收

- 每個有等待的排隊回合都出現 `turn.queue`，帶有等待時間、深度與 route；無爭用的取得則完全不出現。
- 通知回報位置並以有上限的間隔更新；在第一次通知前就解除的等待仍然不送任何訊息。
- 通知送出失敗仍然不能影響鎖的釋放——既有實作已防住，本次不得退步。
- 同一個索引 key 的並發 miss 只產生一次 `build_index` 與一次寫入，且驗證時不使用真實 embeddings 後端。
- 移出鎖的 context 建構會在鎖內重新讀取 state，並有測試證明落在空隙期間的變更會被觀察到而非被覆寫。
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

## 5. WP5 —— 待決 Luck 時不必花模型請求就能回覆

### 5.1 實測問題

一次 20 輪的實跑顯示了待決 Luck 的代價。**決定本身很便宜**：`/coc check` 與 `/coc luck` 由 router 交給 `handle_check_command` 與 `handle_luck_decision`，確定性處理，完全不進 Executor。貴的是玩家在決定待處理期間送出的**其他每一則訊息**。那些都是普通回合，而從訊息進來到 Executor 之間，沒有任何地方查看 `state.pending_luck_decisions` —— 最早的引用在 `supervisor.py:93`，那時回合已經在跑了。

其中一個回合的時間軸：

```text
15:12:15.478  回合開始
15:12:15.714  Executor 啟動
15:12:17.819  模型回應 #1   in=22,940     已計費
15:12:17.820  工具 clear_pending_check
15:12:21.017  模型回應 #2   in=23,390     已計費
15:12:21.018  executor.resolution = await_luck  <- 裁定在這裡才產生
15:12:25.265  模型回應 #3   in=13,670     裁定之後仍然計費
```

裁定不是 pipeline 事先檢查的前提條件，而是 `turn_resolution.validate_resolution()` **拿 Executor 的輸出**推導出來的結論，所以 Executor 必須先跑。接著 Narrator 照樣執行，最後由 `prompt_config.enforce_mechanic_check_consistency` 把 Narrator 寫的東西丟掉，換成 `_pending_luck_fallback` 的確定性文字——而那段文字在回合開始前就已經可用。

整場 20 輪中有 16 輪裁定為 `await_luck`，每輪耗費 2–4 次模型請求、36,000–86,000 input tokens、10–18 秒，且全程握著對話鎖。

另外值得注意：該回合中模型呼叫了 `clear_pending_check`，試圖取消那個待處理檢定，被 `luck_takes_precedence` 正確擋下。提前擋回除了省下成本，也一併避免了這類無效嘗試。

### 5.2 為什麼只做 Luck、不做待處理檢定

**對持有者本人而言，Luck 決定是封閉狀態。** 骰子已經擲出，在他做出決定之前，他說什麼都不能改變那個結果，因此確定性回覆不會拿掉任何模型本來能做的判斷。

**但對整桌而言不是。** 本節初稿宣稱 `turn_resolution.py:148` 寫死了沒有任何東西能排在 Luck 決定前面。重讀之後發現：`luck_takes_precedence` 檢查的是**被等待方**的 owner，不是發話者，所以持有決定的玩家**仍然可以合法地等待另一位玩家的檢定**。既有測試 `test_referenced_check_not_overridden_by_other_players_luck` 正好涵蓋這一點，並且讓第一版實作失敗。因此只要**其他人**持有待處理檢定或決定，閘門就不啟用。

**待處理檢定不是封閉狀態。** 它還沒擲骰，玩家可以合法撤回——`turn_resolution` 有完整的 `cancelled` 路徑，會驗證 `clear_pending_check` 確實執行過、且沒有動到其他狀態。**若對待處理檢定也擋下訊息，就會破壞取消功能**，因此本工作包不碰它。

### 5.3 範圍

在 Executor 之前，僅針對行動中的該名玩家：

| 情況 | 行為 |
|---|---|
| 該玩家持有待決 Luck、**且無他人在等待**、且送出新的遊戲行動 | 以 `pending_luck_reply` 回覆，零模型請求 |
| 任何**其他人**持有待處理檢定或決定 | 不變；必須保留「等待其他玩家」的能力 |
| `/coc luck hard` / `skip`、`/coc check` | 不變；本來就是確定性處理 |
| status、sheet、help | 不變；本來就不進 Executor |
| 其他玩家的回合 | 不變；決定是 per-user |
| KP 或 sudo | 本工作包不改動 |

### 5.4 由路由器決定擋什麼，不由本工作包決定

這原本被列為需要簽核的設計決定，前提是「擋住角色台詞會損失遊戲體驗」。有兩件事讓它結案。

**第一，當初據以推論的分類器已經不存在了。** #99 用 `route_request` 取代它，回傳 `RouteDecision`，並在 `PURE_ROLEPLAY` 與 `GAMEPLAY_ACTION` 之外新增了 `PLAYER_OOC`。閘門現在**複用 Supervisor 本來就會算出的那個決定**，而不是自己另寫一個 helper，且只在 `GAMEPLAY_ACTION` 時啟用：

```text
我往樓梯走過去        GAMEPLAY_ACTION   從 state 回答
好                    PURE_ROLEPLAY     正常回合
（等我想一下）        GAMEPLAY_ACTION   從 state 回答 —— 裸括號屬於 IC
(ooc: 等我想一下)     PLAYER_OOC        正常回合，走 #99 的 OOC 路徑
為什麼要擲骰          PLAYER_OOC        正常回合 —— 規則問題會得到回答
我的角色卡有什麼技能  PLAYER_OOC        正常回合
```

這比本文件最初指定的版本**更好**：玩家問「為什麼要擲骰」現在會得到答案，而不是一句 Luck 提示。

**第二，在舊分類器上這個問題本來就近乎無意義** —— 17 份紀錄、206 次分類中 `PURE_ROLEPLAY` 出現 0 次。但那些紀錄早於 #99，因此對 `PLAYER_OOC` 的實際發生頻率沒有說明力；等 #99 實際上線後，值得用 WP1 的腳本重新量測。

### 5.5 驗收

已實作。`tests/test_pending_luck_short_circuit.py` 讓閘門下游的**每一個階段**都直接拋錯，因此測試只有在它們全都沒被執行時才會通過——連 `build_context` 都不行，否則等於在回合被擋下之前就先花掉一次檢索往返。

- 持有決定的玩家送出遊戲行動時，零模型請求、零 context 建構。**已達成。**
- 其他玩家行動、只有待處理檢定、檢定結算續接、KP 助手回合、純角色扮演——全部仍走正常 pipeline。**已達成。**
- 任何其他人持有待處理檢定或決定時保留完整 pipeline。**已達成**，並由那個推翻第一版實作的既有多人測試覆蓋。
- `turn.short_circuit` 只發出一次，帶原因。**已達成。**
- `/coc luck` 與 `/coc check` 不受影響：這兩條在本次改動前就不進 Executor。

變異驗證：移除閘門會紅 2 項；移除多人收窄會紅 2 項（含那個既有測試）；移除分類器條件會紅純角色扮演那項。

**有一個既有測試必須修改。** `test_supervisor_passes_existing_pending_luck_and_suppresses_new_roll` 設定了一個事先存在的決定，而那現在會被從 state 直接回答；它的斷言照樣通過，但 `run_narrator` **根本沒有被進入過**。現已改為在回合中途產生該決定，那才是仍然會走到 Narrator、也才值得覆蓋的路徑。

### 5.6 限制

16 輪的數字**高估了發生頻率**：harness 會解算待處理檢定但不解算 Luck 決定，因此模擬的是一個永遠不作答的玩家。真實遊戲會在一到三輪內作答。不過**每輪的單價相同**，而且鎖在整段期間都被握著。

這只量測了單一 session、單一劇本。本文件不宣稱待決 Luck 在一般遊玩中出現的頻率。

## 6. 執行順序

```text
WP1   可重現的基準          -> 無 runtime 改動
WP2   快取邊界              -> 證據最強；可用旗標還原
WP3.3 索引 singleflight     -> 獨立缺陷；不動鎖；須在 3.4 之前
WP3.2 排隊可觀測性          -> 與其他一切獨立
WP3.4 移出 context 建構     -> 在 3.3 之後
WP3.5 敘事前釋放鎖          -> 在 #97 與 #99 之後
WP4   檢索往返              -> 在分類量出來之前只做調查
WP5   待決 Luck             -> 直接消滅請求；需先決定 §5.4
```

WP2、WP3、WP4 動到不同檔案，可分開審查。WP3 內部：3.2 與 3.3 彼此獨立、也與 WP2 獨立；3.4 只依賴 3.3；3.5 依賴本文件之外的工作。

## 7. 限制

- 改動後的 session 只有 15 個 executor 回合、77 筆帶 usage 的請求、22 次鎖取得，涵蓋一場遊戲的 18 分鐘。p99 分位數建立在少數幾個觀測值上，`complete_for_action` 的 1/2 樣本太小，不足以稱為趨勢。
- 兩次 session 的內容與程式碼都不同。延遲上升與工具呼叫組成一致，但未執行同劇本對照。
- WP2 建立在「供應商快取前綴從哪裡開始」的推論上，依據是快取 token 總量超過 `instructions + tools`。這與資料一致，但未對照供應商文件確認，這也正是 WP2 必須可用旗標還原、且必須實測而非假定的原因。
- 本文件未審查 `combat.py`、`dice.py`、`luck.py` 的規則正確性，也未審查 PDF 匯入路徑與 Discord UI 層。

## 8. PR #109 review 修正

目標是在保留提示詞快取與鎖外檢索的同時，維持當前回合依據和訊息入列順序。本修正不改持久化 schema、戰鬥規則或購買政策。

1. **回應鏈後備。** 首次請求與無效回應鏈的重試共用輸入組裝方式。啟用 `OPENAI_DYNAMIC_PROMPT_AFTER_INPUT` 時，兩者都恰好包含一次當前的動態 developer 區塊；Token 預留只計算重建後的輸入一次。
2. **預取有效性。** 劇本結果綁定目前章節視窗與劇本文字版本；記憶結果綁定記憶維護提交時會變動的版本。綁定改變時丟棄預取，在鎖內重新檢索。測試須涵蓋排隊期間切章與新增記憶。
3. **入列順序。** 一般回合應先登記對話順序，再等待其預取完成。檢索可在等待時執行，但後一則訊息不能因先檢索完成而超越前一則。已設定的 KP Assistant 仍優先於等待中的玩家；取消須移除其票據。
4. **佇列位置。** 同時計入 KP 優先閘門和對話鎖的等待者；從閘門移到鎖的同一回合不能重複計算。提示和 `turn.queue` 共用同一個順序快照，並隨完成回合更新。

驗收：加入無效回應鏈、章節／記憶變動、故意延遲預取的 FIFO／KP 優先、取消與閘門排位測試；接著跑隔離完整測試、Ruff、mypy 與 `git diff --check`。不新增模型請求或固定審稿階段。
