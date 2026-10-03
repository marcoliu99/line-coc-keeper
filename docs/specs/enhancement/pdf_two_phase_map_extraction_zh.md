# 兩階段 image-grounded map evidence

以 visible-location inventory、inventory-only completeness audit、connectivity evidence、deterministic graph build、最多一次 error-scoped patch、final image audit 取代自由 graph generation。既有共享 durable budget 優先（每次 analysis 最多五個 dispatch；不需 patch 為四個）。Map-only failure 不產生 scenario hard block。

Inventory 覆蓋所有 floor、side elevation、inset、location legend。檢查 visible 非空 exact label 與 image evidence；同 section 重複 label 正規化，保留 original labels，區分 furniture／nonlocation，code 產生穩定唯一 IDs。Inventory audit 只回 missing locations／uncertainties，deterministic merge 後才抽 connectivity，不重建 inventory 或檢查 topology。

Connectivity 使用原圖與 canonical inventory，只回 entry evidence、directed edges（from／to、type、compass、visual_basis、evidence）及新發現 missing locations。Unknown room／target 是 error，不暗中接受。只有 door／open passage／stairs／明確 one-way evidence 可形成 edge；wall／adjacency／proximity／shared wall 不可。Code 建 canonical scene_map，再使用既有 structural validator。Entry unresolved 為 incomplete quarantined map，不虛構入口，也不單獨 hard block scenario。

單次 targeted repair 只回 add_locations／remove_edges／add_edges／replace_edges／entry_update，限 reported errors。Added location 必須明確重新檢查原圖 evidence。不准 room removal 或 full graph output；保留 unrelated evidence，修改 verified evidence 需新 image evidence 與 reason。Patch atomic／deterministic apply；拒絕 out-of-scope change、unknown room／target、malformed edge、unsupported basis。Final image audit 核對所有 rooms／edges／entry／completeness，保留 inventory locations 與先前 supported evidence。Invalid／incomplete 永不進 gameplay maps。

Private provenance 保存原始 phase responses、inventory、connectivity、patch、errors、provider／graph／image hashes／timing；公開 sanitized phase／patch metrics 與狀態。更新 map extraction identity，舊 certificate／cache 失效。OCR、Docling、hidden OCR、numeric／dice gates、publication policy 不變。

Tests 覆蓋 inventory normalization／floors／elevation／furniture／audit merge、traversal basis、patch scope／unknown-room rejection／verified evidence preservation、entry unresolved、final certificate／publication／quarantine、first upload/start。執行 pytest／Ruff／mypy／compileall／diff check。若第三方圖片上傳授權仍未取得，真實 Haunting7／Beacon16 rerun 保持 PENDING，mock 不視為 real verification。
