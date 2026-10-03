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

## 實作結果

F1/F2 改用完整 normalized sentence units，並共同檢查 mandatory selection／coverage。F3 將空 preview 視為無相似度證據；不可讀 PDF 仍失敗。F4 先組合 final selected safe source 再交給下游；標題／placeholder 不能代替未讀取的 raster 正文。有限的 positive canonical dependency fast path 將必要圖片附件綁定 final selected source、圖片 identity 與 region。自動 dependency receipt 只有 authoritative recovery 或所有完整 observed fragments 都有 counterpart 才解除；歷史 native candidate、partial coverage、錯誤 cover 分類都不能推翻明確 canonical requirement。

F6 支援保存 source.pdf 與指定 scenario ID reparse，legacy publication provenance 不偽稱 source verified。F7 在 mutation 前檢查 confirmation／revision／lease，保留不可變內容版本並 pin 正在玩的劇本；明確 correction 保留章節與遊戲 state。F8 將成功 evidence 與已消耗失敗 attempt 分開；明確 Continue／reparse 可在剩餘 cap 內 reserve 新 attempt，cached classification retry 不重啟 OCR／map recovery。

F5 保留 P2 recall 限制：optional wording 辨識不是完整 semantic discovery。可信正文存在時，未知附加內容可 quarantine；未宣稱能證明或發現任意自然語言必要依賴。Hash／schema 只證明 identity，不等於語意正確。

詳見[驗證紀錄](pr155_source_boundary_regressions_validation_zh.md)。本輪新增 external provider request 為 0。
