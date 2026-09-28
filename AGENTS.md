# Agent guide

Read before writing or reviewing code: `CODING_STANDARDS.md` (layering, game-state and comment rules that tooling can't check). README.md `## Architecture` maps the modules.

Checks: `.github/workflows/ci.yml` lists the commands; run the same ones locally. A local `.env` overrides config defaults, so `tests/test_config_defaults.py` and `tests/test_llm_turn_wrapup.py` fail while `MAX_TOOL_ITERATIONS` or `HIGH_ITERATION_WATERMARK` are set there. That's an environment difference, not a regression.

## Agent skills

### Issue tracker

Work is tracked as design specs under `docs/specs/<category>/` (EN + `_zh`), linked from PRs; GitHub Issues are not used. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (created lazily). See `docs/agents/domain.md`.
