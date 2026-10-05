# 穩定的啟用中劇本來源身分

## 狀態與基線

PR A 的實作範圍，基於 `origin/main_v2` `c5a19912c2f1faf5454eb262693ddbc7dc3d0e4d`。這是 Game Opening Phase 2A 的先決條件；本 PR 不改 `/coc start` 或 conversation lock。PR B 須等本 PR merge 後才開始。

## 問題與目標

`GroupState` 有啟用中的 scenario ID、variant、章節、文字與 timeline，卻沒有來源版本。同 ID repair 可以不換 timeline 而取代 library 內容。Scenario Source Store 已有完整解析來源文字的 SHA-256 `manifest.content_hash`。目標是在安裝相應 context 的**同一次 SQLite authoritative transaction**，把這個既有完整 hash 綁定到 active state。

## 範圍與資料契約

新增向後相容的 `GroupState.active_scenario_source_hash: str`，和其他 active scenario 欄位一起序列化。`""` 表示**未驗證／未綁定**，包含 legacy snapshot 或舊 library 沒有有效來源 hash 的 context；反序列化時絕不可從目前 filesystem 猜測補值。已綁定值必須是經 Source Store 驗證之 manifest 的 64 位小寫完整 SHA-256。不建立第二種 hash 演算法，也不把截短 ID 當權威值。

有 hash 的來源，`scenario_library.load_context` 應透過既有 `read_source` 驗證取得一致的 manifest 與完整來源文字；manifest/text 不一致不可安裝。沒有 hash 的 legacy entry 可繼續讀，但維持未綁定。`scenario_activation.install_context_fields` 同時設定 `scenario_text` 與 active hash；此邊界由 Scenario Lifecycle 的首次提交、pending `new`/`fix`、scenario use、reparse，以及章節推進工具使用。newgame 的新 `GroupState` 清空 hash。checkpoint rollback 還原 snapshot 原有 hash；舊 checkpoint 保持未綁定。只有 pending 的提交不得更動 active hash。

filesystem 的 library publication 與 SQLite activation 仍非原子。僅發布候選來源不改 DB 中的**啟用中** binding；待選候選不等於 active source。同 ID V2 已發表但尚未選用時，active state 可保持 V1 hash 與 V1 context 文字，直到選擇交易提交。

## 核心流程

```text
已驗證的 library manifest + 完整來源文字
  -> load_context（章節文字 + 完整 content_hash）
  -> Scenario Lifecycle transition／chapter advance
  -> install_context_fields 同時設定 scenario_text 與 active_scenario_source_hash
  -> 一次 authoritative state transaction
  -> commit 後才刷新衍生圖片
```

同 ID repair 測試必須證明 scenario ID 與 timeline 仍是 S/T，而 active hash 由 H1 變 H2。失敗或衝突的 final commit 保持 H1。reparse 可先發表 candidate；發表本身不推進 active hash。

## 測試與 seam

穿過真實暫存 SQLite、真實 library 發表、Scenario Lifecycle／command seam，檢查持久 state 與獨立取得的 manifest hash，而非 helper 呼叫次數。至少覆蓋 PDF/Markdown 首次啟用、New Upload、同 ID Repair、scenario use、reparse、pending 保留原 binding、失敗／stale activation、章節推進、序列化 round-trip、legacy 缺 hash。只在昂貴 parser 邊界 mock。

相關 scenario/source/transaction tests 後執行完整 `python -m pytest -q`、`ruff check .`、`mypy app`、`git diff --check`，才可判斷 PR A ready。

## 非目標與未解邊界

不解鎖開場抽取、不改 opening UX、不加 opening claim、不改 PDF/Markdown parsing、不急著遷移既有 row、不改 scenario transition policy、不把 filesystem 與 SQLite 假裝成原子交易。未綁定 legacy state 在 Phase 2A 應保留粗鎖路徑；該政策屬 PR B，本 PR 不實作。如果無法在每條 active transition 可靠綁定完整來源 hash，Phase 2A 必須 HOLD。
