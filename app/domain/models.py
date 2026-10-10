from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypedDict, get_args

if TYPE_CHECKING:
    from app.models import Character, GroupState
    from app.services.turn_delivery import DeliveryEnvelope

PlayerTurnKind = Literal["player_action", "resolved_check_followup", "opening_fallback"]
SpeakerRole = Literal["player", "kp_assistant"]
# Who a tool call acts for when it is not an ordinary player's own action. Set only by turn code (never read from a
# tool argument, which a model could forge): the KP Assistant's turn, and a verified narrative correction.
SystemOrigin = Literal["kp_assistant", "correction"]
# Why a player's action got a generic reply instead of a scenario-grounded one. Stable and queryable:
# every fallback logs exactly one of these (``app/services/turn_fallback.py`` classifies them).
FallbackReason = Literal[
    "no_scenario_evidence", "executor_no_action", "unresolved_pending_state", "invalid_tool_plan",
    "tool_failure", "tool_result_rejected", "narration_failure", "state_conflict",
    "unsupported_action", "safety_block", "internal_error", "unknown",
]
FALLBACK_REASONS: tuple[str, ...] = get_args(FallbackReason)


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
class TurnResolution:
    """Validated handoff, never an instruction to mutate state."""
    disposition: str = "incomplete"
    actor_character_id: str = ""
    waiting_for: str = ""
    check_id: str = ""
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    validation_code: str = ""


@dataclass(frozen=True)
class ObservedOutcome:
    """Tool evidence, never inferred causal outcomes from a snapshot difference."""
    evidence_ref: str
    tool_name: str
    success: bool
    public_text: str = ""
    audience: str = "internal"
    recipient_id: str = ""
    fact_ref: str = ""


class CheckStatus(TypedDict, total=False):
    """What the Executor's tools and the handoff established about checks this turn.

    Narration and delivery read it as the authority on whether a check is waiting, a Luck
    decision is open, or a result is final. Who writes each key:

    * Executor / tool gateway, while tools run: ``tool_called``, ``pending``, ``pending_luck``,
      ``resolved``, ``scenario_evidence_blocked``, ``cleared`` (a wait was removed).
    * Executor, once the tools are done: ``tool_event_count``, ``state_changed``, ``dice_rolled``,
      ``combat_opened`` (a call took the battle from inactive to active).
    * ``turn_handoff.prepare_narrator_handoff``, from the latest state: ``pending``,
      ``pending_luck``, ``resolved`` (cleared while a Luck decision is open),
      ``waiting_for_name``, ``current_turn_state``.
    * ``turn_delivery.public_mechanic``, on a copy: clears a private ``pending`` / ``pending_luck``.
    """

    tool_called: bool
    pending: dict[str, Any] | None
    pending_luck: dict[str, Any] | None
    resolved: dict[str, Any] | None
    scenario_evidence_blocked: bool
    cleared: bool
    tool_event_count: int
    state_changed: bool
    combat_opened: bool
    dice_rolled: bool
    waiting_for_name: str
    current_turn_state: Any


@dataclass
class MechanicResult:
    success: bool
    action_type: str
    narrative_facts: list[str]
    state_delta: StateDelta
    events: list[GameEvent] = field(default_factory=list)
    check_status: CheckStatus = field(default_factory=CheckStatus)
    turn_resolution: TurnResolution | None = None
    execution_health: str = "completed"
    observed_outcomes: list[ObservedOutcome] = field(default_factory=list)
    # Every tool the Executor called this turn, with whether it succeeded; never replayed.
    tool_calls: tuple[tuple[str, bool], ...] = ()
    # The scenario passages the Executor's own searches returned this turn.
    scenario_evidence: tuple[str, ...] = ()
    fallback_reason: FallbackReason | None = None


class TurnPayload(TypedDict, total=False):
    """The facts one player turn carries between its stages.

    ``context_builder`` fills the first group; each later stage adds only the keys it owns
    (``tests/test_turn_payload_contract.py`` fails if another module writes them). The
    envelope is read by Executor, Narrator and delivery, never handed to a provider.
    """

    # context_builder: the input and the evidence gathered before any model call
    conversation_id: str
    user_id: str
    display_name: str
    speaker_role: SpeakerRole
    text: str
    resolved_location: dict[str, Any] | None
    state: GroupState
    character: Character | None
    combat_provisional: bool
    resolved_check_events: list[dict[str, Any]]
    rag_context: str
    memory_context: str
    rag_status: str
    memory_status: str
    correction_context: str
    # supervisor: how this turn is routed and what it is answering
    turn_kind: PlayerTurnKind
    intent: str
    resolved_check_context: dict[str, Any]
    mechanic_result: MechanicResult
    recovery_context: str  # the one targeted search made after a recoverable fallback
    # executor: what its tools produced for the player
    private_messages: list[tuple[str, str]]
    image_requests: list[tuple[str | None, int]]
    observed_outcomes: list[ObservedOutcome]
    # narrator: what it was required to honour and whether it failed
    narration_requirements: dict[str, Any]
    narration_failed: bool
    # turn_delivery.finalize: the verdict on what may be shown
    delivery_envelope: DeliveryEnvelope


@dataclass
class AgentMessage:
    """Envelope for passing data between KeeperSupervisor and its Agents."""
    payload: TurnPayload
