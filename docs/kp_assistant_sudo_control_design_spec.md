# KP Assistant Sudo／代操作功能設計規格

## 0. Changeset 與整合基準

- 基準分支：`main_v2`
- 本次起始 changeset：`d0e4a6a`（建立 branch 時的 `origin/main_v2`）
- 實作前重新對齊的 `main_v2` changeset：`f6a5a1f`
- 本次結束 changeset：`f26c16a`（runtime implementation；後續文件修訂若有則以最新 HEAD 為準）
- 工作 branch：`feature/kp-assistant-sudo-control`
- 目標整合分支：`main_v2`

本文件先於 runtime implementation 建立。實作前必須先確認本文件的權限邊界、
指令格式與允許操作清單；未確認前不修改程式碼。

## 1. 問題與目標

目前 KP Assistant 可以透過一般訊息與 AI Keeper 討論主持事項，但手動
`/coc` 指令一律把發送者當成角色擁有者。當玩家暫時離線、需要由主持人代為
完成檢定，或 KP 要協助玩家切換／修正角色時，KP 只能請玩家自己輸入指令。

本功能提供類似 Unix `sudo` 的「代操作」模式：目前登記的 KP Assistant，或
具有 Discord `Keeper` role 的管理者，可以明確指定一名玩家，讓指定的玩家成為
本次操作的 effective subject。這不是冒充登入，也不是把角色所有權轉移給 KP；
它只在該次 command／gameplay turn 以 KP 權限代為執行，並留下可追溯的 actor 與
subject 紀錄。

成功條件：

- KP 可以用一個明確的 `/coc sudo` 指令操作指定玩家的角色。
- 遊戲 state mutation、pending check／Luck、角色查詢與 AI turn 都套用 target
  玩家，而不是 KP 自己。
- 權限判定永遠區分「誰發送」（actor）與「替誰操作」（subject）。
- 代操作不會把 KP 變成 player，也不會進入 KP Assistant 的 OOC prompt／tool
  allowlist；代操作的遊戲回合仍是 target player 的正式遊戲回合。
- `act` 的公開回覆必須明確標示「KP Assistant 代操作」。
- KP 不可透過 sudo 執行 target 的 `luck roll`，也不可代替 target 建立或認領角色。
- 每次成功、拒絕或失敗的 sudo 都有 structured audit event；log 中不直接暴露
  raw Discord user ID。
- 原本玩家自己輸入的所有 command 行為維持不變。

## 2. 名詞與身份模型

### 2.1 Actor 與 subject

```text
actor_user_id   = 實際在 Discord 發送 /coc sudo 的人
subject_user_id = 被代操作、其角色狀態被使用的人

正常玩家 command：actor == subject
KP sudo command：actor != subject，actor 是 KP／Keeper，subject 是玩家
```

兩個 ID 不可在 routing 中混用：

- actor 用來做 sudo authorization、audit 與權限拒絕訊息。
- subject 用來查找 active character、owner、pending check、Luck、map position，
  以及呼叫既有 player command handler。
- `speaker_role` 對 sudo gameplay turn 必須是 `"player"`，不能是
  `"kp_assistant"`；否則 AI 會錯誤套用 KP OOC prompt 與 KP tool allowlist。

建議新增不可變的內部 context：

```python
ActingContext(
    actor_user_id: str,
    subject_user_id: str,
    mode: Literal["self", "kp_sudo"],
    command: str,
)
```

正常 command 也使用 `mode="self"`，讓 handler 不需要依賴隱含的 global state。
此 context 不直接存入 `GroupState`；audit 只透過 structured log 與必要的公開
操作標記保存。

## 3. 使用者介面

### 3.1 指令格式

第一版採用明確的文字 command，不新增 Discord slash command API：

```text
/coc sudo <target> <player-command> [arguments...]
```

例子：

```text
/coc sudo <@123456789> sheet
/coc sudo <@123456789> check
/coc sudo <@123456789> luck hard
/coc sudo <@123456789> switch 小明
/coc sudo <@123456789> act 調查房間裡的書桌
/coc sudo <@123456789> away
/coc sudo <@123456789> retire 小明
```

`<target>` 第一版只接受可無歧義解析的 Discord mention：`<@123>` 或
`<@!123>`；測試與非 Discord adapter 可接受已驗證的 opaque user ID。不可用
顯示名稱模糊搜尋，避免同名玩家被代錯角色。

玩家本人也可以直接輸入 `/coc retire [角色名]` 退出自己的目前角色；KP
Assistant 的代操作只額外提供 `/coc sudo <target> retire [角色名]`，兩者共用同一個
active binding 解除流程。

`act` 是唯一的自由文字入口。它會把其後完整文字當作 target player 的一般
遊戲訊息，不把其中的 `/coc sudo` 再次解析成巢狀 sudo。

### 3.2 允許的 subject-scoped command

sudo 不應任意轉送所有 `/coc` command，而應有明確 allowlist：

| 類別 | 第一版允許 | 說明 |
|---|---|---|
| Gameplay | `act`、`check`、`luck decision` | 以 target 的 active character 執行正式遊戲流程；`luck` 只允許既有 pending Luck 的 `skip`／`regular`／`hard`／`extreme` 選擇 |
| 角色查詢 | `sheet`、`characters`、`pregen`、`pregens` | 查詢或預覽內容，subject 只影響角色／訊息歸屬 |
| 角色切換／資料 | `switch`、`setskill`、`setconnection` | 只可操作 target 擁有的角色 |
| 地圖／位置 | `where`、`enter`、`leavemap`、`showpage` | 位置與頁面套用 target；公開圖片仍遵守既有 visibility 規則 |
| 角色生命週期 | `away`、`back`、`retire` | KP Assistant 可代 target 暫離、回來或退出目前角色；`retire` 不刪除歷史資料 |

以下 command 第一版明確禁止透過 sudo 執行：

- `sudo`（禁止巢狀代操作）
- `luck roll`（LUCK 骰必須由玩家本人執行）
- `pc`、`create`、`alloc`、`usepregen`（角色建立／認領必須由玩家本人執行）
- `newgame`、`start`、`end`、`status`、`setpersona`、`era`、`index`
- `pdf`、`scenario`、`checkpoint`、`checkpoints`、`rollback`、`digest`、`digests`
- `combat` 與其他群組級／劇本級 administrative command

禁止清單不是把 sudo 權限交給 target；這些操作本來就不是「替某位玩家操作
角色」，必須走原本的 group-level authorization。未來若新增 player-scoped
command，必須先加入 registry／allowlist 與測試，不能因為 parser 能解析就自動
被 sudo 轉送。

## 4. 權限規則

1. actor 必須符合以下任一條件：
   - `state.kp_assistant_user_id == actor_user_id`；或
   - Discord adapter 傳入的 `is_keeper=True`。
2. actor 必須先脫離自己的 player character／建角流程，才能登記或執行 KP
   Assistant。這是 actor 自己的角色身分轉換，不可由這次 sudo 操作自動完成；
   對 target 執行 `/coc away` 或 `/coc retire` 不會改變 actor 的脫離要求。
3. 每次執行前都要在 conversation lock 內重新載入 state 並重新驗證 actor，不能
     只使用 router 進入前的 snapshot。
4. `SCENARIO_LIFECYCLE_KP_ONLY` 不控制 sudo；sudo 本身永遠是 KP／Keeper-only。
5. target 不可是目前的 KP Assistant。KP 的 OOC 身分與玩家角色身分仍互斥；
   sudo 第一版不提供角色建立／認領，因此 target 必須已經有 active character
   才能執行需要角色的操作。
6. target 的角色 ownership 不變。所有角色修改都必須維持
   `Character.owner_id == subject_user_id`，不能讓 actor 變成 owner。
7. 不合法 target、未知 command、禁止 command、巢狀 sudo、缺少 target active
   character 或既有 command guard 失敗時，回傳固定、可理解的錯誤，不執行部分
   mutation。
8. sudo 不能繞過既有安全 guard，例如：
   - `game_started` 對 `/coc usepregen` 的限制；
   - pending pregen LUCK 必須完成後才能切換劇本／角色；
   - 已有角色不可再次建立或認領不相容的角色；
   - pending check／Luck 必須屬於同一個 subject。

### 4.1 KP Assistant role transition

| 轉換 | 前置條件 | 行為 |
|---|---|---|
| player → KP Assistant | actor 已真正脫離 player character 與建角流程 | `/coc kp` 才能登記；不得由 sudo 自動完成 |
| player → KP Assistant（仍有角色） | 仍有 active character、角色綁定或 `creation_sessions` | 拒絕登記，要求先執行明確的角色脫離流程 |
| KP Assistant → player | 先執行 `/coc kp quit` | 清除 KP Assistant 身分後，才可依既有角色流程恢復 player 身分 |
| KP Assistant → target away | target 有 active character | `/coc sudo <target> away`，只標記 target 暫離，不改變 actor 身分 |
| KP Assistant → target retired | target 有 active character | `/coc sudo <target> retire`，解除 target 的 active player binding，但保留角色歷史 |

「退角」是解除 target 目前的 player binding，不是刪除角色資料；角色仍保留在
`characters_by_id`，供歷史、查詢或日後重新加入使用。`/coc away` 是可恢復的
暫離狀態，`/coc retire` 是明確退出目前角色的持久狀態。兩者都只能作用於
target，不得偷偷替 actor 完成 detach。若 target 尚有待玩家本人擲的 pregen
LUCK，retire 不得清除該 pending；否則 target 日後回來會繞過玩家擲骰規則。

## 5. Runtime flow

```text
Discord on_message
    │
    ├─ text == /coc sudo <target> <command> ... ?
    │       │ no → existing normal router，actor == subject
    │       │ yes
    │       ▼
    │  parse target mention + command allowlist
    │       │ parse failure / forbidden command
    │       └──────────────► fixed error + sudo.denied
    │
    ▼
  enter KP priority gate + conversation lock
    │
    ▼
  reload GroupState
    │
    ├─ actor is current KP Assistant or Discord Keeper?
    │       no ─────────────► fixed error + sudo.denied
    │
    ├─ actor detached from player role / target valid / target guard valid?
    │       no ─────────────► fixed error + sudo.denied
    │
    ▼
  create ActingContext(actor, subject, mode=kp_sudo)
    │
    ├─ player command → existing handler with effective user=subject
    └─ act → supervisor/keeper turn with user_id=subject,
             speaker_role="player", target active character
    │
    ▼
  state mutation / reply / target DM / image output
    │
    ├─ public result includes mandatory 「KP Assistant 代操作」 marker
    ├─ private information is sent to subject, never actor-only
    └─ sudo.completed or sudo.failed audit event
```

### 5.1 Lock 與回合

- sudo command 要走 KP priority gate，避免 KP 代操作在玩家回合前已排隊時造成
  競態；實際 state mutation 仍使用既有 conversation lock。
- `act` 必須沿用既有 Keeper turn lock、`turn_id`、`request_id` 與 post-turn
  maintenance 流程，但不把這次 turn 寫入 `kp_ooc_log`。
- `act` 的 `run_turn` 必須使用 target 的 active character、map location 與
  canonical player history；AI 不得把它當成 KP OOC instruction。
- direct command 的 handler 不應再取得同一把不可重入的 conversation lock；應由
  router 以既有分派層級持有一次 lock，沿用目前 command handler 的 convention。

### 5.2 流程表

| 階段 | 判定／動作 | 通過結果 | 拒絕結果 |
|---|---|---|---|
| 1. 識別 | 第一個 token 是 `/coc sudo` | 進入 sudo parser | 交給既有 normal router |
| 2. 解析 target | target 是 mention 或已驗證 opaque user ID | 建立 `subject_user_id` | `sudo.denied(reason=invalid_target)`，不讀寫 game state |
| 3. 解析 command | command 在 subject-scoped allowlist | 建立 `ActingContext` | `sudo.denied(reason=forbidden_command)` |
| 4. actor authorization | actor 是目前 KP Assistant 或 Discord Keeper | 進入 KP priority gate + conversation lock | `sudo.denied(reason=not_authorized)` |
| 5. role separation | actor 已脫離 player character／建角流程 | 繼續 target guard | `sudo.denied(reason=actor_role_conflict)`，要求先脫離 |
| 6. target guard | target 不是 KP、已有必要 active character、pending state 合法 | 呼叫既有 handler，effective user 使用 subject | 固定錯誤訊息；不得部分 mutation |
| 7. 執行 | `act` 使用 player role；其他 command 使用既有 handler | state／pending／output 套用 subject | `sudo.failed`，保留 exception 到 log，不把 exception 原文送到 channel |
| 8. 輸出 | public reply、target DM、圖片與按鈕依既有 scope | public reply 加「KP Assistant 代操作」標記；秘密只送 subject | output failure 依既有 reply error flow 處理 |
| 9. 完成 | 操作成功或受控拒絕 | `sudo.completed` 或 `sudo.denied` audit event | 未捕捉例外時 `sudo.failed` audit event |

### 5.3 Command decision table

| Input | 是否允許 | 行為 |
|---|---:|---|
| `/coc sudo <target> act ...` | 是 | 以 target player turn 執行；公開結果必須加代操作標記 |
| `/coc sudo <target> check` | 是 | 只消費 target 的 pending check |
| `/coc sudo <target> luck skip\|regular\|hard\|extreme` | 是 | 只處理 target 已存在的 pending Luck decision |
| `/coc sudo <target> away` | 是 | 代 target 將目前 active character 標記為暫離；不改變 actor 的 KP 身分 |
| `/coc sudo <target> back` | 是 | 代 target 解除暫離；仍須通過既有 active character 與 game guard |
| `/coc sudo <target> retire [角色名]` | 是 | 代 target 退出目前角色並解除 active player binding；保留角色歷史，不刪除角色資料 |
| `/coc sudo <target> luck roll` | 否 | 回覆「LUCK 必須由玩家本人擲骰」，不改動 Luck |
| `/coc sudo <target> pc\|create\|alloc\|usepregen ...` | 否 | 回覆角色建立／認領必須由玩家本人執行，不改動角色或 pregen claim |
| `/coc sudo <target> group-admin-command` | 否 | 回覆 command 不屬於 player-scoped sudo allowlist |
| `/coc sudo <target> sudo ...` | 否 | 禁止巢狀代操作 |

## 6. Output、privacy 與 audit

### 6.1 公開與私訊

- 預設仍回覆目前 channel；所有成功的 sudo public result 前都必須加上明確標記：
  `【KP Assistant 代操作：角色名】`，讓其他玩家知道這不是角色本人輸入。
- 原本 command 會傳送秘密／角色私訊時，`send_dm`／`send_dm_image` 的 recipient
  必須是 subject；不可因 actor 是 KP 就把秘密只傳給 actor。
- 圖片、Help、pending check／Luck 按鈕的現有 channel scope 與 visibility 規則
  不因 sudo 被放寬。

### 6.2 Structured audit event

新增固定 event：

| Event | level | 必要欄位 |
|---|---|---|
| `sudo.started` | INFO | `command`, `actor_user_id_hash`, `subject_user_id_hash` |
| `sudo.completed` | INFO | `command`, `status`, `duration_ms`, `actor_user_id_hash`, `subject_user_id_hash` |
| `sudo.denied` | WARNING | `command`, `deny_reason`, `actor_user_id_hash`, `subject_user_id_hash`（若可解析） |
| `sudo.failed` | ERROR | `command`, `error_type`, `actor_user_id_hash`, `subject_user_id_hash` |

不得將完整玩家訊息、prompt、角色秘密或 raw Discord user ID 放進 audit event。
user ID hash 必須沿用既有 `LOG_HASH_IDENTIFIERS` policy；若系統設定關閉 hash，
才允許輸出明文 identifier。`LOG_ENABLED=false` 時 structured audit 走既有
no-op fast path，但實際拒絕仍要回覆使用者。

`act` 的 canonical game history 必須讓 AI 與玩家知道這是代操作，使用
`[KP Assistant 代操作 <角色名>]` 的受控前綴；不可把 actor 的完整 Discord ID
或 OOC prompt 寫入公開 `state.log`。

## 7. Implementation boundaries

建議責任分層：

| 模組 | 責任 |
|---|---|
| `app/commands/router.py` | 識別 sudo syntax、解析 target、建立 ActingContext、做 top-level authorization、維持 lock／priority flow |
| `app/commands/sudo.py`（新增） | target token parser、player command allowlist、禁止 command 與固定拒絕原因 |
| `app/commands/handlers/*.py` | 接收 effective subject；不自行猜測 actor；保留既有角色／gameplay guard；提供玩家與 sudo 共用的 `retire` target binding 解除流程 |
| `app/agents/supervisor.py`／`app/keeper.py` | `act` 以 player role 執行，保存 canonical history，不進 KP OOC path |
| `app/observability.py` | user ID redaction／audit event 欄位 helper；不記錄 command body 原文 |
| `app/help_registration.py`／player docs | 登記 `/coc sudo` 使用方式與 KP-only 標記；禁止清單也要可查 |

不應把 sudo actor 判定散落到每一個 character handler；top-level authorization
先確認 actor，handler 只處理 subject 角色 ownership 與原本 command-specific guard。

## 8. Backward compatibility 與 migration

- 不新增或修改 `GroupState` 必要欄位；`ActingContext` 是 request-scoped、不可持久化。
- 舊 state JSON 不需 migration。
- normal player command 的 API signature 儘量維持相容；新增 context 應使用 optional
  parameter 或內部 wrapper。
- 舊的 KP Assistant OOC flow、`kp_ooc_log`、KP tool allowlist、canonical dice
  workflow 不改變。
- 既有 Discord Keeper role 的 scenario／checkpoint authorization 不改變。

## 9. Testing plan

### Unit tests

- `<@id>`、`<@!id>`、opaque test user ID 的 target parser；顯示名稱與模糊 token
  必須拒絕。
- 非 KP／非 Keeper 執行 `/coc sudo` 必須拒絕且沒有 state mutation。
- current KP Assistant 與 Discord Keeper 都可通過 actor authorization。
- actor 未脫離 player character／建角流程時必須拒絕；`/coc away` 不得被誤認為
  已脫離 KP／player 身分。
- current KP 不可作為 subject；無 active character 時需要角色的 command 被拒絕。
- 禁止 group-level command、未知 command、巢狀 sudo 都拒絕。
- `sheet`／`switch`／`setskill` 等操作修改或查詢 target，而不是 actor。
- `check`／`luck` 的 pending state 只讀寫 target key；不可消費其他玩家的 pending。
- `/coc sudo <target> away` 必須只標記 target 的 active character；`/coc sudo <target>
  back` 只能解除 target 的暫離狀態。
- `/coc sudo <target> retire [角色名]` 必須解除 target 的 active player binding，
  保留 `characters_by_id` 的角色資料；若有 pending pregen LUCK 必須保留，且不得
  清除 actor 的 KP Assistant 身分。
- target 突然離線時，KP Assistant 可用 sudo `away` 暫停其回合；需要退出目前角色
  時可用 sudo `retire`，且 retire 不刪除角色歷史，只解除 active player binding。
- sudo `away`／`back`／`retire` 的 target 必須是 subject；不能因為 actor 是 KP 就
  把 actor 自己的角色索引誤改掉。
- `luck roll`、`pc`／`create`／`alloc`／`usepregen` 必須拒絕且不改動 state。

### Integration tests

- `/coc sudo <player> act ...` 會使用 target character、`speaker_role="player"`、
  target map location 與 canonical player history。
- sudo `act` 不會增加 `kp_ooc_log`，且會照一般玩家 turn 執行 maintenance。
- target 的 private DM／image recipient 正確；actor 不會意外收到 target secret。
- sudo `away` 只更新 target active character 的暫離狀態；sudo `retire` 解除 target
  的 active binding、保留角色資料，且不會解除 actor 的 KP Assistant 身分。
- public output 必須有 sudo marker，且 ordinary player output 不受影響。
- concurrent player message 與 sudo message 經過 priority gate／conversation lock
  後不會覆蓋較新的 state revision。
- `sudo.started`／`completed`／`denied`／`failed` event 欄位完整且 identifier
  遵守 hash 設定；`LOG_ENABLED=false` 不建立 structured payload。

## 10. Explicit non-goals

- 不讓 KP Assistant 直接取得另一位玩家的 Discord token、帳號或平台權限。
- 不把 sudo 變成任意 Python／資料庫／LLM tool 執行入口。
- 不讓 sudo 繞過 `game_started`、LUCK、角色 ownership、scenario visibility 或
  combat canonical mutation guard。
- 不允許 KP 透過 sudo 執行 `luck roll`、建立角色或認領預製角色。
- 不在第一版新增永久 sudo delegation、多人 KP、角色 ownership transfer 或
  Discord permission UI。
- 不修改既有 AI KP Assistant 的 OOC memory、KP-only tool allowlist，或新增
  `sudo_player_action` tool。

## 11. 已確認決策

1. `act` 的公開回覆必須顯示 `【KP Assistant 代操作：角色名】` 標記。
2. KP 不可透過 sudo 執行 `luck roll`；LUCK 骰必須由玩家本人執行。
3. KP 不可透過 sudo 建立或認領角色：`pc`、`create`、`alloc`、`usepregen`
   都不在 allowlist。
4. AI KP Assistant 不新增 `sudo_player_action` tool；第一版只支援人類 KP 透過
   `/coc sudo` 手動代操作。
5. 使用者必須先脫離 player character／建角流程，才能登記或執行 KP Assistant。
   `/coc away` 只代表暫離，不代表完成角色身分脫離。
6. 已脫離且正在執行 KP Assistant 的 KP，可以在玩家突然消失時，透過
   `/coc sudo <target> away` 暫離 target，或 `/coc sudo <target> retire [角色名]`
   讓 target 退出目前角色；`retire` 保留角色歷史，不刪除資料。
