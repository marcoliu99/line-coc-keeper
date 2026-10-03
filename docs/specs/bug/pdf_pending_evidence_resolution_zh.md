# 五本待處理證據收尾

使用既有相容草稿與已消耗額度核對 45 頁圖片必要性、12 頁閱讀順序。分類 candidate 不是唯一必要 source 的證明；無法確認的證據保留 pending，不假稱必要。不改 admission、runtime、OCR、map、provider policy 或書籍特例。

## 已重現通用缺陷與最小修正

頁底中央窄 block 的 bbox 跨過既有 footer threshold，被當右欄正文並縮窄 gutter。僅對 unresolved ambiguous_gutter 增加 deterministic recovery：窄、淺、中央、頁底 block；其餘正文必須形成有足夠間距的兩欄，不存在 spanning body；其餘 furniture 位置必須可確定。複製 evidence，只修 footer role，保留所有 ID/text，再通過原 geometry/permutation validator。不得花 provider request。

不改 accepted decisions/native analyzer，因此 accepted cache identity 保持；needs_review 不能作 safe cache reuse，Continue 必須重新驗證。

## 驗證與隱私

先 synthetic FAIL 再最小 fix/PASS；測 broad/overlap rejection、mechanics/text preservation、budget 不變。五本 sequential Continue，不 reset/refund/提高 cap/new draft。source-safe 才 publication/reload/activation/start。原圖、原文、provider response 留 private；repo 僅 hashes/enums/counts。執行完整 tests、lint、types、compile、diff 及 Standards/Spec review。

## 另已重現 evidence-binding 缺陷

完整 canonical sentence 明確提供自建角色替代選項，但以玩家偏好條件句加肯定允許，或明確提供預製角色與空白自建角色卡，現有 matcher 無法辨識。僅補肯定 permission；保留 mandatory-pregen conflict、exact source binding、author section、mixed-region audit、clue checks。不得依外觀判 optional。先 synthetic positive/negative FAIL/PASS；cache observations 重新綁 canonical evidence，不新增 classification request。
