# PDF publication policy 驗證

本輪已在 PR155 `enhancement/pdf-multicolumn-ingestion` 實作 publication policy。Source-safe 劇本遇到 invalid／incomplete／missing／failed map 時可以 publish，附 warnings，unsafe Map Engine feature 保持停用。Source failure 仍阻擋；本輪 policy 驗證不需要上傳真實 PDF 圖片至 provider。

1. **Files changed**：production 修改 `app/pdf_loader.py`、`app/pdf_ingestion_drafts.py`、`app/scenario_library.py`、`app/legacy_commands.py`；新增 `tests/test_pdf_publication_policy.py`；更新 map／source regressions、雙語 specs 與 catalog。Activation／movement code 未修改。
2. **Old/new disposition**：原先 unverified map 直接納入 failed_graphic／needs_review；現在只有 source failure 決定 source disposition，map status 另存 derived_feature_warnings 與 publication_severity。安全 source 保持 accepted／legacy_route，即使 map failure。Pipeline identity 為 multicolumn-v7；OCR／map certificate version 不變。
3. **HARD_BLOCK**：不安全 ordering、必要 image-only source 無 authoritative verification、source mechanics unresolved、source transcription unverified、整份缺乏 playable source。Verified graph 不能證明 authoritative transcription。同頁 source defect 與 map warning 並存時 HARD_BLOCK 優先；blocked_pages 等於 hard_block_pages。
4. **SOFT_REVIEW**：source-safe map failure（provider／audit／budget unavailable、entry missing、graph invalid／incomplete、repair unsuccessful），以及其他非阻擋 diagnostics。Report 保存 READY／READY_WITH_WARNINGS／BLOCKED、soft_review_pages、hard_block_pages、map_status。明確驗證的無文字 illustration 仍不需 authoritative OCR；圖片內容不明仍保守 source-blocking。
5. **Draft/resume**：accepted／legacy_route／soft_review compatibility pages 需 source／hash／extraction identity 相符且無 hard source reason。缺少 optional graph 不再強迫 redispatch；unsafe candidate 不作 reusable map 回傳。附帶 gameplay graph 仍檢查 certificate。成功 publication 後 private provenance 存 `.ingestion-provenance.json`（0600）；sanitized parse_quality.json 保留 warning／status／error／hash／timing metadata，不放 candidate graph 或 provider response。
6. **Map publication regressions**：invalid／incomplete／missing／provider-failed／audit-failed map 都能以 READY_WITH_WARNINGS 發布安全 source，library 無該 graph。Map budget exhausted 不阻擋；certificate tampering rejection 仍測試。Soft page resume 不再呼叫 provider；soft_review alias 也通過。
7. **Hard-source regressions**：image-only character sheet numeric disagreement 仍 unverified／BLOCKED、不發布 library；同頁 unresolved STR source 與 invalid map 因 source reasons HARD_BLOCK；整份空白 source 為明確 hard block。原 numeric／dice／percentage acceptance 與 conflict tests 保留。
8. **首次 upload/start**：真實 synthetic PDF 的 30 safe narrative pages + 1 invalid floor-plan graph，經實際 handle_pdf_upload、SQLite／library persistence、activation，再以 investigator 執行既有 /coc start 成功。scenario_library_id／source text 可用，scene_maps.json／state.scene_maps 都無 unsafe graph。成功訊息清楚告知 Map Engine 停用、劇本可開始，不要求 Continue loop；延後選擇也保留警告資料。
9. **Checks**：全套 pytest 2051 passed、2 skipped（9 個既有 dependency deprecation warnings）；Ruff pass；mypy 132 source files pass；python -m compileall app tests pass；git diff --check pass。已依 fetch 後 main_v2 確認 integration branch alignment。
10. **Remaining blockers**：本輪 publication-policy 修改無 blocker。先前 Haunting／Beacon 真實 graph correctness 與 real image-only authoritative-positive evidence 仍 pending；沒有虛構結果，unverified map 保持停用。依新 policy，pending derived-map 結果不阻擋安全劇本來源發布。

## Standards

找到一項 documented breach 並修正：source-blocking reason 封閉集合改為 SourceBlockingReason Literal，PagePublication field 也有型別。另保留兩項 heuristic maintainability 建議：sanitization 知道 map evidence schema、source-safety guards 分布於多個 trust boundary。兩者不是 correctness failure；各 boundary 仍獨立檢查 hash／certificate。

## Spec

無 material findings。Review 確認 source-only hard blocks、quarantined maps、safe soft-page reuse、private provenance、首次／延後 warning，以及實際 first-upload/start regression。未修改 OCR acceptance、graph generation／validation／repair／certificate、movement semantics。

Review counts：Standards 一項 documented breach 已修、兩項 heuristic observation；Spec 零項 material findings。
