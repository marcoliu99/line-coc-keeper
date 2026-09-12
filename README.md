# LINE COC7e 守密人 Bot

在 LINE 群組裡上傳一份《克蘇魯的呼喚》第七版（COC7e）劇本 PDF，就能讓 LLM 扮演守密人（Keeper），直接在群組裡跑團。規則判定（技能檢定、SAN 值、擲骰）由程式碼負責計算，LLM 負責讀劇本、敘事、決定什麼時候該擲骰。後端 LLM 可以在 Claude（Anthropic）和 Gemini（Google）之間切換，見下面「切換 LLM 供應商」段落。

## 架構

```
LINE 群組 ──(webhook)──▶ FastAPI (/callback) ──▶ app/keeper.py（守密人邏輯，供應商無關）
                                                        │
                                                        ▼
                                        app/providers/anthropic_provider.py
                                        app/providers/gemini_provider.py      ← LLM_PROVIDER 決定走哪一條
                                                        │
                                                        ▼
                                工具呼叫（擲骰／技能檢定／SAN／角色數值／戰鬥）
                                                        │
                                                        ▼
                                     data/groups/*.json （每個群組的角色卡、劇本、對話紀錄）
```

- `app/main.py`：LINE webhook 入口與指令處理（`/coc`、`/roll`）
- `app/keeper.py`：守密人的遊戲邏輯（系統提示詞、工具定義、工具執行），不綁定特定 LLM
- `app/providers/anthropic_provider.py`：Claude（Anthropic Messages API）介面卡，含 prompt caching
- `app/providers/gemini_provider.py`：Gemini（google-genai SDK）介面卡
- `app/combat.py`：正式戰鬥輪次狀態機（先攻順位、回合、HP）
- `app/creation.py`：互動式建角流程（擲屬性、分配職業/興趣技能點數）
- `app/pregen_extractor.py`：從劇本文字中抽取內建的預製調查員
- `app/dice.py`：COC7e 規則判定（d100、獎懲骰、成功等級、SAN）
- `app/models.py`：角色卡／群組狀態資料結構與快速生成（3d6 法）
- `app/pdf_loader.py`：抽取上傳 PDF 的文字內容（用 PyMuPDF 處理圖文混排版面，圖片偏多的頁面會嘗試 OCR 備援）
- `app/state.py`：以 JSON 檔案保存每個群組的遊戲狀態

## 第一步：申請 LINE Messaging API Channel

這一步是在 LINE 官方後台建立一個「機器人身分」，之後你的程式才有東西可以連。整個過程都在網頁上點一點，不需要寫程式，大約 10 分鐘。

### 1. 登入 LINE Developers Console

前往 [https://developers.line.biz/console/](https://developers.line.biz/console/)，用你平常用的 **LINE 帳號**登入（跟你手機上的 LINE App 是同一組帳號）。第一次登入可能會要求你補填開發者名稱、Email 等基本資料，照著填完即可。

### 2. 建立 Provider（開發者／公司身分）

- Provider 是一個「誰擁有這個機器人」的分類容器，底下可以放多個 Channel。個人玩票用途的話，名稱隨便取，例如 `我的跑團`、`marcoliu`。
- 登入後若還沒有任何 Provider，畫面會直接引導你建立一個：填名稱 → 按 **Create**。
- 若已經有 Provider，在左上角的下拉選單可以選擇，或按 **Create a new provider** 再建一個新的。

### 3. 建立 Messaging API Channel

- 選定 Provider 後，點 **Create a Messaging API channel**（或在 Provider 頁面裡點 Channels 分頁旁的建立按鈕）。
- 會出現一個表單，需要填：
  - **Channel icon**：Bot 的大頭貼圖片，選填，可以之後再補。
  - **Channel name**：Bot 顯示名稱，也就是玩家在 LINE 群組裡看到「誰在說話」的名字，例如 `COC守密人`（20 字以內，且不能包含 LINE 這幾個字）。
  - **Channel description**：簡短說明，隨便寫，例如「COC7e 跑團守密人」。
  - **Category / Subcategory**：LINE 要求選一個產業分類，找不到完全符合的就選接近的（例如「Entertainment」之類），這欄不影響功能。
  - **Email address**：預設會帶入你的登入信箱，不用改。
- 勾選底下的服務條款同意欄位，按 **Create**。
- 建立完成後會跳出一個確認畫面，按 **OK** 進入這個 Channel 的管理頁面。

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

### 6. 把 Bot 加好友並拉進群組

- 還是在 **Messaging API** 分頁，畫面上方會有一個 QR Code（有時候在 Basic settings 分頁也看得到，標示為 **Bot info** 或 **QR code**）。
- 用手機 LINE App 的「加好友」功能掃描這個 QR Code，把 Bot 加為好友（加好友這步驟是必要的，LINE 的機制上機器人一定要先被加好友才能被拉進群組）。
- 打開你要跑團的 LINE 群組 → 點右上角的群組設定（人形圖示或選單）→ 找「邀請」→ 從好友清單裡選到剛剛加的 Bot → 邀請它加入群組。

到這裡，LINE 這邊的設定就完成了，剩下的 Webhook URL 要等你把伺服器跑起來、透過 ngrok 拿到對外網址之後再回來補。

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

## 第三步：本機啟動 + ngrok 對外

啟動 FastAPI 伺服器：

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
4. 打起來的時候，守密人通常會自己判斷該開戰、依序處理每個人的回合（細節見下面「戰鬥」段落）；也可以自己手動控制：
   - `/coc combat start`：開始戰鬥，依目前角色的 DEX 排出先攻順位
   - `/coc combat addnpc 名稱 DEX HP`：加入一個敵人
   - `/coc combat status`：查看目前回合數、先攻順位、每個人的 HP，以及輪到誰
   - `/coc combat next`：推進到下一位的回合
   - `/coc combat damage 名稱 增減量`：調整某人的 HP（受傷用負數，例如 `-5`；治療用正數）
   - `/coc combat end`：結束戰鬥
5. 其他指令：
   - `/coc sheet`：看自己的角色卡
   - `/coc status`：看目前劇本與所有人狀態
   - `/coc setskill 角色名 技能名 數值`：手動修正自己角色的技能值
   - `/roll 1d100`、`/roll 3d6+2`：純擲骰，不經過守密人
   - `/coc newgame`：清空重來
   - `/coc end`：結束這一局（角色與紀錄仍保留）
   - `/coc help`：顯示指令列表

## 已知限制 / 之後可以擴充的方向

這些都是為了先做出一個能玩的 MVP，刻意先砍掉的範圍。想擴充哪一項都可以直接跟我說，下面附上為什麼會有這個限制、目前有什麼權宜作法、以及之後要補的話大概要改什麼地方。

### 1. 角色建立：三種方式，各有取捨

- **正式規則是怎樣**：COC7e 官方建角流程是先擲出 8 項屬性（STR/CON/DEX/APP/POW 用 3d6×5，SIZ/INT/EDU 用 (2d6+6)×5），接著把 `EDU×20` 當作「職業技能點數」，由玩家自己決定要分配到哪些跟職業相關的技能上；再把 `INT×10` 當作「興趣技能點數」，同樣自由分配到任何技能。很多劇本也會附上已經做好的預製調查員（pregens）給團隊直接使用。
- **這個專案現在怎麼做**：三種都做了。
  - `/coc pc` 快速生成：套一張寫死的「職業技能加值表」，一行指令完成，最快但不能自訂技能分配。
  - `/coc create` + `/coc alloc`：`app/creation.py` 實作了完整的擲屬性 → 算點數 → 自己分配的流程，跟規則書一致，只是每次加點都要打一行指令，比實體桌邊建角慢一些。
  - `/coc pregens` + `/coc usepregen`：`app/pregen_extractor.py` 會請 Claude 掃一次已載入的劇本文字，把裡面明確寫出的角色卡（屬性、技能、背景）抽成結構化資料供選用；規則是「只回報文本裡寫的數值，不自己編」，缺漏的欄位（例如劇本沒寫 MP）才會用公式補上。
- **還是有的限制**：
  1. `/coc create` 的技能點數分配沒有做「哪些技能才算你這個職業的『職業技能』」這層限制（正式規則職業點數理論上只能點在職業清單內的技能），目前是完全自由分配到任何技能，算是刻意放寬、換取指令操作的簡單。
  2. `/coc pregens` 抽取的準確度取決於劇本文字排版；如果角色卡是做成圖片（前面「PDF 抽取」段落提到的狀況），OCR 抽出來的內容可能不完整，用之前建議先 `/coc sheet` 核對一下數值。
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

- **現在怎麼做**：`app/pdf_loader.py` 改用 PyMuPDF 逐頁抽文字，閱讀順序比單純的文字圖層讀取器更穩，多欄排版、文字繞著插圖排的頁面通常都抽得出正確內容。針對「文字很少但頁面有圖片」的頁面（很可能是手卡、地圖、印章這類把文字刻進圖片裡的內容），會自動把那一頁轉成圖片丟給 OCR（`tesseract`，含繁中語言包）辨識，抽出來的文字會併回內文；上傳完成後 Bot 也會列出哪幾頁被判定「文字偏少」，提醒你自己核對，避免關鍵線索被漏掉卻沒人發現。
- **還是有的限制**：
  1. 沒有裝 `tesseract`（或裝了但語言包不齊全）時，OCR 那步會靜靜失敗、跳過，該頁只剩下原本抽到的少量文字，不會讓程式壞掉，但那頁的圖片內容就真的讀不到，只能自己核對後手動補在對話裡。
  2. 如果整份 PDF 從頭到尾都是掃描頁（完全沒有文字圖層、也沒有裝 OCR），還是會直接抽不出任何內容，Bot 會回覆「這份 PDF 抽不出任何文字內容」。
  3. OCR 辨識手寫字、花俏美術字、或解析度很低的掃描圖，準確率仍然有限，複雜的手卡建議還是自己核對一下辨識結果。
- **現在的權宜作法**：先照「第二步」裡的說明裝好 `brew install tesseract tesseract-lang`，大部分「內文文字＋圖片手卡」的排版就能直接用；如果整份都是掃描書頁，上傳前可以先用 Google 雲端硬碟的「用 Google 文件開啟」或 Adobe Acrobat 的「辨識文字」功能整份轉成有文字圖層的 PDF，再傳進群組。
- **之後要擴充的話**：可以把 OCR 的判斷條件做得更細（例如分析圖片本身的文字密度，而不只是看抽出文字的長度），或是把 OCR 換成準確率更高的雲端服務（Google Cloud Vision、Azure Document Intelligence 等）。

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
