# 測試與 checkout 的 .env 隔離

狀態：已實作。基底：main_v2。分支：bug/test-env-isolation。

## 問題與證據

`bug/test-db-isolation`（PR #104）把五個儲存路徑沙箱化，但沒有處理設定。`app/config.py` 在匯入時呼叫 `load_dotenv()`，因此套件要驗證的每一項設定，都會先被 checkout 旁邊的 `.env` 覆寫。

在合併後的 main_v2（`edd2fd6`）上、於帶有部署 `.env` 的 checkout 執行，有五個測試失敗：

```
tests/test_config_defaults.py::...::test_max_tool_iterations_default_is_five
tests/test_config_defaults.py::...::test_high_iteration_watermark_default_is_four
tests/test_llm_turn_wrapup.py::OpenAIHighIterationWatermarkTests::test_emits_event_when_iterations_reach_the_watermark
tests/test_llm_turn_wrapup.py::AnthropicHighIterationWatermarkTests::...
tests/test_llm_turn_wrapup.py::GeminiHighIterationWatermarkTests::...
```

部署 `.env` 設定 `MAX_TOOL_ITERATIONS=12`、`HIGH_ITERATION_WATERMARK=7`，而測試固定驗證程式碼預設值 5 與 4。這些失敗在重跑時穩定出現，因此不是 PR #104 修掉的順序問題。它們只出現在帶有部署 `.env` 的 checkout——包含跑 bot 的那個 worktree——在空白 checkout 則不會出現，這正是 PR #104 的驗證沒抓到的原因。

已有八個測試模組帶著 `sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))`。`setdefault` 只在該模組先於其他匯入 `dotenv` 的程式被載入時才生效，所以這個 workaround 能否成功取決於收集順序。

## 範圍

在 `tests/conftest.py` 中把 `dotenv.load_dotenv` 換成 no-op。pytest 會先匯入 conftest 再匯入任何測試模組，因此發生在 `app.config` 解析 `from dotenv import load_dotenv` 之前。只替換這一個函式：`scripts/bot_lifecycle.py` 使用 `dotenv_values` 且有測試覆蓋，模組本身必須保持完整。另加防護：若 `app.config` 已被匯入則直接拋錯，而不是靜默地什麼都沒隔離到。

不在範圍內：正式程式碼、那八處已失效的 `setdefault`（那些模組同時為了別的理由 stub `yaml` 與 `app.pdf_loader`），以及開發者 shell 中匯出的環境變數。

## 測試策略

- `dotenv.load_dotenv` 必須是被停用的版本；若有人還原成真正的載入器，套件會紅，而不是無聲地讓部署值決定「預設值」是什麼。
- `config.MAX_TOOL_ITERATIONS` 為 5、`config.HIGH_ITERATION_WATERMARK` 為 4，這兩項正是此部署 `.env` 覆寫的值。
- 將真實的部署 `.env` 複製進 checkout 後執行整套測試。

## 驗證

有部署 `.env` 時：1,146 通過、38 子測試，連續兩次。無 `.env` 時：1,146 通過。保留守門測試但還原那一行修正時，同樣的執行會紅 7 項——原本的 5 項加上 2 項守門。ruff、mypy、compileall 均通過。

## 限制

這只涵蓋 `.env` 檔案。執行 pytest 的 shell 中匯出的變數仍會進入 `app.config`；若未來有設定在 `config.py` 之外被讀取，同類型的洩漏會再次出現。守門測試斷言的是「載入器已停用」，不是「沒有任何設定以其他途徑進入行程」。
