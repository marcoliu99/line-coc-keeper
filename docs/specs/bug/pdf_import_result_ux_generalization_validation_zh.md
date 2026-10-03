# 驗證 — PDF 匯入結果與泛用性

False 出口 audit：副檔名錯誤與無法讀取來源顯示失敗及重傳；revision 過期顯示重新開啟 Help；Luck、既有新舊選擇與相似 PDF 顯示等待處理及指令；ownership conflict 顯示草稿或處理中的實際情況；LayoutReviewRequired 保存實際 checkpoint 後顯示 pending；ValueError 僅在已有 page checkpoint 時顯示 pending，其餘失敗；發布後 revision 過期顯示沒有套用；choice race 顯示先完成舊選擇。

unexpected exception 保留 raise。本次 activation commit 完成後若清理或訊息組裝失敗，仍顯示匯入成功及輔助處理警告；不能將既有同 PDF 的 activation 冒充本次成功。使用 attempt-local callback，未新增 persisted publication state。

未修改 bool、admission、來源安全 gate 或 gameplay。一般錯誤訊息不包含 exception body。真實五本結果另存 sanitized JSON；未完成不得宣稱成功。

## 真實 production 結果

六本 SHA256 與 baseline 一致。其餘五本以 fresh isolated state 依序走 handle_pdf_upload，沿用原 caps 與授權 OpenAI gpt-6-luna。結果均保存草稿並顯示 IMPORT_PENDING，沒有新 publication，故沒有执行 reload/activation/start。

Dead Boarder：6 image；Lightless Beacon：17 image、1 ordering；Camp Sunny：6 image；Scritch Scratch：14 image、2 ordering；Alone：2 image、9 ordering。mechanics review 全部為 0。Scritch p23 final selected source accepted、無 detectable corrupted mechanics，p8/p24 ordering 仍阻擋。

45 image review 中，35 UNKNOWN、10 provider SOURCE_CRITICAL candidate。candidate 並非 unique required source 的證明；未宣稱 confirmed false-block 或 confirmed unique-required 數量。NOT_PLAYABLE_TRUE_SOURCE_BLOCK 指 unresolved source safety gate 尚未允許發布，不代表每頁已證實必需。Dead/Camp 的 pregen classification 未完成 canonical alternative-character permission binding，仍待必要性核對。map/topology failures 保持 soft，沒有為泛用性加入書名/頁碼特化或修改 admission。

124 matched reservations / transport attempts；123 HTTP 200、1 MarkItDown transport 未取得 HTTP response。0 hidden retry、0 refund、0 cap increase。最終訊息有 saved draft、中文頁面原因及 continue/status/cancel，沒有 internal reason code。完整 pytest 與 tooling 通過，Standards/Spec review 無剩餘 actionable finding。

Haunting 沿用既有真實完整 acceptance，本輪 source-safe publication/start regression 通過，未重跑 classification。五本尚未證明可玩；MERGE/ROLLOUT HOLD，直到 image necessity 與 ordering evidence 的未解事項完成核對。private images/source/provider responses 未進 repository。
