# Issue tracker: design specs in `docs/specs/` + GitHub PRs

This repo does not use GitHub Issues. Each unit of work is tracked as a design spec under `docs/specs/` and delivered through a GitHub PR.

## Conventions

- Follow the existing spec naming and organization in `docs/specs/`. Keep English and Traditional Chinese versions together when the surrounding specs use bilingual files.
- Link relevant spec files in the PR description under `## Specs`.
- Read a PR with `gh pr view <number> --comments`; inspect its changes with `gh pr diff <number>`.
- To find the relevant spec when none is linked, inspect `docs/specs/` changes in the PR's commit range with `git log --stat <base>..HEAD -- docs/specs`.
- Do not create GitHub Issues for work tracked by this repo.

## Pull requests as a request surface

**PRs as a request surface: no.** PRs are used to deliver and review changes, not as a replacement for work specs.

## Skill routing

- When a skill says to publish work to the issue tracker, create or update the relevant design spec under `docs/specs/` and link it from the PR; do not run `gh issue create`.
- When a skill asks for the relevant ticket, look for the `## Specs` section in the PR body, then inspect spec changes in the commit range.
- Do not use GitHub Issues as a task queue.
