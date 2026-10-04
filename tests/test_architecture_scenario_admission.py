"""The scenario admission sequence is written once.

Installing a library scenario, retiring the old cards, committing and only then publishing
page images used to be repeated by the PDF door, the Markdown door and ``/coc scenario use``.
These checks fail if a door grows its own copy again.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Callable name -> the production files that may call it.
OWNED_CALLS = {
    "commit_and_refresh": {"app/services/scenario_admission.py"},
    "install_context_fields": {
        "app/services/scenario_admission.py",
        "app/keeper_tools/scenario.py",  # advancing a chapter keeps the cast and the maps
    },
}
# Defined once, in the admission module, and gone from the doors.
SINGLE_OWNER = {
    "apply_new_scenario": "app/services/scenario_admission.py",
    "apply_scenario_correction": "app/services/scenario_admission.py",
    "install_library_context": "app/services/scenario_admission.py",
    "replace_scene_maps_preserving_locations": "app/services/scenario_admission.py",
}
RETIRED = {
    "_apply_new_scenario", "_apply_scenario_correction", "_install_library_context",
    "_merge_extracted_pregens", "_replace_scene_maps_preserving_locations",
}


def _production() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted((ROOT / "app").rglob("*.py"))
    ]


def _call_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")


def test_only_the_admission_module_commits_a_scenario_and_publishes_its_images() -> None:
    callers: dict[str, set[str]] = {name: set() for name in OWNED_CALLS}
    for path, tree in _production():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in OWNED_CALLS:
                callers[_call_name(node)].add(path)
    for name, allowed in OWNED_CALLS.items():
        assert callers[name] <= allowed, (name, sorted(callers[name] - allowed))
        assert callers[name], f"{name} is no longer called anywhere"


def test_the_state_transitions_are_defined_once_and_the_old_private_copies_are_gone() -> None:
    defined: dict[str, list[str]] = {}
    for path, tree in _production():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.setdefault(node.name, []).append(path)
    for name, owner in SINGLE_OWNER.items():
        assert defined.get(name) == [owner], name
    assert {name: paths for name, paths in defined.items() if name in RETIRED} == {}
