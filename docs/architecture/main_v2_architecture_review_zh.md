# main_v2 架構總覽與深度審查（以玩家體驗為準）

[English](main_v2_architecture_review.md)

審查基準：`main_v2` 於 `d6f13c3`（#179–#187 合併後）。範圍：`app/` 全部 47,024 行、152 個測試檔（2,740 項測試）、`.github`、`docs/`。

> **這份文件的立場：玩家體驗優先。** 架構漂亮不是理由去增加玩家的等待、拿掉玩家看得到的回饋、或讓一個頻道有可能卡死。能在**不碰玩家路徑**的前提下讓程式好維護，就做；會碰到玩家路徑的，先量測、再動、動完要能看出前後差異。

## 實施進度

本文件的數字（行數、函式長度、環的數量）描述的是審查基準 `d6f13c3`。之後的進展：

| 路線圖項目 | 對應發現 | 狀態 | PR |
| --- | --- | --- | --- |
| P0 每回合一行摘要（不依賴 `LOG_ENABLED`）、內部錯誤附短碼 | F2、F7 | **已合併** | [#195](https://github.com/marcoliu99/line-coc-keeper/pull/195) |
| P0 布林設定統一、設定指南、README 補上新模組 | F8（部分）、F15 | **已合併** | [#196](https://github.com/marcoliu99/line-coc-keeper/pull/196) |
| P2 拆 `keeper.py`：提示建構 | F9 | **已合併** | [#189](https://github.com/marcoliu99/line-coc-keeper/pull/189) |
| P2 拆 `keeper.py`：回合提交、記憶維護 | F9 | **已合併** | [#192](https://github.com/marcoliu99/line-coc-keeper/pull/192) |
| P2 拆 `keeper.py`：工具分派、`keeper_tools/support`、import 閘門；刪除 `keeper.py` | F9 | **已合併** | [#193](https://github.com/marcoliu99/line-coc-keeper/pull/193) |
| P3 釘住持久化按鈕的 `custom_id` | F11 | **已合併** | [#190](https://github.com/marcoliu99/line-coc-keeper/pull/190) |
| P3 拆 `discord_bot.py`（1,862 → 311 行） | F11 | **已合併** | [#194](https://github.com/marcoliu99/line-coc-keeper/pull/194) |
| P1 `reply_pipeline`、`run_turn` 拆五段（送出仍由 router 負責） | F4 | **已合併** | [#198](https://github.com/marcoliu99/line-coc-keeper/pull/198) |
| P1 回合的鎖集中到 `turn_scope`、鎖序測試、掛在鎖本身的持鎖過久報告；一個不依賴牆鐘的時間測試 | F5、F16 | **已合併** | [#199](https://github.com/marcoliu99/line-coc-keeper/pull/199) |
| P3 `handle_system_command` 對照表化 | F10（冷路徑）| **已合併** | [#200](https://github.com/marcoliu99/line-coc-keeper/pull/200) |
| P4 延遲槓桿（旁白出鎖、工具面、舊版戰鬥退役、provider 迴圈核心、同步 SQLite）| F1、F3、F6、F10、F13 | 未開始；需要先用 #195 的摘要與一次五人真實執行取得數據 | — |
| P5 冷路徑套件化、`GroupState` 子狀態 | F12、F14 | 未開始 | — |

已完成的結果：

- `supervisor.run_turn` 讀起來是 `prepare → mechanics → narrate → 回覆步驟 → commit`，回覆步驟是 `app/agents/reply_pipeline.py` 裡的有序清單，違反順序規則的清單在 import 時就會被拒絕。`/coc` 系統子指令是處理函式的對照表。回合的鎖有了一個家（`app/commands/turn_scope.py`）；`tests/test_lock_order.py` 記錄真實取鎖並在順序顛倒時失敗；鎖被持有超過 `LOCK_HELD_WARNING_SECONDS` 會在 logger `app.locks`（永遠輸出）報告 `lock.held_too_long`，且絕不會被自動釋放。
- `app/keeper.py`（1,532 行、扇出 31）**已刪除**，改為 `prompt_builder`、`turn_commit`、`memory_maintenance`、`tool_dispatch` 與 `keeper_tools/support`；`keeper_tools` 與 `keeper` 之間 12 個模組的環消失，`tests/test_architecture_keeper_tools.py` 擋住它回來。`app/` 內持有 `SLF001` 豁免的檔案從 16 個降為 7 個。
- `app/discord_bot.py` 從 1,862 行降到 311 行，其餘在 `app/discord_transport/`（`gateway`、`delivery`、`interactions`、`lifecycle`、`controls`、`help_ui`），並由 `tests/test_architecture_discord_transport.py` 檢查分層與逐一獨立 import。審查中 Codex 找出第一版的傳輸層 import 環（單獨 import 其中一個模組會失敗，被正常啟動順序掩蓋）與測試 patch 的是錯的模組綁定，兩者都已修正並各有測試擋住。
- 上面各項都是純搬移或只新增輸出；**沒有任何延遲改善的宣稱**。

## 0. 一頁結論

1. **這個系統的架構骨幹是健康的。** 狀態寫入只有一扇門（`state_transaction`）、檢定／戰鬥各有引擎、回合階段有明確的「誰能寫哪個鍵」契約（`TurnPayload`／`CheckStatus`）、安全邊界有確定性的最後防線，而且已有 6 個「架構閘門」測試用 import 圖擋住退化。不需要重寫，更不需要換儲存或換框架。
2. **玩家最常感受到的問題是等待，不是程式結構。** 實測（Camp Sunny 500 回合）循序 p50 26 秒、p95 59 秒；五人同時發言 p50 63 秒、p95 121 秒、最長排隊 106 秒。一個回合持鎖約 21.7 秒，其中 Executor（決定機制的模型呼叫迴圈）中位 15.7 秒、Narrator 約 5 秒；中位 4 次、最多 10 次模型請求。**所以最有價值的架構工作是：看得見這些時間、並減少「序列的模型往返」和「別人在排隊時被鎖住的時間」。**
3. **目前正式環境預設看不到這些時間。** `LOG_ENABLED` 預設 `false`，`turn.phases`（#184 的階段計時）因此不會輸出。這是第一個要補的洞（§4 F2），成本最低、風險最低。
4. **維護性的債集中在三個「樞紐」**，都可以在不改任何玩家可見行為的前提下機械式拆開：`app/keeper.py`（1,532 行，扇出 31，與 `keeper_tools` 以延遲 import 形成 12 個模組的環）、`app/discord_bot.py`（1,862 行，事件入口＋遞送＋持久化按鈕＋Help/sudo/PDF 的 Views 混在一起）、`run_turn`（277 行的程序式腳本，回覆後處理的順序藏在行內）。
5. **有幾件事看起來該「優化」但量測說不要動**：整份狀態存成單一 JSON 列（900 KB 狀態載入 0.5 ms、序列化 0.8 ms，不是瓶頸）；逐回合動態過濾工具清單（會破壞提示快取前綴）；預先把所有同步 SQLite 呼叫搬進執行緒（沒有量測支持）。見 §7。

建議順序（詳見 §6）：**P0 看得見（總是輸出一行回合摘要＋文件校正）→ P1 把回覆後處理與 `run_turn` 拆成有序階段（行為不變）→ P2 拆 `keeper.py` → P3 拆 `discord_bot.py` → P4 有數據後再處理延遲槓桿（開啟旁白出鎖、戰鬥工具面、舊版戰鬥退役）。**

## 1. 設計原則與「玩家路徑守則」

### 1.1 玩家體驗不變量（任何重構都不得破壞）

| # | 不變量 | 現在由什麼保證 |
| --- | --- | --- |
| U1 | 收到訊息立刻顯示「輸入中」 | `discord_bot.on_message` 在取鎖前進入 `_best_effort_typing` |
| U2 | 排隊超過 10 秒會被告知，並隨佇列前進更新位置（最多 3 次） | `turn_scope._delayed_queue_notice`、`_QUEUE_ACK_*`（#199 從 `router` 搬出） |
| U3 | 先送出回覆，維護工作在其後背景執行，不擋下一位玩家 | `post_turn.run_post_turn_maintenance_after_output` → `spawn_post_turn_maintenance` |
| U4 | 檢定／Luck 按鈕重啟後仍可用；重複點擊被擋下而不是排隊重擲 | `DynamicItem` 按鈕（`timeout=None`）、`locks.try_acquire_check`、`state_actions` 帳本 |
| U5 | 模型失敗時，已提交的變更保留，玩家得到明確、可行動的說明，而不是靜默或重擲 | `turn_fallback`（12 種原因）、`narrator` 失敗路徑、`LLM_TURN_DEADLINE_SECONDS=180` |
| U6 | 玩家看到的是牌桌用語：中文等級、不含內部識別碼、真實隊伍人數 | `app/presentation.py`（#185） |
| U7 | 同一個動作送兩次，只會生效一次 | `state_transaction` 的 `action_id`／帳本 |
| U8 | 私人資訊只給對的人，劇透被擋 | `spoiler_policy`、`turn_delivery.finalize`、私訊隔離 |
| U9 | 劇本檢索在排隊時就先開始，不用等輪到才搜 | `supervisor.prefetch_retrieval`（規格 WP3.3）|
| U10 | 能直接從狀態回答的問題不叫模型（持有中的 Luck 決定） | `supervisor` 的 `turn.short_circuit`（WP5） |

### 1.2 玩家路徑守則（改到「熱路徑」模組時必須遵守）

**熱路徑**＝一個玩家回合會經過的程式：`discord_bot`（事件與按鈕入口）、`commands/router` 文字路徑、`locks`、`agents/*`、`services/{turn_*, post_turn, presentation, prompt_config}`、`providers/*`、`keeper`（工具分派與提交）、`repositories/state_transaction`、`checks/*`、`combat` 引擎（戰鬥中）、`scenario_rag`／`memory_rag` 的搜尋端。

1. **不新增序列的 LLM 請求。** 需要新的模型判斷，必須與既有請求合併，或移到背景。
2. **不延長持鎖時間。** 新工作若不需要保護狀態，放到鎖外（參考 prefetch 與維護的做法）。
3. **事件迴圈執行緒上不新增 >5 ms 的同步工作**（SQLite、JSON、正規表示式掃描大文字）。
4. **玩家可見文字不能因重構而改變**，除非該改變本身就是目標且有測試。
5. **每個熱路徑 PR 附前後的 `turn.phases` 摘要**（`queue_wait`／`executor_llm`／`narrator_llm`／`other` 的 `exclusive_ms`）。沒有數據就明說沒有。
6. **失敗要「有界」**：任何新的重試、補查、等待都要有次數或時間上限，並有 `turn.fallback` 或事件可觀測。

**冷路徑**（劇本匯入、PDF、劇本撰寫與審閱、Help UI 的建構、角色建立、地圖建構、背景維護）可以依一般架構標準重構，但不得持有熱路徑會用到的鎖超過一次短的讀寫。

## 2. 現況架構

### 2.1 分層與熱／冷路徑

```text
                            ┌────────────── 冷路徑（匯入／撰寫／UI 建構，不在玩家回合上）──────────────┐
                            │ scenario_*  (13%)   pdf_* / markitdown (5%)   pregen_extractor   help_*   │
                            │ scene_map   creation   services/scenario_{ingestion,lifecycle}            │
                            └───────────────▲───────────────────────────────────────────────────────────┘
                                            │ 只經由 scenario_library 的讀取介面（唯讀）
Discord ──► discord_bot ──► commands/router ──► handlers/*  (指令: 解析 + 回覆)
              │  輸入中、佇列提示、按鈕(DynamicItem)
              ▼
        ┌─────────────────────────── 熱路徑：一個玩家回合 ───────────────────────────┐
        │ locks (conversation → keeper turn → narration)  +  priority gate           │
        │                                                                            │
        │ supervisor.run_turn                                                        │
        │   context_builder ─► (scenario_rag / memory_rag / presentation facts)      │
        │   intent_router (規則，不叫模型)                                            │
        │   executor ─► provider tool loop ─► tool_gateway ─► keeper._execute_tool   │
        │                                          └► keeper_tools/registry(61 tools)│
        │   turn_fallback (有界復原)   turn_resolution / turn_handoff (確定性驗證)    │
        │   narrator ─► provider                                                     │
        │   consistent → guard → consistent → obligation_gate → party size           │
        │   → turn_delivery.finalize → presentation.player_text                      │
        │   keeper._commit_turn_result  ─► state_transaction                         │
        └──────────────┬───────────────────────────────────────────┬─────────────────┘
                       │ 規則引擎 (純規則, 經 Port 注入骰子)        │ 背景 (回覆之後)
                       ▼                                           ▼
            checks/{service,rules,luck,events}   post_turn → 記憶維護 / 摘要 / embedding 回補
            combat engine (legacy | managed)     correction_adjudication / correction_summary
            dice  event_obligations
                       │
                       ▼
        repositories/state_transaction  ──►  group_state  ──►  db (SQLite)   [GroupState: 55 欄 JSON 單列]
```

規模（`app/` 47,024 行）：scenario 13%、discord＋commands＋help 13%、combat 10%、其他 services 10%、state＋persistence 7%、turn/agents＋keeper 7%、keeper_tools 7%、providers 7%、pdf 5%、checks 3%、memory 2%。**冷路徑（scenario＋pdf＋pregen＋help）占約三成，熱路徑核心（agents＋keeper＋providers＋checks＋state）約兩成多**——這也是為什麼「重構熱路徑」的風險報酬比與「重構冷路徑」完全不同。

### 2.2 一個回合的生命週期（實測時間）

| 階段 | 做什麼 | 持有的鎖 | 實測 |
| --- | --- | --- | --- |
| 接收 | `on_message`：輸入中、request context | — | — |
| 預取 | 排隊同時開始劇本檢索（唯讀） | — | 約 1 秒，被排隊吸收 |
| 排隊 | 等 conversation lock（FIFO），>10 秒起通知 | — | 五人同時：最長 106 秒 |
| 建 context | `build_context`：狀態、檢索、記憶 | conversation + keeper turn | ≈ 1 秒 |
| Executor | 模型工具迴圈（≤5 輪、≤4 工具），真的改狀態 | 同上 | 中位 15.7 秒（p90 35 秒） |
| 復原 | 只有 fallback 時：一次補查＋一次重跑 | 同上 | 只在原本會變成通用回覆的回合 |
| 旁白出鎖 | `to_narration()`（**預設關**）| 釋放 mutation locks，取 narration lock | — |
| Narrator | 無工具；一次模型請求 | conversation + keeper（預設）| 約 5 秒 |
| 後處理 | 一致性→Guard→義務→人數→`finalize`→`player_text` | 同上 | ≈ 0（純規則；Guard 修復才多一次請求）|
| 提交 | `_commit_turn_result` 一次交易 | 同上 | ≈ 0 |
| 遞送 | `reply`、私訊、圖片；其後才 spawn 維護 | 同上 | 受 Discord 限速／逾時 |

持鎖合計約 **21.7 秒**（量測 15 個 Executor 回合；樣本小，僅供量級判斷）。

### 2.3 並行模型：七層排序／互斥機制

| 機制 | 型別 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `mutation_admission` | 行程內持有表 | conversation | 工具 worker 逾時／被取消後，在真正的 worker 執行緒結束前暫停該頻道的變更（避免逾時的執行緒繼續寫入）|
| conversation lock | `asyncio.Lock`（附排隊計數） | conversation | 一次只處理一個玩家動作的機械階段（FIFO） |
| keeper priority gate | 自製佇列 | conversation | 有 KP Assistant 時 KP 訊息優先 |
| keeper turn lock | `asyncio.Lock` | conversation | Keeper/LLM 回合序列化 |
| narration lock | `asyncio.Lock` | conversation | 旁白順序（**僅旁白出鎖開啟時才有意義**）|
| `try_acquire_check` | 集合 | (conversation, user) | 同一玩家重複檢定直接拒絕 |
| state `RLock` + `BEGIN IMMEDIATE` | threading／SQLite | conversation／DB | 狀態交易 |

這是為了玩家體驗而長出來的複雜度（U2、U4、U7、U9），邏輯上站得住。風險在於**失敗型態是整個頻道卡到重啟**（`config.py` 註解與規格都這麼寫），而這套編排目前主要靠 docstring 與測試維持。見 F5。

### 2.4 狀態與持久化

- `GroupState`：55 個欄位的 dataclass，序列化成 SQLite `group_states` 一列 JSON，另有 `schema_version` 與 migration 表。
- 唯一寫入口：`state_transaction.mutate`（鎖→`BEGIN IMMEDIATE`→讀最新→驗時間線／帳本／修訂→變更→不變式→寫狀態＋事件＋動作結果→提交）。`tests/test_architecture_state_writes.py` 擋住其他寫入者。
- **量測**：900 KB 的合成狀態（200K 字劇本＋160 筆日誌）`from_dict` 0.5 ms、`to_dict`＋`json.dumps` 0.8 ms。**整列 JSON 對延遲沒有實質影響**。
- 日誌有上限（`MAX_LOG_TURNS`，4 倍觸發裁切），裁切內容進入摘要與記憶 RAG。

### 2.5 規則引擎

`checks/`（`DicePort` 注入、單一結算、單一事件記錄）、`combat`（legacy）／`combat_flow`（managed）／`services/combat_engine`（唯一入口，模式只讀一次）、`event_obligations`＋`obligation_gate`（劇本寫明的 SAN／傷害／強制檢定，敘事後同回合結算）、`turn_resolution`（確定性交接驗證：Executor 的宣稱不等於完成）。這一層是專案最有品質的部分：純規則、可注入骰子、有架構閘門測試。

### 2.6 知識層

`scenario_rag`（BM25＋可選 embedding，相鄰 chunk 有界擴充）、`memory_rag`（被裁切的歷史，embedding 有界分段與重試上限）、`scenario_library`（來源與變體的唯一儲存介面）。檢索在 Executor 之前預取，在鎖外執行。

### 2.7 Provider 層

四個轉接器（Anthropic／Gemini／OpenAI／Codex）各自實作 `run_conversation` 的工具迴圈（164–252 行），共用 `retry`、`turn_budget`（回合總期限）、`admission`（OpenAI 速率）、`conversation_session`。Anthropic／OpenAI 有提示快取（量測：回合內第 2 次起 77–79% 命中）。

### 2.8 量化的耦合現況

| 指標 | 數值 | 解讀 |
| --- | --- | --- |
| 模組層級（頂層）import 環 | 1 個（`commands` ↔ `handlers.uploads`，套件 `__init__` 性質）| 很乾淨 |
| 函式內延遲 import | 88 條 | 把環藏起來；真環見下 |
| 含延遲 import 的環 | 5 個：`keeper`↔`keeper_tools/*`↔`turn_context`（12 模組）、`providers/*`（8）、`scenario_library` 叢集（5）、`router`↔`buttons`↔`pending_buttons`（3）、help（2）| 熱路徑上的是第 1、2、4 個 |
| 扇出前三 | `keeper` 31、`discord_bot` 31、`router` 30 | 樞紐 |
| 扇入前幾 | `models` 54、`observability` 54、`config` 42、`mutation_admission` 24 | 合理的共用基礎 |
| 最長函式 | `handle_system_command` 641、`run_turn` 277、`openai_provider.run_conversation` 252、`run_executor` 245、`_handle_text_message_impl` 207 | 需要拆 |
| `SLF001`（跨模組私有存取）豁免檔案 | 17 | 其中 `keeper._*` 被 9 個檔案引用共 35 處 |
| 設定項 | 47 個 `_env_*` ＋ 數個行內解析 | 見 F8 |
| 架構閘門測試 | 6 個（checks、combat、corrections、legacy、scenario_store、state_writes）| 好的做法，應擴充 |

## 3. 必須保留的設計（不要為了漂亮而改掉）

1. **FIFO 對話鎖＋排隊提示＋位置更新**：玩家看得到自己在第幾位。
2. **回覆先送、維護後跑**：維護從不擋下一位玩家。
3. **預取檢索在鎖外**，且 `build_context` 在鎖內重新驗證綁定。
4. **狀態單一寫入口＋動作帳本**：重複點擊、重送、續擲都安全。
5. **確定性最後防線**（`finalize`、`player_text`、`turn_fallback`）：模型出錯時玩家得到明確訊息，已提交變更保留。
6. **規則引擎不依賴傳輸層與模型層**（架構閘門測試守住）。
7. **持久化按鈕**（`DynamicItem`）與過期按鈕重放的冪等回覆。
8. **整列 JSON 狀態**（見 §2.4 的量測）。
9. **`intent_router` 用規則而不是模型**：省掉一次序列請求。

## 4. 審查發現

嚴重度＝對玩家的影響；每項註明是否碰熱路徑。

### F1（高，熱路徑）整個機械階段一次只服務一位玩家，且序列的模型往返很多

- **證據**：`router._handle_text_message_impl`→`turn_scope.conversation_turn`（基準時是 `router._conversation_lock_with_notice`）；`supervisor.run_turn`；實測見 §2.2。`NARRATION_OUTSIDE_MUTATION_LOCK` 預設 `false`（`config.py:306`、`.env.example:160`），規格估計開啟可把持鎖中位從約 21.7 秒降到約 15.7 秒。`TurnHandoff`／narration lock／`narrating_turn` 的複雜度已經付了，好處卻沒收。
- **玩家影響**：五人同時發言時，最後一位等的是前面所有人的 Executor。
- **建議**：不要盲目翻旗標。順序：(1) 先做 F2（看得見）；(2) 在一次五人真實執行中同時記錄 `turn.queue`、`turn.phases`；(3) 補 F5 的持鎖 watchdog；(4) 之後在測試頻道開啟 `NARRATION_OUTSIDE_MUTATION_LOCK`，比較 `queue_wait` 的 p50／p95 與是否出現順序錯亂；(5) 通過才改預設。注意 `supervisor` 在「劇本證據可能寫明機制」時（`obligation_candidates`）會保留 mutation phase，開啟後的實際收益取決於這類回合的占比，要看數據。
- **不要做**：把 Executor 與 Narrator 並行（Narrator 需要 Executor 的確定性結果）；以「不同玩家可並行」切鎖（會引入 `state_revision` 衝突與回合順序問題，沒有量測支持）。

### F2（高，維運）預設看不到延遲

- **證據**：`LOG_ENABLED` 預設 `false`；`observability.event` 在其為 `false` 時直接返回；`turn_phases` 的 `turn.phases`／`turn.phase` 經由它輸出。也就是說 #184 做的量測工具，在預設設定下什麼都不會留下。
- **建議**：新增**總是輸出**的單行回合摘要（INFO、不含任何文字內容）：`turn_id`、`wall_ms`、`queue_wait_ms`、`executor_ms`、`narrator_ms`、`requests`、`tools`、`route`、`fallback_reason`。成本是每回合一次字串格式化；不需要打開完整結構化日誌（它含 token／位元組計數，才是 `LOG_ENABLED` 想避免的成本）。
- **價值**：所有後續延遲決策（F1、F3、F4、F6）的前提。

### F3（中高，熱路徑）Executor 每次請求都帶 57 個工具 schema，且兩套戰鬥工具同時暴露

- **證據**：預設設定、玩家身分下 `tools_for_speaker_role` 回 57 個工具，schema 共 33,043 字元；`combat`（12 個，7,554 字元）＋`managed_combat`（21 個，7,443 字元）合占約 45%。`keeper._tools_for_speaker_role` 不依狀態過濾。Executor 靜態提示在空狀態下約 19.7K 字元。
- **權衡**：工具清單是被快取的前綴；逐回合依狀態過濾會讓前綴變動、快取失效（規格 WP2 的實驗顯示穩定前綴才能到 85–93% 命中）。所以「少送」不一定比較快。
- **建議**：(a) 不做逐回合動態過濾；(b) 若要縮減，只做**穩定的兩個模式**（非戰鬥／戰鬥），模式切換時才換前綴；(c) 舊版（legacy）戰鬥退役後（F13）移除 12 個 legacy 工具；(d) 一律用既有的 `scripts/experiments/ab_prompt_cache_boundary.py` 類型實驗驗證，指標是首請求快取命中與 Executor 中位時間，**沒有改善就不合併**。
- **未驗證**：Codex provider 路徑的快取行為；各 provider 的實際首請求 token 成本。

### F4（中高，熱路徑）回覆後處理是對「整段文字」的有序管線，順序藏在 `run_turn` 裡

- **證據**：`supervisor.run_turn` 277 行，其中 `consistent → guard → consistent → obligation_gate → enforce_party_size → finalize → player_text` 的順序由行內程式碼決定（#181、#185 各自插入一段）。每一段都有「必須在誰之前／之後」的理由，但只存在註解。
- **玩家影響**：這條管線需要完整文字，因此回覆不能串流；這是安全邊界換來的，不是疏忽。維護風險是：下一個「在回覆前加一步」的人可能放錯位置，造成已驗證文字被後面的步驟改掉（#185 就是為此把人數更正放在 `finalize` 之前）。
- **建議（行為不變）**：把這些步驟收成 `reply_pipeline.py` 裡一張**有序的清單**：每步是 `(name, fn, must_precede=…)`，加一個測試斷言順序與「`finalize` 之後只允許無損步驟」。`run_turn` 本身拆成 `prepare → mechanics → narrate → gate → commit → deliver` 六個函式，**awaits 的次數與順序完全不變**。
- **產品選項（需要你決定，不建議未經數據就做）**：回合很長時先送出「機械結果／檢定按鈕」再送旁白，讓玩家早一步有事可做。代價是兩則訊息的順序、`finalize` 對機械文字的保證要重新定義。

### F5（中，熱路徑）鎖編排的失敗型態是頻道卡死，且只靠文件維持

- **證據**：`locks.py` 的 `TurnHandoff` docstring 明寫「一次未釋放的 conversation lock 會讓頻道死鎖到重啟」；基準時 `router` 內有 10 處 `async with _conversation_lock_with_notice(...)`／優先閘門各自重複（現在是 `turn_scope.conversation_turn`／`keeper_turn`，剩下的直接取鎖由 `tests/test_architecture_turn_scope.py` 釘住）；規格 WP3.5 記錄了多次審查才修好的鎖順序問題。
- **已有的緩解**：`LLM_TURN_DEADLINE_SECONDS=180`、`DISCORD_REQUEST_TIMEOUT_SECONDS`、`finally` 釋放。
- **建議**：(1) 把「取鎖→交接→釋放→執行後處理」收成單一 `TurnScope`（router 只用它，不直接碰鎖）；(2) 加鎖順序測試（conversation → keeper turn → narration，違反即失敗）；(3) 加**只記錄不自動釋放**的持鎖 watchdog：持有超過期限（例如 deadline＋60 秒）輸出 `lock.held_too_long` 與持有者回合 id。不要自動強制釋放——那會把「卡住」變成「兩個回合同時改狀態」。

### F6（中，熱路徑，待量測）事件迴圈執行緒上的同步 SQLite

- **證據**：`router` 直接呼叫 `load_state`；`supervisor` 直接呼叫同步的 `keeper._commit_turn_result`。CPU 成本小（§2.4）；未量測的是 SQLite 提交（磁碟同步）與 state `RLock` 被 worker thread 持有時的阻塞——阻塞的是**整個行程所有頻道**的事件迴圈。
- **建議**：先用既有的 `state.save`／`state.load` span 在真實環境看 p99；若 >50 ms 再把提交移入 `asyncio.to_thread`（工具已經是這樣）。**不要預先全面 `to_thread`**：每次切換有成本，且會改變例外與取消的語意。

### F7（低中，熱路徑）通用失敗訊息沒有可回報的識別

- **證據**：`discord_bot` 對未預期例外回「發生內部錯誤了，請稍後再試；詳細資訊已記錄到 Bot log。」；`StateRevisionConflict` 要玩家「再試一次」。
- **建議**：訊息附上 4–6 碼的回合短碼（取自既有的 `turn_id`），KP 可以直接在日誌找到；`turn_fallback` 的原因文字已經是可行動的，沿用同樣風格。不改流程。

### F8（中，設定）47 個設定、兩種布林解析、重要旗標預設關閉

- **證據**：`_env_bool`／`_env_int` 與行內 `os.environ.get(...).lower() in (...)` 並存（如 `SCENARIO_RAG_ENABLED`、`DEBUG_SHOW_INTERNAL_IDS`、`TURN_FALLBACK_RECOVERY_ENABLED`）；`SCENARIO_RAG_ENABLED=false`（整份劇本放進提示）與 `true`（檢索）的延遲特性完全不同，而延遲分析（`search_scenario` 占工具呼叫 79%）針對的是後者。
- **建議**：統一用一種解析；寫一張「支援的組態」表（`docs/guides/`）：目前建議的正式組態、測試頻道組態、各自預設；CI 至少跑一次 `SCENARIO_RAG_ENABLED=true` 與 `NARRATION_OUTSIDE_MUTATION_LOCK=true` 的子集，避免旗標長期只有一側被測。

### F9（中，維護）`keeper.py` 是樞紐

- **證據**：1,532 行、扇出 31；內容包含靜態／動態提示建構（約 300 行提示文字）、狀態變更包裝（`_mutate_and_save_state`）、檢定結果快取、回合提交（`_commit_turn_result`）、記憶維護（約 300 行）、KP 正典觸發解析、工具分派（`_execute_tool` 已降到 33 行）、戰鬥狀態閘。`keeper_tools/*` 有 9 個檔案以延遲 import 回呼 `keeper`，形成 12 模組的環；`keeper._*` 被 9 個檔案引用共 35 處（17 個檔案持有 `SLF001` 豁免）。
- **建議（機械搬移，行為不變；已實施，見〈實施進度〉）**：`prompt_builder.py`（提示）、`turn_commit.py`（`_commit_turn_result`、`_commit_kp_ooc_turn_result`、時間線保證）、`memory_maintenance.py`（維護與摘要）、`tool_dispatch.py`（`_execute_tool` 與角色過濾）、`keeper_tools/support.py`（各 handler 共用的部分）；每搬一組就把對應的 `_` 名稱改成公開名稱並刪掉該檔的 `SLF001` 豁免。**不留長期轉接層**（過渡期的轉接要有刪除日期）。熱路徑保護：函式本體不改、呼叫順序不改，由現有測試＋新增一個 import 圖閘門（`keeper_tools` 不得 import `keeper`）驗證。

### F10（中，維護）超長函式

- **證據**：§2.8。`handle_system_command` 641 行（指令分派表式的 if 鏈）、provider 的 `run_conversation` 四份各 164–252 行重複相同的迴圈骨架。
- **建議**：`handle_system_command` 改為「子指令→處理函式」的對照表（冷路徑，可放心）；provider 迴圈骨架抽出共用核心（`iterate_tool_loop`），各 provider 只保留「請求格式轉換」與「回應解析」。**後者碰熱路徑**：必須用現有的 provider 契約測試加上一次真實呼叫的 A/B，確認 `requests`／`tools`／延遲不變。

### F11（中，維護）`discord_bot.py` 混了四種責任

- **證據**：1,862 行：事件入口（`on_message`）、遞送（`_make_reply` 等）、持久化按鈕（Check／Luck／PDF／Help，約 400 行）、以及 Help／sudo／PDF 的 `View`／`Modal`／`Select`（約 500 行）。
- **建議（已實施為 `app/discord_transport/{gateway,delivery,interactions,lifecycle,controls,help_ui}.py`，見〈實施進度〉）**：原本的提案是拆成 `discord/events.py`、`discord/delivery.py`、`discord/buttons.py`、`discord/views_help.py`、`discord/views_sudo.py`、`discord/views_pdf.py`。**注意**：持久化按鈕以 `custom_id` 正規表示式比對，**格式不得改**（已發出的舊按鈕必須繼續有效，U4）。這是純搬移，加一個測試鎖住各 `custom_id` 模板。

### F12（低中，維護）`GroupState` 是 55 欄位的大物件

- **證據**：`models.py`（1,454 行）；欄位橫跨劇本、戰鬥、檢定、校正、地圖、商務、上傳暫存。
- **建議**：用**組合**分成子狀態（`ScenarioState`、`CombatState`〔已存在〕、`CheckState`、`CorrectionState`、`UploadState`），`to_dict`／`from_dict` 的 JSON 形狀**保持不變**（已有 `schema_version` 與 migration 表）。只在改到該區塊時順手做，不獨立立項；**不要為了效能改儲存**（§2.4）。

### F13（中，維護／熱路徑）舊版（legacy）戰鬥與 managed 戰鬥兩套實作並存

- **證據**：`combat.py` 1,765 行＋`combat_flow.py` 1,598 行＋`combat_resources.py` 716 行；工具面 12＋21 個；新戰鬥一律走 managed（`initialize_working_state`），舊版僅為進行中的戰鬥保留（`LEGACY_NEEDS_ADMISSION`）；`CombatEngine` 已把「模式只讀一次」收斂。
- **建議**：訂一個退役計畫：(1) 以事件計數確認近期新戰鬥 100% 為 managed；(2) 提供一次性「收尾或轉換」流程給仍在 legacy 的進行中戰鬥；(3) 移除 legacy 工具與其提示文字（同時縮小 F3 的工具面）。**不要**直接刪：存檔中的進行中戰鬥會壞。

### F14（低，維護）劇本／PDF 冷路徑

- **證據**：`scenario_*`＋`pdf_*`＋相關 services 約 18% 程式碼；`scenario_library`／`scenario_source_authoring`／`scenario_source_review`／`scenario_templates`／`trusted_scenario_source` 構成 5 模組的環（經延遲 import）；與熱路徑隔離良好，只經 `scenario_library` 的讀取介面。
- **建議**：收進 `app/scenario/` 套件並解環（把共用型別與路徑規則下放到 library 層）。優先度低、風險低；不要與熱路徑 PR 混在一起。

### F15（中，文件）標準與總覽文件已與程式脫節

- **證據**：`CODING_STANDARDS.md` 仍寫「`_execute_tool` 約 1,180 行」（實為 33 行，已由註冊表分派）與「provider 對照表複製在 9 個模組」（現有 `providers/registry`，殘留約 3 處：`guard`／`context_builder` 的 `getattr(config, f"{PROVIDER}_MODEL")`、`discord_bot` 預熱表）；`README.md` 的 Architecture 沒有 #179–#187 新增的任何模組（`turn_fallback`、`event_obligations`、`obligation_gate`、`turn_phases`、`presentation`、`scenario_adjacency`、`embedding_execution`、`memory_chunking` 在 README 中出現 0 次）。
- **建議**：本文件落地後，把 README 的 Architecture 精簡成指向本文件的摘要，標準文件的「為何」數字改成不含易過期的行數。

### F16（低，測試）時間敏感的測試

- **證據**：`tests/test_agentic_pipeline.py:165` 以 `elapsed < 0.15` 斷言；在負載下偶發失敗（本次審查期間遇過一次，單獨跑通過）。
- **建議**：改用注入時鐘或以「呼叫次數／順序」斷言並行，不用牆鐘。

## 5. 目標架構

不是重寫，而是把現有結構的邊界說清楚並用測試守住。

### 5.1 目標分層（箭頭＝允許的依賴方向）

```text
 L5  傳輸       discord_bot (入口) + discord_transport/{gateway,interactions,delivery,lifecycle,controls,help_ui}  ← 不含任何遊戲規則
 L4  入口路由   commands/router (+ TurnScope)  commands/handlers/*  ← 解析、權限、回覆
 L3  回合執行   agents/*  (prepare → mechanics → narrate → gate → commit → deliver)
                reply_pipeline (有序、純函式)   turn_fallback   turn_phases
 L2  規則／領域 checks  combat(engine)  dice  event_obligations  turn_resolution  presentation
                keeper_tools(registry, handlers)  prompt_builder
 L1  狀態與知識 state_transaction → group_state → db      scenario_library(read)  scenario_rag  memory_rag
 L0  基礎       config  observability(+ 回合摘要)  locks  models/domain  providers(轉接器＋共用工具迴圈核心)

 冷路徑（旁路）  app/scenario/*  pdf  pregen  help 建構  → 只能經 L1 的 scenario_library 與 state_transaction 與熱路徑相接
```

規則：(1) 依賴只能向下（L5→L0）；(2) L2 不得 import L3／L4／L5；(3) `keeper_tools` 不得 import `keeper`（以 `prompt_builder`／`tool_dispatch` 取代）；(4) 冷路徑不得被 L3 的熱路徑 import（只有 library 讀取介面例外）；(5) 規則引擎不得 import provider。這五條各寫成一個 import 圖閘門測試，沿用 `tests/test_architecture_*.py` 的做法。

### 5.2 回合管線（行為不變的內部形狀）

```text
run_turn(...) =
  scope = TurnScope(conversation)                # 取鎖／交接／釋放／post-turn hook（router 只認這個）
  ctx   = prepare(state, text, prefetched)       # build_context、intent、grounding 重用
  mech  = mechanics(ctx)                         # executor → recover(≤1 搜尋 +≤1 重跑) → resolve → handoff
  scope.to_narration()  (旗標開啟且無義務候選時)
  text  = narrate(ctx, mech)                     # narrator
  text  = reply_pipeline.run(text, ctx, mech)    # 有序清單：consistent, guard, consistent, obligations, party, finalize, player_text
  commit(ctx, text)                              # 單一交易
  deliver(...)                                   # 先 reply，再背景維護
```

每一步的 `await` 數量與順序，與現在完全相同；這是「拆函式」，不是「換流程」。

### 5.3 模組去向

| 現在 | 去向 | 備註 |
| --- | --- | --- |
| `keeper.py` 提示建構 | `prompt_builder.py` | 提示文字不改 |
| `keeper.py` 提交／時間線 | `turn_commit.py` | |
| `keeper.py` 記憶維護 | `memory_maintenance.py` | 背景執行，與熱路徑無關 |
| `keeper.py` 工具分派 | `tool_dispatch.py` | 與 `registry` 同層 |
| `discord_bot.py` | `discord_bot.py`（入口與事件）＋`discord_transport/*` | 已完成；`custom_id` 格式不變 |
| `commands/handlers/system.py` 的 641 行函式 | 子指令對照表 | 冷路徑 |
| `scenario_*`、`pdf_*` | `app/scenario/`、`app/pdf/` | 冷路徑，最後做 |
| `combat.py`＋`combat_flow.py` | 保留，legacy 退役後刪減 | F13 |

## 6. 路線圖（每步都有玩家體驗守門）

| 階段 | 內容 | 碰熱路徑？ | 守門／驗收 |
| --- | --- | --- | --- |
| **P0** | 總是輸出的單行回合摘要（F2）；訊息短碼（F7）；README／標準文件校正（F15）；支援組態表（F8）| 僅新增輸出 | 單元測試＋確認 `LOG_ENABLED=false` 下仍有一行；每回合新增成本 <0.1 ms |
| **P1** | `reply_pipeline`＋`run_turn` 拆六段（F4）；`TurnScope`＋鎖序測試＋watchdog（F5）；時間敏感測試改注入時鐘（F16）| 是（純結構）| 現有 2,740 項測試全過；新增順序測試；`await` 次數不變（以假 provider 計數斷言）；`turn.phases` 前後相同 |
| **P2** | 拆 `keeper.py`（F9），逐組刪除 `SLF001` 豁免；新增「`keeper_tools` 不得 import `keeper`」閘門 | 是（純搬移）| 同上；每個 PR 只搬一組 |
| **P3** | 拆 `discord_bot.py`（F11）；`handle_system_command` 對照表化（F10 冷路徑部分）| 部分（`custom_id` 不可變）| `custom_id` 模板測試；按鈕重啟重放測試 |
| **P4** | **有數據後**：F1 開啟旁白出鎖；F3 穩定模式化工具面；F13 舊版戰鬥退役；F10 provider 迴圈核心；F6 視 p99 決定 | 是（行為／延遲變動）| 以 P0 的摘要與 `turn.phases` 比較 p50／p95；五人真實執行；任何一項沒有改善就不合併 |
| **P5** | 冷路徑套件化、解環（F14）、`GroupState` 子狀態組合（F12，順手）| 否 | 一般重構標準 |

P0 與 P1 可以在沒有任何真實環境的條件下完成與驗證；P4 需要真實執行才能下結論，**在那之前不要宣稱延遲改善**。

## 7. 明確不做的事

1. **不換儲存**（不拆表、不換資料庫）：整列 JSON 對延遲無實質影響（§2.4）。
2. **不做逐回合動態工具過濾**：破壞提示快取前綴（F3）。
3. **不引入事件匯流排／CQRS／依賴注入框架**：會增加間接層與 `await`，對玩家沒有任何好處。
4. **不把 Executor 與 Narrator 並行**：Narrator 依賴 Executor 的確定性結果。
5. **不在玩家路徑加通用重試**：已有「≤1 搜尋＋≤1 重跑」的有界復原與回合總期限；再加只會把慢變得更慢。
6. **不自動強制釋放鎖**（F5）。
7. **不預先全面 `to_thread`**（F6）。
8. **不改持久化按鈕的 `custom_id` 格式**（F11）。

## 8. 如何驗證

- **延遲**：P0 之後每回合一行摘要；以 `turn_id` 聚合 `queue_wait`／`executor`／`narrator`／`requests`。五人真實執行的驗收門檻沿用 `docs/validation/camp_sunny_latency_before_after.md`（同時回覆不超過 120 秒；p50、p95 各改善 ≥20% 或證明剩餘延遲來自 provider）。
- **行為不變的重構**：現有測試＋「`await` 計數」測試＋公開文字快照；`ruff`、`mypy app`、`pytest` 三者沿用 CI。
- **結構**：新增 import 圖閘門（§5.1 五條規則）；`SLF001` 豁免清單只減不增。

## 9. 限制與未驗證

- **延遲數字的來源**：Camp Sunny 500 回合報告（規格引用）與一次 15 個 Executor 回合的小樣本；沒有在本次審查中重新量測，也沒有 Stage 3–5 的真實執行。
- **未驗證的假設**：開啟旁白出鎖的實際收益；穩定模式化工具面對 Codex 路徑的影響；同步 SQLite 提交在真實磁碟上的 p99；`search_scenario` 占 79% 工具呼叫在目前版本是否仍成立（#179–#184 已改變檢索與重用行為）。
- **審查方法**：靜態閱讀熱路徑（router、locks、supervisor、executor 開頭、post_turn、discord_bot 入口與按鈕、state_transaction）、全部模組的 import 圖（AST）、函式長度、設定與測試統計、以及合成狀態的序列化基準。`narrator`、`guard`、四個 provider 的工具迴圈、`combat*`、`checks/*`、`scenario_*`、`pdf_*` 只做了結構與規模層級的檢視，沒有逐行審查；對它們的結論（F10、F13、F14）屬結構判斷。
- 本文件是審查與提案，**沒有改動任何程式碼**。
