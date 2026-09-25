# Spec: legacy keeper.run_turn has no Guard Agent / rule_validator system-leak protection

## Changeset Tracking
- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: bug/legacy-run-turn-missing-guard — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Found during a full main_v2 code review, not from a specific production
incident.

The new Supervisor pipeline (`app/agents/supervisor.py`, step 6) runs
`guard.enforce_narrative_safety` before every reply reaches a player:
`rule_validator.validate_narrative` checks for system-prompt leaks
(`[SYSTEM]`, "as an AI", "我是一個語言模型", "tool_call") and unclosed
Markdown code blocks; if invalid, `guard.run_repair` retries an LLM rewrite
up to `MAX_REPAIR_ATTEMPTS` times, failing closed to a neutral fallback
text if still invalid.

`app/keeper.py`'s `_run_turn_impl` (the legacy single-call `run_turn` path)
never ran this check at all — only `spoiler_policy.sanitize_public_text`,
a *different* deterministic check for leaked `kp_only` facts/secret goals,
not system-prompt/formatting leaks. This legacy path is still actively
used for:
- The KP Assistant's entire OOC conversation (`app/agents/assistant.py`
  delegates to it, and — notably — `app/agents/supervisor.py`'s own
  `OOC_ASSISTANT` intent routing explicitly bypasses the whole Executor/
  Narrator/Guard pipeline and calls this path instead, per that file's own
  comment on step 2).
- Opening narration generation (`app/commands/handlers/system.py`).
- `/coc check` dice-result follow-up narration (`app/legacy_commands.py`).

A leaked system-prompt fragment or unclosed code block is just as real a
problem in KP-only OOC text as in canonical player-facing narrative —
nothing about the reasons `rule_validator` exists is specific to the new
pipeline.

## Fix

Add the same `guard.enforce_narrative_safety` call to `_run_turn_impl`,
right after `final_text` is produced and before the existing spoiler
check — matching `supervisor.py`'s ordering (system-leak/format check
first, then the separate kp_only-content spoiler check). Applied
unconditionally (not gated by `is_ephemeral`), covering all three current
callers of this legacy path with one fix.

`guard.enforce_narrative_safety(message: AgentMessage, reply_text: str)`
takes an `AgentMessage`, but neither it nor `run_repair` actually reads
anything off it (confirmed via `grep -n "message\." app/agents/guard.py`
— zero matches) — it's accepted only for interface consistency with the
Supervisor pipeline's other agent calls. `_run_turn_impl` doesn't build a
real `AgentMessage`, so a minimal placeholder (`AgentMessage(payload={})`)
is passed.

No circular import: `app.agents.guard`'s own dependencies (`app.config`,
`app.observability`, `app.spoiler_policy`, `app.agents.rule_validator`,
`app.domain.models`, `app.providers.*`, `app.services.prompt_config`)
don't import `app.keeper`, confirmed by grep and by a direct `import
app.keeper` smoke test.

## Testing Strategy

- `test_run_turn_repairs_leaked_system_text_via_guard`
  (`tests/test_kp_assistant_v2.py`): a provider returning text containing
  `[SYSTEM]` gets repaired via the (mocked) Guard Agent before `run_turn`
  returns, instead of the leaked fragment reaching the caller unfiltered.
- Full existing `test_kp_assistant_v2.py` suite (37 pre-existing tests)
  must keep passing unchanged — confirms the guard check is a no-op for
  every existing legitimate-text scenario (no LLM repair call triggered
  when `rule_validator.validate_narrative` already passes).
- Pure application logic (wiring an existing, already-tested check into
  a new call site), no real-API verification needed — `guard.py`'s own
  repair-loop behavior is unchanged.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- The existing comment at the spoiler-check block (now corrected) used to
  claim it was "Same guard as the Supervisor pipeline's" — it wasn't; it
  conflated the spoiler-content check with the separate system-leak Guard
  Agent check the Supervisor pipeline actually runs. Fixed in the same
  change.
