# 保守的 Paddle 三欄原生文字閱讀順序

狀態：使用者已明確核准本設計與 public seams，實作完成。

## Baseline 與證據

分支 `enhancement/paddle-three-column-reading-order` 從最新 `origin/main_v2` 的 `0e87e0670b2ec454609cc5ec63af943c0f736f01` 建立，已包含 PR158。使用隔離 worktree，原工作區未修改。

現有 `tests/fixtures/paddle_layout/page_05.json` 保存原生行、PP-DocLayoutV3 區域、模型橫向 order 與獨立 expected text。Baseline `order_native_lines` 回傳 `fallback/not_two_columns`，不是完整左／中／右預期順序。這頁是 synthetic，不是真實 production 三欄 PDF；輸入與 oracle 不改。

## 目標與界線

只採用明確分離的三欄頁。Paddle 提供類型與座標，三欄不使用模型 order；原生文字依完整左欄、中欄、右欄排列。標題在前，頁尾在後，每行保留一次。

PR158 雙欄選擇、順序與保護維持，包括跨欄 native line 拒絕、repair-required fallback。單欄不變。不加工具／套件／模型、整頁 OCR、OCR／SAN／dice normalization 修改、Python migration／CI／mypy 版本修改、新 flag／setup、importer refactor、scenario library、reparse、map/topology、start/RAG/combat/gameplay/provider 或性能優化。

## 介面與最小改動

沿用 `NativeLine`、`LayoutRegion`、`LayoutResult`、`order_native_lines`、`reorder_with_paddle`，只有一套 layout framework。typed reason 增加 `three_columns`；拒絕碼仍有限、不含原文。不改 persistence/schema、回傳 tuple。

共用原生／區域檢查、配對、完整性保護。現有模型 order 檢查延後至雙欄分支，條件與結果不變。在正文區域分組後增加小段三欄分支；只有避免重複檢查才抽 private geometry helper，不另建 pipeline。

三欄分類不受正文 order 缺少／重複／橫向排列影響；建立區域時這條路徑的 order metadata 可選，雙欄仍維持現有要求。不改 inference/model lifecycle。

```
PDF 原生行 + PP-DocLayoutV3 區域
  -> 現有有效性與安全完整配對
  -> 兩組正文：原 PR158 順序與保護
  -> 三組正文：嚴格 geometry gate，原生行按欄/y/x 排列
  -> 其他／不確定：原 fallback
```

## 保守三欄採用條件

1. bbox 有限、頁內、面積正值；類型已知、native ID 唯一、文字非空。保留嚴重區域重疊檢查。image/chart/seal/header_image/footer_image 不定義文字欄、不提供文字。
2. 沿用 coverage 配對；漏配、模糊、原生行碰到水平分離文字區域均拒絕。跨欄 native line 不拆、不改；跨欄頁首標題必須配到正文上方的獨立標題區域。
3. 已配對 `text` 區域依實際 x、水平區間重疊分組，必須恰好三組分離區域，不用頁寬三等分。標題、頁首尾、註腳、頁碼不定義正文欄數。
4. 沿用 PR158 尺度：兩個 gutter 至少頁寬 2%，每欄區域包絡寬至少頁寬 15%，允許不等寬與不同 margin。三欄各至少三個正文原生行，垂直範圍至少兩個正文原生行高的中位數。門檻源自既有雙欄與重複垂直內容，不寫死 fixture 座標。
5. 正文須為一致垂直帶：各欄 top/bottom 差距不超過「兩個中位行高」與「最大欄高 10%」較大者，保守拒絕半高 sidebar 與欄數中途改變。沿用欄內原生水平斷開 veto；不靠它建立或排序額外欄。
6. 只有正文上方明確的 doc_title/paragraph_title/header 當前置。正文內小標須完整落在單欄；中間跨欄文字／標題、上二下三、unsupported sidebar/table、模糊配對均 fallback。footer/number/footnote 須明確在正文下方。不修中途跨欄內容。
7. 僅三欄：每欄收集所有配對行，依原生 y、x、stable ID 排列；前置 geometry 順序 → 完整左 → 完整中 → 完整右 → 頁尾 geometry 順序。所有三欄順序決定均忽略 Paddle order，不逐 region 拼出橫向交錯。
8. 採用前 native ID multiset 相同且每 ID 一次；原文字行完全不變、token multiset 相同。測試另驗完整 expected output 與數字／百分比／骰子／SAN 表達式。任一檢查失敗就 fallback。

門檻刻意保守，可能拒絕可讀頁。校準須維持 negative cases、不等寬與原雙欄輸出，不為 page_05 放寬；若證據需要實質設計變更，先回報。

## 接點、setup、logging

沿用 PDF_PADDLE_LAYOUT_ENABLED、PP-DocLayoutV3 CPU、scripts/setup_paddle_layout.py。本機 cache 限定；缺模型／套件、init/inference error、需要修補頁維持原流程。Python 3.14 fallback 不變；實測可用既有 Python 3.11/3.13，不改 repository 版本。

沿用 metadata-only log 與初始化／每頁 inference 時間，三欄成功 reason=three_columns。不 log PDF 原文或 raw error。不優化。

## Regression 與 public seams

在 order_native_lines、真正 pdf_loader.extract_text（僅 stub 外部模型）、限定頁數的 public reorder_with_paddle smoke 做垂直 TDD。CI 不測 private function、不下載模型、不呼叫外部 API。

- 首先 page_05 不變輸入對獨立 expected text/IDs：baseline red、修改後 green；改掉／刪掉正文 order 不得影響三欄輸出。
- 普通三欄、標題三欄、圖片三欄、不等寬三欄都驗完整左／中／右順序。
- Negative：雙欄＋短窄側標、低高度第三組、中途跨欄正文／標題、上二下三、跨左中／中右／三欄 native line、漏配／重複／模糊行或區域、嚴重重疊、四組。
- Header 在前、footer/page number 在後；圖片不算第四欄。
- 1d6、1d10、1d4+2、50%、+20、-10、SAN 1/1d6 原樣保留，亦驗所在欄的順序。
- 全部 PR158 fixture 與故障案例：普通／標題／複雜雙欄與 baseline 逐字相同；單欄不變；誤併短文字／錯開行高仍 fallback；缺模型／套件／init/inference／漏配／repair-required fallback 不變。
- 只更新 page_05 必須被拒絕的過時 assertion，保留真正模糊三欄 negative cases。

執行 pytest、ruff check .、mypy app、python -m compileall app tests、git diff --check；記錄命令結果、SHA、時間。CPU smoke 重用原 subset/model、封鎖 runtime 網路。若現有 corpus 有明確真三欄頁，最多 2–3 頁；不跑整本／重新匯入。找不到就明講真三欄 evidence 為零。

## 交付

設計核准後才實作／測試／commit／push；對齊最新 main_v2，開非 draft PR，觸發 Codex review（自動 review 不重複）。Finding：reproduction → test → minimal fix → 全部 checks → push → re-review；不擴 scope。

最終回報使用者 37 項：baseline/final/branch、分類與順序、page_05 前後、正負 fixtures、雙／單欄不變、文字／數字／dice／SAN、無新工具／模型／OCR／gameplay、全部 checks、真實 evidence 數／時間、scope、MERGE READY/HOLD。MERGE READY 須 CI PASS 且無未解決 correctness finding。

審閱決定：使用者已明確核准共用檢查／三欄 geometry 分支與 public test seams。

## 實作證據

未修改的 page_05 regression 實作前確定 RED（fallback/not_two_columns），實作後 GREEN 且逐字符合原 expected text。測試涵蓋普通／標題／圖片／不等寬 synthetic 與指定不安全版面，以及缺少／重複／無效模型 order 不影響三欄。真正 extract_text integration 只 stub 外部模型。原雙欄 expected text 逐字相同；單欄及故障 fallback 不變。

Production 只有 app/pdf_layout.py 行為改動；loader/OCR/setup/Python/gameplay 未修改。本機 CPU smoke 重用五頁 subset 與本機模型，封鎖 socket。真實三欄 evidence 為 0 頁：成功三欄來自既有 synthetic fixture，不宣稱 corpus production 已驗證。[結果與時間](paddle_three_column_reading_order_results.json)。
