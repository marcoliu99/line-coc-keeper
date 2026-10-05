# `/coc` 系統子指令改為處理函式的對照表

[English](system_command_table_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `e4c6044`。

## 問題

`app/commands/handlers/system.py` 的 `handle_system_command` 有 641 行：一長串 `if sub == ...`，每個子指令的內容都寫在裡面，其中 `scenario` 分支 267 行，本身又是依動作字詞的 if 鏈。想知道 `/coc autoroll` 做什麼，得先捲過 `/coc scenario`。見 `docs/architecture/main_v2_architecture_review_zh.md`（F10）。

## 限制：指令行為不變

這些是冷路徑的管理指令，目標是可讀性，不是行為。每個子指令回覆相同的文字、取相同的鎖、呼叫相同的服務。mutation-admission 裝飾器、在 `newgame`、`end`、`rollback`、`era` 與會取代劇本的 scenario 動作之前的替換守門檢查、以及未知指令的回覆都不變。

## 結構

- `handle_system_command` 保留簽章與裝飾器，先做替換守門，再到 `_COMMANDS` 查子指令，以一個不可變的 `_Call`（帶著它的參數）呼叫處理函式。未知子指令得到同樣的「未知的系統指令」回覆。
- 原本每個 `if sub == ...` 分支變成一個處理函式 `async def _<name>_command(call)`，內容原封搬移，只在開頭多一行把它用到的參數綁到內容原本使用的區域變數名稱。`checkpoint`／`checkpoints`／`rollback` 共用一個處理函式，`digest`／`digests` 也是，與原本的 `sub in (...)` 判斷一致。
- `/coc scenario` 到 `_SCENARIO_ACTIONS` 查動作；每個動作（`source`、`template`、`cards`、`import`、`merge`、`list`、`reparse`、`cancel`、`use`、`clean`）是各自的處理函式，接收 `_Call` 與該指令只載入一次的 state。未知動作仍然回覆用法行。
- 新增子指令只要寫一個處理函式並加一列對照。

## 驗證

`tests/test_system_command_table.py`：router 送到這裡的每個子指令都有處理函式、router 視為長操作的 scenario 動作都有處理函式、別名共用處理函式、入口維持只做守門與分派（少於 45 行）、未知子指令與未知 scenario 動作的回覆不變、分派把呼叫者的參數原樣帶給處理函式。既有的指令、開場、劇本與 sudo 測試不改動就通過。`ruff check .`、`mypy app` 與完整 `pytest` 通過。
