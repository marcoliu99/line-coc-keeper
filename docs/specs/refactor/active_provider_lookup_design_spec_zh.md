# 目前 LLM 供應商只從一個地方取得

[English](active_provider_lookup_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `a68df95`。

## 問題

PR #113 已經把供應商對照表收進 `app/providers/registry.py`（`CONVERSATION_PROVIDERS`，以及排除 `codex` 的 `ANALYSIS_PROVIDERS`）。對照表現在是共用的，但**查詢動作仍然到處複製**：十二個呼叫點各自取一個對照表的別名，再用模組層級的設定值去查。

| 類型 | 呼叫點 | 查法 | 找不到供應商時 |
| --- | --- | --- | --- |
| 對話 | `keeper.py:66`、`agents/executor.py:33/47`、`agents/guard.py:13/24`、`agents/narrator.py:18/34` | `_PROVIDERS[LLM_PROVIDER]` | `KeyError` |
| 對話 | `agents/assistant.py:34` | `keeper._PROVIDERS.get(keeper.LLM_PROVIDER)`，直接取用 `keeper` 的私有別名 | 直接回覆設定錯誤 |
| 分析 | `pdf_ai_repair.py:15/76`、`pregen_extractor.py:26/238`、`scenario_compare.py:18/49`、`scenario_index.py:26/104`、`scenario_intro.py:22/105`、`scene_map.py:47/155` | `_PROVIDERS.get(LLM_PROVIDER)` | `None` → 各自回傳自己的空結果 |
| 分析 | `keeper.py:3614`（摘要） | `ANALYSIS_PROVIDERS.get(ANALYSIS_PROVIDER)` | `None` → 保留舊摘要 |

具體代價有三個：

1. **名稱誤導。** 六個分析模組寫的是 `from app.config import ANALYSIS_PROVIDER as LLM_PROVIDER`，所以 `LLM_PROVIDER` 在這個模組是對話設定，在下一個模組卻是分析設定。光讀模組看不出它用的是哪個供應商。
2. **跨層取用私有名稱。** `assistant.py` 依賴 `keeper._PROVIDERS`，這是 PR #116 SLF001 待清理清單上的其中一項。
3. **測試 patch 的是這些副本。** 約 100 行測試直接 patch `keeper._PROVIDERS`、`executor._PROVIDERS`、`narrator._PROVIDERS`、`repair._PROVIDERS` 或模組的 `LLM_PROVIDER`。光是 `keeper._PROVIDERS["openai"]` 的「保存、替換、還原」這套寫法就重複了約十二次。每多一個呼叫點，測試就多一個要 patch 的東西。

## 決策

查詢由 registry 負責，呼叫端依用途取得供應商：

```python
# app/providers/registry.py
def conversation_provider() -> ConversationProvider | None: ...   # config.LLM_PROVIDER
def analysis_provider() -> AnalysisProvider | None: ...           # config.ANALYSIS_PROVIDER
```

- 兩者都在**呼叫當下**讀 `app.config`（`config.LLM_PROVIDER`），不是匯入時複製的值，所以測試只需要在一個地方設定供應商。
- 名稱不存在時兩者都回傳 `None`，**呼叫端保留原本的後備行為**。目前用 `[...]` 查詢的四個對話模組，改成和 `assistant.py` 一樣明確檢查 `None`，丟出或回覆清楚的設定錯誤，而不是裸的 `KeyError`。這是唯一的行為變更，而且只影響 `.env` 設錯的情況。
- 不屬於查詢的供應商特有分支（`assistant.py:103` 的 `keeper.LLM_PROVIDER == "openai"`）改成直接讀 `config.LLM_PROVIDER`，不再經過 `keeper`。

## 範圍

1. 在 `registry.py` 新增這兩個函式。`ANALYSIS_PROVIDERS` 仍然從 `CONVERSATION_PROVIDERS` 衍生。
2. 替換全部十二個呼叫點。刪除模組層級的 `_PROVIDERS` 別名和 `ANALYSIS_PROVIDER as LLM_PROVIDER` 的匯入寫法。仍需要設定名稱來寫日誌的模組，用它的真名匯入。
3. 測試：新增一個輔助函式 `tests/provider_fakes.py::use_fake_provider(fake, *, role="conversation", name="openai")`，負責 patch `app.config` 並對 registry 的對照表做 `patch.dict`，取代手寫的保存／還原區塊。既有的 patch 機械式地改成用它，測試的斷言不變。
4. 如果 `app/agents/assistant.py` 已經沒有其他私有存取，就把它從 `pyproject.toml` 的 SLF001 待清理清單移除（目前它還有其他私有存取，所以這一行多半會保留）。

## 測試

- 改寫後完整測試套件通過。除了「實作說明」列出的三個測試之外，斷言都沒有改變。
- 新增：名稱不存在時 `conversation_provider()`／`analysis_provider()` 回傳 `None`，且 `analysis_provider()` 永遠不會回傳 `codex`。
- 新增：每個對話入口在 `LLM_PROVIDER` 設錯時都走設定錯誤的路徑，而不是 `KeyError`。
- `git grep -n "_PROVIDERS\s*=" app` 只剩 `registry.py`。

## 實作說明

- 對話階段透過 `registry.require_conversation_provider()` 丟出設定錯誤；KP 助手則透過 `conversation_provider()`，保留它原本直接回覆設定錯誤的方式。
- `app/markitdown_shim.py` 也用了 `ANALYSIS_PROVIDER as LLM_PROVIDER`，現在改用真名。它依供應商建立不同 client 的邏輯不是查詢，所以維持原樣。
- **分析為什麼獨立，已經查證：** `codex_provider` 沒有 `analyze_text`／`analyze_image` adapter。Codex CLI（0.157.1）本身接受 `-i/--image` 和 `--output-schema`，transport 也已經在用後者，所以缺的是 adapter，已經規劃在 `enhancement/codex-analysis-provider` 分支。做好之後，只需要改 `registry.ANALYSIS_PROVIDERS`。
- **不是每個測試都只是單純替換。** 有三個測試的改動不只是換掉 patch 的對象：
  - `test_codex_capabilities.py`：兩個測試原本斷言各模組的別名表（`executor._PROVIDERS is ...`）。現在改成透過 registry，斷言那些別名原本要守住的規則：對話可以選 codex、分析永遠不會，而且每個分析模組都呼叫 `registry.analysis_provider`。
  - `test_turn_consistency_handoff.py::test_supervisor_preserves_failed_executor_private_outputs` 原本給 executor 和 narrator 不同的假供應商。只剩一張表之後，改由單一假供應商依呼叫順序分派（executor 先）。
- `app/agents/assistant.py` 仍留在 SLF001 待清理清單上：取用供應商的部分已經移除，但還有其他 11 處 `keeper._*` 存取。

## 限制

- 供應商的*能力*判斷（`supports_dynamic_tools`、`supports_response_stage`）已經在 registry 裡，這次不動。
- 審查決定的順序：完整實作（範圍四個步驟全做），排在 `bug/major-wound-con-check-gate` 和 `refactor/combat-start-in-combat-module` 合併之後。
- 最大的成本在測試的改動量，不在正式程式碼。如果判斷現在不值得，可以只做步驟 1–2，把舊別名留成薄薄的已棄用包裝；但那正是這份規格想要結束的半套狀態。
