# Unified player-turn flow and independent KP Assistant

[繁體中文](unified_keeper_turn_flow_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `refactor`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. All three player inputs enter supervisor.run_turn. Ordinary actions use intent routing; resolved followups and opening fallback bypass ordinary player-intent classification.

2. Pure roleplay can skip mechanics. The single pipeline does not mandate two model calls for every input; it shares context, protection and commit semantics.

3. Resolved followups receive immutable dice/result events. Their callback allowlist permits read-only lookup and necessary damage/turn progression, but cannot create new skill/SAN rolls.

4. Start first uses an extracted scenario opening and its optional opening check. Only missing openings use opening_fallback, with lookup/display tools and no arbitrary mechanical mutation.

5. KP Assistant owns its provider conversation, tool whitelist, kp_ooc_log and explicit/verified canonical promotion. It is not routed through player Executor/Narrator.

6. keeper.run_turn and _run_turn_impl are removed. Shared prompt builders and keeper._execute_tool remain live interfaces; tests target the active agents directly.

7. Commit canonical history once, retain queued private/image output, and reject stale timelines. Clear obsolete OpenAI continuation IDs when authoritative history changes.

## Flow and interfaces

```text
Discord/router -> player_action / resolved_check_followup / opening_fallback -> Supervisor
Supervisor -> context -> Executor or established results -> Narrator -> guard/spoiler checks -> commit
KP OOC -> independent Assistant -> role-limited tools -> OOC or canonical commit
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/commands/handlers/system.py](../../../app/commands/handlers/system.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/unified_keeper_turn_flow_design_spec.md)
