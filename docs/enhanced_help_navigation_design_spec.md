# Enhanced `/coc help` Navigation Design Spec

## 1. Problem and goal

目前 `/coc help` 使用單一的 `HELP_TEXT`，所有角色建立、檢定、戰鬥、地圖、劇本與 KP 指令一次輸出。內容已經過長，玩家也常因為忘記輸入完整 command 而需要重新閱讀整份說明。

本功能要提供一個可由各 command handler／agent 自己註冊的 help registry，讓 `/coc help` 先顯示分類，再透過互動按鈕逐層進入 command 說明。導覽深度最多三層，並保留文字 command 作為 fallback。

成功條件：

- 新增 command 或 agent 功能時，只需在自己的模組註冊 help entry，不必修改一個中央超長字串。
- `/coc help` 顯示分類入口，而不是一次送出完整手冊。
- 使用者最多經過三層按鈕導覽即可看到某個 command 的用途、格式、範例與權限提示。
- 直接輸入 `/coc help <path>` 也能取得同一份內容，方便 LINE、測試與不支援按鈕的平台。
- 舊有所有 command 的實際 routing 與行為不因 help 重構而改變。

## 2. Scope

### In scope

- 新增 platform-agnostic help registry 與 immutable／validated help entry model。
- 將現有 help 內容拆成分類、command entry、可選的 command detail。
- 提供 handler／agent registration API。
- `/coc help` root、分類頁、command detail 頁的 rendering。
- Discord persistent/dynamic buttons：上一層、首頁、分類與 command detail。
- 不支援互動按鈕的平台使用文字 path，例如 `/coc help combat` 或 `/coc help combat damage`。
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
)
```

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
| `app/main.py`／LINE adapter | 使用 text path fallback；若未來加入 LINE quick reply，再接同一組 actions |

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
- 未知 path、alias 或已隱藏／不可見 entry：回傳清楚的「找不到這個 help 頁面」訊息，不 fallback 到整份舊 help。
- `kp_only` entry 只在 KP 身分或適用 context 顯示；若目前 help API 尚未有可靠身分判斷，第一版先以 public metadata 顯示「KP 專用」標籤，實際過濾列為 implementation decision。

## 5. Command and platform flow

### Text command flow

1. Router 解析 `/coc help` 後面的 optional path token。
2. Help service 呼叫 `get_help_page(path)`。
3. 回傳 page text；若 adapter 支援互動元件，同時回傳 navigation actions。
4. 純文字平台可使用 `/coc help`、`/coc help combat`、`/coc help combat damage` 逐頁瀏覽。

### Discord button flow

1. `/coc help` 貼出 root page 與 category buttons。
2. Button custom ID 僅保存 version、conversation id、path；不要把完整說明文字放進 custom ID。
3. 點擊後重新從 registry 解析 path，避免按鈕攜帶過期或被竄改的 help content。
4. callback 驗證 conversation/channel scope，再 edit 原訊息的內容與 View；必要時以 ephemeral error 回覆。
5. `timeout=None` 並使用 DynamicItem／既有跨重啟模式，讓 deploy 後舊按鈕仍能重新解析。
6. 每個頁面固定提供「⬅️ 上一層」與「🏠 Help 首頁」；root 不顯示上一層。

按鈕 label 必須是短標題，不直接使用完整 usage；完整 command 放在 detail page，避免 Discord button label 超長與手機版難讀。

### LINE and other fallback flow

現有 LINE adapter 沒有共用的互動按鈕／quick reply abstraction，因此第一版保證文字 path 可用，並在 root/category page 顯示下一步範例。未來若要加入 LINE quick reply，應只新增 adapter renderer，重用同一個 `HelpPage` 與 action path，不在 registry 裡混入 LINE-specific 型別。

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
10. 現有完整 `unittest discover` 維持通過。

## 8. Open questions and tradeoffs for review

- **平台範圍**：第一版是否接受 Discord 使用按鈕、LINE 使用文字 path fallback？若要求 LINE 也必須有按鈕，需要另納入 LINE Template／Quick Reply adapter 與測試，工作量會增加。
- **KP-only visibility**：help 是否要依目前使用者／群組角色隱藏 KP-only commands，或所有人都看得到但標記「KP 專用」？前者需要把 caller identity/context 傳到 help page builder。
- **分類粒度**：建議先固定 6–8 個高階分類，避免把 category 本身做成無限可巢狀樹；更細節放在 command detail 文字中。
- **相容策略**：可在一個 release 保留 `HELP_TEXT` 作為 debug／fallback，但正式 `/coc help` 不再輸出它；待新 registry 覆蓋完整後再刪除常數。
- **按鈕訊息策略**：建議 Discord 點擊後 edit 同一則 help message，避免每次點擊都洗版；若平台限制 edit，再 fallback 為新訊息。

