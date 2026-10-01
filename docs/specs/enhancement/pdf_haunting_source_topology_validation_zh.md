# The Haunting 正典來源 topology 驗證

狀態：來源 evidence 驗證完成；真實 grammar coverage 未解決。本輪尚未改 runtime。

## 來源權威與範圍
使用既有已發布 scenario.txt，不使用 rejected OCR、provider output、地圖解讀或 narration。UTF-8 SHA-256 與 manifest content_hash 相符：`191b3b2b14530050ded4a55c2c58a5e672dadd4ea4d7d064f200a6f7181e7541`。此為 legacy publication，缺 current PR155 ingestion provenance/quality proof；scene_maps.json 為空。不能稱為本輪新驗證來源或 certified map。以下 evidence 位於 Image OCR annotation 外；排除 p7 map-vision prose。沒有 provider requests，也未 reimport。

## 先讀來源，再看 extractor
人工讀取 final canonical spans，支持：地下室／Room 1 storage → 破壞或移除外側木板 → 可探索的兩牆間空間（正典 Room 3 section）→ 破內側牆 → Room 4。兩道不同障礙，中間空間在第二次破牆之前可探索。Room 3 已有正典 section 名稱，這裡不應發明 Secret Room 或 unnamed source_transit。此為 evidence review，不是已成立 gameplay event。

這些障礙未找到明確 necessary-for-progress 指示，不授權 fail_forward，也不推測 difficulty／HP／armor／tool／one-shot failure。Physical pages 10–12 有明顯多欄 prose 交錯；須保留原始 offsets，不默默改寫來源。

## Evidence（Unicode source offsets；end 不含）

| Physical page | Sanitized fact | Span | SHA-256 |
|---|---|---|---|
| 10 | basement_room_1_storage_section | 30798–31017 | `367bbd7be86a9975f5c548c98a50837e5f49b5575b842de251029c63d2a7cf01` |
| 11 | canonical_named_intermediate_room_3 | 36458–36487 | `1f09cea4ab36543b51e91243f2eb42cf7effab82d55bd98a597a3d9ebcd3feaf` |
| 11 | outer_boards_removed_or_broken_reveal_space_between_two_walls | 36627–36800 | `b2f8d23bbe8421d145b9b721e472f3d1ec26ba395829b92073962c5d6718b8ca` |
| 11 | intermediate_space_can_be_explored | 37113–37132 | `0575a0e728b7b26f4c8d86898d2744f0f2491657f94840ca559423846a7a30bd` |
| 12 | inner_wall_of_same_crawl_space | 40298–40343 | `8f5da35ee109039e64ef95f8880edb88acc64132ec15715117e64a9bf77379f2` |
| 12 | breaking_inner_wall_enters_room_4 | 40693–40832 | `084b32faaaa1606a5de5ea6ed9698f2824f6706606286911c21597ca93dd46a7` |

## Production extractor probe
對完整已發布 canonical source 執行 app.pdf_source_topology.extract。沒有實際 published certified inventory，故 empty-inventory probe 只供診斷。另以來源明載的 Room 1／3／4 references 建立 diagnostic inventory，亦得到零 routes／chains／transits、零 diagnostics。該測試 ID／inventory 絕不當作 published visual graph 或 authoritative endpoint mapping。

現有 grammar 未命中：它接受單一肯定 route assertion；真實 evidence 是跨 section／page 的 conditional narrative，且有 prose 交錯。這是 grammar coverage gap，不是 source 不支持 ordered route。Chains／segments／barriers／transit 均 0；無 route id、endpoint、kind、hidden flag、policy、extracted span。沒有 flatten edge，因為根本沒有產生 source edge。

不能宣稱已驗證真實 A-open／B-blocked、discovery 或 solid-wall/map interaction：沒有真實 extracted chain，也沒有 certified graph。另行重跑既有 synthetic ordered-route／hidden-topology 測試，不能計入真實 evidence。Production 保持 fail closed。

## 擬議 narrow grammar 擴充 — 待確認
改 runtime 前，僅擴充 source-bound deterministic narrative recognition：正典 numbered sections 綁定起點／中間／終點；明確外側木板移除揭露同一可進入 wall space；明確 inner-wall reference 與後續 break-through clause 綁定第二段到具名終點。跨頁保留 original full-source span／hash。每個角色與順序須有 textual proof；指涉衝突或不安全的 prose interleaving 仍判 ambiguous、不授權 topology。不能只靠 hash allowlist 當語意證據。

每個具名正典位置仍須唯一綁定既有 graph inventory。Room identity 缺失只能 diagnostic，不捏造 endpoint 或 unnamed node。Certificate replay 與逐 barrier state 保持。不改 OCR／layout／Docling／publication／image provider。

新增不含原文的 real-derived structural regressions 與 private hash-bound real-source execution。Negative controls 包含互不相關相鄰牆句、shared wall、單一障礙、ambiguous transit、否定、錯誤 inner-wall reference。成功擷取的候選須測逐 barrier runtime／discovery；synthetic visual fixture 與 real map authority 分開。

## 驗證與 review
待執行／紀錄；machine-readable companion 僅保存 source evidence、extractor output，不存原文。

## 信任邊界更新

Legacy scenario.txt 的人工結構閱讀僅為 candidate discovery，不具 certified topology authority。上方 narrow grammar 擴充提案已由 pdf_semantic_topology_discovery_zh.md 取代，未曾實作。PR155 canonical reconstruction 未重新通過 source-quality contract 前，候選一律 AWAITING_CANONICAL_SOURCE。Grammar miss 不是 source unsupported。
