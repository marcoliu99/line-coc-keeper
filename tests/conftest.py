"""Isolate the suite from the checkout it runs in, before app is imported.

Two leaks, both resolved here because pytest imports this file before any test
module and therefore before `app.config` runs.

First, `app/config.py` resolves DATA_DIR, DB_PATH, BACKUP_DIR,
SCENARIO_LIBRARY_DIR and IMPORT_DIR at import time, relative to the current
working directory, and creates them. Tests write real rows through the real
repositories, so without this the suite reads and writes `data/coc_bot.db` of
whatever checkout it runs in — the live bot's database when run from the
deployment worktree. It also made reruns fail: a test that saved a state at
revision 0 found revision 1 waiting on the second run and raised
StateRevisionConflict.

Second, `app/config.py` calls `load_dotenv()`, so any `.env` beside the
checkout is applied to every setting the suite then asserts. Tests that pin a
code default — MAX_TOOL_ITERATIONS is 5, HIGH_ITERATION_WATERMARK is 4 — fail
in exactly the checkouts that have a deployment `.env`, which includes the one
the bot is run from. Eight test modules already worked around this with
`sys.modules.setdefault("dotenv", ...)`, but setdefault only wins when that
module happens to be imported first, so whether the suite passed depended on
collection order.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import dotenv

# Everything below only works ahead of app.config's own import-time work.
if "app.config" in sys.modules:  # pragma: no cover - import-order guard
    raise RuntimeError("app.config was imported before conftest could isolate it")


def _load_dotenv_disabled(*_args, **_kwargs) -> bool:
    """Ignore a checkout's .env: tests assert code defaults, not deployment."""
    return False


# Only this one function is replaced, ahead of app.config's
# `from dotenv import load_dotenv`. scripts/bot_lifecycle.py reads
# dotenv_values and is covered by tests, so the module must stay intact.
dotenv.load_dotenv = _load_dotenv_disabled

_ROOT = Path(tempfile.mkdtemp(prefix="coc-tests-")).resolve()

# Set explicitly rather than relying on four of them being derived from
# DATA_DIR, so a later change to that derivation cannot quietly send writes
# back to the working directory.
os.environ["DATA_DIR"] = str(_ROOT / "groups")
os.environ["DB_PATH"] = str(_ROOT / "coc_bot.db")
os.environ["BACKUP_DIR"] = str(_ROOT / "backups")
os.environ["SCENARIO_LIBRARY_DIR"] = str(_ROOT / "scenarios")
os.environ["IMPORT_DIR"] = str(_ROOT / "imports")

atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)


def pytest_report_header(config):
    return f"storage sandbox: {_ROOT}\ndotenv: disabled (tests assert code defaults)"
