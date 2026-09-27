# Archive logs and profiles after stopping the bot

[繁體中文](enhancement-archive-log-on-stop_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. The lifecycle stop flow archives configured runtime logs and optional profiler output under the configured archive destination. A process must match the recorded bot identity before it is stopped.

2. Archive the actual structured/text log and profiler outputs without mistaking unrelated files for the running process’s artifacts. Preserve useful diagnostics on partial failure.

3. Profiling is opt-in. This is operational file handling, not a new per-turn model request or a production data reset.

## Flow and interfaces

```text
Verify process identity -> graceful stop -> archive logs/profile -> report paths
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [scripts/stop_bot.sh](../../../scripts/stop_bot.sh)
- [scripts/start_bot.sh](../../../scripts/start_bot.sh)
- [tests/test_bot_lifecycle_scripts.py](../../../tests/test_bot_lifecycle_scripts.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-archive-log-on-stop.md)
