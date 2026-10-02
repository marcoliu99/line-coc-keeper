# PDF 圖片頁 criticality validation

目前結果（2026-10-03）：Haunting READY_WITH_WARNINGS、零 hard blocks；publication/reload/activation/start 成功。普通回合 incomplete，其餘五本未執行；MERGE／ROLLOUT HOLD。下列較早 checkpoint 均為歷史紀錄，最新結果見文末 exact-counterpart section 及 results JSON current_validation。


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

## Playable-first 真實驗證 — 2026-10-02

Baseline `e0873782a36ac140783e3715805d6807271f412a`；implementation `4f701e07b143ab1ad9f64ebe976b8db00f9c72b1`。六本 PDF hash 仍一致。本次已認列六本 bounded OpenAI 授權，official `api.openai.com/v1`／`gpt-6-luna` dispatch 成功；舊 authorization／harness-limited checkpoint 不代表本次結果，沒有改 provider 或繞 transport。

Production classifier 只處理 Haunting 原21頁 image blockers，每次一張實體頁圖與最多三段安全 source excerpts（12k chars）。結果：9 cover/decorative、2 map-derived、1 source-backed optional pregen、5 source-critical **candidate**、4 unknown。五個 candidate 並不證明是唯一必要 source；mixed 角色表／reference 內容仍有歧義，不能因作者 pregen 書籤就丟掉 mixed／unknown instructions。Repo 不存版權全文、圖片、raw response 或引用原句。

乾淨隔離 storage 走真實 `handle_pdf_upload`，再一次 durable Continue。Image hard pages **21 → 9**：**35、36、37、38、39、40、42、43、44**。Ordering blockers **0**、mechanics blockers **0**。移除12個已確認 false image blocks，保留 genuine unknown。Soft pages **4、24、34、41**；map p24/p34 為 `MAP_NOT_ANALYZED`，不進 gameplay，也未產生 source hard blocker。Optional pregen／topology assistance 另記 feature warning。

帳本 **36 reservations / 36 SDK transports**：classification **23/24**、shared layout/image **8/8**、獨立 region repair **1**、semantic **4**。Stages：classification23、ordering2、MarkItDown4、page transcription2、region repair1、semantic4。**35 HTTP200**，一筆 request 沒有 HTTP response，reservation 仍消耗。Observer 未保存該筆精確 exception type，因此不猜成 APITimeoutError。沒有 SDK automatic retry、refund、提高 cap 或 framework bypass。

兩張 map 因 safe context 變化曾重複分類；failing regression 證明 cache key 缺陷。Final code 改以同 PDF/model/policy/page/image observation replay，每次重新綁定當前 canonical optional/duplicate source；修正後21頁 replay **0 new request**。先前兩筆仍保留消耗。真實 import 執行於最後這項 cache-only 修正之前；final code 有 replay/regression 驗證，沒有再重跑 clean import。

**Publication 未成功，reload／activation／start／普通 turn 未執行。** 不宣稱真實劇本已可玩，也不虛構 runtime import-call count=0。因 Haunting canary gate 未通過，其餘五本依要求不跑；歷史 extraction 不能推論本次 admission。

Final checks：**2225 passed、1 skipped、152 subtests passed**；criticality regressions26；ruff PASS、mypy PASS（140 files）、compileall PASS、diff-check PASS。Optionality／cache 修正後 Standards／Spec review 無剩餘 actionable code finding。既有 first-upload＋invalid-map publication/start integration tests 通過，但 mock/unit 成功不取代真實 canary。

**PR155 MERGE HOLD／Production rollout HOLD。** 剩餘 acceptance blocker：在 production cap 下分清九頁 optional pregen/mixed reference 與真正唯一必要 source；尚未證明它們是真正 source-critical。本 patch 移除已確認 false block，仍未達成使用者「真實劇本可開始並玩一回合」的最終規則。Resumed merged-part cleanup 維持 deferred P2，沒有擴架構或改 gameplay semantics。

## 2026-10-03 最終附錄分流

基準：`7fabfc7c25eb49d4df307c50fb3bb62036be582d`。九頁既有 classification 重播，沒有新增圖片請求。一次 bounded canonical-source / observed-fragment audit 使用既有最後一筆 allowance（24/24）；OpenAI `gpt-6-luna`，一次 reservation、一次 HTTP transport、沒有 retry。

Canonical investigator-field instruction 明確把 Quick Reference Rules 定義為較熟悉遊戲後可參考的提醒；canonical source 允許自建 investigator。Optional reference permission 與 exact duplicate evidence 分開。Provider NO 不能單獨授權 publication；每個 fragment 都必須有完整且 source-bound 的 region 判定，不得因 reference 標題自動補齊缺漏 ID。Regression 保護混在 reference 中的劇本專用 pushed-failure 規則。

35、36、37、38、39、40、42、43 頁綁定為 OPTIONAL_PREGEN / SOFT_REVIEW。44 頁因 audit 遺漏部分 fragment IDs 仍未解；尚未證明存在唯一必要 Keeper source，也沒有宣稱完成 region transcription。Private audit 重播不消耗新請求。

本機驗證：完整 pytest exit0（2235 collected，一個既有 skip）、Ruff PASS、mypy PASS（140 files）、compileall PASS、diff-check PASS。Standards/Spec review 找到的 missing-ID auto-fill 已移除，無剩餘 actionable finding。真實 publication/start acceptance 仍待 safe production canary 完成，不推論 gameplay 或其他五本成功。

### 最終 exact-counterpart 重播及 production 結果

44 頁漏判 fragments 與已完成完整 source-bound audit 的 optional-reference fragments 正規化後逐字相同。最終 binding 只引用已接受 counterparts、exact equality 及 canonical 作者的 reminder permission，不依標題、數字集合或 dice 相同判定。Regression 拒絕相同 dice、相反效果。九頁均為 SOFT_REVIEW；沒有新增圖片 classification 或 region transcription。

全新 conversation/draft production import 成功：READY_WITH_WARNINGS、零 hard blocks、13 soft pages，unsafe map 未進 gameplay。Persisted library reload、真正 scenario-use activation 與 `/coc start` 均成功。普通玩家回合 **未通過**：API 成功回應，但既有 runtime resolution 回傳 incomplete。State 已持久化，普通回合觀察到的 import-time calls 為零。第一個 runtime probe 的 temperature 參數被 provider 拒絕；既有 `OPENAI_OMIT_TEMPERATURE=true` 設定排除該參數，仍未排除 incomplete turn。Private instrumentation 的 null-error parsing bug 已修正，相關 probe 不算 gameplay 成功。沒有修改 Keeper/runtime production code。

本輪涵蓋 interrupted、blocked 及最終 published canary：29 筆合法 import reservation／29 transports；9 筆 runtime logical dispatch，包含失敗 instrumentation/model probe。SDK hidden retries 為零；unsupported-temperature compatibility redispatch 被阻止。未提高 caps 或 refund。Classification ledger 最終24/24，新增圖片 classification transport 為零。Haunting 普通回合 acceptance 失敗，未執行其餘五本。

最終 full suite：**2236 passed、1 skipped、152 subtests，零 failure/error**（36.423s）。Ruff、mypy（140 files）、compileall、diff-check PASS，沒有新增測試失敗。Standards/Spec review 無剩餘 actionable patch findings。P1 acceptance blocker：真實普通回合 incomplete；P2 既有 staged-part cleanup deferred，configured-model capability 設定仍需部署確認。P3 cleanup deferred。**PR155 MERGE HOLD；Production rollout HOLD。**
