# Documentation alignment and bilingual organization

## Goal and baseline

Align the existing `docs/spec-alignment-and-readme` branch with `main_v2` at
`afe8ace`. Audit every specification against application code and regression tests,
then make current behavior, remaining work, and historical decisions distinguishable.
This is a documentation maintenance task; it does not implement backlog features.

## Layout

- `docs/specs/bug/`: defect contracts and regression expectations.
- `docs/specs/enhancement/`: improvements to existing behavior.
- `docs/specs/feature/`: product capabilities and domain contracts.
- `docs/specs/refactor/`: architecture and flow migrations.
- `docs/specs/maintenance/`: documentation and repository maintenance.
- `docs/references/`: command/API/rules/authoring references, not implementation specs.
- `docs/guides/`: installation and gameplay instructions.
- `docs/evaluations/`: original measurements; preserve raw artifacts and their scope.
- `docs/README.md` and `docs/README_zh.md`: category navigation and audited status index.

English uses the canonical `.md` name; Traditional Chinese uses `_zh.md` next to
it. Language partners link to each other. Keep executable commands, API identifiers,
configuration keys, and required parser labels unchanged. Chinese in quoted player
input or localization fixtures must be identified as an example rather than silently
changing the application's accepted syntax.

## Audit contract

For every spec, record the baseline commit, status, implementation/test evidence,
and any superseding document. A merged PR does not prove every old proposal shipped.
Use implemented, partial, backlog, superseded, or historical status as appropriate.
Import the three remaining spec-only enhancement branches into the catalog without
claiming they are implemented or merging their branches. Preserve measurement sample
sizes, model/effort settings and historical test counts as historical evidence.

    main_v2 -> code/test audit -> status and gap corrections
            -> category moves -> English/Chinese partners -> link verification
            -> full regression suite (documentation-sensitive consumers)

## Compatibility and validation

Update README links, cross-document links, and source/test documentation references.
Check local Markdown paths and language-pair links after moves. Any test reading a
localized reference must read the correct language version. Do not change prompt
text, runtime behavior, credentials, installed dependencies, or evaluation artifacts.
Run the existing suite in isolated storage to catch documentation path dependencies.

The long historical changelog's translation scope is being clarified separately;
current specifications and references are included in the bilingual audit.
