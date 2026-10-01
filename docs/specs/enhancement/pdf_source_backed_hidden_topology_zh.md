# 具 canonical source 證據的隱藏地圖連線

狀態：提案，待規格確認後實作。PR #155，enhancement/pdf-multicolumn-ingestion。

## 目標與權威

維持既有 two-phase visible extraction。Vision 僅能提出有圖片通行證據的 door、open_passage、stairs、one_way。Canonical scenario source 才能授權 hidden_passage、secret_door、conditional_route。實牆否定 vision-only traversal，不能否定已證實的 source-backed hidden route。本輪不呼叫 provider。

## 資料模型

既有 visual room exits 維持原樣；merged map 新增內部 source_topology 集合，不放入普通 exits。每條有向連線保存既存 from/to room IDs、closed route type、authority=scenario_source、visibility=hidden、availability=undiscovered 或 conditional，以及明確條件（若有）。證據保存實體 source page、精確 span offsets、span SHA-256、整份 canonical source SHA-256。公開 parse_quality 不保存原文 quote。Hidden types 不加入 Phase 2 schema。

## Source extraction 與 deterministic merge

專用 import-only 模組在 source integrity gates 後接收 final selected、source-safe canonical pages。只保守辨識明確連線敘述與 validated inventory 的精確端點名稱；以文件與 fixtures 定義接受的 connection/secret-door 語句。模糊敘述、端點不明只保存 private diagnostics，不猜房間或路線。Rejected OCR、unverified AI prose、vision description、runtime narration 不作為輸入。Candidate 綁定來源 span；merge 重播 canonical source 證據並檢查類型、端點、重複連線。Extraction 失敗只屬 derived warning，不變更 source publication severity。

## Audit 與 certificate

Image audit 與 targeted visual repair 只操作 visual graph，不能移除 source route。Merged certificate 綁定既有 visual certificate/graph hash、hidden topology hash、完整 canonical source identity/hash、merged graph hash。驗證重播 deterministic source extraction，不只相信保存的 hash。Visual-only certificate 保持既有 contract；hidden certificate 必須取得精確 canonical source，來源缺失或改變時停用 Map Engine。

Publication 與 library read 使用完整 scenario.txt，而非 chapter window。Draft resume 不信任舊 book-wide source overlay：只 reuse certified visual evidence，待全部 final canonical pages 選定後重建並重新簽證 overlay。Invalid overlay quarantine，source 仍可遊玩。Private provenance 保存 spans/proof；public report 僅保存 hashes/status/errors/counts。

## Runtime 邊界

Hidden routes 保存在內部 source_topology，不放入 rooms[].exits。普通 movement、visible exits、/coc where、公開 Keeper map projection 僅使用 visual exits；對被注入 ordinary exits 的 hidden/source-authority metadata 加入防禦性拒絕或過濾。不新增 discovery transition；undiscovered/conditional routes 不可用於普通移動。未來明確 discovery state transition 另行定義。既有 visual movement semantics 不變。

## 測試與驗證

測 production builder/certificate/publication/cache/runtime 邊界：visual wall reject；有 source 證據的 hidden route 即使實牆仍接受；證據缺失或變造 reject；保留無關 visual graph；公開 exits/prompts 與普通 movement 不暴露 hidden route；source change 使 certificate 失效；visual certificate 正常；Beacon visible stairs 不變。補 source input exclusion、模糊端點、條件、span tampering、draft 重建、library read、report sanitation。執行 pytest、ruff check .、mypy app、compileall app tests、git diff --check。未重新 real run 不宣稱真實地圖 verified。

## 限制

保守 deterministic extraction 不保證辨識所有散文形式的秘密路線；不支援的路線不建立，可偵測時留下 diagnostics。端點必須已存在 validated inventory，本輪不建立 invisible room。Canonical source safety、OCR、Docling、scenario readiness、discovery mechanics 不變。
