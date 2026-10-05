# 五人實跑的回合延遲報告（2026-10-06）

## 結論與證據邊界

截至本報告撰寫時，`origin/main_v2` 是 `0de529b4789fe4858e8a162e5667663fa2eeb04e`。本機可驗證的最近一次完整 Camp Sunny 五人實跑，則是在 `1fd3ccbcb53422f578995e87b493b900c1c6eec7` 執行的 200 個一般回合。因此，以下數字**不是** `0de529b` 的實測，也不能用來宣稱最近提交改善或惡化了延遲。

該次 Camp Sunny 實跑的回合時間中，Executor 佔獨占時間總和的 **59.0%**，Narrator 佔 **23.6%**，排隊佔 **15.9%**。五人並行 burst 的端到端 p95 是 **171.953 秒**。下一個可靠的效能決策，需要在最新基線重跑同一劇本與組態；目前沒有足夠證據決定應先改 Executor、開啟旁白出鎖的預設值，或調整其他熱路徑。

## 樣本與方法

| 樣本 | Git SHA | 一般回合 | 五人並行回合 | 用途 |
| --- | --- | ---: | ---: | --- |
| Camp Sunny 舊樣本 | `38cfad277f51048eac4fb39f2de80ec0da10d676` | 500 | 50 | 歷史參照；執行時本機分支已落後遠端 |
| Camp Sunny 最近完整樣本 | `1fd3ccbcb53422f578995e87b493b900c1c6eec7` | 200 | 20 | 本報告的主要階段分析 |
| Dead Boarder 最近完整樣本 | `1fd3ccbcb53422f578995e87b493b900c1c6eec7` | 200 | 20 | 不同劇本的交叉參照 |
| 目前 `origin/main_v2` | `0de529b4789fe4858e8a162e5667663fa2eeb04e` | 未實跑 | 未實跑 | **沒有新的五人量測** |

Camp Sunny 最近樣本有五名不同玩家，每人 40 個一般回合；每 50 回合有一次五人並行 burst，合計 180 個循序回合與 20 個 burst 回合。Keeper 使用真實 Codex API、`gpt-6-luna`、`medium`，玩家行動由真實模型生成。`NARRATION_OUTSIDE_MUTATION_LOCK=true` 確認在 runtime 生效；這是測試組態，**不是**目前 `.env.example` 的預設值（預設 `false`）。首次 RAG 初始化不計入 gameplay 時間。

階段表使用該 run 的 200 條 `app.turn` / `turn.summary`，排除 70 條檢定續寫。分位數沿用 `scripts/summarize_turn_log.py` 的排序取位法；階段是 `app/services/turn_phases.py` 的獨占時間。表中的「時間占比」是該階段在 200 回合的時間總和除以 200 回合牆鐘時間總和，不是各階段中位數相加。

## 玩家等待與階段分布

### Camp Sunny：端到端 Router 計時

| 範圍 | 樣本數 | p50 | p95 | 最大值 |
| --- | ---: | ---: | ---: | ---: |
| 歷史循序，`38cfad2` | 450 | 26.017 秒 | 59.248 秒 | 99.320 秒 |
| 最近循序，`1fd3ccb` | 180 | 24.422 秒 | 63.285 秒 | 143.103 秒 |
| 歷史五人 burst，`38cfad2` | 50 | 63.042 秒 | 120.765 秒 | 137.753 秒 |
| 最近五人 burst，`1fd3ccb` | 20 | 87.268 秒 | 171.953 秒 | 196.248 秒 |

最近樣本的 burst p95 較舊樣本高，但回合數、玩家生成內容與程式版本均不同，而且沒有相同輸入的 A/B。這是需再量測的訊號，**不是**回歸的因果證據。20 個 burst 回合的尾端分位數也容易受少數回合影響。

### Camp Sunny：`turn.summary` 獨占時間，200 個一般回合

| 階段 | p50 | p95 | 時間總和占比 |
| --- | ---: | ---: | ---: |
| 端到端 `wall` | 25.998 秒 | 97.561 秒 | 100% |
| `queue_wait` | 0.011 秒 | 56.030 秒 | 15.9% |
| `executor` | 16.181 秒 | 56.922 秒 | 59.0% |
| `narrator` | 7.324 秒 | 15.475 秒 | 23.6% |
| `retrieval` | 0.264 秒 | 0.778 秒 | 1.1% |
| `memory` | 0 秒 | 0.610 秒 | 0.3% |
| `tool` | 0 秒 | 0.078 秒 | <0.1% |
| `other` | 0.027 秒 | 0.043 秒 | 0.1% |

200 回合中有 16 回合的獨占排隊時間超過 1 秒。`turn.summary` 的牆鐘時間從接收／預取時間軸計起，Router harness 的端到端計時邊界略有不同，因此兩張表的分位數不能逐格相減。`tool` 很短也**不代表**工具對模型迴圈沒有成本：Executor 等待模型多次往返的時間歸在 `executor`，不歸在同步工具執行。

該次 `turn.summary` 記錄了 5 條帶 fallback 原因的一般回合：`executor_no_action` 3 條、`internal_error` 2 條。既有 gameplay 報告人工辨識了 4 次玩家可見的泛用未完成回覆。兩者口徑不同，不能互相取代；只數 harness 捕捉的例外會漏掉回合內已處理但未完成的結果。

## 不同劇本的交叉參照

同一個 `1fd3ccb` 基線上的 Dead Boarder 200 回合，循序 p50/p95 是 **43.162/90.086 秒**，五人 burst p50/p95 是 **77.956/170.912 秒**。把 200 個一般回合的 `turn.phases` 與 harness request ID 配對後，Executor 獨占 p50 是 **33.194 秒**、Narrator **8.095 秒**、排隊 p95 **48.650 秒**；階段時間總和占比分別為 **71.0%**、**17.0%**、**10.3%**。這支持「Executor 與並行排隊需要優先量測」的方向，但劇本行動、檢索與模型結果不同，不能拿它估算 Camp Sunny 的優化幅度。

Dead Boarder 的 200 條一般回合摘要中有 **44** 條帶 fallback 原因（`executor_no_action` 27、`unsupported_action` 7、`unresolved_pending_state` 2、`no_scenario_evidence` 1、`internal_error` 7）；既有報告認定 **42/200** 回合有玩家可見泛用降級。這再次說明「沒有未捕捉的例外」不等於玩家行動已完成。該 run 還有需獨立處理的物品交接風險，不能被當作效能 PASS。

## 對目前 `main_v2` 的判斷

從 `1fd3ccb` 到 `0de529b`，`turn_scope`、`turn_fallback`、`prompt_config`、Router 及呈現層都有變更，包括排隊告知與失敗文字。即使沒有修改 `turn.phases` 的核心計時，這些改動仍可能改變回合路徑或玩家等待感受。加上最近實跑開啟了預設關閉的旁白出鎖旗標，因此目前**不能**回答最新預設組態的 p50/p95，不能宣稱 #199–#205 已帶來量化延遲改善，也不能僅憑這次樣本翻轉 `NARRATION_OUTSIDE_MUTATION_LOCK` 的預設值。

下一次實跑應從最新 `origin/main_v2` 的乾淨 worktree 開始，在隔離 DB／劇本庫與相同劇本下保存：Git SHA、實際旗標、每回合 `turn.summary`、正常／續寫分類、五人 burst 標記、fallback 原因、Router 端到端計時。至少維持 180 循序加 20 burst 的 200 回合形狀，並對 `NARRATION_OUTSIDE_MUTATION_LOCK=false` 的預設組態取得自己的基準；若要比較旗標，再用同一版本與相同 workload 獨立跑 `true`。模型輸出有變異，單次未配對實跑不構成因果 A/B。

## 可重現證據與限制

- 主要原始證據：`~/.local/share/line-coc-keeper/pr187-soak/runs/coc-camp-sunny-real-5p-200-4msar288/` 的 `run-manifest.json`、`runtime.log`、`turns.jsonl`、`latency-summary.json`；既有敘述為 `~/Downloads/camp_sunny_real_5p_200_validation.md`。
- Camp Sunny 階段彙總：`python3 scripts/summarize_turn_log.py ~/.local/share/line-coc-keeper/pr187-soak/runs/coc-camp-sunny-real-5p-200-4msar288/runtime.log --json`，得到 200 個一般回合與 70 個續寫。時間占比另由這 200 條摘要的各欄位總和計算。
- Dead Boarder 原始證據：`/private/tmp/coc-dead-boarder-real-5p-200-locktrue-20261005T144731Z/` 的 `run-manifest.json`、`turns.jsonl`、`logs/runtime.jsonl*`、`latency-summary.json`；依 `request_id` 精確選出 200 個正常回合。既有敘述為 `~/Downloads/dead_boarder_real_5p_200_locktrue_validation.md`。
- 舊 Camp Sunny 數字：`~/Downloads/camp_sunny_real_5p_500_validation.md`。其執行時本機 SHA `38cfad2` 已落後遠端，僅作歷史參照。
- 不要對 Dead Boarder 的整個 `logs/` 目錄直接執行摘要器：其中包含不同日誌通道與 setup／smoke，會重複計數。應以正式回合 `request_id` 篩選。
- 原始日誌與玩家對話未加入 repository；本報告只保存聚合數字。**本輪沒有在 `0de529b` 發起新的真實五人 API 實跑。**
