# Spec: Guard Agent Enhancement（GUARD_ENABLED 開關 + 修復迴圈 fail-closed 修正）

## Changeset Tracking
- **main_v2 start**: origin/main_v2:5f7f685
- **implementation end**: 本次實作 commit（見 `git log enhancement/guard-agent -1`）；四項檢查（ruff/mypy/compileall/pytest）皆通過，389 tests passed（含新增 7 個 guard agent 測試）

## Purpose & Scope

兩件事：

1. 新增 `GUARD_ENABLED` 環境變數，控制 Guard Agent（`app/agents/guard.py`）的敘述文字修復功能是否啟用。
2. 修正 code review 過程中發現的既有邏輯缺陷：Guard Agent 修復迴圈耗盡重試次數後，**沒有對最終結果做一次驗證**，可能讓仍然驗證失敗（含系統外洩字樣、Markdown 未閉合）的文字直接送到 Discord 公開頻道。

### 0.1 現況澄清（避免跟其他機制混淆）

`app/agents/guard.py` 的職責是**敘述文字層級的系統外洩/格式修復**，跟以下機制完全獨立、互不相關：

- **state-loss-amnesia 修復（PR #48，已合併）**：處理 `run_post_turn_maintenance` 的 state revision 衝突，用 thread isolation 解決回合結果遺失問題。這是 state persistence 層級的問題，Guard Agent 完全不涉及、也沒有能力處理。
- **Spoiler output guard（PR #49，`spoiler_policy.sanitize_public_text()`）**：檢查劇透（kp_only 標記、秘密目標），命中時換成固定中性 fallback，**不呼叫 LLM**。跟 Guard Agent 是兩個獨立的檢查層，都掛在 `app/agents/supervisor.py` 的輸出流程裡（分屬 §6 步驟 6 跟 7），但職責不同。

Guard Agent 只處理 `app/agents/rule_validator.py::validate_narrative()` 抓到的 4 種問題：`[SYSTEM]` 字樣、`as an AI`、「我是一個語言模型」、`tool_call` 字樣、Markdown 代碼區塊未閉合。

### 0.2 只存在於新版 agentic pipeline

已確認 `app/keeper.py`（legacy 單一 LLM 執行路徑）**沒有**呼叫 `rule_validator`/`guard.run_repair()`——legacy path 只有 spoiler output guard 一層檢查。這個修復迴圈只存在於 `app/agents/supervisor.py`，所以這次改動範圍集中在 `supervisor.py` + `guard.py`，不需要動 `keeper.py`。

## 1. 現有邏輯與缺陷

`app/agents/supervisor.py`（目前的樣子）：

```python
max_repairs = 2
attempts = 0
while attempts < max_repairs:
    is_valid, error_reason = rule_validator.validate_narrative(reply_text)
    if is_valid:
        break
    reply_text = await guard.run_repair(message, reply_text, error_reason)
    attempts += 1
```

**缺陷：** 追蹤執行順序——`attempts=0` 檢查失敗 → 修復（第 1 次）→ `attempts=1`；`attempts=1<2` 再檢查，若仍失敗 → 修復（第 2 次）→ `attempts=2`；迴圈因 `attempts=2` 不滿足 `<2` 而結束。**第 2 次修復完的 `reply_text` 從未被驗證過**就直接被後續程式碼使用（送出到 Discord）。如果 LLM 兩次修復都沒修好，一段仍然驗證失敗的文字會原封不動送到公開頻道，且沒有任何 log 記錄「最終還是沒修好」這件事——只看得到兩次「Triggering Guard Agent」的 warning，看不出結果。

## 2. 設計

### 2.1 `GUARD_ENABLED` 開關語意

- **Rule Validator 檢查本身不受這個開關控制**——`validate_narrative()` 是純 regex 比對，沒有效能或延遲成本，沒有理由關閉。
- **`GUARD_ENABLED` 只控制「檢測到問題後要不要呼叫 LLM 修復」**：
  - `true`（預設）：檢測失敗時觸發修復迴圈（見 §2.2）。
  - `false`：檢測失敗時**不修復**，直接使用當下文字（可能是原始未修復文字），記一次 `WARNING` log + observability 事件，不重試、不呼叫 LLM。

### 2.2 修復迴圈 fail-closed 修正

新迴圈邏輯（修正原本「迴圈後未再驗證」的缺陷）：

```text
validate_narrative(reply_text)
        │
   ┌────┴────┐
   ▼ valid    ▼ invalid
  直接使用   GUARD_ENABLED?
              │
     ┌────────┴────────┐
     ▼ false             ▼ true
 使用當下文字          進入修復迴圈（最多 2 次）：
 + WARNING log         每次修復後重新 validate_narrative()
                        │
              ┌─────────┴─────────┐
              ▼ 修復後 valid        ▼ 2 次都還是 invalid
          使用修復後文字         **不使用最後一次修復結果**
                               改用固定中性 fallback 文字
                               + ERROR log + observability 事件
```

關鍵修正：迴圈**每次修復後立刻重新驗證**（不是「先驗證、再修復、迴圈重新開始才驗證」這種可能漏掉最後一次的寫法），且迴圈跑完後，若依然無效，**不會**把最後一次（仍然無效）的修復結果送出去，而是 fail-closed 到固定的中性 fallback 文字——跟 spoiler output guard 的 fail-closed 原則一致（spec 見 `docs/spoiler-protection-hardening_design_spec.md` §6.1）。

### 2.3 介面設計

把「驗證 + 開關判斷 + 修復迴圈 + fail-closed fallback」整個封裝進 `guard.py` 的一個新函式，讓 `supervisor.py` 的呼叫點從一段內聯迴圈簡化成一行呼叫，也讓這段邏輯可以獨立寫單元測試：

```python
# app/agents/guard.py

MAX_REPAIR_ATTEMPTS = 2
_LOOP_EXHAUSTED_FALLBACK_TEXT = "（守密人沉吟片刻，一時有些語塞，決定先按下不表。）"

async def enforce_narrative_safety(message: AgentMessage, reply_text: str) -> str:
    """驗證 reply_text；若違規且 GUARD_ENABLED，最多重試 MAX_REPAIR_ATTEMPTS
    次修復，每次修復後立刻重新驗證。全部重試後仍違規則 fail-closed 到固定
    中性文字，不會把仍然違規的內容送出去（修正原本迴圈跑完不驗證的缺陷）。
    GUARD_ENABLED=false 時直接使用當下文字，只記 log，不嘗試修復。"""
    ...
```

`supervisor.py` 呼叫點簡化為：

```python
# 6. Rule Validator & Guard Agent (Repair Loop)
reply_text = await guard.enforce_narrative_safety(message, reply_text)
```

## 3. `.env` 配置

```bash
# Guard Agent 開關。控制敘述文字驗證失敗時是否呼叫 LLM 修復（系統外洩字樣、
# Markdown 未閉合等）。Rule Validator 的檢查本身不受此開關影響，永遠執行。
# 預設 true。關閉時檢測失敗只記 log、直接送出未修復文字，不重試不呼叫 LLM。
GUARD_ENABLED=true
```

`app/config.py`：

```python
GUARD_ENABLED: bool = _env_bool("GUARD_ENABLED", default=True)
```

## 4. 測試計畫

- `validate_narrative` 通過 → 直接回傳原文，不觸發任何修復或 log。
- `GUARD_ENABLED=false` + 驗證失敗 → 回傳原始文字，記一次 WARNING，不呼叫 `run_repair`（用 mock 確認 `run_repair` 完全沒被呼叫）。
- `GUARD_ENABLED=true` + 驗證失敗 + 第一次修復後就通過 → 回傳修復後文字，`run_repair` 只被呼叫一次。
- `GUARD_ENABLED=true` + 驗證失敗 + 兩次修復後仍失敗 → 回傳固定 fallback 文字（**不是**第二次修復的結果），記一次 ERROR + `guard.repair_exhausted` observability 事件，`run_repair` 恰好被呼叫 2 次。
- Regression test：明確重現原始缺陷的情境（兩次修復都回傳仍然違規的文字），確認新版函式不會把違規文字回傳出去。

## 5. Notes

- 這是本次修復的完整範圍；不涉及 state persistence、rollback、或跟 spoiler_policy 的任何互動（兩者是平行、獨立的檢查層，這次不調整彼此的相對順序）。
- Fallback 文字內容沿用「中性、不破壞沉浸感」的設計原則（跟 spoiler_policy 的 `_NEUTRAL_FALLBACK_TEXT` 精神一致），實際文字可在 review 時調整用詞。
