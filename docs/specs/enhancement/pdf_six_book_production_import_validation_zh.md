# 六本 production scenario import 驗證：preflight 停止

狀態：Phase 0 BLOCKED；**尚未完成六本 production import baseline**。分支 `enhancement/pdf-multicolumn-ingestion`，code baseline `105fe065c87a3cb204d138cfc432d488e8461e4a`。本輪未改 runtime code；provider transport、import、extraction、draft、publication、activation、start 均未執行。依使用者 network safety preflight 的 STOP 規則停止；Haunting canary 未安全完成前，後五本不能開始。

## Preflight 配置

讀取現有環境與 production checkout `.env`，未輸出 credentials；只隔離 storage paths。Provider：OpenAI，model：`gpt-6-luna`，credential configured：yes。Identity：`multicolumn-v9`／`image-map-certification-v2`／`source-topology-v3`／`semantic-source-proof-v1`。建立報告前 worktree clean；main_v2 merge base `6024adfffad39860de2142e87dbf33e056f4960a` 是 ancestor。

Production persistent cache 中 `PP-OCRv5_mobile_rec` manifest 已驗證；CPU worker 是既有 main venv，但缺 Paddle 套件。Tesseract 5.5.3 已安裝。Docling enabled，但套件/cache 不可用。本輪未下載模型、安裝 dependency；這是環境狀態，並非 OCR quality 結果。

Production caps：layout image requests 8/book、pages 4/book、layout retries 1（每次新 transport 仍應有 reservation）；semantic windows 4/import、3 adjacent pages/window、12,000 Unicode chars/window、8 candidates/window；local OCR attempts 8、AI repair attempts 8。Timeout：layout image/inventory 30 秒；connectivity/repair/audit 60 秒；Docling 45 秒；Paddle 60 秒；semantic text 30 秒。現有通用 OpenAI retry config 是 3，另有 timeout retry 1；installed SDK default retries 是 2。完整 sanitized identity/settings 於 JSON。

## 真實 corpus 與實際 timeline

| PDF | 頁數 | 使用者提供的 provider-OFF HARD_BLOCK | 本輪 |
|---|---:|---:|---|
| 01_The_Haunting_QuickStart.pdf | 50 | 23 | NOT_RUN_PREFLIGHT_BLOCKED |
| 02_Dead_Boarder.pdf | 32 | 9 | NOT_RUN_CANARY_NOT_COMPLETED |
| 03_The_Lightless_Beacon.pdf | 43 | 26 | NOT_RUN_CANARY_NOT_COMPLETED |
| 04_Camp_Sunny.pdf | 28 | 7 | NOT_RUN_CANARY_NOT_COMPLETED |
| 05_Scritch_Scratch.pdf | 42 | 25 | NOT_RUN_CANARY_NOT_COMPLETED |
| 06_Alone_Against_the_Flames.pdf | 50 | 15 | NOT_RUN_CANARY_NOT_COMPLETED |

六個原檔已唯讀確認 SHA-256／頁數，沒有修改 PDF。每本僅走 PDF identity → static/offline policy inspection → preflight STOP。完整 SHA-256 列於 EN twin 與 `pdf_six_book_production_import_results.json`。

Canonical source disposition、image classification、topology、map 指標均未量測，JSON 使用 null；不是零錯誤。Library save/reload、activation、`/coc start` 未到達；沒有任何一本標為 IMPORT PASS。Provider-OFF 數字是使用者提供的歷史基準，不是本輪重跑；沒有 ON 結果可比較，更不能宣稱減少 HARD_BLOCK。

已追蹤最高 production seam：`app.commands.router.handle_text_message` 的 `/coc scenario import` → `system._handle_local_import` → `legacy_commands.handle_pdf_upload`。Status／Continue／use／start 也必須經 router。本輪因該完整路徑的外部請求違反 preflight contract，**沒有 invoke**；未用底層 extractor harness 冒充 import。

## Network STOP：P1

1. **Semantic text retry/accounting**：`openai_provider.analyze_text(max_retries=0)` 雖關閉 SDK retry，仍走 `_create_response`；該 compatibility/transient loop 可令一個 discovery reservation 產生多次 actual transport。SDK zero retries 不會關閉這個 loop。
2. **AI repair retry/accounting**：`pdf_ai_repair.repair_page` 呼叫 `analyze_image` 未傳 timeout／max_retries；OpenAI adapter 因此保留 SDK default 2 retries 與 compatibility/transient retry。它的 decrement-only attempt list 也不是 persisted transport reservation。
3. **整本 source privacy／未 reservation 的文字請求**：upload eagerly 將全文交給 scenario index；pregen fallback 也交全文；start opening extraction 收到完整 active scenario text。這些 caller 未傳 zero-retry／timeout，也沒有 durable reservation。直接跑原有流程會違反本輪 bounded-unit 授權。

Static trace 顯示 map stages、image transcription、layout worker 與 MarkItDown image client 有 explicit zero-retry／timeout／reservation 路徑；這不是 live transport 驗證。本輪沒有 monkeypatch 掉不安全路徑、提高 cap 或 fake provider success。

## Reading-order／GitHub review blockers

兩個已知 layout 風險仍可用本機 **synthetic policy counterexample** 重現，與真實 corpus import 結果分開：`_validate` 接受 internal full-width heading 排到其上方 body 之前，也接受 footer 排在 body 之前。即使未來 corpus 恰好不踩到，admission rule 仍不安全。這些 probes 不含 copyrighted source，不能當作 import success。

未解決 threads：

- [heading inverse](https://github.com/marcoliu99/line-coc-keeper/pull/155#discussion_r4144259666)：P1。
- [margin geometry](https://github.com/marcoliu99/line-coc-keeper/pull/155#discussion_r4144600290)：GitHub 標 P2；因 unsafe publication 風險，本輪列為 merge-holding P1。
- [merged-part continue cleanup](https://github.com/marcoliu99/line-coc-keeper/pull/155#discussion_r4144600298)：P2。Draft 後 continue 缺 selected-part cleanup provenance；本輪未執行成功的 resumed merged import。

## Authority 與 request accounting

Corbitt 已確認是完整 50 頁 QuickStart；p24／p28–29 仍只是使用者提供的 physical hints，尚未由 production canonical import 確認。未宣稱 two-barrier／transit certification；false-certified topology 尚未量測，不能報 0。Beacon 的 Service Room／Lamp Room／Lantern Gallery／stairs／floor topology 也未到達。

Requests／reservations／transports 都是 0；accounting 表無 dispatched rows。`transport <= reservations` 只是 vacuously true，並非 request accounting 驗證成功。沒有 tokens／usage／cost 數據，不估算不存在的費用。Raw preflight evidence 保存在 repository 外 private 目錄；public JSON 只保存 hashes、頁數、settings、status 與 sanitized defects。

## Verification 與 readiness

本輪僅 reports/catalog 變更；commit 前執行 `git diff --check` 與 git status。本輪**未重新跑** pytest／ruff／mypy／compileall。引用同一 code baseline `105fe06` 的最近完整證據：pytest 2,181 passed、2 skipped、152 subtests passed；ruff passed；mypy passed（138 modules）；compileall passed；diff-check passed。既有 green suite 不能蓋過這次 preflight P1。沒有 implementation change，故本輪 independent implementation Standards／Spec review 不適用。

**PR155 MERGE HOLD**：network/accounting 與 publication-order P1 未解決，GitHub review threads 仍開啟。

**Production PDF ROLLOUT HOLD**：尚無真實 provider-ON E2E import 完成 admission → persistence → activation → start。

後續：先對具體 preflight P1 加 sanitized regression 並做最小修正，保留 canonical/topology/map gates 與有限 budget，再从全新隔離 state 重啟六本 baseline。不得跳 gate 讓 canary 過關。
