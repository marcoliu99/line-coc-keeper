# Possessions, purchases and narrative plausibility

[繁體中文](carry_audit_zh.md)

## Current implementation

`Character.carried_items` and weapons/ammunition record established possessions. `Character.cash_balances` and `GroupState.commerce` now implement confirmed balances, quotes and purchase receipts. The old statement that purchases are entirely prompt-only and no cash ledger exists is obsolete.

## Judgment boundaries

Permit reasonable mundane personal items without turning play into an audit. Plot-significant, combat-relevant, rare, regulated or expensive objects need scenario/canonical support. Consider era, location, occupation/source and affordability. Do not use blanket historical claims such as all radios or all semiautomatic weapons being unavailable in the 1920s; availability depends on the actual item and setting.

## Purchase sequence

Travel must be resolved and a supported shop/stock established before acquisition. Lifestyle mode records the Credit Rating affordability judgment and receipt; cash mode requires confirmed currency/funds and explicit quote confirmation. Debit, inventory and receipt settle atomically. Map coordinates are optional. The backend checks identity/consistency; the AI still adjudicates narrative arrival and stock.

## Narration and limitations

Describe current purchases as bought now; final inventory alone does not prove prior ownership. A quote is not payment. Do not silently give a weapon because a player says they have always carried it. General structured legality/era validation and automatic asset liquidation are not implemented.
