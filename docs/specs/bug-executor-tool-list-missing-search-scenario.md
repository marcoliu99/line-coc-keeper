# Spec: Executor tool list missing search_scenario (and kp_assistant filtering)

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: bug/executor-tool-list-missing-search-scenario:TBD-after-commit — ruff/mypy/compileall/pytest all green (476 passed, 6 subtests)

## Purpose & Scope

Found while building a real-API verification script for
`docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md`
(round 3's 8-scenario sweep) — confirmed by reading the import chain, not
just log inference, so this is a real bug independent of that spec's
tiering discussion.

`app/agents/tool_gateway.py` exports:

```python
TOOLS: list[dict[str, Any]] = keeper.TOOLS
```

a module-level constant computed once at import time from the bare 34-tool
`keeper.TOOLS` list. `app/agents/executor.py` (the Supervisor/Executor
path used for ordinary free-text roleplay — see
`app/commands/router.py:_handle_ordinary_text_message_locked`) imports
this constant directly and passes it to `provider.run_conversation(...)`
unchanged for every single turn.

The correct tool list for a turn is `keeper._tools_for_speaker_role
(speaker_role)` (`app/keeper.py:3321`), which:
1. Appends `_SEARCH_SCENARIO_TOOL` when `SCENARIO_RAG_ENABLED` (35 tools
   total instead of 34) — the static prompt this same Executor sends
   explicitly instructs the model it **must** use `search_scenario` to
   look up any scenario detail when RAG mode is on
   (`_build_static_prompt`'s "這份劇本改用檢索模式...必須呼叫
   search_scenario 工具查詢" text), but the tool was never actually
   offered.
2. Filters to `_KP_ASSISTANT_ALLOWED_TOOL_NAMES` and patches `roll_dice`'s
   schema with an extra `roll_context` field when `speaker_role ==
   "kp_assistant"`.

`tool_gateway.TOOLS` does neither. Confirmed both failure modes are real,
not just theoretical:
- **RAG mode** (`SCENARIO_RAG_ENABLED=true`, the actual live-deployment
  setting for `line-coc-keeper-main-v2` per its `.env`): every ordinary
  Executor-path turn is missing `search_scenario` entirely, even though
  its own prompt tells it to use that tool.
- **KP Assistant speaking in-character**: `app/commands/router.py`'s
  `_handle_ordinary_text_message_locked` sets `speaker_role = "kp_
  assistant"` when the KP Assistant user sends an ordinary (non-OOC)
  message, and `supervisor.run_turn` routes a `GAMEPLAY_ACTION`-classified
  message straight to `executor.run_executor` regardless of speaker_role
  (`OOC_ASSISTANT` intent is the only thing that bypasses Executor,
  handled separately by `app/agents/assistant.py`) — so Executor genuinely
  can run with `speaker_role == "kp_assistant"`, and when it does, it's
  currently using the *unfiltered* 34-tool list instead of the KP-specific
  restricted/patched one.

## Changes

- `app/agents/tool_gateway.py`: replace the static `TOOLS` constant with a
  function, e.g. `tools_for_speaker_role(speaker_role: str) -> list[dict]`,
  that delegates to `keeper._tools_for_speaker_role(speaker_role)` —
  keeping the existing pattern this module already uses elsewhere
  (`make_tool_executor` delegates to `keeper._execute_tool`; this module is
  the boundary layer between `executor.py` and `keeper.py`'s internals, not
  a place to re-derive logic).
- `app/agents/executor.py`: import `tools_for_speaker_role` instead of the
  static `TOOLS`; call `tool_gateway.tools_for_speaker_role(speaker_role)`
  using the turn's real `speaker_role` (already available as a local
  variable) and pass that to `provider.run_conversation(...)` instead of
  the old static list.

## Testing Strategy

- A test confirming `tool_gateway.tools_for_speaker_role("player")`
  includes `search_scenario` when `SCENARIO_RAG_ENABLED` is patched on,
  and excludes it when patched off — mirroring `keeper._tools_for_speaker_
  role`'s own existing behavior, just confirming the gateway function
  actually delegates rather than re-implementing.
- A test confirming `tool_gateway.tools_for_speaker_role("kp_assistant")`
  returns the filtered/patched set (fewer tools than `"player"`'s, and
  `roll_dice`'s schema includes `roll_context`).
- An `executor.py`-level test (mocking `provider.run_conversation`) asserting
  the `tools` argument it's called with actually varies by `message.payload
  ["speaker_role"]` — i.e. this is genuinely read per-turn, not a constant
  baked in at import time, which is the whole point of this fix.
- Run the standard four checks (ruff, mypy, compileall, pytest).

## Notes
- This is unrelated to `docs/specs/enhancement-executor-model-tiering-and-
  tool-scoping.md`'s model-tiering/dynamic-scoping discussion — that spec
  is about *which subset* of the (now-correct) tool list to offer per
  scene; this bug is about the list being wrong/incomplete in the first
  place, for every Executor-path turn regardless of any tiering decision.
  Fixing this first makes that spec's future real-API trials measure the
  right baseline too (round 3's 8-scenario sweep there was run against the
  same buggy 34-tool list, missing `search_scenario` — its `scenario_
  lookup` scenario result should be re-verified against the fixed tool
  list once this lands, though the conclusion there is unlikely to change
  since that scenario already passed once `search_scenario` was manually
  added back for that specific test).
