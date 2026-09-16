"""Per-conversation locks so two messages for the same conversation can't race
on data/groups/*.json.

app/state.py does a full read-modify-write (load_state -> mutate -> save_state)
with no locking of its own. Without serialization, two events for the same
conversation arriving close together can each load the same on-disk snapshot,
mutate their own in-memory copy, and save — the second save silently discards
whatever the first one wrote (e.g. an HP/SAN change from a skill check that
happened "at the same time" as another player's).

"Conversation" here is a platform-agnostic id — a LINE group/room/user, a
Discord channel, whatever an adapter in app/main.py or app/discord_bot.py
addresses one GroupState by (see app/commands.py, which namespaces these as
"line-group-...", "discord-channel-...", etc. to keep platforms from colliding).

The fix here is coarse but correct: one asyncio.Lock per conversation_id, held
for the entire duration of handling one message (from the initial load_state
through every save_state it triggers, including while awaiting a slow Keeper
LLM call). Different conversations still run fully concurrently — only messages
within the same conversation queue up behind each other.
"""
from __future__ import annotations

import asyncio
import threading

# Legacy conversation lock: used by the current coarse-grained flow and kept
# unchanged while callers are migrated incrementally.
_locks: dict[str, asyncio.Lock] = {}

# State lock: future per-conversation synchronous state transactions around
# load_state -> mutate -> save_state. It is a threading.RLock so Keeper worker
# threads can use the same authoritative lock; async callers should not hold it
# across slow event-loop work.
_state_locks: dict[str, threading.RLock] = {}

# Keeper turn lock: future serialization for Keeper/LLM turns per conversation.
_keeper_turn_locks: dict[str, asyncio.Lock] = {}


def get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[conversation_id] = lock
    return lock


def get_state_lock(conversation_id: str) -> threading.RLock:
    lock = _state_locks.get(conversation_id)
    if lock is None:
        lock = threading.RLock()
        _state_locks[conversation_id] = lock
    return lock


def get_keeper_turn_lock(conversation_id: str) -> asyncio.Lock:
    lock = _keeper_turn_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _keeper_turn_locks[conversation_id] = lock
    return lock


# A resolved check/Luck-decision involves a slow Keeper (LLM) call, so a
# player's second click/resend while the first is still in flight would
# otherwise just queue behind get_conversation_lock above and run as a
# genuinely separate, duplicate roll once its turn comes — not blocked, just
# delayed. This is a non-blocking try-acquire (never awaits) checked *before*
# that lock, specifically to reject a duplicate outright instead of queuing
# it: see app/commands.py's handle_check_command/handle_luck_decision and
# app/discord_bot.py's CheckButton/LuckSpendButton callbacks, which all
# acquire this around themselves and release it in a finally block.
_in_flight_checks: set[tuple[str, str]] = set()


def try_acquire_check(conversation_id: str, user_id: str) -> bool:
    """True if acquired (caller must release_check when done); False if this
    (conversation, user) pair already has a check/Luck-decision in flight —
    caller should tell the player to wait rather than proceeding."""
    key = (conversation_id, user_id)
    if key in _in_flight_checks:
        return False
    _in_flight_checks.add(key)
    return True


def release_check(conversation_id: str, user_id: str) -> None:
    _in_flight_checks.discard((conversation_id, user_id))
