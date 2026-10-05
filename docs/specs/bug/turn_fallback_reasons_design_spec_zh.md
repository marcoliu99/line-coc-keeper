# 回合 fallback 原因與有界復原

[English](turn_fallback_reasons_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`bug`。狀態：**已實作**。來源：Camp Sunny 五人 500 回合驗證的 CS-008（Workstream B）。基於 `main_v2` 的 `aa79f22`。

17 個看起來合理的玩家行動都收到同一句「這次行動目前無法繼續」。沒有任何紀錄說明原因：這句話由同一個函式從十幾種驗證結果產生，所以這 17 個案例可能同根，也可能有 17 種原因。該次執行的日誌不在 repo 裡，無法重播歷史案例；這次變更讓之後的每一個案例都能被分類，並讓可復原的案例再有一次機會。

## 契約

1. **穩定的原因。** `FallbackReason`（`app/domain/models.py`、`FALLBACK_REASONS`）有十二個值：`no_scenario_evidence`、`executor_no_action`、`unresolved_pending_state`、`invalid_tool_plan`、`tool_failure`、`tool_result_rejected`、`narration_failure`、`state_conflict`、`unsupported_action`、`safety_block`、`internal_error`、`unknown`。`turn_fallback.classify` 把 Executor 的每種驗證結果（`validation_code`、執行健康度、失敗的工具、依據）對應到恰好一個；`unknown` 只是最後手段，已知的 code 落入 `unknown` 時測試會失敗。
2. **一個日誌事件。** 每個會回給玩家通用回覆的地方都記錄 `turn.fallback`，包含原因與該回合的證據：campaign、timeline、玩家、回合、場景與章節、自己與他人的待處理檢定／Luck、檢索筆數與命中 id、Executor 的決定、嘗試過的工具與哪些失敗，以及是否復原、結果如何。涵蓋：Executor 的 blocked／incomplete／deferred 裁決、未變動的待處理回覆、敘事失敗、為安全而替換的回覆，以及因時間線已更新而被拒絕的提交。識別碼走既有的遮蔽設定；不記錄憑證或 provider 內容。
3. **具體的回覆。** incomplete／blocked 回合最後那句通用的話，改為該原因對應的指引（下一步該做什麼），不再是「請先確認目前狀態或更正原本的行動」。已經指明待處理檢定或 Luck 的文字不變。
4. **一次有界的復原**（`supervisor._recover_blocked_turn`，開關 `TURN_FALLBACK_RECOVERY_ENABLED`）。只在原因是 `no_scenario_evidence`、`executor_no_action`、`invalid_tool_plan` 或 `unsupported_action`，**而且**第一次嘗試沒有改變遊戲狀態、沒有擲骰、沒有事件／結果／私訊／圖片，並讓每個待處理檢定與 Luck 決定保持原樣時才會進行。若缺的是依據，會做一次針對性的劇本搜尋（目前場景＋章節＋玩家原文；這一次接受詞彙檢索結果），存成 `recovery_context`，Executor 再裁決一次。不會有第二次重試；工具失敗、狀態衝突或待處理等待都不重試。

## 保持不變的契約

機制仍是確定性的：重試不會套用第一次沒動過的東西以外的任何變更，通用文字也不會變成成功。待處理檢定、Luck、暫緩與取消的既有文字不變。`TurnPayload` 新增一個宣告的 key `recovery_context`，只由 supervisor 寫入、Executor 讀取。

## 執行方式

`tests/test_turn_fallback.py`：每個驗證 code 都有原因；每個原因都有不同的指引；復原成功、未解決與重試上限（最多一次搜尋、多一次 Executor 呼叫）；狀態改變、擲骰、事件、工具失敗或待處理等待之後不重試；開關；搜尋失敗；事件欄位；每個非 Executor 的地方都記錄其原因；一般回合不記錄；以及一道在 supervisor 少記錄任何一個通用回覆位置時失敗的檢查。

## 未涵蓋

17 個歷史回合沒有重播（日誌不在此環境），所以無法得知它們會被分到哪些原因。重試是否能在真實劇本中找到缺少的依據，需要定向的真實執行驗證，本次未執行。
