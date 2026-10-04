# Scenario admission

[繁體中文](scenario_admission_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented**. Based on `main_v2` at `f88135d` (2026-10-05); the first item of the 2026-10-05 architecture review.

A library scenario reached a conversation by four doors: a PDF upload, a Markdown upload, the "new scenario or correction" choice that follows an upload while a game runs, and `/coc scenario use`. Each door repeated the same knowledge: which pending states block admission, how to read the previous scenario's content hash, the order *install the chapter → retire the old cards → install the new pool → commit → publish page images*, and the notices that follow. A change to that order (for example when the Markdown door was added) had to be made in each copy.

`app/services/scenario_admission.py` now owns the sequence. A door supplies a prepared library entry and its own wording; the module owns the state transition.

## Interface

| Function | What it does |
| --- | --- |
| `pending_block(state, include_similar=True)` | Why a scenario cannot be admitted now: `pregen_luck`, `upload_choice` or `similar_upload`, in the order the doors check them, or `None`. The door chooses the wording. |
| `content_hash(scenario_id)` | The stored content hash of a library entry, `""` when there is none to read. |
| `admit_upload(...)` | Under the conversation lock: `stale_revision`, `raced` (a choice is already waiting), `needs_choice` (a game is running; the upload is stashed in `pending_pdf_upload`) or `activated` (the first upload, applied at once). |
| `activate_pending_choice(...)` | Resolve a stashed upload as a new scenario or as a correction of the running one. |
| `activate_selected(...)` | `/coc scenario use`: a new campaign context that keeps the live investigators. |
| `commit_activation(...)` | Retire the old cards, install the new pool, commit through `state_transaction`, then publish the page images. Returns an `Activation` with the two notices (`image_note`, `card_note`). |
| `apply_new_scenario`, `apply_scenario_correction`, `apply_scenario_use`, `install_library_context`, `replace_scene_maps_preserving_locations` | The state transitions themselves, moved here unchanged. |

`scenario_activation` stays the layer underneath (copy the fields, publish images after a commit). `scenario_ingestion` and the `/coc scenario` handler are adapters: they parse, check permissions, extract, and word the replies. Advancing a chapter (`advance_scenario_chapter`) keeps the cast and the maps and commits as a tool mutation, so it still calls `install_context_fields` and `refresh_after_commit` itself.

## Contract kept

1. No player-visible wording changed: every message, in the same order and with the same suffixes (the choice reply still puts the card note before the variant notice; the upload replies put it last).
2. Extraction, the library, Keeper authority, the state transaction boundary and what is stored are untouched. The writes still use `commit_snapshot`, with the page images published only after the commit.
3. `tests/test_ingestion_trace.py` replays a first upload, a similar re-upload, a role sheet, a Markdown upload, the new/correction choice and `/coc scenario use` against a trace recorded **before** the refactor; the output is identical.

## Enforcement

`tests/test_scenario_admission.py` covers each outcome of `admit_upload`, both choices, `/coc scenario use` and the failed image refresh on real SQLite. `tests/test_architecture_scenario_admission.py` fails if anything other than the admission module calls `commit_and_refresh`, if `install_context_fields` is called from anywhere but the admission module and the chapter advance, or if a private copy of a moved transition comes back.

## Not changed

The role-sheet upload still installs its own card pool, and the chapter advance keeps its own transition; both differ from the four doors above. The PDF page-decision, correction-authority and scenario-storage candidates of the same review are separate changes.
