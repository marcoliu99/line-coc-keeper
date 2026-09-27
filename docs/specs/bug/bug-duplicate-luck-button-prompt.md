# Idempotent check and Luck button delivery

[繁體中文](bug-duplicate-luck-button-prompt_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Use check/decision identity and timeline to distinguish new prompts from already delivered ones. Concurrent same-channel requests must not emit duplicate actionable buttons.

2. Claim delivery while the turn lock is held; release the lock before slow Discord network delivery. A failed or stranded claim must remain recoverable.

3. Legacy button identities are validated conservatively. Compact custom IDs stay within Discord limits, while callbacks compare the full persisted identity and current timeline.

## Flow and interfaces

```text
Resolve -> claim persisted button identity -> send -> completion/recovery
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [tests/test_logging_completion.py](../../../tests/test_logging_completion.py)
- [tests/test_pending_button_latency.py](../../../tests/test_pending_button_latency.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-duplicate-luck-button-prompt.md)
