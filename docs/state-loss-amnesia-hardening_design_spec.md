# 設計規格：State Loss／Amnesia／Rollback Context Isolation 修正（TOCTOU Race Condition）

## 0. 文件狀態與 Changeset Tracking

- 狀態：Implementation complete on the working branch；本次 PR review fixes 已完成，待整合前 final verification。
- 整合目標：`main_v2`
- 工作 branch：`fix/state-loss-amnesia-hardening`
- 分支基準：`origin/main_v2`
- 本次 spec 起始 changeset：`origin/main_v2:b87625094a20fc7954ad1d05680c7987d0904812`
- implementation changeset：`9ab630c`
- changeset range：`origin/main_v2:b87625094a20fc7954ad1d05680c7987d0904812` → `9ab630c`
- verification：`pytest -q` 全部通過（既有 1 個 Discord 外部整合 skip）；`pytest --cov=app` 全部通過，總 coverage 60%；`ruff check app tests`、`mypy app`、`python3 -m compileall -q app tests` 全部通過。
- 遠端 branch：`origin/fix/state-loss-amnesia-hardening`
- 本文件取代先前以「maintenance stale state revision」為主要 root cause 的草稿；本次 implementation 以本文件的 correctness contract 為準，若實作採等價但較小的 code shape，必須同步更新本文件。
- 若 `main_v2` 在實作或 PR review 期間新增 commit，必須先 fetch、重新對齊、更新本節 changeset 範圍，再繼續實作或更新 PR。
- 本次 review follow-up 仍屬同一 changeset range；完成最新 commit 後必須把本節的 implementation changeset 與 range 更新為最新 commit，不能留下舊 hash。
- 問題證據來源：
  - 先前 review 的 `docs/specs/bug-state-loss-amnesia.md` 草稿。
  - `/Users/marcoliu/workspace/line-coc-keeper-main-v2/.runtime/bots/profile-async.log`。
  - `/Users/marcoliu/workspace/line-coc-keeper-main-v2/.runtime/bots/profile-async.pyinstrument.html`。
- 上述 profiler/log 是外部執行產物，不納入 repository；實作測試不得依賴該檔案存在。

## 1. 問題與目標

### 1.1 問題描述與 root cause 分類

本問題的第一級分類是 **race condition**，更精確是：

```text
deferred asynchronous maintenance
    + time-of-check / time-of-use gap
    + stale result side effects
    + state／memory／provider context 沒有共同 timeline commit boundary
```

因此不能只把它描述成「state revision conflict」。`state_revision` CAS 是目前用來防止 stale `GroupState` 覆蓋 canonical state 的保護機制；真正的問題是 maintenance 在 snapshot 後花數秒做外部 I/O，期間其他操作可以改變 timeline 或追加 state，完成後它的副作用仍可能寫入沒有相同 CAS/timeline 保護的 Memory RAG，或繼續使用舊的 provider response chain。

這是典型的 TOCTOU 流程：

```text
Tcheck：maintenance 讀取 timeline T、revision N、old log prefix
         ↓
         等待 summary LLM／embedding／provider I/O
         ↓
Tuse：  maintenance 使用當時的結果寫入 state／memory／context
```

若 Tcheck 與 Tuse 之間發生 `/coc newgame`、rollback 或其他 turn，Tuse 的結果就可能已經 stale。修正目標不是把所有並行工作改成串行，而是讓 stale result 在 commit gate 被辨識、拒絕並留下可診斷紀錄。

玩家偶爾觀察到以下現象：

- Keeper 忘記剛剛已發生的事件。
- `/coc newgame` 或 rollback 後，舊劇本內容再次出現。
- rollback 後，AI 似乎仍知道 rollback 前的事件。
- 玩家已做出的角色／戰鬥／骰點變更看起來沒有生效。
- 長時間等待後，回覆內容像是使用了較早的 state。

目前的 `GroupState` save path 有 `state_revision` 的 compare-and-swap 保護，能拒絕舊 snapshot 覆蓋較新的 canonical state；但 race 的副作用沒有全部經過同一個 timeline-aware commit gate：

1. `memory_chunks` 目前主要以 `group_id` 尋找，缺少 `timeline_id`，因此背景 maintenance 可能將舊 timeline 的摘要寫入新 timeline 可搜尋的 memory。
2. OpenAI Responses API 的 `openai_previous_response_id` 可能在 rollback 後沿用舊 provider conversation chain，使 canonical state 已 rollback，但模型仍讀到舊 server-side context。
3. `pending_checks` 目前沒有可驗證的 check identity，Discord button 的 custom ID 主要只有 conversation／owner／option；按鈕在 state 被消費、替換或重新註冊後才送出／被點擊時，可能變成 stale button、無法解析，或意外套用到同一角色的新檢定。
4. 檢定流程先在 state lock 下完成骰點與保存，再進入 Keeper narration；button path 在這兩段之間可能被其他 turn 插入，後續 narration 仍可能使用骰點當下的舊 `GroupState` snapshot。
5. pending check 沒有保存原始「角色在什麼情境做什麼」的有限 context。當 provider response chain 或 history 不完整時，骰點結果只含「某技能、某數值、某 roll」，Keeper 會失去行動語境，因而反問玩家要在什麼場景做什麼。

此外，背景 maintenance 在長時間 LLM／embedding 工作完成後才提交結果；若期間發生新回合、newgame 或 rollback，這就是 race 的衝突窗口，必須將結果視為 stale，不能靜默寫入或誤報成功。

### 1.2 目標

1. 保證舊 timeline 的 maintenance、Memory RAG 與 provider conversation chain 不會污染新 timeline。
2. 保證正常 turn、newgame、rollback 與背景 maintenance 之間的 commit 順序可驗證。
3. 保留現有 `state_revision` CAS 保護，並補足目前缺少的 timeline／memory／provider chain 邊界。
4. 將 stale maintenance result 明確標記為 skipped，而不是假設 state 已成功保存。
5. 保持既有遊戲語意：角色、Luck roll、戰鬥、KP Assistant sudo、help、scenario continuity 的規則不因本修正改變。
6. 透過測試重現並固定「maintenance 期間 newgame／rollback」的 race condition。
7. 讓技能／SAN／choice check 的 button 與 `/coc check` 使用同一筆可驗證的 pending request，不因 stale button、重複點擊或併發 turn 而錯骰、漏骰或套用錯檢定。
8. 增加足夠 observability，使下一次可以由 `request_id`、`turn_id`、`maintenance_id`、`check_id`、`timeline_id` 與 revision 還原事件順序。

## 2. 範圍與明確非目標

### 2.1 本期包含

- `memory_chunks` payload 加入 timeline、created_at、source revision 與 idempotency metadata。
- Memory RAG 的 production callers 明確傳入目前 timeline；`search_memory(..., timeline_id=None)` 僅保留給 legacy/test caller 的 unscoped compatibility path。
- Background maintenance 改為「worker 只產生 summary/embedding，commit gate 才寫入 state／memory」。目前以函式參數保存 immutable snapshot，而非新增 `MaintenanceRequest` dataclass。
- Maintenance result 加入 chunk hash 與明確 `commit_status`（committed／duplicate／stale_*）；stale 結果不會把 `state_saved` 回報為 true。
- newgame／rollback／其他 timeline-changing operation 的 provider chain reset。
- `openai_previous_response_id` 與 timeline 綁定，拒絕跨 timeline 使用。
- stale result、prefix mismatch、timeline mismatch、provider chain reset 的 structured log。
- legacy memory 與 legacy provider response ID 的安全 compatibility policy。
- state、RAG、provider chain、maintenance concurrency 與 lock ordering 測試。

### 2.2 本期不包含

- 不更換 LLM provider、model、embedding model 或 Discord library。
- 不把整個 maintenance 改造成新的 distributed queue、外部 job broker 或多機 worker。
- 不在本期調整 prompt 內容、RAG ranking、`SCENARIO_RAG_TOP_K` 或 reasoning effort。
- 不改變遊戲規則、角色 claim／reroll／Luck ownership、KP Assistant sudo policy。
- 不刪除 `state_revision` CAS，也不以最後寫入者覆蓋策略取代 CAS。
- 不把所有 historical memory 永久刪除；legacy data 必須可備份、可診斷、可選擇性恢復。
- 不承諾一次修正所有 LLM hallucination；本期處理的是 state/context consistency。

## 3. 正確性不變量（Implementation Contract）

以下規則是實作與測試的必要契約：

### 3.1 Canonical GroupState

1. `GroupState` 是遊戲狀態唯一 authoritative source。
2. `state_revision` 只能遞增，成功 transaction 不得回退。
3. 舊 snapshot 不得覆蓋較新的 state；CAS conflict 必須拒絕寫入。
4. `timeline_id` 改變後，舊 timeline 的 deferred result 不得修改新 timeline。
5. 任何 state save、maintenance commit、rollback、newgame 都必須遵守既有 per-group state lock 與 SQLite transaction。

### 3.2 Timeline

1. 同一個 `timeline_id` 表示同一條可連續使用的劇情歷史。
2. `/coc rollback` 必須產生新的 `timeline_id`，即使還原到同一個 checkpoint。
3. `/coc newgame` 必須產生新的 `timeline_id` 或使用全新 state；不得沿用舊 timeline 的 context identity。
4. 若 scenario 操作會開始新的 campaign／清空劇情，必須產生新的 timeline；若依產品定義是同一 campaign 的 scenario continuation，則保留 timeline，但 maintenance result 仍須驗證 scenario identity。
5. 舊 timeline 的 scene digest、memory 與 provider chain 可以保留供 audit／歷史查詢，但不得自動進入目前 prompt。

### 3.3 Memory RAG

1. 每一個新 memory chunk 必須包含 `timeline_id`；沒有 timeline 的資料視為 legacy/unscoped。
2. production caller 必須先取得 authoritative `timeline_id`，再以 `search_memory(group_id, ..., timeline_id=...)` 搜尋該 timeline；`timeline_id=None` 只保留給 legacy/test compatibility path，不可由 production prompt path 使用。
3. memory append 必須包含 deterministic `idempotency_key`，retry 不得重複加入同一 chunk。
4. Stale maintenance 不得先 append memory 再做 state validation。
5. Memory commit 必須在確認 timeline、log prefix／chunk identity 與 commit policy 後才可發生。
6. 若 state trim 與 memory append 不是同一個 atomic transaction，兩者至少必須使用同一個 commit gate，並在失敗時保留原始 log，不得只 trim 而遺失可重建來源。

### 3.4 Provider conversation chain

1. Provider response chain 必須與 `timeline_id` 綁定。
2. 只有當 stored chain timeline 等於目前 `GroupState.timeline_id` 時，才可傳入 `previous_response_id`。
3. rollback、newgame、scenario upload／use 與任何 timeline-changing operation 必須清除 `openai_previous_response_id`，並清除其 chain timeline metadata。scenario chapter advance 雖保留同一 timeline，也會清除 OpenAI chain，避免 chapter context 與舊 server-side chain 混用。
4. 新 timeline 的第一個 turn 必須從目前 static/dynamic prompt 與 history 建立新 provider chain，不得接續舊 timeline 的 server-side response chain。
5. Provider chain reset 必須產生 structured event，包含 reason、old timeline、new timeline；不得記錄完整 response ID。

### 3.5 Turn ordering

1. 每個 Discord request 有 `request_id`；每個遊戲 turn 有 `turn_id`；每個背景 maintenance 有獨立 `maintenance_id`。
2. lock wait、state load、LLM、tool、state commit 與 reply 的順序必須可由 log 重建。
3. request 排隊等待不代表 state rollback；實作必須保留 commit sequence／revision 供辨識。
4. LLM timeout、429 或 provider failure 不得部分寫入 canonical state。
5. `CancelledError` 不得被一般 fallback 捕捉成成功。
6. `_commit_turn_result()` 與 `_commit_kp_ooc_turn_result()` 的 false return 表示 timeline commit gate 拒絕舊回覆；caller 不得繼續送出原始 final text、private messages 或 images，必須回傳明確的 stale-turn 訊息並要求依目前 timeline 重試。

### 3.6 Pending check 與 button identity

1. 每筆 `pending_checks[owner_id]` 必須有不可變的 `check_id`；同一角色的新檢定不得沿用舊 ID。
2. `CheckButton` 的 custom ID 必須包含 full `check_id` 的 compact identity token；callback 必須在 conversation／state commit gate 中驗證目前 pending entry 的 full ID 與 timeline 完全相同。
3. stale button 不得消費目前最新的另一筆檢定；只能回覆「這個按鈕已過期」並嘗試刷新目前有效按鈕。
4. 同一個 check 的重複點擊必須是 idempotent：最多一個 request 可以消費 pending check，其餘 request 不得再次骰骰子。
5. `pending_luck_decisions` 也必須有獨立的 `decision_id`；舊 Luck button 不得套用到新的 Luck decision。
6. button 發送前必須以目前 state 做最後 identity check；發送後 callback 仍必須再次驗證，不能只相信發送前 snapshot。沒有 identity 的舊 button 不得消費新 pending；需要重新發布帶 identity 的 button。
7. pending check 必須保存 bounded 的 origin metadata：`timeline_id`、建立時 revision、origin turn/request ID，以及足以描述「要檢定哪個行動」的短 context。不可把完整 prompt 或劇本全文放入 button／log。
8. 檢定結果送回 Keeper 時，必須附帶 deterministic result、原始 skill request context 與目前 timeline；Keeper 不得靠自由回憶重新猜測玩家剛才要做的事情。
9. Discord `custom_id` 不得超過 100 characters。完整 persisted `check_id`／`decision_id` 仍是 state 的 authoritative identity，但新按鈕只能攜帶包含 owner、timeline 與完整 identity hash 的短 transport token；callback 必須重新載入 state，以 full identity 或該 token 驗證，不能把短 token 當成 persisted ID。
10. choice button 的 option 不得把任意長的 label 放進 `custom_id`；新按鈕使用 bounded option index，callback 在已驗證的 pending entry 中重新解析 label。舊版 raw-label button 僅作向後相容。
11. scenario use／新劇本 upload 產生新 timeline 時，必須清除 `pending_checks`、`pending_luck_decisions` 與 deterministic check cache；resolver 仍須拒絕任何帶有不符 timeline metadata 的舊 pending entry。

## 4. 現況流程與修正後流程

### 4.1 現況問題流程

```text
正常 turn 完成
      │
      ├─ GroupState save(revision N+1, timeline T)
      └─ detached maintenance snapshot(T, N+1, old log prefix)
                    │
                    ├─ slow summary / embedding
                    │
                    ├─ /coc newgame 或 rollback
                    │       └─ canonical state → timeline T2
                    │
                    ├─ append_memory(group_id, old text)  ← 沒有 timeline guard
                    │
                    └─ prefix check 失敗，可能跳過 state save

下一個 turn
      └─ memory search(group_id)
             └─ 舊 timeline T 的內容進入 timeline T2 prompt
```

### 4.2 修正後流程

```text
正常 turn 回覆完成
      │
          └─ 建立 logical immutable maintenance snapshot
           (maintenance context, group_id, timeline_id,
            base_revision, dropped_chunk_hash, scenario policy)
                    │
                    ▼
          background worker 只做純計算
          ├─ summary LLM
          ├─ embedding
          └─ 回傳 MaintenanceResult，不寫 state／memory
                    │
                    ▼
          commit gate（per-group state lock + DB IMMEDIATE transaction）
                    │
                    ├─ load latest GroupState
                    ├─ 驗證 current timeline == request.timeline
                    ├─ 驗證 timeline／scenario policy（目前以 timeline gate 隔離）
                    ├─ 驗證 dropped chunk prefix／chunk identity
                    ├─ 驗證 idempotency key 尚未 commit
                    │
                    ├─ 驗證失敗
                    │     ├─ 不寫 state
                    │     ├─ 不寫 memory
                    │     └─ maintenance.commit.skipped(reason=stale_*)
                    │
                    └─ 驗證成功
                          ├─ state trim + summary update
                          ├─ append memory(timeline_id, idempotency_key)
                          ├─ 同一 transaction commit
                          └─ maintenance.commit.completed

目前 turn 的 context builder
      └─ search_memory(group_id, current timeline_id)
             └─ 只返回目前 timeline 的 chunks
```

### 4.3 Timeline-changing flow

```text
/coc rollback、/coc newgame、/coc scenario use 或新劇本上傳
      │
      ▼
get_conversation_lock
      │
      ▼
state lock + SQLite transaction
      ├─ 取得目前 state
      ├─ 建立新的 timeline_id
      ├─ restore／建立新的 GroupState
      ├─ openai_previous_response_id = None
      ├─ provider_chain_timeline_id = None
      └─ commit revision + 1
      │
      ▼
舊 timeline 的背景 maintenance
      └─ commit gate 驗證失敗並安全丟棄，不可寫入新 timeline
```

### 4.4 Skill check／button flow

```text
Keeper 呼叫 skill_check／sanity_check／choice
      │
      ▼
conversation lock + state lock
      ├─ 建立 check_id
      ├─ 保存 timeline_id、origin revision、origin turn/request
      ├─ 保存 bounded action context
      └─ commit pending check
      │
      ▼
回覆玩家並發送 button
      ├─ 發送前重新確認 pending entry 完整內容仍相同
      └─ custom_id = conversation + owner + compact identity token + bounded option index
      │
      ▼
玩家按 button 或輸入 /coc check
      │
      ▼
同一個 conversation lock 內重新 load authoritative state
      ├─ check_id 相同？
      ├─ timeline_id 相同？
      ├─ pending 尚未被消費？
      └─ 不符合 → stale/expired response，不擲骰
      │
      ▼
消費 pending + deterministic roll + save result
      │
      ▼
保持同一 turn ordering，建立 Keeper result message
      ├─ deterministic roll／tier
      ├─ 原始 action context
      ├─ current timeline
      └─ 明確要求只能依既定結果敘事
      │
      ▼
Keeper narration 使用 fresh state／合法 provider chain
      │
      ▼
commit canonical log + response chain + background maintenance
```

若 `handle_check_command`／Luck resolution 在 deterministic state commit 後、Keeper narration 前失敗，button callback 的 finally 仍須在 conversation lock 離開後重新執行 pending-button diff；不能因例外而讓新建立的 pending entry 永久沒有可按的 button。刷新失敗只能記錄錯誤，不能覆蓋原始例外。

若產品上不希望 conversation lock 跨越 LLM narration，則必須改成持久化的 `CheckResolution` event／turn sequence，並在 Keeper narration 前以該 event 建立 fresh state；不得直接把 state lock 釋放後的舊 mutable `GroupState` instance 傳給 Keeper。

## 5. 資料結構與 migration

### 5.1 MaintenanceRequest

Maintenance request 是 immutable snapshot，不可由 worker 重新讀取舊 caller object 取代：

```text
# Logical immutable snapshot. The current implementation keeps these values
# as local immutable/tuple-like variables rather than adding a public class.
maintenance snapshot:
    maintenance_id: str
    group_id: str
    timeline_id: str
    base_revision: int
    scenario_identity: str | None
    dropped_chunk: tuple[str, ...]
    dropped_chunk_hash: str
    idempotency_key: str
```

`dropped_chunk_hash` 必須由穩定序列化內容計算；不可把完整劇本文字寫入 structured log。

目前 implementation 不新增獨立 `scenario_identity` 欄位；新劇本／scenario use／rollback 直接建立新的 `timeline_id`，因此 scenario isolation 由 timeline commit gate 實現。

### 5.2 MaintenanceResult

```text
maintenance result:
    request: MaintenanceRequest
    campaign_summary: str
    embedding: list[float] | None
    success: bool
    error_code: str | None = None
```

`MaintenanceResult` 不得直接攜帶可變的 `GroupState` instance，也不得在 worker 中保存 state／memory。

### 5.3 Memory chunk payload

新格式至少包含：

```json
{
  "chunk_id": "stable-id",
  "timeline_id": "timeline-...",
  "idempotency_key": "maintenance-...",
  "text": "...",
  "embedding": [0.1, 0.2],
  "created_at": "2026-09-22T00:00:00+00:00",
  "source_revision": 123
}
```

`text` 仍不得出現在 performance log；資料庫內的既有 memory data 不因本功能自動刪除。

### 5.4 Pending check payload

新格式至少包含：

```json
{
  "check_id": "check-...",
  "timeline_id": "timeline-...",
  "origin_revision": 123,
  "origin_turn_id": "turn-...",
  "origin_request_id": "request-...",
  "type": "skill",
  "skill": "偵查",
  "skill_value": 60,
  "bonus_dice": 0,
  "penalty_dice": 0,
  "difficulty": "regular",
  "action_context": "在廢棄醫院調查血跡",
  "created_at": "UTC timestamp"
}
```

`action_context` 必須 bounded、適合放入 prompt；它不是完整 prompt、scenario text 或任意長度 user message。若 Keeper 沒有提供 context，resolution path 會從目前 log 取 bounded 的最近 user action；若仍沒有可驗證內容，Keeper message 使用明確的「只描述已確定結果、不要編造場景」fallback，不得假裝知道未知場景。

`pending_luck_decisions` 使用同樣概念，但欄位名稱為 `decision_id`，並且保存原始 `check_id`；Luck button 的 callback 以 `decision_id` 驗證目前 decision，`check_id` 保留作為結果鏈結與 audit metadata。button 不需要把兩個 ID 都塞進 custom ID。

Discord transport token 由 `kind + owner_id + timeline_id + effective identity` 的 SHA-256 digest 產生固定短值（check 以 `c` 開頭、Luck decision 以 `d` 開頭）。它只用來通過 Discord component 的長度限制；完整 ID 仍保留在 state，舊版完整 ID button 仍可被 callback 驗證。scenario reset 後 timeline 變更，即使 identity 恰好相同，舊 token 也不能通過。

角色 mirror cleanup 對現代 row 使用 `conversation_id`，對沒有 metadata 的 legacy row 僅清理明確以 `<group_id>:` 開頭的 key；沒有 metadata 且沒有 group prefix 的 row 保留，避免誤刪無法判定歸屬的資料。

Maintenance commit 若無法解析 authoritative `group_states` row，必須記錄 `maintenance.commit_skipped(reason=corrupt_group_state)`，不寫入 state／memory，也不得讓背景 maintenance task 以未驗證空 state 覆蓋資料。

### 5.5 Provider chain metadata

`GroupState` 必須能判斷 stored response chain 所屬 timeline。可採用下列任一等價實作，但實作前需在 code review 中固定一種：

- 新增 `openai_previous_response_timeline_id`。
- 或將 response chain metadata 收納為 `{response_id, timeline_id}`。

缺少 chain timeline metadata 的 legacy state 不得直接信任其 response ID；目前 runtime 在 provider request path 將它視為 reset，傳入 `previous_response_id=None`，下一個成功 response 才會寫回新的 chain metadata。

### 5.6 Legacy migration policy

1. 先對 SQLite 做 backup，再進行任何人工 payload migration。
2. 沒有 `timeline_id` 的既有 memory chunk 會保留為 unscoped legacy；它只在 `legacy-<group_id>` 相容查詢可見。正常 turn 會先把缺少 timeline 的 legacy state 升級成新的 explicit timeline，因此 legacy chunk 不會進入升級後或新 `newgame`／rollback／scenario-use timeline。
3. legacy chunk 必須保留在資料庫，供人工檢查；本期不提供自動把它宣告為新 timeline 的 migration。
4. 沒有 chain timeline metadata 的既有 `openai_previous_response_id` 不得被 provider path 信任；下一個 turn 以 `previous_response_id=None` 建立新 chain，不刪除 GroupState log。
5. retry 以 memory `idempotency_key` 去重；不得重複建立相同 chunk，也不得因 maintenance retry 額外改變 state revision。
6. 若讀到不合法 optional memory payload，RAG 退化為空結果，不阻塞 bot 啟動。

## 6. Commit gate 與 lock ordering

### 6.1 Commit validation

maintenance result 進入 commit gate 後必須重新讀取 authoritative state，依序檢查：

1. `group_id` 相同。
2. `current.timeline_id == request.timeline_id`。
3. timeline identity 相同；目前 `/coc scenario use`、`newgame` 與 rollback 都建立新 timeline，因此 scenario identity 由 timeline gate 間接隔離。
4. `dropped_chunk` 仍是 current log 的預期 prefix，或使用等價且可驗證的 chunk/event identity。
5. `idempotency_key` 尚未成功 commit。
6. result 成功且 summary／embedding 通過基本 schema validation。

`base_revision` 不必要求 current revision 完全相等；正常新 turn 可以在 maintenance 執行期間追加 log。只要 timeline 與被刪除 chunk identity 仍一致，就可以將 maintenance result 套用到最新 state。revision 差異必須寫入 log，供診斷。

### 6.2 Lock ordering

所有會同時接觸 conversation、GroupState 與 memory 的流程固定採用：

```text
    normal command: conversation lock
        → per-group state lock
            → SQLite transaction
                → state + memory commit

    detached maintenance worker:
        per-group state lock
            → SQLite IMMEDIATE transaction
                → timeline/prefix/idempotency validation
                → state + memory commit
```

正常 request 仍遵守 conversation lock → state lock → transaction。detached maintenance 沒有可同步持有的 asyncio conversation lock，因此以 per-group state lock + SQLite `BEGIN IMMEDIATE` + timeline/prefix/idempotency gate 作為等價 commit boundary；兩條路徑都不得在持有 SQLite transaction 時等待 LLM、embedding、Discord API 或其他外部 I/O。worker 的慢工作必須在 commit gate 外完成。

### 6.3 Stale result policy

| 驗證結果 | State | Memory | Retry／後續處理 |
|---|---|---|---|
| timeline mismatch | 不寫 | 不寫 | 丟棄並記錄；新 timeline 重新建立 maintenance |
| scenario/timeline policy mismatch | 不寫 | 不寫 | 目前由新 timeline 的 gate 覆蓋；不可混用摘要 |
| prefix/chunk mismatch | 不寫 | 不寫 | 丟棄；下一個 turn 可重新 snapshot |
| duplicate idempotency key | 不重寫 | 不重寫 | 視為 idempotent success，記錄 duplicate |
| summary/embedding failure | 不寫 | 不寫 | 保留原始 log；依既有 retry policy 處理 |
| validation success | 寫入 | 寫入 | transaction commit |

「不寫」必須對應明確的 `maintenance.commit.skipped` event，不得回報 `state_saved=true`。

### 6.4 Pending check commit gate

技能檢定與按鈕是另一條短生命週期的 state transaction，必須使用相同的 freshness 原則：

1. 先取得 conversation lock，再讀取 state；不得只依賴 button 發送時的 snapshot。
2. 驗證 `check_id`／`decision_id`、owner、timeline 與 pending type。
3. 驗證成功後才可消費 pending、執行一次 deterministic roll 並保存。
4. 保存後產生 bounded、不可變的 in-memory check resolution payload／canonical state commit，供 Keeper narration 使用；不得只把 mutable `GroupState` instance 傳過跨 await 邊界。Keeper phase 開始前必須 refresh authoritative state。
5. 若 narration 需要等待 provider，後續 turn 不得在同一個 sequence 前插入；若不持有 conversation lock，則必須透過 persisted turn sequence／resolution event 保證順序。
6. 任何 `check_id` mismatch、timeline mismatch、已消費或不存在的 pending，都不得再次骰骰子；必須回覆可理解的 stale/expired 訊息並重新載入有效 pending buttons。
7. `pending_checks.pop()`、Luck decision consume、結果 log、角色數值更新與 response-chain metadata 的 commit 邊界必須明確；不能出現「骰點已保存，但 result narration 使用另一個 timeline」的半完成狀態。

這一段專門處理「骰完後技能檢定失敗」與「按鈕按了沒有反應」的 correctness，不由 background maintenance 的結果推測或修補。

## 7. Provider chain reset policy

### 7.1 必須 reset 的操作

- `/coc newgame`。
- `/coc rollback`。
- 新劇本 upload、`/coc scenario use` 或其他產生新 timeline 的 scenario reset／campaign reset。
- scenario chapter advance：保留 timeline，但清除 OpenAI response chain。
- 任何 restore state 的管理操作。

### 7.2 不必 reset 的操作

- 同一 timeline 內的正常玩家 turn。
- 不改變 campaign identity 的普通 scenario context 更新；chapter advance 是例外，保留 timeline 但清除 provider chain。

### 7.3 Provider request 行為

```text
current state timeline_id == chain timeline_id
    → 可使用 previous_response_id

timeline_id 不同／chain metadata 缺失／reset marker 存在
    → previous_response_id = None
    → 使用 current history 建立新 chain
```

provider response ID 不得完整寫入一般 performance log；可記錄 hash 或存在／不存在與 timeline metadata。

## 8. Observability

### 8.1 Required fields

以下事件至少要包含：

- `maintenance.started`
- `maintenance.result.completed`
- `maintenance.commit.started`
- `maintenance.commit.completed`
- `maintenance.commit.skipped`
- `provider.chain.reset`
- `memory.search.completed`
- `state.save.completed`

必要欄位：

```text
request_id（若由 request 觸發）
turn_id（若由 turn 觸發）
maintenance_id
conversation/group identity（遵守既有遮罩策略）
requested_timeline_id
current_timeline_id（commit 時）
base_revision
current_revision（commit 時）
dropped_chunk_hash
idempotency_key hash
commit_status
skip_reason／error_code
duration_ms
```

### 8.2 禁止內容

- 完整 user message。
- 完整 prompt 或 scenario text。
- 完整 campaign summary。
- 完整 embedding vector。
- API key、Discord token、authorization header。
- 完整 OpenAI response ID。

### 8.3 可診斷的狀態

`maintenance.completed` 不得再用單一 `success` 代表所有情況，至少應區分：

```text
committed
skipped_stale
skipped_duplicate
failed
cancelled
```

## 9. 測試計畫

### 9.1 Unit tests

1. Memory chunk 缺 `timeline_id` 時標記 legacy/unscoped；只有 `legacy-<group_id>` compatibility timeline 可搜尋，fresh timeline 不得返回。
2. Memory search 只返回 current timeline。
3. 相同 idempotency key 重試不重複 append。
4. maintenance snapshot 的 chunk hash／idempotency key 穩定且不受 worker 期間的 list mutation 影響。
5. provider chain timeline 不同時自動清除 `previous_response_id`。
6. rollback／newgame 產生新 timeline 並清除 provider chain metadata。
7. stale result 不會寫 state，也不會寫 memory。
8. prefix mismatch 不會把 `state_saved` 誤報為 true。
9. 每筆 pending check／Luck decision 都有唯一 identity；同一角色的新 request 不沿用舊 ID。
10. stale CheckButton／LuckSpendButton callback 不會消費目前有效的新 request。
11. check resolution／canonical commit 會保留 bounded origin action context 與 timeline metadata。

### 9.2 Concurrency／integration tests

1. maintenance LLM 執行期間，正常 turn 追加 log：
   - 新 log 保留。
   - 舊 chunk 只 trim 一次。
   - summary 不覆蓋新 timeline。

2. maintenance 執行期間 `/coc newgame`：
   - old result 被標記 `skipped_stale`。
   - old memory 不會出現在新 timeline。
   - new timeline 可以正常建立新的 memory。

3. maintenance 執行期間 rollback：
   - rollback state 正確保存。
   - old timeline memory 不進 rollback 後 prompt。
   - `openai_previous_response_id` 不被沿用。

4. 兩個 maintenance 同時完成：
   - 不會遺失 memory chunk。
   - 不會重複 append。
   - commit status 可正確區分 committed／duplicate／stale。

5. LLM timeout、429、embedding failure：
   - canonical state 不被部分更新。
   - 原始 log 未在 memory commit 前被刪除。
   - retry／取消不會重複執行 state mutation。

6. lock ordering test：
   - transaction 期間沒有外部 I/O。
   - concurrent newgame／rollback 與 maintenance 不死結。

7. pending check race：
   - button 發送 snapshot 後 pending 被另一個 turn 消費，舊 button 只能回報 expired，不得再次骰骰子。
   - 同一角色的舊 check button 不得解析到後來建立的新 check。
   - button callback 與 `/coc check` 同時抵達時，只有一個可以 commit deterministic roll。

8. check narration freshness：
   - dice state save 與 Keeper narration 之間插入另一個 turn 時，narration 不可使用舊 mutable state。
   - provider chain 缺失或被 reset 時，Keeper 仍能從 check resolution payload 的 action context 描述正確場景，不得只回問「要在什麼場景做什麼」。

### 9.3 Regression tests

- 現有 state persistence、checkpoint、rollback、scenario use、Memory RAG、OpenAI provider、legacy Keeper 與 agentic Keeper 測試全部通過。
- Discord integration tests 依目前專案規則處理；若環境缺少 Discord credentials，可 skip 外部 API 測試，但不得 skip 純 state／RAG／provider chain tests。
- `ruff check .`、`mypy app`、`python3 -m compileall -q app tests` 與完整 `pytest` 必須在 implementation checkpoint 執行。

### 9.4 Multi-user stress／race test

本期必須加入可重複的 async 壓力測試，但預設不呼叫真實 Discord、OpenAI、embedding API。測試使用 fake transport、fake provider 與 in-memory／temporary SQLite，所有外部等待由 scenario script 控制。

#### 9.4.1 測試角色與事件

同一個 `conversation_id` 建立 5～6 個玩家與 1 個 KP Assistant：

```text
P1：普通調查行動／文字 turn
P2：skill_check，使用 /coc check
P3：skill_check，使用 Discord CheckButton
P4：SAN check → INT chained check 或 Luck decision
P5：戰鬥玩家行動／NPC 防守 choice button
P6：可選的第二個玩家 action／重複點擊 stale button
KP：KP Assistant act、away／retire、戰鬥裁定或正式 game tool
```

每一輪至少混合下列事件：

- 2 個普通 player message。
- 1 個 KP Assistant message 或 `/coc sudo ... act`。
- 1 個 pending skill check registration。
- 1 個 `/coc check` 與 1 個 button click，兩者要有一輪刻意同時抵達。
- 1 個 combat defense choice 或 NPC attack resolution。
- 1 個 Luck decision／skip。
- 1 個背景 post-turn maintenance。
- 至少一輪在 maintenance、button post 或 dice-save/narration 邊界插入另一個 turn。

#### 9.4.2 兩種測試模式

**A. Deterministic race reproduction**

fake provider 在下列 barrier 暫停，測試再啟動指定的 concurrent operation：

```text
Barrier 1：pending check 已建立、button 尚未發送
    → 另一個 request 消費／替換 pending check

Barrier 2：dice result 已保存、Keeper narration 尚未開始
    → 另一個 player turn 或 KP Assistant turn

Barrier 3：maintenance snapshot 已建立、summary 尚未完成
    → /coc newgame 或 rollback

Barrier 4：old button 已發送、pending 已被新 check 替換
    → 點擊舊 button
```

每個 barrier 都必須驗證最終 state，而不是只驗證沒有 exception。

**B. Six-user load profile**

同一 conversation 以 5～6 個玩家加 KP Assistant 發送 30～60 秒事件；使用固定 seed 產生事件順序與 0～500ms arrival jitter。每個 scenario 至少重跑 10 次，並保存：

- accepted／rejected request 數量。
- `commit_sequence`、`state_revision` 與 `timeline_id`。
- 每個 `check_id` 的 create、button post、consume、roll、narration、final commit 次數。
- stale button、duplicate roll、pending leak、lost log、state conflict、deadlock、cancelled task。
- request／router／llm.turn／lock.wait／Discord output 的 P50、P95、P99、max。

同一 conversation 的 request 依序提交是預期的 correctness 行為；壓力測試不應把「所有 request 同時完成」當成功條件，而應確認沒有錯序、漏寫、重骰或跨 timeline 污染。另加一組多 conversation 測試，確認不同 group 不會被同一把 lock 或 maintenance worker 互相阻塞。

#### 9.4.3 AI latency replay profile

依提供的 `profile-async.log` 設定 fake provider 的 replay profile。這些數值只用於測試等待與排隊，不代表每次真實 API 都必須達到相同時間：

| Component | P95 | Observed max | Stress profile |
|---|---:|---:|---|
| `llm.request.completed` | 7.33s | 16.98s | 一輪 7.3s；worst case 17s |
| `llm.turn.completed` | 10.77s | 34.69s | executor 7 iterations／6 tools，最高 35s |
| input tokens | — | 58,165 | 使用 46k～58k token large-context case |
| `lock.wait.completed` | 20.84s | 32.83s | 強制 20s queue wait，另測 35s極端值 |
| `request.completed` | 33.04s | 258.01s | 33s queue profile；258s 只作 starvation regression，不作一般 timeout |
| `rag.search.completed` | 0.93s | 4.85s | 0.9s normal；5s slow RAG |
| `embedding.batch.completed` | 0.89s | 2.30s | 0.9s normal；2.5s slow embedding |
| `discord.reply.completed` | 0.86s | 4.15s | 0.9s normal；4.5s slow Discord |
| `maintenance.completed` | — | 4.64s | 5s background maintenance |

log 中最差的 `llm.turn` 是 executor 34.69s、7 iterations、6 tool calls；最差的單次 `llm.request` 是 16.98s、約 58k input tokens。`request.completed` 的 258s 主要代表 queue／lock starvation，不能當作一般 LLM latency。壓力測試必須分開報告 provider latency 與 queue latency。

#### 9.4.4 Stress test pass criteria

在所有固定 seed、10 次重跑與 slow profile 下：

1. 每個 accepted `check_id` 最多一筆 deterministic roll commit；stale／duplicate button 不得骰第二次。
2. 每個有效 check result 都保留原始 action context、timeline 與 deterministic outcome；Keeper 不得因 context 缺失反問玩家「要在什麼場景做什麼」。
3. `state_revision` 僅遞增；不存在 silent stale overwrite、lost log、lost pending check 或跨角色 Luck 消費。
4. KP Assistant 的 turn 不得覆蓋玩家 turn；KP canonical game tool 與 OOC turn 必須維持既有 log 語意。
5. 戰鬥 defense choice、skill check、SAN／INT chain 與 Luck decision 的 button／command 只能解析目前有效 identity。
6. maintenance 在 newgame／rollback race 中只能 `committed` 或 `skipped_stale`，不得把舊 memory 寫入新 timeline。
7. 所有 request 最終進入 `completed`、`rejected` 或明確 `timeout/cancelled`；不得留下未觀察的 asyncio task。
8. 測試報告必須把「AI API 等待」「lock queue」「Discord output」分欄，不得用單一總時間推論是程式 CPU 慢。

## 10. 驗收條件

本 bug 修正只有在以下條件全部完成時才算完成：

1. `GroupState.state_revision` 在正常與競爭流程中保持遞增，沒有 stale overwrite。
2. newgame／rollback 後，舊 timeline memory 不會進目前 prompt。
3. newgame／rollback 後，舊 OpenAI response chain 不會被重用。
4. maintenance worker 不在 validation 前寫入 state 或 memory。
5. 所有 stale／duplicate／failure 結果都有明確 structured event。
6. migration 後 legacy unscoped memory 不會默默污染新 timeline。
7. stale check button 不會消費新 pending check，也不會造成第二次骰點。
8. 骰點結果可以帶著原始 action context 完成 Keeper narration，不會在 context 缺失時無理由反問玩家場景。
9. concurrency regression tests 可以穩定重現並通過。
10. implementation commit、測試結果與 changeset 範圍記錄回本文件最前方。

## 11. 未決決策與 review gate

實作前需要確認以下設計決策；若沒有另外指定，採本文件的 conservative correctness-first 預設：

1. Memory state 與 GroupState 是否透過同一個 SQLite transaction API commit。預設：是；若現有 repository 邊界無法直接做到，必須提供等價的 commit journal／recovery marker，不可退回先寫 memory 再驗證 state。
2. legacy unscoped memory 是否允許人工指定 current timeline 恢復。預設：保留但不自動搜尋，另提供明確 migration/recovery 操作。
3. scenario identity 是否已有穩定欄位可直接使用。預設：若沒有，新增穩定的 scenario identity hash；不得拿 scenario display name 當唯一防護。
4. rollback 是否清除全部 provider chain，或只清除 OpenAI chain。預設：本期至少清除 OpenAI；其他 provider 若有 server-side conversation identity，必須採同等 timeline policy。
5. 是否將 stale maintenance 結果立即重排。預設：不在 stale snapshot 上重試；下一個正常 turn 或明確 maintenance scheduler 重新以 current timeline 建立 request。

本文件已獲准進入 implementation；後續若 code shape 與 contract 有差異，必須先修改本文件並在 implementation review 中記錄理由。
