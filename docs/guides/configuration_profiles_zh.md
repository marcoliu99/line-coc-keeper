# 設定：每個開關對玩家的影響

[English](configuration_profiles.md)

設定在機器人啟動時從環境變數讀取（`.env`，見 `.env.example`）。這一頁列出會改變玩家所見或等待時間的設定，預設值取自 `app/config.py`。**目前沒有任何設定被建議要偏離預設值**：偏離需要量測，而延遲工作要求的真實執行量測還沒有做（`docs/validation/camp_sunny_latency_before_after.md`）。

布林設定如果寫了無法辨認的值（例如 `ture`），會保留預設值並在啟動時回報；接受 `1/true/yes/on` 與 `0/false/no/off`。

## 延遲與順序

| 設定 | 預設 | 對玩家的影響 | 狀態 |
| --- | --- | --- | --- |
| `NARRATION_OUTSIDE_MUTATION_LOCK` | `false` | 這個回合還在旁白時，下一位玩家的行動就可以開始，不必等旁白結束；順序由旁白鎖保持。 | 估計可把持鎖時間中位數從約 21.7 秒降到約 15.7 秒（`docs/specs/enhancement/measured_turn_latency_priorities_design_spec_zh.md`，WP3.5）。預設關閉，因為鎖處理錯誤會讓頻道卡到重啟；**尚未在真實五人執行中驗證**。劇本證據可能寫明機制的回合維持舊行為。 |
| `SCENARIO_RAG_ENABLED` | `false` | `false`：整份劇本（最多 `MAX_SCENARIO_CHARS`）放進提示。`true`：Keeper 用 `search_scenario` 檢索劇本，長劇本放得下，但每次檢索都是多一次模型往返。 | 延遲分析（`search_scenario` 占工具呼叫的 79%）針對的是 `true`。 |
| `RETRIEVAL_REUSE_FOR_FOLLOWUPS` | `true` | 擲骰後的續擲沿用造成它的那個行動的劇本證據，不重新搜尋（前提是它依賴的東西都沒變）。 | 已有離線測試；對真實劇本回答品質的影響未量測。 |
| `SCENARIO_SEARCH_MAX_PER_TURN` | `5` | 單一回合 `search_scenario` 的呼叫上限。 | |
| `SCENARIO_CONTEXT_TOKEN_CEILING` | `32000` | 劇本搜尋用來計算預算的提示大小上限：搜尋最多能用「這個值減去提示已佔的大小、輸出保留量與安全餘量」。設太低，搜尋會被擠到沒有預算，守密人拿不到劇本依據，玩家就收到通用的「無法繼續」回覆。 | 工具 schema 與靜態提示約 20k tokens（估計，沒有實測）。請設成你的模型實際的上下文視窗減去餘量；它是部署上限，不是對視窗大小的宣稱。 |
| `SCENARIO_CONTEXT_WINDOW_TOKENS` | `128000` | 硬上限。搜尋最多只能用「這個值減去提示與輸出保留量」之後剩下的，即使有下限也一樣，所以請求不會超過模型的視窗。 | 視窗較小的模型請調低；`rag.retrieval.budget` 會回報 `budget_capped_by_window`。 |
| `SCENARIO_RETRIEVAL_TOKEN_BUDGET` / `SCENARIO_RETRIEVAL_MIN_TOKENS` | `6000` / `3000` | 一次劇本搜尋最多、最少能用多少。下限避免擁擠的提示把搜尋壓到零；`0` 允許壓到零。記錄在 `rag.retrieval.budget`，用到下限時帶 `budget_floor_applied`。 | 下限只是後盾：常常看到它被套用，代表上限設太低。 |
| `TURN_FALLBACK_RECOVERY_ENABLED` | `true` | 原本會以通用「無法繼續」回覆收場的回合，先多搜尋一次、多決定一次。 | 最多多一次搜尋與一次模型呼叫，只發生在這類回合。 |
| `LLM_TURN_DEADLINE_SECONDS` | `180` | 模型工作的整回合期限；超過時回合以明確訊息結束，已提交的變更保留。 | |
| `MAX_TOOL_ITERATIONS` / `MAX_TOOLS_PER_TURN` | `5` / `4` | Executor 工具迴圈的上限。 | |

## 安全與告知玩家的內容

| 設定 | 預設 | 對玩家的影響 |
| --- | --- | --- |
| `GUARD_ENABLED` | `true` | 驗證失敗的回覆會由模型修復（最多兩次），而不是原樣送出。驗證本身一律會執行。 |
| `SPOILER_PROTECTION_ENABLED` | `true` | 守密人專屬的劇本內容不會出現在公開回覆。關閉後旁白可能揭露它。 |
| `PRIVACY_ISOLATION_ENABLED` | `true` | 私人資訊與秘密目標只給擁有者。關閉時啟動會有明顯警告。 |
| `DEBUG_SHOW_INTERNAL_IDS` | `false` | 在回覆中顯示內部識別碼（例如 `check_id`），供除錯。原始的結果等級名稱永遠不會顯示。 |
| `CHARACTER_DISPLAY_ALIASES` | 空 | JSON 物件，例如 `{"The Tough Guy": "硬漢"}`。機器人送到 Discord 的每一則訊息（旁白、系統訊息如「…的背包已確認…」，與指令回覆）送出時都會把登記的角色名寫成別名，同一個角色不會變成兩個名字。存檔、遊玩紀錄、回合日誌與 id 仍用登記的名字。不是「非空白文字對非空白文字的 JSON 物件」的值（含空白的鍵或值）會被忽略並在啟動時回報。 |
| `SCENARIO_LIFECYCLE_KP_ONLY` | `false` | 只有目前的 KP Assistant 可以使用劇本選擇按鈕（新上傳或修正重傳）與 `/coc scenario` 指令（use、import、merge、reparse、cancel）。**上傳劇本檔案本身不受限制**：任何玩家都可以上傳，而且一個對話的第一次上傳會立即套用。角色還在安排時請保持關閉。 |

## 看見玩家等了多久

| 設定 | 預設 | 影響 |
| --- | --- | --- |
| `LOG_TEXT_ENABLED` | `true` | 純文字日誌，包含每個玩家回合一行 `turn.summary`（只有時間與識別碼，沒有玩家文字）。 |
| `LOCK_HELD_WARNING_SECONDS` | `LLM_TURN_DEADLINE_SECONDS` + 60 | 對話鎖、Keeper turn 鎖或旁白鎖被持有超過這個時間，會在 logger `app.locks` 以 WARNING 報告一次（`lock.held_too_long`），最後釋放時再報告一次；它不會釋放任何東西。`0` 關閉。 |
| `LOG_ENABLED` | `false` | 結構化事件，帶計時器、計數器與 JSON 內容（`turn.phases`、`llm.turn` 等）。每次呼叫有額外開銷，用來調查問題，不建議預設開啟。 |

## 測了什麼

上面的布林設定，兩種值都有測試明確設定過；完整測試套件以預設值執行。沒有任何 CI 工作會以非預設旗標跑整個測試套件，所以非預設的組合只涵蓋到它自己的測試範圍。
