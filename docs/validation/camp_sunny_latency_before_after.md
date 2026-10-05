# Camp Sunny 修正：延遲前後比較

**沒有「後」的數據。** 這份報告存在是因為規格要求它；它如實記錄基準線、這次改了什麼、預期會影響什麼，以及怎麼量。

## 基準線（來自規格 §7.1，原始驗證報告）

| 指標 | 數值 |
| --- | --- |
| 循序延遲 p50 / p95 / p99 / max | 26.017 / 59.248 / 81.769 / 99.320 秒 |
| 五人同時發言 p50 / p95 / p99 / max | 63.042 / 120.765 / 136.530 / 137.753 秒 |
| Router 佇列最長等待 | 106.471 秒；16 次超過 60 秒 |
| 劇本檢索 | 935 次 / 500 個一般回合（每回合 1.87 次） |

## 這次改了什麼（[#184](https://github.com/marcoliu99/line-coc-keeper/pull/184)）

1. **階段計時**：每個回合、每次擲骰接續、每次記憶維護回報 `turn.phases`——`queue_wait`、`initial_retrieval`、`executor_llm`、`tool_execution`、`recovery_retrieval`、`continuation_processing`、`narrator_llm`、`memory_search`、`memory_write`、`embedding`、`other`；每階段同時給合併後的區間與獨占時間，加總等於牆鐘時間。
2. **擲骰接續沿用其行動的證據**：一次擲骰加接續從 2 次主動劇本搜尋降為 1 次（條件：沒過期、之後沒有別的回合搜尋過、劇本／章節範圍／摘要／記憶／時間線／戰鬥狀態／角色都沒變）。
3. **每回合劇本搜尋工具最多 5 次。**
4. 相關：[#180](https://github.com/marcoliu99/line-coc-keeper/pull/180) 的復原**最多多一次**搜尋與一次 Executor 呼叫（只在原本會變成通用回覆的回合）；[#179](https://github.com/marcoliu99/line-coc-keeper/pull/179) 的相鄰 chunk 只增加回傳文字長度，不增加呼叫次數；[#181](https://github.com/marcoliu99/line-coc-keeper/pull/181) 的義務在敘事後可能多一次工具呼叫（只在規則被觸發時）；[#182](https://github.com/marcoliu99/line-coc-keeper/pull/182) 的記憶分段使 embedding 呼叫變成每個 part 一批。這些都可能增加延遲，不能只看第 2、3 點。

## 沒有做的

* **沒有縮小鎖範圍**（規格 E5）：沒有量測顯示鎖占了什麼時間，縮小一個保護狀態提交的序列化只是猜測。
* **沒有每回合相同查詢快取**（E2）：重複的查詢本來就回「已提供過的片段」。

## 驗收門檻（§7.4）

| 條件 | 狀態 |
| --- | --- |
| 沒有崩潰、沒有擁有者污染、沒有 pending 死結 | 未評估（離線測試只涵蓋邏輯） |
| 同時發言回覆不超過 120 秒 | **未量測** |
| 循序 p50 改善 ≥ 20%（< 20.8 秒）或有證據顯示剩餘延遲來自 provider | **未量測** |
| 同時發言 p95 改善 ≥ 20%（< 96.6 秒）或有證據顯示剩餘延遲來自 provider | **未量測** |
| 快取／沿用沒有造成正確性退步 | 離線測試通過；真實執行未驗證 |

## 怎麼量

第一次五人真實執行後，依 `turn_id` 讀 `turn.phases`（`exclusive_ms` 加總等於 `wall_ms`，`overlap_ms` 說明重複覆蓋了多少），先看 `queue_wait`、`executor_llm`、`narrator_llm`、`initial_retrieval` 各占多少，再決定要調什麼。`rag.followup_grounding` 的 `reused` 與 `executor.scenario_search.limit_exceeded` 可以直接回答第 2、3 點的實際影響。
