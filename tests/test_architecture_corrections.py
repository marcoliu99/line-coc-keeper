"""A correction report's status and shape are owned by ``narrative_corrections``.

A report is a player's claim until a ruling makes it something the game acts on, so the places
that open, rule on, withdraw or replace one must not be written twice. These checks fail if a
handler or service writes a report's status itself or builds its own report dictionary.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "app/services/narrative_corrections.py"
PRIVATE_COPIES = {"_find_report", "_active_reports", "_prune_adjudicated"}


def _production(*, about_corrections: bool = False) -> dict[str, ast.Module]:
    """Parsed ``app`` modules; ``about_corrections`` keeps those that handle correction reports
    (other modules keep a ``status`` of their own, for combat or OCR attempts)."""
    modules = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not about_corrections or "narrative_corrections" in source:
            modules[path.relative_to(ROOT).as_posix()] = ast.parse(source)
    return modules


def status_writes(tree: ast.Module) -> list[int]:
    """Lines assigning ``x["status"] = ...``."""
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
        and isinstance(node.slice, ast.Constant) and node.slice.value == "status"
    ]


def report_literals(tree: ast.Module) -> list[int]:
    """Lines building a dict that looks like a correction report."""
    return [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Dict)
        and {"status", "target_receipt"} <= {key.value for key in node.keys
                                             if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    ]


def test_only_the_lifecycle_module_writes_a_report_status() -> None:
    modules = _production(about_corrections=True)
    offenders = {path: status_writes(tree) for path, tree in modules.items()
                 if path != OWNER and status_writes(tree)}
    assert offenders == {}
    assert {"app/commands/handlers/correct.py", "app/services/natural_corrections.py"} <= set(modules)
    assert status_writes(modules[OWNER])


def test_only_the_lifecycle_module_builds_a_report() -> None:
    offenders = {path: report_literals(tree) for path, tree in _production(about_corrections=True).items()
                 if path != OWNER and report_literals(tree)}
    assert offenders == {}


def test_the_handler_has_no_private_copy_of_the_lifecycle_helpers() -> None:
    defined = {
        node.name for tree in _production().values() for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert defined.isdisjoint(PRIVATE_COPIES)


def test_the_gate_catches_a_stray_write() -> None:
    assert status_writes(ast.parse('report["status"] = "withdrawn"')) == [1]
    assert status_writes(ast.parse('value = report["status"]')) == []
    assert report_literals(ast.parse('x = {"status": "pending", "target_receipt": {}, "id": 1}')) == [1]
