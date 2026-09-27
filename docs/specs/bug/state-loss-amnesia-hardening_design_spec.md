# State revision and timeline isolation

[繁體中文](state-loss-amnesia-hardening_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Long-running model work may outlive rollback, scenario changes or another state write. Recheck timeline and revision before committing; stale turns must not overwrite newer authoritative state.

2. Checkpoint restore creates a new timeline and invalidates stale callbacks/conversation chains. Persisted game data and derived memory must remain scoped to the surviving timeline.

3. Deterministic dice and successful tools commit their effects before narrative continuation. Model failure cannot roll back those effects or authorize duplicate execution.

4. Commands, buttons, Supervisor and KP Assistant must use the same stale-state discipline. Revision conflicts surface as controlled failures, not blind saves.

## Flow and interfaces

```text
Load revision/timeline -> resolve or model work -> guarded write -> one canonical commit
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/repositories/group_state.py](../../../app/repositories/group_state.py)
- [app/checkpoints.py](../../../app/checkpoints.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [tests/test_state_loss_amnesia.py](../../../tests/test_state_loss_amnesia.py)
- [tests/test_state_persistence.py](../../../tests/test_state_persistence.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/state-loss-amnesia-hardening_design_spec.md)
