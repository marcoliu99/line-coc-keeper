# Possessions, purchases and narrative plausibility

[繁體中文](carry_audit_zh.md)

## Current implementation

`Character.carried_items` and weapons/ammunition record established possessions. Legacy `Character.cash_balances` and `GroupState.commerce` remain readable in old saves after the PR #91 rollback, but there is no active cash ledger, quote or purchase receipt workflow.

## Judgment boundaries

Permit reasonable mundane personal items without turning play into an audit. Plot-significant, combat-relevant, rare or regulated objects need scenario/canonical support. Consider era, location, occupation/source and legality. Do not use blanket historical claims such as all radios or all semiautomatic weapons being unavailable in the 1920s; availability depends on the actual item and setting.

## Purchase sequence

Resolve travel and establish a plausible seller and available goods before acquisition. Credit Rating, lifestyle, price and cash do not determine whether an item can be acquired. When the acquisition is established in play, register a lasting possession through `add_carried_item`. There is no quote, payment confirmation, cash debit or atomic transaction tool.

## Narration and limitations

Describe current purchases as bought now; final inventory alone does not prove prior ownership or payment. Do not silently give a weapon because a player says they have always carried it. General structured legality/era validation, cash accounting and automatic asset liquidation are not implemented.
