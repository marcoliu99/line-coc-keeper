# 本地 OCR 實證比較 — 2026-10-01

[English / 詳細方法及重現步驟](pdf_ocr_benchmark_validation.md) · [JSON](pdf_ocr_benchmark_results.json)

**Outcome C：PaddleOCR 值得作為限定 image-based characteristic table 的 challenger；全域仍保留 Tesseract primary。** 尚未選定 production recognizer，也沒有實作 integration。英文 mobile 與 server 在本輪核心品質相同；不因 mobile 是 default 就指定它。

以 main_v2 `189bc8e` 為基準，另讀取並測試 PR155 `ead28a4`。共 **13 張真實頁、26 個相同 PNG 輸入**：13 個 region、13 個 full-page；沒有 synthetic OCR 頁。Haunting 11/14/20、Dead Boarder 3/5/17/20/31、Beacon 4/6/13/27/34。四個實際 production suspect crops 來自 Dead 3、Beacon 4 的 replacement-character 警告，實際內容是目錄標題。角色卡 crop 是人工選取的代表區域，不冒充 production repair selection。

採 300 DPI，native crops 重用既有 bbox padding / page intersection。**12 個有限區域**經視覺核對 gold，才納入品質摘要；整頁 native text 只是未核對的 reference candidate。沒有整頁 sentence hallucination 正確率宣稱。PDF、原文、gold、圖像、完整 OCR 都留在 repo 外。

| 指標 | Tesseract | en mobile | server |
|---|---:|---:|---:|
| numeric exact | 50/103（48.54%） | 98/103（95.15%） | 98/103（95.15%） |
| 現有 raw pair validator | 22/50 | 34/50 | 34/50 |
| dice exact 原始大小寫 | 5/6 | 6/6 | 6/6 |
| skill 名稱＋百分比 | 4/4 | 4/4 | 4/4 |
| lexical coverage | 77.26% | 98.53% | 94.13% |
| added numeric occurrences | 7 | 0 | 0 |
| 兩張表的 main-stat geometry pairs | 1/16 | 16/16 | 16/16 |

重用 pdf_quality 的 numeric_pairs/check_pairs/_NUMBER/_WORD/accept_region。headline numeric 僅拆開已知 label 黏接（SAN1 → SAN 1），沒有修補 glyph、數值、前導零或骰式大小寫。未拆接的既有 `_NUMBER` 另列為 38/103、98/103、97/103；SAN1 是 parser spacing 問題，不能說成 1 被讀成 I。dice casefold sensitivity 三者均 6/6。

最有力的特定類別證據是兩張角色卡 characteristic tables：numeric **6/54 → 50/54**，真實 OCR boxes 的 geometry pairing **1/16 → 16/16**。只用單 token boxes，不猜多字 segment 內的 word 座標。raw pairing 仍另列，Paddle 拆行的表格字串不能直接通過它。兩模型均漏掉四個淡色 +1/-1 年齡修正值；Tesseract 也漏掉，不能稱完整角色卡 recovery。

逐頁 raw region 分類：兩模型都勝於 Dead 3/17/31、Beacon 4、Haunting 14；Haunting 11、Beacon 13 平手；Beacon 34 為數字改善但 raw pairing 退步的 mixed；其餘五頁沒有可評分 region，標不可比較。geometry 補驗則確認 Dead 31、Beacon 34 兩張表的改善。沒有 Tesseract win 或 both-failed 的已評分頁；JSON 保留 per-input 分母、錯誤、missing/added、分類及 regression 清單。不能從 aggregate 推論每個欄位都改善。

使用官方穩定 PaddleOCR 3.7.0 / PaddleX 3.7.2 / PaddlePaddle 3.3.0，隔離 Python 3.11、macOS arm64 CPU、native backend、MKLDNN off、4 threads、rec batch 6，共用 PP-OCRv5_server_det（max side 960），orientation/unwarping 關閉。明確模型設定會使 lang=en 被忽略；實際字集由 en mobile / multilingual server 決定。與 bot Python 3.14 不同，未實測 Linux/CI 部署。

Tesseract 5.5.3 維持 production chi_tra+eng → eng、pytesseract default PSM 與既有 CLI psm 6 fallback；traineddata hash 已記錄。region warm medians **264 / 1363 / 1226 ms**，full-page **2796 / 15599 / 12985 ms**；peak RSS **0.12 / 6.96 / 8.53 GiB**。初始化、首次 inference、三次 warm 樣本及 CPU 分開記錄。執行順序固定且部分重疊，不能用這些數字判定速度勝者。

一次 default-detector mobile 長嘗試未完成第一張四次量測 artifact（至少 246 秒）後中止，exit -4 原樣保留；改明確 960 設定後完成。這不等於已證實 dependency 不相容，也沒有拿 Tesseract 冒充 Paddle 成功。

setup 才下載公開模型，archives 共 181,217,280 bytes；hash、cache path、setup 時間均在 JSON。venv 約 1.0 GiB，archive＋expanded model 約 358 MiB。推論全部由 macOS sandbox **deny network***，沒有 cloud OCR、文件 upload、ingestion auto-download 或 bot startup hook。cache `/private/tmp/coc-ocr-models`、private gold/results 在 `/private/tmp/coc-ocr-private` / `coc-ocr-extra`；系統清理前需自行保存這些本地暫存資料。

最小後續方案是 optional 本地 adapter，限定已辨識 characteristic crop，保留 boxes 做 deterministic validation，再走 Tesseract fallback；無足夠 source evidence 時留 draft/manual review，不補猜 age modifier 或 Luck。現有 graphic route 是 scene_map/vision，local OCR 只是文字 fallback；本次不宣稱已驗證自動 crop eligibility。後續可能涉及 pdf_loader、optional adapter、pdf_quality 的 public geometry seam、config/setup 與 PDF/offline/timeout tests；須另審規格。

markitdown-ocr、pdf_ai_repair、Docling ordering（OCR/table off）、scene_map、Keeper/RAG/combat/gameplay/tool routing 均未修改；沒有 Camelot、Tesseract 移除或 production default 切換。synthetic metric tests 只驗證 substitutions/swaps/duplicates/dice/blank Luck/worker failures 等，不算真實 corpus 勝利。

驗證：benchmark 分支 pytest **1617 passed / 2 skipped / 152 subtests passed**；Ruff 0.16.8 `check .` 通過；`mypy app` 通過（121 files）；compileall app/tests/scripts/experiments 與 diff check 通過。PR155 隔離回歸另為 **1689 passed / 2 skipped / 152 subtests passed**，不能混算成此分支測試數。

重現 runner 新增 parent watchdog：init／每頁四次 inference 合計 120 秒、每 engine 1800 秒；逾時終止 process group 並 reap。原始長 default 嘗試保留原始 failure，未倒填為 watchdog 成功。geometry 可用 `run_geometry` 同樣走禁網／deadline wrapper。

追加 Tesseract sensitivity（另列，未替換 production baseline）：相同 13 region PNG、每設定一次；chi_tra+eng / PSM6 為 numeric 52/103、raw pair 20/50、added numeric 2；eng / PSM6 為 54/103、22/50、added numeric 3。兩者 characteristic geometry 都 0/16。因此本輪改善不能只歸因於簡單切 PSM6 或英文-only；尚未窮舉 Tesseract 的 sparse modes／preprocessing。結果及逐區域診斷列 JSON sensitivity，單次 latency 不與 warm median 混比。可用禁網 wrapper 下的 `sensitivity --private-dir PATH --other-private-dir PATH --language eng` 重現。
