# Agentic Keeper architecture

[繁體中文](agentic_keeper_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `refactor`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Python Supervisor orchestrates typed AgentMessage/MechanicResult/TurnResolution data. It is not an autonomous graph service; agent boundaries separate intent, authority, narration and output protection.

2. Context preparation combines current state, scenario evidence and timeline-scoped memory. Executor invokes existing authoritative tools; StateReducer must not apply their committed mutations a second time.

3. Narrator receives verified mechanic facts and structured current-turn events. Deterministic consistency checks protect actionable check instructions after generation.

4. Command handlers live under app/commands, prompt composition under app/services/prompt_config.py. The original diagrams delegating full turns back to keeper.run_turn are superseded.

5. KP Assistant remains a separate agent with OOC/canonical separation. Unified-turn and state-handoff specs govern later refinements of the original architecture.

## Flow and interfaces

```text
Context -> intent -> mechanics or roleplay -> narration -> validation -> canonical commit
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [app/agents/intent_router.py](../../../app/agents/intent_router.py)
- [app/agents/state_reducer.py](../../../app/agents/state_reducer.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [app/domain/models.py](../../../app/domain/models.py)
- [tests/test_agentic_pipeline.py](../../../tests/test_agentic_pipeline.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/agentic_keeper_design_spec.md)
