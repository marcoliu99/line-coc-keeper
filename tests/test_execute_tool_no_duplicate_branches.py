import ast
import unittest
from collections import Counter
from pathlib import Path

KEEPER_PATH = Path(__file__).resolve().parent.parent / "app" / "keeper.py"


def _find_execute_tool(tree: ast.Module) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_tool":
            return node
    raise AssertionError("app/keeper.py has no _execute_tool function anymore")


def _tool_name_branches(func: ast.FunctionDef) -> list[str]:
    """Collect every `if name == "...":` literal this dispatch function checks.

    Only matches the exact `name == "<literal>"` shape _execute_tool's tool
    dispatch uses — not `if name in (...)`, which legitimately handles more
    than one tool name inside a single shared branch (e.g.
    record_established_fact/record_clue) and isn't what this guards against.
    """
    names = []
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue
        left = test.left
        comparator = test.comparators[0]
        if isinstance(left, ast.Name) and left.id == "name" and isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
            names.append(comparator.value)
    return names


class ExecuteToolNoDuplicateBranchesTests(unittest.TestCase):
    """Regression guard for the dead-code duplication cleaned up in
    docs/dead_combat_tool_branches_design_spec.md — a merge/rebase that
    keeps both sides of a conflict on a `if name == "X":` branch is a silent
    dead-code bug (the first copy always wins), so this fails loudly instead.
    """

    def test_every_tool_name_dispatched_at_most_once(self):
        tree = ast.parse(KEEPER_PATH.read_text(encoding="utf-8"), filename=str(KEEPER_PATH))
        func = _find_execute_tool(tree)
        counts = Counter(_tool_name_branches(func))
        duplicates = {name: count for name, count in counts.items() if count > 1}
        self.assertEqual(
            duplicates, {},
            f"_execute_tool has duplicate `if name == ...:` branches: {duplicates} "
            "— the later copy is unreachable dead code (the first one always returns first).",
        )


if __name__ == "__main__":
    unittest.main()
