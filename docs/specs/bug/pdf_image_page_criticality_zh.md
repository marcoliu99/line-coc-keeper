# PDF 圖片頁 criticality 與 admission

## 狀態

提案，尚未實作。Historical provider-OFF baseline branch enhancement/pdf-multicolumn-ingestion，SHA 5b87207a9a47ad2f0473ecda631504c833669a33；最新待測 provider-ON baseline 37e0bb80eb34d059c487a307c7a863c7890b8522（merge 17aa388）；main_v2=6024adfffad39860de2142e87dbf33e056f4960a。先完成目前 production provider-ON upload baseline 和每頁 audit，才依真實失敗做 regression／最小修正。外部執行遭自動授權審查拒絕，沒有傳送資料；文件不代表 baseline 已完成或新 admission 已實作。

## 問題與目標

目前 pdf_loader 在 requires_image_transcription、缺 authoritative transcription、未 verified_illustration 時，直接加入 source_image_transcription_unverified。前置條件来自低 native text 或 raster gap，缺一般 page-criticality 模型。這是潛在 false HARD_BLOCK 根因，不能因此假定所有 flagged 頁都是非 source。image_only_pages 只計 native-absent，帶短 caption 的 raster 頁不在這個 metric，所以 0 不代表沒有圖片 source。

先判斷圖片是否有 Keeper 執行劇本必需且 canonical 未保留的唯一資訊。mechanics corruption、不安全 ordering、缺真正 unique source、 genuinely unknown criticality 仍 HARD_BLOCK；map graph failure 保持 derived soft warning。

## 範圍與資料模型

只動 page-role evidence、criticality、admission、bounded classification、draft identity 與 publication guards。保持 OCR acceptance、MarkItDown 順序、Docling、map phases/certificate、source topology、hidden route、多障礙 progression、movement；不改 cap，不以 confidence 發布。Repository 只存 hashes/page numbers/classification/reason codes/counts/安全結構事實，不存圖片、全文、response、prompt 或 key。

Closed roles：SOURCE_CRITICAL、PURE_ILLUSTRATION、COVER_DECORATIVE、MAP_DERIVED、OPTIONAL_HANDOUT、OPTIONAL_PREGEN、EMPTY_NON_SOURCE、DUPLICATE_SOURCE、UNKNOWN_NEEDS_REVIEW。criticality=true/false/unknown。Provider 可回 role、source-bearing regions、mechanics/clue/ambiguity，但不能單獨授權 publication。

Deterministic code 結合 native amount/placement、raster union、graphics、可信 classification 與 canonical counterpart。Mixed page 不可丟掉小塊 unique source。角色卡外觀不能證明可選，handout/pregen optionality 需 scenario evidence；duplicate 要有 exact bound canonical counterpart，不能相信 LLM 的重複聲稱。native empty 不等於整頁空白，必須檢查實際 raster/graphics。

## 預計流程

Native/geometry -> page criticality -> 必要時 bounded classification -> 只讀必要 source-bearing region -> 現有 independent mechanics/source checks -> admission。已證非 source 接受；可選/derived asset failure soft review、quarantine 不安全 parsed feature。true critical 或 genuinely unknown 且缺 authoritative source 才 hard block。Ordering/mechanics 不被 classification 清除。

保持 blocked_pages == hard_block_pages。只有 PDF/selected-text/extraction identity 一致的 safe accepted/legacy/soft 頁可 resume；unknown/hard 不可當 accepted cache，uncertified graph 不可進 gameplay。實作改 pipeline identity。Library publication 重驗 source gates，不降低 map certificate。Provider/budget 不可用不能否定已有 deterministic non-source evidence；真正 unresolved unknown 仍 block。

## 真實驗證與 budget

六本 SHA 必須對齊原 245 頁 baseline；從 handle_pdf_upload -> durable draft/continue -> save/reload/activation 進入，只有 READY 劇本才 /coc start。檢查設定 caps 為每本 8 requests / 4 pages，不 reset/increase；max_retries=0、stage-specific timeout、dispatch 前 reserve。1 reservation 最多 1 transport。Harness 不得額外送所有 audit 頁，也不得偷偷替 unreserved/default-retry path 補 budget。耗盡記 BUDGET_LIMITED。

每個原 hard page 按 reason/criticality/expected/actual disposition 比較；未執行保留 null/PENDING，不能以 mock 算 true/false。各書列 image、ordering、mechanics、unknown blockers 與最少剩餘 blocker；semantic topology 不當 admission classifier。可發布書必須 reload/activate/start，無證書 maps 不得使用。

## Regression 與 checks

確認真實 failure 後補 illustration/cover/empty/decorative/map/optional handout/pregen/duplicate 不整本 block；unique image、mixed mechanics、unknown、ordering、mechanics 仍 hard。測 provider unavailable 和 budget exhaustion 對 known non-source/unknown/critical 的差異；native-present low-text graphical 且 image_only_pages=0 的案例；durable accounting、safe resume、publication/start、candidate quarantine。比較 request usage但不省略 safety。跑 pytest、ruff check .、mypy app、compileall、diff-check、Standards/Spec review。

## 尚未解決

Provider-ON baseline 因六本外傳授權審查拒絕而 PENDING；true/false/unknown provider classification 和 after-fix admission 均未取得。Region AI repair 缺 explicit timeout/max_retries=0 和跨 Continue 保留的獨立 durable AI request ledger/reservation。既有 heading inverse order、margin/header/footer 是 P1，未修不可宣稱 MERGE READY；merged-resume staged-part cleanup 與 AI budget resumability 為 P2。此文件不授權重做其他架構。

## 已明確確認的最小 P1 修正（2026-10-02）

使用者本輪明確確認五個 P1，並 supersede 舊兩張圖片限制，授權六本 bounded payload 至 official OpenAI。Canary前先拒 inverse spanning heading、違反 geometry 的 page furniture；max_retries=0 不走 compatibility retry。Region repair保留獨立durable AI allowance/crop-request identity，dispatch前持久化、Continue保留、不refund。Index/pregen/opening只送 bounded window，以private one-shot reservation控制；optional metadata失敗不得新增canonical hard blocker。正常成功的card unit仍保留backstory，不改OCR/map/topology/gameplay架構；ordering validation加入cache identity。

五項regression與full checks先通過，再單跑Haunting handle_pdf_upload -> draft/continue -> save/reload/activation/start/一turn；canary通過才擴其他五本。Provider/budget/真正unknown source據實保留block，只有real evidence確認false admission才最小修。Mechanics/ordering controls不放寬，獨立觀測reservation與actual transport。

## 已核准 playable-first 實作（2026-10-02）

使用者已明確核准於 baseline e087378 實作。測試使用公開 extraction/upload/library/start/router seams。Page criticality 在 page OCR 前執行，不改 numeric/dice/order acceptance 或 map/topology runtime。Source-critical 是正確遊玩必須保留的唯一資訊；真正未知仍保留既有 image-source block。Cover/decorative/illustration 必須確認沒有 gameplay-bearing content；optional asset 必須引用安全 source 的 exact citation；duplicate 必須 deterministic 對應 canonical source。Classification 不等於 transcription。Raw observations/citations 存 mode-600 private cache，report 僅保存 role/flags/reason codes/hashes。

Classification 採獨立有限 allowance：每 PDF/model/policy 預設／上限24個實體頁 request，涵蓋本輪明確要求的21頁；dispatch 前持久化，跨 Continue／clean import 共用。既有 layout/map/image-transcription 與 region-AI cap 不變。每 request 一張 page image、設定 timeout、max_retries=0、不退款／retry。Cache replay 必須重新 deterministic 綁定目前安全 source。Provider/budget failure 保留 unknown，除非 deterministic evidence 能證明非 source。非 source 跳過 OCR/transcription；map 維持 dedicated pipeline；optional 原始資產保留 private 並附 warning。

先用 production classification service 與真實 page identity 只分類 Haunting 既有21頁，再 clean production upload/publication/reload/use/start/普通回合；canary 通過才逐本擴展。不得放寬真正 mechanics/order/source-image gate；不新增 scenario/publication state、retry layer、provider、OCR engine、map/topology ontology 或 gameplay rules。
