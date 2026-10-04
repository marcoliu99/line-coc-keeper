"""Dependency direction and de-duplication for the check engine.

``commands / buttons / tools -> check service -> state transaction -> repository``.
The engine decides and applies rules to the state a transaction hands it; it
never reaches up into a transport, the Keeper or an LLM provider, and it does
not import the combat code (combat-owned checks arrive through the
``ManagedChecks`` port instead).
"""
from __future__ import annotations

import ast
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Modules the check engine (app/checks) must not import, directly or lazily.
FORBIDDEN_FOR_ENGINE = (
    "discord",
    "app.discord_bot",
    "app.commands",
    "app.legacy_commands",
    "app.keeper",
    "app.agents",
    "app.providers",
    "app.combat",
    "app.combat_flow",
    "app.combat_resources",
    "app.services.managed_checks",
)

# Check logic that used to be written twice (or three times). Each name may be
# defined at most once in production code, and only inside app/checks.
SINGLE_OWNER_FUNCTIONS = {
    "persist_resolved_event": "app/checks/events.py",
    "persist_consequence_origin": "app/checks/events.py",
    "build_check_narration": "app/checks/narration.py",
    "resolve_ranged_defense_outcome": "app/checks/rules.py",
    "resolve_player_check": "app/checks/service.py",
    "resolve_luck_decision": "app/checks/service.py",
}
# Names the old code defined in other modules. They must be gone, not renamed.
RETIRED_FUNCTIONS = frozenset({
    "_persist_resolved_check_event",
    "_persist_check_consequence_origin",
    "_resolved_check_event_seed",
    "_resolve_ranged_defense_outcome",
    "_build_check_narration",
    "_build_split_check_feedback",
    "_resolve_check_deterministically",
    "_resolve_luck_decision_deterministically",
    "_resolve_managed_check",
    "_resolve_managed_luck",
    "_finalize_check_result",
    "_tier_zh_for_tier",
})


def imported_modules(source: str, path: str = "<source>") -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def engine_violations(source: str, path: str) -> list[str]:
    return [
        f"{path} imports {module}"
        for module in sorted(imported_modules(source, path))
        if any(module == banned or module.startswith(f"{banned}.") for banned in FORBIDDEN_FOR_ENGINE)
    ]


def defined_functions(source: str, path: str) -> set[str]:
    return {
        node.name for node in ast.walk(ast.parse(source, filename=path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class CheckEngineDependencyTests(unittest.TestCase):
    def test_engine_modules_do_not_reach_into_transport_keeper_providers_or_combat(self):
        violations: list[str] = []
        for path in sorted((ROOT / "app" / "checks").glob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            violations += engine_violations(path.read_text(encoding="utf-8"), relative)
        self.assertEqual(violations, [])

    def test_the_gate_catches_a_real_violation(self):
        source = textwrap.dedent("""
            def late():
                from app import combat_flow
                import discord
        """)
        self.assertEqual(
            sorted(engine_violations(source, "app/checks/fake.py")),
            ["app/checks/fake.py imports app.combat_flow", "app/checks/fake.py imports discord"],
        )


class CheckLogicHasOneOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definitions: dict[str, list[str]] = {}
        for path in sorted((ROOT / "app").rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            for name in defined_functions(path.read_text(encoding="utf-8"), relative):
                self.definitions.setdefault(name, []).append(relative)

    def test_each_check_function_is_defined_once_in_its_owner(self):
        for name, owner in SINGLE_OWNER_FUNCTIONS.items():
            with self.subTest(name=name):
                self.assertEqual(self.definitions.get(name), [owner])

    def test_the_old_duplicates_are_deleted_not_renamed(self):
        leftovers = {name: paths for name, paths in self.definitions.items() if name in RETIRED_FUNCTIONS}
        self.assertEqual(leftovers, {})


if __name__ == "__main__":
    unittest.main()
