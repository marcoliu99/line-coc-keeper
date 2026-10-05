# Splitting `app/keeper.py` by responsibility

[繁體中文](keeper_module_split_design_spec_zh.md)

Status: **partial** — step 1 (prompt construction) implemented; steps 2–3 pending. Base: `main_v2` at `b54c986`.

## Problem

`app/keeper.py` is a 1,500-line hub with a fan-out of 31 modules. It holds, side by side: prompt construction, state-mutation wrappers, the turn commit, post-turn memory maintenance, tool dispatch and the combat-status gate. `keeper_tools/*` call back into it through lazy imports (a 12-module cycle), and about 35 uses of `keeper._*` in 9 other files rely on `SLF001` exemptions. See `docs/architecture/main_v2_architecture_review.md` (F9).

## Constraint: the player path does not change

This is a move, not a redesign. A function keeps its body, its arguments and its position in the call sequence; only the module it lives in changes. In particular no `await` is added or removed, no lock is taken or released earlier or later, and no player-visible text changes.

## Steps

1. **`app/prompt_builder.py`** (this step): `build_static_prompt`, `build_dynamic_prompt`, `correction_context_message`, `format_turn_message`, `format_kp_canonical_history_message`, the persona and KP Assistant prompt constants, and the scenario-budget and spoiler/privacy rule helpers. Their private names become public and every caller follows. `KP_OOC_LOG_MAX_MESSAGES` moves to `app/config.py` because both the prompt and the turn commit read it.
2. **`turn_commit.py` and `memory_maintenance.py`**: `_commit_turn_result`, `_commit_kp_ooc_turn_result`, timeline guarantees; post-turn maintenance and log summarisation.
3. **`tool_dispatch.py`** and a new gate: `keeper_tools` must not import `keeper`.

No compatibility re-exports are left behind in `keeper.py`; tests and callers are updated to the new names in the same change.

## Verification

- The static and dynamic prompts for an empty and an active state, with scenario retrieval on and off, for the player and KP Assistant roles (16 prompts) hash identically before and after the move.
- `ruff check .`, `mypy app` and the full `pytest` pass; tests that patched a moved name now patch it where it lives.
- A test that patched `keeper.SCENARIO_RAG_ENABLED` to change the prompt now also patches `prompt_builder.SCENARIO_RAG_ENABLED`; both modules read the same `app.config` value at import time.

## Not in scope

Changing prompt text, caching behaviour, tool exposure or any lock. Those belong to the latency work in the architecture review (P4) and need measurement first.
