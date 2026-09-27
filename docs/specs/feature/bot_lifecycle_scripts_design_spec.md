# Local Discord bot lifecycle

[繁體中文](bot_lifecycle_scripts_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Scripts manage the Discord module process and its runtime files. Validate executable/module/process identity rather than trusting a PID file alone.

2. Startup must distinguish an already running bot from a stale PID. Shutdown targets only the verified bot and allows graceful termination before bounded escalation.

3. Profiling is optional. Stopping archives the runtime logs and profile artifacts; status should report the actual process state and useful locations.

4. Data cleanup is a separate explicit operation and must not be an accidental side effect of start/stop. Tests use fake processes retaining the same identity contract.

## Flow and interfaces

```text
Start -> record identity/log paths -> status -> identity-checked stop -> archive
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [scripts/start_bot.sh](../../../scripts/start_bot.sh)
- [scripts/stop_bot.sh](../../../scripts/stop_bot.sh)
- [scripts/bot_status.sh](../../../scripts/bot_status.sh)
- [tests/test_bot_lifecycle_scripts.py](../../../tests/test_bot_lifecycle_scripts.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/bot_lifecycle_scripts_design_spec.md)
