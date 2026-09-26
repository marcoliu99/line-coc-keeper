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

## 正式實作候選接口（待本輪結果決定）

```text
provider.run_conversation
  -> InputEstimate(parts, total, tokenizer, inherited_context_known)
  -> AdmissionController.reserve(scope, estimate, output_budget, deadline)
       + RPM 窗口 + TPM 窗口 + scope 共用冷卻
       + 等待前檢查整個回合 deadline；取消則退出
  -> 現有 HTTP 並發 semaphore
  -> provider API attempt
  -> observe(headers, usage, status)
       + 429 更新共同 cooldown；Retry-After 不提前截短
       + 每次重試重新准入，已消耗額度不因 HTTP 完成立刻釋放
  -> 原有 Executor / Narrator 裁決與工具 state 更新
```

scope 至少對應 provider／project／實際 shared-model limit pool；不是每個群組各配180000。
多個服務實例共享額度時需集中 ledger；本輪原型只適用一個 sequential runner，沒有跨程序互斥保證。
正式版應在占用 HTTP semaphore 前等待預算；本輪試验透過 retry callback 包裝，等待發生於既有
semaphore 內，但只有單一 in-flight request，因此不測也不推論並發公平性／slot利用率。

可能設定：enabled、tokens_per_minute、requests_per_minute、headroom、turn_deadline_seconds、
各任務 output_limit；讀取實際 headers 後保守校正。不得把200000當所有帳戶的固定上限。
unknown previous_response_id 的繼承上下文不可估成零；正式版需恢復已保存的用量／上下文資訊
或保守預算，試驗遇到未知鏈則直接失敗並記錄。

歷史裁切優先是輸入組裝策略，而非刪除資料庫記錄。需保持最新 authoritative state、
pending action_context、Luck、敵方機制、RAG，以及可供追查的完整儲存歷史。
max_output_tokens 命中上限須列入失敗／未完成，不能算作輸出更短而已；不自動重播工具。

## 重跑說明

測試工具位於 `scripts/experiments/`，不被 app import。需安裝 tiktoken；資料集不提交。
設定 `COC_TRIAL_ROOT`（含 fixtures.json、snapshot.db、groups/、scenarios/），
`COC_TRIAL_SOURCE`（固定版本的 PR89 runtime checkout），`COC_TRIAL_ENV`（main_v2 .env）。
使用新空結果目錄避免混合舊結果；ledger 不應手動清空以繞過已消耗額度。

```bash
python3 -m pytest -o addopts='' -q tests/test_token_admission_experiment.py
python3 scripts/experiments/run_token_admission.py batch --limit 40
python3 scripts/experiments/analyze_token_admission.py
```

`batch` 會消耗真實 API 額度；一次最多四組各10回合，每回合可有多次 API 請求。
