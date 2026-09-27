# Pending-check ownership and duplicate protection

[繁體中文](bugfix_duplicate_pending_checks_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Registering a new investigator skill or SAN check must inspect both pending_checks and pending_luck_decisions. An unresolved decision cannot be silently replaced by a new roll.

2. Checks belong to an owner and timeline. Repeated commands or buttons must not consume the same check twice; mismatched or stale identities are rejected.

3. Autoroll defaults off. Changing the group setting affects new checks and must not consume existing pending requests.

## Flow and interfaces

```text
Action -> existing pending/Luck gate -> register once -> owner resolves -> persist -> narrate
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)
- [tests/test_state_loss_amnesia.py](../../../tests/test_state_loss_amnesia.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/bugfix_duplicate_pending_checks.md)
