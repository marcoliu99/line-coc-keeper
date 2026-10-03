# PR155 來源邊界回歸修正

基準：`4288029d499c98c6847dde90a14f6b3510456377`。使用者 review 對照舊版 `a0c4f7e`，各 finding 必須依最新程式重新驗證。

## 任務相依圖

A（F1/F2）：重複來源須匹配完整對齊語意單位，保留否定與條件；所有預製角色 optional 路徑共同檢查強制選角與完整 coverage。

B（F3）：有效 PDF 即使開頭無原生文字 preview，仍進正式解析；空 preview 不進相似度比對。

C（F4–F8）：檢查現有 playable source、optional 判斷、保存 PDF reparse、發布邊界與 failure cache；A/B 後只修仍可重現缺陷，保留已完成行為。

D 相依 A/B/C：整合、完整驗證、Standards / Spec review。

## 不變量

否定／条件句中的子字串不是重複指令。引用綁定只證明 provenance，不證明 optional。缺少 preview 不等於無法讀取 PDF。Candidate 不進遊戲權威來源。標題／placeholder 不能單獨構成可遊玩正文。失敗 provider attempt 維持已消耗，明確操作重試可在剩餘上限內另 reserve；成功 evidence 僅對相同輸入 reuse。發布前先檢查 revision／確認，不能先覆蓋現有來源。Reparse 失敗、衝突或較弱 evidence 不得破壞已發布來源與遊戲 state。

禁止書名特例、OCR/model 更換或新 provider／transport retry 層。Regression 使用 synthetic fixtures，私人 corpus evidence 不提交。沿用既有 PR155。
