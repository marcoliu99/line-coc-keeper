# KP Assistant delegated player actions

[繁體中文](kp_assistant_sudo_control_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. KP sudo acts on a selected player/character through existing command and player-turn paths. It does not grant arbitrary unvalidated model tools.

2. Revalidate the current KP role, target identity and conversation state. Pending check/Luck ownership and timeline gates remain applicable to delegated operations.

3. Ordinary KP OOC discussion stays in the independent Assistant with its own log. Explicit canon commands or successful canonical tool events use the controlled promotion path.

4. Delegation does not make private player information public or bypass spoiler/privacy policy. Outputs retain normal delivery destinations and audit context.

## Flow and interfaces

```text
KP authorization -> target investigator -> player command/action route -> authoritative result
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/commands/router.py](../../../app/commands/router.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_kp_sudo.py](../../../tests/test_kp_sudo.py)
- [tests/test_kp_assistant_v2.py](../../../tests/test_kp_assistant_v2.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/kp_assistant_sudo_control_design_spec.md)
