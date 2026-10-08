# Laya 影子分流：在 Executor 旁記錄快速分流器的判斷

## 問題

2026-10-09 的《鬼屋》100 回合重跑，每回合中位數 23.6 秒，其中 Executor 19.8 秒、Narrator 4.6 秒。幾乎每句玩家行動都走 Executor：`intent_router` 只把單純的確認與括號裡的場外話交給 Narrator。之前試過調低 Executor 的推理強度，工具判斷變得不可靠；敘事也維持 medium。

## 變更

[Laya](https://github.com/receptron/laya) 對一個狀態用一次編碼器推論回答型別化的問題（單選、評分、是非），CPU 熱機約 140 毫秒。在真正依它分流之前，先在真實遊戲中量它的準確率：

- `app/agents/laya_shadow.py`：設定 `LAYA_SHADOW_URL` 時，每句玩家行動（`player_action`，不含 KP 助手）會在 Executor 旁同時以 `{玩家行動, 角色, 戰鬥中}` 送給本機的 Laya 小服務，回答（分流、機率、「需要機制」是非題、延遲、那句話）以 `laya.shadow` 事件記在該回合的 id 下。回合從不等它、也不讀它；小服務掛掉或太慢就記 `status: error`。預設關閉。
- `scripts/experiments/laya_router_eval/`：`server.mjs`（小服務）、`questions.mjs`（共用的問題）、`eval.mjs`（拿同一回合 Executor 實際呼叫的工具評分：除了查詢以外有呼叫任何工具就算需要 Executor；被擋下的回合不計分）與 README 執行步驟。

## 未做

依判斷實際分流。需要先在真實測試中找到「走了捷徑但 Executor 其實有動作」接近零的門檻，這個實驗就是為此。

## 測試

`tests/test_laya_shadow.py`：預設關閉、判斷連同那句話被記錄、小服務掛掉時記為一次失敗。
