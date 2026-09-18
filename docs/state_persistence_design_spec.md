# 設計規格：遊戲狀態地端／固定伺服器保存與回溯

> 本文只處理**保存機制**本身：資料存在哪裡、什麼時機寫入、怎麼備份、怎麼還原、格式怎麼加版號。
> 「角色卡欄位長什麼樣子」「NPC 能力/冷卻怎麼設計」這類**資料模型**內容不在本文範圍——那是
> `docs/combat_design_spec.md`（另一支分支）在處理的事，本文假設那份模型無論最後長怎樣，都會
> 透過現有的 `GroupState.to_dict()`/`from_dict()` 走同一條保存路徑，本文不重複定義任何欄位。

## 目標

1. 確認「角色卡與當前數值」「戰鬥順位、回合與待處理行動」「NPC 能力與使用狀態」「物品清單」這些
   已經存在（或即將由 `docs/combat_design_spec.md` 擴充）於 `GroupState`/`Character` 裡的資料，
   都確實可靠地保存在**地端**（Bot 執行機器本機磁碟）或**固定伺服器**（長期執行的正式主機）上，
   不會因為換機器、重開機、容器重建而遺失。
2. 新增目前完全沒有的兩件事：**定期備份**（防資料庫檔案損毀/磁碟故障/誤刪）與**回溯節點**
   （KP 可以手動或在特定時機把某一團的遊戲狀態存一個快照，之後可以還原回去，用於「這場戰鬥／
   這個決定打壞了，想重來」的情境）。
3. 讓「這份資料到底安不安全」變成可以在啟動時自我檢查、可以觀察到的事，而不是只能靠人記得。

## 範圍與非目標

本期包含：

- 確認並強化現有 SQLite 保存路徑（`app/db.py`）的持久性（避免存到暫存/非持久目錄）。
- 新增整個資料庫檔案的定期備份機制（本機或固定伺服器上的另一個路徑）。
- 新增**每個聊天室（group）**層級的具名回溯節點（checkpoint）：建立、列出、還原、清除。
- `/coc checkpoint`、`/coc checkpoints`、`/coc rollback` 等 KP 專用指令。

本期不包含：

- 重新定義 `GroupState`/`Character`/`Combatant` 的欄位內容（見上方範圍聲明）。
- 多主機資料庫複寫／高可用叢集（HA）——「固定伺服器」在本文裡指**一台**長期執行的機器，
  不是分散式部署。
- 雲端物件儲存（S3 等）——本期只處理本機磁碟或同一台固定伺服器上的另一個路徑；之後真的要換
  雲端儲存，是在這個設計之上再加一層，不影響本文的資料結構。
- 劇本庫（`data/scenarios/`）與劇本頁面圖片（`data/groups/<id>_images/`）的備份——這兩個是
  唯讀或低頻寫入的解析產物，遺失後可以重新上傳/重新解析復原，優先度遠低於玩家正在進行中、
  無法重建的遊戲狀態；先不放進本期範圍，之後有餘力再一起納入同一套備份機制。
- 跨團（跨 group_id）的回溯——回溯節點永遠只還原「這一團」的狀態，不影響其他聊天室。

## 名詞

| 名詞 | 定義 |
| --- | --- |
| 地端保存 | 資料存在 Bot 執行所在機器的本機磁碟上（目前預設行為）。 |
| 固定伺服器保存 | Bot 長期執行在一台不會被重建/銷毀的正式主機上，本機磁碟等同持久儲存。 |
| 資料庫備份 | 整個 `coc_bot.db` 檔案在某個時間點的完整複本，用於磁碟損毀/誤刪等災難復原。 |
| 回溯節點（checkpoint） | 某一團在某個時間點的 `GroupState` 完整快照，可具名、可列出、可還原。 |
| 還原（rollback） | 把某一團目前的 `GroupState` 換成某個回溯節點當時的內容。 |

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
——在 `app/main.py`／`app/discord_bot.py` 的啟動流程各自加一個背景迴圈，每
`BACKUP_INTERVAL_MINUTES`分鐘呼叫一次`backup_now("scheduled")`，並在每次備份後清掉超過
`BACKUP_KEEP_COUNT`份的舊備份（依檔名時間戳排序，留最新的N份）。兩個入口各自起一份背景任務、
各自互相獨立即可——不需要跨行程協調，因為它們本來就共用同一個資料庫檔案，兩邊各自備份只是
「多一次一致的複本」，不會互相干擾或重複扣打。

## 回溯節點（Checkpoint / Rollback）

新增一張表（沿用 `app/db.py` 既有的 key-value schema 慣例，不另外設計 schema）：

```python
_TABLES = ("group_states", "characters", "scenario_indexes", "memory_chunks", "dictionary", "state_checkpoints")
```

`state_checkpoints` 的 key 格式：`{group_id}:{checkpoint_id}`（`checkpoint_id` 是系統產生的短
ID，例如 `datetime` + 4 碼隨機字尾，避免 KP 打的具名文字裡出現冒號等字元造成 key 解析問題）。
value 內容：

```json
{
  "group_id": "line-group-...",
  "checkpoint_id": "20260919-1430-a1b2",
  "label": "第二章開戰前",
  "created_by": "kp-user-id",
  "created_at": "2026-09-19T14:30:00Z",
  "reason": "manual",
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

1. **手動**：`/coc checkpoint [名稱]`，僅限目前登記的 KP Assistant 或有 Keeper 角色的人（比照
   `/coc scenario use` 的權限模型）。
2. **自動**：`start_combat`（`app/combat.py`）觸發時，自動建立一個 `reason="auto_combat_start"`
   的節點——開戰是最常見「想回溯」的時機（戰鬥打壞了想重來），不應該要求 KP 每次開戰前都記得
   手動存一次。自動節點跟手動節點共用同一份列表與清除規則（見下方數量上限），不特別區分。

每個 group 保留的節點數量上限：`MAX_CHECKPOINTS_PER_GROUP`（環境變數，預設 20）——超過時，按
建立時間淘汰最舊的節點（`auto_combat_start` 跟 `manual` 一起排序，不特別保護自動節點，因為 KP
永遠可以在重要時機手動建一個具名的來確保不被淘汰）。

### 還原流程

`/coc rollback <checkpoint_id 或 label>`：

1. 權限同建立（KP Assistant／Keeper）。
2. 在 `get_conversation_lock` 底下執行（比照其他會整份覆寫 `GroupState` 的操作，例如
   `/coc scenario use`），避免還原過程中跟正在進行的一般回合互相覆蓋。
3. 還原前，**先對目前狀態建立一個 `reason="pre_rollback"` 的節點**——這樣「回溯回溯錯了」本身
   也可以再回溯回來，不會因為一次操作失誤就真的沒有退路。
4. 用節點裡存的 `state` 做 `GroupState.from_dict()`，整份覆寫目前的 `GroupState`（等同
   `save_state(restored_state)`），不做任何欄位層級的合併——回溯的定義就是「回到那個時間點的
   完整狀態」，合併語意（保留現在但拿回某些舊欄位）不在本期範圍，需要的話那是完全不同的功能。
5. 回覆訊息明確列出：還原到哪個節點（label/時間）、還原前自動建立的 pre_rollback 節點 ID（讓
   KP 知道怎麼「回溯這次回溯」）。

### 指令規格

| 指令 | 行為 |
| --- | --- |
| `/coc checkpoint [名稱]` | 手動建立一個回溯節點，可選具名；預設用建立時間當顯示名稱。僅 KP。 |
| `/coc checkpoints` | 列出這一團目前所有節點：ID、名稱、建立時間、建立原因（手動/開戰自動/回溯前自動）。 |
| `/coc rollback <ID 或名稱>` | 還原到指定節點；還原前自動多存一個節點。僅 KP。 |
| `/coc checkpoint clean <ID>` | 手動刪除一個節點（不等數量上限自然淘汰）。僅 KP。 |

`/coc checkpoints` 的輸出必須用 ID 操作（比照劇本庫 `/coc scenario list` 的既有慣例），名稱允許
重複，不能靠名稱模糊比對刪除或還原——`rollback`／`clean` 接受名稱只在**唯一**符合時才生效，
有多筆同名時要求改用 ID，避免誤還原/誤刪。

## 格式版本

`GroupState.to_dict()` 目前沒有 schema 版本欄位；`from_dict()` 對缺欄位一律用 `.get(key, 預設值)`
容錯，這個「新欄位自動有預設值」的慣例本身已經是這個專案既有、行之有年的相容性策略（見
`app/models.py` 各處 `from_dict`）。本期新增一個不影響現有行為、純粹供未來參考的欄位：

```python
"schema_version": 1,
```

寫入 `to_dict()` 輸出裡；`from_dict()` 讀到不存在就當 `1`。用途：如果之後真的需要一次不相容的
資料搬遷（目前沒有這種已知需求），至少有欄位可以判斷「這份存檔是哪個版本存的」，而不是回頭猜
測。這不是本期要解決的問題，只是替之後鋪路，不需要额外的遷移邏輯。

## 各元件的職責邊界

| 元件 | 負責 | 不負責 |
| --- | --- | --- |
| `app/db.py` | SQLite 連線、備份 API、`state_checkpoints` 表的基本 CRUD | 知道 `GroupState` 長什麼樣子——一律當成不透明 JSON blob |
| `app/repositories/group_state.py` 或新的 `app/checkpoints.py` | 組裝／還原 `GroupState`、封裝 checkpoint 的建立/列出/還原/清除邏輯、跟 `get_conversation_lock` 的整合 | SQLite 細節（透過 `app/db.py`） |
| `app/combat.py` | 呼叫 checkpoint 模組的「自動建立」入口（`start_combat` 觸發） | checkpoint 本身怎麼存 |
| `app/commands/handlers/system.py` | `/coc checkpoint*`／`/coc rollback` 指令解析與權限檢查 | checkpoint 的實際存取邏輯 |
| 背景排程（`app/main.py`／`app/discord_bot.py`） | 定期呼叫 `db.backup_now()`、清舊備份 | 不涉及 per-group checkpoint（那是覆寫同一個 `.db` 內的一張表，不是另外的檔案） |

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
6. 超過 `MAX_CHECKPOINTS_PER_GROUP` 時正確淘汰最舊節點（手動與自動混合排序）。
7. 同名節點超過一筆時，`/coc rollback <名稱>` 拒絕並要求改用 ID，不猜測選哪一筆。
8. `_warn_if_path_looks_transient` 對 `/tmp` 底下的路徑會記警告 log，對專案內的相對/絕對路徑
   不會誤報。
9. 兩個平台入口（LINE／Discord）各自的背景備份迴圈都能正常啟動與停止，不互相阻塞或重複建立
   排程任務。

## 實作順序

1. `app/db.py`：新增 `_warn_if_path_looks_transient`、`state_checkpoints` 表、`backup_now()`。
2. `app/config.py`：新增 `BACKUP_DIR`／`BACKUP_INTERVAL_MINUTES`／`BACKUP_KEEP_COUNT`／
   `MAX_CHECKPOINTS_PER_GROUP` 設定值。
3. 新模組（`app/checkpoints.py`）：建立/列出/還原/清除節點的邏輯，含 `get_conversation_lock`
   整合與 pre-rollback 自動節點。
4. `app/commands/handlers/system.py`：`/coc checkpoint*`／`/coc rollback` 指令，權限比照
   `/coc scenario use`。
5. `app/combat.py`：`start_combat` 觸發自動節點。
6. `app/main.py`／`app/discord_bot.py`：啟動背景定期備份迴圈。
7. `GroupState.to_dict()`：加 `schema_version` 欄位。
8. `docs/setup.md`：補上换正式主機時 `DB_PATH`／`DATA_DIR`／`BACKUP_DIR` 都要指到持久路徑的提醒。
