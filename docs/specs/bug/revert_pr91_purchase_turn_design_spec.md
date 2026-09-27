# Revert PR #91 purchase turn implementation

Status: implementation requested. Base: `main_v2`. Branch: `revert/pr91`.

## Goal and scope

Undo PR #91's purchase transaction workflow, cash ledger commands, purchase receipts, Executor purchase guard, and associated tool/prompt/Help surfaces. PR #106 later changed that workflow; remove its dependent changes where necessary so the resulting code and tests remain consistent. Keep unrelated read-only turn resolution fixes from PR #91 when they still solve independent incomplete-turn errors.

## Data and compatibility

Do not delete or rewrite existing player saves. Persisted cash balances or purchase receipts from previous runs may remain in serialized state but no longer authorize new purchases. No automatic refunds or inventory rollback; those would guess at past player intent and prices.

## Flow

```text
main_v2 with PR #91 and #106
 -> reverse PR #91 merge
 -> remove dependent PR #106 purchase-only logic
 -> restore coherent pre-transaction Help/tool/prompt behavior
 -> tests and isolated persistence check
 -> reviewable revert PR
```

## Verification and tradeoffs

Run focused purchase/turn tests, full isolated suite, Ruff, mypy, and diff checks. Update tests that assert removed purchase commands/tools; retain tests for unrelated turn resolution. This intentionally removes atomic payment and transaction receipts. Existing game state is left untouched. No bot restart or live migration is included.

## Review corrections

The first review restored the pre-ledger Credit Rating affordability rule in the Keeper prompt and aligned both possession references with `add_carried_item`, removing obsolete quote, debit and receipt claims. The Credit Rating portion was superseded by the subsequent user instruction below.

## Subsequent scope change

The user then requested removing Credit Rating from purchase judgments as well. Acquisition depends on supported travel, availability, provenance and legality, without a Credit Rating, lifestyle, price or cash gate. The Credit Rating skill still exists on character sheets for other game uses. Update the current guides and references to this rule; historical purchase specs remain marked as historical.
