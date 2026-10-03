# PR155 opening analysis 與 map retention

基準：`40f95d6a4de9c5745f72424bcdea6dc711593a11`。

A：Codex analyze_text 接受 bounded caller 的 timeout／max_retries=0，deadline 取 caller／request-owner 較短值，不新增 retry。Source-analysis implementation version 防止旧 contract failure cache 阻擋修正版本；舊消耗紀錄保留，成功 current-version cache 可 replay。Sanitized diagnostics 區分 absent／failed／contract error／cache replay。

B：Reparse 獨立保留仍有效的 published map 與 private supporting provenance，對 final merged source replay；不相容 map 只 disable 並記 artifact conflict，不偽造 certificate、不阻擋劇本。不同 verified candidate 保留相容的 published authority。既有 atomic publication／game state preservation 保持。

採 user 指定公開 seam：scenario_intro→Codex transport、source_analysis durable replay、scenario_library save→load_context／correction。先 red 再最小 green，最後 full suite 與 Standards／Spec review。

不改 PDF admission／source boundary／OCR／map extraction／certificate／topology discovery／gameplay rules／image review／corpus ordering。Real isolated Codex start 使用 synthetic source；既有 corpus regression 不重跑 import/provider。Private raw evidence 不提交。
