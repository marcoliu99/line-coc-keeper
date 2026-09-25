# LLM 限流與長回合延遲改善規格

**狀態：3.1／3.2 已實作（簡化版），3.3／3.4 維持待討論**。原本 3.1
提案的「baseline → 分視窗自動調整 cap」機制已決定不做——對單一開發者
維護、流量不大的 Discord bot 來說，建自動調整機制跟一整套量測指標的
投入產出比不划算。改採「先做 3.2、3.1 用手動固定 cap」的簡化版本，
細節見 3.1／3.2 與第 7 節。

## Changeset Tracking
- **main_v2 start**: origin/main_v2:80f245fc6591a021b40dbbedb9d44d41e6b9ec9f
- **implementation end**: enhancement/llm-rate-limit-and-turn-latency（3.1/3.2 only）— ruff/mypy/compileall/pytest all green (full suite)

## 1. 問題與目標

`profile-async2.log` 顯示 LLM 等待與同對話序列化是端到端延遲的主要來源：709 次 LLM request 的 P50 約 3.7 秒、P95 約 10.8 秒、最長約 30.7 秒；322 個 LLM turn 的 P95 約 22.9 秒。759 次 attempt 中有 58 次失敗，其中 54 次被記為 `RateLimitError`、4 次為 `BadRequestError`。同一份 log 中，lock wait P95 約 21 秒、最長約 56 秒；約 15.8% 的 turn 使用至少 4 次 iteration。

這些數據是整體樣本，不直接證明 54 次 429 都由本程序併發造成；實作前應按 provider/model、時間窗、錯誤碼與 retry 結果再切分。request 的總耗時也包含排隊與多個 LLM iteration，不能把它等同單次 API latency。

目標：

1. 降低可避免的 429、重試延遲及跨對話同時打 API 的尖峰。
2. 把 rate-limit admission wait、retry backoff、provider latency、turn iteration 與 conversation lock wait 分開量測。
3. 找出高 iteration turn 中可合併或安全併行的工具工作，縮短串行模型往返。
4. 保持同一對話的事件順序、狀態正確性及工具副作用語意。

## 2. 現有行為

- `app/providers/retry.py` 已集中處理 timeout、429、5xx 與 transport 類錯誤重試；預設最多 3 次 retry，延遲為 1、2、4 秒，沒有 jitter 或明確處理 Retry-After。
- HTTP 4xx（包括 Bad Request）依分類不重試。這是正確方向；要補足的是記錄安全的 provider error code／request ID，讓 400 能歸因而不記錄 prompt 或個資。
- Provider request 目前沒有共用的併發 admission gate。不同 conversation 可同時建立 provider request。
- `conversation_lock` 刻意涵蓋完整處理流程，確保同一對話的歷史與多步遊戲狀態變更有序。既有 queue acknowledgement 只改善等待中的使用者回饋，不會縮短 lock 持有時間。
- `MAX_TOOL_ITERATIONS` 已由前一輪工作降為 5，`HIGH_ITERATION_WATERMARK` 為 4。因此新 log 的 iteration 分布應先確認是否仍在此設定下收集；舊數據的 15.8% 不宜當作調整後現況。

## 3. 提案方案

### 3.1 OpenAI admission gate（簡化版：手動固定 cap，不做自動調整）

**決定（取代原本的分階段自動調整提案）**：這個專案是單一開發者維護、
流量不大的 Discord bot，還沒有證據顯示 429 真的是這個 process 自己
併發造成的（也可能是帳號整體 quota/TPM 限制，加 process-local 併發
上限對這種情況沒有幫助）。原提案要求的「baseline 24h/500 次 → 逐視窗
按 429 比率／admission wait 門檻自動調整 cap」需要額外量測、記錄與
人工 review 循環，投入產出比對這個規模不划算，決定不做。

改用最簡單的版本：

1. 在 OpenAI request boundary 加入可設定的 async semaphore，限制同一
   bot process 同時進行的 API attempts。semaphore 只包住單次網路
   attempt，不包含工具執行及 backoff；429 後先釋放 slot，等待重試時間
   後再重新排隊，避免等待中的 request 佔住名額或阻塞 Anthropic／
   Gemini。
2. Cap 值透過環境變數設定，**憑觀察手動決定初始值**（看一段時間的既有
   log，抓同時進行的 OpenAI request 數的實際尖峰，抓不到具體證據就先
   用一個保守值，例如 3）——不做自動 baseline 收集流程。
3. 上線後**手動觀察**既有的 structured log（429 比率、turn latency）
   一段時間（例如一兩週），覺得太緊或太鬆再手動調整環境變數，不建自動
   調整規則或分視窗評估機制。

第一階段不做 token-per-minute 排程、其他 provider 限流或跨程序共享
限流。

### 3.2 Retry 與錯誤分類（優先做——風險低、效益直接，不依賴 3.1 的決定）

- 保留 429、暫時性 5xx、timeout／網路錯誤可重試；Bad Request、認證錯誤及其他永久 4xx 不重試。
- 指數退避加入 full jitter，降低多個 request 同步重試造成的再碰撞。
- 若 SDK 暴露 Retry-After 或等價的 rate-limit reset 資訊，遵守伺服器建議的等待時間；缺少此資訊時才用本地退避。
- 對每種 SDK 以安全欄位提取 status、provider error code、request ID、retry-after 秒數。無法識別的欄位留空，不從錯誤訊息猜測。
- 維持現有 retry budget 與 timeout retry 上限；任何 budget 調整都須以 latency tail 與成功率一起評估。

### 3.3 長 turn 與工具呼叫數

先用 structured logs 抽出 iteration ≥ 4 的代表性 turn，依序列出每輪模型請求、tool name、工具耗時、是否改變狀態，以及下一個呼叫是否依賴前一個結果。遭遇建立與初始 NPC 設定的 macro tool 已在另一分支/spec 討論，不在本 spec 重複設計：`enhancement/macro-combat-initialization-tool`，文件 `docs/specs/enhancement-macro-combat-initialization-tool.md`。本工作只引用它作為可能減少往返的既有候選；是否實作依該 spec 的 COC7e 正確性審查與遊戲測試決定。

在缺少依賴資訊前，不平行執行同一 turn 的 tool calls。後續若要併行，只允許明確標註為 read-only 且互相獨立的工具；會骰骰、改角色／戰鬥狀態、寫 log 或依前一步結果決策的工作仍依序執行。不要為降低 iteration 而降低驗證或跳過必要工具。

可先從 log/trace 改善開始，不改 `MAX_TOOL_ITERATIONS=5`。工具 schema/prompt 縮減另依高 iteration trace 證據評估。

### 3.4 同對話排隊

保留 `conversation_lock` 與目前 queue acknowledgement。這份工作不採用縮小鎖範圍或讓同一對話的 LLM turn 重疊，避免舊 history snapshot、工具結果過期或劇情狀態交錯。此方案降低的是跨對話 provider 壓力及單 turn 內可避免的模型往返，並不承諾消除同對話排隊；長 turn 實際縮短後，隊列自然可能變短。

## 4. 流程

```text
conversation lock
  -> provider admission wait (metric)
  -> one API attempt (metric)
      -> 429/transient: release slot -> jitter / Retry-After -> admission wait again
      -> permanent 4xx: classify and surface without retry
  -> execute tools (依依賴順序)
  -> next model iteration, if needed
  -> reply and release conversation lock
```

## 5. 範圍與非目標

包含：provider 層限流、retry policy／錯誤 telemetry、turn/tool trace 的可診斷性，以及經 log 證實可安全縮短的工具流程。

不包含：更換模型或 provider、修改遊戲規則、並行同對話 turn、盲目平行化有副作用的工具、無依據地更改推理等級、跨程序分散式限流，以及承諾 API 服務端生成時間會下降。

不需要資料庫 schema 變更；限流設定走既有環境變數設定方式。若 telemetry 需要新增 event 欄位，維持向後相容並避免敏感資料。

## 6. 驗證與成功指標

新增單元測試覆蓋 semaphore 上限、取消時釋放 slot、429 後釋放並重新排隊、Retry-After、jitter 範圍、Bad Request 不重試、錯誤 request ID 安全記錄，以及 tool parallelism 的依賴／副作用規則。加入模擬多 conversation 同時請求的測試，確認限流不會破壞同 conversation 順序。

**不需要**自動化的分視窗前後比較機制（已隨 3.1 簡化決定移除）。上線後
用既有的 structured log 手動看一下 429 比率跟 turn latency 有沒有明顯
改善／變差即可，不需要建立正式的部署前後比較報表。

## 7. 待討論決策

1. **[已解決]** 第一階段以 OpenAI 為主；其他 provider 暫不加 admission gate，除非 telemetry 顯示它們也有明顯限流。
2. **[已解決，2026-09-25]** 原提案「baseline → cap=4 試行 → 按 1%／2%
   429 與 admission wait 門檻逐窗口自動調整 → 比較 turn P95／throughput
   決定保留或回退」的整套自動調整機制**決定不做**——理由見 3.1：單一
   開發者維護、流量不大的專案，這套機制的建置與觀察成本划不來。改用
   3.1 描述的簡化版：手動決定初始 cap、手動看 log 決定要不要調整。
3. Macro tool 已由 `enhancement/macro-combat-initialization-tool` 分支/spec 承接，不是本 spec 新增的設計決策；是否排入實作依該 spec review。
4. 本階段不做跨程序共享限流；若未來確認多個 bot process 共用同一 OpenAI quota 並造成超額，再另開設計。

## 8. 實作前檢查

- 確認 `profile-async2.log` 的 OpenAI 429 按 model/status/error code 的分布，並確認 log 收集時的 `MAX_TOOL_ITERATIONS` 設定。此檢查可幫助手動決定 3.1 的初始 cap 值該抓多少（例如取觀察到的同時 attempt 尖峰，或抓不到就先保守用 3）。
- 確認使用的 SDK 版本如何公開 Retry-After、rate-limit reset、provider request ID 與結構化 error code。
- 抽樣檢視高 iteration turn，避免把互相依賴或有序狀態變更的工具錯列為可平行工作。
- 規格獲確認後，才依決定的階段開始實作與測試。3.2（retry jitter／
  Retry-After）不依賴 3.1 的 cap 值，可以先實作。
