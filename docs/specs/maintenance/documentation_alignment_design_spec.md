# Bilingual specification alignment

[繁體中文](documentation_alignment_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `maintenance`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Organize specs by bug, enhancement, feature, refactor and maintenance; keep references, guides and raw evaluations separate. Every spec has equal English and Traditional Chinese content.

2. Each current edition names its baseline, implementation/test evidence and status. Implemented, partial, backlog, superseded and historical are distinct; a merged PR alone cannot establish completion.

3. Old proposal code, migration chronology and past measurements remain accessible at the immutable original source revision. Current contracts do not present those historical examples as live implementation.

4. English filenames are canonical and _zh.md partners share sections, flows and acceptance evidence. Update local links and documentation-sensitive test readers; do not change runtime behavior.

## Flow and interfaces

```text
main_v2 baseline -> code/test audit -> current bilingual contracts -> category/link validation
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [README.md](../../../README.md)
- [README_zh.md](../../../README_zh.md)
- [docs/README.md](../../README.md)
- [docs/README_zh.md](../../README_zh.md)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/dfd4838/docs/documentation_alignment_design_spec.md)

## Alignment verification (2026-09-27)

- 62 specification pairs (124 files): 55 implemented, 1 partial, 3 backlog, 2 superseded and 1 historical.
- Every catalog entry has an English/Traditional Chinese pair with matching requirement counts; all local Markdown links and implementation/test evidence paths resolve.
- Corrected stale legacy Keeper entry points, fixed Help command counts, nonexistent example modules, character matching gates and pending-correction hold semantics. Kept proposed batch search and three unmerged enhancements explicitly unimplemented.
- Full isolated regression suite: 910 passed, 1 skipped, 33 subtests passed. Live API benchmarking was not part of this documentation audit.
- Python AST comparison (ignoring docstrings) confirmed no runtime changes. The only executable test change points the generated Help-reference comparison to its new Chinese path.

When changing behavior, update both language files and the catalog in the same commit. Preserve technical identifiers and parser-required Chinese literals in English templates. Generate the Chinese command reference with `python3 -m app.help_docs --output docs/references/player_command_reference_zh.md`, then update its English partner.

Historical changelog text is preserved under `docs/history/changelog_zh.md`; the current changelog is a bilingual summary. Historical specification appendices are preserved at the immutable Git revisions linked by each current edition, rather than represented as current requirements.
