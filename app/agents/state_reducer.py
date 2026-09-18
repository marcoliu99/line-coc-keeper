from __future__ import annotations

import logging

from app.domain.models import AgentMessage, MechanicResult

_logger = logging.getLogger(__name__)


def apply_mechanic_result(message: AgentMessage, result: MechanicResult) -> None:
    """
    State Reducer (純 Python 節點).

    Historically this function re-applied `result.state_delta` onto
    `GroupState`/`Character` directly and called save_state() itself. That
    version had two real bugs — it wrote to `char.inventory` (the actual
    field is `carried_items`) and `state.flags` (GroupState has no such
    field) — both unreachable only because the Executor that produced
    `result` always returned an empty StateDelta.

    As of the Executor rewrite (see app/agents/executor.py), real state
    mutation already happens for real, synchronously, before this function
    is ever called: tool calls go through keeper._execute_tool via
    tool_gateway.make_tool_executor, which uses the same
    _mutate_and_save_state locking every other Keeper tool call in this
    project uses (reload-latest-under-lock, mutate, save). Re-applying
    `state_delta` here on top of that — especially via an unlocked, blind
    save_state(state) — would either double-apply the same change or, worse,
    clobber a freshly-saved state with this function's older in-memory
    snapshot. So StateDelta is intentionally left empty by the Executor and
    this function does no mutation and no persistence of its own; it exists
    as an explicit pipeline stage (matching the design spec's architecture
    diagram) and a place to log what happened, not to act on it.
    """
    _logger.info(
        "StateReducer: mechanic result for %s already applied by the Executor's tool calls (success=%s, facts=%s)",
        message.payload.get("display_name"),
        result.success,
        result.narrative_facts,
    )
