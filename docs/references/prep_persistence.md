# Preparation and persistence reference

[繁體中文](prep_persistence_zh.md)

## Storage mapping

Scenario originals, chapter manifests, indexes, images and pregens live in the reusable scenario library. The selected context is copied into `GroupState.scenario_text`. SQLite stores group state, character mirrors, scenario indexes, memory chunks and related archives through `app/db.py` and repositories. Images remain files. The old `app/state.py`/per-group JSON description is historical.

## Before a scene

With RAG disabled, the current scenario snapshot is in the prompt subject to its input cap. With RAG enabled, retrieve relevant evidence and supplement real gaps. Approved externally prepared Chinese records accelerate lookup; original fallback remains available. Retrieval does not unlock chapters or authorize invented scenes.

## Campaign memory

Recent log, campaign summary, scene digest and memory RAG serve different purposes. Context-history budgeting does not erase authoritative state. Checkpoints allow rollback with timeline isolation; backups protect durable storage. Manual role-card assets survive game changes independently of active character pools.

## Operational boundaries

Do not commit runtime data or credentials as preparation documents. External scenario workbooks are source-bound import artifacts, not game saves. Human notes can supplement preparation, but changing a Markdown note does not mutate the live SQLite state.
