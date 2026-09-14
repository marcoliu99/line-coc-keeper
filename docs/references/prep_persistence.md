# 持久化與備團筆記：我們的做法 vs. 檔案式備團

[coc-kp-host/references/prep_persistence.md](https://github.com/SumanasJ/coc-kp-host/blob/main/references/prep_persistence.md) 描述的是單人 Claude Code skill 在本機工作目錄手動建一整套資料夾（`00_守秘人资料/`、`01_模组原文/`……）、寫 Markdown 筆記來管理一場戰役。我們是多租戶的群組 Bot，這整套「備團資料夾」的角色由 `app/state.py` 的 `data/groups/*.json` 自動扮演，不需要人工建立或維護檔案結構。這份文件把他們的概念一一對應到我們實際的機制，並列出幾個他們有、我們還沒做的落差。

## 對應表

| coc-kp-host 概念 | 我們的對應機制 |
|---|---|
| `01_模组原文/<scenario>.txt`（抽出的劇本全文） | `GroupState.scenario_text`，上傳 PDF 時由 `app/pdf_loader.py` 自動抽取寫入 |
| `00_守秘人资料/模组框架.md`（KP 專屬地點/NPC/線索索引） | 沒有獨立索引檔——Keeper 每次都重新讀 `scenario_text`（配 prompt caching，見下）；`SCENARIO_RAG_ENABLED=true` 時改成 `app/scenario_rag.py` 的 BM25 索引，按查詢取回相關頁面 |
| `02_玩家资料/handouts/` + 索引 | `data/groups/<id>_images/page_<n>.png`，由 `app/state.py` 的 `save_page_image`/`load_page_image` 管理；「索引」就是 `GroupState.scene_maps`（地圖類）和低文字頁清單，不是人工維護的 md |
| `03_角色卡/调查员_<name>.md` | `GroupState.characters[owner_id]`（`Character` dataclass），`/coc sheet` 隨時可查，不需要另外開檔案；`app/state.py` 的 `save_state` 額外會同步寫一份 `data/groups/characters/<Discord user id>.json`（純 GM 自己 `cat`/`jq` 用，Bot 本身不讀），算是離這個概念最近的東西 |
| `03_角色卡/NPC队友_<name>.md` | **沒有對應機制**——見下方「落差」 |
| `04_跑团记录/session_log.md`（策展過的重點摘要） | `GroupState.log`，是**完整逐句對話紀錄**（`role`/`content`），不是策展摘要，受 `MAX_LOG_TURNS` 限制會被裁切——見下方「落差」 |
| `05_规则与流程/车卡与跑团格式.md` | `app/models.py` 的 `OCCUPATIONS`、`app/creation.py` 的建角流程本身就是「格式」，不需要另外寫文件描述 |
| `05_规则与流程/文风参考.md`（劇本原文的文風範例） | 沒有獨立抽取——Keeper 讀 `scenario_text` 原文時本來就看得到劇本自己的文字，`docs/keeper_skill.md` 的「文風」一節是我們自己寫的通用規則，不是逐劇本抽取的 |
| Git 版控備團資料夾 | 不適用——`data/groups/` 是執行期產生的使用者資料，`.gitignore` 排除，不進版控（跟 `.env` 一樣的道理） |

## 我們的「search-before-scene」規則

他們的文件強調：玩家選了某個地點/NPC/線索時，要先搜尋抽出的劇本文字檔再敘述，不要憑記憶。我們的對應：

- **預設模式**（`SCENARIO_RAG_ENABLED=false`，目前預設）：整份 `scenario_text` 就在 Keeper 的 system prompt 裡（配 Anthropic prompt caching，重複讀不重複計費），不需要主動搜尋——Keeper 本來就看得到全文，這個規則某種程度上不適用。
- **RAG 模式**（`SCENARIO_RAG_ENABLED=true`）：這時候才真的需要「search-before-scene」——`docs/keeper_skill.md` 已經寫了「需要任何劇本細節都要呼叫 `search_scenario` 查詢，不要憑空想像」，跟他們的規則精神一致，只是我們用工具呼叫達成，不是讀本機檔案。

## NPC 隊友資料結構（落差）

coc-kp-host 對每個 NPC 隊友都有一張完整卡片（背景、屬性、技能、思想信念、加入理由）存在 `03_角色卡/NPC队友_<name>.md`，跨場景保留。我們完全沒有對應的資料結構——`app/models.py` 沒有 `NPCTeammate` 這種東西，`Combatant.is_ally` 只在戰鬥的先攻順位裡存在，戰鬥外的 NPC 隊友完全由 Keeper 在敘事裡自己記住並扮演，沒有結構化資料可以查詢。

如果之後要補：可以參考他們的卡片模板（[原文見這裡](https://github.com/SumanasJ/coc-kp-host/blob/main/references/prep_persistence.md#npc-teammate-card-template)），簡化成一個 `NPCTeammate` dataclass（姓名、職業、加入理由、技能字典），存進 `GroupState.npc_teammates: dict[str, NPCTeammate]`，`/coc npc <名字>` 指令查看。目前沒有做，因為還沒有真實使用場景證明值得做。

## 逐句紀錄 vs. 策展摘要（已修正）

`GroupState.log` 是逐句對話紀錄（跟他們的「verbatim transcript」概念一樣）。原本**沒有**對應他們「session_log.md」那種策展過的重點摘要，`MAX_LOG_TURNS` 是硬上限，超過就直接裁掉最舊的對話，長戰役玩到後期 Keeper 會忘記早期的劇情細節。

**已修正（滾動式摘要／Rolling Summarization）**：新增 `GroupState.campaign_summary: str` 欄位，呼應他們的 session_log.md 概念。`app/keeper.py` 的 `run_turn` 每次要裁切 `state.log`（超過 `MAX_LOG_TURNS*4`）之前，先呼叫 `summarize_log_chunk(current_summary, dropped_chunk)` 把即將被丟棄的那段對話跟現有摘要融合更新，才真的裁掉——不是每一輪都跑，只有真的觸發裁切的那一輪才會多付一次（用輕量的強制工具呼叫，透過 `LLM_PROVIDER` 走，不是寫死某個供應商的 client）。更新後的 `campaign_summary` 放進 `_build_static_prompt`（有 prompt caching 的區塊），跟角色的靜態屬性/技能同一批。摘要函式本身完全防呆：沒設定 `LLM_PROVIDER`、呼叫失敗、或沒回傳可用內容，一律原樣回傳舊摘要，不會讓遊戲進程中斷或摘要消失。

**實測驗證過**（模擬真實使用情境）：手動塞進一段提到 NPC「Gardiner」和「一把生鏽鑰匙」的對話，後面接 80 輪填充閒聊直到超過裁切門檻，觸發下一輪 `run_turn` 後確認：(1) `state.log` 正確從 322 筆裁到 160 筆；(2) `campaign_summary` 正確保留了 Gardiner、鑰匙、地下室警告這些關鍵事實；(3) 直接問 Keeper「剛才 Gardiner 給的鑰匙是要開哪裡的」，即使原始那幾句對話已經不在 `state.log` 裡，Keeper 依然透過摘要正確回答出燈塔、Gardiner、鑰匙的關聯——完全對應「詢問第 2 輪的 NPC 名字或道具，AI 仍能從摘要正確提取」這個驗證標準。

## Keeper 專屬地點/NPC 索引（落差，優先度較低）

他們每場戰役會先建一份「模組框架.md」（地點索引、NPC 索引、時間線、回原文檢索關鍵詞），當作 Keeper 自己的導覽。我們預設模式下不需要——`scenario_text` 全文就在眼前，Keeper 自己讀就好；只有在真的很長的劇本、或開了 Scenario RAG 之後，才會受益於一份預先整理好的索引（讓 `search_scenario` 查詢更容易對到正確關鍵詞）。如果之後 Scenario RAG 被更多人用、抽取準確度不夠，可以考慮在 `/coc pregens` 之外再加一個「抽取地點/NPC 索引」的功能，思路可以照抄他們的「模组框架.md」格式。
