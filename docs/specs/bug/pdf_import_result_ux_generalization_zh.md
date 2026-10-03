# PDF 匯入結果 UX 與真實 corpus 泛用驗證

依據：2026-10-03 PR155 使用者需求。基準：`1fe274acf09840d0c0518e74816829d01640ee2c`。

發布成功必須明確顯示成功與 `/coc start`。輔助功能警告不得變成來源 blocker。可恢復草稿明確顯示等待處理、已保存進度及 continue/status/cancel。無法讀取的 PDF 顯示未啟用與重新上傳，不洩漏 exception body。相似劇本或新舊劇本選擇顯示等待選擇，不能冒充失敗或已啟用。

不修改 admission、OCR、topology、map certificate、gameplay、retry。不得新增書名或頁碼特化邏輯。其餘五本真實 PDF 依序以獨立 storage 與原 production caps 驗證。repository 僅存 sanitized hashes/counts/status/reasons；可發布劇本須實測 reload/activation/start，未完成不得推論成功。

驗收：四種結果與 persisted state 一致；每個 False 出口可理解；真正來源安全 gate 保留；完整 tests 與 Standards/Spec review 通過。真實泛用驗證完成前 rollout HOLD。
