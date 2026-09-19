# Enhanced `/coc help` Navigation Design Spec

## 1. Problem and goal

目前 `/coc help` 使用單一的 `HELP_TEXT`，所有角色建立、檢定、戰鬥、地圖、劇本與 KP 指令一次輸出。內容已經過長，玩家也常因為忘記輸入完整 command 而需要重新閱讀整份說明。

本功能要提供一個可由各 command handler／agent 自己註冊的 help registry，讓 `/coc help` 先顯示分類，再透過互動按鈕逐層進入 command 說明。導覽深度最多三層，並保留文字 command 作為 fallback。

成功條件：

- 新增 command 或 agent 功能時，只需在自己的模組註冊 help entry，不必修改一個中央超長字串。
- `/coc help` 顯示分類入口，而不是一次送出完整手冊。
- 使用者最多經過三層按鈕導覽即可看到某個 command 的用途、格式、範例與權限提示。
- 直接輸入 `/coc help <path>` 也能取得同一份內容，方便不使用按鈕的 Discord 使用者與測試。
- 舊有所有 command 的實際 routing 與行為不因 help 重構而改變。

## 2. Scope

### In scope

- 新增 platform-agnostic help registry 與 immutable／validated help entry model。
- 將現有 help 內容拆成分類、command entry、可選的 command detail。
- 提供 handler／agent registration API。
- `/coc help` root、分類頁、command detail 頁的 rendering。
- Discord persistent/dynamic buttons：上一層、首頁、分類與 command detail。
- 本功能只支援 Discord；不在 LINE adapter 或其他平台實作 help UI。
- 對錯誤 path、未知 command、重複註冊與超過三層的 registry 定義清楚的錯誤行為。
- registry、routing、Discord navigation 與 fallback 的單元測試。

### Explicit non-goals

- 不在這個功能中改變任何 `/coc xxx` 的 command parsing 或 gameplay behavior。
- 不讓 LLM runtime 動態產生或修改 help registry；註冊內容來自程式碼。
- 不把完整 help 內容存進 `GroupState`；help 是程式版本的一部分，不是每個群組的遊戲狀態。
- 不在第一版加入搜尋、模糊比對、個人化排序、權限管理後台或多語系翻譯系統。
- 不讓導覽超過三層；command 的長說明以同一個 detail view 顯示，不再增加第四層。

## 3. Proposed model and registration API

新增 `app/help_registry.py`，維持平台無關的 registry。建議資料模型如下：

```python
HelpCategory(
    key: str,
    title: str,
    description: str,
    order: int = 0,
)

HelpEntry(
    path: tuple[str, ...],       # e.g. ("combat", "damage")
    title: str,
    summary: str,
    usage: tuple[str, ...],      # e.g. ("/coc combat damage 名稱 增減量",)
    examples: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    category: str = "other",
    order: int = 0,
    aliases: tuple[str, ...] = (),
    kp_only: bool = False,
    visibility: str = "always",
)
```

`visibility` 不只是一個靜態 boolean；help 需要根據目前 Discord conversation 的唯讀狀態動態決定 entry 是否顯示。建議使用有限的 policy key，而不是讓每個 entry 任意執行 callback：

```python
visibility: Literal[
    "always",                 # 永遠顯示
    "when_scenario_loaded",   # 已載入劇本時顯示
    "when_pregens_exist",     # 劇本有預設角色時顯示
    "when_no_pregens",        # 劇本沒有預設角色時顯示
    "when_combat_active",     # 戰鬥進行中時顯示
]
```

Help service 接收由 router 先讀取的 immutable `HelpContext`（例如 `scenario_loaded`、`pregens_exist`、`combat_active`、`is_kp`），只做 visibility evaluation；registry 不直接載入 `GroupState`、不取得 conversation lock，也不執行 command。

註冊 API 的方向：

```python
register_help_category(category: HelpCategory) -> None
register_help(entry: HelpEntry) -> None
register_help_entries(entries: Iterable[HelpEntry]) -> None
get_help_page(path: tuple[str, ...] = ()) -> HelpPage
```

Registry 在 application import 時完成註冊。各 handler 以自己的 registration function 匯出，例如 `register_character_help()`、`register_combat_help()`；`app/commands/router.py` 在啟動／首次使用時呼叫各模組 registration，避免 registry 反向 import handler 而造成 circular import。

推薦的責任分界：

| 模組 | 責任 |
| --- | --- |
| `app/help_registry.py` | model、註冊、path validation、排序、page lookup |
| `app/commands/handlers/*.py` | 自己 command 的 help metadata |
| `app/agents/*.py` | agent 對玩家可見的 command／能力說明；純內部 agent 不註冊 |
| `app/commands/router.py` | 將 `/coc help...` 交給 help service，不改其他 routing |
| `app/help_render.py` 或等效 service | 產生 platform-agnostic page text 與 navigation actions |
| `app/discord_bot.py` | 把 navigation actions 轉成 persistent Discord buttons |
| Discord text command path | 提供不使用按鈕時的 `/coc help <path>` fallback |

## 4. Navigation shape and three-level limit

`/coc help` 使用以下固定導覽模型：

```text
Level 1: Help root
  └─ category buttons: 角色、檢定、戰鬥、地圖、劇本、KP、其他

Level 2: Category page
  └─ command buttons: /coc combat start、/coc combat damage、...

Level 3: Command detail
  └─ usage / examples / notes + 回上一層、回首頁
```

Registry 的 `path` 最多允許兩個 token，例如 `("combat", "damage")`。第一個 token 是 category，第二個 token 是 command key。若既有 command 有更多語法，例如 `/coc scenario use <id>`，仍以一個 detail entry 呈現，不建立第三個 path token。註冊時超過兩個 token 應直接 raise validation error，避免 UI 悄悄產生第四層。

Page lookup 的規則：

- `()`：root，列出所有 visible categories。
- `("combat",)`：category page，列出該分類下的 entries。
- `("combat", "damage")`：detail page，顯示單一 command。
- 未知 path、alias 或已隱藏／不可見 entry：回傳清楚的「目前情境沒有這個 help 頁面」訊息，不 fallback 到整份舊 help；按鈕只會產生目前可見的 entries。
- `visibility` 決定 entry 是否出現在目前頁面；`kp_only=True` 本身不隱藏 entry，而是在 category／detail 頁加上「KP-only」標籤。實際能否執行仍由 command handler authorization 決定。

### Context-sensitive help example

角色建立是條件式 entry 的典型案例：

| 狀態 | 顯示 | 隱藏 |
| --- | --- | --- |
| 尚未載入劇本／沒有預設角色 | `/coc pc`、`/coc create` | `/coc pregen`、`/coc usepregen` |
| 目前劇本有預設角色 | `/coc pregens`、`/coc pregen`、`/coc usepregen` | `/coc pc`、`/coc create` |

`/coc pc` 被隱藏只代表目前 help 不提供這個入口；command handler 仍必須保留自己的 guard，因為使用者可以手動輸入被隱藏的 command。Help visibility 永遠不能取代 runtime authorization 或 business rule。

## 5. Command and platform flow

### Text command flow

1. Router 解析 `/coc help` 後面的 optional path token。
2. Help service 呼叫 `get_help_page(path)`。
3. 回傳 page text；若 adapter 支援互動元件，同時回傳 navigation actions。
4. 不使用按鈕時，Discord 使用者仍可用 `/coc help`、`/coc help combat`、`/coc help combat damage` 逐頁瀏覽。

### Discord button flow

1. `/coc help` 貼出 root page 與 category buttons。
2. Button custom ID 僅保存 version、conversation id、path；不要把完整說明文字放進 custom ID。
3. 點擊後重新從 registry 解析 path，避免按鈕攜帶過期或被竄改的 help content。
4. callback 驗證 conversation/channel scope，再 edit 原訊息的內容與 View；必要時以 ephemeral error 回覆。
5. `timeout=None` 並使用 DynamicItem／既有跨重啟模式，讓 deploy 後舊按鈕仍能重新解析。
6. 每個頁面固定提供「⬅️ 上一層」與「🏠 Help 首頁」；root 不顯示上一層。

按鈕 label 必須是短標題，不直接使用完整 usage；完整 command 放在 detail page，避免 Discord button label 超長與手機版難讀。

### End-to-end flow

```text
Discord user
    │
    ├─ 輸入 /coc help
    │       或
    └─ 點擊 Help button
              │
              ▼
      app/commands/router.py
              │  parse path: () / ("combat",) / ("combat", "damage")
              ▼
      app/help_registry.py
              │  validate + lookup + deterministic sort
              ▼
        HelpPage
        ├─ title / description / detail text
        └─ actions: category / command / back / home
              │
              ├─ text command path ──► reply(text)
              │
              └─ Discord renderer ──► discord.ui.View
                                      │
                                      ▼
                              edit original message
                                      │
                                      └─ next click repeats from registry
```

按鈕不直接攜帶 help 內容，而只攜帶合法 path。每次點擊都重新 lookup registry；因此按鈕即使跨 deploy 保留，也不會使用舊的文字內容或讓使用者注入任意 command。

### Registration and startup flow

```text
app/commands/handlers/combat.py
app/commands/handlers/character.py
app/agents/<agent>.py
        │
        └─ export register_help() / register_help_entries()
                    │
                    ▼
          app/help_registration.py
          register_all_help()
                    │
                    ▼
          app/help_registry.py
          global process-local registry
                    │
                    ├─ router handles /coc help
                    └─ Discord button callbacks resolve paths
```

### How another handler or agent registers help

這不是 HTTP API，也不是 LLM tool。建議提供一個 Python API，讓程式碼中的 handler／agent 在啟動時註冊 metadata：

```python
# app/commands/handlers/combat.py
from app.help_registry import HelpEntry, register_help_entries


def register_help() -> None:
    register_help_entries([
        HelpEntry(
            path=("combat", "start"),
            category="combat",
            title="開始戰鬥",
            summary="依 DEX 建立戰鬥先攻順位。",
            usage=("/coc combat start",),
            examples=("/coc combat start",),
            order=10,
        ),
        HelpEntry(
            path=("combat", "damage"),
            category="combat",
            title="調整戰鬥 HP",
            summary="對戰鬥中的角色或 NPC 套用 HP 增減。",
            usage=("/coc combat damage 名稱 增減量",),
            examples=("/coc combat damage 深潛者 -4",),
            order=40,
        ),
    ])
```

Handler 的 command implementation 與 help metadata 保持同一個模組，但兩者不是互相呼叫。註冊內容只描述 usage；真正的 routing 仍由既有 `handle_combat_command()` 負責。

條件式 command 直接在 entry 上指定 policy：

```python
HelpEntry(
    path=("character", "pc"),
    category="character",
    title="快速建立調查員",
    summary="建立一位自訂調查員。",
    usage=("/coc pc 角色名 [職業]",),
    visibility="when_no_pregens",
)

HelpEntry(
    path=("character", "usepregen"),
    category="character",
    title="使用預設角色",
    summary="選擇劇本附帶的預設調查員。",
    usage=("/coc usepregen 編號 [自訂名稱]",),
    visibility="when_pregens_exist",
)
```

Agent 若有玩家可直接使用的 slash command，也使用同一個 API：

```python
# app/agents/scenario_agent.py
from app.help_registry import HelpEntry, register_help_entries


def register_help() -> None:
    register_help_entries([
        HelpEntry(
            path=("scenario", "use"),
            category="scenario",
            title="選用劇本",
            summary="從劇本庫選擇目前要使用的劇本。",
            usage=("/coc scenario use 劇本ID",),
            notes=("KP-only：只有目前登記的 KP Assistant 可以執行。",),
            kp_only=True,
        ),
    ])
```

純內部 agent tool 不註冊成 `/coc` help entry。例如 `search_memory`、`apply_combat_damage` 若沒有玩家可直接輸入的 slash command，就只留在 agent/tool schema 與 KP Assistant 說明，不偽造一個不存在的 `/coc` 指令。

### Central registration point

為避免依賴 import side effect 或 circular import，新增一個明確的 registration point：

```python
# app/help_registration.py
from app.commands.handlers import character, combat, map_handler, system


def register_all_help() -> None:
    register_help_categories()
    character.register_help()
    combat.register_help()
    map_handler.register_help()
    system.register_help()
    # agents with player-facing slash commands register here too
```

Application startup（或 router 第一次處理訊息前的 lazy initialization）只呼叫一次 `register_all_help()`。Registry 需要 idempotent：重複初始化不應增加重複 entries；同一路徑由不同模組註冊則直接 raise configuration error，讓測試或啟動時立刻發現。

新增一個 handler／agent 的實際步驟：

1. 在該模組新增 `register_help()`。
2. 使用自己的 category 與最多兩段的 `path` 註冊 `HelpEntry`。
3. 將該模組加入 `app/help_registration.py` 的 `register_all_help()`。
4. 加入 registry page、router、Discord button callback 測試。
5. 執行完整測試，確認沒有 duplicate path、超過三層或超出 Discord message/button 限制。

這種方式的優點是註冊內容是 typed、可 code review、可在 CI 驗證；代價是每個新模組要在 central registration point 加一行，這是刻意換取 deterministic startup 與避免 import magic。

### Non-Discord platforms

本功能明確排除 LINE 與其他平台。Help registry 與 Discord renderer 不應被 LINE adapter import；未來若要支援其他平台，另開獨立規格與 adapter，不在本 feature 中預留 fallback 行為。

## 6. Integration with existing code conventions

- 移除 router 對 `HELP_TEXT` 的直接依賴；保留短期相容 alias 或 fallback，直到所有平台切換完成。
- `/coc help` 預設 path 為 root；未知 `/coc` subcommand 也導向 root help，而不是輸出舊的巨大字串。
- `/roll` 不屬於 `/coc` 子命令，但可在「其他」分類註冊說明。
- PDF upload、attachment、按鈕與一般文字 command 的既有流程不變。
- Registry registration 必須 deterministic：category 與 entry 以 `order`、再以 key 排序，測試與 UI 不依賴 import set iteration order。
- help renderer 必須遵守平台訊息長度限制；若 detail page過長，按 section 分段或截斷並顯示「請使用更詳細 path」的 fallback，但不得退回完整 HELP_TEXT。
- Help metadata 不應 import `GroupState` 或取得 conversation lock；只有需要 context 的 visibility decision 才由呼叫端提供 context。

## 7. Testing plan

新增測試，至少涵蓋：

1. registry 可註冊 category／entry，並依 deterministic order 輸出。
2. duplicate category、duplicate path、未知 category、超過兩個 path token 會得到明確錯誤。
3. root、category、detail 三種 page 的內容與 actions 正確。
4. alias path 與 canonical path 回傳相同 detail。
5. `/coc help`、`/coc help combat`、`/coc help combat damage` 由 router 正確導向；其他 command 行為不受影響。
6. 未知 help path 不會送出整份 legacy `HELP_TEXT`。
7. Discord help button custom ID 可由 callback 重新解析 page；上一層／首頁按鈕不超出 root。
8. 錯 channel／conversation 的按鈕點擊被拒絕，且不改變原訊息。
9. registry import/reload 不會重複註冊 entries。
10. `when_pregens_exist`／`when_no_pregens` 等 visibility policy 會依 `HelpContext` 正確顯示或隱藏 entries。
11. 被 help 隱藏的 command 仍可手動輸入並由既有 handler guard 正確拒絕或回覆，不把 help filtering 當成 authorization。
12. 現有完整 `unittest discover` 維持通過。

## 8. KP-only classification

Help metadata 的 `kp_only` 必須反映實際 authorization，不能只因某個指令「通常由 KP 使用」就標成 KP-only。依目前程式碼核對結果：

| 分類 | 指令／能力 | 本規格的標記 | 現有行為 |
| --- | --- | --- | --- |
| 已確認 KP-only | `/coc scenario use <scenario-id>` | `kp_only=True` | 只有目前登記的 KP Assistant 可以執行 |
| KP 身分管理 | `/coc kp`、`/coc kp quit` | `kp_only=False`，但標記為「KP 身分」 | 一般符合條件的使用者可登記；只有目前 KP 可解除自己的身分 |
| KP Assistant 專用能力 | 非 slash command 的 KP Assistant 對話與 Keeper tools，例如 private combat status、scenario image private asset、主持用 deterministic tools | 不作為 `/coc help` command entry；在「KP 助手」分類說明 | 由 `speaker_role == "kp_assistant"` 與 tool allowlist 控制 |
| 目前沒有 KP authorization 的管理指令 | `/coc newgame`、`/coc pdf new|fix`、`/coc scenario reparse|cancel|clean`、`/coc end`、`/coc setpersona`、`/coc era`、`/coc index` | 暫不標 `kp_only` | 目前程式碼沒有一致的 KP 身分檢查；若產品決定它們必須是 KP-only，需另開 authorization scope 或在本 feature 中明確加入權限變更 |

因此第一版 help UI 會：

- 隱藏或標示 `/coc scenario use` 為 KP-only（採用哪一種顯示方式需以實作決定，但不可讓未授權使用者誤以為可執行）。
- 顯示 `/coc kp`、`/coc kp quit`，但以「登記／解除 KP Assistant」標籤說明，不把它們誤分類為已經是 KP 才能使用的 command。
- 不把 KP Assistant 的 internal tools 假裝成可直接輸入的 `/coc xxx`。

### Remaining review decisions

- **KP-only visibility（已決定）**：對 `/coc scenario use` 所有人都顯示 entry，分類與 detail 頁明確標記「KP-only」；非 KP 仍可閱讀完整用法，但實際執行時由 command handler 的既有 authorization 拒絕。Help visibility 不取代 command authorization。
- **分類粒度**：建議先固定 6–8 個高階分類，避免把 category 本身做成無限可巢狀樹；更細節放在 command detail 文字中。
- **相容策略**：可在一個 release 保留 `HELP_TEXT` 作為 debug／fallback，但正式 `/coc help` 不再輸出它；待新 registry 覆蓋完整後再刪除常數。
- **按鈕訊息策略**：建議 Discord 點擊後 edit 同一則 help message，避免每次點擊都洗版；若平台限制 edit，再 fallback 為新訊息。
