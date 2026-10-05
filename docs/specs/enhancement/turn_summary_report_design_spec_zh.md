# 用回合摘要行統計沒有完成的回合

[English](turn_summary_report_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `f03a110`。

## 問題

Dead Boarder 五人驗證報告寫「Per-turn errors: 0」，但 102 回合中有 15 回合以「尚未完整處理」的通用回覆結束，其中 4 回合是「系統發生內部錯誤」。計數沒有算錯：Executor 失敗的回合是在回合內部被接住、當作回覆送出，所以沒有例外傳到只計算例外的 harness。能顯示這件事的數字（`fallback=`、`queue_wait_ms=`、各階段時間）自從有回合摘要行（`docs/specs/enhancement/turn_summary_log_design_spec_zh.md`）後，每個回合都寫進一般文字日誌，只是沒有東西在讀它。

驗證 harness 本身不在這個 repo，所以它自己的「errors」欄位在這裡改不了。

## 變更

`scripts/summarize_turn_log.py` 讀取日誌檔（或目錄下所有檔案）裡的 `turn.summary` 行（logger `app.turn`），輸出：

- 玩家等一個行動等多久（wall 的 p50／p90／p95／p99／max、排隊等待、各階段的中位數），只算一般回合，避免把 25 秒的擲骰續寫當成玩家的等待；
- 有多少行以 `fallback=` 原因結束，總數與依原因的分布，並註明 `internal_error` 對只計算例外的工具是看不見的；
- 路由、由狀態直接回答的回合，以及最慢的五個回合與其 id。

`--json` 輸出同樣的數字給其他工具（harness 可以讀它，不必再計算例外），`--since` 丟掉較早的行。沒有讀到任何一行時結束碼為 2，通常代表日誌沒有包含 logger `app.turn` 或 `LOG_TEXT_ENABLED` 關閉，所以空日誌是錯誤，不是乾淨的報告。

唯讀、不依賴 `LOG_ENABLED`，而且看不到玩家文字，因為那一行本來就沒有。

兩種日誌格式都讀：預設的 `LOG_FORMAT=json`（訊息包在帶 `timestamp` 與 `logger` 的物件裡）與 `LOG_FORMAT=text`（`<timestamp> <LEVEL> app.turn <message> <context>`）。只有 logger 是 `app.turn` 且訊息以標記開頭的行才算，所以別的 logger 剛好含有 `turn.summary` 的文字（例如被記錄下來的 Keeper 回覆）無法憑空造出一個回合。

## 驗證

`tests/test_summarize_turn_log.py` 解析的是 runtime 實際寫出的那一行，經由真正的 logger 與兩種真正的格式器（`StructuredFormatter`、`TextFormatter`），所以那一行或外層格式一變，測試就會失敗；別的 logger 的相似行會被拒絕；另外涵蓋日誌前綴與雜訊、依原因的計數、百分位只算一般回合、目錄與 `--json`、`--since`、空的或不存在的日誌。還沒有用真實部署的日誌跑過。
