"""Pending-check and Luck buttons: identity checks and claiming, without Discord I/O.

Moved out of app/discord_bot.py (docs/specs/refactor/discord_events_through_router_design_spec.md,
step 3) so the router can run button clicks; the transport only renders and
posts the buttons.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from app import async_utils, locks, observability
from app.check_identity import (
    compact_identity_token,
    effective_check_id,
    effective_decision_id,
)
from app.commands import sudo as sudo_policy
from app.repositories.group_state import load_state

_logger = logging.getLogger(__name__)


def check_button_matches_pending(
    owner_id: str, pending: dict | None, button_check_id: str, timeline_id: str,
) -> bool:
    """Return whether a persisted CheckButton still names this pending check."""
    if not pending:
        return False
    # Older/migrated payloads may explicitly contain null.  Do not turn that
    # into the literal string "None": an absent timeline is the legacy
    # compatibility value, while a non-empty value must match exactly.
    persisted_timeline_id = str(pending.get("timeline_id") or "").strip()
    if persisted_timeline_id and persisted_timeline_id != timeline_id:
        return False
    current_id = effective_check_id(owner_id, pending, timeline_id)
    # A pre-identity button has no way to distinguish a replacement request
    # with the same owner/option text. It is therefore always stale; a fresh
    # render carries either the full identity (legacy compatibility) or the
    # compact token and can be used safely until the pending entry is consumed.
    compact_id = compact_identity_token("check", owner_id, current_id, timeline_id)
    return bool(button_check_id and button_check_id in {current_id, compact_id})


def luck_button_matches_pending(
    owner_id: str, decision: dict | None, button_decision_id: str, timeline_id: str,
) -> bool:
    if not decision:
        return False
    # See check_button_matches_pending: null is an absent legacy timeline,
    # not the identity string "None".
    persisted_timeline_id = str(decision.get("timeline_id") or "").strip()
    if persisted_timeline_id and persisted_timeline_id != timeline_id:
        return False
    current_id = effective_decision_id(owner_id, decision, timeline_id)
    # A pre-identity button cannot distinguish a replacement Luck decision,
    # so it must not consume any pending decision. Fresh renders carry either
    # the full identity (legacy compatibility) or the compact token.
    compact_id = compact_identity_token("decision", owner_id, current_id, timeline_id)
    return bool(button_decision_id and button_decision_id in {current_id, compact_id})


@dataclass(frozen=True)
class PendingButtonIntent:
    """A pending check or Luck decision whose buttons still need posting."""

    kind: str
    owner_id: str
    entry: dict
    name: str
    timeline_id: str
    public_marker: str | None
    claimed_at: float


@dataclass
class ControlCompletion:
    """One turn's snapshot, in-lock claim, and after-unlock publication."""

    conversation_id: str
    before_pending: dict
    before_luck: dict
    sudo_command: sudo_policy.ParsedSudoCommand | None = None
    intents: list[PendingButtonIntent] | None = field(default=None, init=False)

    async def claim_locked(self) -> None:
        self.intents = await try_claim_pending_buttons_locked(
            self.conversation_id, self.before_pending, self.before_luck,
            sudo_command=self.sudo_command,
        )

    async def publish(
        self, send: Callable[[PendingButtonIntent], Awaitable[None]],
        recover: Callable[[dict, dict, sudo_policy.ParsedSudoCommand | None], Awaitable[None]],
    ) -> None:
        if self.intents is None:
            await recover(self.before_pending, self.before_luck, self.sudo_command)
        else:
            await publish_claimed_buttons(self.conversation_id, self.intents, send)


async def claim_pending_buttons_locked(
    conversation_id: str,
    before_pending: dict,
    before_luck_pending: dict,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
    kinds: frozenset[str] = frozenset({"check", "luck"}),
    public_marker: str | None = None,
) -> list[PendingButtonIntent]:
    """Claim this turn's new buttons while its caller owns the conversation lock.

    There is one state load and at most one save for both collections. No
    Discord I/O occurs here; the caller sends returned intents after unlock.
    """
    # Imported here, not at module level: tests intercept the repository's
    # save_state, and the router imports this module.
    from app.commands.router import sudo_public_marker
    from app.repositories.group_state import save_state

    state = await asyncio.to_thread(load_state, conversation_id)
    marker = sudo_public_marker(state, sudo_command) if sudo_command else public_marker
    timeline_id = state.timeline_id or f"legacy-{conversation_id}"
    claimed_at = time.perf_counter()
    intents: list[PendingButtonIntent] = []
    for kind, collection, before in (
        ("check", state.pending_checks, before_pending),
        ("luck", state.pending_luck_decisions, before_luck_pending),
    ):
        if kind not in kinds:
            continue
        for owner_id, entry in collection.items():
            if before.get(owner_id) == entry or entry.get("_buttons_posted"):
                continue
            original = dict(entry)
            entry["_buttons_posted"] = True
            character = state.get_active_character(owner_id)
            intents.append(PendingButtonIntent(
                kind=kind,
                owner_id=owner_id,
                entry=original,
                name=character.name if character else "你",
                timeline_id=timeline_id,
                public_marker=marker,
                claimed_at=claimed_at,
            ))
    if intents:
        await asyncio.to_thread(save_state, state)
        # Saving a legacy state can create its first timeline_id. Identity
        # tokens must use the persisted value that callbacks will verify.
        if state.timeline_id and state.timeline_id != timeline_id:
            intents = [replace(intent, timeline_id=state.timeline_id) for intent in intents]
        for intent in intents:
            observability.event("pending_button.claimed", kind=intent.kind, status="success")
    return intents


async def try_claim_pending_buttons_locked(
    conversation_id: str,
    before_pending: dict,
    before_luck_pending: dict,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
) -> list[PendingButtonIntent] | None:
    """claim_pending_buttons_locked, or None if claiming failed (logged).

    None tells the caller to fall back to re-posting from the snapshots.
    """
    try:
        return await claim_pending_buttons_locked(
            conversation_id, before_pending, before_luck_pending, sudo_command=sudo_command,
        )
    except Exception:
        _logger.exception("failed to claim pending buttons before unlock for conversation_id=%s", conversation_id)
        return None


def _equal_ignoring_posted_marker(left: dict, right: dict) -> bool:
    return {key: value for key, value in left.items() if key != "_buttons_posted"} == {
        key: value for key, value in right.items() if key != "_buttons_posted"
    }


def _collection_name(kind: str) -> str:
    return "pending_checks" if kind == "check" else "pending_luck_decisions"


async def release_stranded_claim(conversation_id: str, intent: PendingButtonIntent) -> None:
    """Release only the claimed entry when it still has the same identity/content."""
    from app.repositories.group_state import save_state

    try:
        async with locks.get_conversation_lock(conversation_id):
            state = await asyncio.to_thread(load_state, conversation_id)
            collection = getattr(state, _collection_name(intent.kind))
            current = collection.get(intent.owner_id)
            if (current is None or not current.get("_buttons_posted")
                    or not _equal_ignoring_posted_marker(current, intent.entry)):
                return
            current.pop("_buttons_posted", None)
            await asyncio.to_thread(save_state, state)
    except Exception:
        _logger.exception("failed to release stranded posting claim for owner_id=%s in conversation_id=%s",
                          intent.owner_id, conversation_id)


async def _release_many(conversation_id: str, intents: list[PendingButtonIntent]) -> None:
    for intent in intents:
        await release_stranded_claim(conversation_id, intent)


async def publish_claimed_buttons(
    conversation_id: str, intents: list[PendingButtonIntent],
    send: Callable[[PendingButtonIntent], Awaitable[None]],
) -> None:
    """Verify each live claim and send outside the lock through a transport callback."""
    for index, intent in enumerate(intents):
        started = time.perf_counter()
        try:
            state = await asyncio.to_thread(load_state, conversation_id)
            current = getattr(state, _collection_name(intent.kind)).get(intent.owner_id)
            if (state.timeline_id and state.timeline_id != intent.timeline_id
                    or current is None or not current.get("_buttons_posted")
                    or not _equal_ignoring_posted_marker(current, intent.entry)):
                observability.event("pending_button.send.completed", kind=intent.kind, status="stale")
                continue
            await send(intent)
            finished = time.perf_counter()
            observability.event(
                "pending_button.send.completed", kind=intent.kind, status="success",
                claim_to_send_start_ms=(started - intent.claimed_at) * 1000,
                send_duration_ms=(finished - started) * 1000,
                claim_to_send_completed_ms=(finished - intent.claimed_at) * 1000,
            )
        except asyncio.CancelledError:
            observability.event("pending_button.send.failed", kind=intent.kind, status="cancelled")
            cleanup = asyncio.create_task(_release_many(conversation_id, intents[index:]))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                async_utils.observe_background_task(cleanup, operation="pending_button.claim_release")
            raise
        except Exception:
            _logger.exception("failed to send claimed %s button for owner_id=%s in conversation_id=%s",
                              intent.kind, intent.owner_id, conversation_id)
            observability.event("pending_button.send.failed", kind=intent.kind, status="error")
            await release_stranded_claim(conversation_id, intent)
