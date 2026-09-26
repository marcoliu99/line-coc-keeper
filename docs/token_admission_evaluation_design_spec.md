# Token 准入與輸入組成量測：試驗規格

狀態：40回合隔離API試驗已完成；尚未變更 production runtime。
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
| 歷史對話 | 同 admission | 最多最近 5 個 user 起始的完整對話區段 | 原樣省略 |
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

## 量測細節與解讀邊界

四組固定順序 baseline → admission → history → output，每組10案依同樣順序。
本輪沒有交叉平衡與重複輪次；不能將模型工具選擇差異單獨歸因於准入／歷史／輸出設定。
相同案例配對差只作描述，不提供會誤導的小樣本顯著性結論。

Token components 以各部分獨立序列化計數，總和與供應商封裝不會完全相同。
延續鏈包含之前輸入與模型輸出物件估計；模型輸出物件的序列化 envelope 可能高估
實際繼承的上下文。報告必須列 estimate/actual 比例，不能隱藏准入政策的額外保守成本。
正式版本應改善此估計，不應把試驗近似計數當精確用量。

成功完成 API 呼叫、非 incomplete 裁決、可觀察契約通過是三個不同指標。
可觀察契約包含舊測試的數值／物品／combat invariant，並修正已知的過窄假設：
攻擊可合法 deferred、製作更正可保留原 pending、查牆可免檢定完成但不能直接改等另一人。
逃生案例的 pending 要求仍保留為待人工判讀項，不當作通用規則正確性。
不回溯修改不同組的評分以使某組看起來更好。

正式功能是否採用要同時滿足：可接受的完整回合時間、沒有明顯正確性退化、
安全處理工具部分完成，以及限流／排隊可觀察。單獨0次429不足以通過。


## API 試驗結果（2026-09-26）

已完成四組各10回合，共40回合。固定 runtime `776bf1f`，main_v2 .env，隔離 DATA。
所有組統一省略已知不支援的 temperature；未修改實際 .env。

### 主要指標

| 組別 | 邏輯請求 | 429 | 准入等待合計秒 | 重試等待合計秒 | 平均／中位／最慢回合秒 | API完成 | 非incomplete裁決 | 可觀察契約 |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| 原樣 | 36 | 13 | 0.00 | 69.00 | 24.67 / 24.17 / 58.15 | 10/10 | 10/10 | 10/10 |
| 僅准入 | 37 | 0 | 201.19 | 0.00 | 39.99 / 37.18 / 89.36 | 10/10 | 8/10 | 7/10 |
| 准入＋最近5輪 | 37 | 0 | 143.02 | 0.00 | 31.51 / 25.97 / 64.83 | 10/10 | 9/10 | 9/10 |
| 准入＋輸出上限1200 | 34 | 0 | 201.47 | 0.00 | 36.83 / 41.32 / 65.28 | 9/10 | 10/10 | 9/10 |

完整回合時間包含准入、API重試、RAG、工具、敘事與state提交；不含案例複製及組間65秒冷卻。
API完成要求沒有最終例外且所有回應非incomplete；後兩欄也不是無限制劇情／規則正確率。

### 實際 usage 與估計誤差

| 組別 | input tokens | cached input | output tokens | 估計／實際輸入平均比 | API incomplete 次數 |
|---|---:|---:|---:|---:|---:|
| 原樣 | 990,645 | 411,280 | 10,273 | 1.070 | 0 |
| 僅准入 | 1,015,159 | 539,796 | 13,114 | 1.083 | 0 |
| 准入＋最近5輪 | 755,227 | 433,592 | 11,379 | 1.102 | 0 |
| 准入＋輸出上限1200 | 921,176 | 502,133 | 10,965 | 1.076 | 1 |

### 原樣組輸入組成（本地估計，含延續鏈重複使用）

| 部分 | 占比 |
|---|---:|
| 固定規則 static | 30.6% |
| 當前狀態／RAG dynamic | 11.9% |
| 工具 schema | 22.1% |
| 歷史對話 | 28.5% |
| 本次訊息 | 0.1% |
| 工具結果 | 2.6% |
| 延續鏈先前模型輸出 | 4.2% |

本機 tiktoken 不認得模型名稱，全部使用明確標記的 o200k_base fallback。
components 不等於服務商 token ledger；尤其 prior_outputs 序列化可能偏高。
usage 表僅加總已取得usage的回應，不含未回傳usage的失敗嘗試；不能直接換算伺服器每分鐘預留額度。
歷史原有74–126筆，估計5,898–14,907 tokens；縮到最近5個user區段後為711–1,247 tokens。
歷史組沒有新增摘要，只沿用既有summary／RAG／完整state；不可宣稱沒有遺失任何早期對話細節。

### 相同案例描述性比較

| 對照 | 平均完整回合差 | 實際input總量差 | 邏輯請求數差 |
|---|---:|---:|---:|
| 僅准入 − 原樣 | +15.32 秒 | +2.5% | +1 |
| 准入＋最近5輪 − 僅准入 | -8.48 秒 | -25.6% | +0 |
| 准入＋輸出上限1200 − 僅准入 | -3.17 秒 | -9.3% | -3 |

沒有重複輪次及交叉平衡，不將差值宣稱為因果效應／統計顯著改善。

### 未通過的可觀察契約

| 組別 | 案例 | 裁決 | 未通過項目 |
|---|---|---|---|
| 僅准入 | read_status | incomplete | validated_handoff |
| 僅准入 | start_shoot_body | incomplete | validated_handoff, attack_wait_or_resolution |
| 僅准入 | escape_transition | no_mechanics | pending_actor_check |
| 准入＋最近5輪 | start_shoot_body | incomplete | validated_handoff, attack_wait_or_resolution |
| 准入＋輸出上限1200 | combat_craft | resolved_without_check | no_truncated_response |

准入組 read_status 額外呼叫 add_carried_item、射擊案例出現扣彈藥再補回；驗證器保守回覆未完成。
歷史組射擊也出現扣彈藥／建立檢定後取消／補回，不能把最終數值相同當作從未執行。
准入組 escape_transition 選擇免機制離開，舊pending契約不通過；是否需要骰仍須劇本人工判讀。
輸出組 combat_craft 的 Narrator 用完1200 output，其中1200均為reasoning，回傳 incomplete/max_output_tokens。工具已完成，敘事仍未正常完成；按試驗前規格列為失敗。
這些差異涉及模型抽樣與工具選擇，不可直接歸因於速率控制。

### 試驗後決策

- 不直接把180000/60秒硬窗口設為正式預設：本輪消除了429，卻大幅增加准入等待。
- 歷史縮減在本輪降低輸入量；完整回合仍須連同正確性與准入等待評估，不能只看Token。
- 輸出上限的效果以上表為準，不宣稱自動提升2–3倍吞吐。其他組曾有單次1748 output，其中1663 reasoning，說明上限需留推理餘裕。
- 正式版先校正估計、結合headers／共享冷卻／總deadline，採小幅可配置歷史預算；保留state與RAG。
- 正式runtime尚未接入，本輪只交付原型、離線測試、API結果與規格。

### 重現與資料保護

原始state、完整敘事及工具軌跡留在本機 `/private/tmp/coc-token-admission-eval/`；不提交repo。
去識別化聚合與逐案例數值見 `docs/evaluations/token_admission_20260926.json`。
測試原型離線4項通過，修改檔案Ruff、compileall、git diff --check通過。
