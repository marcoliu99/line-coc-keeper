# 設計規格：State Loss／Amnesia／Rollback Context Isolation 修正（TOCTOU Race Condition）

## 0. 文件狀態與 Changeset Tracking

- 狀態：Draft，等待 spec review；本階段不實作 runtime code。
- 整合目標：`main_v2`
- 工作 branch：`fix/state-loss-amnesia-hardening`
- 分支基準：`origin/main_v2`
- 本次 spec 起始 changeset：`origin/main_v2:b87625094a20fc7954ad1d05680c7987d0904812`
- implementation changeset：TBD
- 遠端 branch：`origin/fix/state-loss-amnesia-hardening`
- 本文件取代先前以「maintenance stale state revision」為主要 root cause 的草稿；實作前必須以本文件核准版本為準。
- 若 `main_v2` 在實作或 PR review 期間新增 commit，必須先 fetch、重新對齊、更新本節 changeset 範圍，再繼續實作或更新 PR。
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

此外，背景 maintenance 在長時間 LLM／embedding 工作完成後才提交結果；若期間發生新回合、newgame 或 rollback，這就是 race 的衝突窗口，必須將結果視為 stale，不能靜默寫入或誤報成功。

### 1.2 目標

1. 保證舊 timeline 的 maintenance、Memory RAG 與 provider conversation chain 不會污染新 timeline。
2. 保證正常 turn、newgame、rollback 與背景 maintenance 之間的 commit 順序可驗證。
3. 保留現有 `state_revision` CAS 保護，並補足目前缺少的 timeline／memory／provider chain 邊界。
4. 將 stale maintenance result 明確標記為 skipped，而不是假設 state 已成功保存。
5. 保持既有遊戲語意：角色、Luck roll、戰鬥、KP Assistant sudo、help、scenario continuity 的規則不因本修正改變。
6. 透過測試重現並固定「maintenance 期間 newgame／rollback」的 race condition。
7. 增加足夠 observability，使下一次可以由 `request_id`、`turn_id`、`maintenance_id`、`timeline_id` 與 revision 還原事件順序。

## 2. 範圍與明確非目標

### 2.1 本期包含

- `memory_chunks` payload 加入 timeline 與 idempotency metadata。
- Memory RAG search 預設只搜尋目前 timeline。
- Background maintenance 改為「worker 只產生結果，commit gate 才寫入 state／memory」。
- Maintenance result 加入 immutable snapshot metadata、chunk hash 與明確 commit status。
- newgame／rollback／其他 timeline-changing operation 的 provider chain reset。
- `openai_previous_response_id` 與 timeline 綁定，拒絕跨 timeline 使用。
- stale result、prefix mismatch、timeline mismatch、provider chain reset 的 structured log。
- legacy memory 與 legacy provider response ID 的安全 migration policy。
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

1. 每一個 memory chunk 必須包含 `timeline_id`；沒有 timeline 的資料視為 legacy/unscoped。
2. `search_memory(group_id, ...)` 預設必須取得目前 authoritative `timeline_id`，只搜尋該 timeline。
3. memory append 必須包含 deterministic `idempotency_key`，retry 不得重複加入同一 chunk。
4. Stale maintenance 不得先 append memory 再做 state validation。
5. Memory commit 必須在確認 timeline、log prefix／chunk identity 與 commit policy 後才可發生。
6. 若 state trim 與 memory append 不是同一個 atomic transaction，兩者至少必須使用同一個 commit gate，並在失敗時保留原始 log，不得只 trim 而遺失可重建來源。

### 3.4 Provider conversation chain

1. Provider response chain 必須與 `timeline_id` 綁定。
2. 只有當 stored chain timeline 等於目前 `GroupState.timeline_id` 時，才可傳入 `previous_response_id`。
3. rollback、newgame 與任何 timeline-changing operation 必須清除 `openai_previous_response_id`，並清除其 chain timeline metadata。
4. 新 timeline 的第一個 turn 必須從目前 static/dynamic prompt 與 history 建立新 provider chain，不得接續舊 timeline 的 server-side response chain。
5. Provider chain reset 必須產生 structured event，包含 reason、old timeline、new timeline；不得記錄完整 response ID。

### 3.5 Turn ordering

1. 每個 Discord request 有 `request_id`；每個遊戲 turn 有 `turn_id`；每個背景 maintenance 有獨立 `maintenance_id`。
2. lock wait、state load、LLM、tool、state commit 與 reply 的順序必須可由 log 重建。
3. request 排隊等待不代表 state rollback；實作必須保留 commit sequence／revision 供辨識。
4. LLM timeout、429 或 provider failure 不得部分寫入 canonical state。
5. `CancelledError` 不得被一般 fallback 捕捉成成功。

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
      └─ 建立 immutable MaintenanceRequest
           (maintenance_id, group_id, timeline_id,
            base_revision, dropped_chunk_hash, scenario_identity)
                    │
                    ▼
          background worker 只做純計算
          ├─ summary LLM
          ├─ embedding
          └─ 回傳 MaintenanceResult，不寫 state／memory
                    │
                    ▼
          commit gate（conversation lock + state lock + DB transaction）
                    │
                    ├─ load latest GroupState
                    ├─ 驗證 current timeline == request.timeline
                    ├─ 驗證 scenario identity（若適用）
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
/coc rollback 或 /coc newgame
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

## 5. 資料結構與 migration

### 5.1 MaintenanceRequest

Maintenance request 是 immutable snapshot，不可由 worker 重新讀取舊 caller object 取代：

```python
@dataclass(frozen=True)
class MaintenanceRequest:
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

### 5.2 MaintenanceResult

```python
@dataclass(frozen=True)
class MaintenanceResult:
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
  "created_at": "UTC timestamp",
  "source_revision": 123
}
```

`text` 仍不得出現在 performance log；資料庫內的既有 memory data 不因本功能自動刪除。

### 5.4 Provider chain metadata

`GroupState` 必須能判斷 stored response chain 所屬 timeline。可採用下列任一等價實作，但實作前需在 code review 中固定一種：

- 新增 `openai_previous_response_timeline_id`。
- 或將 response chain metadata 收納為 `{response_id, timeline_id}`。

缺少 chain timeline metadata 的 legacy state 不得直接信任其 response ID；migration 應清除該 ID，讓下一次 turn 建立新 chain。

### 5.5 Legacy migration policy

1. 先對 SQLite 做 backup，再進行 payload migration。
2. 沒有 `timeline_id` 的既有 memory chunk 標記為 `legacy_unscoped=true`，預設不進入目前 RAG prompt。
3. legacy chunk 必須保留在資料庫，供人工檢查或日後明確指定 timeline 後恢復；不可直接假設它屬於目前 timeline。
4. 沒有 chain timeline metadata 的既有 `openai_previous_response_id` 必須清除；這只會讓下一個 turn 重新建立 provider context，不會刪除 GroupState log。
5. migration 必須可重複執行，且不得重複建立 memory chunk 或改變 state revision。
6. 若 migration 發現不合法 payload，保留原始內容於 backup／quarantine，記錄 warning，不阻塞整個 bot 啟動；但該 chunk 不得被搜尋。

## 6. Commit gate 與 lock ordering

### 6.1 Commit validation

maintenance result 進入 commit gate 後必須重新讀取 authoritative state，依序檢查：

1. `group_id` 相同。
2. `current.timeline_id == request.timeline_id`。
3. scenario identity 相同，除非該 scenario operation 明確定義為同一 campaign continuity。
4. `dropped_chunk` 仍是 current log 的預期 prefix，或使用等價且可驗證的 chunk/event identity。
5. `idempotency_key` 尚未成功 commit。
6. result 成功且 summary／embedding 通過基本 schema validation。

`base_revision` 不必要求 current revision 完全相等；正常新 turn 可以在 maintenance 執行期間追加 log。只要 timeline 與被刪除 chunk identity 仍一致，就可以將 maintenance result 套用到最新 state。revision 差異必須寫入 log，供診斷。

### 6.2 Lock ordering

所有會同時接觸 conversation、GroupState 與 memory 的流程固定採用：

```text
conversation lock
    → per-group state lock
        → SQLite transaction
            → state + memory commit
```

不得在持有 SQLite transaction 時等待 LLM、embedding、Discord API 或其他外部 I/O。worker 的慢工作必須在 commit gate 外完成。

### 6.3 Stale result policy

| 驗證結果 | State | Memory | Retry／後續處理 |
|---|---|---|---|
| timeline mismatch | 不寫 | 不寫 | 丟棄並記錄；新 timeline 重新建立 maintenance |
| scenario mismatch | 不寫 | 不寫 | 依 scenario policy 丟棄；不可混用摘要 |
| prefix/chunk mismatch | 不寫 | 不寫 | 丟棄；下一個 turn 可重新 snapshot |
| duplicate idempotency key | 不重寫 | 不重寫 | 視為 idempotent success，記錄 duplicate |
| summary/embedding failure | 不寫 | 不寫 | 保留原始 log；依既有 retry policy 處理 |
| validation success | 寫入 | 寫入 | transaction commit |

「不寫」必須對應明確的 `maintenance.commit.skipped` event，不得回報 `state_saved=true`。

## 7. Provider chain reset policy

### 7.1 必須 reset 的操作

- `/coc newgame`。
- `/coc rollback`。
- 產生新 timeline 的 scenario reset／campaign reset。
- 任何 restore state 的管理操作。

### 7.2 不必 reset 的操作

- 同一 timeline 內的正常玩家 turn。
- 不改變 campaign identity 的普通 scenario context 更新；但 maintenance result 仍應檢查 scenario identity 是否符合該 operation 定義。

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

1. Memory chunk 缺 `timeline_id` 時標記 legacy/unscoped 且不被預設 search 返回。
2. Memory search 只返回 current timeline。
3. 相同 idempotency key 重試不重複 append。
4. `MaintenanceRequest` 的 hash／idempotency key 穩定且不受 list mutation 影響。
5. provider chain timeline 不同時自動清除 `previous_response_id`。
6. rollback／newgame 產生新 timeline 並清除 provider chain metadata。
7. stale result 不會寫 state，也不會寫 memory。
8. prefix mismatch 不會把 `state_saved` 誤報為 true。

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

### 9.3 Regression tests

- 現有 state persistence、checkpoint、rollback、scenario use、Memory RAG、OpenAI provider、legacy Keeper 與 agentic Keeper 測試全部通過。
- Discord integration tests 依目前專案規則處理；若環境缺少 Discord credentials，可 skip 外部 API 測試，但不得 skip 純 state／RAG／provider chain tests。
- `ruff check .`、`mypy app`、`python3 -m compileall -q app tests` 與完整 `pytest` 必須在 implementation checkpoint 執行。

## 10. 驗收條件

本 bug 修正只有在以下條件全部完成時才算完成：

1. `GroupState.state_revision` 在正常與競爭流程中保持遞增，沒有 stale overwrite。
2. newgame／rollback 後，舊 timeline memory 不會進目前 prompt。
3. newgame／rollback 後，舊 OpenAI response chain 不會被重用。
4. maintenance worker 不在 validation 前寫入 state 或 memory。
5. 所有 stale／duplicate／failure 結果都有明確 structured event。
6. migration 後 legacy unscoped memory 不會默默污染新 timeline。
7. concurrency regression tests 可以穩定重現並通過。
8. implementation commit、測試結果與 changeset 範圍記錄回本文件最前方。

## 11. 未決決策與 review gate

實作前需要確認以下設計決策；若沒有另外指定，採本文件的 conservative correctness-first 預設：

1. Memory state 與 GroupState 是否透過同一個 SQLite transaction API commit。預設：是；若現有 repository 邊界無法直接做到，必須提供等價的 commit journal／recovery marker，不可退回先寫 memory 再驗證 state。
2. legacy unscoped memory 是否允許人工指定 current timeline 恢復。預設：保留但不自動搜尋，另提供明確 migration/recovery 操作。
3. scenario identity 是否已有穩定欄位可直接使用。預設：若沒有，新增穩定的 scenario identity hash；不得拿 scenario display name 當唯一防護。
4. rollback 是否清除全部 provider chain，或只清除 OpenAI chain。預設：本期至少清除 OpenAI；其他 provider 若有 server-side conversation identity，必須採同等 timeline policy。
5. 是否將 stale maintenance 結果立即重排。預設：不在 stale snapshot 上重試；下一個正常 turn 或明確 maintenance scheduler 重新以 current timeline 建立 request。

在使用者核准本 spec 前，不開始 runtime implementation。
