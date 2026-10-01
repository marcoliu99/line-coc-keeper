# 有來源依據的有序路線分段

狀態：已實作；獨立 review 待完成。PR #155；enhancement/pdf-multicolumn-ingestion。

## 目標與範圍
保存劇本來源的逐段障礙；一道障礙對應一次狀態轉移。不改 visual Phase 1/2、OCR、Docling、來源發布政策、正常可見移動或既有秘密路線權威規則。

## Schema
既有單障礙 source_topology 有向邊保留。新增 source_route_chains，保存 route id、有序 segments（id/from/to/barrier_id/route_kind）、獨立 barrier metadata 與正典來源證據。每段投影一條獨立 condition key、帶 route/barrier identity 的 source_topology 邊；禁止首尾捷徑。保留來源明載的單障礙雙向語意。

source_transit_nodes 與 visual inventory 分離：穩定 source_transit_<hash> id、kind=source_transit、player_label=""、來源證據，不發明故事名稱。僅在來源明確肯定障礙後有可進入空間時建立。具名中間位置須唯一對應正典 inventory。歧義或不支持的 chain 以私人診斷 fail closed，不阻止劇本發布。vision、narration 不具建立權威。

## 擷取契約
擴充既有保守英文 assertion grammar，接受明確指定起點、第一障礙、可進入中間空間、第二障礙與終點的有序 chain。保存 physical page、Unicode span、SHA-256。不由相鄰牆面或不相關 assertion 推導 chain。未識別文字不授權 topology。以 synthetic canonical-source fixture 說明 grammar，不宣稱驗證真實 Corbitt PDF。

## Runtime
逐 barrier 保存 receipt，綁定 timeline、map、graph/certificate、route、barrier、source identity。開 A 只改 A；B 失敗保留 A opened 且 B 可重試。progress 僅推導連續 opened prefix。操作深層障礙前檢查目前位置及先前段可通行狀態；保持權限與 fresh-state mutation 邊界。

擴充 /coc route <page> <route-id> <barrier-id> opened|discovered|failed [後果]，保留單障礙舊語法。hidden discovery 與 opening 分離；第一層 discovery 不揭露後續障礙或 transit，後層需實際可達與獨立、來源支持的 discovery。移動與公開投影只使用個別開通分段，不公開整條秘密 chain。不推測 difficulty、工具、HP、armor、threshold 或一次性限制；敘事與一般擲骰不轉移狀態。

## Certificate 與 visual 分離
綁定 ordered segments、barrier metadata、transit nodes、source evidence、ordering、merged graph hash；以正典來源 deterministic replay 驗證 transit 證據。重用 visual proof 時移除全部 source overlay。Image audit 仍只檢查可見 inventory/connections；solid wall 可與來源支持的破牆路線並存。順序、障礙數、source span、transit evidence 改變使 certificate 與舊 receipt 失效。

## 測試與驗證
在 extraction/certificate、runtime persistence、movement/projection、command seam 逐段 TDD。Corbitt 型雙牆 synthetic canonical fixture 測 A/B 獨立、A 開後 B 失敗、完成、不可達 B、transit 證據與空名稱、逐層 hidden visibility、重排/source identity 失效、單障礙、secret door、Beacon 可見 topology。執行 pytest、ruff check .、mypy app、python -m compileall app tests、git diff --check，再獨立 Standards/Spec review；明載結果與 grammar 限制。

## 已實作 grammar 與操作細節

支持的肯定 chain 形式：
`A [hidden] route runs from <起點 exact label> through <barrier kind> <來源 barrier label> into <中間 exact label | an unnamed enterable space>, then through <barrier kind> <來源 barrier label> to <終點 exact label>[; it is necessary for progress].`
更多中間階段可重複 `, then through ... into ...`。種類限 breakable wall、blocked passage、sealed door、collapsible barrier。中間具名位置須已能唯一對應正典 inventory。不推導反向 chain 或 compass。來源明載 necessary-for-progress 才選 fail_forward；其餘障礙保持可重試，不加入推測檢定。

hidden chain 的每個 barrier 預設 hidden。KP discovery 僅確認目前層，不開通障礙；每層須獨立 discovery 才能 opened。確認時須有追蹤中的隊員在 segment 起點，且之前分段全部 opened。attempts 計 opened/failed，不計 discovery。failed 保留之前 opened/discovered 狀態，只在該 barrier 保存已裁定後果。

`/coc where` 僅顯示目前層已發現的 blocked 障礙提示，以及逐一開通 segment 的操作指令。`/coc traverse <segment-id>` 讓發出指令的玩家通過一段目前開通的路段；鎖內重新載入 state，並重驗已發布 source/map identity。這提供未命名 transit 的移動方式，不捏造名稱或方向。不能選深層 segment、跳過 chain、改 facing 或改 barrier state。既有 visible exits 與單障礙指令保留。

Source topology certificate 由 source-topology-v1 升為 source-topology-v2；visual certificate/pipeline version 不改。新 receipt 除既有 actor/timeline/map/graph/edge/condition audit 欄位，保存 route_id、barrier_id、source_sha256、state、discovered。重用獨立 visual draft proof 時移除三個 source overlay collection。

Corbitt 型雙牆與 Beacon stairs 測試是 synthetic regression fixtures；本輪不宣稱可解析未支持的自然語言，或已重驗真實來源 PDF。

## 驗證

完整 pytest：2163 passed、2 skipped、152 subtests passed。Ruff check . 通過；mypy app 通過（137 檔）；compileall app/tests、git diff --check 通過。新增 segment 回歸檔 16 passed；既有 hidden-topology 回歸 25 passed。獨立 Standards/Spec review 待完成。

## Review 修正

初次 Standards review 無 documented-standard 違反；共用 module-local guard 處理重複 published identity 檢查建議。初次 Spec review 發現 chain 外房間可跳入深層位置，以及 segment/edge endpoint 同時損壞可能拋 TypeError；兩者均補回歸並修正。獨立、有效的 visible/source exits 保持可用；未授權跳躍拒絕。最終 review 複查待完成。
