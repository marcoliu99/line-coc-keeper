# PR155 來源邊界回歸驗證

基準 `4288029d499c98c6847dde90a14f6b3510456377`；已驗證 production／tests code `d70a3482f356851f0a8b311bac7aaeef89e9cd15`。使用者 review 對照舊 `a0c4f7e`，本輪先檢查既有修正再處理可重現缺陷。

## 證據

使用者原始 7 tests 未改：基準 5 FAIL／2 PASS，最終 7 PASS。Repo regression 補齊否定／條件、mandatory pregen、partial coverage、空 preview 真正 upload／extraction／library reload、正文不足、必要圖片依賴、所有完整 counterpart、authoritative recovery、cached Continue、保存來源 reparse、stale publication、跨群組 revision pinning、tampering 與遊戲 state 保留。Fixtures 為 synthetic source，不提交版權原文。

完整 pytest：**2391 passed、1 skipped、152 subtests passed**，50.79 秒；JUnit 2544 cases、0 failures／errors。Ruff PASS；mypy PASS（142 files）；compileall PASS；diff-check PASS。未觀察到測試 regression。

Standards closure：0 findings。Spec closure：無已重現的剩餘 blocking failure；F5 P2 wording recall 限制保留。Review 已涵蓋最後 canonical binding／retry isolation 修正。

私人既有 Haunting／Dead Boarder／Camp Sunny evidence 重組：各本 hard pages 0、selected source 不變、新 requests 0。這是 **cache／evidence replay**，不是本輪新 real provider import／start。既有歷史 acceptance：Haunting publication／reload／start／兩回合；Dead Boarder、Camp Sunny fresh import／publication／reload／start。Beacon p13、Scritch p24、Alone p3 的核心 ordering review 仍 pending，本輪未降低此 gate。

## 邊界與結論

新增 external request 0；hidden retries／refunds／cap increases 0。未提交圖片、provider response、完整正文或 secrets。Dependency 原文保持 private；public quality 僅允許 hash／page／geometry 等 sanitized 欄位。Library revision 保持 private 且檢查 tampering。

F1/F2/F3 及可重現 F4/F6/F7/F8 邊界缺陷已修正。F5 保留 feature recall 工作；有限 positive grammar 不代表完整 source dependency 理解。Legacy published evidence 保留穩定性，不偽稱獨立驗證。

本 patch 無剩餘已證明 P0/P1，可進 PR review；Production PDF rollout **HOLD**：三處既有 corpus core ordering 與更廣 real generalization 並未被本輪 local tests 解決。不推論新增真實 corpus／gameplay 成功。
