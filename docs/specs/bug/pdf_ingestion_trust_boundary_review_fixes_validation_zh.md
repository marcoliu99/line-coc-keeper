# PR155 信任邊界修正驗證

Review 基準 `9d331bb`；implementation `fca16f9`、`dcad373`，branch `enhancement/pdf-multicolumn-ingestion`。本輪沒有 real Haunting／Beacon 呼叫。只保存 synthetic mechanics、status 與 counts。

1. **F1**：修復後與 cache reuse 的 final selected source 都檢查 integrity。dice coefficient／side／modifier（含 DB）、signed decimal、百分比未解決損毀會 HARD_BLOCK。十一種損毀、clean dice／DB／decimal 加 rejected layout challenger、production region repair、一般 prose replacement glyph regression 通過。舊 challenger failure 不阻擋 clean source。Pipeline 更新為 multicolumn-v8、quality ai-import-repair-v10，舊不安全 cache 失效。
2. **F2**：裁切 bbox 做 x sweep、合併 y intervals 算聯集，避免 overlap 重算。以 50% coverage、現有 200 字門檻和 native 正文位置判定 source gap。已知 native pairs 不證明未轉錄 raster。Tiled scan 加 header 會跑 OCR／獨立 transcription；重疊測得 43.75%，裁切部分 overlap 72.5%。小裝飾或完整 native 正文的大背景不觸發 image-only block。Local candidate accepted 不會跳過 independent verification。
3. **F3**：library read 核對 archived PDF／manifest／provenance hashes、page image 與 current replay certificate。invalid、structurally valid 但 uncertified、proof 缺失／tampered 都停用 Map Engine，source 仍載入。測試經 load_context → install_context_fields；current valid certificate 仍安裝。舊 scenario 不強制 reparse，也不因此 hard block。
4. **F4**：connectivity／patch type、basis closed enum：door/open_passage/stairs/one_way；compass 使用 builder canonical 18 方向。Provider boundary 與 deterministic builder regression 確認 valid door 接受、描述句 basis／free-text compass 拒絕。Validator 未放寬。
5. **F5**：ContextVar capture 分隔每個 dispatch／thread。OpenAI／Anthropic／Gemini 保持 dict 或 None，保留 sanitized error type、HTTP status（含 Gemini code）、timeout、elapsed；map attempt 可見 provider／stage。Timeout、401、429、invalid JSON、missing forced tool 與 privacy assertions 通過。Failure metadata 不保存 exception string／raw body／prompt／image／key。
6. **F6**：OpenAI／Anthropic max_retries=0，Gemini attempts=1，皆使用 configured PDF image timeout。三個 provider 的實際 SDK MockTransport，429／timeout 共六例，各 1 durable reservation、1 transport request。0.25 秒確實到 transport，Gemini client 250ms；沒有 refund。Anthropic 測試使用 SDK 實際 httpx／httpx2，而非只 mock create()。
7. **Stage timeouts**：inventory 30 秒，connectivity／repair／audit 60 秒；stage override 確實傳入 provider。零 retry、dispatch 前 durable shared reserve 維持；generic image timeout 未拉長。
8. **Files**：production 包含 config/env、pdf_loader、pdf_quality、新 pdf_raster_source、scenario_library、pdf_map_analysis/evidence、markitdown_shim、三個 provider adapter、新 providers/image_diagnostics。Tests 包含 source_integrity、library_map_boundary、provider_failures、markitdown_request_budget、image_transcription、two_phase_map、scenario_library；附雙語 spec／validation、catalog、sanitized JSON。
9. **pytest**：2112 passed、2 skipped、10 warnings、152 subtests passed，28.52 秒。完整 suite 涵蓋 numeric／dice／percentage preservation、image-only conflict、pure illustration、soft publication、first upload/start、quarantine、draft/resume、certificate replay、budget exhaustion。
10. **ruff check .**：PASS。
11. **mypy app**：PASS，135 source files；既有 untyped-body note 僅資訊。
12. **compileall app tests**：PASS。
13. **diff check**：PASS；main_v2 為 ancestor、無 conflict。
14. **再次 review**：Standards 0 規範違反、1 optional geometry 重複邏輯 cleanup。Spec 曾重現 F1 DB／decimal suffix 漏洞，dcad373 修復後再 review 為 0 actionable finding。本輪 F1–F6 範圍無剩餘 P0／P1／P2；P3 延後。
15. **Merge readiness**：本輪 code blockers 已解決、checks 通過。Production rollout 仍 HOLD：real graph verification 與 image-only authoritative positive 尚 pending。歷史 Paddle A/B、macOS／Linux 結果沒有重跑或宣稱是新 evidence。
16. **下一次 real validation**：code／schema／diagnostics／timeout 已可測，但需下一輪重新授權和配置 durable budget。本輪 Haunting p7／Beacon p16 呼叫為 0，未使用剩餘第 5 次；mock 不能代表 real verified。
