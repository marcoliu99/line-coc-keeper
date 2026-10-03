# PR155 公平 OCR 與 image-only 驗證

[English](pdf_ocr_image_only_validation.md)

Runtime 修正已完成；**production rollout 維持暫緩**。實作 commits：a583f53、cf62607、62f76db、1c93712；6bb9a79 已對齊最新 main_v2；分支 enhancement/pdf-multicolumn-ingestion。舊 v3/v4 comparison 混入 hidden OCR 與舊 vision/local fallback，不能當 Paddle acceptance threshold。

## Controlled offline Paddle OFF/ON

[JSON 結果](pdf_ocr_controlled_ab_results.json)。同最終 code hash、dependencies、102 個實體頁、local/AI/layout budgets；兩邊 Docling disabled、provider credentials 清空且 socket transport denied，PyMuPDF4LLM hidden OCR 明確 OFF，只改 Paddle enabled。使用同一 persistent model manifest、PP-OCRv5_mobile_rec／PaddleOCR 3.7.0／PaddlePaddle 3.3.0／PaddleX 3.7.2、CPU。暫存 Python interpreter 僅供本次驗證，model cache 仍為持久化目錄。

| 書籍 | Review OFF / ON | Blocked OFF / ON | 秒 OFF / ON |
| --- | --- | --- | --- |
| The_Haunting_Scenario_trimmed.pdf | 13 / 13 | 13 / 13 | 29.4 / 134.2 |
| Dead Boarder.pdf | 10 / 10 | 9 / 9 | 25.1 / 198.7 |
| The Lightless Beacon - Call of Cthulhu.pdf | 27 / 27 | 26 / 26 | 30.7 / 101.0 |
| 合計 | 50 / 50 | 48 / 48 | 85.3 / 434.0 |

兩邊都有 11 個 unresolved native-empty pages，accepted region repair 與 authoritative page transcription 皆 0。Native numeric/dice loss 與 known native pair failure 皆 0；沒有原 accepted/legacy safe page 因 Paddle ON 變 blocked；regression list 為空。Paddle ON 有 16 region attempts、22 page attempts、37 native-gate rejections、1 empty attempt，保留 21 個 preferred Paddle page candidates 為 private/unverified（包含 partial-native pages）。兩邊 Tesseract attempts 都是 38。Local OCR 不消耗 provider dispatch budget。

另以目前可取得的 10 個 visually verified、hash-bound real crops 重測；這不是先前 12 crops／103 numbers 的 denominator。採 production numeric token grammar：Tesseract numeric 32/49、Paddle 44/49；added numeric 2→0；exact dice 5/6→6/6；known pair failures 15→2。無 native 的頁面不能以空 baseline 當 zero-error 證明。所列為 shared validation host 的 cold-worker elapsed time，不是隔離的效能 benchmark，不宣稱 warm throughput。

答案：Paddle 在此有限 reference subset 改善 local candidate 品質，但 offline accepted recovery／unresolved page counts 沒有改善。

## Provider-enabled 真實 production paths

[JSON 結果](pdf_ocr_production_path_results.json)。同最終 runtime、Paddle enabled；ON 使用既有 MarkItDown/provider，OFF 清空 credentials；Docling 依 production config、hidden OCR OFF。四個未改動的真實實體頁分別抽取，保留原 PDF hash 與 physical-page provenance；不是 full-corpus 付費評估。此驗證限制 registry image calls 為 60 秒、無 retry。MarkItDown 每次實際 image completion 與 direct verification 都先共用 durable provider request/page budget 做 reserve/checkpoint；converter 不能繞過耗盡額度。

- The Haunting p20：真正 native-empty 調查員表。有用的 Paddle 候選保留，但 independent mechanics 衝突，維持 unverified，不進 canonical source；尚未證明真實 image-only authoritative positive acceptance。
- Lightless Beacon p5：真正無文字插圖。Paddle empty 加上獨立 AI illustration 分類，Tesseract noise 僅存 diagnostic/private，不發布；不再單因無法認證 OCR prose 而阻擋。
- The Haunting p7：實際 scene_map 有 20 nodes、44 directed exits；structural check 失敗，entry 指向不存在的 room ID。人工局部對照另發現通過 Corbitt hiding place 地下室實牆的 unsupported exit；這次沒有先前 run 的上層臥室互通錯誤。Map quality gate 失敗。
- Lightless Beacon p16：9 nodes、15 directed exits；structural check 失敗，樓梯 exit 指向未建立的 Lamp Room node。Hallway W/E 方位相符，但 side-elevation 的 Service Room／Lamp Room／Lantern Gallery nodes 缺漏，未認證完整 topology。Map quality gate 失敗。

自動 blocked disposition 由 4 降至 1，來自插圖與 map routes；因 map 有已知缺陷，**不能宣稱已驗證 production quality 改善**。Map 驗證與修正仍獨立於 OCR；沒有改 scene_map／gameplay semantics。

## Linux CPU smoke 與 release gates

[Linux JSON](pdf_ocr_linux_cpu_smoke_results.json)、[實際 GitHub run](https://github.com/marcoliu99/line-coc-keeper/actions/runs/36852457588)。Explicit setup 安裝 pinned dependencies 與 persistent cached models；真正 Linux x86_64 CPU inference 在 runtime socket connects denied 下執行。Synthetic `1d6+2` 被辨識為 `ld6+2`，exact mechanics check 失敗，smoke job 保留紅燈；沒有換 model、confidence publish 或放寬 gate 掩蓋結果。

| Gate | 結果 |
| --- | --- |
| Fair A/B／safe-page non-regression | 已執行；無 regression；candidate 品質改善 |
| Real image-only authoritative transcription | 已執行；衝突拒絕；真實 positive acceptance 尚未驗證 |
| Real provider floor-plan graph | 已執行；兩張圖 structural／manual quality 均失敗 |
| Linux CPU smoke | Runtime 已執行；exact mechanics assertion 失敗 |
| Production rollout | 暫緩 |

## 檢查與審查

最終 local pytest：**2007 passed、2 skipped**。ruff check .（指定 0.16.8）、mypy app、python -m compileall app tests、git diff --check 全通過。已確認最新 main_v2 為 ancestor、無 unmerged paths。[一般 CI 已在整合後 runtime 6bb9a79 通過](https://github.com/marcoliu99/line-coc-keeper/actions/runs/36852457581)；獨立 Linux recognition smoke 在同一整合 commit 失敗，結果如上。

雙軸 code review 以 5725738 為固定點。Standards：0 documented breaches，2 個非阻擋 heuristic 建議（verification/report ownership、authority predicates 重複）。Spec：native-header scan 判斷、MarkItDown wrapper budget bypass、native dice modifier 刪除，以及插入否定詞／反轉正負號，均用 red/green loader regression 修正。Quality identity v9 使舊認證失效；完整 native tokens 必須維持連續 span，native signed mechanics 不得改變，span 外仍可補入獨立佐證的影像文字。最終重新審查沒有剩餘重大 spec 問題。Private candidates、canonical exclusion、budget exhaustion、identity/resume、numeric/dice/prose conflicts、Tesseract disagreement、OCR-independent maps 均有測試。

原始 PDF／crops／全文轉錄／graphs 僅存 private /private/tmp 驗證 artifacts。Committed reports 僅保存 hashes、metrics、disposition、有限 diagnostic checks；Linux report 的 source/candidate 全文只有自製 synthetic fixture。沒有發布 production scenario，也沒有改 gameplay module。
