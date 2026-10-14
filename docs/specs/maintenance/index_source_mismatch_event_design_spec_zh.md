# 選擇變體索引時，記錄「函式庫來源不是這場遊戲的來源」

[English](index_source_mismatch_event_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c6512c4`。來自 2026-10-14 對 `main_v2` 的稽核（RAG 與劇本雜湊）。

## 問題

`scenario_templates.index_for_state` 用函式庫目前的來源（manifest 的 `content_hash`）建立中文變體索引，而 `state.scenario_text` 與原文索引屬於遊戲開始時的來源（`state.active_scenario_source_hash`）。函式庫條目重新解析之後，兩邊可能回答不同版本的內容，或變體悄悄讓位給原文（`variant_fallback="invalid_or_stale"`）。完全沒有任何紀錄顯示遊戲的來源和函式庫的來源已經分開。

## 變更

遊戲的雜湊和函式庫的不同時，`_note_source_mismatch` 會記一筆 `scenario.index.source_mismatch` WARNING（劇本 id、兩個雜湊的前 12 字元、雜湊過的群組 id）。它在讀取變體（快取未命中）與變體因無效或過期讓位時執行。只記錄：不改變檢索、退回或任何回覆，所以遊戲和以前完全一樣。沒有遊戲雜湊（舊狀態）、函式庫條目讀不到、或雜湊相同時，都不記。

## 驗證

`tests/test_scenario_template_v3.py`：遊戲從另一個來源開始時，仍拿到變體索引，並記一筆含兩個雜湊的事件；雜湊相同時不記；函式庫條目讀不到時不當成來源不一致。第一個在沒有這項變更時會失敗。
