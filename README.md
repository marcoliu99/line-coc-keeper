# COC7e 守密人 Bot（LINE / Discord）

在 LINE 群組或 Discord 頻道裡上傳一份《克蘇魯的呼喚》第七版（COC7e）劇本 PDF，就能讓 LLM 扮演守密人（Keeper），直接在聊天室裡跑團。規則判定（技能檢定、SAN 值、擲骰）由程式碼負責計算，LLM 負責讀劇本、敘事、決定什麼時候該擲骰。後端 LLM 可以在 Claude（Anthropic）和 Gemini（Google）之間切換，見下面「切換 LLM 供應商」段落；前端聊天平台可以在 LINE 和 Discord 之間切換（甚至兩個同時開），見「切換／同時使用聊天平台」段落。

## 快速開始（Bot 已經跑起來、已經加進群組/伺服器的前提下）

1. **上傳劇本**：把 COC7e 劇本 PDF 檔案直接傳到群組/頻道裡。圖片較多的劇本要等一下（會先回「處理中」，實際結果晚一點才會出現）。
2. **建立角色**（任一位玩家都要做這步）：
   ```
   /coc pc 角色名 職業
   ```
   例如 `/coc pc 陳月 記者`。想用更符合規則的建角流程或劇本內建的預製角色，見下面「玩法」段落的完整說明。
3. **開始玩**：角色建好之後，直接在群組/頻道裡打字描述你的角色要做什麼（不用加任何指令），守密人就會接手敘事、要求擲骰、更新 HP/SAN。
4. 隨時可以 `/coc help` 看完整指令列表、`/coc status` 看目前進度、`/coc sheet` 看自己的角色卡。

其餘章節是給還沒把 Bot 架起來的人看的完整設定教學（申請 LINE/Discord 憑證、部署伺服器等）。

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
                   data/groups/*.json （每個聊天室的角色卡、劇本、對話紀錄）
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
- `app/dice.py`：COC7e 規則判定（d100、獎懲骰、成功等級、SAN）
- `app/models.py`：角色卡／聊天室狀態資料結構與快速生成（3d6 法）
- `app/pdf_loader.py`：抽取上傳 PDF 的文字內容（文字層改用 MarkItDown + markitdown-ocr 預處理，PyMuPDF 負責頁面轉圖片與備援文字層；圖片偏多的頁面會用 Claude 視覺理解／OCR 備援，並保留這些頁面的實際圖片供之後展示；平面圖頁面會額外呼叫 `app/scene_map.py` 拆出結構化房間圖）
- `app/markitdown_shim.py`：讓 `markitdown-ocr` 插件（原生設計走 OpenAI 介面）改用這個專案既有的 `ANTHROPIC_API_KEY`，不用另外申請 OpenAI 帳號
- `app/state.py`：以 JSON 檔案保存每個聊天室的遊戲狀態（檔名依平台加前綴，例如 `line-group-xxx.json`、`discord-channel-xxx.json`，避免兩邊 ID 撞在一起），劇本頁面圖片另外存成 PNG 檔案

想只用 LINE、只用 Discord、還是兩個都開，完全取決於你要不要啟動哪個入口（`app/main.py` 用 `uvicorn` 跑、`app/discord_bot.py` 直接 `python -m` 跑），兩者可以同時執行，互不影響，因為狀態檔案已經照平台分開命名。

### 設計文件（`docs/`）

`app/keeper.py` 的系統提示詞是一大段塞在 Python f-string 裡的繁體中文，改起來要在程式碼裡找對位置；`docs/` 底下是同樣規則的可讀版本，之後要調整 Keeper 行為時建議先看這裡：

- `docs/keeper_skill.md`：完整行為規格（敘事節奏、文風、檢定流程、NPC 隊友、地圖引擎整合……），對應 `_build_static_prompt()` 實際的內容
- `docs/references/gameplay_style.md`：NPC 隊友設計與資訊流控制的完整版（`keeper_skill.md` 裡的只是精簡版）
- `docs/references/rules_reference.md`：COC7e 規則 vs. 目前程式碼實作的落差清單（對抗檢定、組合技能檢定、重傷判定、瘋狂發作表……哪些做了、哪些沒做，之後要擴充規則引擎的施工清單）
- `docs/references/carry_audit.md`：攜帶物合理性審查的設計規格（**目前完全沒實作**，純備忘）
- `docs/references/prep_persistence.md`：跟參考的 [coc-kp-host](https://github.com/SumanasJ/coc-kp-host) 那種手動檔案式備團工作流的對應表，列出我們用 `data/groups/*.json` 自動化掉了哪些事、還缺哪些（NPC 隊友結構化資料、長戰役的劇情摘要）

## 第一步 A：申請 LINE Messaging API Channel（只想用 Discord 的話可以跳過這整節）

這一步是在 LINE 官方後台建立一個「機器人身分」，之後你的程式才有東西可以連。整個過程都在網頁上點一點，不需要寫程式，大約 10 分鐘。

> LINE 的計費規則是「Reply（回覆）免費不限量，只有 Push（主動推播）才計費」，這個 Bot 完全只用 Reply API，正常使用不會產生 LINE 訊息費用。

> **2024 年 9 月後的政策改動**：LINE 已經不能再直接在 Developers Console 裡建立 Messaging API Channel，改成要先建立一個「LINE 官方帳號（LINE Official Account）」，再從官方帳號後台開通 Messaging API——開通的當下會自動在 Developers Console 生出對應的 Channel，之後（步驟 4 開始）的操作都還是在 Developers Console 裡完成。以下是更新後的正確流程：

### 1. 建立 LINE 官方帳號

- 前往 [https://developers.line.biz/console/](https://developers.line.biz/console/)，用你平常用的 **LINE 帳號**登入（跟你手機上的 LINE App 是同一組帳號）。
- 畫面上會有 **Create a LINE Official Account** 按鈕，點下去。
- 用手機門號做簡訊驗證。
- 填官方帳號的基本資料（帳號名稱、行業別等），內容隨便填合理的即可，不影響 API 功能——帳號名稱可以直接用你想要的 Bot 名稱，例如 `COC守密人`。

### 2. 從 LINE Official Account Manager 開通 Messaging API

- 建立完成後會導去（或請你自己去）[LINE Official Account Manager](https://manager.line.biz/)，用同一組帳號登入。
- 畫面右上角找 **設定 / Settings**。
- 左側選單找 **Messaging API**。
- 點 **啟用 Messaging API / Enable Messaging API**。
- Provider 選擇 **新增 Provider / New provider**，輸入一個名稱（個人用途隨便取，例如你的名字或 `我的跑團`）。
- 同意條款、按確認，Messaging API 就開通了——這一步會自動在 Developers Console 建立對應的 Channel。

### 3. 回到 LINE Developers Console

- 重新整理或回到 [https://developers.line.biz/console/](https://developers.line.biz/console/)，應該就能看到剛剛自動建立好的 Provider 和 Channel 了，點進去這個 Channel。
- 接下來（步驟 4 開始）跟原本的流程一樣，都是在這個 Channel 的管理頁面裡操作。

> 上面官方帳號後台的確切按鈕文字，可能因為帳號語言設定顯示中文或英文而略有不同，跟著畫面上意思相近的選項點就好；如果卡在某一步找不到對應按鈕，把畫面截圖或文字描述貼給我，我可以幫你確認。

### 4. 拿到 Channel secret 和 Channel access token

進到 Channel 頁面後，上方會有幾個分頁（**Basic settings** / **Messaging API** / **LIFF** 等）。

- 先看 **Basic settings** 分頁：
  - 往下捲會看到 **Channel secret**，右邊有個「Show」或眼睛圖示，點開後複製這串文字。這個要填進 `.env` 的 `LINE_CHANNEL_SECRET`。
- 切到 **Messaging API** 分頁：
  - 往下捲找到 **Channel access token (long-lived)** 區塊，按 **Issue** 按鈕，會立刻產生一串很長的 token。
  - **馬上複製下來**（有些版本的介面之後就看不到完整內容了，只能重新 Issue 一次讓舊的失效）。這個要填進 `.env` 的 `LINE_CHANNEL_ACCESS_TOKEN`。

> 這兩串東西等同於這個機器人帳號的密碼，只能貼進 `.env` 檔案，絕對不要貼到聊天室、GitHub 公開 repo、或任何截圖裡。

### 5. 設定 Webhook 與自動回覆

還在 **Messaging API** 分頁裡往下捲：

- **Webhook URL**：這欄現在先留空，等你之後啟動 ngrok 拿到網址後，再回來這裡貼上（見「第三步」）。
- 找到 **Use webhook** 開關，切成 **Enabled**（開啟）。這樣 LINE 才會把訊息轉發到你的伺服器，而不是自己內建處理。
- 找到 **Auto-reply messages** 和 **Greeting messages** 這兩列，右邊通常會有一個「Edit」連結，點下去會跳到另一個網站叫 **LINE Official Account Manager**（這是 LINE 官方帳號的另一個管理後台，跟 Developers Console 是分開的兩個系統，但用同一組帳號登入即可進去）。在那邊把「Auto-reply messages（自動回應訊息）」和「Greeting messages（加入好友歡迎訊息）」都切成關閉。
  - 為什麼要關：這兩個是 LINE 官方帳號內建的罐頭回覆功能，如果開著，玩家傳訊息時可能會同時收到 LINE 內建的罐頭回覆，又收到你程式的回覆，變成兩個聲音在搶答。
- 繼續往下捲，找到 **Allow bot to join group chats**，切成 **Enabled**。這個一定要開，否則之後想把 Bot 拉進 LINE 群組時會直接失敗。
- 同一區塊還會看到 **Webhook redelivery** 和 **Error statistics aggregation** 兩個選項：
  - **Webhook redelivery（重新傳送）建議先關掉**：這個專案沒有做事件去重複，如果 LINE 因為你的伺服器一時沒回應而重送同一個事件，同一句話可能會被守密人處理兩次（重複擲骰、角色數值扣兩次）；測試階段常常重開伺服器，更容易踩到。
  - **Error statistics aggregation（錯誤統計）可以開著**，純粹是讓你在 Console 看得到 webhook 錯誤率，沒有副作用。

### 6. 把 Bot 加好友並拉進群組

- 還是在 **Messaging API** 分頁，畫面上方會有一個 QR Code（有時候在 Basic settings 分頁也看得到，標示為 **Bot info** 或 **QR code**）。
- 用手機 LINE App 的「加好友」功能掃描這個 QR Code，把 Bot 加為好友（加好友這步驟是必要的，LINE 的機制上機器人一定要先被加好友才能被拉進群組）。
- 打開你要跑團的 LINE 群組 → 點右上角的群組設定（人形圖示或選單）→ 找「邀請」→ 從好友清單裡選到剛剛加的 Bot → 邀請它加入群組。

> **如果群組裡傳訊息完全沒反應，但跟 Bot 的一對一聊天正常**：很可能是「先拉進群組、後開啟 Allow bot to join group chats」的時間差造成的，權限沒有套用到已經存在的群組成員關係上。把 Bot 從群組移除，重新邀請一次即可。

到這裡，LINE 這邊的設定就完成了，剩下的 Webhook URL 要等你把伺服器跑起來、透過 ngrok 拿到對外網址之後再回來補。

## 第一步 B：建立 Discord Bot（只想用 LINE 的話可以跳過這整節）

Discord 這邊比 LINE 簡單很多：不需要 webhook、不需要 ngrok，Bot 用一條常駐連線直接跟 Discord 對接。

### 1. 建立 Discord Application 和 Bot

1. 前往 [https://discord.com/developers/applications](https://discord.com/developers/applications)，用你的 Discord 帳號登入。
2. 點 **New Application**，取個名字（例如 `COC守密人`），建立。
3. 左側選單點 **Bot**，如果還沒有 Bot 身分會提示你建立，按下去。
4. 在同一頁找到 **Privileged Gateway Intents** 區塊，把 **Message Content Intent** 打開並存檔——這個一定要開，不然 Bot 收到的訊息內容永遠是空的，完全沒辦法判斷指令。
5. 在 **Token** 區塊按 **Reset Token**（第一次是 **Copy**），複製這串 token，填進 `.env` 的 `DISCORD_BOT_TOKEN`。這串等同密碼，一樣不要外流。

### 2. 把 Bot 邀請進你的伺服器

1. 左側選單點 **OAuth2** → **URL Generator**。
2. **Scopes** 勾選 **bot**。
3. **Integration Type** 選 **Guild Install**（不是 User Install——Bot 一定要以伺服器成員的身分才能用常駐連線讀取一般訊息）。
4. 下面出現的 **Bot Permissions** 至少勾選 **View Channel**（檢視頻道）、**Send Messages**（傳送訊息）、**Read Message History**（讀取訊息紀錄）、**Attach Files**（附加檔案——`/coc showpage` 和守密人主動秀劇本圖片給玩家看時需要，Discord 傳圖片是直接附加檔案，不像 LINE 需要額外的公開網址）。
5. 頁面最下面會生成一個邀請連結，複製起來，用瀏覽器打開，選擇要加入的伺服器、授權完成。

如果 Bot 已經加入伺服器後才想到要多勾 **Attach Files**，不用重新邀請，直接去伺服器設定 → 身分組 → 找到 Bot 的身分組 → 打開 **Attach Files** 權限存檔即可。

到這裡 Discord 那邊就設定完了，不需要再回來設定任何 Webhook URL。

## 第二步：設定專案

```bash
cd line-coc-keeper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 編輯 .env，填入：
#   LINE_CHANNEL_SECRET=...
#   LINE_CHANNEL_ACCESS_TOKEN=...
#   LLM_PROVIDER=anthropic   ← 或 gemini，見下面「切換 LLM 供應商」
#   ANTHROPIC_API_KEY=...   （去 https://console.anthropic.com 申請，跟你平常用的 Claude Code 登入是分開的）
```

### 切換 LLM 供應商：Claude、Gemini 或 OpenAI

`.env` 裡的 `LLM_PROVIDER` 決定守密人由誰扮演，三邊共用同一套遊戲邏輯（工具定義、規則判定、狀態管理都在 `app/keeper.py`），只有 `app/providers/` 底下各自的介面卡不一樣，隨時可以改 `.env` 切換，不用動到程式碼。

- **`LLM_PROVIDER=anthropic`**：用 Claude，走 `app/providers/anthropic_provider.py`，需要 `ANTHROPIC_API_KEY`。有做 prompt caching（見下面「已知限制」的說明），劇本內容重複的部分幾乎不會重複計費。
- **`LLM_PROVIDER=gemini`**：用 Google Gemini，走 `app/providers/gemini_provider.py`，需要 `GEMINI_API_KEY`（去 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 申請）。Gemini Flash 系列通常比 Claude 便宜不少，如果主要考量是成本，這是值得一試的選項。
  - **請注意**：這條路徑是照 Google 官方 `google-genai` SDK 文件寫的（手動 function-calling 迴圈：`client.models.generate_content` + `FunctionDeclaration`/`Tool`），程式碼結構跟工具 schema 都用真的 SDK 型別測過可以正常建構，但因為手上沒有 Gemini API key，**沒有實際打過一次真的 API 呼叫**驗證端到端行為。Google 的 Gen AI SDK 這一兩年變動蠻快的，如果切過去發現守密人完全沒反應或行為怪怪的，先去對一下 `app/providers/gemini_provider.py` 裡用到的屬性名稱（`response.function_calls`、`response.text`、`Part.from_function_response` 等）跟當時最新的 SDK 文件是否還一致，再懷疑是遊戲邏輯本身的問題。
  - Gemini 目前沒有做 prompt caching（Google 那邊叫 context caching，跟 Anthropic 的做法不同、而且門檻可能要幾萬 token 起跳），劇本內容會整包重新送——如果之後真的固定用 Gemini，這是下一個值得補的優化。
  - `GEMINI_MODEL` 預設值請自己去 [ai.google.dev](https://ai.google.dev) 核對當下實際可用的 flash 模型名稱再決定要不要改，模型 id 會隨時間變動。
- **`LLM_PROVIDER=openai`**（預設）：用 ChatGPT（`gpt-5.6-luna`），走 `app/providers/openai_provider.py`，需要 `OPENAI_API_KEY`（[platform.openai.com/api-keys](https://platform.openai.com/api-keys) 申請，注意這跟 ChatGPT 網頁版的登入帳號是同一組帳號，但金鑰是分開申請的，格式是 `sk-` 開頭的一長串字元）。走的是 **Responses API**（`client.responses.create(instructions=..., input=..., tools=[...])`），不是舊的 Chat Completions API——OpenAI 官方最新的 Python 函式庫文件（[developers.openai.com/api/docs/libraries](https://developers.openai.com/api/docs/libraries?language=python)，使用者提供的連結）建議新串接都改用這個，所以沒有照抄 Anthropic/Gemini 那兩條路徑的舊介面寫法。工具定義的欄位形狀（`{"type": "function", "name", "description", "parameters"}`，不像 Chat Completions 那樣包一層 `"function"`）跟回傳的 `function_call`/`function_call_output` 項目欄位都是直接讀已安裝的 `openai` SDK 原始碼型別定義確認過的，不是憑印象寫的。**這是目前唯一實際跑過真實 API 驗證過的路徑**（包含完整 `keeper.run_turn` 跑過一次真實劇本場景，確認 `skill_check` 正確請求檢定而不是自己編結果）——`OPENAI_MODEL=gpt-5.6-luna` 也已經拿真的金鑰確認過是有效、可用的模型 id，不是猜測值。目前正式在跑的 `.env` 就是設定成 `openai`；`.env.example` 的預設值仍是 `anthropic`（比較久、比較穩的路徑，給新架設的人一個保守的起點）。
  - 目前沒有做 prompt caching（OpenAI 也有對應的自動 prompt caching 機制，理論上長 system prompt 重複的部分會自動命中降價，但這條路徑還沒特別驗證過實際命中率跟 Anthropic 的手動 `cache_control` 比起來如何）。
  - `app/markitdown_shim.py`（見下面「圖文混排的頁面」那條）在偵測到 `OPENAI_API_KEY` 時，會自動改用真正的 `openai.OpenAI()` client 餵給 `markitdown-ocr`，不再透過 Anthropic 轉接殼——`markitdown-ocr` 原生就是設計給 OpenAI 用的，兩邊共用同一把金鑰之後就不需要那層轉接了；沒有設定 `OPENAI_API_KEY` 時才會退回 Anthropic 轉接殼。

### （可選）開啟 Scenario RAG

`.env` 裡的 `SCENARIO_RAG_ENABLED=true` 可以把「整份劇本塞進 system prompt」改成「Keeper 用 `search_scenario` 工具按需檢索」，不需要另外申請任何金鑰（本地 BM25 關鍵字比對，不是語意檢索）。預設是 `false`（關閉），一般長度的劇本建議保持關閉；細節、取捨、什麼時候該開，見下面「已知限制」的「Scenario RAG 是可選功能」那條。

### （建議）安裝 OCR，讓圖片化的手卡/地圖也能被讀到

很多 COC7e 劇本會把手卡、地圖、印章之類的東西直接做成圖片放進 PDF，而不是可選取的文字。程式在抽取 PDF 時，如果某一頁文字很少但又有圖片，會自動嘗試用 OCR 辨識那張圖片裡的文字；沒裝 OCR 也不會壞掉，只是那幾頁會抽不出文字，Bot 上傳完成後會提醒你哪幾頁需要人工核對。

macOS 安裝方式：

```bash
brew install tesseract tesseract-lang   # tesseract-lang 才有繁體中文語言包
```

不裝也可以正常使用，純文字排版的劇本不受影響。

## 第三步：本機啟動

兩個前端是完全獨立的兩個程式進場，跑其中一個、兩個都跑，或都不跑，各自獨立。

### LINE：啟動 FastAPI 伺服器 + ngrok 對外

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

另開一個終端機視窗，啟動 ngrok（沒裝的話先 `brew install ngrok`，並用 `ngrok config add-authtoken <你的token>` 綁定帳號）：

```bash
ngrok http 8000
```

ngrok 會給你一個類似 `https://xxxx.ngrok-free.app` 的網址。回到 LINE Developers Console 的 Messaging API 分頁，把 **Webhook URL** 設為：

```
https://xxxx.ngrok-free.app/callback
```

按 **Verify**，應該會顯示成功（此時伺服器要正在跑）。

**如果想用 `/coc showpage` 或讓守密人主動秀劇本圖片給玩家看**，還要多一步：把 `.env` 的 `PUBLIC_BASE_URL` 設成同一個 ngrok 網址（不要加 `/callback`，也不要有結尾斜線），例如 `PUBLIC_BASE_URL=https://xxxx.ngrok-free.app`，改完要重啟伺服器才會生效。這個功能對 Discord 是選用的（Discord 直接附加圖片檔案，不需要這個設定），但**對 LINE 是必要的**，因為 LINE 的圖片訊息不能夾帶檔案本體，只能給一個網址讓 LINE 自己去抓圖，`PUBLIC_BASE_URL` 就是用來讓 LINE 抓到我們伺服器上存的劇本圖片。

> ngrok 免費版每次重啟網址都會變，記得每次都要回 LINE Developers Console 更新 Webhook URL，**如果有設定 `PUBLIC_BASE_URL` 也要一起更新**（不然圖片功能會找不到路，`/coc showpage` 或守密人秀圖時會失敗）。之後若要長期使用，建議换成正式主機（Render / Railway / Fly.io / 自己的雲端主機），屆時只要把 uvicorn 換成常駐服務、Webhook URL 和 `PUBLIC_BASE_URL` 都換成正式網域即可，程式碼不用改。

### Discord：直接啟動常駐連線

不需要 ngrok，也不需要任何對外網址：

```bash
source .venv/bin/activate
python -m app.discord_bot
```

看到終端機印出「Discord bot 已上線：...」就代表連上了，直接去伺服器頻道打字測試即可。這個程式會一直佔用終端機、持續連線，測試完想關掉就 `Ctrl+C`；長期使用一樣建議換成正式主機常駐執行（跟 LINE 那邊一樣，程式碼不用改，只是部署方式換掉，也不需要 webhook URL 那一段）。

## 玩法

1. 把準備好的 COC7e 劇本 PDF 檔案直接傳到 LINE 群組裡。Bot 會自動讀取內容、標記為目前劇本；就算是圖文混排的版面（地圖、手卡、插圖跟內文穿插在一起）也沒問題，文字部分照樣抽得出來，圖片偏多、文字很少的頁面還會嘗試自動 OCR。上傳完成後如果有頁面文字明顯偏少，Bot 會提醒你是哪幾頁，建議人工核對一下。
2. 每位玩家建立角色，三選一：
   - **快速生成**（一鍵完成，適合隨興開局）：
     ```
     /coc pc 角色名 職業
     ```
     例如 `/coc pc 陳月 記者`。職業可選：記者、私家偵探、醫生、教授、警察、骨董商、神職人員、流浪漢（不填就是自由人，沒有職業加成）。屬性以經典 3d6 法隨機產生，技能直接套用職業加值表。
   - **互動式建角**（符合官方流程，自己決定技能點怎麼分配）：
     ```
     /coc create 角色名 職業
     ```
     Bot 會擲好 8 項屬性、算出職業技能點數（EDU×20）與興趣技能點數（INT×10），接著你自己輸入：
     ```
     /coc alloc occ 技能名 點數     ← 用職業點數加某項技能
     /coc alloc int 技能名 點數     ← 用興趣點數加某項技能
     ```
     可以重複加很多次；隨時 `/coc create status` 看目前分配進度，點完之後 `/coc create done` 完成建角（沒用完的點數會捨棄），或 `/coc create cancel` 放棄整個流程。
   - **使用劇本內建的預製角色**（如果 PDF 有附）：
     ```
     /coc pregens
     ```
     Bot 會讀一次劇本內容，找出裡面明確寫出的預製調查員並列出清單（第一次呼叫要花幾秒鐘讓 Claude 讀劇本，之後會直接用快取的結果）。想先看某位角色的完整能力再決定：
     ```
     /coc pregen 編號
     ```
     看到想要的之後：
     ```
     /coc usepregen 編號 [自訂名稱]
     ```
     就會直接把那位預製角色登記成你的角色，不自訂名稱就沿用劇本原本的名字。每個角色只能被一位玩家選走，每位玩家在同一局裡也只能選一次。
3. 建好角色後，直接在群組裡打字描述行動或對話（不用加任何指令），守密人就會接手描述場景、要求擲骰、更新 HP/SAN。
   - 有些資訊守密人會**私訊**給特定玩家，而不是公開在群組裡（例如：只有你發現的線索、秘密檢定結果、私人物品內容），會用「🤫（私訊）」開頭。私訊走的是 LINE/Discord 的個人對話，**前提是那位玩家已經把 Bot 加為好友（LINE）或允許伺服器成員私訊（Discord）**——沒有的話私訊會送不出去，而且目前是靜默失敗，不會在群組裡另外提示。
   - 如果劇本內建角色卡有寫「秘密目標」（例如 FBI 探員的目標是找到某人、骨董商想搞清楚某件事），用 `/coc pc` 或 `/coc usepregen` 建立那個角色的當下，會立刻私訊告訴那位玩家他的秘密目標，之後也只有守密人自己知道，不會出現在公開的角色卡（`/coc sheet`）或群組對話裡；守密人後續會透過劇情委婉引導，不會直接講白。
   - 劇本裡的地圖、平面圖、手卡這類圖片內容，守密人在適當時機（例如玩家拿到手卡、看到地圖）會直接把**實際圖片**貼出來，而不是只用文字描述；只有特定人該看到的（例如某人的私人手卡）會私訊給那個人，其他人不會看到。也可以自己主動用 `/coc showpage 頁碼` 指定看某一頁（守密人敘述中提到「第 X 頁」時可以用那個數字）。
4. **需要檢定時，是玩家自己擲骰，不是守密人幫你骰**：守密人判斷需要一次技能／理智檢定時，只會在敘述裡講清楚「要做什麼檢定、目標值多少」然後停下來——不會自己編一個成功或失敗的結果。這時候：
   - 輸入 `/coc check`（不用加任何參數）就會用守密人剛剛指定的技能/目標值幫你擲骰，結果立刻公開顯示在群組裡，然後守密人會根據這個「已經確定的結果」接著敘述，不會重新判定或改判。
   - **Discord 額外會在守密人的訊息下面附一個按鈕**（例如「🎲 偵查（55%）」），直接點按鈕效果等同輸入 `/coc check`，而且只有那位角色的玩家能按（別人點會被擋下來，提示「這不是你的檢定」）。LINE 目前沒有對應的按鈕（LINE 的 Flex Message 按鈕還沒接），只能用打字的 `/coc check`。
   - 也可以自己主動檢定，不用等守密人先開口：`/coc check 技能名 [獎勵骰數] [懲罰骰數]`，例如 `/coc check 潛行 1 0`。
5. 打起來的時候，守密人通常會自己判斷該開戰、依序處理每個人的回合，**嚴格按照 DEX 排出的先攻順位進行**——DEX 不同的人行動跟敘述都要照順序來，只有 DEX 剛好相同的人才會敘述成同時行動（細節見下面「戰鬥」段落）；也可以自己手動控制：
   - `/coc combat start`：開始戰鬥，依目前角色的 DEX 排出先攻順位
   - `/coc combat addnpc 名稱 DEX HP`：加入一個敵人
   - `/coc combat addally 名稱 DEX HP`：加入一個站在調查員這邊的 NPC 隊友（狀態列顯示「隊友」而非「敵方」，一樣照先攻順位輪流行動）
   - `/coc combat status`：查看目前回合數、先攻順位、每個人的 HP，以及輪到誰
   - `/coc combat next`：推進到下一位的回合
   - `/coc combat damage 名稱 增減量`：調整某人的 HP（受傷用負數，例如 `-5`；治療用正數）
   - `/coc combat end`：結束戰鬥
   - 玩家臨時需要離開時，輸入 `/coc away` 標記暫離，戰鬥中會自動跳過該角色的回合（顯示「（暫離）」），不會卡住整團等他；回來後輸入 `/coc back` 恢復正常。
6. 守密人主持風格上的幾個約定（都是 prompt 層面的行為，沒有對應指令）：
   - **一次一個場景片段**：正常對話一回合只推進一個具體的反應點就停下來，不會一次串好幾個場景或發現塞進同一則訊息；多位角色在場時，一次只聚焦在情境自然指向的那一位身上並點名他，不會每次都讓全員一起反應。
   - **主動掌握節奏**：不會等玩家問「現在氣氛如何」才反應，會自己判斷目前壓在調查員身上的威脅／時限，主動用劇情把大家推回劇本主線；但張力沒意義了（例如陷阱已經觸發過）也會老實收掉，不會硬拖。
   - **孤注一擲**：技能／屬性檢定失敗後，如果情境上還有更冒險的做法，守密人可能會主動問你要怎麼豁出去再試一次；孤注一擲之間一定會有時間流逝，而且再失敗的後果一定比第一次更糟，不會是「什麼事都沒發生」。理智、幸運、戰鬥擲骰不能孤注一擲。
   - **NPC 隊友**：劇本或玩家安排的 NPC 隊友會被當成一個有自己個性、會判斷錯誤的獨立調查員來演，不會被拿來直接講出守密人才知道的真相或最佳解法；戰鬥中用 `/coc combat addally` 加入的隊友也會照先攻順位正常行動，不會因為鏡頭焦點在玩家身上就站著不動。
   - 敘事文風偏向流暢散文，擲骰結果會融入句子裡（不會單獨列成一行或用條列格式），也不會用「你可以選擇 1/2/3」這種選單收尾。
7. **地圖引擎（Map/Scene Engine）**：如果劇本裡有平面圖（例如「燈塔一樓」），上傳時會自動偵測並拆成結構化的房間圖。之後玩家講「我進入燈塔，開始檢查右手邊第一個房間」這種話，系統會在呼叫 LLM **之前**，先用房間圖＋目前朝向算出「右手邊第一個房間」實際是哪一間，把算出來的房間名稱與描述直接餵給守密人、並告訴它不准自己另外猜——不是讓 LLM 自己讀地圖描述文字去猜方位（這是之前地圖方向會搞錯的根源）。
   - 這一步完全靠規則式偵測（`app/intent_parser.py` 用 regex 抓「進入／檢查／往左／右手邊／第幾個」這類詞），不會另外呼叫 LLM，抓不到的講法就照舊完全交給守密人自己判斷，跟這個功能出現之前一樣。
   - `/coc where`：查看地圖引擎目前追蹤到你在哪個房間、有哪些出口
   - `/coc enter 頁碼`：手動指定進入某一頁的平面圖（通常會自動偵測到「進入 XX」就觸發，這是備用手動指令，例如自動偵測失敗、或劇本沒寫出地點名稱時）
   - `/coc leavemap`：離開地圖追蹤，移動改回完全由守密人判斷
8. 其他指令：
   - `/coc check [技能名] [獎勵骰數] [懲罰骰數]`：自己擲骰做檢定（見上方第 4 點）
   - `/coc sheet`：看自己的角色卡
   - `/coc status`：看目前劇本與所有人狀態
   - `/coc setskill 角色名 技能名 數值`：手動修正自己角色的技能值
   - `/coc setconnection 角色名 敘述`：設定「★ 關鍵背景連結」——你最重要的一段人／地／物連結，守密人不能不由分說就直接摧毀或奪走它，一定要先給你一次擲骰搶救的機會，真的失去了才會扣理智（1 或 1D6）；這個欄位是公開的，會顯示在 `/coc sheet` 上（跟私密的秘密目標不同）。劇本內建角色卡如果自己寫了這個，`/coc usepregen` 也會直接帶入。
   - `/coc away`／`/coc back`：標記暫離／回來（見上）
   - `/coc showpage 頁碼`：看劇本某一頁的實際圖片（見上）
   - `/roll 1d100`、`/roll 3d6+2`：純擲骰，不經過守密人
   - `/coc newgame`：清空重來
   - `/coc end`：結束這一局（角色與紀錄仍保留）
   - `/coc help`：顯示指令列表

## 已知限制 / 之後可以擴充的方向

這些都是為了先做出一個能玩的 MVP，刻意先砍掉的範圍。想擴充哪一項都可以直接跟我說，下面附上為什麼會有這個限制、目前有什麼權宜作法、以及之後要補的話大概要改什麼地方。

### 1. 角色建立：三種方式，各有取捨

- **正式規則是怎樣**：COC7e 官方建角流程是先擲出 8 項屬性（STR/CON/DEX/APP/POW 用 3d6×5，SIZ/INT/EDU 用 (2d6+6)×5），接著把 `EDU×20` 當作「職業技能點數」，由玩家自己決定要分配到哪些跟職業相關的技能上；再把 `INT×10` 當作「興趣技能點數」，同樣自由分配到任何技能。很多劇本也會附上已經做好的預製調查員（pregens）給團隊直接使用。
- **這個專案現在怎麼做**：三種都做了，而且 `/coc pc` 快速生成現在會參考「這份劇本」的內建角色卡，不是完全套用固定表：
  - `/coc pc` 快速生成：**如果這份劇本已經跑過 `/coc pregens`、確實有內建角色卡**，`/coc pc` 就只能從這份劇本自己的角色裡選一個（模糊比對職業名稱，例如劇本原文是英文 "Artist"，抽取時會請 Claude 翻成「藝術家」），技能直接套用**那份劇本自己的數值**，不接受通用職業或亂打的職業名稱——劇本通常就是圍繞這幾個角色的背景鉤子設計的，用不相干的職業頂進去接不上劇情。同一個角色只能被一位玩家選走，選過的職業會從清單消失，其他人選同一個會被擋下來並提示還剩哪些。**只有這份劇本沒有內建角色卡（或還沒跑過 `/coc pregens`）時**，才會退回套用一張寫死的「通用職業技能加值表」（記者、私家偵探、醫生、教授、警察、骨董商、神職人員、流浪漢、藝術家、海洋生物學家、FBI探員）。
  - `/coc create` + `/coc alloc`：`app/creation.py` 實作了完整的擲屬性 → 算點數 → 自己分配的流程，跟規則書一致，只是每次加點都要打一行指令，比實體桌邊建角慢一些。
  - `/coc pregens` + `/coc pregen 編號` + `/coc usepregen`：`app/pregen_extractor.py` 會請 Claude 掃一次已載入的劇本文字，把裡面明確寫出的角色卡（屬性、技能、背景、秘密目標）抽成結構化資料供選用；規則是「只回報文本裡寫的數值，不自己編」，缺漏的欄位（例如劇本沒寫 MP）才會用公式補上。`/coc pregens` 列出候選並標示哪些已經被選走；`/coc pregen 編號` 可以在選之前先看某位角色的完整屬性與**全部**技能（不像 `/coc sheet` 只顯示前 12 項），但不會洩漏秘密目標——那只在真的選走那個角色的當下才私訊給那位玩家。`/coc pc` 或 `/coc usepregen` 選中一次就記住是誰選的，之後別人選同一位、或同一位玩家想重選都會被擋下來（見下方「一人一角」）。抽取結果會快取在 `state.pregens`，**換一份新劇本上傳時會自動清空**，不會把舊劇本的角色卡誤用到新劇本上（這是實測時真的發生過的問題，已修正）。
  - **一人一角限制**：同一局遊戲進行中（`state.active` 為真），一個玩家只要已經有角色，`/coc pc`、`/coc create`、`/coc usepregen` 都會被擋下並提示現有角色名稱，避免手滑蓋掉正在玩的角色。`/coc end` 結束這局之後限制就解除（角色資料仍保留，但玩家可以重新建角），真的要完全重來則用 `/coc newgame`。
  - **實測抓到一個影響很大的抽取品質問題並修好了**：劇本裡的角色卡頁面（有 STR/CON/SIZ 等屬性欄位、一排排技能百分比）先前會被 `app/pdf_loader.py` 的圖片理解步驟誤判成「不是地圖，就簡短描述」，導致 Claude 只回「這頁列出了完整的角色屬性與技能數值」這種空話，實際數字整個消失，`/coc pregens` 抽出來的每一位角色 `skills` 都是空字典。已經幫圖片理解的提示詞加了專門判斷「這是角色卡」的分支，要求逐一列出每個屬性和每項技能的實際數字、以及背景鉤子/秘密目標段落，不能用摘要帶過。用真實劇本重新測過：4 位預製角色現在都抓到 22-24 項技能的完整數值，秘密目標也正確抓到。
- **還是有的限制**：
  1. `/coc create` 的技能點數分配沒有做「哪些技能才算你這個職業的『職業技能』」這層限制（正式規則職業點數理論上只能點在職業清單內的技能），目前是完全自由分配到任何技能，算是刻意放寬、換取指令操作的簡單。
  2. `/coc pc` 要吃到「這份劇本自己的職業」，前提是**已經手動跑過一次 `/coc pregens`**——沒有自動在背景偷跑這個查詢，因為那是一次額外的 Claude 呼叫，不想讓每次 `/coc pc` 都多花錢、多等待；沒跑過的話 `/coc pc` 就只會顯示通用職業表（此時還沒有內建角色卡可以保護，多人可能剛好取同名角色，不受下面第 3 點的保護）。
  3. 選走的角色目前沒有「放棄／讓出」的指令，選錯了只能等這局遊戲 `/coc end` 之後才能重選（見上方「一人一角限制」），要在同一局內就釋放給別人選，目前得手動編輯存檔。
- **之後要擴充的話**：可以把 `/coc create` 的分配過程做成更友善的引導式對話（例如列出職業相關的技能清單讓玩家直接選），或是加一個「放棄角色」的指令釋放已選走的預製角色。

### 2. 正式的戰鬥輪次系統

- **正式規則是怎樣**：COC7e 戰鬥有明確流程——依 DEX（必要時比 POW）排出先攻順序、每輪每個角色輪流宣告動作（攻擊／閃避／使用道具等）、命中後算傷害與傷害加值、達到一定傷勢要做重傷判定、HP 歸零要做死亡/瀕死流程等，是一套有嚴謹順序的子系統。
- **這個專案現在怎麼做**：`app/combat.py` 實作了一個獨立的 `CombatState`，記錄先攻順位（依 DEX 排序）、目前是第幾輪、輪到誰、每位戰鬥員（玩家角色與 NPC）的 HP。守密人偵測到「打起來了」的場面時會自己呼叫 `start_combat`／`add_npc_to_combat` 開戰，每處理完一位戰鬥員的行動就呼叫 `advance_combat_turn` 推進，血量變化呼叫 `damage_combatant`——這些現在都是程式邏輯在管，不是純粹交給 LLM 自由判斷；玩家也可以用 `/coc combat start`／`addnpc`／`status`／`next`／`damage`／`end` 指令直接手動操作、跟守密人共用同一份戰鬥狀態。
- **還是有的限制**：
  1. 先攻順序是「開戰當下依 DEX 排一次」，戰鬥中途沒有做「DEX 並列時比對抗」或「某些能力可以打斷先攻」之類的細節規則，都是簡化的靜態排序。
  2. 沒有做傷害加值骰（Damage Bonus）自動套用到武器傷害、也沒有重傷（Major Wound）、擊倒／昏迷這些狀態判定，這些還是靠守密人敘事上自行拿捏，只是 HP 數字本身是準的。
  3. NPC 的 DEX／HP 目前是守密人依劇本內容合理估算（劇本沒寫明的話會抓一般人類等級的數值），不是套固定的怪物數值表。
- **之後要擴充的話**：可以把武器傷害加值、重傷判定、擊倒/昏迷規則做成對應的工具函式，讓 Keeper 呼叫後直接拿到規則正確的結果，而不是自己描述時憑感覺套用。

### 3. 圖文混排的頁面（手卡／地圖／插圖）可能抽不完整，整份純掃描 PDF 沒裝 OCR 就讀不出來

- **現在怎麼做**：`app/pdf_loader.py` 的文字層改用 [MarkItDown](https://github.com/microsoft/markitdown)（+ `markitdown-ocr` 插件）預處理，取代原本單純的 PyMuPDF 文字抽取——保留文件結構（標題、表格）比純文字讀取器更完整，而且 `markitdown-ocr` 會偵測頁面裡「內嵌的點陣圖片」自動做圖片理解（見下方說明）。`app/markitdown_shim.py` 讓這個 OCR 插件走**我們自己既有的 `ANTHROPIC_API_KEY`**，不需要另外申請 OpenAI 帳號——`markitdown-ocr` 原生設計是接 OpenAI 的 Chat Completions 介面（`client.chat.completions.create(...)`），所以寫了一個薄薄的轉接層把這個介面轉呼叫 Anthropic，讀了 `markitdown` 和 `markitdown-ocr` 的原始碼才確認這樣接得通，不是照抄教學文章的 OpenAI 範例。PyMuPDF 沒有被拿掉，還是負責兩件 MarkItDown 完全不做的事：把頁面轉成圖片（給下面兩個「整頁圖片」備援用）、以及 MarkItDown 不可用或轉換失敗時的備援文字層——任何一步失敗都會自動退回舊行為，不會整份炸掉。
  - **平面圖／地圖是特別驗證過的案例**：純文字抽取對平面圖幾乎注定失敗——房間名稱在頁面上是 2D 排列的，文字抽取只能拉成一維序列，「進門右手邊第一個房間」這種相對位置關係在抽取過程就丟失了。這不是理論推測：實測時真的發生過，玩家說進門右手邊該是寢室，守密人（讀到的是打亂順序的房間名稱清單）卻說成廚房。改成請 Claude 直接看圖描述空間佈局後，重新測同一頁面，正確重建出了完整動線（正門進去左手邊臥鋪房、右手邊書房、走廊底端連接燈塔），而且抓出了純文字抽取完全不可能拿到的細節（走廊盡頭有個染血的通道，暗示案發地點）。**這一步刻意保留、沒有改用 MarkItDown 取代**：`markitdown-ocr` 的圖片理解只認得到 PDF 裡「內嵌的點陣圖片物件」，一張用向量線條畫出來的平面圖（矩形、直線畫出來的房間格局，不是一張圖片）在它眼裡根本沒有圖片可以辨識，會直接被跳過；`app/pdf_loader.py` 自己「把整頁渲染成圖片」的備援機制不管頁面是向量畫的還是點陣圖片，一律能抓到，這正是它還留著、而且優先權比較高的原因。
  - 一份 43 頁的真實劇本裡，符合「文字偏少」門檻的頁面高達 24 頁（角色卡、地圖、插圖），這些頁面用 6-12 條並行連線一起處理，但因為受 Anthropic 那端速率限制影響，整體還是要跑上將近一分鐘——所以上傳 PDF 時 Bot 會先回一句「收到了，正在讀取劇本內容」的立即回覆，等處理完才用另一則訊息公布結果，而不是讓你對著沒反應的畫面等一分鐘懷疑 Bot 是不是掛了（細節見下面「LINE reply token 的 60 秒限制」那條）。
- **還是有的限制**：
  1. 沒有 `ANTHROPIC_API_KEY`（例如你 `LLM_PROVIDER=gemini` 又沒填 Anthropic 金鑰）時，MarkItDown 的 OCR 插件跟原本「整頁圖片理解」這步都會直接跳過，MarkItDown 本身仍會嘗試純文字轉換，圖片內容則退回本機 `tesseract` OCR；OCR 沒裝或語言包不齊全時，該頁就只剩下原本抽到的少量文字。
  2. 如果整份 PDF 從頭到尾都是掃描頁（完全沒有文字圖層），又沒有 Anthropic 金鑰也沒裝 OCR，還是會直接抽不出任何內容，Bot 會回覆「這份 PDF 抽不出任何文字內容」。
  3. 圖片理解花的是你自己 Anthropic 帳號的用量（一份劇本可能觸發十幾到二十幾次呼叫），雖然單次都不貴，但劇本圖片越多，上傳時花的錢跟等待時間就越多；`markitdown-ocr` 又是完全獨立的第二輪呼叫（偵測頁面內嵌圖片時觸發），實測發現同一頁如果 `markitdown-ocr` 抽到的內嵌圖片說明本身還是偏短（例如一張純裝飾用的小插圖），頁面文字總長度可能還是低於 200 字門檻，導致原本「整頁圖片理解」備援又跑一次幾乎一樣的內容——這是已知、會多花一點錢但不影響正確性的重複，還沒有進一步優化掉。
  4. `markitdown` 的 PDF 轉換偶爾會把沒有實際格線的並排文字（例如純用空格對齊、沒畫框線的技能表）拆成一欄一欄分開輸出，而不是照原本一行一行的順序——這種情況下反而比單純的 PyMuPDF 文字層更難讓 LLM 正確配對「技能名稱」跟「數值」；`app/pdf_loader.py` 目前沒有偵測這種情況並自動改用 PyMuPDF 的機制，需要之後拿到更多真實劇本測試後再決定要不要加。
- **現在的權宜作法**：確保 `.env` 裡有填 `ANTHROPIC_API_KEY`（就算 `LLM_PROVIDER=gemini` 也一樣，這步驟目前固定用 Anthropic），就能吃到 MarkItDown OCR 跟整頁圖片理解的完整效果；如果真的沒有金鑰，退回裝 `brew install tesseract tesseract-lang`，效果會差一截但總比沒有好。
- **之後要擴充的話**：讓圖片理解也支援 Gemini（目前寫死用 Anthropic，跟 `LLM_PROVIDER` 設定無關）；把「這頁是不是平面圖」的判斷做得更精準，避免對純插圖頁也跑一次比較貴的圖片理解呼叫；針對第 4 點，拿更多真實劇本測過 MarkItDown 的表格辨識準確度後，考慮加一個「這頁的欄位順序看起來被打亂了」的偵測，自動退回 PyMuPDF 那頁的文字層。

### 4. Map/Scene Engine 是簡化的羅盤模型，不是真正的 3D 空間

- **這個專案現在怎麼做**：上傳劇本時，`app/pdf_loader.py` 對每個「文字偏少」的頁面除了原本的圖片描述，還會額外呼叫一次 `app/scene_map.py`，請 Claude 判斷這頁是不是平面圖，是的話拆成房間節點＋方位邊（每條邊標 N/NE/E/SE/S/SW/W/NW，樓梯用 U/D）。玩家講「進入燈塔」「檢查右手邊第一個房間」這類話時，`app/intent_parser.py` 用 regex（不呼叫 LLM）判斷是不是移動意圖，是的話 `app/commands.py` 在呼叫守密人**之前**先用 `resolve_move` 算出實際房間，把結果連同房間描述一起塞進守密人的 prompt 並要求「不准自己另外猜」——地圖方向的正確性交給程式碼保證，LLM 只負責敘述算好的結果。
- **還是有的限制**：
  1. 這是「羅盤方位」而非真正的 2D/3D 座標系統：每個房間的出口方位是圖片理解時憑「頁面朝上當北方」估出來的一個相對關係，不是精確測量的角度；圖片本身畫得不夠工整、或門的方向本來就模糊時，估出來的方位可能不準。
  2. 只追蹤「一組共享的隊伍位置」（`state.current_room_id`），沒有做到分隊後每個人在不同房間的獨立位置追蹤——分隊行動目前還是完全交給守密人系統提示詞裡的「分隊敘事規則」處理，跟地圖引擎沒有整合。
  3. `app/intent_parser.py` 是規則式的，只認得固定的方向詞和「進入／前往」之類的動詞組合，講法比較口語或迂迴（例如「欸我們晃去看看那邊那間好了」）大概率抓不到，這時會完全 fallback 回原本「守密人自己判斷移動」的行為，不會出錯，只是沒吃到地圖引擎的好處。
  4. 一張平面圖只要被判定為「文字偏少」頁面就會多花一次 Claude 視覺理解呼叫（跟原本的頁面描述呼叫並行送出），對有平面圖的劇本來說，上傳時的圖片理解成本大約變兩倍；沒有平面圖的劇本這筆呼叫幾乎都會回傳「不是地圖」，等於白花一次小額呼叫（`max_tokens=2048`，比照角色卡辨識用同一顆模型，單次不貴）。
  5. 「自動偵測進入哪個地點」是拿玩家講的地點名稱去模糊比對地圖的 `location_name`（劇本裡有沒有寫清楚這個地點叫什麼名字，決定比對準不準），劇本沒明確講出地點名稱、或玩家講的名稱差太多時就配不到，需要靠 `/coc enter 頁碼` 手動指定。
- **之後要擴充的話**：分隊時每個小隊各自的 `current_room_id`；把「這頁是不是平面圖」的判斷跟原本的頁面分類合併成一次呼叫而不是兩次，省一半的圖片理解成本；`intent_parser.py` 抓不到的講法可以考慮之後接上「Scenario RAG」那條路一起處理，而不是純規則硬撐。

### LINE reply token 的 60 秒限制（PDF 上傳現在會分兩則訊息回覆）

LINE 的 reply token 只能用一次、而且**收到 webhook 後 60 秒內沒用掉就失效**（官方文件寫的，不是猜的）。PDF 上傳現在因為要對「文字偏少」的頁面逐一做圖片理解，即使並行處理，一份圖片較多的劇本輕鬆就會超過 60 秒——這代表如果程式傻傻等處理完才回覆，reply token 早就過期了，玩家會完全收不到「劇本已載入」的確認訊息，卻也不會有任何錯誤提示，看起來就像 Bot 沒反應。

修法：`app/commands.py` 的 `handle_pdf_upload` 現在拆成兩個回覆管道——收到檔案先立刻用 `reply`（reply token）回一句「收到了，正在讀取...」，實際處理完再用 `push`（LINE push message API，沒有 60 秒限制，但需要知道要推給誰）送出真正的結果。Discord 沒有這個限制，兩個管道直接指向同一個 `channel.send`。這個修法有實測過：真實劇本處理耗時 62 秒，確認立即回覆準時送達、最終結果確實走 push 送達，換算下來完全在舊的純 reply 設計會失敗的時間點之後。

唯一要注意的權衡：push message 會計入 LINE 的付費配額（reply 不會），但這裡只有上傳 PDF 這個低頻動作會觸發一次，不影響「Reply 免費不限量」對日常遊玩的結論。

### 5. 檢定改成玩家自己擲骰，不是守密人代骰

- **正式規則是怎樣**：實體桌邊跑團時，檢定通常是玩家自己拿骰子擲出來的，KP 只負責設定目標值、宣告結果，不會替玩家骰。
- **這個專案現在怎麼做**：`app/keeper.py` 的 `skill_check`／`sanity_check` 工具改成只「請求」一次檢定——記錄要檢定的技能、目標值、獎懲骰（或 SAN 損失公式），存進 `GroupState.pending_checks`，不會真的擲骰，也不准守密人自己編一個結果。守密人呼叫完工具後只能敘述「需要做這個檢定」的當下場景，停在那裡等玩家真的擲骰。玩家輸入 `/coc check`（不用加參數就會用守密人剛剛指定的技能/目標值）就會由程式碼（`app/dice.py`，跟原本一樣的判定邏輯）擲出真正的 1d100、算出成功等級，結果立刻公開顯示在群組裡；接著這個「已經確定的結果」會餵回守密人，請它根據這個既定事實繼續敘事，不能重新判定或改判。也可以不等守密人先開口，自己主動打 `/coc check 技能名 [獎勵骰數] [懲罰骰數]` 檢定。
  - **Discord 額外做了互動按鈕**：`app/discord_bot.py` 會在守密人請求檢定後，於訊息下面自動附一個「🎲 技能名（目標值%）」按鈕（Discord 原生的 Component/Interaction，不是文字選單），點下去效果等同輸入 `/coc check`，而且用 `discord.ui.DynamicItem` 依 `custom_id` 比對，不是存在記憶體裡的一般 View，所以就算之後重啟 Discord 常駐行程（這個專案幾乎每次部署都會重啟），舊訊息上的按鈕還是點得動。只有那個角色的玩家能按，別人點會被擋下來並提示「這不是你的檢定」；按完之後原按鈕會被移除，避免重複點擊。
- **還是有的限制**：
  1. LINE 沒有對應的按鈕（LINE 的 Flex Message 也支援互動按鈕，但這個專案還沒接），LINE 玩家目前只能用打字的 `/coc check`。
  2. `pending_checks` 一個玩家只能同時存一筆——如果守密人在同一輪連續對同一位玩家請求了兩次不同的檢定（理論上不太會發生，敘事節奏規則也要求一次只推進一個場景片段），後一次會覆蓋前一次，不會排隊。
  3. 玩家如果遲遲不理會待處理的檢定、直接打字做別的事，守密人的下一次回覆看不到「這個檢定还没解決」的提醒——`pending_checks` 只在 `/coc check` 或下一次真的呼叫 `skill_check`/`sanity_check` 時才會被處理或覆蓋，不會主動提醒玩家還欠一個檢定。
  4. 這個規則靠系統提示詞要求守密人「不能自己編結果」，跟這個專案其他規則一樣沒有程式碼硬性擋住 LLM 自己在敘述裡編一個成功/失敗——只是工具本身回傳的資料已經不含結果，LLM 要憑空編的話等於是完全脫離工具結果自己掰，理論上比以前更難發生，但沒有 100% 的保證。
- **之後要擴充的話**：LINE 也做一版 Flex Message 按鈕；`pending_checks` 改成一個 list 而不是單一 dict，支援同時有多筆排隊；某位玩家的檢定放著太久沒處理時，主動在下一次守密人回覆裡提醒一下。

### 6. Scenario RAG 是可選功能，預設關閉

- **這個專案現在怎麼做**：`SCENARIO_RAG_ENABLED=true`（`.env`）開啟後，`app/keeper.py` 不再把整份劇本文字放進（快取的）system prompt，改放一小段提示文字，並多給 Keeper 一個 `search_scenario` 工具；`app/scenario_rag.py` 把劇本依「--- 第 N 頁 ---」分頁切成 chunk，用純本地的 BM25 關鍵字檢索（CJK 用 bigram 分詞，沒有另外裝分詞套件；ASCII 用單字），Keeper 每次要查劇本細節就呼叫這個工具查詢，只拿到最相關的幾頁內容，而不是整份都在眼前。**預設是關閉的**——不開的話行為跟這個功能出現之前完全一樣，整份劇本照舊放進 system prompt。
- **為什麼不用真正的語意檢索（embeddings）**：這個專案一直很在意額外的花費和依賴（prompt caching、規則式 Intent Parser 不額外呼叫 LLM 都是同樣的考量）。真正的語意檢索需要另一組付費 API（Anthropic 本身沒有 embeddings 端點，常見選擇是 Voyage AI 或 OpenAI）加一個向量資料庫，而目前測過的劇本都還在 `MAX_SCENARIO_CHARS` 上限內、用不到。BM25 是純字面比對，零額外成本、零新依賴，換來的是準度打折。
- **還是有的限制**：
  1. 純字面比對，不是語意理解——玩家問的詞如果劇本裡用完全不同的說法描述（同一個角色，劇本寫全名，玩家只講外號），可能查不到；沒有同義詞或改寫的容錯能力。
  2. 檢索粒度是「一整頁」，不是更細的段落，一頁如果混雜好幾個不相關的主題，查到的內容可能夾雜不需要的部分。
  3. **關掉「整份劇本一次全部在眼前」這件事本身就是取捨**——原本 Keeper 能自己把跨頁的線索兜在一起（例如「這個符號在第 3 頁提過，第 20 頁又出現」），開啟 RAG 後這種跨頁關聯只在 Keeper 主動查了兩次、剛好都查到才會發生，沒有整份文字時那麼可靠。
  4. Keeper 是否記得在需要時呼叫 `search_scenario`，靠的是系統提示詞的自律（跟這個專案其他工具呼叫的約束方式一致），沒有程式碼強制「回覆前一定要先查過劇本」。
  5. 索引依 `group_id` 快取在記憶體裡（跟著程式行程活，不寫到 `data/groups/*.json`），重啟 LINE/Discord 常駐行程後第一次查詢會重建一次索引（純 CPU，很快，沒有額外呼叫）。
- **什麼時候該開**：劇本長度逼近或超過 `MAX_SCENARIO_CHARS`（240,000 字）時，或想省掉「劇本內容佔掉大部分 prompt caching 額度」的成本時，可以考慮開啟；一般長度的劇本，關閉（預設值）通常敘事品質更好、更省心。
- **之後要擴充的話**：真的需要語意檢索時，再接一個 embeddings 供應商做混合檢索（BM25 + 向量取交集/加權）；或是把檢索粒度從整頁再切細一點。

### 7. 本機測試階段，ngrok 網址每次重啟都會變

- **原因**：ngrok 免費方案沒有固定網域，每次執行 `ngrok http 8000`（不管是你自己重開終端機、電腦重開機、還是 ngrok 連線斷掉重連）都會重新配一個隨機網址，例如這次是 `https://a1b2c3d4.ngrok-free.app`，下次可能變成完全不同的一串。
- **影響**：網址一變，LINE Developers Console 裡設定的 Webhook URL 就失效了（因為指向舊網址），Bot 在群組裡會完全沒反應，需要你手動回 Console 重新貼上新網址、按 **Verify** 確認連得到，才能恢復。
- **現在的權宜作法**：測試期間盡量讓本機伺服器和 ngrok 全程開著不要關；如果真的重開了，記得照「第一步」最後段落回 LINE Developers Console 更新 Webhook URL。ngrok 付費方案有提供「固定網域（reserved domain）」，綁定後網址就不會再變。
- **之後要擴充的話**：正式要長期給朋友使用的話，建議換成有固定網域的雲端主機（Render / Railway / Fly.io / 自己的雲端主機都可以），把 `uvicorn` 換成常駐服務執行、Webhook URL 設一次固定網址就永久有效，程式碼完全不用改，只是部署方式換掉。

### 其他次要限制

- ~~`data/groups/*.json` 是整檔讀出、整檔覆寫，沒有加鎖，同一群組兩人幾乎同時打字可能互相覆蓋對方的 HP/SAN 變化~~ 已修正：`app/locks.py` 加了一個 per-group 的 `asyncio.Lock`，`app/main.py` 在 `load_state` 到最後一次 `save_state` 之間（包含等待守密人 LLM 回覆的期間）都持有同一把鎖，同一個群組的訊息會排隊依序處理，不同群組之間仍然完全並行、不互相卡住。用 20 次同時觸發的 HP 變化模擬測過，改動前後的結果都對得上（沒有任何一次更新遺失）。代價是同一個群組如果同時有很多人講話，訊息會變成排隊處理而不是真的同時處理——正常聊天速度感覺不出來，但如果好幾個人在戰鬥中搶著同時行動，會依訊息抵達順序一個一個處理，不會真的並行。
- 貼圖、圖片、影片、語音、位置訊息現在會回一句「這個類型讀不懂，麻煩用文字描述」，不會再靜靜已讀不回；但也僅止於告知，沒有真的去分析圖片內容或把語音轉成文字——如果之後想讓 Bot「看得懂」玩家傳的照片或語音，需要另外接圖像／語音辨識。
- 遊戲狀態存在本機 `data/groups/*.json`，換主機記得把整個 `data/` 資料夾也搬過去，不然角色和劇本進度會不見；長期使用建議改接資料庫（例如 SQLite）取代純檔案儲存。
- ~~每一句非指令訊息都會把完整劇本文字整包重新送進 Claude 的 system prompt，沒有用 Anthropic 的 prompt caching~~ 已修正：`app/keeper.py` 現在把 system prompt 拆成兩塊——「規則說明＋劇本內容」這個大而穩定的區塊標了 `cache_control`（連同固定不變的 `tools` 清單），角色卡／戰鬥狀態這種每回合都在變的小區塊留在快取範圍外，改動不會打掉快取。劇本只要幾分鐘內持續有人互動，同一份劇本內容幾乎只算一次錢，明顯降低長劇本、長時間遊戲的花費。快取預設有效期是 5 分鐘，如果一段時間沒人說話快取會過期，下一句話會重新算一次全額 token（之後仍會回到快取狀態）。
- ~~`MAX_LOG_TURNS`／`MAX_SCENARIO_CHARS` 分別限制了守密人能看到的對話紀錄長度和劇本長度，超過的部分會被截掉；長篇戰役玩到後期，比較早期的劇情細節守密人可能會忘記，很長的劇本 PDF 後段內容也可能讀不到~~ 部分緩解：
  - `MAX_SCENARIO_CHARS` 從 90,000 調高到 240,000（可在 `.env` 調整）。實測那份 43 頁的真實劇本《The Lightless Beacon》OCR 後是 77,691 字，舊上限下已經逼近截斷邊緣；新上限下大約還有 3 倍空間，可以放下長很多的劇本，上傳時如果真的還是超過上限，Bot 現在會明確告訴你「後半段已經被截斷」，不會悄悄漏掉卻沒人發現。
  - `MAX_LOG_TURNS` 從 40 調高到 80（可在 `.env` 調整），而且 `app/providers/anthropic_provider.py` 現在也對「對話紀錄」這塊做了跟劇本文字一樣的 prompt caching（在最後一筆歷史紀錄上標快取斷點，只要視窗沒有被裁切過，前面的對話內容幾乎不用重算），所以拉長對話紀錄視窗不會讓每回合成本線性增加。
  - **還是沒解決的部分**：這兩個數字終究是硬上限，不是真的「無限記憶」。劇本超過 240,000 字還是會被截斷（只是機率低很多）；對話紀錄超過 `MAX_LOG_TURNS*4`＝320 筆時一樣會被裁掉舊的，長到某個程度的戰役，守密人還是會忘記很早期的劇情細節。真的要做到「不會忘記」，需要另外做一套定期把舊對話摘要成「劇情摘要」永久保留的機制，目前還沒做。
- `/coc newgame`、`/coc end` 這類影響整個群組的指令，目前任何一個群組成員都能下，沒有限定只有開團的人或管理員才能用。
- 目前沒有任何自動化測試／CI，功能都是開發過程中手動跑腳本驗證的。
- 加了 `app/providers/` 這層供應商抽象換取「Claude／Gemini 隨時可切換」的彈性，代價是多一層間接、多一組要維護的介面（兩邊都要各自處理好工具呼叫、訊息格式轉換）；其中 Gemini 那條路徑目前只做了型別層級和用假 client 的迴圈邏輯測試，沒有真的打過一次線上 API，細節見「切換 LLM 供應商」段落。
- LINE reply token 60 秒的限制目前只有 PDF 上傳那條路修好了（見上面「LINE reply token 的 60 秒限制」）。守密人回合本身（`keeper.run_turn`）如果剛好遇到很多次工具呼叫串在一起（例如複雜戰鬥一次要判定好幾個人的檢定），理論上一樣有機會撐超過 60 秒，這條路目前還是純用 `reply`，沒有比照 PDF 上傳做 reply+push 分流；還沒有實測案例踩到，但這是已知的同類風險，還沒修。
- 守密人可以用 `send_private_info` 工具私訊玩家（見「玩法」段落），但目前是**靜默失敗**：如果那位玩家在 LINE 還沒加 Bot 好友、或在 Discord 關閉了「允許伺服器成員私訊」，私訊會送不出去，`app/commands.py` 直接吞掉例外，群組裡不會有任何提示。之所以沒做失敗通知，是因為要在不洩漏私人內容本身的前提下告知「有訊息送不出去」，需要另外一條不佔用 LINE reply token 的訊息管道（跟 PDF 上傳那個 reply+push 分流是同一類問題），目前還沒有為一般對話回合做這層分流，之後如果要補，可以順便補這個。
  - 實測時還抓到一個更隱蔽的洩漏模式：守密人會在公開回覆裡寫類似「（如果骨董商在場，這裡就會認出這是卡西迪——但目前無人認得他）」這種括號旁白。這種寫法就算沒直接爆雷，也已經洩漏了「這裡有東西能被特定人物認出來」這件事本身，等於變相劇透。已經在系統提示詞裡明確禁止這種「條件式旁白／後設說明」，改成規則是：符合條件的角色真的在場就用 `send_private_info` 私訊，不在場就完全不提，不留下任何暗示。
- 敘事節奏紀律、文風、孤注一擲、NPC 隊友設計指南（見「玩法」段落第 5 點）目前全都是系統提示詞層面的行為要求，沒有程式碼強制執行——參考了 [coc-kp-host](https://github.com/SumanasJ/coc-kp-host) 這個純 prompt 型 KP skill 的做法。跟這個專案既有的 DEX 先攻順位、暫離跳過等規則不同的是，這幾項完全靠 LLM 自己遵守提示詞，沒有像 `app/combat.py` 那樣的程式碼守門，理論上模型偶爾還是可能忘記（例如孤注一擲問一半又自己算過、或一次講太多場景），沒有自動化測試能保證每次都遵守。
- 「★ 關鍵背景連結」（`/coc setconnection`）目前只是一個自由文字欄位加上提示詞層面「不能沒收搶救機會」的約束，沒有真的擋住守密人的 `adjust_character`／`sanity_check` 工具呼叫；換句話說技術上守密人還是叫得動工具直接刪掉，全靠提示詞自律。`/coc create` 互動建角流程也還沒有讓玩家在建角當下就設定這個欄位，得建完角色後另外呼叫 `/coc setconnection`。
- NPC 隊友（`/coc combat addally`）只在戰鬥的先攻順位裡多一個「隊友」分類；戰鬥外沒有獨立的「NPC 隊友角色卡」資料結構（不像玩家角色有 `Character`），完全由守密人在敘事裡自己記住並扮演，沒有結構化資料能查詢或跨場景保留 NPC 隊友的技能數值。
- 使用者提過一張更大的目標架構圖（玩家訊息 → Intent Parser → Keeper Skill → Deterministic Engine → Map/Scene Engine → Scenario RAG → LLM）。五層都做了對應版本：Map/Scene Engine（`app/scene_map.py`）、規則式的 Intent Parser（`app/intent_parser.py`，只做移動意圖偵測，不是那張圖上完整的意圖分類器）、Deterministic Engine 對應既有的 `app/dice.py`／`app/combat.py`、Scenario RAG（`app/scenario_rag.py`，見下方「Scenario RAG 是可選功能」那條）。
