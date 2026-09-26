# Token 准入與輸入組成量測：試驗規格

狀態：使用者授權先做隔離 API 試驗，結果待回填；尚未變更 production runtime。
分支：enhancement/token-admission-evaluation，基底 main_v2 95d8ca3。
測試 runtime 固定 PR #89 修正版 776bf1f；其程式未複製／合併進本分支。

## 問題與目標

先前 10 回合／39 邏輯請求出現 15 次 TPM 429，headers 顯示 200000 TPM、500 RPM，
remaining requests=499。需區分減少 API 重試與真正改善完整回合耗時。

## 隔離試驗矩陣

四組各同樣 10 個既有 DATA/log 案例，總計 40 完整回合：

| 組 | Token 准入 | 歷史 | max_output_tokens |
|---|---|---|---|
| baseline | 無 | 原樣 | 原樣省略 |
| admission | 180000 / 60 秒 | 原樣 | 原樣省略 |
| history | 同 admission | 最多最近 5 個 user 起始的完整對話區段 | 原樣省略 |
| output | 同 admission | 原樣 | 1200 |

1200 僅為依前 10 案最大 output 684 加餘裕的試驗值，不是所有任務的正式預設。
共用 main_v2 .env 與隔離 DB／scenario 副本。API 金鑰不寫入報告。
所有組統一啟用已存在 OPENAI_OMIT_TEMPERATURE，排除每個子程序重複400協商。
組內 sequential 完整回合；組間至少65秒冷卻，不製造高並發。每組10案僅一個 block，
可能受模型抽樣、供應商負載、cache 與時序影響；結果為探索性，不作統計顯著宣稱。
每回合180秒總期限（包括本地排隊），原 provider retry 次數不變；中止已提交工具不回滾。

## Token 計數與准入原型

- 使用 tiktoken 模型 encoding；未知則明確標示 o200k_base 近似值。
- 各請求記錄 static instructions、dynamic instructions、history/current input、tools、
  tool results，以及 previous_response_id 繼承上下文估計；不只計算新送出的 JSON。
- 本地預約值 = 完整輸入估計 + 輸出預算1200（未設API cap的組也使用同樣估計）。
  這是保守本地政策，不宣稱等於 OpenAI 限流公式。
- 以持久化測試 ledger 保存已送出請求的60秒預約；完成後不立即釋放；重試也先准入。
- 未完成樣本、超時與失敗仍保留。超過單次預算明確拒絕，避免永久排隊。
- 記錄實際 usage、估計誤差、retry delay、admission wait 與已驗證裁決；
  不減去 cached tokens 來猜 TPM，也不省略完整回合時間中的排隊。
- 本輪僅隔離單機准入原型；正式多 worker 共用冷卻、Redis、RPM與deadline設計待試驗後定案。

```text
固定 fixture + main_v2 env + PR89 修正版
  -> 各組介入（原樣／准入／歷史／輸出上限）
  -> 組成計數（含延續上下文）
  -> 准入 ledger（適用組） -> 原有 semaphore -> API -> 重試仍准入
  -> 真實工具提交 -> 驗證裁決 -> Narrator -> 最後指示檢查
  -> 保存隔離 state、完整回合耗時、API／排隊／裁決資料
  -> 相同案例配對描述比較 -> 回填 spec
```

## 正確性与非目標

原始 benchmark assertions 有過窄假設，保留原分數但不稱完整機制正確率。
新增可觀察契約：交接雙方背包正確且裁決不誤報 incomplete；pending 身分／Luck／回合保留；
不復活已敗敵人；已提交工具不重播。另列模型推論疑義，不使用付費 LLM 評分。
不裁掉權威 state、敵方能力、scenario RAG 或原始 pending action_context。
歷史組僅改 provider 的 history 參數，不修改儲存 log 或額外生成摘要。
本輪不修改 main_v2 .env、不部署、不修改 PR89、不開PR。

## 測試與交付

離線驗證：時間窗完成後仍占額、窗口到期、超額、deadline、延續上下文、歷史保留完整區段。
API 結果：各組429、API呼叫、等待、mean/median/max完整回合、輸入组成／usage、
API完成率、裁決 incomplete 與機制契約，並保留失敗樣本。
本地 raw artifacts 不入庫；只提交去識別化聚合指標、可重跑腳本與規格。

## 官方依據

- https://developers.openai.com/api/docs/guides/rate-limits
- https://developers.openai.com/api/docs/guides/reasoning

輸出上限包含 reasoning；需處理 incomplete，不能以截斷輸出掩飾錯誤。
