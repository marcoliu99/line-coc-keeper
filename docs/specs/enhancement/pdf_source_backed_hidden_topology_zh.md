# 具 canonical source 證據的隱藏地圖連線

狀態：已實作；驗證與雙軸 review 記錄於 validation companion。PR #155，enhancement/pdf-multicolumn-ingestion。

## 目標與權威

維持既有 two-phase visible extraction。Vision 僅能提出有圖片通行證據的 door、open_passage、stairs、one_way。Canonical scenario source 才能授權 hidden_passage、secret_door、conditional_route。實牆否定 vision-only traversal，不能否定已證實的 source-backed hidden route。本輪不呼叫 provider。

## 資料模型

既有 visual room exits 維持原樣；merged map 新增內部 source_topology 集合，不放入普通 exits。每條有向連線保存既存 from/to room IDs、closed route type、authority=scenario_source、visibility=hidden、availability=undiscovered 或 blocked，以及明確條件（若有）。證據保存實體 source page、精確 span offsets、span SHA-256、整份 canonical source SHA-256。公開 parse_quality 不保存原文 quote。Hidden types 不加入 Phase 2 schema。

## Source extraction 與 deterministic merge

專用 import-only 模組在 source integrity gates 後接收 final selected、source-safe canonical pages。只保守辨識明確連線敘述與 validated inventory 的精確端點名稱；以文件與 fixtures 定義接受的 connection/secret-door 語句。模糊敘述、端點不明只保存 private diagnostics，不猜房間或路線。Rejected OCR、unverified AI prose、vision description、runtime narration 不作為輸入。Candidate 綁定來源 span；merge 重播 canonical source 證據並檢查類型、端點、重複連線。Extraction 失敗只屬 derived warning，不變更 source publication severity。

## Audit 與 certificate

Image audit 與 targeted visual repair 只操作 visual graph，不能移除 source route。Merged certificate 綁定既有 visual certificate/graph hash、hidden topology hash、完整 canonical source identity/hash、merged graph hash。驗證重播 deterministic source extraction，不只相信保存的 hash。Visual-only certificate 保持既有 contract；hidden certificate 必須取得精確 canonical source，來源缺失或改變時停用 Map Engine。

Publication 與 library read 使用完整 scenario.txt，而非 chapter window。Draft resume 不信任舊 book-wide source overlay：只 reuse certified visual evidence，待全部 final canonical pages 選定後重建並重新簽證 overlay。Invalid overlay quarantine，source 仍可遊玩。Private provenance 保存 spans/proof；public report 僅保存 hashes/status/errors/counts。

## Runtime 邊界

Hidden routes 保存在內部 source_topology，不放入 rooms[].exits。普通 movement、visible exits、/coc where、公開 Keeper map projection 僅使用 visual exits；對被注入 ordinary exits 的 hidden/source-authority metadata 加入防禦性拒絕或過濾。只有明確授權的 KP outcome 能啟用 source route；undiscovered/blocked routes 不可用於普通移動。既有 visual movement semantics 不變。

## 測試與驗證

測 production builder/certificate/publication/cache/runtime 邊界：visual wall reject；有 source 證據的 hidden route 即使實牆仍接受；證據缺失或變造 reject；保留無關 visual graph；公開 exits/prompts 與普通 movement 不暴露 hidden route；source change 使 certificate 失效；visual certificate 正常；Beacon visible stairs 不變。補 source input exclusion、模糊端點、條件、span tampering、draft 重建、library read、report sanitation。執行 pytest、ruff check .、mypy app、compileall app tests、git diff --check。未重新 real run 不宣稱真實地圖 verified。

## 限制

保守 deterministic extraction 不保證辨識所有散文形式的秘密路線；不支援的路線不建立，可偵測時留下 diagnostics。端點必須已存在 validated inventory，本輪不建立 invisible room。Canonical source safety、OCR、Docling、scenario readiness、discovery mechanics 不變。

## 已授權範圍擴充：conditional barriers

後續實作要求新增 source-only breakable_wall、blocked_passage、sealed_door、collapsible_barrier。預設 availability=blocked；除來源明寫 hidden 外 visibility=visible。Condition 保存由 route evidence 決定的 world_state key、expected=true，以及來源明確條件文字。不得自行增加 STR 難度、HP、armor、工具門檻。明確可破障礙預設 retryable；fail_forward 必須有明確 necessary-for-progress 敘述。失敗不得永久封路。

Static topology/certificate 維持不可變；另於 GroupState 持久化 route outcomes，綁 route identity 與 timeline。既有 action/check/damage workflow 後，由明確授權 KP transition 在 state lock 下保存 discovered/opened/failed、actor、可選 consequence；以 /coc route 提供窄範圍確認，拒絕玩家操作。Narration 不能改狀態。Failed 只增加 attempts，不改 availability，仍可 retry。Opened barrier 或 discovered hidden route 才 available。既有 room-name movement 也須遵守 source route availability；direction movement 只有來源明寫 compass 才使用，不猜方位。公開 exits 隱藏未發現連線，明確 transition 後可顯示 available route。Visible blocked route 回 generic actionable interaction，不創造 mechanics。Source certificate extension 新增 condition metadata hash，visual certificate 不變。


## 已實作的 source grammar 與 runtime confirmation

目前 deterministic extractor 僅接受英文 affirmative whole sentence：`A/The/There is a [two-way] [hidden] <route kind> connects/links <精確 inventory label> to <精確 inventory label>`，或 `leads/runs from ... to ...`。可選 `If/When <condition>,` 保存明確來源條件。精確 `to the east`（或支援的方位、上下）suffix 才提供 compass；未提供則留空。`; it is necessary for progress` 才授權 fail_forward。Two-way 必須明寫，不猜反向通行。跨樓層重複 label 無法授權端點。已辨識但端點不明的敘述使 map incomplete，不封鎖 source；其他語言或散文形式不靠猜測解讀。

`/coc route <page> <route-id> opened|discovered|failed [已裁定的 consequence]` 只允許目前 KP；持久化前重新驗證 published map/source certificate。Route IDs 在 private map artifact 中，不新增公開 hidden-route 列表。此確認接在正常 action resolution 後；不會因任意 skill roll 或 narration 自動開通障礙。Runtime outcomes 另存 GroupState，不修改 immutable certificate。Timeline 或 graph identity 改變時，舊 availability receipt 停用。


## Availability 後續修正：多重 source barriers

同一組端點與方位的所有 matching source barrier conditions 都約束該連線。已 available 的 source route 不得繞過另一個仍 blocked 的障礙。Activated source-route projection、named movement、directional movement 共用同一 gate；不同方位且未 blocked 的 existing visual traversal 維持可用。新增 sealed_door 加 blocked_passage regression：未開通與只開通任一障礙時仍 blocked，全部開通後才可通行。Source extraction、certificate、publication severity 不變。

Route compass 不明時，不能藉此宣稱不同替代路徑；matching blocked endpoint barriers 仍需有 available outcomes 才可通行。
