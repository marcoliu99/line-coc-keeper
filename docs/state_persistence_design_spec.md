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
| 場景摘要（scene digest） | 某一團目前結構化、權威的狀態精簡摘要（角色數值、戰鬥、已知線索、NPC 能力使用狀態等），取代舊場景反覆整段帶進 prompt。 |
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
- 累積類欄位（`established_facts`／`known_clues`／`consumed_or_removed_items`／
  `npc_abilities.*.used_this_scene`）用**合併**語意更新：新摘要跟上一份摘要的同名欄位做集合
  聯集（去重），不是整份覆蓋——不然每次摘要都會忘記更早之前已知的線索。
- 現狀類欄位（`characters.*.location/hp/san/...`、`combat.*`、`recent_checkpoints`）用**取代**
  語意更新：永遠反映摘要當下的最新數值，不保留歷史值。

### 儲存位置：每場歷史各自保留一筆（已確認）

跟 `state_checkpoints` 同一種表結構，新增一張表：

```python
_TABLES = ("group_states", "characters", "scenario_indexes", "memory_chunks", "dictionary",
           "state_checkpoints", "scene_digests")
```

key 格式：`{group_id}:{digest_id}`（`digest_id` 產生方式跟 `checkpoint_id` 一樣：時間戳 + 4 碼
隨機字尾）。每次觸發（章節推進或回合數到門檻）都是**新增一列**，不是覆寫舊的——這樣每個場景
的摘要都各自留存，之後可以用 `/coc digests` 翻查任何一個舊場景當時的狀態。

累積類欄位（`established_facts`／`known_clues`／`consumed_or_removed_items`／
`npc_abilities.*.used_this_scene`）在**產生新的一列時**，用「上一筆摘要的累積內容」聯集「這個
場景新發生的事」算出來，然後整份存進新的一列——所以每一列本身都是「到這個時間點為止」的完整
快照，不需要在讀取時把多列拼起來，`/coc digest`／prompt 組裝只要讀最新一列即可拿到完整最新
狀態，`/coc digests <ID>` 則可以單獨看某一場的當時切面。

跟 `state_checkpoints` 不同的是：`scene_digests` **不做數量上限淘汰**——`state_checkpoints`
存在的目的是「最近可回溯的幾個點」，太舊的意義不大所以會自然淘汰；`scene_digests` 存在的目的
是「這一團完整的場景歷史記錄」，本來就是 SQLite 本地檔案、儲存成本可忽略，沒有理由主動丟棄。
真的需要清的話，靠下面的 `/coc digest clean` 手動處理，不自動淘汰。

### 跟 prompt 組裝的整合

`app/keeper.py` 的 `_build_dynamic_prompt` 在現有 `state.campaign_summary` 那段旁邊，新增
`scene_digest` 的區塊——只讀 `scene_digests` 表裡這個 group **最新一列**（不是整個歷史），
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
| `/coc digest` | KP 專用，顯示**最新一筆**場景摘要的內容（`public` 部分；`private` 不透過這個指令外洩）。 |
| `/coc digests` | KP 專用，列出這一團所有歷史場景摘要：ID、`scene_label`、建立時間。 |
| `/coc digest <ID>` | KP 專用，顯示指定那一筆歷史摘要的內容（`public` 部分）——用來回頭翻某個舊場景當時的狀態。 |
| `/coc digest clean <ID>` | KP 專用，手動刪除一筆歷史摘要（不會自動淘汰，見上）。 |

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
| `app/db.py` | SQLite 連線、備份 API、`state_checkpoints`／`scene_digests` 兩張表的基本 CRUD | 知道 `GroupState` 長什麼樣子——一律當成不透明 JSON blob |
| `app/repositories/group_state.py` 或新的 `app/checkpoints.py` | 組裝／還原 `GroupState`、封裝 checkpoint 的建立/列出/還原/清除邏輯、跟 `get_conversation_lock` 的整合 | SQLite 細節（透過 `app/db.py`） |
| `app/combat.py` | 呼叫 checkpoint 模組的「自動建立」入口（`start_combat` 觸發） | checkpoint 本身怎麼存 |
| `app/commands/handlers/system.py` | `/coc checkpoint*`／`/coc rollback`／`/coc digest` 指令解析與權限檢查 | checkpoint／場景摘要的實際存取邏輯 |
| 背景排程（`app/main.py`／`app/discord_bot.py`） | 定期呼叫 `db.backup_now()`、清舊備份 | 不涉及 per-group checkpoint（那是覆寫同一個 `.db` 內的一張表，不是另外的檔案） |
| `app/keeper.py`（`_run_post_turn_maintenance_after_output` 掛勾／`_build_dynamic_prompt`） | 判斷場景摘要觸發時機、呼叫摘要合併邏輯、把 `scene_digest` 的 `public`／`private` 組進動態 prompt | 摘要本身怎麼從 `GroupState` 抽取／合併（見下一列） |
| 新模組（`app/scene_digest.py`） | 從 `GroupState` 抽取欄位、跟 `scene_digests` 表裡該 group **最新一列**做合併（取代 vs 聯集語意）、寫入新的一列、提供讀最新／讀指定 ID／列出歷史的查詢函式 | 呼叫時機（由 `keeper.py` 決定）、prompt 組裝格式（由 `keeper.py` 決定） |

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
10. `advance_scenario_chapter` 觸發後，`scene_digests` 新增一列；累積類欄位（線索/事實/已用
    NPC 能力）跟前一列做聯集後存進新的一列，不遺失舊場景已知的內容；現狀類欄位（位置/HP/SAN/
    戰鬥）正確反映最新值，不殘留舊場景數值；**舊的那一列本身不被覆寫或刪除**，`/coc digest
    <舊 ID>` 仍能讀到當時的切面。
11. 達到 `SCENE_DIGEST_TURN_INTERVAL` 時即使沒有章節推進也會觸發一次摘要（新增一列）；同一輪
    不會被兩個觸發條件（章節推進＋回合數）重複觸發兩次（新增兩列）。
12. `scene_digest` 的 `private` 內容只出現在餵給 Keeper LLM 的 prompt 裡，不會透過
    `/coc digest`／`/coc digests` 或任何玩家可見的回覆外洩。
13. `scene_digests` 不受 `MAX_CHECKPOINTS_PER_GROUP` 那套數量上限規則影響——即使歷史列數超過
    `state_checkpoints` 的上限值，也不會被自動清掉；只有 `/coc digest clean <ID>` 才會刪除。
14. `/coc digests` 依建立時間列出全部歷史列（含 ID／`scene_label`／建立時間），順序穩定可預期。

## 實作順序

1. `app/db.py`：新增 `_warn_if_path_looks_transient`、`state_checkpoints` 表、`backup_now()`。
2. `app/config.py`：新增 `BACKUP_DIR`／`BACKUP_INTERVAL_MINUTES`／`BACKUP_KEEP_COUNT`／
   `MAX_CHECKPOINTS_PER_GROUP`／`SCENE_DIGEST_TURN_INTERVAL` 設定值。
3. 新模組（`app/checkpoints.py`）：建立/列出/還原/清除節點的邏輯，含 `get_conversation_lock`
   整合與 pre-rollback 自動節點。
4. `app/commands/handlers/system.py`：`/coc checkpoint*`／`/coc rollback` 指令，權限比照
   `/coc scenario use`。
5. `app/combat.py`：`start_combat` 觸發自動節點。
6. `app/main.py`／`app/discord_bot.py`：啟動背景定期備份迴圈。
7. `GroupState.to_dict()`：加 `schema_version` 欄位。
8. `docs/setup.md`：補上换正式主機時 `DB_PATH`／`DATA_DIR`／`BACKUP_DIR` 都要指到持久路徑的提醒。
9. `app/db.py`：新增 `scene_digests` 表（跟 `state_checkpoints` 同結構，不設數量上限）；新模組
   `app/scene_digest.py`：從 `GroupState` 抽取欄位、跟最新一列合併（取代 vs 聯集）、寫入新一列、
   讀最新／讀指定 ID／列出歷史。
10. `app/keeper.py`：`_run_post_turn_maintenance_after_output` 掛勾判斷觸發時機（章節推進或
    `SCENE_DIGEST_TURN_INTERVAL`）、`_build_dynamic_prompt` 讀最新一列併入 `public`／`private`
    區塊。
11. `app/commands/handlers/system.py`：`/coc digest`／`/coc digests`／`/coc digest <ID>`／
    `/coc digest clean <ID>` 指令。
