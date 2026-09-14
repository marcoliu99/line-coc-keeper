# COC7e 守密人 Bot（LINE / Discord）

在 LINE 群組或 Discord 頻道裡上傳一份《克蘇魯的呼喚》第七版（COC7e）劇本 PDF，就能讓 LLM 扮演守密人（Keeper），直接在聊天室裡跑團。規則判定（技能檢定、SAN 值、擲骰）由程式碼負責計算，LLM 負責讀劇本、敘事、決定什麼時候該擲骰。後端 LLM 可以在 Claude（Anthropic）、Gemini（Google）、OpenAI 之間切換，見 [docs/setup.md](docs/setup.md) 的「切換 LLM 供應商」段落；前端聊天平台可以在 LINE 和 Discord 之間切換（甚至兩個同時開）。

## 快速開始

### 還沒把 Bot 架起來？

完整安裝教學（申請 LINE/Discord 憑證、設定 `.env`、本機啟動）都搬到 **[docs/setup.md](docs/setup.md)**，跟著那份文件從頭做一次即可。

### Bot 已經跑起來、已經加進群組/伺服器？

1. **上傳劇本**：把 COC7e 劇本 PDF 檔案直接傳到群組/頻道裡。圖片較多的劇本要等一下（會先回「處理中」，實際結果晚一點才會出現）。
2. **建立角色**（任一位玩家都要做這步）：
   ```
   /coc pc 角色名 職業
   ```
   例如 `/coc pc 陳月 記者`。想用更符合規則的建角流程或劇本內建的預製角色，見 **[docs/gameplay.md](docs/gameplay.md)** 的完整說明。
3. **開始玩**：角色建好之後，直接在群組/頻道裡打字描述你的角色要做什麼（不用加任何指令），守密人就會接手敘事、要求擲骰、更新 HP/SAN。
4. 隨時可以 `/coc help` 看完整指令列表、`/coc status` 看目前進度、`/coc sheet` 看自己的角色卡。完整指令與玩法細節見 **[docs/gameplay.md](docs/gameplay.md)**。

## 架構

```
LINE 群組 ──(webhook)──▶ app/main.py ────────┐
                                              │
Discord 頻道 ──(gateway)──▶ app/discord_bot.py ┤
                                              ▼
                              app/commands.py（指令與遊戲邏輯，平台無關）
                                              │
                              app/intent_parser.py + app/scene_map.py（Map/Scene Engine，
                              規則式偵測移動意圖，呼叫 LLM 前先用房間圖算出目的地房間）
                                              │
                                              ▼
                                      app/keeper.py（守密人邏輯，LLM 供應商無關）
                                              │
                              app/scenario_rag.py（Scenario RAG，SCENARIO_RAG_ENABLED=true
                              時才啟用，本地 BM25 檢索取代整份劇本塞進 prompt）
                                              │
                                              ▼
                              app/providers/anthropic_provider.py
                              app/providers/gemini_provider.py      ← LLM_PROVIDER 決定走哪一條
                                              │
                                              ▼
                        工具呼叫（擲骰／技能檢定／SAN／角色數值／戰鬥）
                                              │
                                              ▼
                 data/coc_bot.db （每個聊天室的角色卡、劇本、對話紀錄；頁面圖片另存 data/groups/）
```

- `app/main.py`：LINE 專用 webhook 入口，把 LINE 的事件轉譯成呼叫 `app/commands.py`
- `app/discord_bot.py`：Discord 專用的常駐連線入口，把 Discord 的事件轉譯成呼叫 `app/commands.py`
- `app/commands.py`：**平台無關**的指令解析與遊戲流程（`/coc`、`/roll`、PDF 上傳、自由文字轉守密人），兩個前端共用同一份
- `app/keeper.py`：守密人的遊戲邏輯（系統提示詞、工具定義、工具執行），不綁定特定 LLM
- `app/providers/anthropic_provider.py`：Claude（Anthropic Messages API）介面卡，含 prompt caching
- `app/providers/gemini_provider.py`：Gemini（google-genai SDK）介面卡
- `app/locks.py`：per-conversation 鎖，防止同一個聊天室的兩則訊息互相覆蓋對方的存檔
- `app/combat.py`：正式戰鬥輪次狀態機（先攻順位、回合、HP）
- `app/creation.py`：互動式建角流程（擲屬性、分配職業/興趣技能點數）
- `app/pregen_extractor.py`：從劇本文字中抽取內建的預製調查員
- `app/intent_parser.py`：規則式（regex，不額外呼叫 LLM）偵測玩家訊息裡的移動意圖與「進入某地點」意圖
- `app/scene_map.py`：把劇本平面圖轉成結構化房間圖（節點＋方位邊），並提供「目前位置＋朝向＋方向＋第幾個門 → 目的地房間」的純程式碼解析（不靠 LLM 猜）
- `app/scenario_rag.py`：本地 BM25 式關鍵字檢索（`SCENARIO_RAG_ENABLED=true` 時才啟用），取代「整份劇本塞進 system prompt」，改成 Keeper 用 `search_scenario` 工具按需查詢
- `app/scenario_index.py`：抽取劇本的 NPC／怪物與地點數值索引（`/coc index`，上傳劇本時也會自動跑一次），給 Keeper 一份固定對照表，避免同一隻怪物前後講出不同數值
- `app/dice.py`：COC7e 規則判定（d100、獎懲骰、成功等級、SAN）
- `app/models.py`：角色卡／聊天室狀態資料結構與快速生成（3d6 法）
- `app/pdf_loader.py`：抽取上傳 PDF 的文字內容（文字層改用 MarkItDown + markitdown-ocr 預處理，PyMuPDF 負責頁面轉圖片與備援文字層；圖片偏多的頁面會用 Claude 視覺理解／OCR 備援，並保留這些頁面的實際圖片供之後展示；平面圖頁面會額外呼叫 `app/scene_map.py` 拆出結構化房間圖）
- `app/markitdown_shim.py`：讓 `markitdown-ocr` 插件（原生設計走 OpenAI 介面）改用這個專案既有的 `ANTHROPIC_API_KEY`，不用另外申請 OpenAI 帳號
- `app/db.py`：SQLite 存取層，把每個聊天室的遊戲狀態、角色索引鏡像、Scenario/Memory RAG 的索引快取都存成資料庫裡的一列（取代原本各自的 `data/groups/*.json` 檔案）
- `app/state.py`：呼叫 `app/db.py` 保存每個聊天室的遊戲狀態與角色索引鏡像；劇本頁面圖片仍另外存成 PNG 檔案（不進資料庫）

想只用 LINE、只用 Discord、還是兩個都開，完全取決於你要不要啟動哪個入口（`app/main.py` 用 `uvicorn` 跑、`app/discord_bot.py` 直接 `python -m` 跑），兩者可以同時執行，互不影響，因為狀態檔案已經照平台分開命名。

## 文件導覽

這份 README 只放「第一次認識這個專案」需要的東西；其他內容都分別搬到獨立文件，讓每份文件保持專注、方便找：

| 想知道... | 看這份 |
|---|---|
| 怎麼申請 LINE/Discord 憑證、設定 `.env`、本機啟動 | **[docs/setup.md](docs/setup.md)** |
| 有哪些指令、怎麼玩、各項機制怎麼操作 | **[docs/gameplay.md](docs/gameplay.md)** |
| 為什麼有這個限制、目前怎麼做的、實測過什麼、之後想擴充要改哪裡（逐功能的開發紀錄） | **[docs/changelog.md](docs/changelog.md)** |
| Keeper 的系統提示詞規則（`_build_static_prompt()` 的可讀版本） | [docs/keeper_skill.md](docs/keeper_skill.md) |
| NPC 隊友設計與資訊流控制的完整版 | [docs/references/gameplay_style.md](docs/references/gameplay_style.md) |
| COC7e 規則 vs. 目前程式碼實作的落差清單 | [docs/references/rules_reference.md](docs/references/rules_reference.md) |
| 攜帶物合理性審查的設計規格與實作狀態 | [docs/references/carry_audit.md](docs/references/carry_audit.md) |
| 跟手動檔案式備團工作流（[coc-kp-host](https://github.com/SumanasJ/coc-kp-host)）的對應表 | [docs/references/prep_persistence.md](docs/references/prep_persistence.md) |
