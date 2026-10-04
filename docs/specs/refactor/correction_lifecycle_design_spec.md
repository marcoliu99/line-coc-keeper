# Correction lifecycle

[繁體中文](correction_lifecycle_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented**. Based on `main_v2` at `ee23aa9`; the third item of the 2026-10-05 architecture review ("Concentrate correction authority transitions"). Authority rules: [ADR 0001](../../adr/0001-keeper-adjudicates-corrections-without-kp.md) and [ADR 0002](../../adr/0002-separate-presentation-from-world-authority.md).

A player's correction is a claim; only a ruling by the KP Assistant or the Keeper makes it something the game acts on. The status of a report (`pending`, `unverified`, `approved`, `rejected`, `withdrawn`, `superseded`) was changed in four places: the rulings in `narrative_corrections`, but also `withdrawn`, `superseded` and the hold scope directly in the `/coc correct` handler, and a report was built twice (the command and the natural-language path, with different field order), with private copies of "find a report", "the reports of this timeline" and "prune closed reports" in the handler. A reader had to reconstruct, across modules, when a claim becomes authoritative.

## Contract

`narrative_corrections` now owns every change to a report:

| Operation | Moves |
| --- | --- |
| `new_report`, `file_report` | creates a `pending` report for the current timeline and files it (discarding reports of inactive timelines) |
| `record_unverified` | `pending` → `unverified` (the Keeper could not verify it) |
| `record_ruling` | `pending`/`unverified` → `approved` or `rejected` (KP Assistant or Keeper; the Keeper's carries its basis) |
| `record_presentation_repair` | `pending` → `approved` for a harmless presentation correction that is not promoted to world canon |
| `withdraw` | an open report → `withdrawn` |
| `supersede` | an `approved` report → `superseded` by another approved one; marks the log entries and queues a summary rebuild |
| `hold` | marks the names whose actions pause; never changes the status |
| `prune_closed`, `find_report`, `active` | bounded state size; lookup within the current timeline |

The allowed moves are a table (`TRANSITIONS`): `pending → unverified/approved/rejected/withdrawn`, `unverified → approved/rejected/withdrawn`, `approved → superseded`; rejected, withdrawn and superseded are terminal. A move the table does not allow raises instead of being applied.

The handler and `natural_corrections` parse, check permissions and caps, and word the replies.

## Contract kept

1. Command names, reply wording, the caps (12 pending per group and 3 per reporter for `/coc correct`; 10 and 3 for natural-language corrections, which are different on purpose today) and the stored shape of a report are unchanged; a report's fields are the same, only built in one place.
2. The authority rules are unchanged: allegations never create a mechanical hold by themselves, presentation repairs are not world facts, an approval is recorded and then published by `save` (archive, memory annotation, provider chain reset).
3. The only removed statement is a redundant `report["status"] = "pending"` after a failed inventory repair, on a report that was already pending.
4. The existing correction suites (`test_narrative_correction_command`, `test_narrative_correction_lifecycle`, `test_natural_corrections`, `test_keeper_adjudicates_corrections`) pass; two of them call `narrative_corrections.prune_closed` instead of the handler's private function.

## Enforcement

`tests/test_correction_lifecycle.py` checks every pair of statuses against the table and each operation. `tests/test_architecture_corrections.py` fails if a module that handles correction reports assigns `["status"]` itself or builds its own report dictionary, or if the handler grows private copies of the helpers again.

## Not changed

The Keeper's ruling (`correction_adjudication`: evidence, claim checks, spoiler screening), the projection of verified facts (`canonical_facts`) and the summary rebuild (`correction_summary`) keep their own modules. The two cap values above are left as they are; making them one policy would change behaviour.
