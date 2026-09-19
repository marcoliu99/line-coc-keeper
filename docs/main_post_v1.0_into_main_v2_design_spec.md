# Integrate `main` Commits After `v1.0` into `main_v2`

## 1. Goal

將 `main` 上 `v1.0` tag 之後的所有 commit 整合到 `main_v2`，讓 `main_v2` 保留既有功能並取得主線後續修正與增強。

目前已確認：

- `v1.0` 是 `main_v2` 歷史中的 ancestor。
- `v1.0..origin/main` 有 11 個 commit。
- 整合來源為最新的 `origin/main`，目標基線為最新的 `origin/main_v2`。

## 2. Scope

### In scope

- 在獨立 integration branch 上整合 `origin/main` 相對於 `v1.0` 的變更。
- 保留 Git merge history，使用 merge commit 表達這次主線整合。
- 解決整合過程中的內容衝突，並檢查 unresolved merge entries。
- 執行 repository 既有測試與靜態檢查。
- 確認整合 branch 包含最新 `origin/main_v2`，再提交供 `main_v2` 審核的變更。

### Explicit non-goals

- 不直接在本地或遠端改寫 `main_v2`。
- 不修改 `v1.0` tag 或 `main` 的歷史。
- 不把目前的 `feature/enhanced-help-navigation` 工作內容混入本次整合。
- 不額外重構或改寫來源 commit 的功能；非衝突性的行為變更維持來源實作。

## 3. Integration strategy

1. 從最新 `origin/main_v2` 建立並推送 `enhancement/merge-main-post-v1.0`。
2. 在該 branch 執行 `git merge --no-ff origin/main`。
3. 若發生衝突，依 `main_v2` 現有架構與來源 commit 的意圖逐檔處理，並補測試驗證。
4. 執行 `git diff --check`、確認 `git ls-files -u` 無輸出，並跑現有測試。
5. 在提交前再次 fetch `main_v2`，確認 `origin/main_v2` 是整合 branch 的 ancestor。

這裡使用完整 merge 而不是 cherry-pick，因為 `v1.0` 已經在 `main_v2` 歷史中；Git merge 會只帶入目標 branch 尚未包含的主線 commit，同時保留主線的 merge topology。

## 4. Acceptance criteria

- `origin/main` 的 `v1.0..origin/main` 變更已出現在 integration branch。
- `origin/main_v2` 的既有內容未被遺失或回退。
- 工作樹沒有 unresolved conflict，`git diff --check` 通過。
- 既有測試與必要的整合測試通過。
- integration branch 已推送，且可透過 PR 審核後合併到 `main_v2`。

## 5. Rollback

整合只在獨立 branch 進行；若 review 發現問題，關閉或刪除該 branch 即可，不需回寫 `main_v2`。合併後若需要回退，使用該 merge commit 的 revert PR，避免重寫共享分支歷史。
