from __future__ import annotations

import logging
from typing import Any

from app.domain.models import AgentMessage, MechanicResult
from app.repositories.group_state import save_state
from app.models import GroupState

_logger = logging.getLogger(__name__)


def apply_mechanic_result(message: AgentMessage, result: MechanicResult) -> None:
    """
    State Reducer (純 Python 節點).
    Safely applies the StateDelta from the MechanicResult to the actual GroupState.
    This guarantees that the LLM cannot hallucinate arbitrary state changes outside
    of the explicit delta structure.
    """
    state: GroupState = message.payload["state"]
    char = message.payload.get("character")
    delta = result.state_delta

    _logger.info(f"StateReducer processing delta for {message.payload['display_name']}: {delta}")

    # 1. Apply Character-specific deltas (if the user has an active character)
    if char:
        if delta.hp_change != 0:
            char.hp = max(0, min(char.hp_max, char.hp + delta.hp_change))
            _logger.info(f"[{char.name}] HP adjusted by {delta.hp_change}, now {char.hp}/{char.hp_max}")
            
        if delta.sanity_change != 0:
            char.san = max(0, min(char.san_max, char.san + delta.sanity_change))
            _logger.info(f"[{char.name}] SAN adjusted by {delta.sanity_change}, now {char.san}/{char.san_max}")
            
        if delta.mp_change != 0:
            char.mp = max(0, min(char.mp_max, char.mp + delta.mp_change))
            _logger.info(f"[{char.name}] MP adjusted by {delta.mp_change}, now {char.mp}/{char.mp_max}")
            
        for item in delta.inventory_add:
            if item not in char.inventory:
                char.inventory.append(item)
                _logger.info(f"[{char.name}] Gained item: {item}")
                
        for item in delta.inventory_remove:
            if item in char.inventory:
                char.inventory.remove(item)
                _logger.info(f"[{char.name}] Lost item: {item}")

    # 2. Apply Group-wide deltas
    for flag_name, flag_val in delta.flags_set.items():
        state.flags[flag_name] = flag_val
        _logger.info(f"[Group] Set flag {flag_name}={flag_val}")
        
    if delta.time_cost_minutes > 0:
        # If the game system tracks time, add it here. (Mocked for now)
        _logger.info(f"[Group] Time advanced by {delta.time_cost_minutes} minutes.")

    # 3. Persistence: Centralized point where DB saves occur for this turn.
    # By saving state ONLY here in the pipeline, we prevent race conditions
    # from multiple agents trying to persist partial data.
    save_state(state)
    _logger.info("StateReducer: GroupState successfully persisted.")
    
    # Store the processed result back in the message for the Narrator to use
    message.payload["mechanic_result"] = result
