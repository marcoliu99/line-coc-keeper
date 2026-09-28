# Agent guide

Read before writing or reviewing code: `CODING_STANDARDS.md` (layering, game-state and comment rules that tooling can't check). README.md `## Architecture` maps the modules.

Checks: `.github/workflows/ci.yml` lists the commands; run the same ones locally.

## Agent skills

### Issue tracker

Work is tracked as design specs under `docs/specs/<category>/` (EN + `_zh`), linked from PRs; GitHub Issues are not used. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (created lazily). See `docs/agents/domain.md`.
