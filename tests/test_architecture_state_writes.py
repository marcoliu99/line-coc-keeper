"""Game-state writes have exactly one door: ``app.repositories.state_transaction``.

An AST check (not a text search) over every production module and script. It
follows import aliases, so ``from ... import save_state as persist``,
``import ...group_state as gs; gs.save_state(...)`` and
``from app.repositories import group_state`` are all caught. It also rejects
direct ``db.set_json``/``db.set_json_tx``/``db.delete_json*`` calls aimed at the
game-state tables and raw ``INSERT/UPDATE/DELETE`` SQL against them.

Writes to other tables (checkpoints, scene digests, memory chunks, manual
pregen assets, correction archives) are not game state and stay legal; those
that must be atomic with a state change join the transaction through
``ctx.conn`` / ``state_transaction.ambient``.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Low-level symbols that write (or prepare a write of) a ``GroupState`` row.
FORBIDDEN_SYMBOLS = frozenset({"save_state", "write_state_tx", "_save_state_unlocked", "_save_state_impl"})
GAME_STATE_TABLES = frozenset({"group_states", "characters"})
DB_WRITERS = {"set_json": 1, "delete_json": 1, "set_json_tx": 2, "delete_json_tx": 2}
GROUP_STATE_MODULES = {"app.repositories.group_state"}
SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+(group_states|characters)\b",
    re.IGNORECASE,
)

# The storage boundary itself. Nothing else under app/ may touch these symbols.
PRODUCTION_ALLOWLIST = frozenset({
    "app/repositories/group_state.py",
    "app/repositories/state_transaction.py",
})
# One-time operator scripts that talk to the database directly, with the reason.
SCRIPT_ALLOWLIST = {
    "scripts/migrate_json_to_sqlite.py": "one-time import of the pre-SQLite JSON files",
    "scripts/migrate_skill_names.py": "offline data migration run by an operator against a stopped bot",
    "scripts/benchmark_state_mirrors.py": "benchmark that seeds mirror rows directly",
    "scripts/codex_pipeline_fixture.py": "operator smoke run that seeds an isolated conversation",
    "scripts/codex_smoke_check.py": "operator smoke run that seeds an isolated conversation",
}


def violations_in_source(source: str, path: str) -> list[str]:
    """Every way ``source`` writes game state outside the transaction module."""
    tree = ast.parse(source, filename=path)
    found: list[str] = []
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets if isinstance(target, ast.Name)
    }
    module_aliases: set[str] = set()
    symbol_aliases: set[str] = set()
    db_aliases: set[str] = {"db"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported = f"{node.module}.{alias.name}"
                if node.module in GROUP_STATE_MODULES and alias.name in FORBIDDEN_SYMBOLS:
                    symbol_aliases.add(alias.asname or alias.name)
                    found.append(f"{path}:{node.lineno} imports {alias.name}")
                if imported in GROUP_STATE_MODULES:
                    module_aliases.add(alias.asname or alias.name)
                if imported == "app.db":
                    db_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in GROUP_STATE_MODULES:
                    module_aliases.add(alias.asname or alias.name.rsplit(".", 1)[-1])
                if alias.name == "app.db":
                    db_aliases.add(alias.asname or "db")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_SYMBOLS:
            found.append(f"{path}:{node.lineno} uses .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in symbol_aliases and isinstance(node.ctx, ast.Load):
            found.append(f"{path}:{node.lineno} calls alias {node.id}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in db_aliases
                and node.func.attr in DB_WRITERS
            ):
                table_index = DB_WRITERS[node.func.attr] - 1
                table = node.args[table_index] if len(node.args) > table_index else None
                if isinstance(table, ast.Name) and table.id in constants:
                    table = ast.Constant(constants[table.id])
                if not (isinstance(table, ast.Constant) and isinstance(table.value, str)):
                    found.append(f"{path}:{node.lineno} db.{node.func.attr} with a non-literal table")
                elif table.value in GAME_STATE_TABLES:
                    found.append(f"{path}:{node.lineno} db.{node.func.attr} on {table.value}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_WRITE.search(node.value):
            found.append(f"{path}:{node.lineno} raw SQL write to a game-state table")
    # A module alias on its own is fine; only the forbidden attribute is not.
    return sorted(set(found))


def _python_files(directory: str) -> list[Path]:
    return sorted(
        path for path in (ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts and "experiments" not in path.parts
    )


class StateWriteBoundaryTests(unittest.TestCase):
    def test_production_modules_never_write_game_state_directly(self):
        offenders: list[str] = []
        for path in _python_files("app"):
            relative = path.relative_to(ROOT).as_posix()
            if relative in PRODUCTION_ALLOWLIST:
                continue
            offenders.extend(violations_in_source(path.read_text(encoding="utf-8"), relative))
        self.assertEqual(offenders, [], "write game state through app.repositories.state_transaction")

    def test_only_listed_scripts_write_game_state_and_each_still_needs_the_exemption(self):
        offenders: list[str] = []
        needed: set[str] = set()
        for path in _python_files("scripts"):
            relative = path.relative_to(ROOT).as_posix()
            problems = violations_in_source(path.read_text(encoding="utf-8"), relative)
            if not problems:
                continue
            needed.add(relative)
            if relative not in SCRIPT_ALLOWLIST:
                offenders.extend(problems)
        self.assertEqual(offenders, [])
        self.assertEqual(
            needed, set(SCRIPT_ALLOWLIST),
            "remove scripts from SCRIPT_ALLOWLIST once they stop writing game state directly",
        )

    def test_allowlisted_storage_modules_exist(self):
        for relative in PRODUCTION_ALLOWLIST:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_the_checker_catches_aliased_and_indirect_writes(self):
        cases = {
            "direct import": "from app.repositories.group_state import save_state\nsave_state(s)\n",
            "aliased import": "from app.repositories.group_state import save_state as persist\npersist(s)\n",
            "module attribute": "from app.repositories import group_state\ngroup_state.save_state(s)\n",
            "module alias": "import app.repositories.group_state as gs\ngs.write_state_tx(s)\n",
            "foreign attribute": "def f(m, s):\n    m._save_state_unlocked(s, conn=None)\n",
            "db literal": "from app import db\ndb.set_json('group_states', 'k', {})\n",
            "db tx literal": "from app import db\ndb.set_json_tx(conn, 'characters', 'k', {})\n",
            "db delete": "from app import db\ndb.delete_json('group_states', 'k')\n",
            "db unknown table": "from app import db\ndb.set_json(table, 'k', {})\n",
            "raw sql": "conn.execute(\"UPDATE group_states SET data = ? WHERE key = ?\", (1, 2))\n",
            "raw sql insert": "conn.execute('insert or replace into characters (key, data) values (?, ?)')\n",
        }
        for name, source in cases.items():
            with self.subTest(name):
                self.assertTrue(violations_in_source(source, "x.py"), name)

    def test_the_checker_allows_non_game_state_writes_and_reads(self):
        source = textwrap.dedent(
            """
            from app import db
            from app.repositories import state_transaction
            from app.repositories.group_state import load_state, load_page_image
            db.set_json('memory_chunks', 'k', {})
            db.set_json_tx(conn, 'state_checkpoints', 'k', {})
            db.delete_json('scenario_indexes', 'k')
            db.get_json('group_states', 'k')
            conn.execute('SELECT data FROM group_states WHERE key = ?', (1,))
            state_transaction.mutate('c', lambda ctx: None)
            """
        )
        self.assertEqual(violations_in_source(source, "x.py"), [])


class ImportSmokeTests(unittest.TestCase):
    """AST rules do not prove the import graph; a fresh interpreter does."""

    ENTRY_POINTS = (
        "app.repositories.state_transaction",
        "app.keeper",
        "app.commands.router",
        "app.agents.supervisor",
        "app.discord_bot",
        "app.checkpoints",
    )

    def test_every_entry_point_imports_in_a_fresh_process(self):
        script = "import importlib, sys\nfor name in sys.argv[1:]:\n    importlib.import_module(name)\n"
        for name in self.ENTRY_POINTS:
            with self.subTest(module=name):
                completed = subprocess.run(
                    [sys.executable, "-c", script, name], cwd=ROOT, capture_output=True,
                    text=True, timeout=120, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
