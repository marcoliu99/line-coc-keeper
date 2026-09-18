from __future__ import annotations

import logging
from typing import Any, Callable

from app import keeper
from app.models import GroupState

_logger = logging.getLogger(__name__)

# The design spec originally called for a condensed set of ~5 high-level
# tools (mechanic_action/character_action/inventory_action/combat_action/
# information_action) with an `action` sub-field, to save tokens versus
# app/keeper.py's 24 granular tools. Implementing that condensed schema for
# real means re-deriving every one of keeper.py's already-verified behaviors
# (dice rolls, pending_checks registration, ammo/weapon safety checks,
# combat state transitions, the _mutate_and_save_state locking discipline —
# see app/keeper.py's _execute_tool and this project's changelog for the
# bugs that discipline was built to prevent) a second time, with every
# chance of silently reintroducing bugs already found and fixed there. The
# previous version of this file took a shortcut instead: each "high-level"
# tool just returned a description string ("Requested skill check for X on
# Y.") without ever touching real dice/state — no check was ever actually
# rolled, no pending_checks entry was ever registered, no HP/SAN/ammo was
# ever really adjusted.
#
# This version drops the condensed schema and exposes keeper.py's real
# TOOLS/_execute_tool directly instead — the Executor Agent gets the exact
# same tool set and behavior the original single-LLM Keeper had, just called
# from a different orchestration layer. Token-count reduction from
# condensing the tool list is a real, separate optimization that can be
# revisited later without re-touching correctness.
TOOLS: list[dict[str, Any]] = keeper.TOOLS


def make_tool_executor(
    state: GroupState,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
    speaker_role: str,
    facts: list[str],
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Returns the (tool_name, tool_input) -> dict callback that
    provider.run_conversation expects for its execute_tool parameter.

    Delegates every call straight to keeper._execute_tool — the same
    function app/legacy_commands.py's Keeper turn uses — so dice rolls,
    pending_checks registration, and all state mutation go through the
    identical, already-locked (_mutate_and_save_state) path. `facts`
    collects one human-readable line per call for MechanicResult.
    narrative_facts, so the Narrator agent has something concrete to
    narrate from without re-deriving what happened itself.
    """

    def execute(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        result = keeper._execute_tool(state, tool_name, tool_input, private_messages, image_requests, speaker_role)
        facts.append(_describe_tool_call(tool_name, result))
        return result

    return execute


def _describe_tool_call(tool_name: str, result: dict[str, Any]) -> str:
    if not result.get("ok", True):
        return f"{tool_name} 失敗：{result.get('error', '未知錯誤')}"
    # Keep this a plain, factual line (not prose) — the Narrator agent turns
    # facts into narrative text; this just needs to state what happened.
    details = ", ".join(f"{k}={v}" for k, v in result.items() if k not in ("ok", "note"))
    return f"{tool_name} 成功：{details}" if details else f"{tool_name} 成功。"
