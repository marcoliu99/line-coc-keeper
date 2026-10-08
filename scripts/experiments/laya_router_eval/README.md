# Laya 分流實驗（影子模式）

目的：確認 [Laya](https://github.com/receptron/laya) 能不能在 Executor 之前就判斷出「這句玩家行動不需要任何機制」，讓那些回合直接交給 Narrator，省下 Executor 約 20 秒。

**影子模式只記錄，不改變遊戲**：每個玩家回合，bot 照原本流程跑 Executor 和 Narrator；同時把玩家那句話送給本機的 Laya 小服務，把它的判斷寫進 runtime log（事件 `laya.shadow`）。之後用 `eval.mjs` 拿同一回合 Executor 實際呼叫的工具當正確答案，算準確率。Laya 掛掉或太慢只會少一筆資料，不影響回合。

## 執行步驟（Codex CLI 照做即可）

需求：Node.js 20 以上；第一次執行會從 Hugging Face 下載約 1.7 GB 的模型，載入後約佔 2 GB 記憶體。Apple 晶片直接用 CPU 跑即可，不需要 MLX。

1. 安裝：
   ```sh
   cd scripts/experiments/laya_router_eval
   npm install
   ```
2. 另開一個終端機啟動 Laya 小服務，等它印出 `laya ready ...`：
   ```sh
   node scripts/experiments/laya_router_eval/server.mjs --port 8765
   ```
   已有本機的 ONNX 匯出就加 `--model-dir 路徑`。
3. 在 bot 的 `.env` 打開結構化 log 和影子模式，其他設定（模型、推理強度）都不要動：
   ```
   LOG_ENABLED=true
   LOG_FORMAT=json
   LOG_FILE=data/evaluations/<run-id>-runtime.jsonl
   LAYA_SHADOW_URL=http://127.0.0.1:8765/route
   ```
4. 用平常的 soak 測試流程跑 200 輪真實遊戲（劇本、玩家行動照舊）。跑完確認 runtime log 裡有 `"event": "laya.shadow"` 而且 `"status": "success"`：
   ```sh
   grep -c '"laya.shadow"' data/evaluations/<run-id>-runtime.jsonl
   grep -c '"laya.shadow".*"status": *"error"' data/evaluations/<run-id>-runtime.jsonl
   ```
   error 很多代表小服務沒開或太慢（預設 3 秒逾時，可用 `LAYA_SHADOW_TIMEOUT` 調整）。
5. 產生報告：
   ```sh
   node scripts/experiments/laya_router_eval/eval.mjs \
     --runtime data/evaluations/<run-id>-runtime.jsonl --limit 200 \
     --out data/evaluations/<run-id>-laya
   ```
   輸出 `report.md`（給人看）和 `results.jsonl`（每回合的判斷與實際工具）。
6. 把 `report.md`、`results.jsonl` 和 runtime log 交回來分析。

## 報告怎麼看

- **誤放**：Laya 判成「純敘事」而且信心達門檻，但 Executor 那回合其實呼叫了會改變狀態的工具（擲骰、戰鬥、物品、線索……）。真的走捷徑的話，這些回合會漏掉該擲的骰。誤放要接近 0，那個門檻才能用。
- **走捷徑佔全部**：在那個門檻下，有多少回合可以跳過 Executor。乘上 Executor 的時間（預設 20 秒，可用 `--executor-seconds` 改）就是估計省下的時間。
- 被擋下的回合（例如「請先完成檢定」「戰鬥暫停中」），Executor 沒呼叫工具是因為別的事卡住，不能拿來證明那句話不需要機制，預設不計分（`--include-blocked` 可納入）。
- 「判成 narrate 但實際需要 Executor」清單列出最危險的錯誤，可以直接看是哪些句子。

## 用舊測試記錄先試（可選）

有 `turns.jsonl`（含 `request_id`、`input`）和對應的 tool-events 或 runtime log 的舊 soak 測試，不用重跑遊戲也能先算一次（這時 Laya 在本機直接跑）：

```sh
node scripts/experiments/laya_router_eval/eval.mjs \
  --turns A-turns.jsonl --tools A-tool-events.jsonl \
  --turns B-turns.jsonl --tools B-runtime.jsonl --limit 200
```

加 `--dry-run` 只檢查資料、不載入模型。
