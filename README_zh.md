# COC7e 守密人 Bot（Discord）

[English](README_zh.md) | **繁體中文**

在 Discord 頻道裡上傳一份《克蘇魯的呼喚》第七版（COC7e）劇本 PDF，或檔名以 `scenario` 開頭的 UTF-8 Markdown 劇本，就能讓 LLM 扮演守密人（Keeper），直接在聊天室裡跑團。規則判定（技能檢定、SAN 值、擲骰）由程式碼負責計算，LLM 負責讀劇本、敘事、決定什麼時候該擲骰。後端支援 Claude（Anthropic）、Gemini（Google）、OpenAI 與已登入的 Codex CLI；Codex 可處理對話與一般文字分析，PDF／圖片／OCR 和預製角色卡分析則須使用 API Provider，因為量測到的 Codex 擷取正確率不足。詳見[安裝設定](docs/guides/setup_zh.md)及 [Codex OAuth 指南](docs/guides/codex_oauth_testing_zh.md)。

## 快速開始

### 還沒把 Bot 架起來？

完整安裝教學（申請 Discord 憑證、設定 `.env`、本機啟動）都搬到 **[docs/setup.md](docs/guides/setup_zh.md)**，跟著那份文件從頭做一次即可。

### Bot 已經跑起來、已經加進群組/伺服器？

1. **上傳劇本**：把 COC7e 劇本 PDF，或檔名以 `scenario` 開頭的 UTF-8 `.md` 檔案直接傳到群組／頻道裡。Markdown 會直接匯入文字、不跑 PDF/OCR；圖片較多的 PDF 要等一下（會先回「處理中」，實際結果晚一點才會出現）。
2. **建立角色**（任一位玩家都要做這步）：
   ```
   /coc pc 角色名 職業
   ```
   例如 `/coc pc 陳月 記者`。想用更符合規則的建角流程或劇本內建的預製角色，見 **[docs/gameplay.md](docs/guides/gameplay_zh.md)** 的完整說明。
3. **開始玩**：至少一位角色建好後，先輸入 `/coc start` 開始劇情；成功開場後，直接在群組/頻道裡打字描述你的角色要做什麼（不用加任何指令），守密人就會接手敘事、要求擲骰、更新 HP/SAN。
4. 隨時可以 `/coc help` 看 Discord 分類式指令列表、`/coc status` 看目前進度、`/coc sheet` 看自己的角色卡。需要手動複製指令時，請看 **[docs/player_command_reference.md](docs/references/player_command_reference_zh.md)**；完整玩法細節見 **[docs/gameplay.md](docs/guides/gameplay_zh.md)**。

## 架構

```text
Discord 頻道 -> app/discord_bot.py -> app/commands/router.py
  |
  +-- 指令／劇本附件 -> commands/handlers/*.py -> legacy_commands.py
  |
  +-- 玩家文字 -> agents/supervisor.py
  |     -> context_builder.py（state／劇本與記憶檢索）
  |     -> intent_router.py（規則式分類）
  |     -> 遊戲行動：executor.py -> 真實工具更新 -> Python 驗證裁決
  |        純角色扮演：略過 Executor
  |     -> narrator.py -> Guard／防雷／下一步指示檢查 -> 回覆
  |
  +-- 檢定／Luck 結果後續、開場後備 -> 同一 Supervisor
  |     -> Narrator 使用該入口限定工具 -> 驗證 -> 回覆
  |
  +-- KP 場外討論 -> Supervisor -> assistant.py 獨立流程

共用：keeper.py（提示詞／工具） -> providers/*_provider.py；Codex 對話與結構化分析共用 Codex CLI transport
      services/turn_context.py、turn_resolution.py（權威 state／裁決驗證）
      scenario_rag.py、memory_rag.py（BM25／可選 embeddings）
儲存：data/coc_bot.db、data/groups/（圖片）、data/scenarios/（劇本庫）
```

**指令與訊息路由**
- `app/discord_bot.py`：Discord 專用的常駐連線入口，把 Discord 的事件轉譯成呼叫 `app/commands/router.py`
- `app/commands/router.py`：**平台無關**的指令路由入口（取代舊版單一檔案 `app/commands.py`，已重新命名為 `app/legacy_commands.py`）——依關鍵字分派到下面的 handler 模組，自由文字（不是 `/coc` 指令）交給 `app/agents/supervisor.py`
- `app/commands/handlers/`：依領域拆開的指令處理模組——`character.py`（建角／角色卡等 9 個子指令）、`combat.py`、`system.py`（`newgame`／`pdf`／`kp`／`scenario`／`status`／`era`… 等 12 個子指令，含劇本庫的 `/coc scenario` 系列）、`map_handler.py`（`showpage`／`where`／`enter`／`leavemap`）——這些模組委派回 `app/legacy_commands.py` 裡既有、已驗證過的邏輯，不是重新實作
- `app/legacy_commands.py`（原 `app/commands.py`）：PDF／Markdown 劇本上傳流程、`/coc check`／`/coc luck`（玩家自己在程式碼裡擲骰，結果交給同一個 Supervisor 的 `resolved_check_followup` 入口敘事，不得重擲已結算的骰）、預製角色合併、角色離開/回歸等仍集中在這裡的邏輯

**Agentic Keeper：自由文字（角色扮演／遊戲行動）走的多代理流水線**
- `app/agents/supervisor.py`：純 Python 調度器，統一玩家一般回合、檢定結果後續與開場後備；依入口與訊息意圖決定機制及敘事流程
- `app/agents/context_builder.py`：收集 Scenario RAG／Memory RAG 上下文，可使用 embeddings，不保證完全本地；Scenario RAG 受 `SCENARIO_RAG_ENABLED` 控制，戰鬥中略過主動檢索
- `app/agents/intent_router.py`：規則式（不呼叫 LLM）分類成 `OOC_ASSISTANT`（KP 助手場外討論）／`PURE_ROLEPLAY`（純角色扮演）／`GAMEPLAY_ACTION`（含機制動作）
- `app/agents/executor.py` + `app/agents/tool_gateway.py`：`GAMEPLAY_ACTION` 才會呼叫，直接複用 `keeper.TOOLS`／`keeper._execute_tool`（真的擲骰、真的落庫，不是重新發明一份工具）
- `app/agents/assistant.py`：`OOC_ASSISTANT`（KP 助手場外討論）的獨立 agent，具有自己的 provider／工具／Guard／歷史提交流程，保留明確建立正式事件的規則
- `app/agents/narrator.py`：生成玩家可見敘事；一般回合不提供工具，檢定結果後續與開場後備則提供各自限定的工具
- `app/agents/rule_validator.py` + `app/agents/guard.py`：正則規則校驗（洩漏系統字眼、Markdown 代碼區塊沒閉合），沒過且啟用 Guard 時最多進行 2 次修復性 LLM 呼叫，仍不合格則使用後備回覆
- `app/agents/state_reducer.py`：純記錄用的流水線節點，不做任何狀態套用（真正的狀態變更在 Executor 呼叫工具時就已經完成並落庫）
- `app/services/prompt_config.py`：Executor／Narrator／Guard 三個真的會呼叫 LLM 的階段共用的提示詞組裝，包在 `keeper._build_static_prompt`／`_build_dynamic_prompt`（既有、持續在維護的內容）外面
- `app/domain/models.py`：流水線內部傳遞用的 `AgentMessage`／`MechanicResult`／`StateDelta`／`TurnResolution` 資料結構

目前入口與流程見 **[統一 Keeper 流程](docs/specs/refactor/unified_keeper_turn_flow_design_spec_zh.md)**；權威狀態交接見 **[回合一致性規格](docs/specs/bug/log_backed_turn_consistency_design_spec_zh.md)**。原始設計脈絡、已知落差與踩過的坑，見 **[docs/agentic_keeper_design_spec.md](docs/specs/refactor/agentic_keeper_design_spec_zh.md)**。

**核心遊戲邏輯（不分走哪條路由都會用到）**
- `app/keeper.py`：守密人的系統提示詞組裝、工具定義（擲骰／檢定／戰鬥／角色數值／劇本庫圖片與章節推進等）、工具執行，不綁定特定 LLM
- `app/providers/anthropic_provider.py`：Claude（Anthropic Messages API）介面卡，含 prompt caching
- `app/providers/gemini_provider.py` / `app/providers/openai_provider.py` / `app/providers/codex_provider.py`：Gemini（google-genai SDK）／OpenAI／已登入 Codex CLI 介面卡；Codex 用於對話及一般文字分析
- `app/locks.py`：per-conversation 鎖，防止同一個聊天室的兩則訊息互相覆蓋對方的存檔；KP 助手訊息另有優先權佇列
- `app/combat.py`：正式戰鬥輪次狀態機（先攻順位、回合、HP）
- `app/creation.py`：互動式建角流程（擲屬性、分配職業/興趣技能點數）
- `app/pregen_extractor.py`：從劇本文字中抽取內建的預製調查員
- `app/intent_parser.py`：規則式（regex，不額外呼叫 LLM）偵測玩家訊息裡的移動意圖與「進入某地點」意圖
- `app/scene_map.py`：把劇本平面圖轉成結構化房間圖（節點＋方位邊），並提供「目前位置＋朝向＋方向＋第幾個門 → 目的地房間」的純程式碼解析（不靠 LLM 猜）
- `app/scenario_rag.py`：BM25 與可選 embeddings 混合檢索（`SCENARIO_RAG_ENABLED=true` 時啟用），取代「整份劇本塞進 system prompt」，改成 Keeper 用 `search_scenario` 工具按需查詢
- `app/memory_rag.py`：對已裁切掉的舊對話做語意檢索，補足 `campaign_summary` 滾動摘要「越壓越抽象」的細節遺失
- `app/scenario_index.py`：抽取劇本的 NPC／怪物與地點數值索引（`/coc index`，上傳劇本時也會自動跑一次），給 Keeper 一份固定對照表，避免同一隻怪物前後講出不同數值
- `app/scenario_library.py`：可重用的 PDF／Markdown 劇本庫（`data/scenarios/<劇本ID>/`）——同一份劇本不用每個聊天室各自重新解析一次，支援章節切分與「目前章＋下一章」滑動 Context 視窗、圖片資產搜尋、KP 專用的 `/coc scenario list／use／clean／reparse／cancel`。完整設計見 **[docs/scenario_library_design_spec.md](docs/specs/feature/scenario_library_design_spec_zh.md)**
- `app/dice.py`：COC7e 規則判定（d100、獎懲骰、成功等級、SAN）
- `app/models.py`：角色卡／聊天室狀態資料結構與快速生成（3d6 法）
- `app/pdf_loader.py`：抽取上傳 PDF 的文字內容（文字層改用 MarkItDown + markitdown-ocr 預處理，PyMuPDF 負責頁面轉圖片與備援文字層；圖片偏多的頁面會用視覺理解／OCR 備援，並保留這些頁面的實際圖片供之後展示；平面圖頁面會額外呼叫 `app/scene_map.py` 拆出結構化房間圖）；另有低成本的 `extract_preview()`，供劇本庫上傳去重使用
- `app/markitdown_shim.py`：將 MarkItDown 的 OpenAI 型式視覺介面接到專案設定的 provider，包含 Anthropic 轉接
- `app/db.py`：SQLite 存取層，把每個聊天室的遊戲狀態、角色索引鏡像、Scenario/Memory RAG 的索引快取都存成資料庫裡的一列（取代原本各自的 `data/groups/*.json` 檔案）
- `app/repositories/group_state.py`（原 `app/state.py`）：呼叫 `app/db.py` 保存每個聊天室的遊戲狀態與角色索引鏡像；劇本頁面圖片仍另外存成 PNG 檔案（不進資料庫）
- `app/checks/`：檢定引擎。`service.py` 把檢定規則套用到交易交給它的狀態（玩家的 `/coc check` 或按鈕、Luck 決定、Keeper 的自動擲骰）；`rules.py` 負責骰子與算術（走 `DicePort`）；`luck.py` 說明誰能花 Luck；`events.py` 把已結算的檢定只記錄一次。指令（`app/commands/handlers/checks.py`）與 Keeper 工具（`app/keeper_tools/checks.py`）都是它之上的薄轉接；`tests/test_architecture_checks.py` 讓引擎不匯入傳輸層、Keeper、provider 或戰鬥程式碼（[規格](docs/specs/refactor/check_engine_design_spec_zh.md)）
- `app/repositories/state_transaction.py`：遊戲狀態唯一的寫入入口。鎖住該聊天室、在 `BEGIN IMMEDIATE` 交易內讀取最新資料、驗證 timeline／action ledger／revision、執行 mutation，並把狀態、事件與 action 結果一起提交。`tests/test_architecture_state_writes.py` 會在其他程式碼直接寫遊戲狀態時讓建置失敗（[規格](docs/specs/refactor/state_transaction_design_spec_zh.md)）

只需要啟動 `app/discord_bot.py`；Discord 直接附加圖片，不需要 webhook、ngrok 或公開圖片網址。

### Token 預算與 API 准入

OpenAI 路徑量測輸入組成、套用可配置的歷史預算，並依 response headers 推估請求／Token 配額與共用冷卻。回合期限涵蓋 LLM 准入、API、重試及敘事；各階段可配置輸出上限，預設省略。

截斷回應中的工具不會執行；先前已提交的變更與私訊／圖片佇列會保留，不自動重播工具。詳見 **[Token 准入規格](docs/specs/enhancement/token_admission_evaluation_design_spec_zh.md)**。

### 效能分析 profiler

先安裝開發工具：

```bash
pip install -r requirements-dev.txt
```

一般啟動不會啟用 profiler；需要分析時，用 `BOT_PROFILER` 明確選擇工具：

```bash
BOT_PROFILER=pyinstrument ./scripts/start_bot.sh discord --name profile-async
BOT_PROFILER=py-spy ./scripts/start_bot.sh discord --name profile-live
```

`pyinstrument` 會產生 async-aware HTML call stack，`py-spy` 會 attach 到已啟動的
Bot 並產生 SVG flame graph；兩者的 artifact 都會放在 `.runtime/bots/`，可用
`./scripts/bot_status.sh` 查路徑。可用值為 `off`、`pyinstrument`、`py-spy`，也可以
用 `./scripts/start_bot.sh --help` 直接查看範例。macOS 使用 `py-spy` attach process
時可能需要 root 或額外的 process attach 權限；權限不足時 script 會明確失敗。

## 文件導覽

這份 README 只放「第一次認識這個專案」需要的東西；其他內容都分別搬到獨立文件，讓每份文件保持專注、方便找：

| 想知道... | 看這份 |
|---|---|
| 怎麼申請 Discord 憑證、設定 `.env`、本機啟動 | **[docs/setup.md](docs/guides/setup_zh.md)** |
| 有哪些指令、怎麼玩、各項機制怎麼操作 | **[docs/gameplay.md](docs/guides/gameplay_zh.md)** |
| 為什麼有這個限制、目前怎麼做的、實測過什麼、之後想擴充要改哪裡（逐功能的開發紀錄） | **[docs/changelog.md](docs/changelog_zh.md)** |
| Keeper 的系統提示詞規則（`_build_static_prompt()` 的可讀版本） | [docs/keeper_skill.md](docs/references/keeper_skill_zh.md) |
| Agentic Keeper 多代理流水線的完整設計、已知落差與踩過的坑 | **[docs/agentic_keeper_design_spec.md](docs/specs/refactor/agentic_keeper_design_spec_zh.md)** |
| 可重用劇本 PDF 庫（章節切分、滑動 Context 視窗、圖片資產、`/coc scenario` 系列指令）的完整設計 | **[docs/scenario_library_design_spec.md](docs/specs/feature/scenario_library_design_spec_zh.md)** |
| HTTP 端點、聊天指令、PDF 上傳生命週期、Provider／劇本庫內部介面的參考手冊 | [docs/API.md](docs/references/API_zh.md) |
| NPC 隊友設計與資訊流控制的完整版 | [docs/references/gameplay_style.md](docs/references/gameplay_style_zh.md) |
| COC7e 規則 vs. 目前程式碼實作的落差清單 | [docs/references/rules_reference.md](docs/references/rules_reference_zh.md) |
| 攜帶物合理性審查的設計規格與實作狀態 | [docs/references/carry_audit.md](docs/references/carry_audit_zh.md) |
| 跟手動檔案式備團工作流（[coc-kp-host](https://github.com/SumanasJ/coc-kp-host)）的對應表 | [docs/references/prep_persistence.md](docs/references/prep_persistence_zh.md) |


所有規格均提供英文與繁體中文版本，分類與實作狀態見[文件索引](docs/README_zh.md)。

## 本機 Codex OAuth 實驗

[本機 Codex OAuth 實驗](docs/guides/codex_oauth_testing_zh.md)
