"""The Discord transport is layered, and every layer can be imported on its own.

``app/discord_transport`` was split out of ``app/discord_bot.py``. Imported through the bot, an import cycle between
its modules can hide behind a lucky order (the bot imports ``controls`` first); imported alone, the same cycle fails
with a partially initialised module. Each module may import only the ones above it in this list at module level.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "app.discord_transport"
# Lowest first. A module may import, at module level, only modules that come before it.
LAYERS = ["gateway", "interactions", "delivery", "lifecycle", "controls", "help_ui"]


def module_level_transport_imports(path: Path) -> set[str]:
    """The transport modules ``path`` imports when it loads (not the ones imported inside a function)."""
    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom) and node.module == PACKAGE:
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(PACKAGE + "."):
            found.add(node.module.split(".")[2])
    return found


def test_every_transport_module_is_in_the_layer_list():
    on_disk = {p.stem for p in (ROOT / "app" / "discord_transport").glob("*.py") if p.stem != "__init__"}
    assert on_disk == set(LAYERS)


@pytest.mark.parametrize("name", LAYERS)
def test_a_module_imports_only_layers_below_it_at_module_level(name):
    below = set(LAYERS[: LAYERS.index(name)])
    imported = module_level_transport_imports(ROOT / "app" / "discord_transport" / f"{name}.py")
    assert imported <= below, f"{name} imports {sorted(imported - below)} at module level"


@pytest.mark.parametrize("module", [f"{PACKAGE}.{name}" for name in LAYERS] + ["app.discord_bot"])
def test_each_module_imports_in_a_fresh_interpreter(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr[-800:]
