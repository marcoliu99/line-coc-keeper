# Durable state, checkpoints and scoped memory

[繁體中文](state_persistence_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. SQLite is the authority for group state, character mirrors and persisted indexes/memory. Page images and scenario library artifacts remain filesystem resources with separately configured directories.

2. State writes preserve revisions and timeline identity. Transactional companion writes, including manual assets or correction archives, must commit with their associated group state.

3. Checkpoints and backups serve different purposes. Restore must invalidate old action identities and prevent stale model work or memory from leaking across timelines.

4. Defaults include backups every 60 minutes retaining 48, and scene digest maintenance every 12 turns. These are configuration defaults, not proof backups exist on a particular deployment.

5. Historical text is summarized/indexed under its proper scope. Current pending checks, Luck, inventory and combat state remain authoritative over summaries and retrieved memories.

6. Do not reapply state deltas for tools that already committed. Failed narrative continuation retains effects; empty or failed tool results do not prove mutation.

## Flow and interfaces

```text
Load SQLite state -> checked mutation -> transactional save/checkpoint -> backup -> optional new-timeline restore
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/db.py](../../../app/db.py)
- [app/repositories/group_state.py](../../../app/repositories/group_state.py)
- [app/checkpoints.py](../../../app/checkpoints.py)
- [app/scene_digest.py](../../../app/scene_digest.py)
- [app/memory_rag.py](../../../app/memory_rag.py)
- [tests/test_state_persistence.py](../../../tests/test_state_persistence.py)
- [tests/test_state_loss_amnesia.py](../../../tests/test_state_loss_amnesia.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/state_persistence_design_spec.md)
