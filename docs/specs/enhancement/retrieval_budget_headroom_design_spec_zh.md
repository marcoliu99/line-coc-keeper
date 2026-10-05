# 不讓劇本搜尋被擠到沒有預算，以及沒有 tokenizer 時誠實地估算中文

[English](retrieval_budget_headroom_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `f03a110`。

## 問題

劇本搜尋拿到的預算是 `SCENARIO_CONTEXT_TOKEN_CEILING`（預設 32,000）減去這次請求已經佔的大小、輸出保留量與安全餘量，最多 `SCENARIO_RETRIEVAL_TOKEN_BUDGET`（6,000）。搜尋沒有預算就回不出可用的依據，守密人沒東西可以裁決，玩家就收到通用的「無法繼續」回覆。

Haunting 500 回合測試（`b0e875c`）記錄到 198 次 `rag.retrieval.budget` 為 0，上下文估計成長到約 68k，500 回合中有 32 回合（6.4%）以通用回覆結束。程式裡的原因，是算出來的，不是量出來的：

- 57 個工具 schema（約 13k tokens）與靜態提示（約 8k）每次 Executor 請求都會送出：在任何狀態或歷史之前就已經約 21k tokens。這些大小是我的估計（沙盒沒有 tokenizer；算法是中文字 0.9 token、其他文字字元數的四分之一），不是實測。
- 32,000 減約 21k 再減 6,144，剩不到 5k 給動態提示、遊戲歷史（非 OpenAI 的 provider 會送出並計入整份 log，最多 160 筆）與本回合的工具結果。幾十回合之後就沒了。
- 另外，載不到 tokenizer 時估算會退回 UTF-8 位元組，這會把每個中文字算成三倍（一個字約一個 token），同樣的上下文看起來大約三倍，預算更快歸零。Haunting 的基準已經包含 tokenizer 重試修正，所以沒有證據顯示那裡就是這個原因；這是 `tiktoken` 沒裝或快取是冷的時候的失敗模式。

## 限制：不新增模型呼叫，也不讓提示變長

送給 provider 的內容完全不變。改變的只有「一次搜尋最多能回多少劇本依據」的算術，以及它記錄的標籤。

## 變更

- **`SCENARIO_RETRIEVAL_MIN_TOKENS`**（預設 3,000，上限是 `SCENARIO_RETRIEVAL_TOKEN_BUDGET`，`0` 恢復舊行為）：搜尋至少拿到這麼多。3,000 是一般預算的一半，也等於主動搜尋的上限。`rag.retrieval.budget` 事件新增 `budget_floor_applied` 與 `budget_before_floor`，用到下限時是 WARNING，操作者看得出上限設得太低。
- **`SCENARIO_CONTEXT_WINDOW_TOKENS`**（預設 128,000，低於所有支援 provider 的視窗）：硬上限。下限可以超過上限（它只是規劃用的數字），但絕不超過視窗：預算最多是視窗減去提示與輸出保留量之後剩下的（可能是零），所以下限不會因為太大而讓請求失敗。同一回合中每一輪先前的工具呼叫再從剩餘空間扣 1,000 tokens（`PRIOR_ROUND_ALLOWANCE_TOKENS`）：呼叫與結果已經算進去，但模型在呼叫前自己的文字與推理只存在於 provider 的訊息串裡。這個數字是預留量，不是量測值；provider 回報的用量沒有接到這個位置。事件新增 `context_window` 與 `budget_capped_by_window`。模型視窗較小時請調低。
- **真正的解法是設定，不是程式：** 把 `SCENARIO_CONTEXT_TOKEN_CEILING` 調到模型實際的上下文視窗減去餘量。這個值由操作者決定（本專案不知道 provider 的視窗大小）；`.env.example` 與設定指南都這麼說。
- **備用估算認得中文**（`input_budget.fallback_tokens`）：每個 CJK 字元（含假名、諺文與全形符號）1.5 token，其他每三個位元組 1 token，取代每個位元組 1 token。罕見字元（擴充 A、諺文字母、相容表意文字，以及基本多文種平面以外的所有字元：擴充 B 之後的漢字、emoji）每個 3 token，含數字且 20 字元以上的識別碼樣式連續片段（hex id、雜湊、base64）每兩個字元 1 token。它對一般文字偏高，但只是估計而不是上界：特殊文字仍可能更貴，輸出保留量、安全餘量與上面的視窗就是為此而設。標籤從 `utf8_bytes_fallback` 改為 `fallback_estimate`（舊名稱描述的已經不是它在做的事）。模型未知時，搜尋內部計算依據成本也用它，所以過去因為成本被拒的必要依據現在放得下。

## 沒做的

- 限制非 OpenAI provider 收到的歷史（OpenAI 路徑保留 4,000 tokens）會縮小提示，但會改變守密人看到的內容，而且沒有證據顯示它讓回合更快：Dead Boarder 的 92 個一般回合，等待中位數是平的（前 20 回合 42 秒、最後 12 回合 42 秒），而 log 成長了二十倍。
- 工具面（每次請求 57 個 schema）是最大的固定成本，也是獨立的延遲槓桿；留給有量測資料的工作。
- 這些都沒有用真實 provider 或真實日誌跑過。要看有沒有幫助，請在同一個劇本上比較前後的 `budget_floor_applied` 與以 fallback 結束的回合比例（`scripts/summarize_turn_log.py`）。

## 驗證

`tests/test_retrieval_budget_floor.py`（各文字種類與混合文字的估算、不低於每字一個 token、一般 `estimate` 經由備用估算；有空間、空間不多、上下文全滿、關掉下限、下限高於預算、只調高上限時的預算）與更新後的 `tests/test_retrieval_readiness.py`（原本釘住位元組數的數字改釘新的估算；新增一個案例保留「候選資訊在預算很緊時會縮減」的保證）。`ruff check .`、`mypy app` 與完整 `pytest` 通過。
