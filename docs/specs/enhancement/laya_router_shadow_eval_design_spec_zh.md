# Laya 分流器影子評估設計規格

## 問題與目標

目前 `app.agents.intent_router` 使用確定性規則把玩家回合分成 `PURE_ROLEPLAY`、`GAMEPLAY_ACTION` 或 `OOC_ASSISTANT`。本實驗想評估本機 Laya 分流器，判斷哪些原本會進入 Executor 的文字看起來不需要遊戲機制。評估期間必須保留現有路由結果與遊戲行為；Laya 只提供旁路預測，不能略過 Executor 或改變 Narrator 的輸入。

交付物包括可選的 Laya shadow client、獨立 Node.js Laya 服務、以實際 Keeper 工具呼叫為標籤的離線評估器，以及可重現的執行說明。初次正式樣本以 200 個合格玩家回合為目標。

## 範圍

- 僅在 `player_action` 且現有分類結果為 `GAMEPLAY_ACTION`、即將呼叫 Executor 的路徑呼叫 Laya。
- 使用 `LAYA_SHADOW_URL` 啟用 shadow client；設定為空時不啟動 HTTP 請求，也不影響一般部署。
- 預設最多等待 3 秒。連線錯誤、逾時、無效回應或服務關閉只記錄 shadow 錯誤，不阻止或延遲既有流程超過設定期限。
- 寫入結構化事件 `laya.shadow`，欄位包含 `turn_id`、狀態、預測類別、信心值、模型修訂、延遲與錯誤類型；不得記錄完整玩家句子或劇本內容。以既有 turn ID 關聯 `app.turn` 文字紀錄及工具觀察。
- Node.js 實驗服務固定套件版本，支援明確固定 Hugging Face revision 或指定本機模型目錄，並可輸出實際載入的模型 revision；預設在本機 CPU 執行。
- `eval.mjs` 從一個或多個 runtime JSONL 讀取文字紀錄、Laya 預測及該回合 Executor 實際觀察到的工具呼叫，依 `turn_id` 關聯並輸出 `report.md` 與 `results.jsonl`。
- 評估指標包括納入／排除筆數、Laya 錯誤及缺漏、誤放數（高信心判為純敘事但實際工具顯示需要機制）、在門檻下可跳過 Executor 的估計比例，以及最危險的誤放樣本。
- 待處理檢定、Luck 決定、敵方行動等待、戰鬥暫停、工具失敗等阻塞回合預設排除於準確率與捷徑比例；可用 `--include-blocked` 另行納入。

## 非目標

- 本變更不使用 Laya 預測改變即時路由，不省略任何 Executor 呼叫。
- 不新增玩家可見訊息、Discord 指令、資料庫欄位或遊戲狀態。
- 不將 Laya 改造成 Keeper、意圖路由器正式替代品或遊戲規則來源。
- 不把沒有工具呼叫直接視為「純敘事」標籤；必須辨識阻塞回合並在預設分析中排除。
- 不固定或變更正式遊戲使用的 provider/model、推理強度、場景或工具設定。

## 資料與設定

新增設定：

- `LAYA_SHADOW_URL`：空值代表停用；啟用時指向服務的 `/route` 端點。
- `LAYA_SHADOW_TIMEOUT`：正數秒數，預設 `3`。

新增 `laya.shadow` 結構化事件，以現有 `observability.event` 發送，僅使用低敏感度預測欄位。事件透過 `turn_id` 關聯，不建立第二份使用者文字副本。評估輸出逐回合保留原句時，來源限於使用者明確開啟的 `LOG_TEXT_ENABLED=true` runtime 文字頻道；輸出檔不可預設提交至版本控制。

Node.js 子專案應有獨立 `package.json` 與 lockfile，固定 Laya 版本；評估器不得依賴 Python bot 的非公開記憶體狀態。報告應記錄套件版本、模型 revision、執行參數及輸入檔名，以利重現。

## 流程

1. Supervisor 依現有規則分類回合，保持現有路由不變。
2. 僅對即將呼叫 Executor 的玩家 `GAMEPLAY_ACTION`，以 turn ID 與玩家文字向本機 shadow 端點送出旁路請求；shadow 任務受 timeout 約束。
3. 不論 shadow 成功、失敗或逾時，Supervisor 照舊呼叫 Executor、Reducer 與 Narrator。shadow 故障不改變遊戲結果。
4. Runtime log 記錄預測、狀態及延遲；既有文字 log 記錄可關聯的玩家句子，既有工具觀察提供實際標籤。
5. 離線評估器按 turn ID 對齊三種紀錄，拒絕歧義/重複 ID，並將缺少 shadow 或文字的回合作為缺漏列出，不補造答案。
6. 200 個回合 soak 完成後，確認樣本數與 shadow 成功率，再產生報告及逐回合結果。

## 與既有架構整合

- Hook 位於 `app/agents/supervisor.py` `_mechanics` 中，緊接 `GAMEPLAY_ACTION` 的 Executor 路由、在 `executor.run_executor(message)` 前。不能移到 Discord command router，因為後者尚未得到最終 gameplay intent；也不能對 OOC、純敘事、檢定續接或狀態捷徑呼叫 Laya。
- 使用 `app.observability.event` 及目前 request context 的 `turn_id`；不得在 shadow logger 引入另一套識別碼。
- Client 使用標準非阻塞 HTTP 呼叫並局部捕捉可恢復錯誤。取消 Supervisor task 時必須遵守既有取消語意，不得吞掉 `CancelledError`。
- 新設定沿用 `app/config.py` 的環境值驗證和 `.env.example` 說明。
- 服務與評估器限定在 `scripts/experiments/laya_router_eval/`，不成為 bot 執行時的 Node 依賴。

## 驗收與測試計畫

- 設定空白時，不發出 HTTP 請求、不新增事件，既有 gameplay 路徑相同。
- 成功回應會記錄模型預測、revision、turn ID 和延遲；預測不會改變 Executor 次數或工具 schema。
- 拒絕連線、逾時、HTTP 錯誤及回應格式錯誤都能記錄狀態並繼續原本 Executor 流程；取消仍可傳播。
- 各種不應呼叫 Laya 的分類/續接/短路路徑都不會送請求。
- 評估器測試涵蓋工具標籤、無工具敘事、阻塞排除、`--include-blocked`、重複/缺漏 ID、失敗 shadow、門檻計算及多組輸入檔。
- 執行相關 pytest；Node 子專案執行 `npm test` 或等效的單元測試。
- 實跑驗收使用固定模型 revision，完成 200 個合格回合；核對 `laya.shadow` 成功/錯誤筆數、輸出檔筆數、排除項目及報告的誤放清單。實跑結果不作為單元測試替代品。

## 尚待決定

- Laya 0.1.2 與其 Transformers/ONNX 執行後端在 Node.js 20、Apple CPU 的實際 API、下載 revision 取得方式與資源用量，需在實作時核實；README 的約 1.7 GB 下載與 2 GB 記憶體是估值，不作硬性驗收數字。
- 200 回合真實遊戲會呼叫已設定的對話模型，可能花費相當時間/額度。本次依既有指示使用 `the-haunting-ddc24ec6`，從地下室開始；執行時固定記錄場景雜湊、模型與 seed。
- `LOG_TEXT_ENABLED=true` 會把玩家原句寫入 log。實驗操作者應確認 log 檔的保存與分享範圍；評估器只讀取指定檔案，不自行上傳內容。
- 若 runtime 現有文字 log 無法可靠地以 `turn_id` 對齊 input，需先確認可用事件型態，再決定是否增加最小化、遮蔽識別資訊的使用者文字紀錄。
