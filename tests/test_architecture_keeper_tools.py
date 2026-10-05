"""The Keeper tool handlers sit below the code that dispatches to them.

``app/tool_dispatch.py`` calls the handlers registered in ``app/keeper_tools``; a handler that imported
``tool_dispatch`` (or the turn runtime above it) would make that a cycle, which is what ``app/keeper.py`` used
to be: 12 modules importing each other through function-level imports. Handlers share
``app/keeper_tools/support.py`` instead.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HANDLERS = sorted((ROOT / "app" / "keeper_tools").glob("*.py"))

# Anything at or above the dispatcher, plus the transports.
FORBIDDEN = (
    "app.keeper",
    "app.tool_dispatch",
    "app.prompt_builder",
    "app.turn_commit",
    "app.memory_maintenance",
    "app.agents",
    "app.commands",
    "app.discord_bot",
    "app.providers",
)


def imported_modules(source: str) -> set[str]:
    """Every module named by an import anywhere in ``source``, function-level imports included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def violations(source: str) -> list[str]:
    return sorted(
        name for name in imported_modules(source)
        if any(name == banned or name.startswith(banned + ".") for banned in FORBIDDEN)
    )


@pytest.mark.parametrize("path", HANDLERS, ids=lambda p: p.name)
def test_a_tool_handler_does_not_import_the_dispatcher_or_anything_above_it(path):
    assert violations(path.read_text(encoding="utf-8")) == []


def test_the_rule_sees_a_function_level_import():
    lazy = "def handler():\n    from app import tool_dispatch\n    return tool_dispatch\n"
    assert violations(lazy) == ["app.tool_dispatch"]
    assert violations("from app.keeper_tools import support\n") == []
    assert violations("from app.keeper_tools import registry as tool_registry\nfrom app import dice\n") == []


def test_there_is_no_keeper_module_left_to_import():
    assert not (ROOT / "app" / "keeper.py").exists()
