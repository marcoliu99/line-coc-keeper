# Spec: unhandled exceptions in OpenAI tool-call parsing and legacy run_turn's provider call

## Changeset Tracking
- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: bug/provider-tool-call-unhandled-exceptions — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Found during a full main_v2 code review, not from a specific production
incident. Two related availability gaps, both in the production OpenAI
path:

### 1. OpenAI tool-call argument JSON parsing had no error handling

`app/providers/openai_provider.py`'s `run_conversation` (~line 410):
`args = json.loads(fc.arguments or "{}")` — a malformed tool-call argument
payload from the model raises `json.JSONDecodeError` straight out of
`run_conversation`, crashing the whole turn. Anthropic/Gemini don't have
this exact failure mode (their SDKs hand back already-parsed dicts), so
this was an OpenAI-only gap in the currently-production provider.

### 2. `keeper.run_turn`'s provider call had no try/except at all

`app/keeper.py`'s `_run_turn_impl` calls `provider.run_conversation(...)`
(both the OpenAI branch and the else branch) with no surrounding
try/except. `app/agents/executor.py`'s `run_executor` and
`app/agents/narrator.py`'s `run_narrator` each wrap their own equivalent
call and degrade gracefully on failure — this legacy single-call path
(still used for the KP Assistant's OOC conversation, opening narration,
and `/coc check` result narration) has no such safety net. An unhandled
exception here propagates straight out of `run_turn` to whichever caller
invoked it (`legacy_commands.py`, `assistant.py`, `commands/handlers/
system.py`), none of which catch it either — the player gets no reply at
all, not even an error message, even though any tool calls already
executed and saved earlier in the same turn already took effect.

## Fix

1. Wrap the `json.loads(fc.arguments or "{}")` call in `try/except
   (TypeError, ValueError)`, surfacing a parse failure to the model as an
   ordinary tool-failure result (`{"ok": False, "error": "..."}`) instead
   of letting it raise — the same shape every other tool failure already
   uses, so the model can see and potentially recover from it.
2. Wrap both `provider.run_conversation(...)` call sites in
   `_run_turn_impl` with `try/except Exception`, logging and falling back
   to the same neutral text `narrator.py` already uses for its own
   equivalent failure (`"（守密人一時語塞，請再說一次剛才的行動）"`).
   `except Exception` (not bare `except`) so `asyncio.CancelledError`
   (which inherits from `BaseException`, not `Exception`) still propagates
   correctly.

## Testing Strategy

- `test_openai_malformed_tool_call_arguments_do_not_crash_the_turn`
  (`tests/test_async_provider_contract.py`): a function-call with
  unparseable `arguments` doesn't raise, the tool executor is never
  invoked for that malformed call, and the turn still completes.
- `test_run_turn_falls_back_gracefully_when_the_provider_call_raises`
  (`tests/test_kp_assistant_v2.py`): a provider whose `run_conversation`
  raises still returns the narrator-style fallback text from `run_turn`
  instead of propagating.
- Pure application logic (error handling, not model behavior), no
  real-API verification needed.
- Standard four checks (ruff, mypy, compileall, pytest).
