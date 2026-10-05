"""Boolean settings are read one way.

Six flags used to be parsed inline with ``os.environ.get(...).lower() in ("1", "true", "yes")``: a different set of
spellings from ``_env_bool``, and an unrecognised value silently turned the flag off even for a flag that is on by default.
``_env_bool`` accepts ``1/true/yes/on`` and ``0/false/no/off``, and for anything else keeps the default and records the
bad value so startup can warn about it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import config

CONFIG = Path(config.__file__)
FLAGS = {
    "SCENARIO_RAG_ENABLED": False, "SCENARIO_LIFECYCLE_KP_ONLY": False, "OPENAI_OMIT_TEMPERATURE": False,
    "DEBUG_SHOW_INTERNAL_IDS": False, "TURN_FALLBACK_RECOVERY_ENABLED": True, "RETRIEVAL_REUSE_FOR_FOLLOWUPS": True,
}


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_the_accepted_spellings_of_true(monkeypatch, raw):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert config._env_bool("SOME_FLAG", False) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", " no ", "off"])
def test_the_accepted_spellings_of_false(monkeypatch, raw):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert config._env_bool("SOME_FLAG", True) is False


@pytest.mark.parametrize("default", [True, False])
def test_an_unrecognised_value_keeps_the_default_and_is_recorded(monkeypatch, default):
    monkeypatch.setattr(config, "INVALID_LOG_SETTINGS", [])
    monkeypatch.setenv("SOME_FLAG", "ture")
    assert config._env_bool("SOME_FLAG", default) is default
    assert config.INVALID_LOG_SETTINGS == [("SOME_FLAG", "boolean", str(default).lower())]


def test_an_unset_flag_takes_its_default(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert config._env_bool("SOME_FLAG", True) is True


def test_every_flag_is_read_through_env_bool_with_its_documented_default():
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    found: dict[str, bool] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "_env_bool"):
            found[node.targets[0].id] = bool(node.value.args[1].value)
    for name, default in FLAGS.items():
        assert found.get(name) is default, name


def test_no_setting_is_parsed_as_a_boolean_inline():
    """A new flag goes through ``_env_bool``; ``os.environ.get(...).lower() in (...)`` is how the drift started."""
    source = CONFIG.read_text(encoding="utf-8")
    inline = [node.lineno for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.Compare) and any(isinstance(op, ast.In) for op in node.ops)
              and "os.environ.get" in ast.unparse(node.left)]
    assert inline == []
