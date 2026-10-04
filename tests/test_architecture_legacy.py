"""``legacy_commands`` is gone, not renamed (L1, L7).

Its duties went to their owners: upload orchestration to
``scenario_ingestion``, map work to ``map_service``, the investigator rules to
``character_service``, the plain-message commands to ``handlers/messages``,
checks to ``app/checks`` and battles to the combat engine. The checks below
would fail if the module came back under its own name, behind a lazy
``__getattr__``, through a dynamic import, or as a differently named shim.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RETIRED = "legacy_commands"
SHIM_NAMES = ("legacy_commands", "legacy_helpers", "legacy_shim", "legacy_compat", "commands_legacy")
NEW_OWNERS = (
    "app/services/scenario_ingestion.py",
    "app/services/map_service.py",
    "app/services/character_service.py",
)


def _python_files() -> list[Path]:
    return sorted(
        path for folder in ("app", "scripts", "tests") for path in (ROOT / folder).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def imports_retired_module(source: str) -> list[int]:
    """Lines that import the retired module, by statement or by ``import_module``/``__import__``."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(RETIRED in alias.name for alias in node.names) or isinstance(node, ast.ImportFrom) and (
            RETIRED in (node.module or "") or any(alias.name == RETIRED for alias in node.names)
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and RETIRED in first.value:
                    lines.append(node.lineno)
    return lines


def runtime_imports(source: str) -> set[str]:
    """Modules imported when the module loads, not only for type checking."""
    tree = ast.parse(source)
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and (
            (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING")
            or (isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING")
        ):
            skipped.update(id(child) for child in ast.walk(node))
    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_module_file_is_gone_and_no_shim_took_its_place():
    assert not (ROOT / "app" / "legacy_commands.py").exists()
    stems = {path.stem for path in (ROOT / "app").rglob("*.py")}
    assert not [stem for stem in stems if any(name in stem for name in SHIM_NAMES)]


def test_nothing_imports_it_in_production_scripts_or_tests():
    offenders = {}
    for path in _python_files():
        if path.name == Path(__file__).name:
            continue
        lines = imports_retired_module(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.relative_to(ROOT).as_posix()] = lines
    assert offenders == {}


def test_the_gate_catches_each_way_back_in():
    for source in (
        "import app.legacy_commands",
        "from app import legacy_commands",
        "from app.legacy_commands import handle_pdf_upload",
        "import importlib\nimportlib.import_module('app.legacy_commands')",
        "__import__('app.legacy_commands')",
    ):
        assert imports_retired_module(source), source
    assert imports_retired_module("from app.services import scenario_ingestion") == []


def test_no_package_resolves_names_lazily_on_first_use():
    """A module-level ``__getattr__`` in a package ``__init__`` is how an old name keeps working invisibly."""
    lazy = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "app").rglob("__init__.py")
        if any(
            isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
            for node in ast.parse(path.read_text(encoding="utf-8")).body
        )
    ]
    assert lazy == []


@pytest.mark.parametrize("path", NEW_OWNERS)
def test_the_new_services_do_not_depend_on_the_command_layer_at_runtime(path):
    """Commands call services; a service reaches the command package only for type names."""
    imports = runtime_imports((ROOT / path).read_text(encoding="utf-8"))
    assert not {name for name in imports if name == "app.commands" or name.startswith("app.commands.")}


def test_the_retired_module_cannot_be_imported():
    result = subprocess.run(
        [sys.executable, "-c", "import app.legacy_commands"], cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode != 0 and "ModuleNotFoundError" in result.stderr


@pytest.mark.parametrize("module", [
    "app.commands", "app.commands.router", "app.discord_bot", "app.services.scenario_ingestion",
    "app.services.map_service", "app.services.character_service", "app.commands.handlers.messages",
])
def test_every_entry_point_loads_without_it(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_command_package_exports_resolve_to_their_owners():
    from app import commands

    expected = {
        "handle_pdf_upload": "app.services.scenario_ingestion",
        "handle_role_sheet_upload": "app.services.scenario_ingestion",
        "handle_scenario_compare_upload": "app.services.scenario_ingestion",
        "handle_map_upload": "app.services.map_service",
        "handle_pregen_luck_roll": "app.commands.handlers.character",
        "handle_roll_command": "app.commands.handlers.messages",
        "handle_unsupported_message": "app.commands.handlers.messages",
        "resolve_pdf_upload_choice": "app.commands.handlers.uploads",
        "handle_check_command": "app.commands.handlers.checks",
        "handle_luck_decision": "app.commands.handlers.checks",
    }
    for name, owner in expected.items():
        assert getattr(commands, name).__module__ == owner, name
    assert set(commands.__all__) >= set(expected)
