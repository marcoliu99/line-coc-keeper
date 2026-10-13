# 被拒絕的劇本查詢不算工具失敗

[English](search_limit_is_not_a_tool_failure_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `06d5d9a`。來自 2026-10-13 的 Scritch-Scratch 200 輪（第 11、32 輪）。

## 問題

「在小屋內逐一查看可進入的房間」讓執行器一直查劇本，直到這回合的查詢額度（`SCENARIO_SEARCH_MAX_PER_TURN`，5 次）拒絕了下一次查詢。執行器於是把這回合交回 incomplete，並說明缺什麼（「請聚焦一個房間或一項具體調查」）。被拒絕的查詢被算成工具失敗，這回合就被歸為 `tool_failure`，不重試，玩家讀到「處理這個行動的工具失敗了；已完成的變更會保留，請稍後重試或換個做法」，彷彿有東西壞了。200 輪裡有兩輪這樣收場，更早的一次測試也有同樣情形。

## 變更

查詢額度拒絕一次查詢時，工具閘道在這回合做記號（`check_status["scenario_searches_refused"]` (a count)）。`turn_fallback.classify` 之後不把這麼多次失敗的 `search_scenario` 算進失敗的工具；因自身原因失敗的查詢仍然算數。於是放棄的執行器歸為 `executor_no_action`：備援本來就會重試一次（帶著第一次查到的段落），仍無法解決時則用場景裡具體的選項回覆玩家。其他工具失敗、或沒有被拒絕卻失敗的查詢，仍是 `tool_failure`。

## 驗證

`tests/test_turn_fallback.py`：唯一失敗的呼叫是被拒絕的查詢時歸為 `executor_no_action`；沒有記號、或另有工具失敗時仍是 `tool_failure`。`tests/test_turn_phases.py`：閘道在第一次被拒絕的查詢時做記號，之前不做。
