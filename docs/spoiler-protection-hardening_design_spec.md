# 設計規格：防劇透強化（Spoiler Protection Hardening）

## 0. 文件狀態與 Changeset Tracking

- 狀態：Spec draft v3，等待 review；尚未實作 runtime code。
- 整合目標：`main_v2`
- 工作 branch：`feature/spoiler-protection-hardening`
- 分支基準：`origin/main_v2`
- spec 起始 changeset：`origin/main_v2`（建立 branch 時的最新 commit）
- implementation changeset：尚未開始
- changeset range：`origin/main_v2` → 尚未實作
- 本功能不修改既有 state-loss PR #48 的 branch；所有變更從 `main_v2` 開始。

### 0.1 v2 變更摘要（相對 v1 draft）

1. **移除** `/coc setpersona` 限制為 KP Assistant/Discord Keeper 的規則（不再是本期目標）。
2. **新增**全局環境變數開關，統一控制本 spec 新增的 output guard，**以及**專案既有的防劇透機制。
3. **新增**現有防劇透機制盤點（§2.3），明確列出目前已存在、分散在各檔案的防護點，並把它們一起掛進開關。
4. 補齊所有流程圖（開關決策流程、完整輸出流程、現有機制整合流程、command 流程）。

### 0.2 v3 變更摘要（相對 v2 draft，本次 review 確認）

1. **確定採用兩層開關**（原 v2 §3.3 的折衷方案，經 review 確認採用，不再是備選項）：
   - `SPOILER_PROTECTION_ENABLED`：控制「劇透防護」類機制（章節限制、KP-only 摘要、公開摘要、NPC/Narrator/劇本防劇透 prompt、output guard、`/coc index`、`/coc pregen`）。
   - `PRIVACY_ISOLATION_ENABLED`：控制「基礎隱私隔離」類機制（私人資訊 DM、秘密目標、私人手卡/圖片歸屬、戰鬥隱藏資訊、敵方傷害過濾），**預設 true 且強烈建議正式環境恆為 true**。
2. §3、§3.4、§5.1、§6、§8、§10、§11、§12 全面更新為兩層開關版本。

## 1. 問題與目標

目前防劇透主要依賴 Keeper/Narrator prompt 規則，搭配分散在多個檔案的程式權限過濾（DM-only、KP-only visibility、章節 Context、戰鬥資訊過濾等）。這些機制確實存在且運作中（詳見 §2.3 盤點），但呈現「多處各自為政、沒有單一開關、沒有統一輸出層檢查」的狀態，因此仍有以下風險：

1. LLM 忽略 prompt，直接把劇本真相、幕後 NPC、未揭露弱點或未到章節內容寫進公開回覆——目前沒有最後一層「公開輸出前的程式級掃描」。
2. `/coc index` 將 NPC 名稱與 HP 公開，可能提前揭露敵人資料。
3. `/coc pregen <id>` 的 `notes`／`extra_fields` 若含劇情秘密，可能在全頻道顯示。
4. `record_established_fact`、`record_clue`、scene digest、RAG context 的 visibility 規則分散在不同模組（`app/keeper.py`、`app/scene_digest.py`、`app/combat.py`、`app/services/prompt_config.py`…），未由單一 policy 統一檢查、統一開關。
5. 現有機制全部「常開」，沒有一個地方可以整體關閉／開啟以利除錯、KP 自訂玩法、或壓力測試時暫時放寬。

目標：

- 將防劇透從「分散的 prompt 規則 + 程式過濾」提升為「資料分級 + 權限檢查 + 公開輸出檢查」的多層防護，且**全部由單一 `.env` 開關統一控制**。
- 保留 Keeper 判斷所需的完整機密資料，但避免把機密資料直接交給玩家可見輸出。
- 玩家仍可正常取得自己應得的角色卡、秘密目標、私人線索與私人手卡。
- 不改變劇情規則、骰點規則、KP Assistant sudo 或角色 ownership。
- 不改變 `/coc setpersona` 現有權限模型（本期不處理）。
- 所有拒絕、遮蔽與降級行為都可測試且可觀察。

## 2. 範圍

### 2.1 本期包含

1. 建立集中式 spoiler policy module，統一處理：
   - `public`
   - `player_private`
   - `kp_only`
   - `future_context`
   - `internal_system`
2. 新增全局開關 `SPOILER_PROTECTION_ENABLED`（詳見 §3），統一控制：
   - 本 spec 新增的 output guard（公開回覆送出前掃描）
   - §2.3 盤點出的所有既有防劇透機制
3. 公開回覆送出前建立 output guard：
   - 檢查明確標記為 `kp_only` 的 facts/clues。
   - 檢查未解鎖章節的 scenario facts、NPC secrets、弱點與真相標記。
   - 檢查禁止公開的 internal labels、private reason、secret goal。
   - 發現疑似洩漏時，移除/改寫為中性敘述，並記錄 structured warning。
4. 將 `/coc index` 的玩家輸出改為安全摘要；完整 NPC HP、能力與弱點只供 KP/AI 使用。
5. 將 `/coc pregen <id>` 的公開 preview 改用明確 allowlist，不輸出 `secret_goal`、敏感 `extra_fields` 或未標記公開的 scenario hook。
6. 統一 scenario image、scene digest、facts/clues 與 RAG 結果的 visibility filtering，並掛上全局開關。
7. 補齊 unit、integration、property/pressure tests，含開關 on/off 兩種狀態的測試。

### 2.2 明確非目標

- **不變更 `/coc setpersona` 的現有權限規則**（v1 draft 曾規劃限制為 KP-only，v2 移除此項）。
- 不保證 LLM 產出的任何文字在語意上百分之百沒有劇透；output guard 只能處理可辨識的機密標記與已知 token。
- 不將完整劇本永久拆成每位玩家獨立的 LLM session。
- 不重新設計 scenario parser、RAG ranking 或 embedding pipeline。
- 不改變 `/coc autoroll`、Luck、戰鬥、KP sudo、角色 claim/reroll 規則。
- 不把 KP Assistant 的機密內容提供給普通玩家。
- 不自動刪除既有 scenario、memory、digest 或 checkpoint 資料。

### 2.3 現有防劇透機制盤點（分別掛進兩層開關，見 §3）

目前專案的防劇透機制是「Prompt 規則 + 部分程式權限過濾」，分散在以下位置。本期依機制性質，分別收斂到 `SPOILER_PROTECTION_ENABLED`（劇透防護）或 `PRIVACY_ISOLATION_ENABLED`（隱私隔離）之下：

| # | 功能 | 位置 | 防劇透方式 | 歸屬開關 |
|---|---|---|---|---|
| 1 | 私人資訊 DM | `app/keeper.py:653`、`app/legacy_commands.py:709` | `send_private_info` 只送指定玩家 DM，失敗時不 fallback 到公開頻道 | `PRIVACY_ISOLATION_ENABLED` |
| 2 | 秘密目標 | `app/models.py:263` | `keeper_notes_text()` 只給 Keeper；`sheet_text()` 不輸出 `secret_goal` | `PRIVACY_ISOLATION_ENABLED` |
| 3 | 私人手卡/圖片 | `app/keeper.py:2456` | `visibility != public` 時，普通玩家不能搜尋或展示 | `PRIVACY_ISOLATION_ENABLED` |
| 4 | 章節限制 | `app/keeper.py:2480`、`scenario_library.load_context()` | 圖片只能取目前 Context 章節 | `SPOILER_PROTECTION_ENABLED` |
| 5 | KP-only 摘要 | `app/scene_digest.py:110` | private 內容只放 Keeper prompt | `SPOILER_PROTECTION_ENABLED` |
| 6 | 公開摘要 | `app/scene_digest.py:28` | `_public_state()` 只保留 public facts/clues | `SPOILER_PROTECTION_ENABLED` |
| 7 | 戰鬥隱藏資訊 | `app/combat.py:1123` | 敵人 HP、能力等只在 `include_private=True` 時提供 | `PRIVACY_ISOLATION_ENABLED` |
| 8 | 敵人傷害資訊 | `app/keeper.py:1386` | `_filter_public_combat_damage_result()` 過濾敵方內部欄位 | `PRIVACY_ISOLATION_ENABLED` |
| 9 | NPC 防劇透 | `app/keeper.py:2770` | Prompt 禁止 NPC 直接講真相、弱點、最佳路線 | `SPOILER_PROTECTION_ENABLED` |
| 10 | Narrator 防劇透 | `app/services/prompt_config.py:79` | Narrator 只依照既定機制結果敘事 | `SPOILER_PROTECTION_ENABLED` |
| 11 | 劇本防劇透規則 | `app/keeper.py:2722`、`2750`、`2753`、`2759`、`2770` | 要求只逐步揭露，不公開幕後真相與條件式旁白 | `SPOILER_PROTECTION_ENABLED` |

**歸屬邏輯：**

- **`PRIVACY_ISOLATION_ENABLED`**（機制 #1、#2、#3、#7、#8）：這些是「這份資料本來就屬於誰」的基礎隱私問題（玩家私訊、秘密目標、私人手卡歸屬、戰鬥/傷害內部數值），跟「劇情該不該被提前看到」是不同性質的風險，因此獨立一層，且**建議正式環境恆為 true**。
- **`SPOILER_PROTECTION_ENABLED`**（機制 #4、#5、#6、#9、#10、#11 + 新增 output guard、`/coc index`、`/coc pregen`）：這些是「劇情揭露節奏」問題，KP 可能因為自訂玩法、除錯、或特殊場次需求想暫時放寬，因此獨立成可調整的一層。

**風險備忘（沿用自審計，未變）：**

- 沒有最後一層的輸出劇透掃描器（→ 本期 §6 output guard 補上）。
- `/coc index` 公開 NPC 名稱與 HP（→ §7.1 修正）。
- `/coc pregen <id>` 的 `notes`/`extra_fields` 可能洩漏（→ §7.2 修正）。
- 劇本全文在 RAG 關閉時直接放入 Keeper prompt，防護完全依賴 LLM 遵守 Prompt（→ 維持現狀，output guard 作為最後一道防線）。

## 3. 全局開關設計（兩層開關）

### 3.1 `.env` 配置

```bash
# 劇透防護開關。控制「劇情揭露節奏」相關機制（章節限制、KP-only 摘要、
# 公開摘要、NPC/Narrator 防劇透 prompt、output guard、/coc index、/coc pregen）。
# 預設 true。KP 可視場次需求調整。
SPOILER_PROTECTION_ENABLED=true

# 隱私隔離開關。控制「資料歸屬」相關機制（私人 DM、秘密目標、私人手卡、
# 戰鬥隱藏資訊、敵方傷害過濾）。預設 true，正式環境強烈建議恆為 true，
# 僅供本機開發/除錯時關閉。
PRIVACY_ISOLATION_ENABLED=true
```

對應 `app/config.py` 新增：

```python
SPOILER_PROTECTION_ENABLED: bool = _env_bool("SPOILER_PROTECTION_ENABLED", default=True)
PRIVACY_ISOLATION_ENABLED: bool = _env_bool("PRIVACY_ISOLATION_ENABLED", default=True)
```

### 3.2 兩層開關的關係

兩層開關**互相獨立**，任何一層都可以單獨開關，不互相牽動：

```text
                        ┌─────────────────────────────┐
                        │  SPOILER_PROTECTION_ENABLED  │──▶ 劇情揭露節奏類機制
                        │      （劇透防護層）           │    （§2.3 機制 #4/#5/#6/
                        └─────────────────────────────┘     #9/#10/#11 + output
                                                              guard + index/pregen）

                        ┌─────────────────────────────┐
                        │  PRIVACY_ISOLATION_ENABLED   │──▶ 資料歸屬類機制
                        │      （隱私隔離層）           │    （§2.3 機制 #1/#2/
                        └─────────────────────────────┘     #3/#7/#8）
```

### 3.3 開關決策流程（單一檢查點的判斷邏輯）

每個檢查點只查詢「自己歸屬的那一層」開關，不會同時查兩層：

```text
                     防劇透 / 隱私檢查點被呼叫
                              │
                              ▼
                  這個檢查點屬於哪一層？（見 §2.3 對照表）
                              │
              ┌───────────────┴───────────────┐
              ▼ 劇透防護類                      ▼ 隱私隔離類
   查 SPOILER_PROTECTION_ENABLED      查 PRIVACY_ISOLATION_ENABLED
              │                                │
      ┌───────┴───────┐                ┌───────┴───────┐
      ▼ true            ▼ false         ▼ true           ▼ false
  正常執行過濾邏輯    略過檢查，直接放行  正常執行過濾邏輯   略過檢查，直接放行
  （output guard /   （return 原始資料， （DM-only /       （return 原始資料，
   章節限制 / KP-only  不套用遮蔽）       秘密目標隱藏 /     不套用遮蔽）
   摘要…）                               戰鬥隱藏資訊…）
      │                  │                  │                 │
      ▼                  ▼                  ▼                 ▼
 依結果放行或        記錄 debug log     依結果放行或       記錄 warning log
 遮蔽 / fallback    (spoiler.protection  遮蔽 / fallback   (privacy.isolation
                     .disabled)                             .disabled，等級較高，
                                                             因為風險較高)
```

### 3.4 每個既有機制掛開關後的行為

| # | 機制 | 歸屬開關 | 開關 = true（現狀） | 開關 = false |
|---|---|---|---|---|
| 1 | 私人資訊 DM | `PRIVACY_ISOLATION_ENABLED` | 只送 owner DM | **跳過隔離**，可能改走一般回覆路徑（僅建議開發環境使用） |
| 2 | 秘密目標 | `PRIVACY_ISOLATION_ENABLED` | `sheet_text()` 不輸出 | 秘密目標可能出現在一般輸出 |
| 3 | 私人手卡/圖片 | `PRIVACY_ISOLATION_ENABLED` | 非 public 不可搜尋 | 不檢查 visibility，允許搜尋 |
| 4 | 章節限制 | `SPOILER_PROTECTION_ENABLED` | 只取目前 Context 章節圖片 | 允許取任意章節圖片 |
| 5 | KP-only 摘要 | `SPOILER_PROTECTION_ENABLED` | 只放 Keeper prompt | 可能被一般 formatter 讀取 |
| 6 | 公開摘要 | `SPOILER_PROTECTION_ENABLED` | 只留 public facts/clues | 保留全部 facts/clues |
| 7 | 戰鬥隱藏資訊 | `PRIVACY_ISOLATION_ENABLED` | `include_private=True` 才給 | 一律視為可公開 |
| 8 | 敵人傷害資訊 | `PRIVACY_ISOLATION_ENABLED` | 過濾敵方內部欄位 | 不過濾 |
| 9 | NPC 防劇透 prompt | `SPOILER_PROTECTION_ENABLED` | 注入禁止洩漏規則 | 不注入該段 prompt |
| 10 | Narrator 防劇透 prompt | `SPOILER_PROTECTION_ENABLED` | 同上 | 同上 |
| 11 | 劇本防劇透規則 | `SPOILER_PROTECTION_ENABLED` | 注入 | 不注入 |
| 新 | Output guard（本 spec） | `SPOILER_PROTECTION_ENABLED` | 掃描並攔截 | 不掃描，直接送出 |
| 新 | `/coc index` 摘要化 | `SPOILER_PROTECTION_ENABLED` | 一般玩家看安全摘要 | 回傳完整 index |
| 新 | `/coc pregen` allowlist | `SPOILER_PROTECTION_ENABLED` | 只輸出 allowlist 欄位 | 回傳完整 pregen 資料 |

**設計理由：** 兩層開關讓 KP 可以單獨放寬「劇情揭露節奏」（例如自由劇本、除錯、直播模式）而不必連帶關掉「玩家私人資料保護」；反過來，`PRIVACY_ISOLATION_ENABLED` 預設不建議關閉，因為它保護的是玩家個人資料（秘密目標、私訊內容），跟劇本設計無關。

## 4. Visibility model

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

### 4.1 Visibility contract

| Visibility | 可進公開頻道 | 可進指定玩家 DM | 可進 KP/AI context | 可進一般 player narration |
|---|---:|---:|---:|---:|
| `public` | 是 | 是 | 是 | 是 |
| `player_private` | 否 | 僅指定 owner | 是 | 只可提供該 owner 對應流程 |
| `kp_only` | 否 | 否，除非另有明確 reveal event | 是 | 否 |
| `future_context` | 否 | 否 | 僅目前解鎖後 | 否 |
| `internal_system` | 否 | 否 | 僅程式必要欄位 | 否 |

任何未識別或缺少 visibility 的 scenario secret 欄位，預設採 `kp_only`，不能預設公開。

此表僅在 `SPOILER_PROTECTION_ENABLED=true` 時生效；關閉時 visibility 標記仍保留在資料中，但 filter 函式一律放行（見 §3.3）。

## 5. Proposed architecture

新增 `app/spoiler_policy.py`，提供純函式與 immutable result，避免把防護散落在 Discord、legacy command、Keeper prompt 各處。所有函式第一步都檢查自己歸屬的那一層開關（見 §2.3、§3.4 對照表）。

預計介面：

```python
class Visibility(StrEnum):
    PUBLIC = "public"
    PLAYER_PRIVATE = "player_private"
    KP_ONLY = "kp_only"
    FUTURE_CONTEXT = "future_context"
    INTERNAL_SYSTEM = "internal_system"

def is_spoiler_protection_enabled() -> bool:
    """讀取 config.SPOILER_PROTECTION_ENABLED，供「劇透防護類」函式共用。"""
    ...

def is_privacy_isolation_enabled() -> bool:
    """讀取 config.PRIVACY_ISOLATION_ENABLED，供「隱私隔離類」函式共用。"""
    ...

# 劇透防護類（查 is_spoiler_protection_enabled）
def sanitize_public_text(text: str, protected_terms: Sequence[str]) -> SpoilerCheckResult: ...
def redact_public_pregen(pregen: Mapping[str, Any]) -> dict[str, Any]: ...
def redact_public_scenario_index(index: Mapping[str, Any]) -> dict[str, Any]: ...
def filter_kp_context(record: Mapping[str, Any]) -> dict[str, Any] | None: ...

# 隱私隔離類（查 is_privacy_isolation_enabled）
def filter_public_record(record: Mapping[str, Any]) -> dict[str, Any] | None: ...
def filter_player_record(record: Mapping[str, Any], owner_id: str) -> dict[str, Any] | None: ...
```

`sanitize_public_text()` 不應直接把整段劇情交給另一個 LLM 做檢查；第一版採 deterministic token/marker checks，避免額外 latency 與第二個 LLM 造成不穩定。

### 5.1 函式內部開關檢查流程

**劇透防護類函式**（`sanitize_public_text` / `redact_public_pregen` / `redact_public_scenario_index` / `filter_kp_context`）：

```text
filter_xxx(record, ...) 被呼叫
              │
              ▼
     is_spoiler_protection_enabled()?
              │
   ┌──────────┴──────────┐
   ▼ true                 ▼ false
 依 visibility 規則      直接 return record（不遮蔽）
 進行過濾 / 遮蔽                  │
   │                              ▼
   ▼                    observability.event(
 return 過濾後結果         "spoiler.protection.disabled",
                           level=DEBUG, fn=filter_xxx.__name__)
```

**隱私隔離類函式**（`filter_public_record` / `filter_player_record`）：

```text
filter_xxx(record, owner_id, ...) 被呼叫
              │
              ▼
     is_privacy_isolation_enabled()?
              │
   ┌──────────┴──────────┐
   ▼ true                 ▼ false
 依 owner_id / visibility  直接 return record（不遮蔽）
 規則進行過濾                     │
   │                              ▼
   ▼                    observability.event(
 return 過濾後結果         "privacy.isolation.disabled",
                           level=WARNING, fn=filter_xxx.__name__)
                         （等級比劇透防護高，因為關閉隱私
                          隔離的風險較大，值得多留意）
```

## 6. 公開輸出流程（整合既有機制 + 新 output guard，標注各自歸屬開關）

```text
Keeper / Supervisor / legacy command 產生 reply
                         │
                         ▼
             收集 public + private side effects
                         │
        ┌────────────────┼──────────────────────────┐
        ▼                ▼                           ▼
  DM payload        image payload               public text
  (機制#1/#2                │                         │
   秘密目標/DM 隔離)          │                         ▼
        │                    │              SPOILER_PROTECTION_ENABLED?
        ▼                    │                         │
PRIVACY_ISOLATION_ENABLED?    │              ┌──────────┴──────────┐
        │                    │              ▼ true                 ▼ false
 ┌──────┴──────┐             │        output guard 掃描：       直接送出
 ▼ true         ▼ false      │        - kp_only marker           (現狀)
owner-specific  直接送出       │        - future_context
policy check    (不隔離)       │        - secret_goal
        │                    │        - private_reason
        ▼                    ▼                   │
  送 DM / 拒絕      ┌─────────┴─────────┐   ┌──────┴──────┐
                    ▼                   ▼   ▼ safe/no      ▼ suspected
              機制#3 私人手卡        機制#4 章節限制    match          leak
              歸屬 PRIVACY_          歸屬 SPOILER_          │              │
              ISOLATION_ENABLED     PROTECTION_ENABLED      ▼              ▼
                    │                   │              Discord        neutral
              ┌─────┴─────┐       ┌─────┴─────┐       public send    fallback +
              ▼ true       ▼ false ▼ true       ▼ false               structured
        僅 owner 可搜尋  允許搜尋  只取目前章節  允許任意章節           warning log
```

### 6.1 Output guard policy

- 不檢查普通角色扮演中的所有名詞，避免誤傷正常劇情。
- 優先檢查明確 machine-readable secret markers、future chapter entities、`secret_goal`、`private_reason`、未公開 ability id 與 `kp_only` fact/clue text。
- guard 判定疑似洩漏時，不把原始疑似內容送到公開頻道。
- fallback 應為中性敘述，例如「局勢仍有未明之處，Keeper 暫不公開更多細節。」
- structured log 只記錄 hashed marker、reason、timeline、turn/request ID，不記錄完整秘密內容。
- 若 output guard 本身失敗，採 fail-closed：不送原始公開文字，改送固定安全 fallback。
- **此 guard 完全由 `SPOILER_PROTECTION_ENABLED` 控制**；關閉時不執行掃描，直接送出原始文字。

## 7. Command policy

> v1 draft 原 §6.1 的 `/coc setpersona` KP-only 限制**已移除**，本期不變更 `/coc setpersona` 權限模型。

### 7.1 `/coc index`

```text
/coc index 被呼叫
        │
        ▼
SPOILER_PROTECTION_ENABLED?
        │
 ┌──────┴──────┐
 ▼ true         ▼ false
呼叫者是 KP /    回傳完整 index
Discord Keeper?  （現狀，不分角色）
 │
 ├─ 是 → 回傳完整 index（HP/能力/弱點）
 │
 └─ 否 → redact_public_scenario_index()
          → 只回傳已公開登場名稱 + 安全摘要
          → 不顯示 HP、護甲、能力、弱點、冷卻、private reason
          → 未標記 public 的 NPC 預設只供 KP/AI
```

### 7.2 `/coc pregen`

```text
/coc pregen <id> 被呼叫
        │
        ▼
SPOILER_PROTECTION_ENABLED?
        │
 ┌──────┴──────┐
 ▼ true         ▼ false
redact_public_pregen()   回傳完整 pregen 資料（現狀）
        │
        ▼
只輸出 allowlist 欄位：
公開屬性、公開技能、公開背景、公開 key connection
        │
        ▼
永不公開：secret_goal、claimed_by 實際 user ID、
未標記的 extra_fields、internal source metadata
```

- `/coc pregens`：保留角色名稱、職業、可公開基本摘要（不受開關影響，本來就是公開清單）。
- 角色被 claim 後，其他玩家仍不能取得其私人秘密內容（機制 #2，見 §3.4）。

### 7.3 `/coc digest`

```text
/coc digest 被呼叫（KP/Discord Keeper-only command，權限檢查不變）
        │
        ▼
SPOILER_PROTECTION_ENABLED?
        │
 ┌──────┴──────┐
 ▼ true         ▼ false
_public_state()           get_digest(id) 回傳完整內容
只顯示 public 區塊         （不過濾 kp_only 區塊）
（機制 #6）
```

- `get_digest(id)` 即使拿到有效 ID，開關開啟時也不能繞過 visibility policy。

## 8. Prompt/context policy

現有 `_build_static_prompt()` 與 `_build_dynamic_prompt()` 保留，但必須改成由 policy formatter 組裝：

- Executor/Keeper 可取得 `kp_only` context，但必須明確標示不可輸出。
- Narrator 只取得當回合已確定的 mechanic facts、允許的 public/player-private context，以及必要的已揭露 scenario facts。
- Player-facing narration 不應收到完整 `future_context`。
- `player_private` 只有在該玩家的 resolution/DM flow 中可被使用。
- RAG 結果在進 prompt 前必須依 timeline、chapter 與 visibility 過濾。
- 機制 #9/#10/#11（NPC 防劇透、Narrator 防劇透、劇本防劇透規則）的 prompt 注入段落，改由 `is_spoiler_protection_enabled()` 控制是否加入 system prompt（見 §3.4 對應行）。

```text
build_dynamic_prompt() 組裝流程
        │
        ▼
SPOILER_PROTECTION_ENABLED?
        │
 ┌──────┴──────┐
 ▼ true         ▼ false
注入防劇透 prompt 區塊：    略過防劇透 prompt 區塊
- NPC 不得透露真相/弱點      （LLM 仍可能自行避免，但
- Narrator 僅依機制結果敘事    無程式強制規則）
- 逐步揭露規則
        │
        ▼
組合 static + dynamic prompt
```

## 9. Data contract

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

## 10. 測試計畫

### 10.1 Unit tests

- **開關測試（新增）**：`SPOILER_PROTECTION_ENABLED` 與 `PRIVACY_ISOLATION_ENABLED` 各自 true/false，共 4 種組合下，每個 filter 函式與 output guard 的行為差異。
- visibility default、unknown visibility、owner mismatch。
- `secret_goal`、`private_reason`、NPC ability、enemy HP 不出現在 public formatter（`PRIVACY_ISOLATION_ENABLED=true` 時）。
- `kp_only` facts/clues 不出現在 public digest（`SPOILER_PROTECTION_ENABLED=true` 時）。
- public image 可展示；私人手卡對非 owner 拒絕（`PRIVACY_ISOLATION_ENABLED=true` 時）；未解鎖 chapter 拒絕（`SPOILER_PROTECTION_ENABLED=true` 時）。
- pregen allowlist 不輸出 secret goal、claimed user ID、敏感 extra fields（`SPOILER_PROTECTION_ENABLED=true` 時）。
- output guard 對 marker/token 命中時回傳 neutral fallback（`SPOILER_PROTECTION_ENABLED=true` 時）。
- output guard exception 時 fail-closed（不受任何開關影響，例外處理永遠 fail-closed）。
- `SPOILER_PROTECTION_ENABLED=false` 時，output guard 完全略過，不呼叫底層掃描邏輯（驗證效能與行為）。
- `PRIVACY_ISOLATION_ENABLED=false` 時，DM/秘密目標/戰鬥隱藏資訊完全略過過濾（驗證行為符合 §3.4，且記錄 WARNING 等級 log）。

### 10.2 Integration tests

- `/coc index`、`/coc pregen`、`/coc digest` 在 `SPOILER_PROTECTION_ENABLED` on/off 下的輸出差異。
- KP Assistant/Discord Keeper 可查看完整 index/digest（不受任一開關影響，權限檢查獨立）。
- `send_private_info` 只送 owner DM，DM 失敗不會把秘密改送 public（`PRIVACY_ISOLATION_ENABLED=true` 時）。
- Supervisor 與 legacy Keeper 兩條輸出路徑都經過相同 public output guard（`SPOILER_PROTECTION_ENABLED=true` 時）。
- Discord button、KP sudo、scenario chapter advance 不繞過 visibility policy（兩開關皆 true 時）。
- 兩層開關獨立切換：`SPOILER_PROTECTION_ENABLED=false` + `PRIVACY_ISOLATION_ENABLED=true`（放寬劇情但保護隱私）與反向組合，驗證互不干擾。

### 10.3 Regression / pressure tests

- 5～6 玩家、KP Assistant 同時送出含秘密線索、戰鬥、RAG、private handout 的訊息。
- 驗證公開訊息沒有 `kp_only` marker、future chapter token、secret goal（兩開關皆 true 時）。
- 驗證私人 DM 只到正確 owner（`PRIVACY_ISOLATION_ENABLED=true` 時）。
- 驗證所有拒絕與 fallback 都保留 request/turn/timeline correlation。
- 驗證開關切換（runtime 重啟後生效）不會導致既有 state 資料損毀。

## 11. Observability

新增事件：

- `spoiler.guard.checked`
- `spoiler.guard.blocked`
- `spoiler.visibility.denied`
- `spoiler.private_delivery.failed`
- `spoiler.protection.disabled`（`SPOILER_PROTECTION_ENABLED=false` 時，各劇透防護類函式第一次略過檢查時記錄，等級 DEBUG，避免洗版）
- `privacy.isolation.disabled`（`PRIVACY_ISOLATION_ENABLED=false` 時，各隱私隔離類函式第一次略過檢查時記錄，等級 **WARNING**，因為風險較高，值得比劇透防護更醒目）

必要欄位：

- `request_id`
- `turn_id`
- `conversation_id_hash`
- `timeline_id`
- `visibility`
- `reason`
- `matched_term_hash`（不可記錄原文）
- `fallback_used`
- `spoiler_protection_enabled`（記錄 `SPOILER_PROTECTION_ENABLED` 當下狀態）
- `privacy_isolation_enabled`（記錄 `PRIVACY_ISOLATION_ENABLED` 當下狀態）

## 12. 未決決策與 review gate

1. Output guard 第一版是否只擋 machine-readable secret markers，還是同時加入 scenario entity dictionary？預設：兩者都做，但 entity dictionary 只使用已標記為 `future_context` 或 `kp_only` 的項目，避免誤傷一般名詞。
2. 一般玩家是否可以看到 `/coc index` 的 NPC 名稱？預設：可以看到已公開登場的名稱，但不顯示數值與能力；未公開 NPC 只顯示「未知存在」或完全隱藏。
3. output guard 擋下回覆時是否通知 KP？預設：只寫 structured log，不在公開頻道通知；KP 可透過 debug/observability 查詢。
4. 是否允許 KP 明確 reveal 一個 `kp_only` fact？預設：本期只設計 policy hook，不自動新增 reveal command；另開後續 spec 處理可追蹤 reveal event。
5. ~~§3.3 折衷設計：是否採單一開關，還是拆成兩層？~~ **已於 v3 確認採用兩層開關**（`SPOILER_PROTECTION_ENABLED` + `PRIVACY_ISOLATION_ENABLED`），見 §3。
6. `PRIVACY_ISOLATION_ENABLED=false` 的預期使用情境是什麼？是否僅限本機開發/測試環境，正式營運環境是否應強制 `true`（例如啟動時 warning 或拒絕以 false 啟動）？**待 review 確認**。
7. **（新增）**`SPOILER_PROTECTION_ENABLED=false` 是否也該在正式環境跳出 warning？預設：只記錄一次啟動時 log（`spoiler.protection.disabled` at startup），不阻擋啟動，因為這一層本來就設計給 KP 依場次調整。

本文件 review 通過前，不開始修改 runtime code。
