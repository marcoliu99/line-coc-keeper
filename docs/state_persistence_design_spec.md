# 設計規格：遊戲狀態地端／固定伺服器保存與回溯

> 本文只處理**保存機制**本身：資料存在哪裡、什麼時機寫入、怎麼備份、怎麼還原、格式怎麼加版號。
> 「角色卡欄位長什麼樣子」「NPC 能力/冷卻怎麼設計」這類**資料模型**內容不在本文範圍——那是
> `docs/combat_design_spec.md`（另一支分支）在處理的事，本文假設那份模型無論最後長怎樣，都會
> 透過現有的 `GroupState.to_dict()`/`from_dict()` 走同一條保存路徑，不重複定義 NPC／戰鬥相關
> 欄位。唯一例外是「場景摘要」一節新增的 `established_facts`／`known_clues`／
> `consumed_or_removed_items` 三個 `GroupState` 欄位——這是保存機制本身為了不必呼叫額外 LLM
> 才需要的資料，見該節「資料來源」小節的說明。

## 目標

1. 確認「角色卡與當前數值」「戰鬥順位、回合與待處理行動」「NPC 能力與使用狀態」「物品清單」這些
   已經存在（或即將由 `docs/combat_design_spec.md` 擴充）於 `GroupState`/`Character` 裡的資料，
   都確實可靠地保存在**地端**（Bot 執行機器本機磁碟）或**固定伺服器**（長期執行的正式主機）上，
   不會因為換機器、重開機、容器重建而遺失。
2. 新增目前完全沒有的三件事：**定期備份**（防資料庫檔案損毀/磁碟故障/誤刪）、**回溯節點**
   （KP 可以手動或在特定時機把某一團的遊戲狀態存一個快照，之後可以還原回去，用於「這場戰鬥／
   這個決定打壞了，想重來」的情境），以及**場景摘要**（每個主要場景或每 10～15 回合，把當下
   確切的結構化狀態壓縮成一份精簡摘要，讓舊場景不用整段回合記錄反覆帶進 Keeper 的 prompt）。
3. 讓「這份資料到底安不安全」變成可以在啟動時自我檢查、可以觀察到的事，而不是只能靠人記得。

## 範圍與非目標

本期包含：

- 確認並強化現有 SQLite 保存路徑（`app/db.py`）的持久性（避免存到暫存/非持久目錄）。
- 新增整個資料庫檔案的定期備份機制（本機或固定伺服器上的另一個路徑）。
- 新增**每個聊天室（group）**層級的具名回溯節點（checkpoint）：建立、列出、還原、清除。
- 新增**場景摘要**（scene digest）：每個主要場景或每 10～15 回合，結構化壓縮當下權威狀態，
  取代舊場景整段記錄反覆帶入 prompt 的做法；`public`／`private` 分區，機密資訊明確標示不可
  公開。
- `/coc checkpoint`、`/coc checkpoints`、`/coc rollback`、`/coc digest` 等 KP 專用指令。
- 為了讓場景摘要不必呼叫額外 LLM，新增 `GroupState.established_facts`／`known_clues`／
  `consumed_or_removed_items` 三個欄位與對應的 `record_established_fact`／`record_clue` 工具
  （見「場景摘要」一節「資料來源」小節）——這三個欄位是本文自己的保存機制需要，不是在幫
  `docs/combat_design_spec.md` 定義 NPC／戰鬥資料模型，兩者範圍仍然分開。

本期不包含：

- 重新定義 `Character`/`Combatant`（NPC 能力、護甲、攻擊表等）的欄位內容——那是
  `docs/combat_design_spec.md` 的範圍；本文只在 `GroupState` 上新增跟保存機制直接相關的三個
  欄位（見上）。
- 多主機資料庫複寫／高可用叢集（HA）——「固定伺服器」在本文裡指**一台**長期執行的機器，
  不是分散式部署。
- 雲端物件儲存（S3 等）——本期只處理本機磁碟或同一台固定伺服器上的另一個路徑；之後真的要換
  雲端儲存，是在這個設計之上再加一層，不影響本文的資料結構。
- 劇本庫（`data/scenarios/`）與劇本頁面圖片（`data/groups/<id>_images/`）的備份——這兩個是
  唯讀或低頻寫入的解析產物，遺失後可以重新上傳/重新解析復原，優先度遠低於玩家正在進行中、
  無法重建的遊戲狀態；先不放進本期範圍，之後有餘力再一起納入同一套備份機制。
- 跨團（跨 group_id）的回溯——回溯節點永遠只還原「這一團」的狀態，不影響其他聊天室。

### 建議分階段交付

為了讓每一階段都能獨立驗證，實作依下列順序切分；後一階段不得繞過前一階段的保存契約：

1. **持久性基礎**：路徑解析與自我檢查、schema/timeline/revision metadata、中央保存 log、SQLite
   transaction、定期備份與跨 process backup lock。
2. **回溯**：manual／auto checkpoint、pre-rollback、rollback transaction、權限與指令，先驗證
   snapshot-before-mutation 和 timeline isolation。
3. **場景摘要**：structured facts、scene digest 歷史、目前 timeline 過濾、Keeper prompt 整合與
   digest 指令。這一階段不可用「取最後一列」取代 timeline 過濾。

## 本期固定的保存不變量

以下規則是本功能的必要契約，實作與測試都必須遵守：

- **單一 authoritative object**：`GroupState` 內所有索引若指向同一角色，記憶體中必須共享同一個
  `Character` instance；不能讓 `characters` 與 `characters_by_id` 各自 deserialize 出兩份可分歧的物件。
- **單一 authoritative write**：checkpoint、rollback、scene digest 與一般 state save 都必須使用同一個
  per-group state lock；跨 process 的資料庫 transaction 仍是最後一道 atomic 邊界。
- **timeline isolation**：rollback 會建立新的 `timeline_id`。舊 timeline 的 scene digest 可以保留供歷史
  查詢，但不得再被目前 Keeper prompt 當成最新狀態使用。
- **snapshot-before-mutation**：`auto_combat_start` 必須保存戰鬥開始前的 state，而不是已經建立第一輪
  combat state 之後才拍快照。
- **private-by-default for backups**：database backup、checkpoint 與 digest 都可能含 secret goal、KP
  notes、玩家 ID 與完整對話；檔案與目錄預設只允許 bot service user 讀寫。

## 名詞

| 名詞 | 定義 |
| --- | --- |
| 地端保存 | 資料存在 Bot 執行所在機器的本機磁碟上（目前預設行為）。 |
| 固定伺服器保存 | Bot 長期執行在一台不會被重建/銷毀的正式主機上，本機磁碟等同持久儲存。 |
| 資料庫備份 | 整個 `coc_bot.db` 檔案在某個時間點的完整複本，用於磁碟損毀/誤刪等災難復原。 |
| 回溯節點（checkpoint） | 某一團在某個時間點的 `GroupState` 完整快照，可具名、可列出、可還原。 |
| 場景摘要（scene digest） | 某一團目前結構化、權威的狀態精簡摘要（角色數值、戰鬥、已知線索、NPC 能力使用狀態等），取代舊場景反覆整段帶進 prompt。 |
| 還原（rollback） | 把某一團目前的 `GroupState` 換成某個回溯節點當時的內容。 |

## 整體流程圖（概念總覽）

本文三個機制（每輪自動流程、KP 手動回溯、背景定期備份）怎麼跟既有的每輪對話流程接起來：

```text
[ 玩家／KP 傳一則訊息 ]
        │
        ▼
app/commands/router.py → supervisor.run_turn
        │
        ▼
keeper._build_dynamic_prompt
        │
        ├─ 讀 scene_digests 表「最新一列」──► public 區塊接進一般動態 prompt
        │                                  private 區塊接進 keeper-only 機密區
        │
        ▼
   Keeper LLM 生成敘事、必要時呼叫工具
   （工具呼叫已經是「真的」在改 GroupState，
     _mutate_and_save_state 鎖機制不變）
        │
        ▼
save_state()（GroupState 整份覆寫，SQLite atomic transaction）
        │
        ▼
_run_post_turn_maintenance_after_output   ← 既有掛勾，每輪後都跑一次
        │
        ├──────────────────┬──────────────────────┐
        ▼                  ▼                       ▼
[ log 太長？ ]      [ 場景摘要該觸發了嗎？ ]   [ 背景備份時間到了嗎？ ]
（既有機制，不動）   章節推進 (advance_          （獨立的 asyncio 背景迴圈，
        │           scenario_chapter)              不掛在單輪回合上）
        ▼           或回合數達                       │
 campaign_summary   SCENE_DIGEST_TURN_INTERVAL        ▼
 （LLM 生成散文摘要）        │                  db.backup_now("scheduled")
        +                   ▼                         │
 Memory RAG 索引     app/scene_digest.py               ▼
                     原樣讀取 GroupState 當下  coc_bot-{時間戳}-scheduled.db
                     所有相關欄位（不需要跟                │
                     前一列比對／不呼叫 LLM——              ▼
                     累積早在 record_established_   清掉超過 BACKUP_KEEP_COUNT
                     _fact／record_clue／             的舊備份檔
                     remove_carried_item 這些
                     工具呼叫當下就完成了）
                            │
                            ▼
                  scene_digests 新增一列
                  （不覆寫、不刪除舊列）


[ KP 手動或事件觸發的回溯節點 ]

/coc checkpoint [名稱] ──┐
                         │
start_combat 觸發 ───────┼──► state_checkpoints 新增一列
（reason=auto_          │    （完整 GroupState.to_dict()，
  combat_start）         │     不做欄位挑選）
                         │
/coc rollback <ID> ──────┘
        │
        ▼
先自動存一筆 reason=pre_rollback 的節點
（讓「回溯回溯錯了」也能再回溯回去）
        │
        ▼
用該節點的 state 整份覆寫（`get_conversation_lock` + per-group State Lock 底下執行）
        │
        ▼
save_state()
```

### 實作時點與一致性流程（authoritative）

下面這張圖覆蓋上圖中容易產生歧義的時點；實作與測試以本圖為準。`state_revision` 由 authoritative
state transaction 遞增，`timeline_id` 只在 rollback 後切換。

```text
[ 正常回合 ]
player/KP message
      │
      ▼
router → supervisor → Keeper prompt
      │                  ▲
      │                  │ 只讀目前 timeline_id 的最新 scene_digest
      ▼                  │
deterministic tools ─────┘
      │
      ▼
per-group state lock
      │
      ▼
save_state: state_revision + 1 → SQLite transaction
      │
      ▼
save result log: success/failure + group/revision/timeline/duration
      │
      ▼
reply 已送出後的 maintenance
      ├─ log trimming / Memory RAG
      └─ digest trigger → 新增同 timeline_id 的 scene_digest

[ 備份 ]
backup timer → BACKUP_DIR/backup.lock
      ├─ lock 失敗：跳過本輪
      └─ lock 成功：SQLite Connection.backup()
                    → .tmp + fsync + atomic rename
                    → 只清理超過保留數量的 scheduled backup

[ 開戰 ]
lock → 建立 auto_combat_start checkpoint（尚未改 CombatState）
     → start_combat mutation → save_state

[ 回溯 ]
get_conversation_lock + per-group State Lock + one SQLite transaction
      → 驗證 group_id／schema_version／checkpoint
      → 建立 pre_rollback checkpoint
      → 載入 checkpoint state
      → 產生新的 timeline_id 與 state_revision
      → 覆寫 GroupState
      → 舊 timeline digest 保留供歷史查詢，但不再進目前 prompt
```

## 現況與缺口

現有保存路徑（`app/db.py` + `app/repositories/group_state.py`，2026-09 的 SQLite 遷移已經做好的部分）：

- 單一 SQLite 檔案（預設 `data/coc_bot.db`，可用 `.env` 的 `DB_PATH` 調整），WAL 模式，每次
  `save_state()` 是一個 atomic transaction——不會因為寫到一半當機而壞檔。
- `group_states`／`characters`／`scenario_indexes`／`memory_chunks`／`dictionary` 五張 key-value
  表，每次 `save_state()` 都會覆寫整個 `GroupState` blob（含角色卡、戰鬥狀態、待處理檢定/擲骰
  決定等所有欄位）。
- 換機器目前只要複製一個 `.db` 檔案就好（相對於遷移前「一堆散落 JSON 檔案」是進步）。

缺口（本文要補的）：

1. **`DATA_DIR`／`DB_PATH` 預設值是相對路徑**（`app/config.py`：`DATA_DIR = Path(os.environ.get("DATA_DIR", "data/groups"))`）。如果實際部署時的工作目錄不是預期的專案根目錄（例如某些容器/systemd
   設定沒有明確指定 `WorkingDirectory`，或换了啟動方式），資料庫可能悄悄寫到一個不是原本以為的
   地方，甚至寫進一個之後會被清掉的暫存路徑——不會報錯，只會在下次啟動時「角色跟劇本都不見了」
   才被發現。
2. **完全沒有備份。** 現有 SQLite 遷移解決的是「寫入當下」的原子性，沒有處理「檔案本身損毀、
   磁碟故障、或有人不小心刪掉整個 `data/` 資料夾」這種情境——這種情況下沒有任何復原手段。
3. **完全沒有回溯機制。** 每次 `save_state()` 都是覆寫，沒有任何歷史版本——KP 沒有辦法「這場
   戰鬥打壞了，想回到開戰前的狀態重來」，唯一的手段是手動記錄或憑記憶口頭調整數值。

## 持久性保護：啟動自我檢查

`app/db.py` 的 `_ensure_tables()`（目前在模組載入時無條件執行一次）新增一個啟動時的路徑檢查，
不阻擋啟動（避免把一個可用的開發環境擋住），但會明確記一筆警告 log：

```python
_TRANSIENT_PATH_HINTS = ("/tmp", "/var/tmp", "/private/tmp")

def _warn_if_path_looks_transient(path: Path) -> None:
    resolved = str(path.resolve())
    if any(resolved.startswith(hint) for hint in _TRANSIENT_PATH_HINTS):
        _logger.warning(
            "DB_PATH (%s) 看起來在暫存目錄底下，容器重建或系統清理暫存空間時資料可能會消失；"
            "地端長期使用或固定伺服器部署請把 DB_PATH／DATA_DIR 指到一個確定會保留的路徑。",
            resolved,
        )
```

同時把 `docs/setup.md` 現有「之後若要長期使用，建議换成正式主機」那段，補上一句明確提醒：换成
正式主機時，`DB_PATH`／`DATA_DIR`／新增的 `BACKUP_DIR`（見下）都要指到那台主機上會長期保留的
路徑，不是容器的可拋棄層（ephemeral layer）。這是文件提醒，不是程式碼能完全防呆的事——`_warn_
if_path_looks_transient` 只能抓「看起來像暫存目錄」這種明顯情況，抓不到「這個路徑其實在一個之
後會被整個銷毀重建的容器裡」這種更根本的部署選擇問題。

實作時必須先把 `DB_PATH`／`DATA_DIR`／`BACKUP_DIR` 解析成絕對路徑並記錄在啟動摘要中；相對路徑
以明確的 `APP_DATA_ROOT`（若未設定則使用啟動時的 working directory）解析一次，之後不可因為
working directory 改變而換到另一份資料庫。暫存路徑判斷要用 `Path.is_relative_to()` 或等價的
`os.path.commonpath()`，不能用單純的字串 `startswith`（例如 `/tmp2` 不應被當成 `/tmp`）。
正式部署若路徑不可寫或仍指向明顯的 ephemeral 位置，應提供設定選項讓啟動失敗，而不是靜默建立
另一份空資料庫。

### 保存結果 log

所有改動 `GroupState` 的中央保存路徑都必須產生結構化 log，讓維運可以確認「有沒有存成功、存到
哪個版本」，但不得把完整角色卡、checkpoint state、token 或玩家私密內容寫進 log。至少包含：

```text
state_save_success group_id=<redacted-or-hash> revision=<n> timeline_id=<id>
reason=<command|tool|combat|rollback|startup> duration_ms=<n>
```

- 成功使用 `INFO`；失敗使用 `ERROR`，包含例外類型、group 的不可逆識別值與 transaction 是否已
  rollback，並保留 traceback 供排查。
- `state_revision` 只能在 transaction 成功後視為已提交；失敗不能讓記憶體中的 revision 假裝已
  落地。若呼叫端有 retry，log 要能用 `event_id` 辨識同一事件，避免把一次重試誤判成兩次狀態變更。
- 一般 `save_state()` 會在 per-group State Lock 內比對資料庫目前的 `state_revision`；如果呼叫端拿著
  stale snapshot，必須拒絕寫入並記錄 revision conflict，不能讓舊 snapshot 覆蓋新狀態。Discord adapter
  會把這個衝突轉成「狀態剛被另一個操作更新，請重試」的使用者訊息；刻意整體替換遊戲的
  `/coc newgame` 是明確標記的例外。
- checkpoint、rollback、backup 也要各自記錄 `started`／`success`／`failure` 與耗時；backup
  另記錄最終檔案路徑、檔案大小與保留清理數量，但不記錄檔案內容。
- log handler 失敗不能阻塞保存；保存失敗也不能被空泛的「已排程」log 掩蓋。測試要用 caplog／
  等價機制驗證成功與失敗事件都有出現。

## 資料庫備份

新增環境變數（`app/config.py`）：

```python
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(DB_PATH.parent / "backups")))
BACKUP_INTERVAL_MINUTES = int(os.environ.get("BACKUP_INTERVAL_MINUTES", "60"))
BACKUP_KEEP_COUNT = int(os.environ.get("BACKUP_KEEP_COUNT", "48"))  # 預設值：60 分鐘一次、留 48 份，約兩天
```

`app/db.py` 新增：

```python
def backup_now(reason: str = "scheduled") -> Path:
    """對目前資料庫檔案做一次一致性快照複本，回傳複本路徑。"""
```

實作用 SQLite 內建的線上備份 API（`sqlite3.Connection.backup()`），不是單純檔案複製——WAL 模式下
直接 `shutil.copy` 資料庫檔案可能複製到一個尚未 checkpoint、跟 `-wal` 檔案不同步的不一致狀態；
`backup()` API 會在來源連線仍在使用中的情況下產生一份一致的複本，這是 SQLite 官方就是為了這個
情境設計的介面。

檔名格式：`coc_bot-{YYYYMMDD-HHMMSS}-{reason}.db`（`reason` 例如 `scheduled`／`manual`／
`pre-rollback`，方便之後人工排查是哪一種情境觸發的備份）。

排程：沿用專案既有的背景任務模式（`app/legacy_commands.py`
的`_spawn_post_turn_maintenance`那種「fire-and-forget asyncio task」寫法，不新增排程框架依賴）
——在 `app/discord_bot.py` 的啟動流程加一個背景迴圈，每
`BACKUP_INTERVAL_MINUTES`分鐘呼叫一次`backup_now("scheduled")`，並在每次備份後清掉超過
`BACKUP_KEEP_COUNT`份的舊備份（依檔名時間戳排序，留最新的N份）。Discord bot 只起一份背景任務，
但若部署意外啟動兩個 process，仍必須靠跨 process lock 防止同時備份。

上段的「各自獨立」只適用於啟動與停止，不代表兩個 process 可以同時執行備份。實作必須使用
`BACKUP_DIR/backup.lock` 或等價的跨 process lease：同一時間只有一個 worker 可以進入
`backup_now()`，另一個 worker 本輪直接跳過。背景 loop 必須保存 task handle，在正常 shutdown 時
cancel 並等待結束；單次備份失敗只記錄 error、保留下一輪重試，不得讓 bot process 退出。

`backup_now()` 的檔案流程固定為：先用 SQLite `Connection.backup()` 寫入同一 backup directory
下的暫存檔，完成後 flush/fsync，再用 atomic rename 換成最終檔名。不能直接 `shutil.copy` 活躍的
`.db`，也不能讓半成品使用正式的 `.db` 檔名。`BACKUP_KEEP_COUNT` 只套用 `scheduled` 備份；
`manual` 與 `pre-rollback` 備份不得被排程自動刪除，若未來要清理由獨立的明確清理指令處理。

`BACKUP_DIR` 及產生的檔案預設使用 service user 專用權限（目錄 `0700`、檔案 `0600`）。備份檔包含
完整遊戲狀態與機密資料，不提供靜態 HTTP/Discord 附件路徑，也不在一般玩家回覆中顯示檔案內容。

checkpoint 清單與 rollback 回覆只顯示 metadata；不得把 checkpoint 內的完整 `state` 或備份內容
直接回傳給玩家。建立、還原與清除節點的結果要寫入上述結構化 log。

## 回溯節點（Checkpoint / Rollback）

新增一張表（沿用 `app/db.py` 既有的 key-value schema 慣例，不另外設計 schema）：

```python
_TABLES = ("group_states", "characters", "scenario_indexes", "memory_chunks", "dictionary",
           "state_checkpoints", "scene_digests")
```

`state_checkpoints` 的 key 格式：`{group_id}:{checkpoint_id}`（`checkpoint_id` 是系統產生的短
ID，例如 `datetime` + 4 碼隨機字尾，避免 KP 打的具名文字裡出現冒號等字元造成 key 解析問題）。
value 內容：

```json
{
  "group_id": "discord-group-...",
  "checkpoint_id": "20260919-1430-a1b2",
  "label": "第二章開戰前",
  "created_by": "kp-user-id",
  "created_at": "2026-09-19T14:30:00Z",
  "reason": "manual",
  "timeline_id": "timeline-7f2a",
  "state_revision": 184,
  "schema_version": 1,
  "state": { /* 完整 GroupState.to_dict() 當下內容 */ }
}
```

- `label`：KP 建立時可選的自訂名稱（`/coc checkpoint 第二章開戰前`），沒給就用建立時間當預設
  顯示名稱。
- `reason`：`manual`（KP 手動建立）或 `auto_combat_start`（見下方自動快照）。
- `state`：直接存整份 `GroupState.to_dict()` 的輸出——不做任何欄位挑選或裁剪，這樣不管
  `docs/combat_design_spec.md` 之後往 `GroupState`／`Character`／`Combatant` 加了什麼新欄位，
  回溯節點都會自動涵蓋到，不需要每次資料模型變動都回來同步這裡的欄位清單。

### 建立時機

1. **手動**：`/coc checkpoint [名稱]`，僅限目前登記的 KP Assistant 或 Discord 上具有名稱為
   `Keeper` 的角色的人（LINE 沒有這個 role context，因此只使用 KP Assistant 身分）。
2. **自動**：進入 `start_combat` 的 service/command 邊界時，在任何 `CombatState` mutation 之前，
   自動建立一個 `reason="auto_combat_start"` 的節點。純 `app/combat.py` 不直接操作 SQLite；若
   同一個 combat-start event 重試，必須用 event id 做 idempotency，不能產生重複自動節點。
   開戰是最常見「想回溯」的時機（戰鬥打壞了想重來），不應該要求 KP 每次開戰前都記得手動存一次。

`state_checkpoints` 跟 `scene_digests` 一樣**不做數量上限淘汰**：SQLite 本地檔案的儲存成本
可忽略，沒有理由自動丟棄任何一筆回溯節點的歷史紀錄——不管是手動建的還是 `auto_combat_start`
自動建的，永遠留著，除非 KP 自己用 `/coc checkpoint clean <ID>` 手動刪除。

### 還原流程

`/coc rollback <checkpoint_id 或 label>`：

1. 權限同建立（KP Assistant／Discord `Keeper` role）。
2. 在 `get_conversation_lock` 底下執行，並把「建立 pre-rollback + 寫入 restored state」放在同一個
   SQLite transaction，避免只完成一半。transaction 內再次驗證 checkpoint 的 `group_id` 與
   `schema_version`，拒絕跨團或未知未來版本的節點。
3. 還原前，**先對目前狀態建立一個 `reason="pre_rollback"` 的節點**——這樣「回溯回溯錯了」本身
   也可以再回溯回來，不會因為一次操作失誤就真的沒有退路。
4. 用節點裡存的 `state` 做 `GroupState.from_dict()`，整份覆寫目前的 `GroupState`，不做任何欄位
   層級的合併；restore 後產生新的 `timeline_id`，rollback transaction 以目前 revision + 1 寫入新的
   `state_revision`。
   回溯的定義就是「回到那個時間點的完整狀態」，合併語意不在本期範圍。
5. 舊 timeline 的 digest 不刪除，可供 `/coc digest <ID>` 歷史查詢，但 `latest digest` 查詢與 Keeper
   prompt 只能接受目前 timeline_id 的資料。
6. 回覆訊息明確列出：還原到哪個節點（label/時間）、還原前自動建立的 pre_rollback 節點 ID（讓
   KP 知道怎麼「回溯這次回溯」）。

### 指令規格

| 指令 | 行為 |
| --- | --- |
| `/coc checkpoint [名稱]` | 手動建立一個回溯節點，可選具名；預設用建立時間當顯示名稱。僅 KP Assistant／Discord `Keeper` role。 |
| `/coc checkpoints` | 列出這一團目前所有節點：ID、名稱、建立時間、建立原因（手動/開戰自動/回溯前自動）。僅 KP Assistant／Discord `Keeper` role。 |
| `/coc rollback <ID 或名稱>` | 還原到指定節點；還原前自動多存一個節點。僅 KP Assistant／Discord `Keeper` role。 |
| `/coc checkpoint clean <ID 或唯一名稱>` | 手動刪除一個節點——節點不會自動淘汰，這是唯一的刪除方式。僅 KP Assistant／Discord `Keeper` role。 |

`/coc checkpoints` 的輸出必須用 ID 操作（比照劇本庫 `/coc scenario list` 的既有慣例），名稱允許
重複，不能靠名稱模糊比對刪除或還原——`rollback`／`clean` 接受名稱只在**唯一**符合時才生效，
有多筆同名時要求改用 ID，避免誤還原/誤刪。checkpoint 建立、清除、rollback 與 digest 建立/清除
都必須先取得同一個 per-group State Lock；rollback 另外在 router 的 conversation lock 下執行。

## 場景摘要（Scene Digest）——結構化版本的 `campaign_summary`

### 目的

`app/keeper.py` 已經有一套處理「對話歷史太長」的機制：`run_post_turn_maintenance` 在
`state.log` 超過 `MAX_LOG_TURNS*4` 筆時，把要丟掉的那段摺進 `campaign_summary`（一段用 LLM
生成的**敘事散文摘要**）跟 Memory RAG。這解決的是「劇情講了什麼」，但沒有解決「目前確切的
數值狀態是什麼」——`campaign_summary` 是給 Narrator 讀的敘事脈絡，不是給 Executor／Narrator
核對事實用的結構化資料，實際數值仍然只能從當下的 `GroupState` 欄位讀，跟對話歷史長度無關。

本節新增的「場景摘要」要解決的是不同的問題：每個主要場景（或每 10～15 回合）結束時，把當下
**確切、結構化**的權威狀態壓縮成一份精簡摘要，讓舊場景的完整回合記錄不需要每次都整段重新帶
進 prompt 就能維持敘事一致——跟 `campaign_summary` 是同一種「不能讓 prompt 無限變長，但又不能
忘記重要事實」的精神，但一個是敘事散文、一個是結構化事實，兩者並存、互補，不是互相取代。

### 效能：絕不擋在玩家看到回覆的路徑上

這一節每一步都設計成**不需要額外呼叫 LLM**（見下面「資料來源」），純粹是讀 `GroupState` 既有
欄位＋一次 SQLite 寫入，成本跟現有每輪都會做一次的 `save_state()` 同等級——即使這樣，建立摘要
的時機仍然掛在 `_run_post_turn_maintenance_after_output` 這個既有的**背景 fire-and-forget**
掛勾上（跟現有 `campaign_summary`／Memory RAG 維護完全同一個機制），不是掛在「產生回覆」那條
路徑上：回覆已經送出去給玩家／KP 之後，這個掛勾才開始跑。也就是說，就算場景摘要的建立邏輯之
後真的需要變重（例如之後真的要接一次 LLM 摘要），也不會讓使用者多等一秒——這是這個機制的硬性
設計原則，不是「目前剛好不需要 LLM 所以碰巧不影響速度」，是刻意選在這個掛勾上、刻意不做任何
同步阻塞式呼叫。

### 資料來源：主動記錄工具，不呼叫額外 LLM

`established_facts`（已確定的劇情事實）與 `known_clues`（已取得的線索）這兩類資料，
`GroupState` 目前沒有對應欄位——需要先解決「這兩個欄位的內容從哪裡來」，才能真的做出場景摘要。

**不採用**：讓場景摘要建立時額外呼叫一次 LLM，讀最近幾輪 log、摘要出「這場新出現了什麼線索／
事實」（`campaign_summary` 的 `summarize_log_chunk` 就是這種做法）。會多一次 LLM 呼叫成本，即使
掛在背景維護任務上不影響玩家體感速度，也是不必要的額外開銷與额外的失敗點。

**採用**：比照 `keeper.TOOLS` 現有每一個工具（`skill_check`／`adjust_character`／
`add_carried_item`……）的做法，新增兩個一樣走 `_mutate_and_save_state` 鎖機制、純 Python、
不呼叫 LLM 的工具：

```python
{
    "name": "record_established_fact",
    "description": "劇情裡有件事變成確定的事實時（不是猜測，是已經證實/發生）呼叫這個工具記下來，"
                    "之後場景摘要與 KP 助手都會參考這份清單，避免同一件事前後矛盾。",
    "input_schema": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
},
{
    "name": "record_clue",
    "description": "調查員實際取得一條線索時呼叫這個工具記下來（不是每一句對話都要記，只記"
                    "真正推進案情、之後可能被回頭引用的線索）。",
    "input_schema": {"type": "object", "properties": {"clue": {"type": "string"}}, "required": ["clue"]},
},
```

`_execute_tool` 的輸入仍可維持簡單的 `fact`／`clue` 字串，但持久化時改成結構化記錄，至少保留
下列 metadata：

```json
{
  "text": "地下室的門被反鎖",
  "created_at": "2026-09-19T14:30:00Z",
  "source_event_id": "evt-...",
  "visibility": "public",
  "scene_id": "digest-..."
}
```

`visibility` 預設 `public`，需要 Keeper 才能知道的事實必須能標成 `kp_only`；`source_event_id`
讓重試與去重可追蹤，`scene_id` 沒有摘要時可為空。完全相同的 canonical `(visibility, text)`
不重複加入，但同一句話在不同 visibility 下是兩筆不同資料。這代表兩個欄位**從呼叫的那一刻
起就已經是持久化的**，不是等到場景摘要建立時才第一次寫進去。`app/services/prompt_config.py`
的 `EXECUTOR_INSTRUCTION` 補一句提醒 Executor「劇情揭露確定事實或線索時記得呼叫這兩個工具」，
比照現有工具使用規則的寫法。

`consumed_or_removed_items` 不需要新工具——現有 `remove_carried_item` 工具只有在**成功找到並
移除**物品後，才追加一筆結構化記錄（至少物品 canonical name、角色 ID、時間與 source event）。
找不到物品、數量不足或 transaction 失敗時不得留下「已消耗」紀錄；角色 ID 不能只靠顯示名稱推斷。
同一次 `_mutate_and_save_state` 呼叫裡順手做，不是另一次寫入，對呼叫端完全透明。

有了這三個持續累積、由工具直接維護的 `GroupState` 欄位之後，場景摘要建立時**只是原樣讀取**
這些欄位的當下內容，不需要在建立摘要的當下做任何「跟上一份摘要比對、算聯集」的邏輯——聯集這件
事已經在每次工具呼叫時，透過「已存在的事實不重複加入」自然達成了。這比原本設計的「建立新列時
跟前一列做聯集」更簡單，也把「這個欄位到底何時更新」的答案從「不確定，反正建摘要時處理」變成
「工具被呼叫的當下就更新」，更符合這個專案「工具呼叫是唯一改動狀態的地方」的既有慣例。

`npc_abilities.*.used_this_scene` 不在本文新增欄位範圍內（見文件開頭的範圍聲明）——這個欄位
最終長怎樣、怎麼追蹤由 `docs/combat_design_spec.md` 決定；場景摘要建立時原樣讀取那份資料當下
的內容即可，讀不到就留空，不影響本節其他欄位的運作。

### 觸發時機

1. **章節推進**：`advance_scenario_chapter` 工具呼叫成功時（見
   `docs/agentic_keeper_design_spec.md`）——這是唯一目前程式碼裡已經存在、可靠的「主要場景
   結束」結構化訊號。比場景更細的「一場對話／一個房間」邊界目前沒有機制偵測，不在本期嘗試
   猜測。
2. **每 10～15 回合**（`SCENE_DIGEST_TURN_INTERVAL`，環境變數，預設 12）：避免同一章節內長時間
   停留（一個章節可能橫跨遠超過 15 輪的調查與對話）完全沒有摘要動作。用 `state.log` 自上次摘要
   以來新增的筆數判斷，掛在既有的 `_run_post_turn_maintenance_after_output` 這個「每輪之後都會
   跑」的既有掛勾上檢查，不另外新增排程。

兩個觸發互相獨立，任一個成立就建立一次摘要；同一輪不會重複建立。

### 內容欄位

```json
{
  "updated_at": "2026-09-19T14:30:00Z",
  "timeline_id": "timeline-7f2a",
  "state_revision": 184,
  "schema_version": 1,
  "scene_label": "第二章：燈塔內部",
  "game_time": "1926年10月，深夜",
  "public": {
    "characters": {
      "陳墨": {
        "location": "燈塔一樓大廳", "away": false,
        "hp": 8, "hp_max": 10, "san": 42, "san_max": 99, "luck": 55, "mp": 6, "mp_max": 8,
        "ammo": {".38 左輪": 4}, "carried_items": ["手電筒", "撬棍"], "status_tags": []
      }
    },
    "combat": {
      "active": true, "round_number": 3, "current_turn": "陳墨",
      "pending_checks": {"陳墨": {"type": "san", "loss_success": "1", "loss_failure": "1d6"}}
    },
    "established_facts": ["守燈人三週前失蹤", "地下室的門被反鎖"],
    "known_clues": ["守燈人日記提到「潮水會帶它回來」"],
    "consumed_or_removed_items": ["用掉的最後一發照明彈"],
    "npc_abilities": {
      "深潛者混種": {"used_this_scene": ["撕咬（已用 1 次）"], "not_yet_used": ["魅惑凝視"]}
    },
    "recent_checkpoints": [{"id": "20260919-1420-x1", "label": "開戰前", "reason": "auto_combat_start"}]
  },
  "private": {
    "note": "以下內容僅供你（Keeper）判斷因果使用，絕對不可以任何方式透露給玩家，包含暗示。",
    "secret_goals": {"陳墨": "其實在幫深潛者教會臥底"},
    "npc_secret_triggers": {"深潛者混種": "SAN 檢定連續兩次大失敗才會現出原形"}
  }
}
```

- `public`／`private` 兩個頂層分組，直接沿用這次工作已經在圖片資產上建立的 `visibility`
  慣例（`public`／`kp_only` 兩級）——這裡用區塊分組取代逐欄位標記，因為摘要注定是要整塊塞進
  Keeper 的 prompt 給它讀的，用同一個機制（明確的區塊邊界 + 一句「不可公開」的提示文字）比
  逐欄位判斷更不容易在組 prompt 時漏標。
- `established_facts`／`known_clues`／`consumed_or_removed_items` 不需要在建立摘要時做任何
  合併運算——這三個欄位本身就是 `GroupState` 上持續累積、由「資料來源」小節那兩個新工具（與
  `remove_carried_item` 的既有工具擴充）直接維護的欄位，場景摘要只是**原樣讀取當下內容**存
  進這一列，累積邏輯已經在工具呼叫當下完成了。
- `npc_abilities.*.used_this_scene` 同樣是原樣讀取（這個欄位本身怎麼追蹤由
  `docs/combat_design_spec.md` 決定，本文不重複定義）。
- 現狀類欄位（`characters.*.location/hp/san/...`、`combat.*`、`recent_checkpoints`）也是原樣
  讀取 `GroupState` 當下的值——摘要裡所有欄位其實都是「讀取，不是計算」，這也是這一節前面
  「效能：絕不擋在玩家看到回覆的路徑上」能成立的原因：沒有任何欄位需要在建立摘要當下做額外
  的資料處理或 LLM 呼叫。

### 儲存位置：每場歷史各自保留一筆（已確認）

跟 `state_checkpoints` 同一種表結構，新增一張表：

```python
_TABLES = ("group_states", "characters", "scenario_indexes", "memory_chunks", "dictionary",
           "state_checkpoints", "scene_digests")
```

key 格式：`{group_id}:{digest_id}`（`digest_id` 產生方式跟 `checkpoint_id` 一樣：時間戳 + 4 碼
隨機字尾）。每次觸發（章節推進或回合數到門檻）都是**新增一列**，不是覆寫舊的——這樣每個場景
的摘要都各自留存，之後可以用 `/coc digests` 翻查任何一個舊場景當時的狀態。

每列必須保存建立當下的 `timeline_id`、`state_revision` 與 `schema_version`。最新摘要查詢不是
單純取 group 的最後一列，而是先以目前 `timeline_id` 過濾，再依 `state_revision`／建立時間取最新；
舊 timeline 仍可用 digest ID 查詢歷史，但不能重新餵進目前 Keeper prompt。

因為累積邏輯已經搬到「資料來源」小節那兩個工具（與 `remove_carried_item` 的擴充）身上，建立
新的一列時不需要再跟前一列做任何聯集運算——直接讀 `GroupState` 當下所有相關欄位的值，整份
存進新的一列即可，每一列本身自然就是「到這個時間點為止」的完整快照。`/coc digest`／prompt
組裝只要讀最新一列即可拿到完整最新狀態，`/coc digests <ID>` 則可以單獨看某一場的當時切面。

跟 `state_checkpoints` 一致：`scene_digests` 也**不做數量上限淘汰**——都是 SQLite 本地檔案，
儲存成本可忽略，沒有理由主動丟棄任何一筆歷史紀錄。真的需要清的話，靠下面的 `/coc digest clean`
手動處理，不自動淘汰。

### 跟 prompt 組裝的整合

`app/keeper.py` 的 `_build_dynamic_prompt` 在現有 `state.campaign_summary` 那段旁邊，新增
`scene_digest` 的區塊——只讀 `scene_digests` 表裡這個 group **目前 timeline 的最新一列**（不是整個歷史），
`public` 部分正常接在既有動態 prompt 的角色/戰鬥狀態說明附近（很大程度上是既有
`_build_dynamic_prompt` 已經在組的那些即時數值的**壓縮版**，主要價值在「舊場景的部分不用整段
log 重新帶入，讀這份摘要就夠」），`private` 部分要接在 keeper-only 的機密資訊區塊（比照現有
`secret_goal`／KP 助手主持規則的呈現方式），並保留 `private.note` 那句不可公開的提示文字，
不能因為壓縮格式而弄丟。

`app/services/prompt_config.py` 不需要為此新增獨立的 `build_*` 函式——這段組裝邏輯留在
`keeper._build_dynamic_prompt` 內（跟 `campaign_summary` 現在的處理方式一致），因為它本來就是
`keeper.py` 持續在維護的動態 prompt 組裝內容的一部分，不是 Executor／Narrator／Guard 三個
Agent 階段各自需要的提示詞片段。

### 指令規格（追加）

| 指令 | 行為 |
| --- | --- |
| `/coc digest` | KP Assistant／Discord `Keeper` role 專用，顯示**最新一筆**場景摘要的內容（`public` 部分；`private` 不透過這個指令外洩）。 |
| `/coc digests` | KP Assistant／Discord `Keeper` role 專用，列出這一團所有歷史場景摘要：ID、`scene_label`、建立時間。 |
| `/coc digest <ID>` | KP Assistant／Discord `Keeper` role 專用，顯示指定那一筆歷史摘要的內容（`public` 部分）——用來回頭翻某個舊場景當時的狀態。 |
| `/coc digest clean <ID>` | KP Assistant／Discord `Keeper` role 專用，手動刪除一筆歷史摘要（不會自動淘汰，見上）；找不到 ID 時回報錯誤。 |

## 格式版本與 timeline

`GroupState.to_dict()` 新增下列保存 metadata；這些欄位跟遊戲狀態一起由中央保存路徑維護：

```python
"schema_version": 1,
"timeline_id": "timeline-7f2a",
"state_revision": 184,
```

舊資料沒有欄位時，`schema_version` 以 `1` 相容讀取，`state_revision` 以 `0` 讀取，
`timeline_id` 由 `group_id` 加固定 legacy suffix 產生，避免舊資料每次載入都被視為不同 timeline。
程式碼要集中定義 `CURRENT_SCHEMA_VERSION` 與明確 migration steps；遇到大於目前版本的資料必須拒絕
載入／rollback 並記錄可操作的錯誤，不能默默忽略未知欄位。未來 schema migration 必須是可測試、
可重跑且在 transaction 內完成。

每次一般 `save_state()` 或 rollback transaction 成功才遞增 `state_revision`；rollback 一律產生新的
`timeline_id`，避免舊 timeline 的摘要在新的遊戲分支裡被誤用。`state_revision` 與 `timeline_id` 也
必須寫入成功保存或 rollback log，方便把 log、checkpoint 與 scene digest 對回同一個狀態版本。

## 各元件的職責邊界

| 元件 | 負責 | 不負責 |
| --- | --- | --- |
| `app/db.py` | SQLite 連線、備份 API、`state_checkpoints`／`scene_digests` 兩張表的基本 CRUD | 知道 `GroupState` 長什麼樣子——一律當成不透明 JSON blob |
| `app/repositories/group_state.py` 或新的 `app/checkpoints.py` | 組裝／還原 `GroupState`、封裝 checkpoint 的建立/列出/還原/清除邏輯、跟 `get_conversation_lock` 的整合 | SQLite 細節（透過 `app/db.py`） |
| `app/combat.py` | 呼叫 checkpoint 模組的「自動建立」入口（`start_combat` 觸發） | checkpoint 本身怎麼存 |
| `app/commands/handlers/system.py` | `/coc checkpoint*`／`/coc rollback`／`/coc digest` 指令解析與權限檢查 | checkpoint／場景摘要的實際存取邏輯 |
| 背景排程（`app/discord_bot.py`） | 定期呼叫 `db.backup_now()`、清舊備份 | 不涉及 per-group checkpoint（那是覆寫同一個 `.db` 內的一張表，不是另外的檔案） |
| `app/keeper.py`（`_run_post_turn_maintenance_after_output` 掛勾／`_build_dynamic_prompt`） | 判斷場景摘要觸發時機、呼叫摘要建立邏輯、把最新一列的 `public`／`private` 組進動態 prompt；`_execute_tool` 新增 `record_established_fact`／`record_clue` 實作，並擴充 `remove_carried_item` 一併記錄 `consumed_or_removed_items` | 摘要本身怎麼從 `GroupState` 抽取、寫進哪張表（見下一列） |
| 新模組（`app/scene_digest.py`） | 原樣讀取 `GroupState` 相關欄位（含 `established_facts`／`known_clues`／`consumed_or_removed_items`）、寫入 `scene_digests` 新的一列、提供讀最新／讀指定 ID／列出歷史的查詢函式 | 呼叫時機（由 `keeper.py` 決定）、prompt 組裝格式（由 `keeper.py` 決定）、`established_facts` 等欄位的值從何而來（由工具呼叫決定，不是這個模組算的） |

## 測試驗收

至少應涵蓋：

1. `db.backup_now()` 在有並行寫入的情況下，產生的複本用 `sqlite3` 開啟仍是合法、可讀的資料庫
   （驗證用的是 `.backup()` API 而不是天真檔案複製）。
2. 超過 `BACKUP_KEEP_COUNT` 的舊備份會被清掉，且清掉的是最舊的，不是隨機的。
3. `/coc checkpoint` 建立節點後，`state_checkpoints` 表裡的 `state` 完整還原得出跟建立當下
   `GroupState.to_dict()` 相同的內容。
4. `/coc rollback` 還原後，目前 `GroupState` 的角色卡／戰鬥狀態／待處理檢定等欄位確實變回節點
   當時的值；且還原前有自動多存一個 `pre_rollback` 節點。
5. `start_combat` 觸發時自動建立節點；`end_combat`／一般回合不會意外多建節點。
6. `state_checkpoints` 不做數量上限淘汰——建立遠超過（例如上百筆）測試量級的節點後，最早的
   幾筆仍然完整存在、`/coc checkpoints` 讀得到，不會被自動清掉；只有 `/coc checkpoint clean`
   才會真的刪除。
7. 同名節點超過一筆時，`/coc rollback <名稱>` 拒絕並要求改用 ID，不猜測選哪一筆。
8. `_warn_if_path_looks_transient` 對 `/tmp` 底下的路徑會記警告 log，對專案內的相對/絕對路徑
   不會誤報。
9. Discord 背景備份迴圈能正常啟動與停止；同一時間只有一個 process 透過 backup lock 實際備份，
   另一個 process 本輪跳過，失敗會記 error 並在下一輪重試。
10. `advance_scenario_chapter` 觸發後，`scene_digests` 新增一列，內容正確反映 `GroupState`
    當下的值；**舊的那一列本身不被覆寫或刪除**，`/coc digest <舊 ID>` 仍能讀到當時的切面。
11. 達到 `SCENE_DIGEST_TURN_INTERVAL` 時即使沒有章節推進也會觸發一次摘要（新增一列）；同一輪
    不會被兩個觸發條件（章節推進＋回合數）重複觸發兩次（新增兩列）。
12. `scene_digest` 的 `private` 內容只出現在餵給 Keeper LLM 的 prompt 裡，不會透過
    `/coc digest`／`/coc digests` 或任何玩家可見的回覆外洩。
13. `scene_digests` 同樣不做數量上限淘汰——即使歷史列數遠超過測試量級，也不會被自動清掉；
    只有 `/coc digest clean <ID>` 才會刪除。
14. `/coc digests` 依建立時間列出全部歷史列（含 ID／`scene_label`／建立時間），順序穩定可預期。
15. `record_established_fact`／`record_clue` 呼叫後，`GroupState.established_facts`／
    `known_clues` 立即（不等場景摘要觸發）就能讀到新內容；重複呼叫同樣的字串不會產生重複項。
16. `remove_carried_item` 呼叫後，除了原有的移除角色物品行為不變，`GroupState.
    consumed_or_removed_items` 也同時多一筆記錄，兩者在同一次 `_mutate_and_save_state` 呼叫裡
    一起落地，不會只有其中一個成功。
17. 建立場景摘要的過程中，全程沒有任何一次 LLM provider 呼叫（用 mock／spy 驗證 `run_
    conversation` 之類的呼叫次數是 0）——這是「效能：絕不擋在玩家看到回覆的路徑上」小節的
    直接可驗證版本，不是只在文件裡宣稱。
18. `GroupState` 的 `characters` 與 `characters_by_id` 對同一角色共享同一個 instance；legacy
    字串角色資料與非空 ID index 合併後不會遺失或產生兩份 authoritative object。
19. rollback 產生新的 `timeline_id` 與 revision；舊 timeline digest 仍可用 ID 查詢，但不會出現在
    目前 Keeper prompt；未知未來 `schema_version` 會拒絕載入並產生可定位的 error log。
20. checkpoint／rollback transaction 注入失敗時，既不留下半個 pre-rollback 節點，也不覆寫目前
    state；成功與失敗各自有包含耗時、revision/timeline 的結構化 log，log 不含完整 state。
21. backup lock 競爭時只有一個實際 backup；暫存檔未完成前不會出現正式檔名，atomic rename 後
    才可讀；scheduled retention 不會刪掉 manual 或 pre-rollback 備份，檔案權限符合 `0600`。
22. 成功的 `record_established_fact`／`record_clue` 會保留 source event、visibility 與時間 metadata；
    相同 canonical text 在同一 visibility 下去重，`remove_carried_item` 只有成功移除才記錄消耗。
23. auto combat checkpoint 發生在第一個 CombatState mutation 之前，同一 event retry 不會建立重複
    checkpoint；所有保存結果可由 log 對回 group、revision、timeline 與 reason。
24. checkpoint／rollback／scene digest 的建立與清除都在 per-group State Lock 下執行；stale state save
    會被拒絕而不覆蓋較新 revision，Discord 會收到可操作的重試訊息。

## 實作順序

1. `app/db.py`：新增絕對路徑解析與 `_warn_if_path_looks_transient`、中央 state save log、
   `state_checkpoints` 表、`backup_now()` 與跨 process backup lock。
2. `app/config.py`：新增 `BACKUP_DIR`／`BACKUP_INTERVAL_MINUTES`／`BACKUP_KEEP_COUNT`／
   `SCENE_DIGEST_TURN_INTERVAL` 設定值（`state_checkpoints`／`scene_digests` 都不設數量上限，
   不需要對應的環境變數）。
3. 新模組（`app/checkpoints.py`）：建立/列出/還原/清除節點的邏輯，含 `get_conversation_lock`
   整合、pre-rollback 自動節點、schema 驗證與同一 transaction 的 rollback。
4. `app/commands/handlers/system.py`：`/coc checkpoint*`／`/coc rollback` 指令，權限比照
   `/coc scenario use`。
5. `start_combat` 所在的 service/command boundary：在任何 CombatState mutation 前觸發自動節點；
   `app/combat.py` 保持戰鬥規則純粹，不直接操作 SQLite。
6. `app/discord_bot.py`：啟動背景定期備份迴圈。
7. `GroupState.to_dict()`／`from_dict()`：加入 `schema_version`／`timeline_id`／`state_revision`、
   legacy defaults、CURRENT_SCHEMA_VERSION 與明確 migration/reject 行為。
8. `docs/setup.md`：補上换正式主機時 `DB_PATH`／`DATA_DIR`／`BACKUP_DIR` 都要指到持久路徑的提醒。
9. `app/models.py`：`GroupState` 新增 `established_facts`／`known_clues`／
   `consumed_or_removed_items` 三個欄位（含 `to_dict`/`from_dict`）。
10. `app/keeper.py`：`TOOLS` 新增 `record_established_fact`／`record_clue`，`_execute_tool`
    實作（純 append＋去重，經 `_mutate_and_save_state`）；擴充既有 `remove_carried_item` 的
    實作，同一次呼叫裡一併記錄 `consumed_or_removed_items`；`app/services/prompt_config.py`
    的 `EXECUTOR_INSTRUCTION` 補上何時該呼叫這兩個新工具的提示。
11. `app/db.py`：新增 `scene_digests` 表（跟 `state_checkpoints` 同結構，不設數量上限）；新模組
   `app/scene_digest.py`：原樣讀取 `GroupState` 相關欄位、寫入新一列、提供讀最新／讀指定 ID／
   列出歷史的查詢函式；讀最新必須過濾目前 `timeline_id`（此時不需要任何合併邏輯，見上面第
   9-10 步已經把累積邏輯做在工具本身）。
12. `app/keeper.py`：`_run_post_turn_maintenance_after_output` 掛勾判斷觸發時機（章節推進或
    `SCENE_DIGEST_TURN_INTERVAL`）、`_build_dynamic_prompt` 讀最新一列併入 `public`／`private`
    區塊。
13. `app/commands/handlers/system.py`：`/coc digest`／`/coc digests`／`/coc digest <ID>`／
    `/coc digest clean <ID>` 指令。
