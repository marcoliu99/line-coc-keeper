# Purchase provenance and informational turn handoffs

## Observed production cases

The 2026-09-27 07:01 local-time turn asked who/where the investigator was and
what actions were available. Logs show one successful scenario search, no dice
or mutation tools, and an incomplete decision. The old log omits the rejection
reason, so its exact rejection branch cannot be proven retrospectively.

The 07:06 turn explicitly requested buying kerosene, a lamp, a flashlight and an
axe. Four add_carried_item calls committed them during this turn. The handoff was
resolved_without_check; final narration incorrectly described them as previously
owned. No financial settlement appears in the tool trace. Current character models
have no dedicated cash ledger; prompt policy uses Credit Rating/lifestyle instead.

## Authorized independent fixes

- Permit no_mechanics with successful, explicitly allowlisted information queries
  only when complete observed before/after gameplay snapshots prove no mutation.
  Dice, output delivery, unknown tools, failed tools and changed state remain excluded.
- Log fixed validation reason codes without model prose or scenario content.
- Pass inventory deltas as structured current-turn events to Narrator. Final state
  is not evidence of prior ownership, a purchase source or payment.
- Make incomplete guidance depend on actual committed state/dice evidence. A read-only
  failure must not claim dice were rolled or resources changed.
- Regression tests use real tools/state with mocked model responses, not paid APIs.
  Preserve PR89 evidence checks, pending/Luck actions and no-replay guarantees.

## Purchase workflow (approved)

Both Credit Rating/lifestyle settlement and explicit cash accounting are supported.
Arrival is narrative adjudication independent of maps/room IDs. Executor must establish
that travel finished and the shop/stock are supported by scenario or canonical events.
A search miss is unknown, never permission to invent a shop or available stock.

    player action + existing scenario/state context
        -> Executor resolves travel and availability
             -> unresolved obstacle/check: stop, no acquisition
             -> arrival can be established:
                  ONE purchase_items(shop, arrived, arrival_basis, source,
                                     mode, affordability, items, currency)
                      -> Python rechecks actor / pending / combat / input validity
                      -> lifestyle: atomic inventory + purchase receipt
                      -> cash: persist quote, no payment or inventory yet
                           -> /coc purchase ID confirms exact cart/price
                           -> atomic cash debit + inventory + receipt
        -> validate existing Executor handoff
        -> Narrator describes arrival -> buying -> acquisition

The ordinary path adds one tool operation, not three arrival/quote/settlement operations.
There is no fixed extra LLM review or forced player turn. Cash confirmation is a
non-LLM command. This implementation conservatively requires explicit confirmation of
cash quotes; it does not interpret arbitrary natural-language text as monetary consent.

Python verifies sequencing/identity and monetary consistency, not the semantic truth
of arrival prose, shop existence, availability or lifestyle affordability. The AI
records its travel and affordability judgment in the same request. No persistent
world-location engine is introduced, and no separate arrival tool is needed.

## State and commands

- Character.cash_balances: currency -> integer hundredths (two-decimal accounting
  convention; no FX conversion). Legacy characters default to no confirmed funds.
- GroupState.commerce: durable transactions and KP balance adjustment audit.
- Receipt: actor, timeline, shop, arrival/source rationale, items/quantities, mode,
  actual Credit Rating, affordability rationale, unit prices and total for cash,
  quote/purchased/expired status; cash receipt includes before/after balances.
- One cart per actor per Executor invocation. Server-generated turn identity makes
  repeated calls idempotent; changing the cart mid-invocation fails explicitly.
- Identical item labels are stored with multiplicity, preserving purchased quantity.
- `/coc funds CHARACTER CURRENCY AMOUNT`: KP-only confirmed balance initialization
  or correction. It never infers money from Credit Rating.
- `/coc purchases`: player's balances, open quotes and recent receipts.
- `/coc purchase ID`: active owner confirms the exact quote; repeat confirmation is
  a no-op. It rechecks funds, character, timeline, pending checks and combat.
- Cash quotes expire on the owner's next Executor invocation. Existing map position,
  when present, must also match; maps are not required. Other players' simultaneous
  actions do not expire the owner's quote. KP balance correction can unblock payment.
- Commands use existing conversation/state locks and executable Help controls.

Prompts prohibit using add_carried_item for purchases. A conservative explicit
purchase-word guard additionally rejects that tool during matching turns; mixed
purchase/pickup declarations may need a separate declaration. This lexical guard is
not a general semantic intent classifier. Mundane non-purchase possessions remain
allowed under the existing policy.

Only current-turn structured events enter the Narrator handoff; the full ledger is
not fed back every turn. Purchases must be described as acquired now. Quoted goods
cannot be narrated as paid or owned. Lifestyle receipts do not claim exact cash debit.

## Verification and limitations

Use real SQLite persistence/tools with mocked providers to cover no-map purchases,
arrival/pending gates, missing/insufficient funds, owner/timeline checks, save failure,
duplicate settlement, quote expiry, query handoffs and truthful partial-failure guidance.
Verify one existing Executor provider invocation and one purchase tool call on the
ordinary path. This is a structural regression test, not a live API latency benchmark;
actual end-to-end speed/model fidelity still require a controlled API trial.

The live production inventory is not altered or retroactively charged. No paid API
calls, production bot restart or deployment is part of this change.

### Recorded local verification

- Isolated full pytest suite: 795 passed, 1 skipped, 15 subtests passed.
- `python3 -m mypy app`: 75 source files passed.
- Ruff on all changed Python files and `git diff --check`: passed.
- Providers were mocked; real tools and isolated SQLite persistence were exercised.

## Review hardening (2026-09-27)

Recognize bare traditional/simplified Chinese purchase verbs in the inventory bypass
guard. Dice provenance requires successful tool results in both normal and provider
failure paths; rejected expressions must never produce a no-reroll instruction.
Regression tests cover Chinese purchase phrasing and all three dice tools.
