# Bug Fix：防止待處理檢定重複執行

## 問題描述

同一個骰子檢定（如格鬥檢定）在某些情況下會被執行**兩次**，導致重複的 `pending_checks` 記錄。

### 現象
- 用戶執行一個操作（如攻擊）
- AI Keeper 註冊了待處理檢定 (pending=True)
- 經過一段時間（2 分鐘+）或狀態重新載入
- 同樣的檢定被再次執行，參數**完全相同**
- 結果：日誌中出現重複的骰子結果

### 根本原因

`GroupState.pending_checks` 在數據庫中持久化。當遠端系統（如 ExecutorAgent）重新載入狀態時：

1. 舊的 `pending_checks` 被重新加載
2. AI 代理或某個流程看到待處理檢定仍存在
3. 檢定被重新註冊，但沒有檢查是否已經相同
4. 導致完全重複的檢定被執行多次

## 解決方案

### 防重複檢查機制

新增 `_is_identical_pending_check()` 函數，用來檢查新的待處理檢定是否與現有的**完全相同**。

當檢定被重新提交時，檢查流程：

```
是否已有待處理檢定？
  ├─ 無 → 正常註冊新檢定
  └─ 有 → 是否完全相同？
       ├─ 是 → 返回已存在的結果，不重新保存（should_save=False）
       └─ 否 → 拒絕（已有不同的待處理檢定）
```

### 受影響的工具

1. **skill_check** - 技能檢定
2. **offer_check_choice** - 防守選項
3. **offer_npc_attack_defense_choice** - NPC 攻擊防守

`skill_check` 與 `offer_check_choice` 對完全相同的重播採 idempotent reuse；不同
request 會拒絕。`sanity_check` 與 `offer_npc_attack_defense_choice` 則維持既有
pending 時直接拒絕，尤其 NPC 攻擊流程不能重新使用或丟棄攻擊方骰子。

## 實現細節

### 新增函數

```python
def _is_identical_pending_check(existing: dict, new_check_dict: dict) -> bool:
    """比較待處理檢定是否相同，支援 skill / sanity / choice 三種類型"""
```

### 修改的工具處理

每個工具的 mutator 都添加：

```python
# 防重複：如果已經有完全相同的待處理檢定，直接返回結果而不重新保存
existing = target_state.pending_checks.get(target_char.owner_id)
if existing and _is_identical_pending_check(existing, new_check):
    return _StateMutation({...已存在的結果...}, should_save=False)
```

## 測試策略

### 手動測試場景

1. **執行單一檢定，等待狀態重新加載**
   - 執行格鬥檢定 → 待玩家響應
   - 模擬狀態重新載入或 turn 重複
   - 驗證：不出現重複的骰子結果

2. **檢查日誌中的 revision 序列**
   - 同一操作不應該導致多個不同的 revision
   - 防重複成功時應該看到 "should_save=False"

### 自動化測試（如果需要）

- 模擬 ExecutorAgent 重複呼叫相同工具
- 驗證待處理檢定只被註冊一次
- 驗證掷骰結果一致（不會有多個不同的 roll 值）

## 影響範圍

- **正向**：防止因狀態重新加載導致的意外重複
- **向後相容**：不改變 API，只改變內部邏輯
- **副作用**：如果真的需要重新檢定，必須明確清除舊的 pending_checks 再提交

## 後續考慮

1. 是否需要在數據庫層面添加去重邏輯？
2. 是否應該在 pending_checks 中保存時間戳，用來判斷何時該過期？
3. 是否需要對其他類型的待處理狀態（如 pending_luck_decisions）做同樣的防重複？
