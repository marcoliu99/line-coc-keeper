"""What can be asked of a battle: the actions ``CombatEngine.handle`` accepts.

Each action is a frozen record of the caller's intent and nothing else: the
state it runs on is passed to ``handle``, and the mode of the battle (idle
or managed) is read once there. The type parameter is the result type, so
``engine.handle(state, Run(action_id="a1"))`` is a ``dict`` and
``engine.handle(state, RollPending(...))`` is a ``SkillCheckResult``.

Actions never carry a die result. Dice are drawn by the engine, and an action
that can repeat (a retried tool call, a double-clicked button) carries the
stable identity the battle's ledger keys it on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from app import combat_rules, dice
from app.models import Character, Combatant, PostcombatObligation

R = TypeVar("R")
Result = dict[str, Any]


class Action(Generic[R]):
    """Base of every combat action; ``R`` is what ``CombatEngine.handle`` returns."""


# --------------------------------------------------------------------------
# Setting up and looking at a battle (any mode)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Start(Action[None]):
    """Begin a battle (with its pre-fight checkpoint), or confirm one is running."""


@dataclass(frozen=True, kw_only=True)
class AddCombatant(Action[Any]):
    """Add an NPC or ally, starting the battle if needed. Returns ``combat.AddedCombatant``."""

    name: str
    dex: int
    hp: int
    is_ally: bool = False
    armor: list[dict[str, Any]] | None = None
    attacks: list[dict[str, Any]] | None = None
    abilities: list[dict[str, Any]] | None = None
    force_new_instance: bool = False
    source: dict[str, Any] | None = None
    skills: dict[str, int] | None = None


@dataclass(frozen=True)
class Status(Action[str]):
    """The battle as a Keeper-readable table."""

    include_private: bool = False


@dataclass(frozen=True)
class Obligations(Action[list[PostcombatObligation]]):
    """Continuing obligations as they would stand if the battle were settled now."""


# --------------------------------------------------------------------------
# Moving a battle along
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Advance(Action[Result]):
    """End the current turn. ``event_id`` is the stable identity a managed battle requires."""

    actor_id: str = ""
    event_id: str = ""
    skip: bool = False  # end the turn without an action (guard, cover, retreat: nothing the engine resolves)


@dataclass(frozen=True)
class PlanEnemy(Action[Result]):
    enemy_name: str = ""


@dataclass(frozen=True)
class RunEnemyPlan(Action[Result]):
    """Execute a planned NPC action to the next point that needs a person."""

    plan_id: str


@dataclass(frozen=True, kw_only=True)
class ResolveEnemy(Action[Result]):
    """Resolve an enemy plan from a caller-supplied outcome (battles saved before the pipeline only)."""

    plan_id: str
    outcome: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class SetInitiative(Action[Result]):
    combat_id: str
    actor_ids: list[str]
    event_id: str
    reason: str


@dataclass(frozen=True)
class FinishRetiredTurn(Action[None]):
    """Hand the turn on after the investigator whose turn it was retired."""

    old_order: list[Combatant]
    old_index: int
    removed_ids: set[str]


# --------------------------------------------------------------------------
# One attack, from declaration to outcome (managed)
# --------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Declare(Action[Result]):
    """Declare an attack and run it as far as it goes without a person."""

    action_id: str
    actor_id: str
    target_id: str
    weapon_reference: str
    action_kind: Literal["melee", "single_shot"] = "melee"
    distance_yards: float | None = None
    scenario_definitions: tuple[combat_rules.WeaponDefinition, ...] = ()
    weapon_instance: combat_rules.WeaponInstance | None = None


@dataclass(frozen=True)
class Run(Action[Result]):
    """Continue a declared action after the person it waited for has answered."""

    action_id: str


@dataclass(frozen=True, kw_only=True)
class Choose(Action[Result]):
    interaction_id: str
    owner_id: str
    choice: str


@dataclass(frozen=True, kw_only=True)
class ChoiceReceipt(Action[Result | None]):
    """The stored answer to a choice already submitted, so a repeat replays it."""

    interaction_id: str
    owner_id: str
    choice: str


@dataclass(frozen=True, kw_only=True)
class ValidatePending(Action[Result]):
    """Is this pending control still the one the battle is waiting on?"""

    pending: dict[str, Any]
    owner_id: str


@dataclass(frozen=True, kw_only=True)
class RollPending(Action[dice.SkillCheckResult]):
    """Draw (or replay) the roll a pending control stands for."""

    pending: dict[str, Any]
    owner_id: str


@dataclass(frozen=True, kw_only=True)
class CheckResult(Action[Result]):
    """Feed an authoritative check result back into the action that was waiting for it."""

    pending_entry: dict[str, Any]
    owner_id: str
    result: dice.SkillCheckResult
    final: bool = True


# --------------------------------------------------------------------------
# Damage and effects
# --------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class ApplyDamage(Action[Result]):
    """A trusted damage amount. ``bypass_armor`` marks an already-final amount."""

    target: str
    raw_damage: int
    damage_type: str = "physical"
    tags: list[str] | None = None
    source_id: str = ""
    bypass_armor: bool = False
    entry_point: str = "apply_combat_damage"
    event_id: str = ""


@dataclass(frozen=True)
class DamageCombatant(Action[Result]):
    """Change a combatant's hit points by ``delta`` (negative is damage)."""

    name: str
    delta: int


@dataclass(frozen=True, kw_only=True)
class SingleHit(Action[Result]):
    """One explicit hit on an investigator, e.g. from a resource adjustment."""

    character: Character
    damage: int
    event_id: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class AddEffect(Action[Result]):
    target: str
    label: str
    timing: str = "turn_start"
    damage: str = ""
    damage_type: str = "physical"
    remaining_rounds: int | None = None
    tags: list[str] | None = None
    source_id: str = ""
    public_description: str = ""


@dataclass(frozen=True, kw_only=True)
class DeclareEffect(Action[Result]):
    combat_id: str
    effect_id: str
    target_id: str
    severity_id: str
    scope: Literal["incident", "round"]
    reason: str
    stop_condition: str
    timing: str = "round_end"
    special_rule: str | None = None
    defense: Literal["none"] = "none"


@dataclass(frozen=True, kw_only=True)
class StopEffect(Action[Result]):
    effect_id: str
    event_id: str
    reason: str
    combat_id: str | None = None


@dataclass(frozen=True)
class RunEffect(Action[Result]):
    effect_id: str


# --------------------------------------------------------------------------
# Rulings, injuries and care
# --------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Rule(Action[Result]):
    """A controller's ruling that lets a paused action continue."""

    combat_id: str
    action_id: str
    event_id: str
    reason: str
    decision: Literal["resume", "cancel"]
    weapon_reference: str = ""
    distance_yards: float | None = None
    scenario_definitions: tuple[combat_rules.WeaponDefinition, ...] = ()
    weapon_instance: combat_rules.WeaponInstance | None = None


@dataclass(frozen=True, kw_only=True)
class ReconcileCorrection(Action[Result]):
    combat_id: str
    event_id: str
    reason: str
    injury_by_character: dict[str, dict[str, bool]]
    acknowledge_action_ids: list[str]
    acknowledge_check_ids: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class Stabilize(Action[Result]):
    character_id: str
    source_check_id: str
    event_id: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class RequestStabilization(Action[Result]):
    healer_character_id: str
    character_id: str
    event_id: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class ProcessPostcombat(Action[Result]):
    logical_round: int
    event_id: str


# --------------------------------------------------------------------------
# Settlement and administration
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PreviewSettlement(Action[Result]):
    pass


@dataclass(frozen=True, kw_only=True)
class ConfirmSettlement(Action[Result]):
    combat_id: str
    settlement_id: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class Rollback(Action[Result]):
    combat_id: str
    event_id: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class CorrectEvent(Action[Result]):
    combat_id: str
    target_event_id: str
    event_id: str
    changes: dict[str, Any]
    reason: str


@dataclass(frozen=True, kw_only=True)
class ReconcileBaseline(Action[Result]):
    combat_id: str
    character: Character
    event_id: str
    decision: str
    reason: str
