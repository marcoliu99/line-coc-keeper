# 設計規格：防劇透強化（Spoiler Protection Hardening）

## 0. 文件狀態與 Changeset Tracking

- 狀態：Spec draft，等待 review；尚未實作 runtime code。
- 整合目標：`main_v2`
- 工作 branch：`feature/spoiler-protection-hardening`
- 分支基準：`origin/main_v2`
- spec 起始 changeset：`origin/main_v2`（建立 branch 時的最新 commit）
- implementation changeset：尚未開始
- changeset range：`origin/main_v2` → 尚未實作
- 本功能不修改既有 state-loss PR #48 的 branch；所有變更從 `main_v2` 開始。

## 1. 問題與目標

目前防劇透主要依賴 Keeper/Narrator prompt 規則。程式雖然已經有 DM、KP-only visibility、章節 Context、敵人資訊過濾等保護，但沒有一個統一的「公開輸出前防劇透 gate」。因此仍有以下風險：

1. LLM 忽略 prompt，直接把劇本真相、幕後 NPC、未揭露弱點或未到章節內容寫進公開回覆。
2. `/coc setpersona` 可能被非 KP 使用者改寫，形成 prompt injection 或要求 AI 公開秘密。
3. `/coc index` 將 NPC 名稱與 HP 公開，可能提前揭露敵人資料。
4. `/coc pregen <id>` 的 `notes`／`extra_fields` 若含劇情秘密，可能在全頻道顯示。
5. `record_established_fact`、`record_clue`、scene digest、RAG context 的 visibility 規則分散，未由單一 policy 統一檢查。

目標：

- 將防劇透從「只靠 prompt」提升為「資料分級 + 權限檢查 + 公開輸出檢查」的多層防護。
- 保留 Keeper 判斷所需的完整機密資料，但避免把機密資料直接交給玩家可見輸出。
- 玩家仍可正常取得自己應得的角色卡、秘密目標、私人線索與私人手卡。
- 不改變劇情規則、骰點規則、KP Assistant sudo 或角色 ownership。
- 所有拒絕、遮蔽與降級行為都可測試且可觀察。

## 2. 範圍

### 2.1 本期包含

1. 建立集中式 spoiler policy module，統一處理：
   - `public`
   - `player_private`
   - `kp_only`
   - `future_context`
   - `internal_system`
2. 公開回覆送出前建立 output guard：
   - 檢查明確標記為 `kp_only` 的 facts/clues。
   - 檢查未解鎖章節的 scenario facts、NPC secrets、弱點與真相標記。
   - 檢查禁止公開的 internal labels、private reason、secret goal。
   - 發現疑似洩漏時，移除/改寫為中性敘述，並記錄 structured warning。
3. 將 `/coc setpersona` 限制為 KP Assistant 或 Discord Keeper。
4. 將 `/coc index` 的玩家輸出改為安全摘要；完整 NPC HP、能力與弱點只供 KP/AI 使用。
5. 將 `/coc pregen <id>` 的公開 preview 改用明確 allowlist，不輸出 `secret_goal`、敏感 `extra_fields` 或未標記公開的 scenario hook。
6. 統一 scenario image、scene digest、facts/clues 與 RAG 結果的 visibility filtering。
7. 補齊 unit、integration、property/pressure tests。

### 2.2 明確非目標

- 不保證 LLM 產出的任何文字在語意上百分之百沒有劇透；output guard 只能處理可辨識的機密標記與已知 token。
- 不將完整劇本永久拆成每位玩家獨立的 LLM session。
- 不重新設計 scenario parser、RAG ranking 或 embedding pipeline。
- 不改變 `/coc autoroll`、Luck、戰鬥、KP sudo、角色 claim/reroll 規則。
- 不把 KP Assistant 的機密內容提供給普通玩家。
- 不自動刪除既有 scenario、memory、digest 或 checkpoint 資料。

## 3. Visibility model

```text
scenario / character / combat / fact input
                 │
                 ▼
        normalize_visibility()
                 │
      ┌──────────┼──────────┐
      ▼          ▼          ▼
   public   player_private  kp_only
      │          │          │
      ▼          ▼          ▼
 public reply   DM only   KP/AI context only
```

### 3.1 Visibility contract

| Visibility | 可進公開頻道 | 可進指定玩家 DM | 可進 KP/AI context | 可進一般 player narration |
|---|---:|---:|---:|---:|
| `public` | 是 | 是 | 是 | 是 |
| `player_private` | 否 | 僅指定 owner | 是 | 只可提供該 owner 對應流程 |
| `kp_only` | 否 | 否，除非另有明確 reveal event | 是 | 否 |
| `future_context` | 否 | 否 | 僅目前解鎖後 | 否 |
| `internal_system` | 否 | 否 | 僅程式必要欄位 | 否 |

任何未識別或缺少 visibility 的 scenario secret 欄位，預設採 `kp_only`，不能預設公開。

## 4. Proposed architecture

新增 `app/spoiler_policy.py`，提供純函式與 immutable result，避免把防護散落在 Discord、legacy command、Keeper prompt 各處。

預計介面：

```python
class Visibility(StrEnum):
    PUBLIC = "public"
    PLAYER_PRIVATE = "player_private"
    KP_ONLY = "kp_only"
    FUTURE_CONTEXT = "future_context"
    INTERNAL_SYSTEM = "internal_system"

def filter_public_record(record: Mapping[str, Any]) -> dict[str, Any] | None: ...
def filter_player_record(record: Mapping[str, Any], owner_id: str) -> dict[str, Any] | None: ...
def filter_kp_context(record: Mapping[str, Any]) -> dict[str, Any] | None: ...
def sanitize_public_text(text: str, protected_terms: Sequence[str]) -> SpoilerCheckResult: ...
def redact_public_pregen(pregen: Mapping[str, Any]) -> dict[str, Any]: ...
def redact_public_scenario_index(index: Mapping[str, Any]) -> dict[str, Any]: ...
```

`sanitize_public_text()` 不應直接把整段劇情交給另一個 LLM 做檢查；第一版採 deterministic token/marker checks，避免額外 latency 與第二個 LLM 造成不穩定。

## 5. 公開輸出流程

```text
Keeper / Supervisor / legacy command produces reply
                         │
                         ▼
             collect public + private side effects
                         │
                         ├─ DM payload → owner-specific policy
                         ├─ image payload → chapter + visibility policy
                         └─ public text → spoiler output guard
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                 safe / no match              suspected leak
                         │                         │
                         ▼                         ▼
                 Discord public send       neutral fallback + warning log
```

### 5.1 Output guard policy

- 不檢查普通角色扮演中的所有名詞，避免誤傷正常劇情。
- 優先檢查明確 machine-readable secret markers、future chapter entities、`secret_goal`、`private_reason`、未公開 ability id 與 `kp_only` fact/clue text。
- guard 判定疑似洩漏時，不把原始疑似內容送到公開頻道。
- fallback 應為中性敘述，例如「局勢仍有未明之處，Keeper 暫不公開更多細節。」
- structured log 只記錄 hashed marker、reason、timeline、turn/request ID，不記錄完整秘密內容。
- 若 output guard 本身失敗，採 fail-closed：不送原始公開文字，改送固定安全 fallback。

## 6. Command policy

### 6.1 `/coc setpersona`

- 僅 KP Assistant 或 Discord Keeper 可執行。
- 玩家執行時回覆拒絕訊息，不修改 state。
- persona 內容仍需 bounded length。
- persona 不得覆蓋 spoiler policy；system-level policy 置於 persona 之後或以不可覆蓋區塊提供。

### 6.2 `/coc index`

- KP/Discord Keeper：可查看完整 index。
- 一般玩家：只能看到必要的公開描述，不顯示 HP、護甲、能力、弱點、冷卻與 private reason。
- 若沒有明確 public label，NPC entry 預設只供 KP/AI 使用。

### 6.3 `/coc pregen`

- `/coc pregens`：保留角色名稱、職業、可公開基本摘要。
- `/coc pregen <id>`：只輸出 allowlist 欄位：公開屬性、公開技能、公開背景、公開 key connection。
- 永不公開：`secret_goal`、`claimed_by` 的實際 user ID、未標記的 `extra_fields`、internal source metadata。
- 角色被 claim 後，其他玩家仍不能取得其私人秘密內容。

### 6.4 `/coc digest`

- 維持 KP/Discord Keeper-only command。
- 公開 digest 只能顯示 `public` 區塊。
- `get_digest(id)` 即使拿到有效 ID，也不能繞過 visibility policy。

## 7. Prompt/context policy

現有 `_build_static_prompt()` 與 `_build_dynamic_prompt()` 保留，但必須改成由 policy formatter 組裝：

- Executor/Keeper 可取得 `kp_only` context，但必須明確標示不可輸出。
- Narrator 只取得當回合已確定的 mechanic facts、允許的 public/player-private context，以及必要的已揭露 scenario facts。
- Player-facing narration 不應收到完整 `future_context`。
- `player_private` 只有在該玩家的 resolution/DM flow 中可被使用。
- RAG 結果在進 prompt 前必須依 timeline、chapter 與 visibility 過濾。
- `setpersona` 不能降低上述 policy 的優先級。

## 8. Data contract

Facts/clues/scenario index/image asset 新增或正規化欄位：

```json
{
  "visibility": "public | player_private | kp_only | future_context | internal_system",
  "owner_id": "optional-user-id",
  "chapter_id": "optional-chapter-id",
  "source_event_id": "stable-event-id",
  "revealed_at": "optional-UTC-timestamp"
}
```

相容策略：

1. 舊資料缺 `visibility`：facts/clues 依既有 default `public`；scenario secret/unknown fields 依保守 policy 視為 `kp_only`。
2. 不在 migration 時猜測 `extra_fields` 的語意；公開 pregen formatter 只使用 allowlist。
3. 不把完整 secrets 寫入 performance log、Discord output 或 error fallback。

## 9. 測試計畫

### 9.1 Unit tests

- visibility default、unknown visibility、owner mismatch。
- `secret_goal`、`private_reason`、NPC ability、enemy HP 不出現在 public formatter。
- `kp_only` facts/clues 不出現在 public digest。
- public image 可展示；KP-only image 對玩家拒絕；未解鎖 chapter 拒絕。
- pregen allowlist 不輸出 secret goal、claimed user ID、敏感 extra fields。
- output guard 對 marker/token 命中時回傳 neutral fallback。
- output guard exception 時 fail-closed。

### 9.2 Integration tests

- player 不能執行 `/coc setpersona`、`/coc index` 不取得完整秘密資料。
- KP Assistant/Discord Keeper 可查看完整 index/digest。
- `send_private_info` 只送 owner DM，DM 失敗不會把秘密改送 public。
- Supervisor 與 legacy Keeper 兩條輸出路徑都經過相同 public output guard。
- Discord button、KP sudo、scenario chapter advance 不繞過 visibility policy。

### 9.3 Regression / pressure tests

- 5～6 玩家、KP Assistant 同時送出含秘密線索、戰鬥、RAG、private handout 的訊息。
- 驗證公開訊息沒有 `kp_only` marker、future chapter token、secret goal。
- 驗證私人 DM 只到正確 owner。
- 驗證所有拒絕與 fallback 都保留 request/turn/timeline correlation。

## 10. Observability

新增事件：

- `spoiler.guard.checked`
- `spoiler.guard.blocked`
- `spoiler.visibility.denied`
- `spoiler.private_delivery.failed`

必要欄位：

- `request_id`
- `turn_id`
- `conversation_id_hash`
- `timeline_id`
- `visibility`
- `reason`
- `matched_term_hash`（不可記錄原文）
- `fallback_used`

## 11. 未決決策與 review gate

1. Output guard 第一版是否只擋 machine-readable secret markers，還是同時加入 scenario entity dictionary？預設：兩者都做，但 entity dictionary 只使用已標記為 `future_context` 或 `kp_only` 的項目，避免誤傷一般名詞。
2. 一般玩家是否可以看到 `/coc index` 的 NPC 名稱？預設：可以看到已公開登場的名稱，但不顯示數值與能力；未公開 NPC 只顯示「未知存在」或完全隱藏。
3. output guard 擋下回覆時是否通知 KP？預設：只寫 structured log，不在公開頻道通知；KP 可透過 debug/observability 查詢。
4. 是否允許 KP 明確 reveal 一個 `kp_only` fact？預設：本期只設計 policy hook，不自動新增 reveal command；另開後續 spec 處理可追蹤 reveal event。

本文件 review 通過前，不開始修改 runtime code。
