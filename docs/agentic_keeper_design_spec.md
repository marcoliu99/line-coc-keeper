# 實作計畫：Agentic Keeper 架構最佳化 (Discord Only)

本計畫整合了「Fast/Slow Path 動態路由」與參考自 `leezehuan/COC-AI-keeper` 的 **純 Python `KeeperSupervisor` (選項 A)** 架構，徹底汰除難以除錯的串行大黑箱與複雜的 LangGraph。

核心原則：**不是所有訊息都需要經過完整的三個 LLM Agent；所有 Agent 調度皆由 Supervisor 以 Python 程式碼明確掌握。**

---

## 🗺️ 架構藍圖一：KeeperSupervisor 動態調度 (取代原 keeper.py)

```text
[ 玩家在 Discord 輸入訊息 ]
           │
           ▼
+-------------------------------------------------------+
| 🚦 KeeperSupervisor (app/agents/supervisor.py)          |
| ↳ 負責建立 Context 並控制以下所有 Agent 的調度迴圈          |
+-------------------------------------------------------+
           │
           ▼ (Phase 1)
+-------------------------------------------------------+
| 0. Intent Classifier & Context Builder                |
+-------------------------------------------------------+
           │ ↳ 判斷是 PURE_ROLEPLAY 還是 GAMEPLAY_ACTION
           │ ↳ 分層載入上下文 (不每次全載入 RAG)
           │
           ├─ Fast Path (純角色扮演) ─────────┐
           │                                 │
           └─ Slow Path (含機制動作) ─┐       │
                                     │       │
                                (Phase 3)    │
+------------------------------------+       │
| 1. ⚙️ Executor Agent (機制執行)    |       │
+------------------------------------+       │
| ↳ 掛載 Tool Gateway (濃縮為5個高階工具)    |
| ↳ 產出結構化 MechanicResult 與 StateDelta  |
+------------------------------------+       │
           │                                 │
           ▼                                 │
+------------------------------------+       │
| 2. 🧮 State Reducer (純 Python)    |       │
+------------------------------------+       │
| ↳ 攔截 LLM 直接寫庫的風險，由程式計算血量 |
+------------------------------------+       │
           │                                 │
           ▼                                 ▼
                                   (Phase 4)
+-------------------------------------------------------+
| 3. 🎭 Narrator Agent (敘事生成)                       |
+-------------------------------------------------------+
           │ ↳ 接收 Facts-Only Input，絕對禁止重新判定機制
           │ ↳ 生成最終敘事文案
           ▼
                                   (Phase 5)
+-------------------------------------------------------+
| 4. 🛡️ Rule Validator (純 Python 規則校驗)             |
+-------------------------------------------------------+
           │ ↳ 檢查是否洩漏系統 ID 或 Markdown 異常
           │
           ├─ PASS (無風險) ─────────────────┐
           │                                 │
           └─ HIGH_RISK (高風險) ─┐          │
                                 ▼          │
                 +-----------------------+  │
                 | 5. 🛡️ Guard Agent     |  │
                 | (只在 10-20% 時觸發)  |  │
                 +-----------------------+  │
                 | ⚠️ 觸發 Repair Loop   |  │
                 | Supervisor 會退回前述 |  │
                 | 節點要求重寫 (最多2次)|  │
                 +-----------------------+  │
                                 │          │
           ┌─────────────────────┴──────────┘
           ▼
                                   (Phase 6)
+-------------------------------------------------------+
| 6. State Persistence (落庫與記憶)                     |
+-------------------------------------------------------+
           │ ↳ 統一提交本回合副作用：安全寫入 SQLite，更新長期記憶
           ▼
[ 發送回覆至 Discord ]
```

### 為什麼選擇純 Python Supervisor (Option A)？
1. **除錯極度透明**：透過 `AgentMessage` 傳遞狀態，我們可以用傳統的 `pdb` 或斷點逐步追蹤，不需要學習 LangGraph 複雜的圖遍歷。
2. **精準控制 Repair Loop**：Supervisor 可以輕易用 `while repair_attempts < 2` 迴圈來要求 NarratorAgent 修正文案。
3. **效能優勢**：結合 Fast Path，純角色扮演 (如「我環顧四周」) 只需要過 Narrator 一個 LLM，耗時從 15 秒大幅壓縮至 5-8 秒。

---

## 🗺️ 架構藍圖二：指令系統路由拆解 (原 commands.py 重構)

```text
[ 玩家輸入系統指令: "/coc pc", "/coc check" ]
           │
           ▼
+-------------------------------------------------------+
| 🚦 Command Router (app/commands/router.py)            |
+-------------------------------------------------------+
           │ ↳ 解析字串，根據第一個關鍵字動態分發 (Dispatch)
           │
           ├─ "/coc combat" ────────┐
           ├─ "/coc check" ───────┐ │
           ├─ "/coc pc" ────────┐ │ │
           │                    │ │ │
           ▼                    ▼ ▼ ▼
+-------------------------------------------------------+
| 📦 Handlers (各司其職的業務模組)                      |
| (combat.py, check.py, character.py, system.py)        |
+-------------------------------------------------------+
           │
           ▼
[ 更新 GroupState 並發送秒回至 Discord ]
```

---

## 🗺️ 架構藍圖三：提示詞集中管理 (Prompt Configuration)

為了確保 Agent 邏輯乾淨且專注於控制流，所有 LLM 提示詞（System Prompts、User Templates、JSON Schemas）將被集中抽取至 `app/services/prompt_config.py` 統一管理。

```text
[ 業務邏輯與流程控制 ]                   [ 提示詞與字串渲染 ]
(app/agents/*.py)                     (app/services/prompt_config.py)

       │                                     │
       ├─ (1) 呼叫 build_intent_prompt() ───►│ (為 Intent Router 準備)
       │◄── 回傳組裝好的 Prompt 字串 ────────┤ ↳ 組合 INTENT_SYSTEM_PROMPT
       │                                     │
       ├─ (2) 呼叫 build_turn_plan_prompt() ─►│ (為 Planner Agent 準備)
       │◄── 回傳組裝好的 Prompt 字串 ────────┤ ↳ 組合 PLAN_SYSTEM_PROMPT
       │                                     │
       ├─ (3) 呼叫 build_keeper_response() ─►│ (為 Narrator Agent 準備)
       │◄── 回傳組裝好的 Prompt 字串 ────────┤ ↳ 組合 NARRATOR_SYSTEM_PROMPT
       │                                     │
       ├─ (4) 呼叫 build_reflection_prompt()►│ (為 Guard Agent 準備)
       │◄── 回傳組裝好的 Prompt 字串 ────────┤ ↳ 組合 GUARD_SYSTEM_PROMPT
       │                                     │
       ▼                                     │
[ 呼叫 LLMClient 執行對話 ]                  │
```

### 集中管理的優點：
1. **業務邏輯解耦**：`agent.py` 只需要傳入變數（如血量、理智、擲骰結果），不用在程式碼中寫死落落長的 Markdown 提示詞。
2. **防護規則統一**：防劇透、角色人設、JSON 格式強制約定等，全部在一個檔案內調整，大幅提升維護性。

---

## 🏁 最終執行路徑 (Roadmap)

1. **Phase 0: 建立基礎資料契約 (Data Contracts)**
   - 建立 `app/domain/models.py`，定義 `MechanicResult`, `StateDelta`, `GameEvent`, `AgentMessage`。
2. **Phase 2: 建立 Application Layer 與 Prompt Config**
   - 將核心邏輯從 Discord 介面抽離至 `app/services/` (如 `combat_service.py`)。
   - 建立 `app/services/prompt_config.py`，集中管理所有 LLM 提示詞。
3. **Phase 3: 指令系統解體 (`commands.py` 重構)**
   - 建立 `app/commands/handlers/` 目錄與 Router。
4. **Phase 4~8: 建立各獨立 Agent 與 State Reducer**
   - 依序實作 `ContextBuilder`, `Executor`, `StateReducer`, `Narrator`, `RuleValidator`, `Guard`。
5. **Phase 9: 實作 KeeperSupervisor 進行整合**
   - 撰寫 `supervisor.py`，將所有 Agent 以純 Python 流水線與 `while` 迴圈串接。
   - 將 `discord_bot.py` 的入口全面切換到新架構。
