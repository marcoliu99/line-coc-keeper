# A refused scenario search is not a tool failure

[繁體中文](search_limit_is_not_a_tool_failure_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `06d5d9a`. Found in the 2026-10-13 Scritch-Scratch 200-turn run (turns 11 and 32).

## Problem

「在小屋內逐一查看可進入的房間」 sent the Executor searching the scenario until the turn's allowance (`SCENARIO_SEARCH_MAX_PER_TURN`, 5) refused the next search. The Executor then left the turn incomplete, naming what was missing (「請聚焦一個房間或一項具體調查」). The refused search counted as a failed tool, so the turn was classified `tool_failure`, was not retried, and the player read 「處理這個行動的工具失敗了；已完成的變更會保留，請稍後重試或換個做法」, as if something had broken. Two turns in 200 ended this way; a third, in an earlier run, the same.

## Change

When the allowance refuses a search, the tool gateway marks the turn (`check_status["scenario_search_limit_reached"]`). `turn_fallback.classify` then does not count a `search_scenario` call among the failed tools of that turn. A model that gave up after it is `executor_no_action`, which the fallback already retries once (carrying the first attempt's passages) and, if it stays unresolved, answers with the scene's concrete options. Any other failed tool, or a search that failed with no refusal, is still a `tool_failure`.

## Verification

`tests/test_turn_fallback.py`: a turn whose only failed call was the refused search is `executor_no_action`; the same calls without the mark, or with another failed tool, stay `tool_failure`. `tests/test_turn_phases.py`: the gateway sets the mark on the first refused search and not before.
