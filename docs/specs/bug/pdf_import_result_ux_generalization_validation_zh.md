# 驗證 — PDF 匯入結果與泛用性

False 出口 audit：副檔名錯誤與無法讀取來源顯示失敗及重傳；revision 過期顯示重新開啟 Help；Luck、既有新舊選擇與相似 PDF 顯示等待處理及指令；ownership conflict 顯示草稿或處理中的實際情況；LayoutReviewRequired 保存實際 checkpoint 後顯示 pending；ValueError 僅在已有 page checkpoint 時顯示 pending，其餘失敗；發布後 revision 過期顯示沒有套用；choice race 顯示先完成舊選擇。

unexpected exception 保留 raise。本次 activation commit 完成後若清理或訊息組裝失敗，仍顯示匯入成功及輔助處理警告；不能將既有同 PDF 的 activation 冒充本次成功。使用 attempt-local callback，未新增 persisted publication state。

未修改 bool、admission、來源安全 gate 或 gameplay。一般錯誤訊息不包含 exception body。真實五本結果另存 sanitized JSON；未完成不得宣稱成功。
