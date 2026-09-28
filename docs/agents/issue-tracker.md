# Issue tracker: GitHub

Issues for this repo live in GitHub Issues. Use the `gh` CLI for all operations.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open`
- Comment: `gh issue comment <number> --body "..."`
- Apply or remove labels with `gh issue edit`.
- Close with `gh issue close <number> --comment "..."`.
- Infer the repository from `git remote -v`; `gh` does this automatically inside the clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

If this repo later treats external PRs as feature requests, change this flag to `yes` before using `/triage` on PRs.

## Skill routing

- When a skill says to publish work to the issue tracker, create a GitHub issue.
- When a skill asks for the relevant ticket, use `gh issue view <number> --comments`.
- Do not treat pull requests as issues for triage.
