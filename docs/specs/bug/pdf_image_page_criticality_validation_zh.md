# PDF 圖片頁 criticality validation

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
