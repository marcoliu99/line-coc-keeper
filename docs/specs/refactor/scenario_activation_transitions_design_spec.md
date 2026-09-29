# Scenario activation and chapter transitions

[繁體中文](scenario_activation_transitions_design_spec_zh.md)

Status: **in progress**. Base: `main_v2` at `07d55a7`.

## Current behavior

First PDF upload, upload-choice resolution, `/coc scenario use`, chapter advance, and checkpoint rollback each install scenario context or rebuild page images. The first four currently refresh derived images before the authoritative SQLite commit; rollback commits first. These paths retain different timeline, map, pregen, manual-card, and position policies.

## Interface

`app/scenario_activation.py` owns context-field installation and the commit-before-image publication sequence. Callers supply their existing SQLite commit function, variant and preservation policy, and context; the module returns whether the derived image cache was refreshed. Image failure after commit is reported as a refresh failure, never as a failed state transition or an invitation to repeat it. A failed commit does not publish images. The shared image publisher clears and copies the selected chapter's pages using the existing library and file repository; rollback may tolerate a missing historical library entry.

The transition-specific policy remains explicit at each caller: upload new versus correction, library selection, chapter advance, and rollback retain their existing state resets, manual role-card transaction, variant selection, position and map rules, and receipts. No database adapter is introduced. The authority boundary remains real SQLite plus file-system images.

## Verification

With a real temporary SQLite database and image directory, exercise each transition's commit failure and image failure. Verify state is unchanged if commit fails, state remains committed if only image refresh fails, and old page images are not published before a successful commit. Preserve existing state persistence and manual pregen tests, then run full pytest, Ruff 0.16.8, mypy, and compileall.
