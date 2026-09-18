from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StateDelta:
    hp_change: int = 0
    sanity_change: int = 0
    mp_change: int = 0
    inventory_add: list[str] = field(default_factory=list)
    inventory_remove: list[str] = field(default_factory=list)
    flags_set: dict[str, bool] = field(default_factory=dict)
    clarification: bool = False
    time_cost_minutes: int = 0
    danger_delta: int = 0
    generated_clues: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GameEvent:
    type: str
    payload: dict[str, Any]


@dataclass
class MechanicResult:
    success: bool
    action_type: str
    narrative_facts: list[str]
    state_delta: StateDelta
    events: list[GameEvent] = field(default_factory=list)


@dataclass
class AgentMessage:
    """Envelope for passing data between KeeperSupervisor and its Agents."""
    payload: dict[str, Any]
