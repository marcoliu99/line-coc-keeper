# Python 3.13 預熱程序生命週期與既有模型搬移

## 問題與目標

Python 3.13 沒有 `ProcessPoolExecutor.terminate_workers()`。劇本 RAG 預熱仍須是可選工作，卡住時不能阻塞 Bot shutdown 或 asyncio 主事件迴圈。已準備的 OCR 與 layout 模型也需要安全複製到固定 production 目錄。

## 範圍與非目標

劇本預熱只使用一個 `multiprocessing.Process`，在 worker thread 啟動，並以 public `terminate`、`kill`、`join` 停止。新增一個明確執行的模型複製 CLI。不修改 `get_index`、RAG 檢索、PDF 擷取、OCR／layout 推論、依賴版本或遊戲流程。

## 狀態與檔案契約

不變更資料庫或遊戲狀態 schema。parent 保留 process 與非同步完成 waiter；wrapper 取消後由 `shutdown_prewarm` 負責回收 child。啟動／停止 lock 避免 shutdown 與正在進行的 spawn 競爭。正常完成記錄 `rag.prewarm.completed`，失敗記錄 `rag.prewarm.failed`。shutdown 保留 grace period 與 `rag.prewarm.shutdown_degraded`；若 `kill` 後仍未退出，取消 waiter，避免 shutdown 無限等待。

搬移 CLI 必須明確提供兩個目的地；預設來源是 `~/.cache/line-coc-keeper/paddleocr` 與 `~/.cache/line-coc-keeper/paddle-layout`，可另外指定來源。完整性直接使用 `app.pdf_ocr.models_ready()` 與 `app.pdf_layout.model_ready()`。寫入前檢查所有路徑與來源，只複製指定模型到同層暫存目錄，再次驗證後 rename 到正式位置。舊目的地若不完整，先備份；發布失敗就復原。路徑交疊一律拒絕。完整目的地直接 no-op。除非明確指定 `--remove-source`，否則保留來源；即使指定，也只刪指定模型目錄。CLI 不下載模型、不修改 `.env`。

## 驗證

測試故意放慢 process start 時 asyncio 仍可執行、正常完成、wrapper 取消、grace timeout、terminate、kill 備援與 kill 後仍無法退出的 bounded shutdown。模型搬移測完整／不完整來源、dry-run、重複執行、部分目的地、安全 rollback、交疊路徑、預設保留與明確刪除來源。執行 Python 3.13／3.14 全套 pytest，以及 ruff、mypy、compileall、diff-check 和 GitHub Python 3.13 CI。

## 取捨

每次可選預熱啟動一個 process，不再重用 pool；仍維持先前的單 worker 上限，且 Python 3.13／3.14 使用相同 public API。每個目的地各自原子發布；兩組模型不構成共同交易。
