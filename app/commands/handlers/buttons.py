"""Check and Luck button clicks, run the way the text /coc check and /coc luck routes are.

Moved out of app/discord_bot.py's CheckButton / LuckSpendButton callbacks
(docs/specs/refactor/discord_events_through_router_design_spec.md, step 3).
The order is unchanged: ownership, the in-flight guard, the Discord
acknowledgement, then validation and the command under the conversation lock,
claiming new buttons before unlock and restoring them after.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app import locks, observability
from app.legacy_commands import (
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
    handle_check_command,
    handle_luck_decision,
)
from app.models import GroupState
from app.repositories.group_state import load_state
from app.services import pending_buttons
from app.services.pending_buttons import PendingButtonIntent

_logger = logging.getLogger(__name__)

_BUSY = "上一次的檢定還在處理中，請稍等結果出來，不要重複點擊。"


@dataclass(frozen=True)
class ButtonIO:
    """What a button click needs from the transport."""

    notify: Callable[[str], Awaitable[None]]  # a message only the clicker sees
    acknowledge: Callable[[], Awaitable[None]]  # remove the clicked buttons; Discord needs this within 3 s
    reply: Reply
    send_dm: SendDM
    send_image: SendImage
    send_dm_image: SendDMImage
    # After unlock: post the claimed intents, or re-post from the snapshots
    # when claiming failed (intents None).
    restore_buttons: Callable[[dict, dict, list[PendingButtonIntent] | None], Awaitable[None]]


# Validates the click against the locked state and returns the command to run,
# or None after telling the clicker why not. Calls `snapshot()` at the point
# from which buttons must be restored even if the click goes no further.
_Prepare = Callable[[GroupState, Callable[[], None]], Awaitable[Callable[[], Awaitable[None]] | None]]


async def _run_button(
    conversation_id: str, clicker_id: str, owner_id: str, io: ButtonIO, *, not_yours: str, prepare: _Prepare,
) -> None:
    if clicker_id != owner_id:
        await io.notify(not_yours)
        return
    if not locks.try_acquire_check(conversation_id, owner_id):
        # A slow Keeper call from a first click (or an earlier /coc check) is
        # still in flight: reject outright rather than letting a second click
        # queue behind the conversation lock and roll again once its turn comes.
        await io.notify(_BUSY)
        return
    snapshots: list[tuple[dict, dict]] = []
    claimed: list[PendingButtonIntent] | None = None
    try:
        await io.acknowledge()
        async with locks.get_conversation_lock(conversation_id):
            state = await asyncio.to_thread(load_state, conversation_id)

            def snapshot() -> None:
                snapshots.append((dict(state.pending_checks), dict(state.pending_luck_decisions)))

            command = await prepare(state, snapshot)
            if command is None:
                return
            try:
                await command()
            finally:
                before_pending, before_luck = snapshots[-1]
                claimed = await pending_buttons.try_claim_pending_buttons_locked(
                    conversation_id, before_pending, before_luck,
                )
    finally:
        # A deterministic check/luck entry may be persisted before a later
        # Keeper or narration step raises. Restore any newly created buttons
        # even then, but only once the conversation lock has been released.
        if snapshots:
            try:
                await io.restore_buttons(*snapshots[-1], claimed)
            except Exception:
                _logger.exception("failed to restore pending buttons after a button click for conversation_id=%s", conversation_id)
        locks.release_check(conversation_id, owner_id)


def _stale(kind: str, reason: str, owner_id: str, **identity: Any) -> None:
    observability.event(
        f"{kind}.button.stale", level=logging.INFO, reason=reason,
        owner_id_hash=observability.safe_identifier(owner_id), **identity,
    )


async def handle_check_button(
    conversation_id: str, clicker_id: str, owner_id: str, option: str, check_id: str, io: ButtonIO,
) -> None:
    """A CheckButton click: the same resolution as `/coc check <option>`."""

    async def prepare(state: GroupState, snapshot: Callable[[], None]) -> Callable[[], Awaitable[None]] | None:
        pending = state.pending_checks.get(owner_id)
        if not pending:
            _stale("check", "missing_pending", owner_id, check_id=check_id or None)
            await io.notify("這個檢定已經結束或失效了，請等待目前的檢定按鈕。")
            return None
        if not pending_buttons.check_button_matches_pending(
            owner_id, pending, check_id, state.timeline_id or f"legacy-{conversation_id}",
        ):
            _stale("check", "identity_mismatch", owner_id, check_id=check_id or None)
            await io.notify("這個檢定按鈕已經過期，請使用最新的按鈕。")
            return None
        snapshot()
        resolved = option
        if pending.get("type") == "choice" and resolved.startswith("#"):
            try:
                resolved = str(pending["options"][int(resolved[1:])]["label"])
            except (IndexError, KeyError, TypeError, ValueError):
                _stale("check", "invalid_choice_token", owner_id, check_id=check_id or None)
                await io.notify("這個檢定按鈕已經失效，請使用最新的按鈕。")
                return None
        command_text = f"/coc check {resolved}" if resolved else "/coc check"
        return lambda: handle_check_command(
            conversation_id, owner_id, io.reply, io.send_dm, io.send_image, io.send_dm_image,
            command_text, split_roll_feedback=True, acquire_legacy_for_keeper=False,
        )

    await _run_button(
        conversation_id, clicker_id, owner_id, io,
        not_yours="這不是你的檢定，換你自己的角色來按。", prepare=prepare,
    )


async def handle_luck_button(
    conversation_id: str, clicker_id: str, owner_id: str, choice: str, decision_id: str, io: ButtonIO,
) -> None:
    """A LuckSpendButton click: the same resolution as `/coc luck <choice>`."""

    async def prepare(state: GroupState, snapshot: Callable[[], None]) -> Callable[[], Awaitable[None]] | None:
        decision = state.pending_luck_decisions.get(owner_id)
        if not decision:
            _stale("luck", "missing_pending", owner_id, decision_id=decision_id or None)
            await io.notify("這個 Luck 決定已經結束或失效了。")
            return None
        if not pending_buttons.luck_button_matches_pending(
            owner_id, decision, decision_id, state.timeline_id or f"legacy-{conversation_id}",
        ):
            _stale("luck", "identity_mismatch", owner_id, decision_id=decision_id or None)
            await io.notify("這個 Luck 按鈕已經過期，請使用最新的按鈕。")
            return None
        snapshot()
        return lambda: handle_luck_decision(
            conversation_id, owner_id, choice, io.reply, io.send_dm, io.send_image, io.send_dm_image,
            split_roll_feedback=True, acquire_legacy_for_keeper=False,
        )

    await _run_button(
        conversation_id, clicker_id, owner_id, io,
        not_yours="這不是你的 Luck 花費決定，換你自己的角色來按。", prepare=prepare,
    )
