# 有來源依據的有序路線分段

狀態：提案。PR #155；enhancement/pdf-multicolumn-ingestion。

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
