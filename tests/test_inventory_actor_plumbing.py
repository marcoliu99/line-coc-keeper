"""Inventory tools learn who is acting, and which trusted path a call came from, before any gate relies on it."""
from __future__ import annotations

import asyncio
import dataclasses
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from app import tool_dispatch
from app.agents import tool_gateway
from app.keeper_tools import registry
from app.models import Character, GroupState


def _state() -> GroupState:
    state = GroupState(group_id="g", timeline_id="t")
    state.characters["a"] = Character(name="Ann", owner_id="u1", carried_items=["手電筒"])
    return state


def _recording_handler(seen: list[registry.ToolCall]):
    def handler(call: registry.ToolCall) -> dict[str, Any]:
        seen.append(call)
        return {"ok": True}
    return handler


@contextmanager
def _recording(name: str, seen: list[registry.ToolCall]):
    """Swap the named tool's handler for one that records the call (ToolSpec is frozen, so replace the entry)."""
    spec = dataclasses.replace(registry.REGISTRY[name], handler=_recording_handler(seen))
    with patch.dict(registry.REGISTRY, {name: spec}), patch.object(tool_dispatch.mutation_admission, "assert_admitted"):
        yield


def test_the_gateway_hands_the_acting_player_to_the_inventory_tools() -> None:
    seen: list[registry.ToolCall] = []
    for name in ("add_carried_item", "remove_carried_item"):
        with _recording(name, seen):
            execute = tool_gateway.make_tool_executor(_state(), [], [], "player", [], actor_id="u1")
            asyncio.run(execute(name, {"investigator": "Ann", "item": "鑰匙"}))
    assert [call.actor_id for call in seen] == ["u1", "u1"]
    assert all(call.system_origin is None for call in seen)


def test_an_ordinary_player_call_has_no_system_origin() -> None:
    seen: list[registry.ToolCall] = []
    with _recording("add_carried_item", seen):
        tool_dispatch.execute_tool(_state(), "add_carried_item", {"investigator": "Ann", "item": "x"}, [], [], actor_id="u1")
    assert seen[0].system_origin is None


def test_a_tool_argument_can_never_set_the_origin() -> None:
    seen: list[registry.ToolCall] = []
    with _recording("add_carried_item", seen):
        tool_dispatch.execute_tool(
            _state(), "add_carried_item",
            {"investigator": "Ann", "item": "x", "system_origin": "correction"}, [], [], actor_id="u1")
    assert seen[0].system_origin is None


def test_a_call_from_turn_code_can_carry_the_trusted_origin() -> None:
    seen: list[registry.ToolCall] = []
    with _recording("add_carried_item", seen):
        tool_dispatch.execute_tool(
            _state(), "add_carried_item", {"investigator": "Ann", "item": "x"}, [], [], system_origin="correction")
    assert seen[0].system_origin == "correction"


def test_both_correction_paths_mark_their_inventory_call() -> None:
    import inspect

    from app.services import correction_adjudication, natural_corrections
    for module in (correction_adjudication, natural_corrections):
        source = inspect.getsource(module)
        call = source[source.index("add_carried_item(ToolCall("):]
        assert 'system_origin="correction"' in call.split("))", 1)[0], module.__name__
