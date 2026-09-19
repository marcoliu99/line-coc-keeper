# COC7e 守密人 Bot（Discord）

在 Discord 頻道裡上傳一份《克蘇魯的呼喚》第七版（COC7e）劇本 PDF，就能讓 LLM 扮演守密人（Keeper），直接在聊天室裡跑團。規則判定（技能檢定、SAN 值、擲骰）由程式碼負責計算，LLM 負責讀劇本、敘事、決定什麼時候該擲骰。後端 LLM 可以在 Claude（Anthropic）、Gemini（Google）、OpenAI 之間切換，見 [docs/setup.md](docs/setup.md)。

## 快速開始

### 還沒把 Bot 架起來？

完整安裝教學（申請 Discord 憑證、設定 `.env`、本機啟動）都搬到 **[docs/setup.md](docs/setup.md)**，跟著那份文件從頭做一次即可。

### Bot 已經跑起來、已經加進群組/伺服器？

1. **上傳劇本**：把 COC7e 劇本 PDF 檔案直接傳到群組/頻道裡。圖片較多的劇本要等一下（會先回「處理中」，實際結果晚一點才會出現）。
2. **建立角色**（任一位玩家都要做這步）：
   ```
   /coc pc 角色名 職業
   ```
   例如 `/coc pc 陳月 記者`。想用更符合規則的建角流程或劇本內建的預製角色，見 **[docs/gameplay.md](docs/gameplay.md)** 的完整說明。
3. **開始玩**：角色建好之後，直接在群組/頻道裡打字描述你的角色要做什麼（不用加任何指令），守密人就會接手敘事、要求擲骰、更新 HP/SAN。
4. 隨時可以 `/coc help` 看 Discord 分類式指令列表、`/coc status` 看目前進度、`/coc sheet` 看自己的角色卡。需要手動複製指令時，請看 **[docs/player_command_reference.md](docs/player_command_reference.md)**；完整玩法細節見 **[docs/gameplay.md](docs/gameplay.md)**。

## 架構

```
Discord 頻道 ──(gateway)──▶ app/discord_bot.py
                                              ▼
                        app/commands/router.py（指令路由，平台無關，取代舊版單一 app/commands.py）
                                              │
                    ┌─────────────────────────┼───────────────────────────────┐
                    ▼                         ▼                               ▼
     /coc pc／sheet／combat／      /coc newgame／pdf／kp／scenario／…    自由文字（角色扮演／
     newgame／pdf／… 等指令        → app/commands/handlers/*.py           遊戲行動），走
     → app/commands/handlers/*.py    （委派回 app/legacy_commands.py       app/agents/supervisor.py
     或直接呼叫 app/legacy_commands.py 已驗證過的邏輯）                    （Agentic Keeper 多代理
     （PDF 上傳、/coc check、/roll 等）                                    流水線，見下方）
                                                                              │
                                                                              ▼
                                              app/agents/context_builder.py（RAG／記憶上下文，
                                              純本地 BM25，不呼叫 LLM）
                                              → app/agents/intent_router.py（規則式分類：
                                                OOC_ASSISTANT／PURE_ROLEPLAY／GAMEPLAY_ACTION）
                                              → app/agents/executor.py（機制判定＋工具呼叫）
                                                或 app/agents/assistant.py（KP 助手場外討論，
                                                委派給 keeper.run_turn）
                                              → app/agents/narrator.py（生成最終敘事文字）
                                              → app/agents/rule_validator.py／guard.py
                                                （正則校驗失敗才觸發一次修復性 LLM 呼叫）
                                                              │
                                                              ▼
                                      app/keeper.py（守密人的工具定義與執行、System Prompt
                                      組裝，LLM 供應商無關——被 Agentic Keeper 流水線與
                                      /coc check、/coc luck 的結果敘事共用）
                                                              │
                              app/scenario_rag.py（Scenario RAG，SCENARIO_RAG_ENABLED=true
                              時才啟用，本地 BM25 檢索取代整份劇本塞進 prompt）
                              app/memory_rag.py（長期記憶：對已裁切的舊對話做語意檢索）
                                                              │
                                                              ▼
                              app/providers/anthropic_provider.py
                              app/providers/gemini_provider.py／openai_provider.py
                                                    ← LLM_PROVIDER 決定走哪一條
                                                              │
                                                              ▼
                        工具呼叫（擲骰／技能檢定／SAN／角色數值／戰鬥／劇本庫圖片與章節推進）
                                                              │
                                                              ▼
                 data/coc_bot.db （每個聊天室的角色卡、劇本、對話紀錄；頁面圖片另存 data/groups/；
                 可重用的劇本 PDF 解析結果另存 data/scenarios/，見「劇本庫」）
```

**指令與訊息路由**
- `app/discord_bot.py`：Discord 專用的常駐連線入口，把 Discord 的事件轉譯成呼叫 `app/commands/router.py`
- `app/commands/router.py`：**平台無關**的指令路由入口（取代舊版單一檔案 `app/commands.py`，已重新命名為 `app/legacy_commands.py`）——依關鍵字分派到下面的 handler 模組，自由文字（不是 `/coc` 指令）交給 `app/agents/supervisor.py`
- `app/commands/handlers/`：依領域拆開的指令處理模組——`character.py`（建角／角色卡等 9 個子指令）、`combat.py`、`system.py`（`newgame`／`pdf`／`kp`／`scenario`／`status`／`era`… 等 12 個子指令，含劇本庫的 `/coc scenario` 系列）、`map_handler.py`（`showpage`／`where`／`enter`／`leavemap`）——這些模組委派回 `app/legacy_commands.py` 裡既有、已驗證過的邏輯，不是重新實作
- `app/legacy_commands.py`（原 `app/commands.py`）：PDF 上傳流程、`/coc check`／`/coc luck`（玩家自己在程式碼裡擲骰，結果直接呼叫 `keeper.run_turn` 敘事，不經過下面的 Agentic Keeper 流水線）、預製角色合併、角色離開/回歸等仍集中在這裡的邏輯

**Agentic Keeper：自由文字（角色扮演／遊戲行動）走的多代理流水線**
- `app/agents/supervisor.py`：純 Python 調度器，取代舊版「一次 LLM 呼叫做完所有事」的單一 `keeper.run_turn`；依訊息意圖決定要不要經過完整的機制判定
- `app/agents/context_builder.py`：收集 Scenario RAG／Memory RAG 上下文（純本地 BM25，不呼叫 LLM；Scenario RAG 部分同樣只在 `SCENARIO_RAG_ENABLED=true` 時才跑）
- `app/agents/intent_router.py`：規則式（不呼叫 LLM）分類成 `OOC_ASSISTANT`（KP 助手場外討論）／`PURE_ROLEPLAY`（純角色扮演）／`GAMEPLAY_ACTION`（含機制動作）
- `app/agents/executor.py` + `app/agents/tool_gateway.py`：`GAMEPLAY_ACTION` 才會呼叫，直接複用 `keeper.TOOLS`／`keeper._execute_tool`（真的擲骰、真的落庫，不是重新發明一份工具）
- `app/agents/assistant.py`：`OOC_ASSISTANT`（KP 助手場外討論）整回合委派給既有的 `keeper.run_turn(..., speaker_role="kp_assistant")`，含「KP 觸發正式擲骰會自動升格進正式劇情歷史」的既有規則
- `app/agents/narrator.py`：唯一負責生成最終玩家可見敘事文字的階段，沒有工具存取權限
- `app/agents/rule_validator.py` + `app/agents/guard.py`：正則規則校驗（洩漏系統字眼、Markdown 代碼區塊沒閉合），沒過才觸發一次修復性 LLM 呼叫，Supervisor 用迴圈最多重試 2 次
- `app/agents/state_reducer.py`：純記錄用的流水線節點，不做任何狀態套用（真正的狀態變更在 Executor 呼叫工具時就已經完成並落庫）
- `app/services/prompt_config.py`：Executor／Narrator／Guard 三個真的會呼叫 LLM 的階段共用的提示詞組裝，包在 `keeper._build_static_prompt`／`_build_dynamic_prompt`（既有、持續在維護的內容）外面
- `app/domain/models.py`：流水線內部傳遞用的 `AgentMessage`／`MechanicResult`／`StateDelta` 資料結構

完整設計脈絡、已知落差與踩過的坑，見 **[docs/agentic_keeper_design_spec.md](docs/agentic_keeper_design_spec.md)**。

**核心遊戲邏輯（不分走哪條路由都會用到）**
- `app/keeper.py`：守密人的系統提示詞組裝、工具定義（26 個，擲骰／檢定／戰鬥／角色數值／劇本庫圖片與章節推進等）、工具執行，不綁定特定 LLM
- `app/providers/anthropic_provider.py`：Claude（Anthropic Messages API）介面卡，含 prompt caching
- `app/providers/gemini_provider.py` / `app/providers/openai_provider.py`：Gemini（google-genai SDK）／OpenAI 介面卡
- `app/locks.py`：per-conversation 鎖，防止同一個聊天室的兩則訊息互相覆蓋對方的存檔；KP 助手訊息另有優先權佇列
- `app/combat.py`：正式戰鬥輪次狀態機（先攻順位、回合、HP）
- `app/creation.py`：互動式建角流程（擲屬性、分配職業/興趣技能點數）
- `app/pregen_extractor.py`：從劇本文字中抽取內建的預製調查員
- `app/intent_parser.py`：規則式（regex，不額外呼叫 LLM）偵測玩家訊息裡的移動意圖與「進入某地點」意圖
- `app/scene_map.py`：把劇本平面圖轉成結構化房間圖（節點＋方位邊），並提供「目前位置＋朝向＋方向＋第幾個門 → 目的地房間」的純程式碼解析（不靠 LLM 猜）
- `app/scenario_rag.py`：本地 BM25 式關鍵字檢索（`SCENARIO_RAG_ENABLED=true` 時才啟用），取代「整份劇本塞進 system prompt」，改成 Keeper 用 `search_scenario` 工具按需查詢
- `app/memory_rag.py`：對已裁切掉的舊對話做語意檢索，補足 `campaign_summary` 滾動摘要「越壓越抽象」的細節遺失
- `app/scenario_index.py`：抽取劇本的 NPC／怪物與地點數值索引（`/coc index`，上傳劇本時也會自動跑一次），給 Keeper 一份固定對照表，避免同一隻怪物前後講出不同數值
- `app/scenario_library.py`：可重用的劇本 PDF 庫（`data/scenarios/<劇本ID>/`）——同一份劇本不用每個聊天室各自重新解析一次，支援章節切分與「目前章＋下一章」滑動 Context 視窗、圖片資產搜尋、KP 專用的 `/coc scenario list／use／clean／reparse／cancel`。完整設計見 **[docs/scenario_library_design_spec.md](docs/scenario_library_design_spec.md)**
- `app/dice.py`：COC7e 規則判定（d100、獎懲骰、成功等級、SAN）
- `app/models.py`：角色卡／聊天室狀態資料結構與快速生成（3d6 法）
- `app/pdf_loader.py`：抽取上傳 PDF 的文字內容（文字層改用 MarkItDown + markitdown-ocr 預處理，PyMuPDF 負責頁面轉圖片與備援文字層；圖片偏多的頁面會用 Claude 視覺理解／OCR 備援，並保留這些頁面的實際圖片供之後展示；平面圖頁面會額外呼叫 `app/scene_map.py` 拆出結構化房間圖）；另有低成本的 `extract_preview()`，供劇本庫上傳去重使用
- `app/markitdown_shim.py`：讓 `markitdown-ocr` 插件（原生設計走 OpenAI 介面）改用這個專案既有的 `ANTHROPIC_API_KEY`，不用另外申請 OpenAI 帳號
- `app/db.py`：SQLite 存取層，把每個聊天室的遊戲狀態、角色索引鏡像、Scenario/Memory RAG 的索引快取都存成資料庫裡的一列（取代原本各自的 `data/groups/*.json` 檔案）
- `app/repositories/group_state.py`（原 `app/state.py`）：呼叫 `app/db.py` 保存每個聊天室的遊戲狀態與角色索引鏡像；劇本頁面圖片仍另外存成 PNG 檔案（不進資料庫）

只需要啟動 `app/discord_bot.py`；Discord 直接附加圖片，不需要 webhook、ngrok 或公開圖片網址。

## 文件導覽

這份 README 只放「第一次認識這個專案」需要的東西；其他內容都分別搬到獨立文件，讓每份文件保持專注、方便找：

| 想知道... | 看這份 |
|---|---|
| 怎麼申請 Discord 憑證、設定 `.env`、本機啟動 | **[docs/setup.md](docs/setup.md)** |
| 有哪些指令、怎麼玩、各項機制怎麼操作 | **[docs/gameplay.md](docs/gameplay.md)** |
| 為什麼有這個限制、目前怎麼做的、實測過什麼、之後想擴充要改哪裡（逐功能的開發紀錄） | **[docs/changelog.md](docs/changelog.md)** |
| Keeper 的系統提示詞規則（`_build_static_prompt()` 的可讀版本） | [docs/keeper_skill.md](docs/keeper_skill.md) |
| Agentic Keeper 多代理流水線的完整設計、已知落差與踩過的坑 | **[docs/agentic_keeper_design_spec.md](docs/agentic_keeper_design_spec.md)** |
| 可重用劇本 PDF 庫（章節切分、滑動 Context 視窗、圖片資產、`/coc scenario` 系列指令）的完整設計 | **[docs/scenario_library_design_spec.md](docs/scenario_library_design_spec.md)** |
| HTTP 端點、聊天指令、PDF 上傳生命週期、Provider／劇本庫內部介面的參考手冊 | [docs/API.md](docs/API.md) |
| NPC 隊友設計與資訊流控制的完整版 | [docs/references/gameplay_style.md](docs/references/gameplay_style.md) |
| COC7e 規則 vs. 目前程式碼實作的落差清單 | [docs/references/rules_reference.md](docs/references/rules_reference.md) |
| 攜帶物合理性審查的設計規格與實作狀態 | [docs/references/carry_audit.md](docs/references/carry_audit.md) |
| 跟手動檔案式備團工作流（[coc-kp-host](https://github.com/SumanasJ/coc-kp-host)）的對應表 | [docs/references/prep_persistence.md](docs/references/prep_persistence.md) |
