# 普通回合 completion diagnostics

基準 `f2004d6`。範圍僅限 OpenAI runtime response、tool loop、Executor resolution；不改 PDF/admission/source safety 或 gameplay 規則。

真實 trace 顯示：舊 Haunting state 收到 provider completed response，內容是可解析的 Executor JSON，actor 正確，但 disposition 明確為 incomplete；runtime 以 model_incomplete 保留模型裁決。尚未證明 parser 丟字或 status mapping 錯誤。同一 published library 的乾淨 state，第一回合可完成 no_mechanics/validated。Temperature omission 是既有設定前提，不是語意 root cause。

最小修改只增加安全的 response shape、loop termination 與 Executor outcome taxonomy；保留既有回傳值、pending checks、malformed-output rejection、tool effects。不得把 model incomplete 或 Executor prose 轉成成功機制。只記錄 hashes/counts/stage/iteration/status/category/text presence/length/tool result count，不記錄來源、prompt、敘事、credentials。

於使用者指定的 run_conversation／Executor resolution seams 加 regression。真實 response fixture 只保留 SDK enum/shape，內容改成自造文字。覆蓋 no-tool text、mixed text/tool continuation、多個 outputs、status、empty/max iterations、malformed、pending checks，以及 model incomplete 必須仍 incomplete。真實 canary 使用 persisted Haunting，router scenario-use/start、兩個普通回合、state reload；import-time counters 必須零。

沒有 incorrect transition evidence 就不宣稱已修正行為；其餘五本本輪不執行。

## 已確認 optional-field wire defect

真實 skill_check failure hash 精確符合 opposed_checks.contract invalid-value rejection；該分支只會在 opposed 全部欄位都有提供時觸發。OpenAI runtime function tools 未指定 strict，Responses 可能把 schema 正規化為 strict mode，使 optional 欄位變必填。Wire regression 在修正前失敗：repo schema 的 opposed 明明 optional，但 strict 沒有明訂。Adapter 現在只對 runtime function tools 發送 strict=false；analysis/image paths 不變。Supplied opposed 仍完整通過既有 deterministic validation；不把空 object 改成 None，不改技能、擲骰、角色所有權。

參考：[官方 function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)。Failure fixture 僅自造資料，不含 PDF 原文。

## 真實 acceptance

使用既有 published Haunting，不重新 import；真正 library reload、scenario use、start 成功。第一回合「我環顧四周。」為 no_mechanics/validated；第二回合聆聽為 await_check/validated，合法 pending 檢定保留，opposed 為 absent。兩回合 assistant turn 完成，未冒充已擲骰。State 持久化 revision12，兩回合 import-time counters 都零。Post-fix 8 admissions／8 transports／8 HTTP200，SDK retry零；before-fix diagnosis 共30 transports，未增加 cap。其餘五本依本輪 scope 未跑。

Sanitized shapes／hashes／loop transitions／resolution 可查 results JSON。待 full suite 及最終 review 完成後更新 merge gate；rollout 仍待更廣 corpus validation。

## 最終驗證

2251 passed、1 skipped、152 subtests，零 error/failure（36.406s）。第一次 full run 發現七個 tool-only fixture telemetry regression；已修正 optional output_text handling，最終 full suite 全綠。Ruff、mypy（140 files）、compileall、diff-check PASS。Standards0／Spec0 remaining actionable finding，已補 catalog entry。沒有修改 PDF admission 或 gameplay 規則。已知普通回合 P1 gate 由真實兩回合 acceptance 解決。PR155 已知 findings 的 merge gate READY；production rollout HOLD，仍待其餘 corpus 驗證。P2 staged-part cleanup／更廣 corpus、portability、feature validation deferred；P3 cleanup deferred。
