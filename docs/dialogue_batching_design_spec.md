# 設計規格：玩家訊息批次合併送 AI（Dialogue Batching）

> 適用範圍：`main` 分支（`app/commands.py`／`app/keeper.py` 單檔架構，非 `main_v2`）。
> 僅涵蓋 Discord（`app/discord_bot.py`）；LINE（`app/main.py`）維持凍結，不在本次範圍內。

## 目標

目前每一則 Discord 玩家訊息都會各自觸發一次 `keeper.run_turn` → 一次 AI 呼叫。當多位玩家在短
時間內連續發言時，後面的訊息會卡在既有的 `get_keeper_turn_lock`／`get_keeper_priority_gate`
（`app/locks.py`）後面排隊，一則一則輪流跑，等待時間隨排隊人數線性增加。

本次要讓「Keeper 正在處理 A 的時候，B/C/D 在這段時間內送進來的訊息」改成暫存，等 A 處理完後，
把暫存的 B/C/D 合併成**一次** AI 呼叫一起處理，而不是各自再跑一次——一次處理一組訊息預期比
一次處理一句話的總延遲更低，且 AI 呼叫次數（成本）也會下降。

## 範圍與非目標

本期包含：

- 每個 conversation（= 每個 Discord 頻道，見下方「現況」）維護一個玩家訊息批次佇列。
- 修改 `keeper.py` 的單一發言者呼叫路徑，改為可接受多位發言者的一批訊息，合併成一次 AI 呼叫。
- AI 回覆中對不同角色分別要求擲骰時，各自產生正確歸屬的擲骰按鈕（`coc_check:{conversation_id}:
  {owner_id}:{option}`）——這部分**不需要新增程式邏輯**，見下方「現況」第 3 點，現有機制已經支援。
- 明確排除 KP 控場訊息進入玩家批次（見下方「污染防護」）。
- `tests/test_message_batching.py`，涵蓋批次合併、逾時強制送出、KP 插隊、空批次/單則批次的
  回歸行為。

本期不包含：

- 不新增「這句話是不是在跟 AI 說話」的語意分類器。現有唯一的硬過濾仍然只是 `_is_ooc_message`
  的 `@`／`<@` 開頭前綴（`app/discord_bot.py:35-37`）——這是既有行為，不在本次改動範圍，也不會
  因為批次化而變得更寬鬆或更嚴格。
- 不改動 `get_keeper_priority_gate` 的 KP 優先權邏輯本身（`app/locks.py:46-90`）——KP 永遠優先
  處理、永遠不與玩家訊息合併，本次只是在既有規則上加一層「玩家端的合併」。
- 不改動 LINE（`app/main.py`）。
- 不影響 `main_v2` 分支。

## 現況

### 1. 訊息入口與呼叫鏈

`app/discord_bot.py:406` 的 `on_message` 收到訊息後，排除 bot 自己與 OOC 訊息
（`_is_ooc_message`），非附件類訊息會呼叫 `commands.handle_text_message(...)`
（`discord_bot.py:502`），最終落到 `app/commands.py:1373` 的
`_handle_ordinary_text_message_locked`，呼叫：

```python
# app/keeper.py:1647
keeper.run_turn(state, user_id, display_name, text, resolved_location, speaker_role)
```

**一則 Discord 訊息 → 一次 `run_turn` 呼叫 → 一次 AI 呼叫**，`run_turn` 的參數就假設了單一發言者
（單一 `user_id`／`text`）。

### 2. conversation_id 的範圍

`_conversation_id(channel_id) = f"discord-channel-{channel_id}"`（`discord_bot.py:54`）——
**每個頻道一個 conversation_id**，整個 session 期間固定不變，不是每則訊息或每次 AI 呼叫才產生
一個新的。也就是說，同一頻道裡不同玩家的所有發言，本來就共用同一個 conversation_id，批次合併
不需要重新設計 conversation_id 的產生方式。

### 3. 擲骰按鈕已經支援多位角色

`app/discord_bot.py:182` 的 `_post_check_buttons` 會遍歷 `state.pending_checks`（一個以
`owner_id` 為 key 的 dict）裡**所有**待處理的檢定，逐一貼出按鈕——它並不假設一次只有一位發言者。
`pending_checks[target_char.owner_id]`（`keeper.py:915`／`954`／`978`／`1014`）是 AI 執行
`skill_check` 工具呼叫時，根據角色名稱查出來的 owner_id 設定的，不是從呼叫 `run_turn` 時傳入的
`user_id` 帶入。

換句話說：**只要 AI 在一次回覆裡對不同角色分別呼叫 `skill_check` 工具，現有機制就已經會產生各自
正確歸屬的按鈕**——這正是原始構想裡「設計得好，應該有可行性」的部分，本次不需要改動這一段。

真正需要補的是下一點：目前 AI 一次只會收到一位發言者的輸入，所以自然也只會處理一位角色的意圖；
要讓 AI 在一次呼叫裡看到多位發言者、進而可能對多位角色分別要求檢定，需要先解決「怎麼把多人的話
一起餵給 AI」。

### 4. 真正的缺口：單一發言者格式化

`app/keeper.py:1581` 的 `_format_turn_message(speaker_name, message_text, speaker_role)`：

```python
f"{speaker_name}：{message_text}"
```

只接受單一發言者。`_commit_turn_result` 目前也是每個 turn 記一組 `user`/`assistant` 歷史紀錄。
這是本次唯一需要新增邏輯的地方。

### 5. 既有的並發控制（`app/locks.py`）

- `get_conversation_lock`：整個訊息處理過程持有的粗粒度 per-conversation 鎖。
- `get_keeper_turn_lock`：序列化同一 conversation 的 AI 呼叫（同一時間只有一次在跑）。
- `get_keeper_priority_gate(conversation_id, is_kp)`：`_KeeperPriorityGate`
  （`locks.py:46-90`），FIFO 佇列，KP 與玩家分開排隊，**KP 永遠優先被放行**，只有在該
  conversation 已設定 KP 助手時才啟用。

**這個佇列機制已經存在**——目前的行為是：B 訊息進來時若 A 正在處理，B 會卡在 gate 後面等待，
等輪到它時「各自」跑一次 `run_turn`。本次要做的改動，就是把「等待期間陸續進來的多則訊息」從
「各自排隊、各自呼叫」改成「合併成一次呼叫」，其餘的鎖與優先權機制原封不動沿用。

### 6. KP 控場訊息的判斷方式

沒有獨立的控場頻道。KP 助手是 state 上的一個 `user_id` 旗標
（`state.kp_assistant_user_id`），`speaker_role`（`"player"` 或 `"kp_assistant"`）由
`_handle_ordinary_text_message_locked`（`commands.py:1396`）依此旗標判斷。KP 訊息會走不同的
prompt／工具白名單（`_KP_ASSISTANT_ALLOWED_TOOL_NAMES`、`_tools_for_speaker_role`），且只有
明確的 `!` 前綴（`_parse_kp_manual_canon_trigger`）才會寫入 canon。

### 7. 既有測試慣例

最相關的是 `tests/test_keeper_priority_gate.py`、`tests/test_keeper_priority_integration.py`
——直接測試 `locks.py` 的 gate 與 `commands.handle_text_message`，慣例是 stdlib `unittest` +
`asyncio.run`（不用 `pytest-asyncio`），用 `StateStorePatch` context manager mock
`load_state`/`save_state`、`ReplyCollector` 收集回覆、`FakeKeeperRunner` stub 掉
`keeper.run_turn`。本次新測試會沿用同一套慣例。

## 設計

### 批次視窗：依附在既有的忙碌狀態上，而不是獨立的固定視窗

原始構想是「Keeper 正在處理 A 時，B/C/D 在接下來 X 秒內送進來就暫存」。這與其做成一個獨立於
現有鎖機制之外的固定時間視窗（debounce），不如直接依附在 `get_keeper_turn_lock` 現有的忙碌狀態
上，理由：現有的排隊/優先權機制已經正確處理了「同一時間只能有一次 AI 呼叫在跑」與「KP 永遠優先」
兩件事，重新做一個平行的計時器容易跟現有機制打架（例如兩邊對「現在算不算忙碌」判斷不一致）。

具體規則（每個 conversation 各自獨立）：

- **情境 A：Keeper 目前閒置**，某玩家訊息進來——不要立刻呼叫 AI，而是開啟一個
  `BATCH_WINDOW_SECONDS`（預設 **3 秒**，經 `app/config.py` 環境變數 `BATCH_WINDOW_SECONDS`
  可調）的收集視窗。視窗期間內再進來的玩家訊息，加入同一批。視窗到期後，把這批訊息**合併成一次**
  AI 呼叫送出。
  - 這代表「只有一則訊息」的最常見情境，仍然會多等最多 3 秒才送出——這是本次設計主動接受的延遲
    換取合併機率的取捨，需要 Marco 確認是否可接受（見文末「待決策事項」）。
- **情境 B：Keeper 目前忙碌**（一次 AI 呼叫進行中），玩家訊息進來——直接加入「下一批」的佇列，
  不再各自排隊等著跑單獨一次。**下一批的收集視窗，在目前這次 AI 呼叫結束後才開始計時**（而不是
  訊息進來當下就開始算），這樣才能真正涵蓋「A 處理期間陸續進來的 B/C/D」，不會因為 A 處理太久而
  讓視窗提早關閉、漏掉還沒送到的訊息。
  - 為避免單一批次無限期等待（例如玩家們陸續斷斷續續發言，視窗一直被重新觸發），加上
    `MAX_BATCH_WAIT_SECONDS`（預設 **8 秒**，同樣可透過環境變數調整）作為封頂：一旦某則訊息在
    佇列裡等待超過這個上限，不論視窗有沒有到期，立刻強制送出目前累積的整批。
- **KP 插隊（preemption）**：若玩家批次視窗還開著（尚未送出）時，KP 助手發言進來——立刻把目前
  累積的玩家批次送出（視為一次完整的批次呼叫），接著才處理 KP 的訊息（走現有的 solo 路徑，不
  進入任何批次）。這維持「KP 永遠優先、永遠不與玩家訊息混在同一次呼叫」的既有不變式
  （見現況第 5、6 點），只是新增「玩家批次不能無限期擋在 KP 前面」這條規則。

### 訊息封裝：比照 TCP/IP 封包的想法，逐則加上序號與發言者標頭

批次送給 AI 時，每則排隊中的訊息都帶著 `(seq, user_id, display_name, text, received_at,
speaker_role)`，格式化時比照現有 `_format_turn_message` 的「`發言者：內容`」風格，逐行列出、
不合併成一段連續文字，讓 AI 清楚看到這是 N 筆各自獨立的輸入：

```
[1] 小明：我要對著門開鎖
[2] 小華：哈囉我剛回來 XD
[3] 阿強：我掩護小明
```

`_format_turn_message` 改為 `_format_batch_messages(packets: list[QueuedMessage]) -> str`。
**當批次只有一則訊息時，輸出必須與現行 `_format_turn_message` 的結果逐字元相同**——這是刻意的
回歸保護：多數情況下（沒有排隊競爭）批次大小仍然是 1，不能因為改成批次架構而讓最常見情境的
prompt 內容產生變化、進而影響既有的 AI 回覆品質或既有測試的 snapshot。

`keeper.py` 新增 `run_batched_turn(state, packets, resolved_location, ...)`，取代
`commands.py:1373` 目前呼叫 `run_turn` 的呼叫點；`run_turn` 本身保留（batch size = 1 時內部
直接委派給它，或反過來由 `run_turn` 變成 `run_batched_turn` 的單訊息版本，實作時二擇一，以
維持程式碼只有一份格式化/呼叫邏輯為原則）。

### 污染防護：離題訊息混入批次的風險

Marco 提出的疑慮——一堆訊息裡有人打了一句沒有 `@` 的閒聊，AI 可能誤解語意讓整串跑掉。這個風險
**在單則訊息時代本來就存在**（現況第 7 點／既有的 `_is_ooc_message` 只過濾 `@` 開頭，其餘一律
送給 AI），批次化不會製造新的風險類型，但會放大影響範圍：原本一句離題話最多讓那一次回覆跑掉，
批次化後可能連帶讓同批的其他 N 位玩家的正經發言都被誤判。

本次採取的因應方式（不做語意分類器，理由見「範圍與非目標」）：

1. **逐行標號＋發言者屬性化**（上一節的封裝格式）本身就是主要防線：把每則訊息清楚地列成獨立的
   `[序號] 發言者：內容` 行，而不是揉成一段連續敘述，讓 AI 更容易把離題的那一行當成獨立、可以
   單獨略過或簡短回應的輸入，而不會把它的語意套用到其他行的動作判定上。
2. **System prompt 需要新增明確指示**：告知 AI「同一批可能包含來自不同玩家、彼此無關的輸入，
   其中可能有純聊天、與當前場景無關的內容；請個別判斷每一行，不要讓離題內容影響其他行的判定」。
   這是 prompt 調整，非程式邏輯，但是本次設計不可省略的一部分，需要在實作階段連同 spec 一併
   撰寫、並用測試腳本驗證批次 prompt 的實際內容包含這段指示。
3. `_is_ooc_message` 的 `@` 前綴過濾維持不變，仍然是唯一的硬性排除機制。

### 歷史紀錄（`_commit_turn_result`）

批次呼叫產生的一組「使用者輸入（多行）＋ AI 回覆（一則）」，建議記為**一筆**歷史紀錄（`user`
side 是上述多行合併文字，`assistant` side 是 AI 的回覆），而不是拆成 N 筆各自獨立的
user/assistant 配對——這樣歷史紀錄重播時，看到的內容會跟 AI 當初實際看到的輸入一致。这點會影響
未來任何讀取歷史紀錄的功能（目前查了一輪沒有發現讀取歷史紀錄做逐則玩家歸因的既有功能），列在
下方待確認事項。

## 待決策事項（需要 Marco 在 review 時定案）

1. `BATCH_WINDOW_SECONDS`（預設 3 秒）、`MAX_BATCH_WAIT_SECONDS`（預設 8 秒）這兩個數字是否
   合適？是否要做成可調的環境變數（目前規劃是要）？
2. 情境 A（Keeper 閒置時第一則訊息也要多等 3 秒視窗）帶來的「單人發言延遲增加」是否可接受？
   或是否要改成「Keeper 閒置時第一則訊息立即送出（維持現有零延遲），只有『忙碌期間陸續進來的』
   才進入批次」——這樣情境 A 就不需要視窗，只有情境 B 才有等待，更貼近 Marco 原始描述的「Keeper
   正在處理 A 時，B/C/D 在接下來 X 秒內送進來」。**傾向採用這個修正版**，實際上等於把「情境 A
   的視窗」拿掉，只保留情境 B；本 spec 先把兩個選項都列出來，由 Marco 決定。
3. 歷史紀錄要記成一筆合併紀錄，還是拆成多筆——見上一節。
4. 是否需要批次「筆數」上限（例如最多合併 5 則），避免極端情況下單次 prompt 過長？目前規劃
   不另加筆數上限，只靠 `MAX_BATCH_WAIT_SECONDS` 間接限制，但可視需要新增。

## 測試計畫（`tests/test_message_batching.py`）

沿用 `test_keeper_priority_integration.py` 的慣例（stdlib `unittest`、`StateStorePatch`、
`ReplyCollector`、`FakeKeeperRunner`、`asyncio.run`）：

- 單一訊息（無競爭）情境下，批次路徑產生的 prompt 內容與現行 `_format_turn_message` 逐字元相同
  ——回歸測試。
- Keeper 忙碌期間陸續進來兩則以上玩家訊息，會在目前這次呼叫結束後合併成一次呼叫送出。
- `MAX_BATCH_WAIT_SECONDS` 到期時，即使視窗理論上還沒關閉，也會強制送出目前累積的批次。
- KP 訊息在玩家批次視窗開著時進來：先強制送出玩家批次（單獨一次呼叫），再處理 KP 訊息（單獨一次
  呼叫），兩者不會合併，且順序正確。
- 一次批次呼叫中，AI 對多位不同角色分別呼叫 `skill_check`，各自產生正確歸屬 owner_id 的擲骰
  按鈕（延伸既有 `_post_check_buttons` 的測試覆蓋）。
- `@` 開頭的 OOC 訊息不進入批次、也不送給 AI，行為與現行一致。
