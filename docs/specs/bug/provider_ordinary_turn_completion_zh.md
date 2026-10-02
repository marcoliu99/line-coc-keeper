# 普通回合 completion diagnostics

基準 `f2004d6`。範圍僅限 OpenAI runtime response、tool loop、Executor resolution；不改 PDF/admission/source safety 或 gameplay 規則。

真實 trace 顯示：舊 Haunting state 收到 provider completed response，內容是可解析的 Executor JSON，actor 正確，但 disposition 明確為 incomplete；runtime 以 model_incomplete 保留模型裁決。尚未證明 parser 丟字或 status mapping 錯誤。同一 published library 的乾淨 state，第一回合可完成 no_mechanics/validated。Temperature omission 是既有設定前提，不是語意 root cause。

最小修改只增加安全的 response shape、loop termination 與 Executor outcome taxonomy；保留既有回傳值、pending checks、malformed-output rejection、tool effects。不得把 model incomplete 或 Executor prose 轉成成功機制。只記錄 hashes/counts/stage/iteration/status/category/text presence/length/tool result count，不記錄來源、prompt、敘事、credentials。

於使用者指定的 run_conversation／Executor resolution seams 加 regression。真實 response fixture 只保留 SDK enum/shape，內容改成自造文字。覆蓋 no-tool text、mixed text/tool continuation、多個 outputs、status、empty/max iterations、malformed、pending checks，以及 model incomplete 必須仍 incomplete。真實 canary 使用 persisted Haunting，router scenario-use/start、兩個普通回合、state reload；import-time counters 必須零。

沒有 incorrect transition evidence 就不宣稱已修正行為；其餘五本本輪不執行。
