# 實作計畫：Agentic Keeper 架構最佳化

本計畫用「Fast/Slow Path 動態路由」搭配一個純 Python `KeeperSupervisor` 取代原本
`app/keeper.py` 單一 LLM 呼叫「一次做完所有事」的串行大黑箱——所有 Agent 之間的調度全部
由 Supervisor 以明確的 Python 控制流掌握，不用學習或除錯圖遍歷框架的行為。

核心原則：**不是所有訊息都需要經過完整的機制判定流程；所有 Agent 調度皆由 Supervisor 以
Python 程式碼明確掌握，能重用 `app/keeper.py` 既有、已經驗證過的邏輯就直接重用，不重新
發明一份風險更高的複製品。**

> 這份文件記錄的是**目前這個專案實際做出來、實測過的架構**，不是單純的規劃草稿——每個
> 章節都對照過 `app/agents/`、`app/commands/`、`app/services/prompt_config.py` 的真實
> 程式碼跟真實 LLM 呼叫的測試結果修訂過，跟實作有落差的地方已經訂正，落差本身也記錄在
> 「現況與經驗」一節，讓之後的人不用重新踩一次同樣的坑。

---

## 🗺️ 架構藍圖一：KeeperSupervisor 動態調度（取代原 `keeper.py` 單一 LLM 呼叫）

```text
[ 玩家／KP 助手輸入訊息 ]
           │
           ▼
+-------------------------------------------------------+
| 🚦 KeeperSupervisor (app/agents/supervisor.py)          |
| ↳ 負責建立 Context 並控制以下所有 Agent 的調度           |
+-------------------------------------------------------+
           │
           ▼
+-------------------------------------------------------+
| 0. Context Builder & Intent Router                    |
| (app/agents/context_builder.py／intent_router.py)      |
+-------------------------------------------------------+
           │ ↳ Context Builder：收集 RAG（scenario_rag）／記憶（memory_rag）上下文，
           │   純本地 BM25 檢索，不呼叫 LLM
           │ ↳ Intent Router：規則式（不呼叫 LLM）分類成三種意圖之一
           │
           ├─ speaker_role == "kp_assistant" ─────────► OOC_ASSISTANT（見下方）
           │
           ├─ PURE_ROLEPLAY（純角色扮演／簡短確認句／括號 OOC）────────┐
           │                                                          │
           └─ GAMEPLAY_ACTION（含機制動作，預設值——拿不準時寧可判定為  │
              這一類，交給 Executor 自己判斷要不要真的呼叫工具）─┐    │
                                                               │    │
+------------------------------------+                         │    │
| 1. ⚙️ Executor Agent                |                         │    │
| (app/agents/executor.py)            |                         │    │
+------------------------------------+                         │    │
| ↳ 呼叫一次 LLM＋工具（直接複用      |                         │    │
|   app/keeper.py 的 26 個工具與      |                         │    │
|   keeper._execute_tool，見下方      |                         │    │
|   「現況與經驗」第 1 點），真的     |                         │    │
|   擲骰／登記 pending_checks／改     |                         │    │
|   HP・SAN・戰鬥狀態並落庫           |                         │    │
| ↳ 產出 MechanicResult（純敘事性事實 |                         │    │
|   摘要，供 Narrator 使用）          |                         │    │
+------------------------------------+                         │    │
           │                                                    │    │
           ▼                                                    │    │
+------------------------------------+                         │    │
| 2. 🧮 State Reducer (純 Python)     |                         │    │
| (app/agents/state_reducer.py)       |                         │    │
+------------------------------------+                         │    │
| ↳ 不套用、也不落庫任何東西——真正   |                         │    │
|   的狀態變更已經在上一步 Executor   |                         │    │
|   呼叫工具時完成，這裡只是記錄用    |                         │    │
|   的流水線節點（見「現況與經驗」    |                         │    │
|   第 2 點：為什麼不能重複套用）     |                         │    │
+------------------------------------+                         │    │
           │                                                    │    │
           ▼                                                    ▼    │
+-------------------------------------------------------+            │
| 3. 🎭 Narrator Agent (app/agents/narrator.py)          |◄───────────┘
+-------------------------------------------------------+
           │ ↳ 呼叫一次 LLM，接收 MechanicResult 的事實摘要，絕對禁止重新判定機制
           │ ↳ 生成最終玩家可見敘事文案
           ▼
+-------------------------------------------------------+
| 4. 🛡️ Rule Validator (app/agents/rule_validator.py)    |
| 純正則規則校驗，不呼叫 LLM                             |
+-------------------------------------------------------+
           │ ↳ 檢查是否洩漏系統/AI 身分字眼、Markdown 代碼區塊有沒有閉合
           │
           ├─ PASS ──────────────────────────┐
           │                                 │
           └─ 驗證失敗 ──┐                   │
                        ▼                    │
           +-----------------------+         │
           | 5. 🛡️ Guard Agent     |         │
           | (app/agents/guard.py) |         │
           +-----------------------+         │
           | 驗證失敗才呼叫一次 LLM |         │
           | 重寫，Supervisor 用    |         │
           | while 迴圈最多重試 2 次|         │
           +-----------------------+         │
                        │                    │
           ┌────────────┴────────────────────┘
           ▼
+-------------------------------------------------------+
| 6. State Persistence（落庫本回合對話紀錄）             |
+-------------------------------------------------------+
           │ ↳ keeper._commit_turn_result：在 get_state_lock 底下重新載入最新狀態、
           │   append 這回合的 log、存檔——遊戲機制本身的狀態變更已經在 Executor
           │   階段落庫過了，這裡只補上對話歷史
           ▼
[ 回傳給呼叫端（app/commands/router.py）發送至 Discord／LINE ]


── OOC Assistant Path（Phase 10，KP 助手場外討論；見 app/agents/assistant.py）──

[ speaker_role == "kp_assistant" ]
           │
           ▼
+-----------------------------+
| Assistant Agent             |
| (app/agents/assistant.py)   |
+-----------------------------+
| 完全繞開上面 1～6 整條「機制判定與故事生成」主線，把整回合直接委派給
| keeper.run_turn(state, user_id, display_name, text, resolved_location,
| "kp_assistant") 一行呼叫——不在這裡另外組 prompt、另外過濾工具、另外決定
| 要落庫到哪裡（原因見「現況與經驗」第 5 點：早期版本自己重組過一次
| keeper.run_turn 的邏輯，main 後續替 KP Assistant 加的新規則沒有反映過來，
| 是這次改成整段委派的直接理由）。
| ↳ keeper.run_turn 內部沿用既有、持續在維護的邏輯：static/dynamic prompt
|   （含 KP 主持規則區塊＋最近的 kp_ooc_log 歷史）、
|   keeper._tools_for_speaker_role("kp_assistant") 工具白名單
|   （keeper._execute_tool 內部還有第二層同樣的白名單防禦）。
| ↳ 落庫不是無條件走 kp_ooc_log：keeper._kp_tool_result_creates_canon 判斷
|   這輪 KP 呼叫的工具是不是「正式遊戲事件」（skill_check／sanity_check／
|   npc_skill_check／offer_check_choice／roll_weapon_damage／
|   roll_impaling_damage，或 roll_dice 且 roll_context="game_resolution"）：
|   否 → keeper._commit_kp_ooc_turn_result 存進獨立的 kp_ooc_log，不寫入
|        state.log、不動 state.openai_previous_response_id；
|   是 → 升格：keeper._format_kp_canonical_history_message 把這輪 KP 指令
|        與觸發的工具事件格式化成一則
|        「[KP ASSISTANT / CANONICAL GAME EVENT]」訊息，透過
|        keeper._commit_turn_result 寫進正式 state.log，並正常延續
|        openai_previous_response_id 對話鏈——這輪從「場外討論」變成
|        「KP 代替玩家觸發了一個真的發生的遊戲事件」。
+-----------------------------+
           │
           ▼
[ 回傳給呼叫端；呼叫端（router.py）額外用 run_maintenance=not is_kp_assistant
  跳過這輪的背景記憶壓縮任務 ]
```

### 為什麼選擇純 Python Supervisor，而不是 LangGraph 之類的圖框架？
1. **除錯透明**：透過 `AgentMessage` 傳遞狀態，用一般的 `pdb` 或斷點就能逐步追蹤整條流水線，
   不需要另外學習圖遍歷框架自己的執行模型。
2. **精準控制 Repair Loop**：Supervisor 用一個簡單的 `while attempts < max_repairs` 迴圈
   就能控制 Rule Validator／Guard 的重試次數，邏輯完全攤在 `supervisor.py` 一個檔案裡。
3. **Fast Path 省下額外 LLM 呼叫**：純角色扮演／簡短確認句不會經過 Executor，只呼叫一次
   Narrator；OOC 助理走獨立的 Assistant Path，也只呼叫一次 LLM。實際能省多少時間會隨
   LLM 供應商／模型而不同，這份文件不重複一個沒有在這個專案裡實測過的具體秒數。

---

## 🗺️ 架構藍圖二：指令系統路由拆解（原 `commands.py` 重構）

```text
[ 玩家輸入系統指令："/coc pc"、"/coc check"、"/coc combat" … ]
           │
           ▼
+-------------------------------------------------------+
| 🚦 Command Router (app/commands/router.py)             |
+-------------------------------------------------------+
           │ ↳ 解析字串，根據第一個關鍵字動態分發
           │
           ├─ "/coc check"／"/coc luck" ──► 直接呼叫 app/legacy_commands.py 的
           │                                handle_check_command／handle_luck_decision
           │                                （擲骰／Luck 花費邏輯本身不需要拆成獨立
           │                                handler 模組，router.py 直接引用）
           ├─ "/coc combat" ──────────────► app/commands/handlers/combat.py
           ├─ "/coc pc"／"sheet"／…9 個角色相關子指令 ──► handlers/character.py
           ├─ "/coc newgame"／"pdf"／"kp"／"scenario"／…12 個系統類子指令 ──► handlers/system.py
           ├─ "/coc showpage"／"where"／…4 個地圖類子指令 ──► handlers/map_handler.py
           │
           ▼
[ 更新 GroupState 並發送回覆 ]
```

指令 handler 模組全部委派回 `app/legacy_commands.py` 裡已經驗證過的邏輯（例如
`_resolve_pdf_upload_choice_locked`、`_heal_character`、`_build_readiness_roster`），
不是重新實作一份——這批指令處理邏輯本身沒有機制判定或敘事生成的需求，純粹是路由層拆分，
跟藍圖一的 Agent 流水線是兩件事。

---

## 🗺️ 架構藍圖三：提示詞集中管理（Prompt Configuration）

`app/agents/` 底下只有三個階段真的會呼叫 LLM：Executor、Narrator、Guard（Context
Builder／Intent Router／State Reducer／Rule Validator 都刻意維持純 Python／規則判斷，
不呼叫 LLM）。這三個階段用到的提示詞全部集中在 `app/services/prompt_config.py`，
`agents/*.py` 只負責準備上下文變數，呼叫 `build_*` 函式組出最終文字。

```text
[ 業務邏輯與流程控制 ]                        [ 提示詞與字串組裝 ]
(app/agents/executor.py／narrator.py／       (app/services/prompt_config.py)
 guard.py)

       │                                            │
       ├─ build_executor_static_prompt(       ────►│ 前面接 EXECUTOR_INSTRUCTION，
       │    keeper._build_static_prompt(state))     │ 後面接 keeper.py 既有、持續在
       │◄── 組好的 static_system ───────────────────┤ 維護的角色卡／劇本／工具規則
       │                                            │
       ├─ build_narrator_static_prompt(  ─────────►│ 前面接 NARRATOR_INSTRUCTION
       │    keeper._build_static_prompt(state))     │ （含防雷、事實優先順序守則）
       │◄── 組好的 static_system ───────────────────┤
       │                                            │
       ├─ build_dynamic_prompt_with_context(  ────►│ Executor／Narrator 共用，把
       │    keeper._build_dynamic_prompt(...),      │ RAG／記憶上下文接在
       │    rag_context, memory_context)            │ keeper._build_dynamic_prompt
       │◄── 組好的 dynamic_system ──────────────────┤ 的輸出後面
       │                                            │
       ├─ build_mechanic_facts_block(result) ─────►│ Narrator 專用：把 Executor 的
       │◄── 「系統判定結果」事實區塊 ────────────────┤ MechanicResult 轉成事實文字
       │                                            │
       ├─ build_guard_dynamic_prompt(  ───────────►│ Guard 專用
       │    original_text, error_reason)            │
       │◄── 組好的 dynamic_system ──────────────────┤
       ▼                                            │
[ asyncio.to_thread 呼叫 provider.run_conversation ]│
（每個 provider 的 run_conversation 都是同步函式，   │
 見 app/providers/*.py，永遠用 asyncio.to_thread     │
 包起來呼叫，不要直接 await）                        │
```

### 集中管理的優點
1. **業務邏輯解耦**：`executor.py`／`narrator.py`／`guard.py` 只需要傳入變數（角色資料、
   機制結果、錯誤原因等），不用在程式碼裡寫死落落長的提示詞正文。
2. **防護規則統一**：防劇透、角色人設、工具使用規則等，全部在 `prompt_config.py` 一個
   檔案內調整。
3. **重用既有內容，不重新發明**：`keeper._build_static_prompt`／`_build_dynamic_prompt`
   已經是這個專案角色卡、劇本、NPC／地點索引、所有工具使用規則持續在維護的唯一來源，
   `prompt_config.py` 的 `build_*` 函式直接吃它們的輸出組出最終 prompt，不是另外重新
   宣告一份格式不同、內容會漸漸不同步的複製品。

---

## Phase 10：OOC Assistant Path（KP 助手場外討論）

`app/commands/router.py` 會判斷目前訊息是不是來自 `state.kp_assistant_user_id` 對應的
使用者（`speaker_role = "kp_assistant"`）。這種訊息屬於「場外」討論——KP 助手在跟 AI 討論
規則、劇情安排、要不要出手修正場面，不是遊戲世界裡的角色行動——所以完全不應該經過藍圖一
「機制判定與故事生成」那條主線，理由：
- Intent Router 若把這種訊息硬塞進 PURE_ROLEPLAY／GAMEPLAY_ACTION 分類，Executor／
  Narrator 會把 KP 的討論誤判成角色行動或需要擲骰的機制動作。
- 這輪對話如果寫進 `state.log`（正式劇情歷史），之後 Narrator／Executor 讀到歷史時會把
  這段場外討論誤認成劇情裡真的發生過的事。

做法：`intent_router.classify_intent` 一開始就檢查 `speaker_role`，是 `kp_assistant`
就無條件回傳新增的 `OOC_ASSISTANT` 意圖；`supervisor.run_turn` 分類出這個意圖時直接呼叫
`assistant.run_assistant(message)` 並提早 return。`assistant.py` 本身很薄，直接複用
`app/keeper.py` 已經驗證過的 KP Assistant 機制（見架構藍圖一的 OOC Assistant Path 區塊），
不重新實作一份。

**這次沒做，留給之後**：HyDE（生成偽規則文本再查）、MQE（把玩家問題擴展成多個同義查詢）
這兩個進階 RAG 技巧——都需要額外的 LLM 呼叫。目前 OOC Assistant 沿用
`context_builder.build_context` 已經幫所有訊息（不分 speaker_role）準備好的純本地 BM25
檢索結果（`scenario_rag.search`／`memory_rag.search_memory`），沒有加這兩個技巧。決定
先用真實的 KP 提問實測純 BM25 夠不夠準，真的不夠準再考慮加上去，避免預先多付兩次 LLM
呼叫的延遲與成本卻沒有實際證據證明有沒有幫助。

---

## 🏁 現況與經驗

以下是實作過程中，跟這份文件最早版本的規劃有落差、而且落差本身就值得記錄下來的地方——
不是失敗，是實測後做出的、有理由的取捨：

1. **「濃縮為 5 個高階工具」最後沒有採用，直接複用 `app/keeper.py` 的工具（目前 26 個，隨劇本庫等後續功能持續增加）。**
   最早的想法是把 `app/keeper.py` 的細顆粒度工具（`skill_check`、`adjust_character`、
   `add_npc_to_combat`……）濃縮成 5 個帶 `action` 子欄位的高階工具，省 token。實際動手做
   Executor Agent 時發現：要讓濃縮後的工具「真的」擲骰、真的登記 `pending_checks`、真的
   安全地改動狀態，等於要把 `keeper._execute_tool` 裡每一種工具的邏輯（含 `_mutate_and_
   save_state` 鎖機制、彈藥安全檢查、重傷規則自動觸發 CON 檢定等，全部是這個專案在別的
   分支上一項一項踩雷修出來的）重新刻一份。第一版流水線因為時間有限，先用回傳描述字串的
   假工具頂著，結果就是每一則遊戲機制訊息都「假裝成功、實際上什麼都沒發生」。修正方式：
   `app/agents/tool_gateway.py` 不重新發明條件式工具，直接 `TOOLS = keeper.TOOLS`，
   `make_tool_executor` 回傳的 callback 直接委派給 `keeper._execute_tool`。濃縮工具省
   token 的想法本身沒有錯，但要先確保安全機制原封不動被保留，不能為了省 token 犧牲正確性。

2. **`StateDelta`／State Reducer「由程式計算狀態變更」的原始設計沒有實際採用。**
   最早設計是 Executor 產出結構化的 `MechanicResult` + `StateDelta`（`hp_change`、
   `inventory_add` 等欄位），由 State Reducer 這個「純 Python 節點」讀取 `StateDelta`、
   實際套用到 `GroupState`／`Character` 上並落庫，藉此避免 LLM 直接寫庫的風險。實作後改
   成：Executor 呼叫工具時（透過 `keeper._execute_tool`）就已經是「真的」在改動狀態並且
   落庫了，`StateDelta` 因此永遠是空的。原因：如果 State Reducer 再對著同一份
   `StateDelta` 套用一次，等於重複套用同一個變更（例如 HP 被扣兩次），或者用 State
   Reducer 手上那份可能已經過期的 `state` 物件把 Executor 剛存好的最新資料蓋掉——兩者都
   是真的會發生的資料錯亂，不是理論上的疑慮。`domain/models.py` 的 `StateDelta` 型別
   保留下來（未來如果真的需要一個「跟哪個工具無關、單純描述這回合發生了什麼變化」的資料
   契約還用得到），但 `state_reducer.apply_mechanic_result` 現在只做記錄，不做任何套用
   或落庫。

3. **Guard Agent 沒有做成「只在 10-20% 時觸發」的機率抽樣。**
   最早的想法是 Guard 只在一部分（10-20%）的回合裡隨機觸發，用來控制額外 LLM 呼叫的成本。
   實際做出來的是決定性（不是機率性）的觸發條件：`rule_validator.validate_narrative` 用
   正則規則檢查敘事有沒有洩漏系統/AI 身分字眼、Markdown 代碼區塊有沒有正確閉合，檢查沒過
   才觸發 Guard 重寫，通過的話完全不會呼叫 Guard。這樣的好處是「什麼時候會被修正」是可以
   解釋、可以重現的，不會因為抽樣沒抽中就讓一段真的有問題的敘事流出去。

4. **拿掉對外部參考專案的引用。** 最早的文件開頭提到參考自某個外部開源專案的架構選型，
   這份文件已經改成單純描述這個專案自己的設計推論與實測結果，不再引用外部專案名稱——這個
   架構後續每一次修正都是照這個專案自己的程式碼、資料結構、既有機制（`keeper.py` 的鎖
   機制、KP Assistant OOC 隔離等）推導出來的，不是對照外部專案的做法。

5. **`assistant.py` 曾經自己重組過一次 `keeper.run_turn` 的邏輯，結果漏接了後續新增的
   規則；改成整段委派後這類問題結構性地不會再發生。** OOC Assistant Path 最早的實作是
   自己呼叫 `keeper._build_dynamic_prompt`／`keeper._tools_for_speaker_role`／
   `keeper._commit_kp_ooc_turn_result` 等個別函式，手動拼出跟 `keeper.run_turn` 平行的
   一份流程。main 之後替 KP Assistant 加上「擲骰即正史」（KP 成功觸發正式擲骰／檢定時，
   這輪對話要從 `kp_ooc_log` 升格寫進正式 `state.log`，見上面 OOC Assistant Path 圖裡的
   `_kp_tool_result_creates_canon`）時，這份平行複製品完全沒有反映到——因為它本來就不是
   `keeper.run_turn` 本身，新規則只加在後者身上。修正方式是把 `assistant.py` 簡化成一行
   `keeper.run_turn(..., "kp_assistant")` 呼叫，讓這條路徑之後不管 `keeper.run_turn`
   對 `speaker_role="kp_assistant"` 的行為怎麼演進，都自動繼承，不需要每次改 keeper.py
   都記得回來同步一次 assistant.py。跟本節第 1 點「不重新發明 keeper.py 已經驗證過的
   邏輯」是同一個教訓的第二次印證。

6. **不是每一輪 Keeper 敘事都走 Supervisor 流水線——`/coc check`／`/coc luck` 的結果敘事
   刻意繞過，這件事這份文件之前沒寫清楚。** 藍圖一畫的是「玩家／KP 助手輸入訊息」這條
   自由文字路徑（`app/commands/router.py::handle_text_message` 的非指令分支 →
   `supervisor.run_turn`），但玩家自己用 `/coc check`／`/coc luck` 在程式碼裡擲骰之後，
   結果敘事是 `handle_check_command`／`handle_luck_decision` →
   `_finalize_check_result`（`app/legacy_commands.py`）直接呼叫 `keeper.run_turn`，完全
   不經過 Supervisor／Executor／Narrator。這是刻意的：擲骰本身已經是確定的既成事實，不需
   要再讓 Intent Router／Executor 判斷要不要呼叫工具，直接一次 LLM 呼叫敘事即可，等同
   Supervisor 流水線裡「機制已定、只需要 Narrator」的那一半，只是沒有透過 Supervisor 走。

   複查時另外發現、且已處理的落差：`app/legacy_commands.py` 自己還留著一份完整的
   `handle_text_message`／`_handle_ordinary_text_message_locked`（一樣直接呼叫
   `keeper.run_turn`，邏輯幾乎跟 `router.py` 那份逐行對應），但 `app/main.py`／
   `app/discord_bot.py` 實際呼叫的是 `app/commands/router.py::handle_text_message`，
   `legacy_commands.py` 這份**沒有任何正式進線會呼叫到**——唯一還在呼叫它的是
   `tests/test_keeper_priority_integration.py` 和 `tests/test_kp_assistant_v2.py`
   裡驗證 priority gate／KP 助手輪替順序的測試，等於那批測試驗證的是一條正式流量根本不會
   走到的路徑，而 `router.py` 真正在用的那份 Priority Gate／KP 助手輪替邏輯完全沒有對應
   測試。處理方式：刪掉 `legacy_commands.py` 那份重複實作（連帶清掉 `router.py` 原本就沒用
   到、只是順手一起匯入的 `_handle_coc_command` 死 import），把兩個測試檔案裡呼叫
   `commands.handle_text_message` 的地方全部改打 `app.commands.router.handle_text_message`，
   mock 點也從 `commands.keeper.run_turn`（同步、經 `asyncio.to_thread` 分派到背景執行緒）
   換成 `router.supervisor.run_turn`（本身就是 `async def`，直接 `await`，不經執行緒）——
   priority-gate 測試原本用真正的 `threading.Event`／`threading.Lock` 跨執行緒同步「卡住
   一輪、讓其他人排隊」，換成 async fake 後改用單一事件迴圈內的 `asyncio.Event` 就夠了，
   不再需要跨執行緒同步。修正後用真實 LLM 呼叫再驗證一次 `router.handle_text_message` 本身
   沒有被這次刪除動到（見 `tests/test_keeper_priority_integration.py`／
   `tests/test_kp_assistant_v2.py` 裡對應測試）。

   注意：`_handle_coc_command`（`app/legacy_commands.py`，處理 `/coc pc`／`/coc kp`／
   `/coc newgame` 等指令的另一套舊派發邏輯）本身**還在**，只是失去了唯一的正式呼叫點
   （原本只被剛刪掉的 `handle_text_message` 呼叫）——`router.py` 自己內聯重新實作了整套
   `/coc` 指令派發（分派到 `app/commands/handlers/*.py`），不經過它。`_handle_coc_command`
   現在只被 `tests/test_kp_assistant_v2.py` 裡另外 4 個測試（`/coc kp quit`／`/coc end`／
   `/coc kp`／`/coc newgame`）直接呼叫，是跟這次處理的問題同類、但沒有一併處理的第二個
   實例——這次複查沒有動它，留給之後決定。

7. **兩個「線路接對了但沒接上」的缺陷，複查時才發現、已修正。** 一是 `supervisor.py`
   呼叫 Executor 後算出的 `mechanic_result` 從沒寫回 `message.payload`——`narrator.py`
   讀到的永遠是 `None`，於是「GAMEPLAY_ACTION 應該把 Executor 的機制事實交給 Narrator」
   這件事其實從沒真的發生過，每一輪機制動作都被當成純角色扮演在敘事，直接牴觸本節第 1、
   2 點想強調的「Executor 真的落庫、Narrator 真的看得到結果」。二是 `context_builder.py`
   的 Scenario RAG 沒有依 `SCENARIO_RAG_ENABLED` 開關運作，不管開關與否都會跑
   `scenario_rag.get_index`／`search`，跟 `keeper._build_static_prompt` 本身有的同一個
   開關矛盾——關掉 RAG 並沒有真的省下這筆查詢成本與延遲。兩者都是「程式碼看起來合理、
   單獨測試也不會報錯，但沒有真的用真實 LLM 回合去驗證輸出內容有沒有反映機制事實」才會
   漏掉的那種缺陷；修正後都補了對應的 regression test（見
   `tests/test_agentic_pipeline.py`），也是這份文件開頭「跟真實 LLM 呼叫的測試結果修訂
   過」這句話這次真正兌現的地方。
