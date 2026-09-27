# Atomic narrative purchases and acquisition provenance

[繁體中文](purchase_turn_provenance_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Executor establishes completed travel, supported commercial context and affordability. For ordinary goods, known commercial surroundings support a bounded AI procurement ruling without a named shop or itemized scenario stock. Maps are optional; Python checks sequence/identity, not the semantic truth of the arrival narrative.

2. Lifestyle purchases settle inventory and a receipt together using actual Credit Rating plus recorded affordability rationale. They do not claim an exact cash debit.

3. Cash mode first persists an exact quote. /coc purchase ID explicitly confirms its cart/prices; recheck owner, character, timeline, funds, pending checks and combat before atomic debit and acquisition.

4. Character.cash_balances stores integer hundredths by currency without FX conversion. Legacy cards have no inferred funds; KP-only /coc funds initializes or corrects confirmed balances.

5. GroupState.commerce holds durable quotes/receipts and balance adjustments. One cart per actor per Executor invocation uses server turn identity; duplicate settlement is a no-op and changed retry carts fail.

6. Quotes expire on the owner’s next Executor invocation and recheck existing map position when present. Another player’s action does not itself expire the owner’s quote.

7. Reject add_carried_item during recognized purchase requests, including bare Chinese purchase verbs. This lexical guard is conservative, not a general intent classifier; mixed pickup/purchase may require separate declarations.

8. Narrator receives current-turn purchase/inventory events and describes buying now, not prior ownership. Quotes do not imply payment or possession. Successful dice evidence alone justifies no-reroll guidance.

9. The ordinary lifestyle path uses one purchase tool, with no fixed extra LLM reviewer or arrival call. Cash confirmation is deterministic. This does not retroactively charge old production inventory.

## Flow and interfaces

### Approved incident correction (2026-09-27)

The 20:44 Chinese-scenario shopping turn retrieved complete selected records in 0.51s with a 4,350-token follow-up budget. Executor returned validated `blocked` after only a search; it never called purchase or inventory tools. The 12.54s request ended with a deterministic generic replacement of Narrator output. The log contains no raw resolution reason, so the precise model rationale is unknown. State has Credit Rating 20, no pending check/Luck/combat, no purchase receipt and no confirmed cash balance.

Allow an AI ruling for ordinary legal goods in a scenario-established commercial environment, including modest quantities of lamps and bottled kerosene for lighting. This is an explicit canon-policy exception, not permission to invent named shops, shopkeeper backstories, clues, weapons, rare/controlled goods or scenario-critical resources. Respect isolation, closure, scarcity, blocked travel and outstanding mechanics. Existing Chinese evidence of shops is sufficient context; no English lookup is required solely to find an itemized ordinary-goods catalog. Record the commercial basis, arrival and affordability in the existing receipt. Prices/cash still require confirmation; lifestyle mode uses the actual Credit Rating without inventing a cash debit.

Add optional `TurnResolution.blocker_code` for blocked/incomplete handoffs: `purchase_source_unconfirmed`, `purchase_arrival_unconfirmed`, `purchase_price_unconfirmed`, or `purchase_funds_unconfirmed`. These are bounded uncertainty categories, not proof that a shop does not exist or the player lacks money. Validate the closed set, log only the code, and render static actionable messages. Do not expose raw model reasons, hidden scenario details or arbitrary next-step commands. Existing pending checks/Luck, partial changes and actual missing-evidence gates take priority. Legacy handoffs without a code retain the safe fallback. No new database schema or fixed LLM stage.

```text
Known commercial setting + ordinary goods + feasible travel
  -> AI arrival/availability/affordability ruling -> existing purchase_items
Missing requirement -> blocked/incomplete + blocker_code
  -> Python validates code -> preserves pending/partial safeguards
  -> static explanation of what still needs confirmation
```

Test offline with the real Executor/tool/state path and mocked provider output: lamp plus two kerosene bottles at Credit Rating 20, no-map arrival, bounded blockers and malicious free-text reasons, unknown-code rejection, pending mechanics, cash confirmation and duplicate prevention. Prompt tests cover the same exception in shared canon and retrieval instructions. These tests cannot prove live-model compliance; no paid replay or production-state repair is included.

Implemented on `bug/actionable-purchase-blockers` from `main_v2` `edd2fd6`. Verification: 1,157 passed, 1 skipped, 41 subtests passed; Ruff, mypy (86 source files), and `git diff --check` passed. No new fixed model call is introduced. Availability and affordability remain AI adjudications, not semantic guarantees provided by the Python receipt validator.

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
