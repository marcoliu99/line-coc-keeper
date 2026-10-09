"""An investigator knocked out with no one able to help wakes after a time skip (house rule).

A failed major-wound CON check leaves an investigator unconscious with HP above 0. With a companion still standing,
First Aid can bring them round, so their player is told so at once instead of running the Executor. When nobody is
left standing the table would otherwise stall forever: their next line wakes them, HP unchanged, and the Keeper is
told to narrate the time that passed, including what the enemy did, before handling the line. HP 0 (dying or dead)
is not this rule: those follow the combat obligations, and a party all at 0 HP ends the scenario at settlement.
"""
from __future__ import annotations

from typing import Literal

from app import combat
from app.models import Character, GroupState
from app.repositories import state_transaction

KNOCKED_OUT_TAGS = ("昏迷", "倒地")


def knocked_out(character: Character) -> bool:
    """Unconscious with HP left, neither dying nor dead."""
    injury = character.injury or {}
    return (character.hp > 0 and not injury.get("dying") and not injury.get("dead")
            and (bool(injury.get("unconscious")) or "昏迷" in character.status_tags))


def _standing(character: Character) -> bool:
    injury = character.injury or {}
    return (character.hp > 0 and not character.away and not injury.get("unconscious")
            and not injury.get("dying") and not injury.get("dead") and "昏迷" not in character.status_tags)


def decide(state: GroupState, user_id: str) -> Literal["wake", "wait"] | None:
    """What a knocked-out investigator's line does outside combat: wake them, or wait for a standing companion."""
    actor = state.get_active_character(user_id)
    if state.combat.active or actor is None or not knocked_out(actor):
        return None
    companions = [c for c in combat.active_characters(state) if c.character_id != actor.character_id]
    return "wait" if any(_standing(c) for c in companions) else "wake"


def wait_reply(name: str) -> str:
    return f"{name} 還昏迷著，暫時無法行動；隊友可以替{name}急救，讓{name}醒來。"


def wake_note(name: str) -> str:
    return (f"【時間跳躍】{name} 昏迷了一段時間後醒來（HP 不變）。先依劇本描寫這段時間發生了什麼、"
            "敵人趁這段時間做了什麼，再處理接下來這句行動：")


def wake(conversation_id: str, character_id: str, *, timeline_id: str | None) -> None:
    """Clear the unconscious state on the latest committed state; the major wound itself stays."""
    def apply(ctx: state_transaction.TxContext) -> None:
        # The owner-keyed and id-keyed indexes may hold separate copies of the same investigator: wake both.
        copies = {id(c): c for c in [*ctx.state.characters.values(), *ctx.state.characters_by_id.values()]
                  if c.character_id == character_id}.values()
        if not any(knocked_out(c) for c in copies):
            ctx.skip_save()
            return
        for character in copies:
            character.injury = {**(character.injury or {}), "unconscious": False}
            character.status_tags = [tag for tag in character.status_tags if tag not in KNOCKED_OUT_TAGS]

    state_transaction.mutate(conversation_id, apply, reason="unconscious_time_skip", expected_timeline=timeline_id)
