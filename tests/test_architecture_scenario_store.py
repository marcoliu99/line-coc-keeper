"""Only ``scenario_library`` knows where a scenario source and its variants are stored."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "app/scenario_library.py"
LIBRARY_NAMES = {"scenario_library", "library"}
# Files inside a scenario or variant directory; the modules above the library must not name them.
STORAGE_FILES = {"manifest.json", "records.json", "scenario.txt", "coverage.json", "glossary.json", "template.md"}
ABOVE_THE_LIBRARY = ("app/scenario_templates.py", "app/scenario_source_review.py", "app/help_actions.py",
                     "app/scenario_activation.py")


def _modules() -> dict[str, ast.Module]:
    return {str(path.relative_to(ROOT)): ast.parse(path.read_text(encoding="utf-8"))
            for path in sorted((ROOT / "app").rglob("*.py"))}


def private_library_use(tree: ast.Module) -> list[int]:
    """Lines reading ``scenario_library._x`` or ``library._x``."""
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__")
            and isinstance(node.value, ast.Name) and node.value.id in LIBRARY_NAMES]


def storage_names(tree: ast.Module) -> list[int]:
    """Lines holding a string constant that names a file of the library's layout."""
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in STORAGE_FILES]


def test_no_module_reaches_into_the_library_s_private_names() -> None:
    offenders = {path: private_library_use(tree) for path, tree in _modules().items()
                 if path != OWNER and private_library_use(tree)}
    assert offenders == {}


def test_the_modules_above_the_library_do_not_name_its_files() -> None:
    modules = _modules()
    offenders = {path: storage_names(modules[path]) for path in ABOVE_THE_LIBRARY if storage_names(modules[path])}
    assert offenders == {}
    assert storage_names(modules[OWNER])


def test_the_gate_catches_a_stray_use() -> None:
    assert private_library_use(ast.parse("x = scenario_library._read_json(p, None)")) == [1]
    assert private_library_use(ast.parse("x = library._path(sid)")) == [1]
    assert private_library_use(ast.parse("x = library.read_source(sid)")) == []
    assert storage_names(ast.parse('p = root / "records.json"')) == [1]
    assert storage_names(ast.parse('p = root / "images"')) == []
