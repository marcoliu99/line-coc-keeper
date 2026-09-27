# 測試儲存隔離

狀態：已實作。基底：main_v2。分支：bug/test-db-isolation。

## 問題與證據

測試套件沒有 `conftest.py`。`app/config.py` 在匯入時就解析 DATA_DIR、DB_PATH、BACKUP_DIR、SCENARIO_LIBRARY_DIR、IMPORT_DIR，路徑相對於當前工作目錄，並直接建立。測試會透過真實的 repository 寫入真實資料列，因此 `pytest` 讀寫的是「在哪個 checkout 執行就用哪個」的 `data/coc_bot.db`。

兩個後果，2026-09-27 均已重現：

1. **重跑會紅。** `tests/test_narrative_correction_lifecycle.py` 以 revision 0 存檔後重新載入。第二次執行時該列已是 revision 1，`correct._save` 拋出 `StateRevisionConflict: loaded=0, current=1`。實測：第一次 7 通過，第二次 2 失敗。也就是說，套件能全綠取決於「之前沒人在這個目錄跑過」。
2. **正式資料庫可被寫入。** 若在部署用的 worktree 執行，套件會把測試用的 group 列寫進正式 bot 的 `data/coc_bot.db`。2026-09-27 檢查：正式資料庫只有兩筆真實的 `discord-channel-*`，尚未發生，但沒有任何機制阻止。

## 範圍

新增 `tests/conftest.py`，在 `app.config` 被匯入前把五個儲存路徑指向同一個拋棄式目錄；另加一項守門測試，任何路徑落在 checkout 內就失敗。

pytest 會先匯入 `conftest.py` 再匯入任何測試模組，因此環境變數在 `app.config` 執行前就設定完成。`load_dotenv()` 不覆寫已存在的變數，所以 checkout 自己的 `.env` 無法蓋過。五個路徑全部明確設定，不依賴其中四個由 DATA_DIR 推導，避免日後推導方式改變時又把寫入悄悄導回工作目錄。

沙箱以 `tempfile.mkdtemp` 每次 session 建立、結束時移除，這正是讓重跑彼此獨立的關鍵。不更動任何正式程式碼，也不更動原本失敗的那些測試。

## 測試策略

- 逐一檢查每個儲存路徑都不在 checkout 內。
- 五個路徑共用同一個沙箱根目錄，讓「某個路徑忘了改」的情況被抓到，而不是無聲逃逸。
- `DB_PATH` 不等於 checkout 的 `data/coc_bot.db`。
- 整套測試連續執行三次。

## 驗證

連續三次執行整套：1,037 通過、38 子測試，無失敗，且 checkout 內沒有產生 `data/` 或 `imports/`。暫時移除 `conftest.py` 後，同一個檔案第一次 7 通過、第二次 2 失敗，`data/` 重新出現並含 `coc_bot.db`、`groups`、`scenarios`、`backups`。

## 限制

這只隔離儲存路徑。共用其他行程層級狀態的測試——模組快取、context variable、被 patch 的全域值——不在處理範圍，彼此的執行順序相依仍然存在。守門測試檢查的是路徑指向何處，不是每次寫入都確實經過這些路徑：若有程式碼寫死路徑而非讀取 config，仍可繞過。
