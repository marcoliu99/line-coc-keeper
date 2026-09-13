"""Formal initiative-order combat tracking.

Kept as explicit, code-owned state (round number, turn order, HP) rather than left
to the Keeper's own judgement, so multi-combatant fights don't lose track of whose
turn it is or drift on HP. Both the Keeper's tool-use loop (app/keeper.py) and
direct `/coc combat ...` chat commands (app/main.py) call into this module.
"""
from __future__ import annotations

from app.models import Combatant, CombatState, GroupState


def _seed_from_characters(state: GroupState) -> list[Combatant]:
    return [
        Combatant(name=c.name, dex=c.dex, hp=c.hp, hp_max=c.hp_max, is_pc=True)
        for c in state.characters.values()
        if c.hp > 0
    ]


def _ensure_started(state: GroupState) -> None:
    if not state.combat.active:
        state.combat = CombatState(active=True, round_number=1, order=_seed_from_characters(state), current_index=0)


def start_combat(state: GroupState) -> CombatState:
    _ensure_started(state)
    return state.combat


def add_npc(state: GroupState, name: str, dex: int, hp: int, is_ally: bool = False) -> CombatState:
    _ensure_started(state)
    current_name = state.combat.order[state.combat.current_index].name if state.combat.order else None

    state.combat.order.append(Combatant(name=name, dex=dex, hp=hp, hp_max=max(1, hp), is_pc=False, is_ally=is_ally))
    state.combat.order.sort(key=lambda c: -c.dex)

    if current_name:
        for i, c in enumerate(state.combat.order):
            if c.name == current_name:
                state.combat.current_index = i
                break
    return state.combat


def _find_combatant(state: GroupState, name: str) -> Combatant | None:
    norm = name.strip().lower()
    for c in state.combat.order:
        if norm and (norm == c.name.lower() or norm in c.name.lower() or c.name.lower() in norm):
            return c
    return None


def damage_combatant(state: GroupState, name: str, delta: int) -> dict:
    combatant = _find_combatant(state, name)
    if not combatant:
        return {"ok": False, "error": f"戰鬥中找不到「{name}」"}

    combatant.hp = max(0, min(combatant.hp_max, combatant.hp + delta))
    combatant.defeated = combatant.hp <= 0

    if combatant.is_pc:
        pc = state.get_character_by_name(combatant.name)
        if pc:
            pc.hp = combatant.hp

    return {
        "ok": True,
        "name": combatant.name,
        "hp": combatant.hp,
        "hp_max": combatant.hp_max,
        "defeated": combatant.defeated,
    }


def _is_skippable(state: GroupState, combatant: Combatant) -> bool:
    if combatant.defeated:
        return True
    if combatant.is_pc:
        pc = state.get_character_by_name(combatant.name)
        if pc and pc.away:
            return True  # player stepped out (/coc away) — don't stall the group waiting on them
    return False


def advance_turn(state: GroupState) -> dict:
    combat = state.combat
    if not combat.active or not combat.order:
        return {"ok": False, "error": "目前沒有進行中的戰鬥"}
    if all(_is_skippable(state, c) for c in combat.order):
        return {"ok": False, "error": "所有戰鬥角色都已倒下或暫離，戰鬥應該結束了，請呼叫 end_combat 結束戰鬥"}

    n = len(combat.order)
    for _ in range(n):
        combat.current_index = (combat.current_index + 1) % n
        if combat.current_index == 0:
            combat.round_number += 1
        if not _is_skippable(state, combat.order[combat.current_index]):
            break

    current = combat.order[combat.current_index]
    return {"ok": True, "round": combat.round_number, "current_turn": current.name, "hp": current.hp, "hp_max": current.hp_max}


def end_combat(state: GroupState) -> None:
    state.combat = CombatState()


def status_text(state: GroupState) -> str:
    combat = state.combat
    if not combat.active or not combat.order:
        return "目前沒有進行中的戰鬥。"

    lines = [f"⚔️ 戰鬥中 - 第 {combat.round_number} 輪"]
    for i, c in enumerate(combat.order):
        skippable = _is_skippable(state, c)
        marker = "👉 " if i == combat.current_index and not skippable else "　　"
        away = c.is_pc and not c.defeated and skippable
        tag = "（倒下）" if c.defeated else "（暫離）" if away else ""
        side = "我方" if c.is_pc else "隊友" if c.is_ally else "敵方"
        lines.append(f"{marker}{c.name}［{side}］DEX {c.dex}　HP {c.hp}/{c.hp_max}{tag}")
    return "\n".join(lines)
