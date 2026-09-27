# Scenario canon boundaries and durable corrections

[繁體中文](narrative_boundaries_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Scenario materials and explicitly established canonical events govern world facts. A player assumption or earlier unsupported AI narration cannot create a room, enemy, clue or significant item.

2. Reasonable mundane possessions are allowed. New content with plot or mechanical impact needs evidence; a retrieval miss means unconfirmed, not nonexistent and not permission to invent.

3. Dedicated /coc correct reports are out of game. Validate their target message against a current-channel/timeline Keeper receipt; player allegations do not automatically become canon or mechanical holds.

4. Keep raw report text out of system prompts. The bounded user-message projection separates pending allegations from approved resolutions and KP-defined hold_scope.

5. Limit pending reports to 3 per reporter and 12 per group. Project at most 6000 JSON characters, prune/archive closed records, and do not silently drop effective approved decisions to fit a budget.

6. Approve/reject/hold/supersede are authorized adjudication operations; reporters may withdraw their own reports. A scoped hold blocks relevant actions, while unrelated actions continue.

7. If authoritative decisions exceed projection capacity, require explicit consolidation rather than silently forgetting canon. Rollback/scenario changes isolate correction timelines.

8. No fixed per-turn LLM reviewer is added. Prompt compliance and semantic completeness are not guaranteed by deterministic state/receipt validation alone.

## Flow and interfaces

### Ordinary procurement exception (2026-09-27)

The player approved ordinary legal purchases based on an established commercial environment without requiring a named shop or an itemized scenario catalog. AI must still adjudicate arrival and affordability, record the basis, and settle through `purchase_items`. Small quantities of lamps and bottled kerosene for lighting qualify for consideration. Weapons, rare/controlled goods, clues and scenario-critical resources do not qualify; scenario restrictions, scarcity and outstanding mechanics override convenience. This exception does not authorize shopkeeper backstories or arbitrary new locations. See the [purchase contract](../bug/purchase_turn_provenance_design_spec.md) for the actionable blocker handoff and cash/lifestyle distinction.

```text
Player action/hypothesis -> scenario evidence -> mechanics -> narration
/coc correct -> verified message receipt -> pending allegation -> KP adjudication -> durable projection
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/services/narrative_corrections.py](../../../app/services/narrative_corrections.py)
- [app/commands/handlers/correct.py](../../../app/commands/handlers/correct.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [tests/test_narrative_boundary_prompts.py](../../../tests/test_narrative_boundary_prompts.py)
- [tests/test_narrative_correction_lifecycle.py](../../../tests/test_narrative_correction_lifecycle.py)
- [tests/test_narrative_correction_command.py](../../../tests/test_narrative_correction_command.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/narrative_boundaries_design_spec.md)
