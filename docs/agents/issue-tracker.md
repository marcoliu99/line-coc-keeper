# Issue tracker: design specs in docs/specs + GitHub PRs

This repo does not use GitHub Issues. Each unit of work is a design spec committed to the repo and delivered through a PR.

## Layout

- Specs live at `docs/specs/<category>/<name>.md`, where `<category>` is one of `bug`, `enhancement`, `feature`, `maintenance`, `refactor`.
- Every English spec has a Traditional Chinese twin, `<name>_zh.md`. Keep both in sync when either changes.
- `docs/specs/catalog.json` indexes every spec: `title`, `zh`, `category`, `status`, `evidence` (implementing files/tests), `path`, `zh_path`. Add or update the entry whenever a spec is created or its status changes.
- Branch names mirror the category: `bug/…`, `enhancement/…`, `feature/…`, `refactor/…`, `maintenance/…`. PRs target `main_v2`.

## When a skill says "publish to the issue tracker"

Write a new spec at `docs/specs/<category>/<name>.md` plus its `_zh` twin, and add a `catalog.json` entry with `status: "backlog"`. Use a spec file, not `gh issue create`.

## When a skill says "fetch the relevant ticket"

Resolve the spec in this order:

1. The `## Specs` section of the PR body (`gh pr view <n> --json body`), which lists spec paths.
2. Spec paths touched by commits in the range (`git log --stat <base>..HEAD -- docs/specs`), usually a `docs:` commit before the implementation.
3. A `catalog.json` entry whose title/path matches the branch name, or whose `evidence` lists the changed files.

Treat the English spec as canonical; consult `_zh` only for wording disputes.

## Pull requests

- **Read a PR**: `gh pr view <n> --comments`; diff with `gh pr diff <n>`.
- PR bodies use `## Summary`, `## Verification`, and `## Specs` sections.
- PRs as a request surface: **no**.
