"""Point every storage path at a throwaway directory before app is imported.

`app/config.py` resolves DATA_DIR, DB_PATH, BACKUP_DIR, SCENARIO_LIBRARY_DIR
and IMPORT_DIR at import time, relative to the current working directory, and
creates them. Tests write real rows through the real repositories, so without
this the suite reads and writes `data/coc_bot.db` of whatever checkout it runs
in — the live bot's database when run from the deployment worktree. It also
made reruns fail: a test that saved a state at revision 0 found revision 1
waiting on the second run and raised StateRevisionConflict.

pytest imports this file before any test module, so setting the environment
here happens before `app.config` runs. `load_dotenv()` does not override
variables that are already set, so a checkout's own `.env` cannot win.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="coc-tests-")).resolve()

# Explicit rather than relying on the derived defaults, so a later change to
# how one path is derived from another cannot quietly send writes back to the
# working directory.
os.environ["DATA_DIR"] = str(_ROOT / "groups")
os.environ["DB_PATH"] = str(_ROOT / "coc_bot.db")
os.environ["BACKUP_DIR"] = str(_ROOT / "backups")
os.environ["SCENARIO_LIBRARY_DIR"] = str(_ROOT / "scenarios")
os.environ["IMPORT_DIR"] = str(_ROOT / "imports")

atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)


def pytest_report_header(config):
    return f"storage sandbox: {_ROOT}"
