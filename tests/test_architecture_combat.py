"""The combat modules form layers, and only the engine picks a battle's mode (B10).

``combat_rules`` and ``combat_resources`` are leaves. ``combat`` builds on
``combat_resources``; ``combat_flow`` builds on ``combat``; ``CombatEngine``
sits above both and is the only module that imports ``combat_flow``. The check
is an AST walk over every import in every production module — function-level
(lazy) imports included — followed to a fixed point, so a cycle hidden behind a
lazy import or a third module still fails.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FLOW = "app.combat_flow"
CORE = "app.combat"
RESOURCES = "app.combat_resources"
RULES = "app.combat_rules"
ENGINE = "app.services.combat_engine"


def _module_name(path: Path) -> str:
    name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    return name.removesuffix(".__init__")


def _production_modules() -> dict[str, Path]:
    return {_module_name(path): path for path in sorted((ROOT / "app").rglob("*.py"))}


def imports_of(source: str, module: str, known: set[str], *, is_package: bool = False) -> set[str]:
    """Every project module ``source`` imports anywhere in it, lazily or not."""
    package = module if is_package else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name in known)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*parts, *([node.module] if node.module else [])])
            if base in known:
                found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in known)
    return found


def import_graph() -> dict[str, set[str]]:
    modules = _production_modules()
    known = set(modules)
    return {
        name: imports_of(path.read_text(encoding="utf-8"), name, known, is_package=path.name == "__init__.py")
        for name, path in modules.items()
    }


def reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        for target in graph.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


@pytest.fixture(scope="module")
def graph() -> dict[str, set[str]]:
    return import_graph()


def test_the_leaves_reach_neither_the_rules_that_use_them_nor_the_engine(graph):
    for leaf in (RULES, RESOURCES):
        assert not {CORE, FLOW, ENGINE} & reachable(graph, leaf), leaf


def test_combat_never_reaches_the_managed_pipeline_or_the_engine(graph):
    assert not {FLOW, ENGINE} & reachable(graph, CORE)


def test_the_managed_pipeline_builds_on_combat_and_never_on_the_engine_or_a_transport(graph):
    reached = reachable(graph, FLOW)
    assert CORE in reached
    assert ENGINE not in reached
    for forbidden in ("app.discord_bot", "app.tool_dispatch", "app.prompt_builder", "app.turn_commit", "app.memory_maintenance", "app.commands", "app.agents", "app.providers"):
        assert not {m for m in reached if m == forbidden or m.startswith(forbidden + ".")}, forbidden


def test_the_models_do_not_reach_into_combat_code(graph):
    assert not {CORE, FLOW, RESOURCES, RULES, ENGINE} & reachable(graph, "app.models")


def test_only_the_engine_imports_the_managed_pipeline(graph):
    importers = {module for module, targets in graph.items() if FLOW in targets}
    assert importers == {ENGINE}


def test_no_combat_module_is_part_of_an_import_cycle(graph):
    family = {CORE, FLOW, RESOURCES, RULES, ENGINE, "app.services.combat_actions", "app.services.managed_checks"}
    for module in family:
        assert module not in reachable(graph, module), f"{module} imports itself through a cycle"


def _functions_referencing(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owners: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr == name:
                owners.add(self.stack[-1] if self.stack else "<module>")
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if node.id == name:
                owners.add(self.stack[-1] if self.stack else "<module>")

    Visitor().visit(tree)
    return owners


def test_the_combat_rules_do_not_branch_on_the_mode():
    """``combat.py`` never asks whether a battle is managed; the engine decides and passes the ops down."""
    assert not _functions_referencing(ROOT / "app" / "combat.py", "is_managed")
    assert not _functions_referencing(ROOT / "app" / "combat.py", "pipeline_version")


def test_the_engine_reads_the_mode_in_one_place():
    path = ROOT / "app" / "services" / "combat_engine.py"
    assert _functions_referencing(path, "is_managed") == {"mode_of"}
    assert _functions_referencing(path, "mode_of") <= {"handle"}


MODE_SENSITIVE = frozenset({
    "advance_turn", "plan_enemy_turn", "process_timing", "apply_combat_damage", "damage_combatant",
    "begin_combat", "add_combatant", "resolve_enemy_action", "finish_retired_current_turn", "status_text",
})


def test_only_the_engine_and_the_pipeline_call_the_mode_sensitive_combat_rules():
    """Commands, tools and the check engine reach them through ``CombatEngine.handle``."""
    callers: dict[str, set[str]] = {}
    for module, path in _production_modules().items():
        if module == CORE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute) and node.attr in MODE_SENSITIVE
                and isinstance(node.value, ast.Name) and node.value.id == "combat"
            ):
                callers.setdefault(module, set()).add(node.attr)
    assert set(callers) <= {ENGINE, FLOW}, callers


def test_the_gate_catches_a_lazy_import_cycle():
    source = "def late():\n    from app import combat_flow\n"
    assert FLOW in imports_of(source, CORE, {"app", FLOW, CORE})


@pytest.mark.parametrize("module", [
    CORE, FLOW, RESOURCES, RULES, ENGINE, "app.tool_dispatch", "app.prompt_builder", "app.turn_commit", "app.memory_maintenance", "app.commands.router", "app.keeper_tools.registry",
])
def test_each_module_imports_in_a_fresh_process_in_either_order(module):
    """No cycle is hidden by import order: the module must load first, in a new interpreter."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
