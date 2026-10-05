"""The facts a turn carries between its stages are a declared contract (``TurnPayload``).

``context_builder`` supplies the input and the gathered evidence; each later stage adds only
the keys it owns. mypy checks every literal key read and written; these checks add who may
*write* a key, so a new piece of evidence cannot start being set from an unrelated module.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.domain.models import CheckStatus, TurnPayload

ROOT = Path(__file__).resolve().parent.parent

# Written once by context_builder, as the payload is built.
BUILT_BY_CONTEXT_BUILDER = {
    "conversation_id", "user_id", "display_name", "speaker_role", "text", "resolved_location",
    "state", "character", "combat_provisional", "resolved_check_events", "rag_context",
    "memory_context", "rag_status", "memory_status", "correction_context",
}
# Added later, by exactly these modules.
WRITERS = {
    "app/agents/supervisor.py": {"turn_kind", "intent", "resolved_check_context", "mechanic_result", "recovery_context"},
    "app/agents/executor.py": {"private_messages", "image_requests", "observed_outcomes"},
    "app/agents/narrator.py": {"narration_requirements", "narration_failed", "observed_outcomes"},
    "app/services/turn_delivery.py": {"delivery_envelope"},
}
MUTATORS = {"setdefault", "update", "pop", "popitem", "clear", "__setitem__"}

# CheckStatus: the stage that may write each key (see the table in ``CheckStatus``).
CHECK_STATUS_WRITERS = {
    "app/agents/tool_gateway.py": {
        "tool_called", "pending", "pending_luck", "resolved", "scenario_evidence_blocked", "cleared",
    },
    "app/agents/executor.py": {
        "tool_called", "pending", "pending_luck", "resolved", "tool_event_count", "state_changed", "dice_rolled",
    },
    "app/services/turn_handoff.py": {
        "pending", "pending_luck", "resolved", "waiting_for_name", "current_turn_state",
    },
    "app/services/turn_delivery.py": {"pending", "pending_luck"},
}
CHECK_STATUS_NAMES = {"status", "check_status"}


def _is_payload(node: ast.expr, *, bare_name: bool) -> bool:
    """``message.payload``, or a parameter called ``payload`` inside the turn pipeline."""
    return (isinstance(node, ast.Attribute) and node.attr == "payload") or (
        bare_name and isinstance(node, ast.Name) and node.id == "payload"
    )


def _in_turn_pipeline(path: str) -> bool:
    return path.startswith("app/agents/") or Path(path).name.startswith("turn_")


def _replaces(node: ast.AST, attribute: str, matches, *, bare_name: bool) -> bool:
    """``x.payload = ...`` or ``payload |= ...``: the whole object is replaced or merged into."""
    if isinstance(node, ast.Assign):
        return any(isinstance(target, ast.Attribute) and target.attr == attribute for target in node.targets)
    if isinstance(node, ast.AugAssign):
        return matches(node.target, bare_name=bare_name)
    return False


def _literal(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def payload_writes(source: str, *, bare_name: bool = True) -> set[str]:
    """Keys assigned to or set on a payload; ``"*"`` for a write whose key is not a literal."""
    written: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if _replaces(node, "payload", _is_payload, bare_name=bare_name):
            written.add("*")  # the whole payload replaced or merged into
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and _is_payload(node.value, bare_name=bare_name):
            written.add(_literal(node.slice) or "*")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in MUTATORS and _is_payload(node.func.value, bare_name=bare_name)):
            written.add((_literal(node.args[0]) if node.args else None) or "*")
    return written


def _is_check_status(node: ast.expr, *, bare_name: bool) -> bool:
    """``result.check_status``, or a local called ``status`` / ``check_status`` in the turn pipeline."""
    return (isinstance(node, ast.Attribute) and node.attr == "check_status") or (
        bare_name and isinstance(node, ast.Name) and node.id in CHECK_STATUS_NAMES
    )


def check_status_writes(source: str, *, bare_name: bool = True) -> set[str]:
    """Keys assigned into a check status, by subscript or in the dict literal that builds it."""
    written: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if _replaces(node, "check_status", _is_check_status, bare_name=bare_name):
            written.add("*")  # the whole status replaced or merged into
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) \
                and _is_check_status(node.value, bare_name=bare_name):
            written.add(_literal(node.slice) or "*")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in MUTATORS and _is_check_status(node.func.value, bare_name=bare_name):
            written.add((_literal(node.args[0]) if node.args else None) or "*")
        literal: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in CHECK_STATUS_NAMES or isinstance(node, ast.keyword) and node.arg == "check_status":
            literal = node.value
        if isinstance(literal, ast.Dict):
            written.update((_literal(key) or "*") if key is not None else "**" for key in literal.keys)
    return written - {"**"}


def _production() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "app").rglob("*.py"))
    }


def test_every_declared_key_has_a_supplier_and_only_one_key_has_two() -> None:
    declared = set(TurnPayload.__annotations__)
    suppliers = {
        key: int(key in BUILT_BY_CONTEXT_BUILDER) + sum(key in keys for keys in WRITERS.values())
        for key in declared
    }
    assert set().union(BUILT_BY_CONTEXT_BUILDER, *WRITERS.values()) == declared
    # The Executor creates ``observed_outcomes`` and the Narrator appends to it; every other
    # key has exactly one stage that supplies it.
    assert {key for key, count in suppliers.items() if count != 1} == {"observed_outcomes"}
    assert suppliers["observed_outcomes"] == 2


def test_the_payload_is_built_with_the_declared_input_keys() -> None:
    tree = ast.parse(_production()["app/agents/context_builder.py"])
    built = next(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "payload"
    )
    assert isinstance(built, ast.Dict)
    assert {_literal(key) for key in built.keys if key is not None} == BUILT_BY_CONTEXT_BUILDER


def test_a_payload_key_is_only_written_by_the_stage_that_owns_it() -> None:
    offenders: dict[str, set[str]] = {}
    for path, source in _production().items():
        written = payload_writes(source, bare_name=_in_turn_pipeline(path))
        if written - WRITERS.get(path, set()):
            offenders[path] = written - WRITERS.get(path, set())
    assert offenders == {}


def test_the_stages_really_do_write_what_the_contract_says_they_own() -> None:
    sources = _production()
    for path, keys in WRITERS.items():
        assert payload_writes(sources[path]) == keys, path


def test_the_gate_catches_replacing_the_whole_object() -> None:
    assert payload_writes("message.payload = {}") == {"*"}
    assert payload_writes("payload |= {'x': 1}") == {"*"}
    assert check_status_writes("result.check_status = {}") == {"*"}
    assert check_status_writes("status |= {'x': 1}") == {"*"}


def test_the_gate_catches_a_stray_write() -> None:
    assert payload_writes('message.payload["delivery_envelope"] = 1') == {"delivery_envelope"}
    assert payload_writes('message.payload.setdefault("observed_outcomes", [])') == {"observed_outcomes"}
    assert payload_writes('message.payload.update({"x": 1})') == {"*"}
    assert payload_writes('name = message.payload["state"]; other = message.payload.get("text")') == set()


def test_every_check_status_key_has_a_stage_that_may_write_it() -> None:
    assert set(CheckStatus.__annotations__) == set().union(*CHECK_STATUS_WRITERS.values())


def test_a_check_status_key_is_only_written_by_a_stage_that_owns_it() -> None:
    offenders: dict[str, set[str]] = {}
    for path, source in _production().items():
        written = check_status_writes(source, bare_name=_in_turn_pipeline(path) or path.endswith("prompt_config.py"))
        if written - CHECK_STATUS_WRITERS.get(path, set()):
            offenders[path] = written - CHECK_STATUS_WRITERS.get(path, set())
    assert offenders == {}


def test_the_stages_really_do_write_what_the_check_status_contract_says() -> None:
    sources = _production()
    for path, keys in CHECK_STATUS_WRITERS.items():
        assert check_status_writes(sources[path]) == keys, path


def test_the_check_status_gate_catches_a_stray_write() -> None:
    assert check_status_writes('result.check_status["dice_rolled"] = True') == {"dice_rolled"}
    assert check_status_writes('status["resolved"] = None; status.update({"x": 1})') == {"resolved", "*"}
    assert check_status_writes('check_status: CheckStatus = {"tool_called": False, "pending": None}') == {
        "tool_called", "pending",
    }
    assert check_status_writes('run(check_status={**check_status, "state_changed": 1})') == {"state_changed"}
    assert check_status_writes('value = result.check_status["pending"]; other = status.get("x")') == set()
