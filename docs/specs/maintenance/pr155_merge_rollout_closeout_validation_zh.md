# PR155 merge／rollout 收尾驗證

本輪基準：`e85aaf9cb7aac2cb9d98d7bff424007b4fc767ad`。Implementation：`3eb7706246c50f2fcc3900016c038f50f3944e1a`。本輪僅修改文件，production code 與 tests 不變。

## 分離兩個 gate

**PR155 MERGE READY。** No remaining reproducible P0/P1 findings。Merge 要求通用 contract 正確：安全 playable-first source composition、quarantine 不進 gameplay authority、核心 ordering／mechanics 嚴格保護、canonical／certified-map 單調 reparse、Codex bounded opening 實際可用、revision／live-state 保留，以及 tests／tooling 通過。不要求解完所有 corpus pages／features、六本都 publication，或完成 500-round soak。安全可遊玩的 core 可以發布，不具 authority 的 unresolved content 仍隔離。

**Production PDF rollout HOLD。** Lightless Beacon p13、Scritch Scratch p24、Alone Against the Flames p3 保持 CORE_PLAYABLE_SOURCE ordering review。本輪未解決、未降級這三個 gate；安全解決與 broader real generalization evidence 應另開 follow-up validation／PR。

舊 `a0c4f7e` 500-round 記錄是 **historical / superseded robustness evidence**。現有 7/500 progress 不是目前 certified-map implementation failure，也不是 PR155 merge gate。

## Final review 與 regression

Standards／Spec 獨立 review `40f95d6...e85aaf9` 與目前通用 authority／persistence contract。兩者皆 **No remaining reproducible P0/P1 findings**，不需要 production fix。

Regression 覆蓋完整 persistence path：published certified map → reparse candidate → final merged source certificate replay → atomic save／revision → persisted reload／load_context → correction activation。仍有效的舊 proof 獨立保留；source 不相容只停用 map；verified graph 衝突保留有效 published artifact。相容 page／room／facing 與遊戲 state 保留。Source-boundary、image reviews、corpus ordering gates 均保持凍結。

本輪離線重跑 isolated published Haunting／Dead Boarder／Camp Sunny 的真正 router scenario use／start。三本 reload／activation／start 全 PASS。三次 current completed found=false analysis cache replay，沒有新 dispatch；fallback provider boundary 使用明確標記 synthetic text。本次是離線 regression，並非新的 real provider 成功宣稱；import-time calls 為 0。Implementation 不變，上一輪三本真實 reload／activation／start 證據仍有效。

Synthetic isolated Codex `/coc start` 走真正 opening helper／bounded adapter，外部 transport 使用 fixture：1 adapter dispatch／1 synthetic transport、TypeError 0、completed record、found=true opening 實際使用。上一輪 real Codex canary 是外部證據：1 reservation／1 CLI transport、helper 使用、無 fallback；CLI 內部 HTTP packets 未直接量測。Caller／owner deadline、zero retry、cache version regression 皆通過。

新增外部 PDF／provider requests 0；hidden retries／refunds／cap increases 0。未重跑 import／OCR／classification／map／topology。Book-specific rules 新增 0。

## Verification

- 完整 pytest：**2412 passed、1 skipped、152 subtests passed**，50.38 秒，無 errors／failures。
- `ruff check .`：PASS。
- `mypy app`：PASS，142 files。
- `python -m compileall app tests`：PASS。
- `git diff --check`：PASS。
- GitHub baseline HEAD：CI checks 與 optional Linux CPU portability smoke PASS。main_v2 required-status API 回報 Branch not protected（404），沒有另設 required-check list。最終 docs HEAD 的 checks 另於 final report 確認。

未提交 raw PDF／圖片／source prose／provider response／credentials。[Sanitized results](pr155_merge_rollout_closeout_results.json)；先前真實 canary 詳見 [opening／map 驗證](../bug/pr155_opening_map_retention_validation_zh.md)。
