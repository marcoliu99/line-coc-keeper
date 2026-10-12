"""What happens to a Narrator reply before a player reads it, as an ordered list that checks its own order.

Every step needs the whole reply, which is why a reply cannot stream: that is the safety boundary, not an
oversight. The order is the contract. A step placed after the one that validates delivery can change text that was
already validated, so each rule below says what must come before what and why, and ``validate`` rejects a list that
breaks one. The list lived as inline code in ``supervisor.run_turn`` (#181 and #185 each inserted a step); moving it
here changes no behaviour and awaits exactly the steps that awaited before: the Guard and the obligation gate.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app import presentation
from app.agents import guard, obligation_gate
from app.domain.models import AgentMessage, MechanicResult, SpeakerRole, StateDelta
from app.models import GroupState
from app.services import prompt_config, turn_delivery, turn_fallback, turn_handoff


@dataclass(frozen=True)
class ReplyContext:
    """What the steps read about the turn. Nothing here changes while the pipeline runs."""

    state: GroupState
    message: AgentMessage
    user_id: str
    speaker_role: SpeakerRole
    turn_kind: str
    resolved_check_context: dict[str, Any] | None
    turn_id: str
    obligation_candidates: bool
    obligation_evidence: Sequence[str]
    handoff_before: tuple[dict, dict]


@dataclass
class ReplyDraft:
    """The reply as the steps rewrite it, and the facts a rewrite can change."""

    text: str
    private_messages: list[tuple[str, str]]
    image_requests: list[tuple[str | None, int]]
    mechanic_result: MechanicResult | None
    public_result: MechanicResult | None
    private_controls: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[ReplyContext, ReplyDraft], Awaitable[None] | None]
    # Rewrites the text for display only and is the last writer by design. It already ran after delivery validation
    # before this module existed, and this move keeps that; it is the one step allowed to follow ``finalize``.
    display_only: bool = False


def no_mechanics() -> MechanicResult:
    """The mechanics of a turn that ran no Executor, for work that needs somewhere to record them."""
    return MechanicResult(success=True, action_type="none", narrative_facts=[], state_delta=StateDelta())


def _consistent(ctx: ReplyContext, draft: ReplyDraft, candidate: str, *, trailing: str = "") -> str:
    if ctx.turn_kind == "resolved_check_followup":
        origin_check_id = (ctx.resolved_check_context or {}).get("check_id")
        new_pending_check = any(
            isinstance(entry, dict) and entry.get("source_check_id") == origin_check_id
            for entry in ctx.state.pending_checks.values()
        )
        candidate = prompt_config.enforce_resolved_check_consistency(
            candidate, ctx.resolved_check_context or {}, new_pending_check=new_pending_check,
        )
    if draft.public_result is not None:
        # ``trailing`` is passed only when set, so a pass without one keeps the call shape tests stub.
        extra = {"trailing": trailing} if trailing else {}
        candidate = prompt_config.enforce_mechanic_check_consistency(candidate, draft.public_result, state=ctx.state, **extra)
    return candidate


def _scrub_leaks(ctx: ReplyContext, draft: ReplyDraft) -> None:
    scrubbed = presentation.strip_junk_tail(presentation.strip_leaked_decision(draft.text))
    if draft.text.strip() and not scrubbed:
        # A reply that was nothing but the decision object leaves no narration. The lines the steps below add (the
        # roll, the Luck offer) still reach the player; ``finalize`` says the narration failed only if nothing does
        # (Haunting 2026-10-11 rerun3: two Luck turns read only 「守密人暫時無法完成敘事」).
        turn_fallback.record("narration_failure", state=ctx.state, user_id=ctx.user_id, turn_id=ctx.turn_id,
                             result=draft.mechanic_result, rag_context=ctx.message.payload.get("rag_context", ""))
    draft.text = scrubbed


def _repair_consistency(ctx: ReplyContext, draft: ReplyDraft) -> None:
    draft.text = _consistent(ctx, draft, draft.text)


async def _guard(ctx: ReplyContext, draft: ReplyDraft) -> None:
    draft.text = await guard.enforce_narrative_safety(ctx.message, draft.text)


async def _obligations(ctx: ReplyContext, draft: ReplyDraft) -> None:
    """What the scenario attaches to an event is owed now, not when a player later says they are frightened (CS-007).

    Decided on the narration that survived consistency repair and the Guard, so a trigger they removed charges nothing.
    """
    if not ctx.obligation_candidates:
        return
    # What this turn's tools already did, the Executor's and a follow-up Narrator's alike (it may roll Sanity for what
    # the settled roll revealed): the gate does not apply that a second time.
    seen = draft.mechanic_result or no_mechanics()
    told = [o for o in ctx.message.payload.get("observed_outcomes", []) if o not in seen.observed_outcomes]
    owed = await obligation_gate.enforce(
        ctx.state, ctx.user_id, draft.text, ctx.obligation_evidence,
        replace(seen, observed_outcomes=[*seen.observed_outcomes, *told]),
        turn_id=ctx.turn_id, speaker_role=ctx.speaker_role, private_messages=draft.private_messages,
        image_requests=draft.image_requests, observed_outcomes=ctx.message.payload.get("observed_outcomes", []),
    )
    if owed:
        if draft.mechanic_result is None:
            draft.mechanic_result = no_mechanics()
        turn_handoff.prepare_narrator_handoff(
            ctx.state, ctx.user_id, draft.mechanic_result, ctx.handoff_before[0], ctx.handoff_before[1],
            ctx.message.payload)
        draft.public_result = turn_delivery.public_mechanic(draft.mechanic_result, ctx.state)
        summaries = "\n".join(item.summary for item in owed)
        draft.text = _consistent(ctx, draft, draft.text.rstrip() + "\n\n" + summaries, trailing=summaries)


def _scenario_names(ctx: ReplyContext, draft: ReplyDraft) -> None:
    """An English original the scenario index lists is written as its Chinese name, as the prompt asks; the prompt
    alone left some through (a glossary in the prompt is followed most of the time, not every time)."""
    state = ctx.state
    keep = [c.name for c in (*state.characters_by_id.values(), *state.characters.values())]
    keep += [str(p.get("name") or "") for p in state.pregens if isinstance(p, dict)]
    entries = [*state.scenario_npc_index, *state.scenario_location_index]
    draft.text = presentation.use_scenario_names(draft.text, entries, keep)
    draft.private_messages = [(owner, presentation.use_scenario_names(text, entries, keep))
                              for owner, text in draft.private_messages]


def _party_size(ctx: ReplyContext, draft: ReplyDraft) -> None:
    draft.text = presentation.enforce_party_size(draft.text, len(ctx.state.active_characters()))


def _finalize(ctx: ReplyContext, draft: ReplyDraft) -> None:
    draft.text, draft.private_controls = turn_delivery.finalize(ctx.message, draft.text)
    if not draft.text.strip() and not draft.private_controls:
        # Nothing at all to send, public or private: say so, never a blank, and through delivery like any reply.
        draft.text, draft.private_controls = turn_delivery.finalize(ctx.message, turn_fallback.guidance("narration_failure"))


def _player_text(ctx: ReplyContext, draft: ReplyDraft) -> None:
    draft.text = presentation.player_text(draft.text)
    draft.private_messages = [(owner, presentation.player_text(text)) for owner, text in draft.private_messages]


STEPS: tuple[Step, ...] = (
    Step("scrub_leaks", _scrub_leaks),
    Step("consistency", _repair_consistency),
    Step("guard", _guard),
    Step("consistency_after_guard", _repair_consistency),
    Step("obligations", _obligations),
    Step("scenario_names", _scenario_names),
    Step("party_size", _party_size),
    Step("finalize", _finalize),
    # Maps tier names and removes internal ids for display. Not lossless: a validated line that carried a raw tier
    # name or a labelled id would be rewritten after validation. None does today (``finalize`` labels outcomes itself
    # and ``player_text`` is idempotent), and validating the mapped text instead is a behaviour change, not a move.
    Step("player_text", _player_text, display_only=True),
)

# (earlier, later, why). The reasons are the ones the inline code carried as comments.
ORDER_RULES: tuple[tuple[str, str, str], ...] = (
    ("scrub_leaks", "consistency", "an internal decision object is removed before anything repairs or validates the text"),
    ("consistency", "guard", "consistency repair precedes the Guard"),
    ("guard", "consistency_after_guard", "any Guard rewrite is checked for consistency again"),
    ("consistency_after_guard", "obligations",
     "obligations are decided on the narration that survived repair and the Guard"),
    ("obligations", "scenario_names", "names are put in Chinese on the final wording, owed lines included"),
    ("scenario_names", "finalize", "delivery validates the names as the player will read them"),
    ("obligations", "party_size", "the party count is corrected on the final wording, owed lines included"),
    ("party_size", "finalize", "delivery validates what is sent, so the count is corrected before it validates"),
    ("finalize", "player_text", "display mapping is the last writer"),
)


def validate(steps: Sequence[Step]) -> None:
    """Raise ValueError when ``steps`` breaks an ordering rule or rewrites text after delivery validated it other than for display."""
    names = [step.name for step in steps]
    for earlier, later, why in ORDER_RULES:
        if earlier not in names or later not in names:
            raise ValueError(f"reply pipeline lacks '{earlier}' or '{later}': {why}")
        if names.index(earlier) > names.index(later):
            raise ValueError(f"reply pipeline: '{earlier}' must run before '{later}': {why}")
    if "finalize" in names:
        for step in steps[names.index("finalize") + 1:]:
            if not step.display_only:
                raise ValueError(f"reply pipeline: '{step.name}' runs after delivery validated the text "
                                 "but is not marked display_only")


validate(STEPS)


async def run(ctx: ReplyContext, draft: ReplyDraft) -> ReplyDraft:
    """Run every step in order. Only the Guard and the obligation gate wait on anything."""
    for step in STEPS:
        outcome = step.run(ctx, draft)
        if inspect.isawaitable(outcome):
            await outcome
    return draft
