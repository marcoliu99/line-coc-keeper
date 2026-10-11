"""An investigator down outside combat is not left out of the game.

Unconscious (a failed major-wound CON check, or HP 0 without a major wound, as 7e has it) with a companion still
standing: their first line is told that First Aid can bring them round, and the next one wakes them after a time
skip (house rule). A table where nobody gives First Aid otherwise kept one player out for 44 turns (Haunting
2026-10-12). Nobody left standing: the line wakes them at once. A dying investigator (HP 0 with a major wound) waits
for First Aid every time; the combat obligations decide whether they live. Dead is not this rule.
"""
from __future__ import annotations

from typing import Literal

from app import combat
from app.models import Character, GroupState, InjuryState
from app.repositories import state_transaction

KNOCKED_OUT_TAGS = ("昏迷", "倒地")


def knocked_out(character: Character) -> bool:
    """Down and out of the game outside combat: unconscious, or at HP 0 (which 7e makes unconscious or dying); not
    dead. HP 0 played on as if standing: Edith acted for 11 turns after a fight (Haunting 2026-10-12)."""
    injury = character.injury or {}
    if injury.get("dead"):
        return False
    return (character.hp <= 0 or bool(injury.get("dying")) or bool(injury.get("unconscious"))
            or "昏迷" in character.status_tags)


def dying(character: Character) -> bool:
    injury = character.injury or {}
    return bool(injury.get("dying")) or (character.hp <= 0 and bool(injury.get("major_wound")))


def _standing(character: Character) -> bool:
    injury = character.injury or {}
    return (character.hp > 0 and not character.away and not injury.get("unconscious")
            and not injury.get("dying") and not injury.get("dead") and "昏迷" not in character.status_tags)


def decide(state: GroupState, user_id: str) -> Literal["wake", "wait"] | None:
    """What a knocked-out investigator's line does outside combat: wake them, or wait for a standing companion."""
    actor = state.get_active_character(user_id)
    if state.combat.active or actor is None or not knocked_out(actor):
        return None
    if dying(actor):
        return "wait"
    companions = [c for c in combat.active_characters(state) if c.character_id != actor.character_id]
    if any(_standing(c) for c in companions) and not (actor.injury or {}).get("first_aid_offered"):
        return "wait"
    return "wake"


def wait_reply(character: Character) -> str:
    name = character.name
    if dying(character):
        return f"{name} 倒地瀕死，無法行動；隊友要盡快替{name}急救，否則{name}撐不了多久。"
    return (f"{name} 還昏迷著，暫時無法行動；隊友可以替{name}急救，讓{name}醒來。"
            f"若沒有人急救，{name}下一次行動時會在一段時間後自己醒來。")


def offer_first_aid(conversation_id: str, character_id: str, *, timeline_id: str | None) -> None:
    """Remember that the party was told First Aid could help, so the investigator's next line wakes them."""
    def apply(ctx: state_transaction.TxContext) -> None:
        copies = [c for c in [*ctx.state.characters.values(), *ctx.state.characters_by_id.values()]
                  if c.character_id == character_id]
        if not copies or all((c.injury or {}).get("first_aid_offered") for c in copies):
            ctx.skip_save()
            return
        for character in copies:
            character.injury = {**(character.injury or {}), "first_aid_offered": True}

    state_transaction.mutate(conversation_id, apply, reason="unconscious_first_aid_offered",
                             expected_timeline=timeline_id)


def wake_note(name: str) -> str:
    return (f"【時間跳躍】{name} 昏迷了一段時間後醒來。先依劇本描寫這段時間發生了什麼、"
            "敵人趁這段時間做了什麼，再處理接下來這句行動：")


def wake(conversation_id: str, character_id: str, *, timeline_id: str | None) -> None:
    """Clear the unconscious state on the latest committed state; the major wound itself stays, HP 0 becomes 1."""
    def apply(ctx: state_transaction.TxContext) -> None:
        # The owner-keyed and id-keyed indexes may hold separate copies of the same investigator: wake both.
        copies = {id(c): c for c in [*ctx.state.characters.values(), *ctx.state.characters_by_id.values()]
                  if c.character_id == character_id}.values()
        if not any(knocked_out(c) for c in copies):
            ctx.skip_save()
            return
        for character in copies:
            injury = InjuryState(**(character.injury or {}))
            injury.pop("first_aid_offered", None)
            character.injury = {**injury, "unconscious": False}
            character.status_tags = [tag for tag in character.status_tags if tag not in KNOCKED_OUT_TAGS]
            if character.hp <= 0:
                character.hp = 1  # come round from HP 0 after the time skip, as natural recovery would leave them

    state_transaction.mutate(conversation_id, apply, reason="unconscious_time_skip", expected_timeline=timeline_id)
