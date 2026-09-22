# Spec: Remove Dead Legacy `/coc` Command Handler

## Changeset Tracking
- **main_v2 start**: origin/main_v2:c134e6d
- **implementation end**: 本次實作 commit（見 `git log bug/remove-dead-legacy-coc-command-handler -1`）；四項檢查（ruff/mypy/compileall/pytest）皆通過，350 tests passed

## Purpose & Scope

在實作 spoiler-protection-hardening（PR #49）過程中發現：`app/legacy_commands.py` 裡的 `_handle_coc_command` 函式（約 900 行）是孤兒程式碼，**沒有任何呼叫點**。

驗證方式：
```
grep -rn "_handle_coc_command" app/     # 只有定義，沒有呼叫
grep -rn "from app.legacy_commands"     # 其他檔案只 import Reply/SendImage 等輔助類型
```

實際的指令路由是 `app/commands/router.py` → `app/commands/handlers/{character,system,combat,map_handler}.py`，這些 handler 已經是真正生效的實作。`_handle_coc_command` 看起來是舊版路由被拆分成多個 handler 模組後遺留下來、忘記刪除的舊實作。

目標：安全地移除這段死代碼，減少維護負擔與未來修改時的混淆風險（例如 PR #49 的防劇透修正就曾被誤放進這段死代碼裡，後來才發現要 revert）。

### ⚠️ 實作前發現：測試也在測死代碼路徑

原本以為 `_handle_coc_command` 完全零呼叫點，但 grep 範圍加入 `tests/` 後發現 `tests/test_kp_assistant_v2.py::test_kp_ooc_lifecycle_cleanup_commands` 有 4 處直接呼叫 `commands._handle_coc_command(...)`，測的是 `/coc kp quit`、`/coc kp`、`/coc end`、`/coc newgame` 對 `kp_ooc_log`（KP OOC 對話紀錄）生命週期清理的行為。

查證結果：這段清理邏輯在真正生效的 `app/commands/handlers/system.py` 裡**都有等價實作**（`kp_ooc_log = []`，`system.py:414/436/482`；`/coc newgame` 則是整個 state 重置達到同樣效果，`system.py:389`），**不是功能缺失**，純粹是測試沒跟著遷移到新路徑。目前 `handlers/system.py` 的這段邏輯**零測試覆蓋**（`grep -rln kp_ooc_log tests/` 只找到這一個測死代碼的檔案）。

因此範圍擴大一項：**先把這 4 個測試案例改寫成呼叫真正的 `handle_system_command`，再刪除死代碼**，避免刪除後這段邏輯變成無測試覆蓋。

## Changes

- 刪除 `app/legacy_commands.py` 裡的 `_handle_coc_command` 函式本體
- 確認並一併清理只被這個函式使用、其他地方用不到的 private helper（需要實作時逐一確認每個 helper 的其他呼叫點，不要連坐刪除仍被 handlers/ 使用的共用函式，例如 `_pregen_full_sheet_text` 這類已確認被 `character.py` 等 handler 共用的函式要保留）
- 確認 `app/discord_bot.py`、`app/commands/__init__.py`、`app/commands/router.py` 等 import `legacy_commands` 的地方，import 列表中若有項目只為了這個死函式存在，一併清理
- **新增**：改寫 `tests/test_kp_assistant_v2.py::test_kp_ooc_lifecycle_cleanup_commands`，把呼叫目標從 `commands._handle_coc_command(...)` 換成 `app.commands.handlers.system.handle_system_command(...)`（或透過 `app.commands.router` 走完整路由），驗證同樣的 4 個場景（`/coc kp quit`、`/coc kp`、`/coc end`、`/coc newgame` 對 `kp_ooc_log` 的影響）在真實路徑上成立
- 不改動任何實際生效的指令行為本身（`app/commands/handlers/*.py` 的邏輯不變，只補測試）

## Testing Strategy

- 先確認改寫後的測試在刪除死代碼**之前**就能通過（證明測的是真實路徑，不是巧合）
- 刪除死代碼後跑一次完整 `pytest tests/`，確認總測試數量與刪除前一致或增加（不應減少，因為舊測試是「改寫」不是「刪除」）
- `ruff check`、`mypy`、`compileall` 確認沒有殘留的未使用 import 或型別錯誤
- 手動 grep 確認刪除後沒有任何地方還引用被移除的函式/helper 名稱

## Notes

- 這不再是純粹零風險的死代碼清理——多了一步「把測試遷移到真實路徑」，但範圍依然限定在這一個功能點，不擴大到其他重構
- 若刪除過程中發現某個 helper 其實有隱藏的間接呼叫（例如透過字串反射、動態 dispatch），要立刻停下來重新確認，不要盲目刪除
- 若在遷移測試時發現真實路徑的行為跟死代碼路徑有任何不一致（不只是 `kp_ooc_log`，還要留意 reply 文字、狀態欄位等細節），要如實記錄下來，不能為了讓測試通過而放寬斷言
