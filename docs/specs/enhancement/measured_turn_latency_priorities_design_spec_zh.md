# 以實測結果決定的回合延遲優先序

[English](measured_turn_latency_priorities_design_spec.md)

狀態：**提案中，等待設計審查**。基底：`main_v2` 的 `5961f2b`。

## 0. 這份文件為什麼存在

已經有三份規劃文件描述本專案的延遲工作：`main_v2_detailed_fix_plan.md`、`main_v2_detailed_fix_plan_ux_latency_v2.md`、`main_v2_python_optimization_safety_addendum.md`。三份都明確聲明未執行測試套件、未進行正式 Discord 測試、未做真實 LLM 效能評估。它們訂下的約束是正確的，本文件不重新檢討那些約束。它們缺的是量測，因此其優先序沒有資料支撐。

本文件補上量測，並據此重排工作順序，涵蓋三個工作包。不新增 Agent、不新增資料庫、不引入提前結束規則，也不更動那三份文件建立的任何正確性契約。

### 0.1 證據基礎

以下數字來自 `~/coc_v2_log` 的 17 份 runtime log，涵蓋 2026-09-25 至 2026-09-27：323 筆帶 request id 的請求、156 筆帶 usage 的模型請求、175 次 conversation lock 取得。本地 CPU 數字是對真實 85 KB 持久化狀態的直接 benchmark，不是剖析器的歸因。所有數字都在 `edd2fd6` 部署之前取得，因此全部是部署前基準。

### 0.2 量測改變了什麼

| 受檢視的主張 | 來源 | 實測 | 判定 |
|---|---|---|---|
| 本地 CPU 工作（結果整理、token 計數、快照建構）應列入第一批 | addendum P3/P4/P5 | 每回合 6–10 ms，對比 16,000 ms 的回合 —— 0.04% | 降級 |
| `select_history()` 重複呼叫 `estimate()`，值得快取 | addendum P4 | 整份 log 只需 0.07 ms | 降級 |
| `_encoding()` 目前使用 `lru_cache` | addendum P4 | 已於 #102 換成帶重試期限的顯式快取 | 已過期 |
| 正常工具回合是 `b + 1` 次 Executor 加一次 Narrator | v2 UX.3 | 實測每回合 3–5 次模型請求 | 確認正確 |
| 靜態 prompt 穩定、動態資料後置、以實際 usage 驗證快取 | v2 UX.5-D | 未實作；#97 與 #99 完全沒有動到 `prompt_config.py` 的 static 組裝 | 採納為 WP2 |
| 同團 gameplay 維持序列化，唯讀指令繞過鎖 | v1 F8.1 | 同意，但實測到的等待發生在 gameplay 回合之間，唯讀繞過碰不到 | 缺口 —— WP3 |

每回合本地 CPU，直接 benchmark（含 worker thread 的工作，主執行緒剖析器不會歸因到這些）：

```text
keeper._build_static_prompt        0.01 ms
keeper._build_dynamic_prompt       0.10 ms
input_budget.estimate(static)      0.84 ms
input_budget.estimate(tools)       1.22 ms
input_budget.select_history(log)   0.07 ms
GroupState.from_dict               0.13 ms
state.to_dict() + json.dumps       0.14 ms
                        每回合合計   6–10 ms   （16 秒回合的 0.04%）
```

## 1. WP1 —— 在改動任何東西之前先建立實測基準

`edd2fd6` 合併了 #102、#103、#104，而此後沒有任何 bot 行程執行過。本文件所有快取數字都在 `prompt_cache_key` 生效之前。若在取得該基準之前更動 prompt 結構，兩個效果會混在一起，兩邊都無法歸因。

交付物：`scripts/analyze_turn_latency.py`，對 runtime log 目錄唯讀，輸出的正是本文件各項判斷所依據的指標：

```text
回合內依請求位置的快取命中率        （基準：4.6% / 71.4% / 64.7%）
依階段的快取命中率                  （基準：executor 51.6%、narrator 18.3%）
conversation lock 等待分位數        （基準：p99 54,706 ms、max 63,343 ms）
每回合模型請求數與 input tokens     （基準：3–5 次、87k–113k tokens）
各 agent 的耗時                     （基準：executor 中位 9.5 s、narrator 6.2 s）
劇本投影完整性                      （基準：complete_for_action 0/6）
```

不需要任何 runtime 改動：`observability.usage_fields` 已經在記錄 `cached_input_tokens`，`lock.wait` span 也已帶有耗時。這個工作包加的是讀取器，不是觀測點。

驗收：該腳本能從既有 log 重現本文件的每一個基準數字，並在 WP2 開始前，對一次 `edd2fd6` 之後的實際遊戲執行一次。

## 2. WP2 —— 把動態資料移到快取邊界之後

### 2.1 實測問題

可快取前綴的長度，取決於請求之間第一個改變的位元組。`app/providers/openai_provider.py` 組成 `instructions = f"{static_system}\n\n{dynamic_system}"`，而 `dynamic_system` 帶有 HP/SAN、位置與檢索 context，每回合都不同。

在同一回合內，第一次之後的請求快取量中位為 19,810 tokens，超過 `static + dynamic + tools`（7,972 + 1,284 + 9,527 = 18,783）。這代表快取已延伸到工具定義之後的 input items，也就是 `instructions` 位於快取前綴的最前端或接近最前端。

跨回合時，比對必然在 `dynamic_system` 開始處中斷，因此它之後的一切——包含 9,527 tokens 的工具 schema——都無法重用。跨回合快取的結構上限因此是 7,972 tokens，而實測每回合第一次請求的命中率是 4.6%，樣本為 42 筆請求、814,162 input tokens。

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

`run_conversation` 有兩種 input 形態。沒有 `previous_response_id` 時送出選取後的 history 加上使用者訊息；有 `previous_response_id` 時只送 `[{"role": "user", "content": new_message}]`，靠 `instructions` 帶當前狀態。

把 `dynamic_system` 移出 `instructions` 就拿掉了這個保證。鏈式分支必須一併修改，讓動態區塊在回合第一次請求時以 input item 送出並沿鏈繼承；而 `_commit_turn_result` 本來就會在回合之間讓 response chain 失效，所以新回合會重新送出。**只搬動區塊而沒有處理鏈式分支的實作，會讓模型讀到過期的 HP/SAN —— 那是正確性失敗，不是效能退步。**

### 2.4 本工作包排除的項目

把 `build_executor_static_prompt` / `build_narrator_static_prompt` 從 `INSTRUCTION + keeper_static_prompt` 改為 `keeper_static_prompt + INSTRUCTION`，讓 Narrator 能重用 Executor 剛暖好的 7,131 tokens 區塊（Narrator 基準 18.3%），是另一個更小的改動。刻意延後，一是讓兩個效果維持可歸因，二是它把角色指令移到 7,131 tokens 的內容之後，屬於行為改動，需要自己的評估。

### 2.5 驗收

- 離線：動態區塊在兩種 input 形態下都確實送達模型，包含鏈式回合的每一次請求；以假 provider 斷言組成後的請求本身，而不是斷言某個 helper 的回傳值。
- 量測：以 WP1 的腳本對改動後的實際遊戲執行，對照 `edd2fd6` 之後的基準，不是對照本文件的數字。
- 正確性：先前帶有當前 HP/SAN/位置的每一次請求，改動後都必須仍然帶有。**丟失狀態的快取改善是失敗。**
- 可還原：以單一旗標即可回到原本的組成方式，不需重新部署 prompt 內容。

## 3. WP3 —— 界定並量測回合排隊

### 3.1 實測問題

conversation lock 從收到訊息一路握到 Executor、Narrator、Guard、防雷掃描與 log 提交結束，兩次模型往返都包含在內。

| conversation lock 等待 | 數值 |
|---|---|
| 中位 | 0 ms |
| p99 | 54,706 ms |
| max | 63,343 ms |
| 超過 1 秒 | 175 次中 11 次（6.3%）|
| 超過 10 秒 | 8 次 |

有玩家等了 63 秒才輪到自己的回合開始，接著還要再等約 16 秒跑完。`_conversation_lock_with_notice` 在 10 秒後送一次通知，之後就沒有了，所以在 p99 的情況下，玩家還要再坐 45 秒，沒有任何後續訊號，也不知道自己是第一個還是第三個。

現有 spec 與三份規劃文件都沒有針對這件事。`enhancement-conversation-lock-and-tool-loop-latency.md` 刻意不縮小鎖範圍。v1 F8.1 維持同團 gameplay 序列化，只把唯讀指令移出，碰不到這個發生在 gameplay 回合之間的等待。v2 UX.4 定義了 `T_turn_queue` 指標、UX.5 要求量測 player starvation；兩者都沒有實作。

### 3.2 本工作包要做的事

1. 發出 `turn.queue` 事件，帶上實測等待時間、前方等待者數量、route 與 speaker role。這實作了 v2 的 `T_turn_queue`，並讓 starvation 可以按玩家而非按對話量化。
2. 在 `_ObservableConversationLock` 追蹤等待者深度，把位置帶進排隊通知，並以有上限的間隔更新，而不是在 p99 等待的其餘時間完全沉默。

兩者都只是觀測與訊息。不縮小任何鎖、不平行化任何回合、不更動任何狀態契約。

### 3.3 本工作包不做什麼，以及為什麼

在 Executor 提交後釋放 conversation lock，讓 Narrator 中位 6.2 秒的時間落在鎖外，是顯而易見的結構性收益，本文件**暫不提案**。一般回合的 Narrator 是 `tools=[]`，所以不會有變更逃出鎖外；`_commit_turn_result` 也本來就會在 state lock 下重新載入並拒絕過期 timeline。真正的阻擋風險是**順序**而非變更：下一位玩家的 Executor 會對著「敘事尚未貼出」的狀態裁決，於是玩家可能讀到一個引用了他還沒被告知的事件的結果。

這個風險正是 #97 的 mutation admission 與 #99 的 delivery envelope 要界定的範圍。因此本工作包只記錄這個提案與它的前提，等那兩者落地、且 WP1 的排隊指標能顯示是否有幫助之後再做。

### 3.4 驗收

- 每個有等待的排隊回合都出現 `turn.queue`，帶有等待時間、深度與 route；無爭用的取得則完全不出現。
- 通知回報位置並以有上限的間隔更新；在第一次通知之前就解除的等待仍然不送任何訊息。
- 通知送出失敗仍然不能影響鎖的釋放——既有實作已經防住這點，本次不得退步。
- WP1 的腳本能依 route 與 speaker role 分別輸出排隊等待分位數。

## 4. 執行順序

```text
WP1  量測          -> 無 runtime 改動；取得 edd2fd6 之後的基準
WP2  快取邊界      -> 改動前後各量一次；可用旗標還原
WP3  排隊可見度    -> 與 WP2 獨立；縮小鎖範圍維持延後
```

WP2 與 WP3 動到不同檔案，可分開審查。兩者都**不得在 WP1 基準建立之前合併**，因為兩者的依據都是 `edd2fd6` 之前的數字。

## 5. 限制

- 所有基準數字都早於 #102、#103、#104 的部署。它們界定問題的規模，不預測改善的幅度。
- 樣本是單一部署三天份：35 個 executor 回合、156 筆帶 usage 的請求、175 次鎖取得。p99 的分位數建立在少數幾個觀測值上。
- WP2 建立在「供應商快取前綴從哪裡開始」的推論上，依據是快取 token 總量超過 `instructions + tools`。這與資料一致，但未對照供應商文件確認，這也正是 WP2 必須可用旗標還原、且必須實測而非假定的原因。
- 本文件未審查 `combat.py`、`dice.py`、`luck.py` 的規則正確性，也未審查 PDF 匯入路徑與 Discord UI 層。
