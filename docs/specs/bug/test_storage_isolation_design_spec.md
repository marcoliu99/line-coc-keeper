# Test storage isolation

Status: implemented. Base: main_v2. Branch: bug/test-db-isolation.

## Problem and evidence

The suite has no `conftest.py`. `app/config.py` resolves DATA_DIR, DB_PATH, BACKUP_DIR, SCENARIO_LIBRARY_DIR and IMPORT_DIR at import time, relative to the current working directory, and creates each one. Tests write real rows through the real repositories, so `pytest` reads and writes `data/coc_bot.db` of whichever checkout it runs in.

Two consequences, both reproduced 2026-09-27:

1. **Reruns fail.** `tests/test_narrative_correction_lifecycle.py` saves a state at revision 0 and reloads it. On a second run the row is already at revision 1, so `correct._save` raises `StateRevisionConflict: loaded=0, current=1`. Observed: first run 7 passed, second run 2 failed. A green suite therefore depends on nobody having run it in that directory before.
2. **The deployment database is reachable.** Run from the deployment worktree, the suite would write test group rows into the live bot's `data/coc_bot.db`. Inspected 2026-09-27: the live database holds only its two real `discord-channel-*` keys, so this has not happened, but nothing prevents it.

## Scope

Add `tests/conftest.py` that points all five storage paths at one throwaway directory before `app.config` is imported, and a guard test that fails if any of them lands inside the checkout.

pytest imports `conftest.py` before any test module, so the environment is set before `app.config` runs. `load_dotenv()` does not override variables that are already set, so a checkout's own `.env` cannot win. All five are set explicitly rather than relying on four of them being derived from DATA_DIR, so a later change to that derivation cannot quietly send writes back to the working directory.

The sandbox is created per session with `tempfile.mkdtemp` and removed at exit, which is what makes reruns independent. No production code changes, and no changes to the tests that were failing.

## Testing strategy

- No storage path is inside the checkout, checked per path.
- All five paths share one sandbox root, so a path left on its default is caught rather than silently escaping.
- `DB_PATH` is not the checkout's `data/coc_bot.db`.
- Whole suite run three times consecutively.

## Verification

Suite run three times in a row: 1,037 passed, 38 subtests, no failures, and no `data/` or `imports/` directory created in the checkout. With `conftest.py` temporarily removed, the same file passes 7 on the first run and fails 2 on the second, and `data/` reappears with `coc_bot.db`, `groups`, `scenarios` and `backups`.

## Limits

This isolates storage paths only. Tests that share other process-wide state — module caches, context variables, patched globals — are not addressed, and ordering dependencies between them would still exist. The guard test checks where paths point, not that every write actually goes through them: code that hardcodes a path instead of reading config would bypass it.
