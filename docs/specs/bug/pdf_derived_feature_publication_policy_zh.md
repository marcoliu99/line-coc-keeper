# PDF source readiness 與 derived-feature quarantine

## 問題與範圍

目前 map graph missing／invalid／incomplete 會封鎖安全劇本來源的 publication。本輪分開 approved-source readiness 與 derived feature readiness，不修改 OCR acceptance、Docling、map generation／validation／repair／certificate 或 gameplay movement semantics。

## Contract

Page 保留 source `disposition`（accepted／legacy_route／needs_review），新增 `publication_severity`（HARD_BLOCK／SOFT_REVIEW／NONE）。只有 source failure 造成 needs_review／HARD_BLOCK。Map failure 以原 map status 存 `derived_feature_warnings`，保留 private provenance 與原圖，排除 gameplay map；source safe 時為 SOFT_REVIEW。兩者同時存在則 HARD_BLOCK 優先。

Source-blocking 包含 unresolved source numeric fields／mechanics、無安全 fallback 的 ordering、必要 image-only source 無 independently authoritative transcription、缺乏 playable source。Graph certificate 不是 source transcription。已明確驗證的無文字 illustration 不需要 OCR，diagnostic OCR noise 不撤銷分類；圖片內容不明時仍 source-blocking，不猜成 decorative。

Scenario report 新增 `scenario_readiness`（READY／READY_WITH_WARNINGS／BLOCKED）、`hard_block_pages`、`soft_review_pages`、各頁 `map_status`。`blocked_pages == hard_block_pages`；review_pages 可含非阻擋 diagnostics。Library 拒絕 hard source failure，保留 graph certificate guard；unsafe graph 永不進 scene_maps.json 或 state.scene_maps。公開 publication report 只含 sanitized status／warnings；candidate graph／repair provenance 保持 private。

Draft safe pages（accepted／legacy_route／soft_review compatibility）在 source、selected-text hash、extraction identity 相符時，可不帶 graph resume；帶 gameplay graph 仍需 image-bound certificate。Continue 不重跑 soft map failure，只重試 source hard block。更新 pipeline identity，保留 OCR／map certificate version。

首次成功上傳直接 activation canonical library source 與原圖，清楚告知未驗證頁 Map Engine 停用。延後選擇也保留此警告。Soft-only failure 不要求 continue loop。

## 驗證

以 public extraction／publication／draft／activation seams 測 safe source + invalid／incomplete／provider-failed map、image-only source failure、soft reuse 無 provider redispatch、首次 30 safe pages + 1 invalid map。確認 library／runtime 無 unsafe graph，canonical source 可用；mechanics conflict 仍 block，illustration 不 block。執行 pytest、Ruff、mypy、compileall、diff check。此 publication policy 不需真實 provider 結果才能驗證；先前 graph-correctness evidence 仍 pending。
