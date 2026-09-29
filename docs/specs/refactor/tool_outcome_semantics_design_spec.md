# Tool outcome semantics

[繁體中文](tool_outcome_semantics_design_spec_zh.md)

Status: **backlog — awaiting spec review**. Base: `main_v2` at `7cef87c` (2026-09-29).

## Problem and goal

Keeper tools already have ordered schemas, handlers, and capability flags in `app/keeper_tools/registry.py`. Their *executed outcomes* still have to be recognized independently by `app/agents/executor.py`, `app/services/turn_resolution.py`, and `app/services/turn_delivery.py`. Adding `initialize_combat` required separate changes to deferred-encounter validation and public observation; omissions in both places were fixed during PR #148 review. The goal is a deep module that interprets verified tool events once and gives these consumers consistent, limited facts.

## Scope and interface

- Inventory every registered state-changing tool and its current result shape, completion relevance, follow-up need, and safe public observation. Include read-only dice and query results only where a current consumer depends on their outcome semantics. Migrate by tool family with equivalence tests; do not maintain two permanent lists.
- Put the shared interpretation behind an independent module, separate from `ToolSpec`. Its input is the actual tool event and available authoritative before/after evidence; its output distinguishes a verified effect, an unresolved or failed effect, a possible encounter setup, and safe observation facts. A tool name or `ok=true` alone never proves a mutation.
- `turn_resolution` retains final proof against current state, evidence references, actor and timeline identity, pending/Luck priority, and the strict `_setup_only` snapshot comparison. Encounter setup may add combatants in round 1; it never licenses damage, advancing an existing turn, or unrelated state changes.
- `turn_delivery` retains audience and privacy projection. The shared module must not expose enemy cards, ability details, private clues, raw RAG, internal errors, or unfiltered arguments. Existing KP Assistant permissions remain with the registry/gateway.
- An unclassified new state-changing tool fails closed for completion and public observation. Add a completeness test that requires an explicit interpretation for every such registered tool, so omissions are caught before deployment.

## Data and compatibility

No database, tool schema, prompt, or provider protocol change is planned. The interpreted outcome is an internal typed value, not a persisted replacement for raw receipts. Preserve tool order and `tool:N` evidence identities. The refactor must preserve existing player-facing wording unless a current unsafe exposure is proven.

## Flow

```text
Keeper tool -> actual receipt + authoritative snapshots
            -> outcome semantics module -> verified, bounded facts
            -> turn_resolution: whole-turn proof against fresh state
            -> turn_delivery: audience/privacy projection
            -> Narrator handoff
```

## Non-goals

Do not move tool execution or state mutation into this module. Do not make registry capability flags sufficient proof of completion, merge speaker-role authorization into outcome interpretation, weaken scenario canon checks, or add an LLM review call. This work does not implement general durable Discord delivery.

## Verification

1. Freeze current result behavior for every migrated family. Exercise a successful mutation, no-op, failed tool, missing result field, stale snapshot, and private outcome where applicable.
2. Test `initialize_combat`, `start_combat`, and `add_npc_to_combat` as round-1 setup, then reject damage, turn advance, and unrelated mutations under `deferred`.
3. Assert the registry inventory has no state-changing tool without an outcome interpretation; unknown outcomes remain internal and cannot establish completion.
4. Keep `tests/test_tool_gateway_speaker_role.py`, `tests/test_keeper_tool_registry.py`, `tests/test_initialize_combat.py`, and `tests/test_turn_consistency_handoff.py` green. Run full pytest, Ruff 0.16.8, `mypy app`, and `python -m compileall app tests` after implementation.

## Trade-off and review decision

The module owns shared event meaning, while final turn proof and audience policy remain in their current owners. This is intentionally more than moving name sets into a helper: deleting the module should force outcome knowledge back into at least the Executor, resolution, and delivery consumers. Marco confirmed an independent branch, full inventory with family-by-family migration, conservative defaults, and the separate authority/privacy seam. No implementation begins until this spec is reviewed.
