# PDF 圖片頁 criticality validation

最新結果：五項已核准 P1 已於 `1bcb12b` 修正；真實 Haunting canary 為 **HARNESS_LIMITED**，尚未證明可遊玩。新六本授權已接受，8 次 OpenAI 實際 request 均成功；下列舊 PENDING／零 request 敘述為歷史 checkpoint。Merge／rollout HOLD。

Baseline branch enhancement/pdf-multicolumn-ingestion @ 5b87207a9a47ad2f0473ecda631504c833669a33，六本 SHA 6/6 與前次一致，245 頁。原 hard counts 23/9/26/7/25/15，共105頁；86頁 image、19頁 ordering、1頁 mechanics reason（1頁多重原因）。

## Provider-ON：PENDING

自動授權審查在程序啟動前拒絕六本外部執行，0 requests，沒有圖片/正文外傳。理由是既有 trusted authorization 只限两張 map PNG，認為本輪六本 payload/destination 未足夠明確。沒有換 transport、間接執行、mock、擴 cap 或 reset ledger。Private harness 已準備 handle_pdf_upload -> 真實 draft/continue -> publication，guard official OpenAI、SDK retries=0 和 durable reserve；region AI repair 目前不符合這個 policy，不偷補設定/ledger。

Results 的 provider classification/current disposition/true-false counts/start 保留 null/PENDING。Provider-OFF totals 不能冒充 provider-ON baseline。未改 runtime；full baseline suite PASS：2147 passed、2 skipped（2149 collected），Ruff PASS、mypy 137 files PASS、compileall PASS、diff-check PASS。Green tests 不能解除已重現 review findings。

## 本機 audit

105個 prior hard-page records 覆蓋全部86 image targets，含 physical page、image gate理由、native amount、raster union、graphics、map candidate、provider null、unique-source/criticality/transcription unknown、expected conservative unknown blocker、current pending。未有頁面同時 native/images/drawings 全空，不能把 native empty 宣稱 EMPTY_NON_SOURCE。86 UNKNOWN 是 evidence 未取得，不是86個已證 source-critical。

Dead Boarder 7頁：p20 native142/raster0.625668/map；p21/23/25/27/29/31 native41/raster1.0/character sheets。Camp Sunny：p16/18/20/22/24/26 native39/raster1.0/character sheets；p28 native78/raster0.668203/map-like，但 current map_candidate=false。本機視讀 Dead p20–29 與 Camp p16–28；Dead p31尚未視讀確認。角色卡是否 optional、map labels是否 canonical duplicate 仍 unknown，不按外觀直接接受。

image_only_pages 只計 native-absent，所以 image_only=0 可同時有短native的 raster gaps。pdf_loader 現行 requires_image_transcription + no authoritative + not verified illustration 直接加入 source_image_transcription_unverified，沒有一般 criticality gate；這是精確潛在 root cause，沒有宣稱 provider-confirmed false block。

Haunting special pages、Beacon p28–42、Scritch p23 mechanics/p24 ordering、Alone ordering 都保留原reason。Ordering/mechanics沒有降級，maps仍quarantine。去除 false blocks 後每書最少 TRUE blocker，需provider baseline後才能判定，不能拿舊total替代。

Raw PDF/images/source/harness outputs private outside repo。Repository只存hashes/page facts/status/reasons/counts。每頁紀錄見 results JSON。Merge/rollout HOLD；既有 ordering P1與merged-draft cleanup P2未宣稱解決。

## 保留 review findings

已重現兩個 P1：上方 body 可被排到下方 spanning heading 後；margin/header/footer 沒有幾何順序約束，可接受 footer -> body -> header。Resumed merge 在 Continue 成功後仍留 staged parts（P2）；region AI allowance 在 Continue 重置、未 durable reserve（P2）。應持久化原本獨立 AI allowance，不要求重設 architecture 或偷偷改扣 layout ledger。本次沒有修 runtime。文件 Standards review 0 findings。

## 最新 branch 整合

Remote PR155 已前進至 37e0bb80eb34d059c487a307c7a863c7890b8522，merge 為 17aa388，catalog 雙方entries保留。5b87207a9a47ad2f0473ecda631504c833669a33與2147-pass屬歷史 provider-OFF/code evidence，最新candidate baseline為37e0bb80eb34d059c487a307c7a863c7890b8522；merge後checks另記results。最新code已包含semantic discovery，不沿用歷史「沒有semantic fallback」結論。Static preflight仍見semantic analyze_text(max_retries=0)有internal compatibility retry、region repair缺bounded durable dispatch、index/pregen/start有full-source/default-retry路徑。這些network-contract P1與AI allowance persistence P2分開。整合前後都沒有實際provider-ON requests。

17aa388 merge後本輪實測：pytest 2181 passed / 2 skipped；Ruff PASS；mypy 138 source files PASS；compileall PASS；diff-check PASS。後續只改sanitized文件。

## 最小 P1 修正與真實 canary — 2026-10-02

Baseline `fb3e8b746157e59c678476f549efecafc01bea33`；implementation `1bcb12b700cfbacfbd8576f4521e0635319a6655`。六本 SHA256 全數一致。只修 inverse spanning-heading、margin/header/footer geometry、semantic zero-retry 直達 SDK、獨立 durable region-AI allowance/crop dedup，以及 index/pregen/opening bounded source window。Extraction identity 增加 ordering validation version 2。未重做 OCR/map/topology/movement/publication；optional metadata 寫檔或解析失敗可降級，直接 pregen parser 仍保留既有嚴格 contract。

真 SDK＋HTTP MockTransport regression 包含 success、429、timeout、connection、unsupported parameter，核對 timeout、reserve-before-transport、不退款、失敗 crop 不重發、無隱藏重試、bounded payload。Geometry regression 保留 valid heading/two-column fixtures，拒絕已確認錯序。

真實入口 `app.legacy_commands.handle_pdf_upload`，使用隔離私有 state/library/draft。使用者 explicit approval 是本輪授權依據，成功取代舊兩張地圖限制；execution 接受該 scope，HTTP 200 只證明 endpoint 回應，不是授權來源。記錄 `AUTHORIZATION_CONFIRMED`；唯一 destination `https://api.openai.com/v1`，model `gpt-6-luna`。沒有 whole PDF／whole scenario text／額外 audit provider request。原 production caps 不變：layout 8 requests/4 pages、region AI 8、semantic 4。

實際 8 requests 全為 HTTP 200：ordering p16/p28 共2、MarkItDown p1 共1、region p4 共1、semantic bounded windows 共4。Layout reservation/transport=8/3；獨立 region=1/1；semantic=4/4；總數13/8，沒有 SDK hidden retry。5 筆 MarkItDown reservation 未到 transport，因私有觀測 guard 錯誤禁止同 SDK client 在新 reservation 下合法重用。這是驗證 harness 缺陷，不能當成產品問題。Guard 已改用每筆 fresh reservation identity，但**沒有重跑**；沒有退款、提高 cap、重設 ledger 或另建 draft 恢復 budget。原 guard 與完整 evidence 留在 private。

Haunting import 294.094秒後回傳 false。Hard pages 23→21；p16/p28 真實 provider ordering 已接受，ordering/mechanics blocker 均0。剩餘21頁皆 image transcription unverified：p1/2/5/17/24/34–49；soft review p4。p24/p34 map NOT_ANALYZED，gameplay maps=0。Semantic 有1 candidate、0 certified，等待 canonical source。Production page-role classification 未執行，加上 harness 干擾，21頁全部維持 UNKNOWN_NEEDS_REVIEW；true/false criticality 為 null，不能以封面／pregen 外觀假造通過。未修改 admission gate。

| 劇本 | 先前 hard pages | 本次 hard pages | Publication | Activation/start/turn | 可遊玩性 | 主要限制 |
|---|---:|---:|---|---|---|---|
| Haunting | 23 | 21 | 否 | 未執行 | 未證明；harness limited | 21頁 image 未驗證，criticality 未知 |
| Dead Boarder | 9 | Pending | 未執行 | 未執行 | Pending | Haunting canary 未通過 |
| Lightless Beacon | 26 | Pending | 未執行 | 未執行 | Pending | 同上 |
| Camp Sunny | 7 | Pending | 未執行 | 未執行 | Pending | 同上 |
| Scritch Scratch | 25 | Pending | 未執行 | 未執行 | Pending | 同上 |
| Alone Against the Flames | 15 | Pending | 未執行 | 未執行 | Pending | 同上 |

未宣稱 publication/reload/activation/start/普通回合成功。Shared image/layout requests 已耗盡，因此未 dispatch Continue。依使用者 canary gate，未開始其他五本；舊 safety control 未變。每本最小「真正必要」blocker 在 criticality evidence 不足時仍未知。

Verification：**2199 passed、1 skipped、152 subtests passed，39.17秒**；Ruff PASS；mypy PASS（139 files）；compileall PASS；diff-check PASS。Standards／Spec review：本次最小 patch 無剩餘 actionable finding；沒有新增本機 suite regression。P2 resumed merge staged-part cleanup 延後；region-AI Continue reset 已修。P3 cleanup 延後。本 patch 未發現新增 P0/P1，但至少一個真實劇本可遊玩的 acceptance gate 尚未達成。**PR155 MERGE HOLD；production rollout HOLD**。

完整 sanitized request receipts、changed files 與 per-page unknown audit 在 `pdf_image_page_criticality_results.json:minimal_p1_followup`。Raw images/provider responses/candidate graphs/full source 全部維持 private，不入 repository。
