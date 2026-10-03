# PR155 opening analysis 與 map retention

基準：`40f95d6a4de9c5745f72424bcdea6dc711593a11`。

A：Codex analyze_text 接受 bounded caller 的 timeout／max_retries=0，deadline 取 caller／request-owner 較短值，不新增 retry。Source-analysis implementation version 防止旧 contract failure cache 阻擋修正版本；舊消耗紀錄保留，成功 current-version cache 可 replay。Sanitized diagnostics 區分 absent／failed／contract error／cache replay。

B：Reparse 獨立保留仍有效的 published map 與 private supporting provenance，對 final merged source replay；不相容 map 只 disable 並記 artifact conflict，不偽造 certificate、不阻擋劇本。不同 verified candidate 保留相容的 published authority。既有 atomic publication／game state preservation 保持。

採 user 指定公開 seam：scenario_intro→Codex transport、source_analysis durable replay、scenario_library save→load_context／correction。先 red 再最小 green，最後 full suite 與 Standards／Spec review。

不改 PDF admission／source boundary／OCR／map extraction／certificate／topology discovery／gameplay rules／image review／corpus ordering。Real isolated Codex start 使用 synthetic source；既有 corpus regression 不重跑 import/provider。Private raw evidence 不提交。

## 已實作證據

Codex 接受 keyword bounds；僅 analysis 透過同一 OpenAI OAuth config alias 設定 CLI request／stream retries 0（CLI 0.159.3 保留 built-in provider ID）。Runtime transport 預設不變。Analysis cache identity 包含 implementation version 2、provider／model、source／window、schema 與 prompt。Failed attempt 維持已消耗，source／opening observability 只保存 safe status／type。

Map provenance 獨立選擇，對 final merged source replay，連同原始 image／PDF identity 保存。不重發不相容 certificate：只停用 map／其位置，source／game 維持可用。Conflicting verified candidate 保留仍有效的 published map；reparse 弱候選不能取代它，first publication certificate gate 仍嚴格。

详見[驗證](pr155_opening_map_retention_validation_zh.md)與 sanitized results。本輪不改 admission／source-boundary decision／corpus ordering gates。
