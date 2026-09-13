# COC7e 守密人 Bot（LINE / Discord）

在 LINE 群組或 Discord 頻道裡上傳一份《克蘇魯的呼喚》第七版（COC7e）劇本 PDF，就能讓 LLM 扮演守密人（Keeper），直接在聊天室裡跑團。規則判定（技能檢定、SAN 值、擲骰）由程式碼負責計算，LLM 負責讀劇本、敘事、決定什麼時候該擲骰。後端 LLM 可以在 Claude（Anthropic）和 Gemini（Google）之間切換，見下面「切換 LLM 供應商」段落；前端聊天平台可以在 LINE 和 Discord 之間切換（甚至兩個同時開），見「切換／同時使用聊天平台」段落。

## 架構

```
LINE 群組 ──(webhook)──▶ app/main.py ────────┐
                                              │
Discord 頻道 ──(gateway)──▶ app/discord_bot.py ┤
                                              ▼
                              app/commands.py（指令與遊戲邏輯，平台無關）
                                              │
                                              ▼
                                      app/keeper.py（守密人邏輯，LLM 供應商無關）
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
- `app/dice.py`：COC7e 規則判定（d100、獎懲骰、成功等級、SAN）
- `app/models.py`：角色卡／聊天室狀態資料結構與快速生成（3d6 法）
- `app/pdf_loader.py`：抽取上傳 PDF 的文字內容（用 PyMuPDF 處理圖文混排版面，圖片偏多的頁面會嘗試 OCR 備援）
- `app/state.py`：以 JSON 檔案保存每個聊天室的遊戲狀態（檔名依平台加前綴，例如 `line-group-xxx.json`、`discord-channel-xxx.json`，避免兩邊 ID 撞在一起）

想只用 LINE、只用 Discord、還是兩個都開，完全取決於你要不要啟動哪個入口（`app/main.py` 用 `uvicorn` 跑、`app/discord_bot.py` 直接 `python -m` 跑），兩者可以同時執行，互不影響，因為狀態檔案已經照平台分開命名。

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
3. 下面出現的 **Bot Permissions** 至少勾選 **Send Messages**、**Read Message History**、**Attach Files**（讀取劇本 PDF 附件需要）。
4. 頁面最下面會生成一個邀請連結，複製起來，用瀏覽器打開，選擇要加入的伺服器、授權完成。

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

### 切換 LLM 供應商：Claude 或 Gemini

`.env` 裡的 `LLM_PROVIDER` 決定守密人由誰扮演，兩邊共用同一套遊戲邏輯（工具定義、規則判定、狀態管理都在 `app/keeper.py`），只有 `app/providers/` 底下各自的介面卡不一樣，隨時可以改 `.env` 切換，不用動到程式碼。

- **`LLM_PROVIDER=anthropic`**（預設）：用 Claude，走 `app/providers/anthropic_provider.py`，需要 `ANTHROPIC_API_KEY`。有做 prompt caching（見下面「已知限制」的說明），劇本內容重複的部分幾乎不會重複計費。這是目前唯一實際跑過真實 API 驗證過的路徑。
- **`LLM_PROVIDER=gemini`**：用 Google Gemini，走 `app/providers/gemini_provider.py`，需要 `GEMINI_API_KEY`（去 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 申請）。Gemini Flash 系列通常比 Claude 便宜不少，如果主要考量是成本，這是值得一試的選項。
  - **請注意**：這條路徑是照 Google 官方 `google-genai` SDK 文件寫的（手動 function-calling 迴圈：`client.models.generate_content` + `FunctionDeclaration`/`Tool`），程式碼結構跟工具 schema 都用真的 SDK 型別測過可以正常建構，但因為手上沒有 Gemini API key，**沒有實際打過一次真的 API 呼叫**驗證端到端行為。Google 的 Gen AI SDK 這一兩年變動蠻快的，如果切過去發現守密人完全沒反應或行為怪怪的，先去對一下 `app/providers/gemini_provider.py` 裡用到的屬性名稱（`response.function_calls`、`response.text`、`Part.from_function_response` 等）跟當時最新的 SDK 文件是否還一致，再懷疑是遊戲邏輯本身的問題。
  - Gemini 目前沒有做 prompt caching（Google 那邊叫 context caching，跟 Anthropic 的做法不同、而且門檻可能要幾萬 token 起跳），劇本內容會整包重新送——如果之後真的固定用 Gemini，這是下一個值得補的優化。
  - `GEMINI_MODEL` 預設值請自己去 [ai.google.dev](https://ai.google.dev) 核對當下實際可用的 flash 模型名稱再決定要不要改，模型 id 會隨時間變動。

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

> ngrok 免費版每次重啟網址都會變，記得每次都要回 LINE Developers Console 更新 Webhook URL。之後若要長期使用，建議换成正式主機（Render / Railway / Fly.io / 自己的雲端主機），屆時只要把 uvicorn 換成常駐服務、Webhook URL 換成正式網域即可，程式碼不用改。

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
     Bot 會讀一次劇本內容，找出裡面明確寫出的預製調查員並列出清單（第一次呼叫要花幾秒鐘讓 Claude 讀劇本，之後會直接用快取的結果）。看到想要的之後：
     ```
     /coc usepregen 編號 [自訂名稱]
     ```
     就會直接把那位預製角色登記成你的角色，不自訂名稱就沿用劇本原本的名字。
3. 建好角色後，直接在群組裡打字描述行動或對話（不用加任何指令），守密人就會接手描述場景、要求擲骰、更新 HP/SAN。
   - 有些資訊守密人會**私訊**給特定玩家，而不是公開在群組裡（例如：只有你發現的線索、秘密檢定結果、私人物品內容），會用「🤫（私訊）」開頭。私訊走的是 LINE/Discord 的個人對話，**前提是那位玩家已經把 Bot 加為好友（LINE）或允許伺服器成員私訊（Discord）**——沒有的話私訊會送不出去，而且目前是靜默失敗，不會在群組裡另外提示。
   - 如果劇本內建角色卡有寫「秘密目標」（例如 FBI 探員的目標是找到某人、骨董商想搞清楚某件事），用 `/coc pc` 或 `/coc usepregen` 建立那個角色的當下，會立刻私訊告訴那位玩家他的秘密目標，之後也只有守密人自己知道，不會出現在公開的角色卡（`/coc sheet`）或群組對話裡；守密人後續會透過劇情委婉引導，不會直接講白。
4. 打起來的時候，守密人通常會自己判斷該開戰、依序處理每個人的回合，**嚴格按照 DEX 排出的先攻順位進行**——DEX 不同的人行動跟敘述都要照順序來，只有 DEX 剛好相同的人才會敘述成同時行動（細節見下面「戰鬥」段落）；也可以自己手動控制：
   - `/coc combat start`：開始戰鬥，依目前角色的 DEX 排出先攻順位
   - `/coc combat addnpc 名稱 DEX HP`：加入一個敵人
   - `/coc combat status`：查看目前回合數、先攻順位、每個人的 HP，以及輪到誰
   - `/coc combat next`：推進到下一位的回合
   - `/coc combat damage 名稱 增減量`：調整某人的 HP（受傷用負數，例如 `-5`；治療用正數）
   - `/coc combat end`：結束戰鬥
   - 玩家臨時需要離開時，輸入 `/coc away` 標記暫離，戰鬥中會自動跳過該角色的回合（顯示「（暫離）」），不會卡住整團等他；回來後輸入 `/coc back` 恢復正常。
5. 其他指令：
   - `/coc sheet`：看自己的角色卡
   - `/coc status`：看目前劇本與所有人狀態
   - `/coc setskill 角色名 技能名 數值`：手動修正自己角色的技能值
   - `/coc away`／`/coc back`：標記暫離／回來（見上）
   - `/roll 1d100`、`/roll 3d6+2`：純擲骰，不經過守密人
   - `/coc newgame`：清空重來
   - `/coc end`：結束這一局（角色與紀錄仍保留）
   - `/coc help`：顯示指令列表

## 已知限制 / 之後可以擴充的方向

這些都是為了先做出一個能玩的 MVP，刻意先砍掉的範圍。想擴充哪一項都可以直接跟我說，下面附上為什麼會有這個限制、目前有什麼權宜作法、以及之後要補的話大概要改什麼地方。

### 1. 角色建立：三種方式，各有取捨

- **正式規則是怎樣**：COC7e 官方建角流程是先擲出 8 項屬性（STR/CON/DEX/APP/POW 用 3d6×5，SIZ/INT/EDU 用 (2d6+6)×5），接著把 `EDU×20` 當作「職業技能點數」，由玩家自己決定要分配到哪些跟職業相關的技能上；再把 `INT×10` 當作「興趣技能點數」，同樣自由分配到任何技能。很多劇本也會附上已經做好的預製調查員（pregens）給團隊直接使用。
- **這個專案現在怎麼做**：三種都做了，而且 `/coc pc` 快速生成現在會參考「這份劇本」的內建角色卡，不是完全套用固定表：
  - `/coc pc` 快速生成：沒指定的話用一張寫死的「通用職業技能加值表」（記者、私家偵探、醫生、教授、警察、骨董商、神職人員、流浪漢、藝術家、海洋生物學家、FBI探員），一行指令完成。**如果先跑過 `/coc pregens`**，`state.pregens` 裡會快取這份劇本實際附帶的角色卡；這時候 `/coc pc 角色名 職業` 只要職業名稱能對得上劇本裡任一位預製角色的職業（模糊比對，例如劇本原文是英文 "Artist"，抽取時會請 Claude 順便翻成「藝術家」），就會直接套用**那份劇本自己的技能數值**當加值，而不是通用表；沒有輸入 `/coc pc`（不帶職業）或用法錯誤時，提示訊息也會動態列出目前這份劇本有哪些職業可選。
  - `/coc create` + `/coc alloc`：`app/creation.py` 實作了完整的擲屬性 → 算點數 → 自己分配的流程，跟規則書一致，只是每次加點都要打一行指令，比實體桌邊建角慢一些。
  - `/coc pregens` + `/coc usepregen`：`app/pregen_extractor.py` 會請 Claude 掃一次已載入的劇本文字，把裡面明確寫出的角色卡（屬性、技能、背景）抽成結構化資料供選用；規則是「只回報文本裡寫的數值，不自己編」，缺漏的欄位（例如劇本沒寫 MP）才會用公式補上。抽取結果會快取在 `state.pregens`，**換一份新劇本上傳時會自動清空**，不會把舊劇本的角色卡誤用到新劇本上（這是實測時真的發生過的問題，已修正）。
- **還是有的限制**：
  1. `/coc create` 的技能點數分配沒有做「哪些技能才算你這個職業的『職業技能』」這層限制（正式規則職業點數理論上只能點在職業清單內的技能），目前是完全自由分配到任何技能，算是刻意放寬、換取指令操作的簡單。
  2. `/coc pc` 要吃到「這份劇本自己的職業」，前提是**已經手動跑過一次 `/coc pregens`**——沒有自動在背景偷跑這個查詢，因為那是一次額外的 Claude 呼叫，不想讓每次 `/coc pc` 都多花錢、多等待；沒跑過的話 `/coc pc` 就只會顯示通用職業表。
  3. 目前沒有做「技能點數用完前禁止有人搶先完成」之類的多人協調，都是各自輸入各自的建角流程，互不影響。
- **之後要擴充的話**：可以把 `/coc create` 的分配過程做成更友善的引導式對話（例如列出職業相關的技能清單讓玩家直接選），或是讓 `/coc pregens` 支援劇本裡有多套預製角色組合時的篩選。

### 2. 正式的戰鬥輪次系統

- **正式規則是怎樣**：COC7e 戰鬥有明確流程——依 DEX（必要時比 POW）排出先攻順序、每輪每個角色輪流宣告動作（攻擊／閃避／使用道具等）、命中後算傷害與傷害加值、達到一定傷勢要做重傷判定、HP 歸零要做死亡/瀕死流程等，是一套有嚴謹順序的子系統。
- **這個專案現在怎麼做**：`app/combat.py` 實作了一個獨立的 `CombatState`，記錄先攻順位（依 DEX 排序）、目前是第幾輪、輪到誰、每位戰鬥員（玩家角色與 NPC）的 HP。守密人偵測到「打起來了」的場面時會自己呼叫 `start_combat`／`add_npc_to_combat` 開戰，每處理完一位戰鬥員的行動就呼叫 `advance_combat_turn` 推進，血量變化呼叫 `damage_combatant`——這些現在都是程式邏輯在管，不是純粹交給 LLM 自由判斷；玩家也可以用 `/coc combat start`／`addnpc`／`status`／`next`／`damage`／`end` 指令直接手動操作、跟守密人共用同一份戰鬥狀態。
- **還是有的限制**：
  1. 先攻順序是「開戰當下依 DEX 排一次」，戰鬥中途沒有做「DEX 並列時比對抗」或「某些能力可以打斷先攻」之類的細節規則，都是簡化的靜態排序。
  2. 沒有做傷害加值骰（Damage Bonus）自動套用到武器傷害、也沒有重傷（Major Wound）、擊倒／昏迷這些狀態判定，這些還是靠守密人敘事上自行拿捏，只是 HP 數字本身是準的。
  3. NPC 的 DEX／HP 目前是守密人依劇本內容合理估算（劇本沒寫明的話會抓一般人類等級的數值），不是套固定的怪物數值表。
- **之後要擴充的話**：可以把武器傷害加值、重傷判定、擊倒/昏迷規則做成對應的工具函式，讓 Keeper 呼叫後直接拿到規則正確的結果，而不是自己描述時憑感覺套用。

### 3. 圖文混排的頁面（手卡／地圖／插圖）可能抽不完整，整份純掃描 PDF 沒裝 OCR 就讀不出來

- **現在怎麼做**：`app/pdf_loader.py` 改用 PyMuPDF 逐頁抽文字，閱讀順序比單純的文字圖層讀取器更穩，多欄排版、文字繞著插圖排的頁面通常都抽得出正確內容。針對「文字很少但頁面有圖片」的頁面（門檻是 200 字，含很多手卡、地圖、印章這類把文字刻進圖片裡的內容），會把那一頁轉成圖片，優先請 Claude **用視覺直接讀圖**（有 `ANTHROPIC_API_KEY` 才會走這條路），讀不到才退回本機 `tesseract` OCR；上傳完成後 Bot 也會列出哪幾頁被判定「文字偏少」，提醒你自己核對。
  - **平面圖／地圖是特別驗證過的案例**：純文字抽取對平面圖幾乎注定失敗——房間名稱在頁面上是 2D 排列的，文字抽取只能拉成一維序列，「進門右手邊第一個房間」這種相對位置關係在抽取過程就丟失了。這不是理論推測：實測時真的發生過，玩家說進門右手邊該是寢室，守密人（讀到的是打亂順序的房間名稱清單）卻說成廚房。改成請 Claude 直接看圖描述空間佈局後，重新測同一頁面，正確重建出了完整動線（正門進去左手邊臥鋪房、右手邊書房、走廊底端連接燈塔），而且抓出了純文字抽取完全不可能拿到的細節（走廊盡頭有個染血的通道，暗示案發地點）。
  - 一份 43 頁的真實劇本裡，符合「文字偏少」門檻的頁面高達 24 頁（角色卡、地圖、插圖），這些頁面用 6-12 條並行連線一起處理，但因為受 Anthropic 那端速率限制影響，整體還是要跑上將近一分鐘——所以上傳 PDF 時 Bot 會先回一句「收到了，正在讀取劇本內容」的立即回覆，等處理完才用另一則訊息公布結果，而不是讓你對著沒反應的畫面等一分鐘懷疑 Bot 是不是掛了（細節見下面「LINE reply token 的 60 秒限制」那條）。
- **還是有的限制**：
  1. 沒有 `ANTHROPIC_API_KEY`（例如你 `LLM_PROVIDER=gemini` 又沒填 Anthropic 金鑰）時，圖片理解這步會直接跳過，退回本機 `tesseract` OCR；OCR 沒裝或語言包不齊全時，該頁就只剩下原本抽到的少量文字。
  2. 如果整份 PDF 從頭到尾都是掃描頁（完全沒有文字圖層），又沒有 Anthropic 金鑰也沒裝 OCR，還是會直接抽不出任何內容，Bot 會回覆「這份 PDF 抽不出任何文字內容」。
  3. 圖片理解花的是你自己 Anthropic 帳號的用量（一份劇本可能觸發十幾到二十幾次呼叫），雖然單次都不貴，但劇本圖片越多，上傳時花的錢跟等待時間就越多。
- **現在的權宜作法**：確保 `.env` 裡有填 `ANTHROPIC_API_KEY`（就算 `LLM_PROVIDER=gemini` 也一樣，這步驟目前固定用 Anthropic），就能吃到圖片理解的完整效果；如果真的沒有金鑰，退回裝 `brew install tesseract tesseract-lang`，效果會差一截但總比沒有好。
- **之後要擴充的話**：讓圖片理解也支援 Gemini（目前寫死用 Anthropic，跟 `LLM_PROVIDER` 設定無關），或是把「這頁是不是平面圖」的判斷做得更精準，避免對純插圖頁也跑一次比較貴的圖片理解呼叫。

### LINE reply token 的 60 秒限制（PDF 上傳現在會分兩則訊息回覆）

LINE 的 reply token 只能用一次、而且**收到 webhook 後 60 秒內沒用掉就失效**（官方文件寫的，不是猜的）。PDF 上傳現在因為要對「文字偏少」的頁面逐一做圖片理解，即使並行處理，一份圖片較多的劇本輕鬆就會超過 60 秒——這代表如果程式傻傻等處理完才回覆，reply token 早就過期了，玩家會完全收不到「劇本已載入」的確認訊息，卻也不會有任何錯誤提示，看起來就像 Bot 沒反應。

修法：`app/commands.py` 的 `handle_pdf_upload` 現在拆成兩個回覆管道——收到檔案先立刻用 `reply`（reply token）回一句「收到了，正在讀取...」，實際處理完再用 `push`（LINE push message API，沒有 60 秒限制，但需要知道要推給誰）送出真正的結果。Discord 沒有這個限制，兩個管道直接指向同一個 `channel.send`。這個修法有實測過：真實劇本處理耗時 62 秒，確認立即回覆準時送達、最終結果確實走 push 送達，換算下來完全在舊的純 reply 設計會失敗的時間點之後。

唯一要注意的權衡：push message 會計入 LINE 的付費配額（reply 不會），但這裡只有上傳 PDF 這個低頻動作會觸發一次，不影響「Reply 免費不限量」對日常遊玩的結論。

### 4. 本機測試階段，ngrok 網址每次重啟都會變

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
