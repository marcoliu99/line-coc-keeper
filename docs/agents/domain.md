# Domain Docs

How engineering skills should consume this repo's domain documentation.

## Before exploring

- Read the root `CONTEXT.md` if it exists.
- Read `CONTEXT-MAP.md` instead if it exists, then read the relevant context files it lists.
- Read ADRs in `docs/adr/` that concern the area being changed.
- If these files do not exist, continue without treating their absence as a blocker. Create them when domain terms or decisions need to be recorded.

## Layout

This is a single-context repo:

- `CONTEXT.md`: shared project vocabulary and domain concepts.
- `docs/adr/`: project-wide architecture decisions.

Use the vocabulary in `CONTEXT.md` in issue titles, proposals, hypotheses, and tests. If a change conflicts with an ADR, call out the conflict instead of silently overriding it.
