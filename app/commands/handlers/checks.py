"""``/coc check``, ``/coc luck`` and their buttons: the adapter over the check engine.

Three steps, in this order, and nothing else:

1. one state transaction settles the roll (``app.checks.service`` decides, this
   module only commits what it decided; no Discord, Keeper or LLM in there);
2. a refusal or a Luck prompt goes straight back to whoever the pending entry
   was addressed to;
3. a settled check is handed to the Keeper to narrate, after the roll is
   already committed, so a failed or retried narration never rerolls.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app import locks, observability
from app.agents import supervisor
from app.checks import events
from app.checks import service as check_service
from app.checks.dice_port import DEFAULT_DICE, DicePort
from app.checks.models import CheckOutcome
from app.commands.handlers.transact import CONFLICT_TEXT
from app.commands.types import Reply, SendDM, SendDMImage, SendImage
from app.models import GroupState
from app.repositories import state_transaction
from app.repositories.group_state import load_state
from app.services import mutation_admission, turn_delivery
from app.services.managed_checks import ManagedCombatChecks
from app.services.post_turn import run_post_turn_maintenance_after_output

_logger = logging.getLogger(__name__)

_MANAGED = ManagedCombatChecks()


def _settle(
    conversation_id: str, decide: Callable[[GroupState], CheckOutcome], *, reason: str,
) -> CheckOutcome:
    """Run ``decide`` on the latest state; commit what it changed, nothing if it refused."""
    def apply(ctx: state_transaction.TxContext) -> CheckOutcome:
        outcome = decide(ctx.state)
        if not outcome.changed:
            ctx.skip_save()
        elif outcome.save_reason:
            ctx.reason = outcome.save_reason
        return outcome

    result = state_transaction.mutate(conversation_id, apply, reason=reason)
    if result.value is None:
        # Only an invariant rejection lands here; the mutation's own refusals
        # come back as an outcome with reply_text.
        return CheckOutcome(reply_text=CONFLICT_TEXT)
    return result.value


def resolve_check(
    conversation_id: str, user_id: str, text: str, *, dice_port: DicePort = DEFAULT_DICE,
) -> CheckOutcome:
    """Resolve ``/coc check`` without invoking the Keeper (blocking; call off the event loop)."""
    return _settle(
        conversation_id,
        lambda state: check_service.resolve_player_check(
            state, user_id, text, dice_port=dice_port, managed=_MANAGED,
        ),
        reason="check",
    )


def resolve_luck(
    conversation_id: str, user_id: str, choice: str, *, dice_port: DicePort = DEFAULT_DICE,
) -> CheckOutcome:
    """Resolve ``/coc luck <choice>`` without invoking the Keeper (blocking)."""
    return _settle(
        conversation_id,
        lambda state: check_service.resolve_luck_decision(
            state, user_id, choice, dice_port=dice_port, managed=_MANAGED,
        ),
        reason="luck",
    )


async def _deliver(
    outcome: CheckOutcome, conversation_id: str, user_id: str, reply: Reply, send_dm: SendDM,
    send_image: SendImage, send_dm_image: SendDMImage, split_roll_feedback: bool,
    acquire_legacy_for_keeper: bool,
) -> bool:
    if outcome.reply_text:
        if outcome.visibility != "public":
            await send_dm(user_id, outcome.reply_text)
        else:
            await reply(outcome.reply_text)
        return False
    if not outcome.should_finalize:
        return False
    await finalize_check_result(
        conversation_id, user_id, outcome, reply, send_dm, send_image, send_dm_image,
        split_roll_feedback=split_roll_feedback, acquire_legacy_for_keeper=acquire_legacy_for_keeper,
    )
    return True


@mutation_admission.guard_async_entry
async def handle_check_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
    split_roll_feedback: bool = False,
    acquire_legacy_for_keeper: bool = False,
) -> bool:
    """Resolve a pending choice or legacy pending check.

    New ordinary skill, attack, and SAN checks are rolled immediately inside
    Keeper tools. This command remains for selecting a pending Dodge/Fight Back
    option and for compatibility with snapshots created before that change;
    it is not a player-owned dice command.
    """
    outcome = await asyncio.to_thread(resolve_check, conversation_id, user_id, text)
    return await _deliver(
        outcome, conversation_id, user_id, reply, send_dm, send_image, send_dm_image,
        split_roll_feedback, acquire_legacy_for_keeper,
    )


@mutation_admission.guard_async_entry
async def handle_luck_decision(
    conversation_id: str,
    user_id: str,
    choice: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    split_roll_feedback: bool = False,
    acquire_legacy_for_keeper: bool = False,
) -> bool:
    """Resolves a pending Luck-spend decision (see handle_check_command above
    and app/luck.py) — either "skip" (keep the natural roll) or a tier name
    ("regular"/"hard"/"extreme") to buy up to, deducting the cost from the
    character's Luck before handing the (possibly improved) result to the
    Keeper exactly like a normal check."""
    outcome = await asyncio.to_thread(resolve_luck, conversation_id, user_id, choice)
    return await _deliver(
        outcome, conversation_id, user_id, reply, send_dm, send_image, send_dm_image,
        split_roll_feedback, acquire_legacy_for_keeper,
    )


async def finalize_check_result(
    conversation_id: str,
    user_id: str,
    outcome: CheckOutcome,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    *,
    split_roll_feedback: bool = False,
    acquire_legacy_for_keeper: bool = False,
) -> None:
    """Shared tail for every resolved check (sanity, choice, plain skill, and
    a Luck-spend decision) — hands the already-determined result to the
    Keeper for narration and delivers whatever it queued."""
    resolved_event = outcome.resolved_event
    check_id, decision_id, timeline_id = outcome.check_id, outcome.decision_id, outcome.timeline_id
    private_result = turn_delivery.is_private(resolved_event or {})
    if private_result:
        # Route by persisted audience, never by whichever channel invoked /coc check.
        async def private_reply(text: str) -> None:
            await send_dm(user_id, text)

        async def private_image(png: bytes, group_id: str, page: int) -> None:
            await send_dm_image(user_id, png, group_id, page)

        reply = private_reply
        send_image = private_image
    if split_roll_feedback:
        await reply(outcome.roll_line if private_result else (outcome.roll_feedback_text or outcome.roll_line))

    async def run_keeper_phase() -> None:
        # Deliberately NOT running keeper_message through _resolve_map_action:
        # keeper_message is a system-generated result narration (e.g. "（角色
        # 擲骰做了一次「CON」檢定...）"), not the player's own words — but
        # intent_parser.has_movement_verb's trigger list is broad enough (a
        # bare "去"/"走" is enough) that ordinary narration text can trip it
        # by accident (e.g. major-wound's "...請描述角色失去意識倒下的過程"
        # contains "去"). When that happens, _resolve_map_action_core falls
        # back to fuzzy-matching the *entire* narration text against every
        # room name on the current map — any short, common room name (臥室,
        # 書房, ...) that happens to appear as a substring anywhere in that
        # text gets treated as "the player just moved there", handed to the
        # Keeper as an authoritative Map Engine result it's told not to
        # second-guess. That's a real, observed bug (an apparent teleport to
        # an unrelated room right after a skill/sanity check), not a
        # theoretical one. Passing None here costs nothing useful: the Keeper
        # still learns the character's actual current room from state.
        # current_map_page/current_room_id via _build_dynamic_prompt's own
        # "resolved_location is None" fallback block — it just won't be
        # mislabeled as a fresh Map Engine move this check never made.
        resolved_location = None
        async with locks.narrating_turn(conversation_id):
            # The deterministic dice transaction may have finished before the
            # Keeper turn got the per-conversation slot.  Refresh the
            # authoritative snapshot so the provider sees the state that was
            # actually committed, not a stale mutable object from before a
            # concurrent maintenance/state update.
            fresh_state = load_state(conversation_id)
            current_timeline_id = fresh_state.timeline_id or f"legacy-{conversation_id}"
            if timeline_id and current_timeline_id != timeline_id:
                observability.event(
                    "check.result.stale",
                    level=logging.WARNING,
                    reason="timeline_mismatch",
                    requested_timeline_id=timeline_id,
                    current_timeline_id=current_timeline_id,
                    check_id=check_id or None,
                    decision_id=decision_id or None,
                )
                await reply("這個檢定結果所屬的劇情時間線已經失效，請依目前劇情重新操作。")
                return
            fresh_char = fresh_state.get_active_character(user_id)
            if fresh_char is None:
                await reply("這個檢定結果所屬的角色已經不在目前劇情中，請使用目前有效的角色操作。")
                return
            context_note = outcome.action_context or (
                "（未提供原始行動情境；只描述已確定的檢定結果，不要自行編造未確認的場景或行動。）"
            )
            identity_note = ""
            if check_id:
                identity_note += f" check_id={check_id}"
            if decision_id:
                identity_note += f" decision_id={decision_id}"
            if timeline_id:
                identity_note += f" timeline_id={timeline_id}"
            keeper_context_message = (
                f"【檢定結果上下文{identity_note}】\n"
                f"【玩家原始行動情境】{context_note}\n"
                f"{outcome.keeper_message}"
            )
            if resolved_event is not None:
                await asyncio.to_thread(events.persist_consequence_origin, conversation_id, resolved_event)
                # Preserve changes directly caused by the deterministic roll
                # (SAN loss or Luck spend). For other fields, start observing
                # at the serialized Keeper phase so unrelated changes made
                # while the player was resolving the check aren't attributed
                # to this check.
                keeper_start = events.character_attribute_snapshot(fresh_char)
                tracked_fields = set(resolved_event.get("tracked_roll_fields", []))
                for field_name in resolved_event["state_before"]:
                    if field_name not in tracked_fields:
                        resolved_event["state_before"][field_name] = keeper_start[field_name]
            resolved_check_context: dict[str, Any] | None = None
            if resolved_event is not None:
                resolved_check_context = {
                    key: resolved_event[key]
                    for key in (
                        "investigator", "skill", "skill_value", "roll", "difficulty",
                        "outcome", "action_context", "check_id", "timeline_id",
                        "opposed_outcome", "player_declaration", "action_basis",
                        "visibility", "recipient_id", "event_id", "success", "consequences",
                    )
                    if key in resolved_event
                }
            if resolved_check_context is None:
                # Legacy snapshots can lack the structured event. The dice
                # were still resolved by the deterministic transaction above;
                # keep the follow-up type explicit and forbid another roll.
                resolved_check_context = {
                    "investigator": fresh_char.name,
                    "outcome": outcome.roll_line,
                    "action_context": context_note,
                }
            keeper_reply, private_messages, image_requests = await supervisor.run_turn(
                state=fresh_state,
                user_id=user_id,
                display_name=fresh_char.name,
                text=keeper_context_message,
                resolved_location=resolved_location,
                speaker_role="player",
                conversation_id=conversation_id,
                turn_kind="resolved_check_followup",
                resolved_check_context=resolved_check_context,
            )
            if resolved_event is not None:
                await asyncio.to_thread(events.persist_resolved_event, conversation_id, resolved_event)
            if split_roll_feedback:
                public_message = f"{outcome.keeper_header}\n\n{keeper_reply}" if outcome.keeper_header else keeper_reply
            else:
                public_message = f"{outcome.roll_line}\n\n{keeper_reply}"
            await run_post_turn_maintenance_after_output(
                conversation_id,
                reply,
                public_message,
                send_dm,
                send_image,
                send_dm_image,
                private_messages,
                image_requests,
            )

    if acquire_legacy_for_keeper:
        async with locks.get_conversation_lock(conversation_id):
            await run_keeper_phase()
    else:
        await run_keeper_phase()
