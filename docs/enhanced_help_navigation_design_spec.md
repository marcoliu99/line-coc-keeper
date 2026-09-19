# Enhanced `/coc help` Navigation Design Spec

## Review follow-up (2026-09-19)

- `kp_only=True` 必須與 command handler 的實際 authorization 一致；除 scenario import／merge／use 外，checkpoint、checkpoints、rollback、digest、digests 也必須在分類與 detail 頁顯示 `[KP-only]`。這些 command 仍允許具有 Discord Keeper role 的管理者執行，detail notes 需保留此例外說明。
- Discord Help navigation 必須有 adapter-level tests，驗證 custom ID path round-trip、錯誤 channel scope 被拒絕、button callback 重新載入 state 後編輯原訊息，以及 root/category/detail 的 View actions。
- 本次不處理 mypy 對 discord.py `DynamicItem(..., template=...)` 的型別報告；runtime implementation 維持不變，另列為後續技術債。

## 1. Problem and goal

目前 `/coc help` 使用單一的 `HELP_TEXT`，所有角色建立、檢定、戰鬥、地圖、劇本與 KP 指令一次輸出。內容已經過長，玩家也常因為忘記輸入完整 command 而需要重新閱讀整份說明。

本功能要提供一個可由 command handler／agent 提供 metadata 的 help registry，讓 `/coc help` 先顯示分類，再透過互動按鈕逐層進入 command 說明。導覽深度最多三層，並保留文字 command 作為 fallback。

成功條件：

- 新增 command 或 agent 功能時，只需新增一組 registry metadata，不必修改一個中央超長字串；目前由明確的中央 registration point 集中呼叫各組 metadata。
- `/coc help` 顯示分類入口，而不是一次送出完整手冊。
- 使用者最多經過三層按鈕導覽即可看到某個 command 的用途、格式、範例與權限提示。
- 直接輸入 `/coc help <path>` 也能取得同一份內容，方便不使用按鈕的 Discord 使用者與測試。
- 舊有所有 command 的實際 routing 與行為不因 help 重構而改變。

## 2. Scope

### In scope

- 新增 platform-agnostic help registry 與 immutable／validated help entry model。
- 將現有 help 內容拆成分類、command entry、可選的 command detail。
- 提供 handler／agent registration API。
- 產生玩家可閱讀、可直接照著輸入的 `docs/player_command_reference.md`。
- `/coc help` root、分類頁、command detail 頁的 rendering。
- Discord persistent/dynamic buttons：上一層、首頁、分類與 command detail。
- 本功能只支援 Discord；不在其他平台實作 help UI。
- 對錯誤 path、未知 command、重複註冊與超過三層的 registry 定義清楚的錯誤行為。
- registry、routing、Discord navigation 與 fallback 的單元測試。

### Explicit non-goals

- 不在這個功能中改變任何 `/coc xxx` 的 command parsing 或 gameplay behavior。
- 不讓 LLM runtime 動態產生或修改 help registry；註冊內容來自程式碼。
- 不把完整 help 內容存進 `GroupState`；help 是程式版本的一部分，不是每個群組的遊戲狀態。
- 不另外維護一份與 registry 分叉的手寫 command 清單；玩家文件由 registry metadata 產生或由同一份 metadata 驗證。
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
    path: tuple[str, ...],       # exactly (category, command-key), e.g. ("combat", "damage")
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

Help service 接收由 Discord adapter 先讀取的 immutable `HelpContext`（例如 `scenario_loaded`、`pregens_exist`、`combat_active`、`is_kp`），只做 visibility evaluation；registry 不直接載入 `GroupState`、不取得 conversation lock，也不執行 command。

註冊 API 的方向：

```python
register_help_category(category: HelpCategory) -> None
register_help(entry: HelpEntry) -> None
register_help_entries(entries: Iterable[HelpEntry]) -> None
get_help_page(path: tuple[str, ...] = ()) -> HelpPage
```

目前 registry 在首次使用時由 `app/help_registration.py` 的明確中央 registration point 完成註冊。為避免 registry 反向 import handler 造成 circular import，所有內建 metadata 集中在該檔案；未來若拆成各 handler 的 registration function，仍必須由中央 registration point 明確呼叫。

推薦的責任分界：

| 模組 | 責任 |
| --- | --- |
| `app/help_registry.py` | model、註冊、path validation、排序、page lookup |
| `app/help_registration.py` | 內建 command 的 metadata 與 deterministic registration 順序 |
| `app/agents/*.py` | 若有玩家可見 command，提供 metadata 給中央 registration；純內部 agent 不註冊 |
| `app/discord_bot.py` | 將 Discord `/coc help...` 交給 help service |
| `app/help_render.py` 或等效 service | 產生 platform-agnostic page text 與 navigation actions |
| `app/discord_bot.py` | 把 navigation actions 轉成 persistent Discord buttons |
| Discord text command path | 提供不使用按鈕時的 `/coc help <path>` fallback |
| `docs/player_command_reference.md` | 玩家手動輸入用的完整 command reference，由 registry 產生 |

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

Registry 的 entry `path` 固定為兩個 token，例如 `("combat", "damage")`。第一個 token 是 category，第二個 token 是 command key；category page 使用單一 category token，但不能註冊成 entry。若既有 command 有更多語法，例如 `/coc scenario use <id>`，仍以一個 detail entry 呈現，不建立第三個 path token。註冊時不是兩個 token、token 不符合 Discord custom ID 的 `[a-z0-9_-]+` 格式，或 visibility policy 不在允許集合內，都應直接 raise validation error，避免 UI 悄悄產生第四層或產生無法持久化的按鈕。

Page lookup 的規則：

- `()`：root，列出所有 visible categories。
- `("combat",)`：category page，列出該分類下的 entries。
- `("combat", "damage")`：detail page，顯示單一 command。
- 超過兩個 token 的 path 不會被截斷，而是回傳未知 Help 頁面。
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

1. Discord adapter 解析 `/coc help` 後面的 optional path token。
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
      app/discord_bot.py
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
app/help_registration.py
  _entries() / register_all_help()
        │
        └─ 集中註冊 handler／agent 提供的 metadata
                    │
                    ▼
          app/help_registry.py
          global process-local registry
                    │
                    ├─ Discord adapter handles /coc help
                    └─ Discord button callbacks resolve paths
```

### How another handler or agent registers help

這不是 HTTP API，也不是 LLM tool。這是一個 Python registration API；目前新 entry 直接加入 `app/help_registration.py` 的 `_entries()`，由中央入口在啟動／首次使用時註冊：

```python
# app/help_registration.py（或未來由 handler 提供後再由中央入口呼叫）
from app.help_registry import HelpEntry, register_help_entries


def register_combat_help() -> None:
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

Handler 的 command implementation 與 help metadata 可以保持同一個責任邊界，但兩者不是互相呼叫。註冊內容只描述 usage；真正的 routing 仍由既有 `handle_combat_command()` 負責。現行版本為避免分散註冊與 import side effect，metadata 集中在 `app/help_registration.py`。

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
# app/help_registration.py
from app.help_registry import HelpEntry, register_help_entries


def register_scenario_help() -> None:
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

為避免依賴 import side effect 或 circular import，使用一個明確的中央 registration point：

```python
def register_all_help() -> None:
    _register_categories()
    register_help_entries(_entries())
```

Application startup（或 Discord adapter 第一次處理訊息前的 lazy initialization）呼叫 `register_all_help()`。這個入口是 idempotent：重複呼叫不會增加重複 entries；同一路徑由不同模組註冊則直接 raise configuration error，讓測試或啟動時立刻發現。

新增一個 handler／agent 的實際步驟：

1. 在 `app/help_registration.py` 新增該 command 的 `HelpEntry` metadata（若日後拆到 handler，則由該 handler 提供 `register_help()`）。
2. 使用既有 category 與正好兩段的 `path` 註冊 `HelpEntry`。
3. 若使用新的 handler registration function，將它加入 `app/help_registration.py` 的 `register_all_help()`。
4. 加入 registry page、Discord text path、Discord button callback 測試。
5. 執行完整測試，確認沒有 duplicate path、超過三層或超出 Discord message/button 限制。

這種方式的優點是註冊內容是 typed、可 code review、可在 CI 驗證；代價是每個新 command 要修改 central registration point，這是刻意換取 deterministic startup 與避免 import magic。若未來 metadata 拆回 handler，仍需由此入口顯式呼叫。

## 5.2 Player manual command reference

除了 Discord 導覽按鈕，repository 必須提供一份玩家可以直接閱讀與複製指令的文件：

```text
docs/player_command_reference.md
```

文件每個 command entry 至少包含：

- command syntax，例如 `/coc combat damage 名稱 增減量`
- 一個或多個可直接複製的範例
- 用途與必要前置條件
- `visibility` 條件，例如「只有劇本沒有預設角色時顯示」
- `KP-only` 標籤（若適用）
- command alias（若存在）

條件式 entry 不會從文件消失，而是明確標註適用條件。例如 `/coc pc` 應寫成「劇本沒有預設角色時使用」；`/coc usepregen` 應寫成「劇本有預設角色時使用」。這讓玩家即使在按鈕入口被隱藏時，仍能知道可以手動輸入什麼。

文件生成建議提供一個 deterministic script／function，例如：

```text
python -m app.help_docs --output docs/player_command_reference.md
```

CI 或測試應驗證生成結果與 committed file 一致；若 registry metadata 改了但文件沒更新，應讓檢查失敗。文件是玩家 manual input 的穩定 reference，不依賴某個群組當下的 `HelpContext`，因此會列出所有已註冊 command，再附上 visibility／KP 說明。

### Non-Discord platforms

本功能明確只支援 Discord。Help registry 與 Discord renderer 不應被其他平台 import；未來若要支援其他平台，另開獨立規格與 adapter，不在本 feature 中預留 fallback 行為。

## 6. Integration with existing code conventions

- Discord adapter 不再使用 router 的 `HELP_TEXT` help fallback；正式 help 全部由 registry 提供。
- `/coc help` 預設 path 為 root；未知 `/coc` subcommand 也導向 root help renderer，顯示分類按鈕，而不是輸出舊的巨大字串或純文字 root。
- `/roll` 不屬於 `/coc` 子命令，但可在「其他」分類註冊說明。
- PDF upload、attachment、按鈕與一般文字 command 的既有流程不變。
- Registry registration 必須 deterministic：category 與 entry 以 `order`、再以 key 排序，測試與 UI 不依賴 import set iteration order。
- Discord help renderer 必須遵守 1900 字元安全上限；過長頁面截斷並顯示「請使用更詳細的 Help 路徑查看」，但不得退回完整 legacy help。單一 View 也不得超過 Discord 的 25 個按鈕，超過時以 registry configuration error 明確失敗。
- Help metadata 不應 import `GroupState` 或取得 conversation lock；只有需要 context 的 visibility decision 才由呼叫端提供 context。

## 7. Testing plan

新增測試，至少涵蓋：

1. registry 可註冊 category／entry，並依 deterministic order 輸出。
2. duplicate category、duplicate path、未知 category、超過兩個 path token 會得到明確錯誤。
3. root、category、detail 三種 page 的內容與 actions 正確。
4. alias path 與 canonical path 回傳相同 detail。
5. Discord `/coc help`、`/coc help combat`、`/coc help combat damage` 由 adapter 正確導向。
6. 未知 help path 與未知 `/coc` subcommand 不會送出整份 legacy help，而是回到 registry root renderer／未知頁面。
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
| 可切換 authorization 的劇本 lifecycle | `/coc pdf new|fix`、`/coc scenario reparse|cancel|clean` | 依 `SCENARIO_LIFECYCLE_KP_ONLY` | 預設 `false` 開放初期流程；設為 `true` 後需要目前 KP Assistant 或 Discord Keeper role，Help 也同步顯示 KP-only |
| 目前沒有 KP authorization 的一般管理指令 | `/coc newgame`、`/coc end`、`/coc setpersona`、`/coc era`、`/coc index` | 暫不標 `kp_only` | 這些仍維持現有開放政策；若產品決定限制，需另開 authorization scope |

因此第一版 help UI 會：

- 顯示 `/coc scenario use` 並明確標示為 KP-only；非 KP 仍可閱讀完整用法，但不能因此誤以為自己可以執行。
- 顯示 `/coc kp`、`/coc kp quit`，但以「登記／解除 KP Assistant」標籤說明，不把它們誤分類為已經是 KP 才能使用的 command。
- 不把 KP Assistant 的 internal tools 假裝成可直接輸入的 `/coc xxx`。

### Remaining review decisions

- **KP-only visibility（可切換）**：`scenario use` 永遠對所有人顯示並標記 KP-only；PDF choice、scenario reparse/cancel/clean 是否標記與限制，依 `SCENARIO_LIFECYCLE_KP_ONLY` 同步切換。Help visibility 不取代 command authorization。
- **分類粒度**：建議先固定 6–8 個高階分類，避免把 category 本身做成無限可巢狀樹；更細節放在 command detail 文字中。
- **相容策略**：不保留 legacy `HELP_TEXT` fallback；正式 Help 與未知 `/coc` subcommand 都使用 registry，避免新舊指令清單分叉。
- **按鈕訊息策略**：建議 Discord 點擊後 edit 同一則 help message，避免每次點擊都洗版；若平台限制 edit，再 fallback 為新訊息。
