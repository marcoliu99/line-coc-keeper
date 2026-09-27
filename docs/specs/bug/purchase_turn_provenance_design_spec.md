# Atomic narrative purchases and acquisition provenance

[繁體中文](purchase_turn_provenance_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Executor establishes completed travel, a scenario-supported shop/stock and affordability. Maps are optional; Python checks sequence/identity, not the semantic truth of the arrival narrative.

2. Lifestyle purchases settle inventory and a receipt together using actual Credit Rating plus recorded affordability rationale. They do not claim an exact cash debit.

3. Cash mode first persists an exact quote. /coc purchase ID explicitly confirms its cart/prices; recheck owner, character, timeline, funds, pending checks and combat before atomic debit and acquisition.

4. Character.cash_balances stores integer hundredths by currency without FX conversion. Legacy cards have no inferred funds; KP-only /coc funds initializes or corrects confirmed balances.

5. GroupState.commerce holds durable quotes/receipts and balance adjustments. One cart per actor per Executor invocation uses server turn identity; duplicate settlement is a no-op and changed retry carts fail.

6. Quotes expire on the owner’s next Executor invocation and recheck existing map position when present. Another player’s action does not itself expire the owner’s quote.

7. Reject add_carried_item during recognized purchase requests, including bare Chinese purchase verbs. This lexical guard is conservative, not a general intent classifier; mixed pickup/purchase may require separate declarations.

8. Narrator receives current-turn purchase/inventory events and describes buying now, not prior ownership. Quotes do not imply payment or possession. Successful dice evidence alone justifies no-reroll guidance.

9. The ordinary lifestyle path uses one purchase tool, with no fixed extra LLM reviewer or arrival call. Cash confirmation is deterministic. This does not retroactively charge old production inventory.

## Flow and interfaces

```text
Arrival/stock adjudication -> purchase_items
  Lifestyle -> affordability decision -> atomic items + receipt
  Cash -> stored quote -> /coc purchase ID -> revalidate -> atomic debit + items + receipt
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/services/purchases.py](../../../app/services/purchases.py)
- [app/commands/handlers/purchase.py](../../../app/commands/handlers/purchase.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/models.py](../../../app/models.py)
- [tests/test_purchase_flow.py](../../../tests/test_purchase_flow.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/purchase_turn_provenance_design_spec.md)
