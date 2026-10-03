# PR155 opening／map 驗證

基準 `40f95d6a4de9c5745f72424bcdea6dc711593a11`；已驗證 code `3eb7706246c50f2fcc3900016c038f50f3944e1a`。

## RED → GREEN 與 local checks

Codex opening 原本因 analyze_text 不接受 timeout／max_retries 而沒有 dispatch，TypeError 被 durable cache 保存；透過真正 adapter 的 regression 已先 RED 再 GREEN。舊版本 failure 不壓住新版；current completed cache replay 不再 dispatch。Deadline 為 owner／caller 較短值，非零 retries 明確拒絕，四個 provider 都接受 bounded keyword、default retry 0。

Map 重現：quarantined map page 的正文恢復時，new failed analysis 取代舊 map provenance，save_scenario 拋 Source upgrade conflicts with existing certified map authority。未發生正文 upgrade 的 missing／failed candidate 原本已保留 map，屬正向 controls。修正獨立選 map evidence，對 final source replay；visual map 可保留，source-bound certificate 不相容就只 quarantine map，不重發 certificate。Verified graph 衝突保留有效舊 artifact。10 map tests 通過 persisted reload、原始 provenance／image／PDF、相容 live room／page／facing、只清除不相容 map position、transaction failure rollback。

完整 pytest **2412 passed、1 skipped、152 subtests passed**，47.87 秒（JUnit 2565 cases、0 errors／failures）。Ruff／mypy（142 files）／compileall／diff-check PASS。Standards／Spec 各 0 findings。既有 source-boundary tests／rules 未修改。

## 真實驗證

最終 synthetic isolated Codex router `/coc start`：**1 opening reservation／1 analysis CLI transport**、TypeError 0、cache completed、helper found=true 實際用於開場、fallback 無需使用；game_started 與 state persistence PASS。CLI alias 使用原有 OAuth／backend／model 且實際接受。CLI 內部 HTTP 並非本 harness 直接量測的 packet count；已明確設定 request／stream retries 0。[官方設定文件](https://developers.openai.com/codex/config-reference)支持 retry settings；本機 CLI 0.159.3 禁止 reserved built-in provider override，所以使用 analysis-only config alias，普通 runtime 不變。

本輪共有三個 Codex probe：preliminary contract success、reserved-ID config failure（保留失敗、消耗 reservation、normal fallback）、final bounded-alias helper success。共 3 opening reservations／3 analysis CLI starts，另 1 ordinary fallback CLI start。不宣稱 preliminary probe 在設定 override 前已零 internal retries。

已發布 Haunting／Dead Boarder／Camp Sunny 複製到 private isolated storage，reload／activation PASS。Bounded OpenAI opening 共 **3 reservations／3 實測 HTTP transports**，皆 completed、合法 found=false；current-version replay 新 analysis dispatch 0。接著正常 bounded RAG fallback 真實 `/coc start` 三本全 PASS，runtime HTTP transports 分別 3／3／4；gpt-6-luna、僅 api.openai.com/v1/responses、SDK／host retries 0、未送整份 source／PDF。HTTP instrumentation 曾有重複 http_client keyword 的 harness failure，已排除於成功 starts。PDF／OCR／classification／map／topology import calls 0；沒有重跑六本 import，也未加 cap／refund。

外部 500-round report 是 **historical / superseded robustness evidence**：舊 a0c4f7e 的 7/500，execution PENDING，未重啟、不宣稱完成；不是目前 implementation failure，也不是 merge gate。其 legacy uncertified map 觀察不等於目前 certified map 被降級；本輪不改既有 read-boundary quarantine policy。

## 結論與範圍

Targeted A/B 完成，無新增已證明 P0/P1。既有 F5 recall P2 不在本輪。Beacon p13／Scritch p24／Alone p3 ordering gates 不變。Targeted patch **MERGE READY**；Production rollout **HOLD**，保留上述 gates 與 broader validation。

Production files：`app/providers/codex_provider.py`, `app/providers/codex_transport.py`, `app/providers/openai_provider.py`, `app/providers/anthropic_provider.py`, `app/providers/gemini_provider.py`, `app/source_analysis.py`, `app/scenario_intro.py`, `app/scenario_library.py`, `app/scenario_activation.py`。
