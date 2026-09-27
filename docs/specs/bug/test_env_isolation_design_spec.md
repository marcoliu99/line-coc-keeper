# Test isolation from a checkout's .env

Status: implemented. Base: main_v2. Branch: bug/test-env-isolation.

## Problem and evidence

`bug/test-db-isolation` (PR #104) sandboxed the five storage paths but left configuration alone. `app/config.py` calls `load_dotenv()` at import, so every setting the suite asserts is first overwritten by whatever `.env` sits beside the checkout.

Run on merged main_v2 (`edd2fd6`) in a checkout holding the deployment `.env`, five tests fail:

```
tests/test_config_defaults.py::...::test_max_tool_iterations_default_is_five
tests/test_config_defaults.py::...::test_high_iteration_watermark_default_is_four
tests/test_llm_turn_wrapup.py::OpenAIHighIterationWatermarkTests::test_emits_event_when_iterations_reach_the_watermark
tests/test_llm_turn_wrapup.py::AnthropicHighIterationWatermarkTests::...
tests/test_llm_turn_wrapup.py::GeminiHighIterationWatermarkTests::...
```

The deployment `.env` sets `MAX_TOOL_ITERATIONS=12` and `HIGH_ITERATION_WATERMARK=7`; the tests pin the code defaults of 5 and 4. The failures are stable across reruns, so they are not the ordering problem PR #104 fixed. They appear in exactly the checkouts that carry a deployment `.env` — including the worktree the bot is run from — and disappear in a bare checkout, which is why PR #104's verification did not catch them.

Eight test modules already carried `sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))`. `setdefault` only takes effect when that module is imported before anything else pulls in `dotenv`, so the workaround's success depended on collection order.

## Scope

Replace `dotenv.load_dotenv` with a no-op in `tests/conftest.py`, which pytest imports before any test module and therefore before `app.config` resolves `from dotenv import load_dotenv`. Only that one function is replaced: `scripts/bot_lifecycle.py` reads `dotenv_values` and is covered by tests, so the module must stay intact. A guard raises if `app.config` is somehow already imported, rather than silently isolating nothing.

Not in scope: production code, the eight scattered `setdefault` lines (now inert, and those modules stub `yaml` and `app.pdf_loader` for unrelated reasons), and ambient environment variables exported in a developer's shell.

## Testing strategy

- `dotenv.load_dotenv` is the disabled loader, so restoring the real one fails the suite rather than quietly letting deployment values decide what "default" means.
- `config.MAX_TOOL_ITERATIONS` is 5 and `config.HIGH_ITERATION_WATERMARK` is 4, the two values this deployment's `.env` overrides.
- Whole suite run with a real deployment `.env` copied into the checkout.

## Verification

With the deployment `.env` present: 1,146 passed, 38 subtests, twice in a row. Without any `.env`: 1,146 passed. With the one-line patch reverted while keeping the guard tests, the same run fails 7 — the original 5 plus the 2 new guards. ruff, mypy and compileall pass.

## Limits

This covers a `.env` file only. Variables exported in the shell that runs pytest still reach `app.config`, and the same class of leak would return for any future setting read outside `config.py`. The guard asserts the loader is disabled, not that no configuration reached the process by another route.
