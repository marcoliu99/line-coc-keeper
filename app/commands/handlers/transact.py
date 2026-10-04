"""Run one command's state change through the shared state transaction.

Handlers parse input and format replies; the rules they apply live in the
domain modules. This helper is the only thing in between: it hands the domain
call the **latest** committed state inside ``state_transaction.mutate`` so a
validation made on a stale read can never be committed, and it keeps the reply
text out of the transaction.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models import GroupState
from app.repositories import state_transaction

CONFLICT_TEXT = "遊戲狀態剛被其他操作更新，這次指令沒有套用，請再試一次。"


@dataclass(frozen=True)
class Outcome:
    """What a command's mutation decided: whether it worked, what to say, and
    whether the state it touched should be saved."""

    ok: bool
    text: str = ""
    save: bool = True
    value: Any = None


def done(text: str = "", *, value: Any = None, save: bool = True) -> Outcome:
    return Outcome(True, text, save, value)


def refuse(text: str, *, save: bool = False) -> Outcome:
    return Outcome(False, text, save)


def run(
    conversation_id: str,
    mutation: Callable[[GroupState], Outcome],
    *,
    reason: str = "command",
    **transaction_options: Any,
) -> Outcome:
    result = state_transaction.mutate(
        conversation_id, _as_mutation(mutation), reason=reason, **transaction_options,
    )
    return _unwrap(result)


async def transact(
    conversation_id: str,
    mutation: Callable[[GroupState], Outcome],
    *,
    reason: str = "command",
    **transaction_options: Any,
) -> Outcome:
    """``run`` off the event loop."""
    result = await state_transaction.amutate(
        conversation_id, _as_mutation(mutation), reason=reason, **transaction_options,
    )
    return _unwrap(result)


def _as_mutation(
    mutation: Callable[[GroupState], Outcome],
) -> Callable[[state_transaction.TxContext], Outcome]:
    def apply(ctx: state_transaction.TxContext) -> Outcome:
        outcome = mutation(ctx.state)
        if not outcome.save:
            ctx.skip_save()
        return outcome

    return apply


def _unwrap(result: state_transaction.TxResult[Outcome]) -> Outcome:
    if result.outcome in (state_transaction.Outcome.CONFLICT, state_transaction.Outcome.STALE_TIMELINE):
        return Outcome(False, CONFLICT_TEXT, save=False)
    if not result.ok or result.value is None:
        return Outcome(False, CONFLICT_TEXT, save=False)
    return result.value
