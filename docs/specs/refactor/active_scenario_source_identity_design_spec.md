# Stable Active Scenario Source Identity

## Status and baseline

Implementation scope for PR A, based on `origin/main_v2` at `c5a19912c2f1faf5454eb262693ddbc7dc3d0e4d`. This is the prerequisite for Game Opening Phase 2A; it does not change `/coc start` or any conversation lock. PR B must wait until this change is merged.

## Problem and goal

`GroupState` stores the active scenario ID, variant, chapter context, text and timeline, but no source version. Repair can replace a library entry under the same ID without rotating the timeline. The library already records a full SHA-256 `manifest.content_hash` of the complete parsed source text. Bind that existing full hash to authoritative active state in the **same SQLite transaction** that installs the corresponding context.

## Scope and data contract

Add a backward-compatible `GroupState.active_scenario_source_hash: str` field, serialized with the other active scenario fields. `""` means **unverified/unbound**, including legacy snapshots and contexts from older libraries without a valid source hash; it must never be guessed from current filesystem contents during deserialization. A bound value is the exact 64-character lowercase SHA-256 from the verified library manifest. No second hashing scheme or truncated ID is authoritative.

`scenario_library.load_context` should obtain a self-consistent manifest and full source text through the Source Store's existing `read_source` validation for hashed entries; an inconsistent manifest/text pair must not be installed. Legacy entries without a hash may still load, but remain unbound. Active binding is set centrally with `scenario_activation.install_context_fields`, which is used by Scenario Lifecycle's first upload, pending `new`/`fix`, scenario use and reparse, and the chapter-advance tool. Newgame clears it through a fresh `GroupState`. Checkpoint rollback restores the hash present in its saved snapshot; old checkpoints remain unbound. Pending-only submission must leave the active binding unchanged.

The source library's filesystem publication and SQLite activation remain non-atomic. Publication alone does not change the **active** DB binding; a pending candidate does not become the active source. If a same-ID publication occurred before a pending decision, the active state may still contain the previous source hash and context text until the decision commits. This is intentional and permits precise comparison of active V1 versus candidate V2.

## Key flows

```text
verified library manifest + full text
  -> load_context (chapter text + full content_hash)
  -> Scenario Lifecycle transition / chapter advance
  -> install_context_fields sets scenario_text and active_scenario_source_hash together
  -> one authoritative state transaction
  -> derived image refresh afterward
```

The same-ID repair test must prove `scenario_library_id` and timeline remain S/T while the active hash changes from H1 to H2. Failed or conflicted final commits retain H1. A reparse can publish a candidate before active activation; that publication does not itself advance the active hash.

## Tests and seams

Test through real temporary SQLite, real library publication and the Scenario Lifecycle / command seams. Assert persisted state and independently known library manifest hash, not a helper call count. Cover PDF and Markdown first activation; New Upload; same-ID Repair; scenario use; reparse; pending candidate retaining active binding; failed and stale/conflicted activation; chapter advance; serialization round-trip; legacy absent-hash preservation. Keep parser mocks at the expensive extraction boundary only.

Run relevant scenario/source/transaction tests, full `python -m pytest -q`, `ruff check .`, `mypy app`, and `git diff --check` before PR A is considered ready.

## Non-goals and unresolved boundary

Do not unlock opening extraction, modify opening UX, add an opening claim, change PDF/Markdown parsing, migrate existing rows eagerly, change scenario transition policies, or make filesystem and SQLite atomic. Phase 2A must keep the coarse-lock behavior for an unbound legacy state; that policy is specified for PR B, not implemented here. If a valid full source hash cannot be bound consistently on every active transition, hold Phase 2A.
