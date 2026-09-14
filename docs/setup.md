# 安裝與設定教學

從申請 LINE/Discord 憑證到本機啟動的完整步驟。已經把 Bot 架起來的話，回 [README.md](../README.md) 看 Quick Start 跟玩法就好，不用看這份。

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

- **`LLM_PROVIDER=anthropic`**：用 Claude，走 `app/providers/anthropic_provider.py`，需要 `ANTHROPIC_API_KEY`。有做 prompt caching（見 [docs/changelog.md](changelog.md) 的說明），劇本內容重複的部分幾乎不會重複計費。
- **`LLM_PROVIDER=gemini`**：用 Google Gemini，走 `app/providers/gemini_provider.py`，需要 `GEMINI_API_KEY`（去 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 申請）。Gemini Flash 系列通常比 Claude 便宜不少，如果主要考量是成本，這是值得一試的選項。
  - **請注意**：這條路徑是照 Google 官方 `google-genai` SDK 文件寫的（手動 function-calling 迴圈：`client.models.generate_content` + `FunctionDeclaration`/`Tool`），程式碼結構跟工具 schema 都用真的 SDK 型別測過可以正常建構，但因為手上沒有 Gemini API key，**沒有實際打過一次真的 API 呼叫**驗證端到端行為。Google 的 Gen AI SDK 這一兩年變動蠻快的，如果切過去發現守密人完全沒反應或行為怪怪的，先去對一下 `app/providers/gemini_provider.py` 裡用到的屬性名稱（`response.function_calls`、`response.text`、`Part.from_function_response` 等）跟當時最新的 SDK 文件是否還一致，再懷疑是遊戲邏輯本身的問題。
  - Gemini 目前沒有做 prompt caching（Google 那邊叫 context caching，跟 Anthropic 的做法不同、而且門檻可能要幾萬 token 起跳），劇本內容會整包重新送——如果之後真的固定用 Gemini，這是下一個值得補的優化。
  - `GEMINI_MODEL` 預設值請自己去 [ai.google.dev](https://ai.google.dev) 核對當下實際可用的 flash 模型名稱再決定要不要改，模型 id 會隨時間變動。
- **`LLM_PROVIDER=openai`**（預設）：用 ChatGPT（`gpt-5.6-luna`），走 `app/providers/openai_provider.py`，需要 `OPENAI_API_KEY`（[platform.openai.com/api-keys](https://platform.openai.com/api-keys) 申請，注意這跟 ChatGPT 網頁版的登入帳號是同一組帳號，但金鑰是分開申請的，格式是 `sk-` 開頭的一長串字元）。走的是 **Responses API**（`client.responses.create(instructions=..., input=..., tools=[...])`），不是舊的 Chat Completions API——OpenAI 官方最新的 Python 函式庫文件（[developers.openai.com/api/docs/libraries](https://developers.openai.com/api/docs/libraries?language=python)，使用者提供的連結）建議新串接都改用這個，所以沒有照抄 Anthropic/Gemini 那兩條路徑的舊介面寫法。工具定義的欄位形狀（`{"type": "function", "name", "description", "parameters"}`，不像 Chat Completions 那樣包一層 `"function"`）跟回傳的 `function_call`/`function_call_output` 項目欄位都是直接讀已安裝的 `openai` SDK 原始碼型別定義確認過的，不是憑印象寫的。**這是目前唯一實際跑過真實 API 驗證過的路徑**（包含完整 `keeper.run_turn` 跑過一次真實劇本場景，確認 `skill_check` 正確請求檢定而不是自己編結果）——`OPENAI_MODEL=gpt-5.6-luna` 也已經拿真的金鑰確認過是有效、可用的模型 id，不是猜測值。目前正式在跑的 `.env` 就是設定成 `openai`；`.env.example` 的預設值仍是 `anthropic`（比較久、比較穩的路徑，給新架設的人一個保守的起點）。
  - **OpenAI 的 prompt caching 是自動的，不用寫程式碼去標記，而且實測真的有生效**：拿真的金鑰測過——同一段約 3600 token 的 `instructions` 連續呼叫兩次，第一次 `cached_tokens=0`（全部要重新算），第二次 `cached_tokens=3623 / 3643`，幾乎整段都命中快取，只有真正變動的十幾個 token 需要重算。這跟 Anthropic 需要手動在特定區塊標 `cache_control` 才會生效不一樣——OpenAI 這邊只要「這次呼叫的內容開頭」跟最近一次呼叫完全一樣（逐字比對），就會自動命中，不需要特別分段或標記，所以 `app/providers/openai_provider.py` 現在的寫法（`instructions = static_system + dynamic_system` 兜成一整串）已經自動吃到這個效果了——只要 `static_system`（劇本內容）沒變，即使後面 `dynamic_system`（角色卡/戰鬥狀態）每回合都在變，前面那一大段還是會命中快取。快取存活時間、確切折扣比例沒有進一步測過，只確認了「同一個 process 裡連續兩次呼叫會命中」這件事。
  - `app/markitdown_shim.py`（見 [docs/changelog.md](changelog.md) 的「圖文混排的頁面」那條）在偵測到 `OPENAI_API_KEY` 時，會自動改用真正的 `openai.OpenAI()` client 餵給 `markitdown-ocr`，不再透過 Anthropic 轉接殼——`markitdown-ocr` 原生就是設計給 OpenAI 用的，兩邊共用同一把金鑰之後就不需要那層轉接了；沒有設定 `OPENAI_API_KEY` 時才會退回 Anthropic 轉接殼。

### （可選）開啟 Scenario RAG

`.env` 裡的 `SCENARIO_RAG_ENABLED=true` 可以把「整份劇本塞進 system prompt」改成「Keeper 用 `search_scenario` 工具按需檢索」，不需要另外申請任何金鑰（本地 BM25 關鍵字比對，不是語意檢索）。預設是 `false`（關閉），一般長度的劇本建議保持關閉；細節、取捨、什麼時候該開，見 [docs/changelog.md](changelog.md) 的「Scenario RAG」那條。

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
