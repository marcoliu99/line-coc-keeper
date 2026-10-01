# Map connectivity reliability 驗證

基準 `7f3e109`，implementation `24342f6`。本輪只改 map evidence boundary、timeout routing 與相關 tests；real Haunting／Beacon request 為 0，未使用第 5 次。

1. **Files changed**：.env.example、app/pdf_map_analysis.py、app/pdf_map_evidence.py；tests/test_pdf_map_evidence.py、新 test_pdf_map_request_policy.py、test_pdf_provider_failures.py、test_pdf_two_phase_map.py；雙語 spec／validation、catalog、sanitized JSON。
2. **Traversal enum**：沿用 type／visual_basis tool enum：door/open_passage/stairs/one_way。Builder 只接受 canonical，拒絕 passage／open passage／描述句 alias，不猜 traversal。Gameplay semantics 不變。
3. **Compass enum**：沿用 N/NNE/NE/ENE/E/ESE/SE/SSE/S/SSW/SW/WSW/W/WNW/NW/NNW/U/D。Compass Literal 擁有固定集合。Map evidence builder 拒絕 east／e／roughly east；gameplay direction parsing 未修改。
4. **Timeout**：沿用四個 settings，phase1_audit 修正為 inventory timeout。Generation／location audit 30 秒；connectivity、repair、final audit 各 60 秒；generic image timeout 不改。Fake clock 模擬 Phase1 12／15 秒、Phase2 35 秒、final audit 50 秒成功，不 sleep；custom routing test 通過。
5. **Diagnostics**：沿用 ContextVar capture、OpenAI dict 或 None contract。只保存 provider／stage／error_type／status_code／timeout／elapsed。補 APIConnectionError、invalid tool arguments；原有 timeout／401／429／missing tool／JSON／privacy tests 全通過。Failure record 無 raw body／prompt／image／key。
6. **APITimeoutError**：actual OpenAI SDK MockTransport 讓 Phase1 兩階段成功，再於 connectivity 注入 ReadTimeout。SDK 轉 APITimeoutError，dispatcher 保留 phase2_generation、null status、timeout=true。該 stage 只有 1 transport／reservation，沒有 retry／repair／final audit。40.245 秒為無 sleep 模擬，不是新 real evidence。
7. **描述句 basis**：visible doorway／open passage、free-text compass 拒絕；canonical door／E 及四種合法 traversal 接受。Provider boundary 檢查 connectivity／patch tool enums。
8. **Targeted repair**：synthetic provider 的 2 missing locations＋3 scoped invalid edges，一次 patch 加 2 visible locations、替換 2 edges、刪 1 wall edge，unrelated edge 完整保留。4 rooms／3 directed exits verified、certificate replay 通過。越界修改 unrelated edge 整份 atomic reject、不進 final audit。
9. **Entry unresolved**：不虛構入口。只有 entry 不明時不 repair／final audit；混合 repair 僅修其他錯誤，仍 incomplete。Source-safe extraction → library publication → activation 為 READY_WITH_WARNINGS，blocked_pages 空、scene_maps 空。
10. **Durable accounting**：timeout／429／connection actual SDK transport 三例，各 pipeline 3 reservations／3 transports（generation、inventory audit、connectivity 各一次），失敗 Phase2 只有一筆、不 refund。max_retries=0。Synthetic happy path 4 requests、repaired path 5 requests。
11. **pytest**：2122 passed、2 skipped、10 warnings、152 subtests passed，27.93 秒。
12. **ruff**：PASS。
13. **mypy**：PASS，135 files；既有 untyped-body note 僅資訊。
14. **compileall**：PASS。
15. **diff-check**：PASS；最終 publication 前核對 main_v2 alignment。Standards review 0 findings；Spec review 0 actionable findings。
16. **Remaining blockers**：真實 Phase2 reliability、Corbitt wall-edge、Beacon stairs／Hallway／elevation topology、entry evidence、final audit 尚 pending。歷史 inventory／結果保留；synthetic regression 不等於 real certificate。Rollout HOLD。
17. **下一次 real validation**：code／schema／timeout／diagnostics 已可測；目前 request 未使用。下一輪須重新授權及配置新 durable budget。OCR／Docling／source-publication policy／gameplay production code 未改。
