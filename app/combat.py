"""Formal initiative-order combat tracking plus enemy combat cards.

The public API deliberately keeps the old entry points (`start_combat`,
`add_npc`, `advance_turn`, `damage_combatant`, `status_text`) so existing
commands and Keeper tools keep working. Internally, enemies now get an
`EnemyCombatCard` with armor, attacks, special abilities, usage counters, and
effect state. That gives the Keeper a code-owned plan for an enemy turn instead
of defaulting to "the nearest investigator gets punched".
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app import dice
from app.models import (
    ArmorRule,
    AttackRule,
    Combatant,
    CombatState,
    EffectState,
    EnemyCombatCard,
    GroupState,
    SpecialAbility,
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _ensure_character_identity(state: GroupState) -> None:
    """Populate the new character-id view from the legacy owner_id view.

    The implementation still lets older code read/write `state.characters`, but
    combat uses stable character ids where possible so a user's primary,
    partner, and test characters can eventually coexist without overwriting each
    other.
    """
    if not state.characters_by_id:
        for owner_id, char in state.characters.items():
            if not char.character_id:
                char.character_id = f"legacy-user:{owner_id}"
            state.characters_by_id[char.character_id] = char
    for owner_id, char in state.characters.items():
        if not char.character_id:
            char.character_id = f"legacy-user:{owner_id}"
        state.active_character_id_by_user.setdefault(owner_id, char.character_id)


def active_characters(state: GroupState):
    _ensure_character_identity(state)
    by_id = state.characters_by_id or {c.character_id: c for c in state.characters.values()}
    active_ids = set(state.active_character_id_by_user.values())
    if not active_ids:
        return list(state.characters.values())
    return [c for cid, c in by_id.items() if cid in active_ids and c.active]


def _seed_from_characters(state: GroupState) -> list[Combatant]:
    return [
        Combatant(
            name=c.name,
            display_name=c.name,
            dex=c.dex,
            hp=c.hp,
            hp_max=c.hp_max,
            is_pc=True,
            side="pc",
            character_id=c.character_id or f"legacy-user:{c.owner_id}",
            combatant_id=f"pc:{c.character_id or f'legacy-user:{c.owner_id}'}",
        )
        for c in active_characters(state)
        if c.hp > 0 and not c.away
    ]


def _ensure_started(state: GroupState) -> None:
    _ensure_character_identity(state)
    if not state.combat.active:
        state.combat = CombatState(active=True, round_number=1, order=_seed_from_characters(state), current_index=0)


def start_combat(state: GroupState) -> CombatState:
    _ensure_started(state)
    return state.combat


def _default_attack() -> AttackRule:
    return AttackRule(id="unarmed", label="徒手攻擊", skill_name="格鬥（鬥毆）", skill_value=25, damage="1D3", range_band="engaged")


def _coerce_armor(raw: list[dict[str, Any]] | None) -> list[ArmorRule]:
    return [ArmorRule.from_dict(a) for a in (raw or [])]


def _coerce_attacks(raw: list[dict[str, Any]] | None) -> list[AttackRule]:
    attacks = [AttackRule.from_dict(a) for a in (raw or [])]
    return attacks or [_default_attack()]


def _coerce_abilities(raw: list[dict[str, Any]] | None) -> list[SpecialAbility]:
    return [SpecialAbility.from_dict(a) for a in (raw or [])]


def _enemy_card_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower() or "enemy"
    return f"enemy-{slug}-{uuid.uuid4().hex[:8]}"


def create_enemy_card(
    state: GroupState,
    name: str,
    *,
    dex: int = 50,
    hp: int = 10,
    armor: list[dict[str, Any]] | None = None,
    attacks: list[dict[str, Any]] | None = None,
    abilities: list[dict[str, Any]] | None = None,
    stats: dict[str, int] | None = None,
    skills: dict[str, int] | None = None,
    hidden_notes: str = "",
    public_description: str = "",
    source: dict[str, Any] | None = None,
    incomplete: bool = False,
) -> EnemyCombatCard:
    _ensure_started(state)
    card = EnemyCombatCard(
        id=_enemy_card_id(name),
        name=name,
        aliases=[],
        source=source or {},
        hp=max(0, hp),
        hp_max=max(1, hp),
        armor=_coerce_armor(armor),
        attacks=_coerce_attacks(attacks),
        abilities=_coerce_abilities(abilities),
        stats={"DEX": dex, **(stats or {})},
        skills=skills or {},
        hidden_notes=hidden_notes,
        public_description=public_description,
        incomplete=incomplete,
    )
    state.combat.enemy_cards[card.id] = card
    return card


def add_enemy_card_to_combat(state: GroupState, card_id: str) -> CombatState:
    _ensure_started(state)
    card = state.combat.enemy_cards[card_id]
    current_id = state.combat.order[state.combat.current_index].combatant_id if state.combat.order else None
    dex = int(card.stats.get("DEX", 50))
    state.combat.order.append(
        Combatant(
            name=card.name,
            display_name=card.name,
            dex=dex,
            hp=card.hp,
            hp_max=card.hp_max,
            is_pc=False,
            is_ally=False,
            side="enemy",
            enemy_card_id=card.id,
            combatant_id=f"enemy:{card.id}",
        )
    )
    state.combat.order.sort(key=lambda c: -c.dex)
    if current_id:
        for i, c in enumerate(state.combat.order):
            if c.combatant_id == current_id:
                state.combat.current_index = i
                break
    return state.combat


def add_npc(
    state: GroupState,
    name: str,
    dex: int,
    hp: int,
    is_ally: bool = False,
    *,
    armor: list[dict[str, Any]] | None = None,
    attacks: list[dict[str, Any]] | None = None,
    abilities: list[dict[str, Any]] | None = None,
) -> CombatState:
    _ensure_started(state)
    if is_ally:
        current_id = state.combat.order[state.combat.current_index].combatant_id if state.combat.order else None
        state.combat.order.append(
            Combatant(name=name, dex=dex, hp=hp, hp_max=max(1, hp), is_pc=False, is_ally=True, side="ally")
        )
        state.combat.order.sort(key=lambda c: -c.dex)
        if current_id:
            for i, c in enumerate(state.combat.order):
                if c.combatant_id == current_id:
                    state.combat.current_index = i
                    break
        return state.combat

    card = create_enemy_card(
        state,
        name,
        dex=dex,
        hp=hp,
        armor=armor,
        attacks=attacks,
        abilities=abilities,
        incomplete=not attacks and not abilities,
    )
    return add_enemy_card_to_combat(state, card.id)


def _find_combatant(state: GroupState, name: str) -> Combatant | None:
    norm = _normalize(name)
    for c in state.combat.order:
        names = [c.name, c.display_name, c.combatant_id, c.enemy_card_id, c.character_id]
        if norm and any(norm == _normalize(n) or norm in _normalize(n) or _normalize(n) in norm for n in names if n):
            return c
    return None


def _card_for(state: GroupState, combatant: Combatant) -> EnemyCombatCard | None:
    if combatant.enemy_card_id:
        return state.combat.enemy_cards.get(combatant.enemy_card_id)
    return None


def _sync_combatant_from_card(combatant: Combatant, card: EnemyCombatCard) -> None:
    combatant.hp = card.hp
    combatant.hp_max = card.hp_max
    combatant.defeated = card.hp <= 0


def _sync_pc_hp(state: GroupState, combatant: Combatant) -> None:
    if combatant.is_pc:
        pc = state.get_character_by_name(combatant.name)
        if pc:
            pc.hp = combatant.hp
        if combatant.character_id and combatant.character_id in state.characters_by_id:
            state.characters_by_id[combatant.character_id].hp = combatant.hp


def _pc_for_combatant(state: GroupState, combatant: Combatant):
    if not combatant.is_pc:
        return None
    if combatant.character_id and combatant.character_id in state.characters_by_id:
        return state.characters_by_id[combatant.character_id]
    return state.get_character_by_name(combatant.name)


def _register_major_wound_check(state: GroupState, combatant: Combatant, final_damage: int, hp_after: int) -> bool:
    pc = _pc_for_combatant(state, combatant)
    if not pc or hp_after <= 0:
        return False
    if final_damage < pc.hp_max / 2:
        return False
    state.pending_checks[pc.owner_id] = {
        "type": "skill",
        "skill": "CON",
        "skill_value": pc.con,
        "bonus_dice": 0,
        "penalty_dice": 0,
        "difficulty": "regular",
        "major_wound_trigger": True,
    }
    return True


def _armor_reduction(card: EnemyCombatCard | None, damage_type: str, tags: list[str]) -> tuple[int, str]:
    if not card:
        return 0, ""
    best = 0
    label = ""
    tag_set = set(tags or [])
    for armor in card.armor:
        if armor.applies_to not in ("all", damage_type):
            continue
        if set(armor.bypass_tags) & tag_set:
            continue
        if armor.value > best:
            best = armor.value
            label = armor.label
    return best, label


def apply_combat_damage(
    state: GroupState,
    target_name: str,
    raw_damage: int,
    *,
    damage_type: str = "physical",
    tags: list[str] | None = None,
    source_id: str = "",
) -> dict[str, Any]:
    combatant = _find_combatant(state, target_name)
    if not combatant:
        return {"ok": False, "error": f"戰鬥中找不到「{target_name}」"}
    card = _card_for(state, combatant)
    armor, armor_label = _armor_reduction(card, damage_type, tags or [])
    final = max(0, int(raw_damage) - armor)
    before = combatant.hp
    after = max(0, before - final)
    combatant.hp = after
    combatant.defeated = after <= 0
    if card:
        card.hp = after
        card.status_tags = [t for t in card.status_tags if t]
    _sync_pc_hp(state, combatant)
    major_wound = _register_major_wound_check(state, combatant, final, after)
    return {
        "ok": True,
        "target": combatant.display_name,
        "target_id": combatant.combatant_id,
        "raw_damage": raw_damage,
        "damage_type": damage_type,
        "armor_reduction": armor,
        "armor_label": armor_label,
        "weakness_bonus": 0,
        "final_damage": final,
        "hp_before": before,
        "hp_after": after,
        "hp": after,
        "hp_max": combatant.hp_max,
        "major_wound_triggered": major_wound,
        "defeated": combatant.defeated,
        "public_summary": f"{combatant.display_name} 受到 {final} 點傷害" + ("（部分傷害被擋下）" if armor else ""),
        "private_notes": f"raw={raw_damage}, armor={armor_label or '-'}:{armor}, source={source_id}",
    }


def damage_combatant(state: GroupState, name: str, delta: int) -> dict:
    combatant = _find_combatant(state, name)
    if not combatant:
        return {"ok": False, "error": f"戰鬥中找不到「{name}」"}

    if delta < 0:
        return apply_combat_damage(state, name, -delta)

    before = combatant.hp
    combatant.hp = max(0, min(combatant.hp_max, combatant.hp + delta))
    combatant.defeated = combatant.hp <= 0
    card = _card_for(state, combatant)
    if card:
        card.hp = combatant.hp
    _sync_pc_hp(state, combatant)

    return {
        "ok": True,
        "name": combatant.display_name,
        "hp": combatant.hp,
        "hp_max": combatant.hp_max,
        "hp_before": before,
        "defeated": combatant.defeated,
    }


def _is_skippable(state: GroupState, combatant: Combatant) -> bool:
    if combatant.defeated:
        return True
    if combatant.is_pc:
        if combatant.character_id and combatant.character_id in state.characters_by_id:
            return bool(state.characters_by_id[combatant.character_id].away)
        pc = state.get_character_by_name(combatant.name)
        if pc and pc.away:
            return True
    return False


def _reset_round_usage(state: GroupState) -> None:
    for card in state.combat.enemy_cards.values():
        for ability in card.abilities:
            ability.usage["used_this_round"] = 0
            if ability.current_cooldown > 0:
                ability.current_cooldown -= 1


def _tick_effect(effect: EffectState) -> None:
    if effect.remaining_rounds is not None:
        effect.remaining_rounds -= 1


def resolve_effect_damage(expression: str) -> int:
    expr = (expression or "").strip()
    if not expr:
        return 0
    if re.fullmatch(r"\d+", expr):
        return int(expr)
    return dice.roll_expression(expr).total


def _validate_effect_damage_expression(expression: str) -> str | None:
    expr = (expression or "").strip()
    if not expr or re.fullmatch(r"\d+", expr):
        return None
    m = re.fullmatch(r"(\d*)d(\d+)\s*([+-]\s*\d+)?", expr, re.IGNORECASE)
    if not m:
        return f"無法解析骰子表示式: {expression!r}（範例：1d100、3d6+2）"
    n = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    if n < 1 or n > 100:
        return "骰子數量必須介於 1 到 100 之間"
    if sides < 2 or sides > 1000:
        return "骰子面數必須介於 2 到 1000 之間"
    return None


def add_combat_effect(
    state: GroupState,
    target_name: str,
    label: str,
    *,
    timing: str = "turn_start",
    damage: str = "",
    damage_type: str = "physical",
    remaining_rounds: int | None = None,
    tags: list[str] | None = None,
    source_id: str = "",
    public_description: str = "",
) -> dict[str, Any]:
    combatant = _find_combatant(state, target_name)
    if not combatant:
        return {"ok": False, "error": f"戰鬥中找不到「{target_name}」"}
    if timing not in {"round_start", "turn_start", "turn_end", "round_end"}:
        return {"ok": False, "error": f"不支援的效果時點：{timing}"}
    if remaining_rounds is not None and remaining_rounds < 1:
        return {"ok": False, "error": "remaining_rounds 必須大於 0，或省略表示無限期"}
    damage_error = _validate_effect_damage_expression(damage)
    if damage_error:
        return {"ok": False, "error": f"無法解析效果傷害：{damage_error}"}

    effect = EffectState(
        id=f"effect-{uuid.uuid4().hex[:8]}",
        label=label,
        source_id=source_id,
        target_id=combatant.combatant_id,
        timing=timing,
        remaining_rounds=remaining_rounds,
        damage=damage,
        damage_type=damage_type,
        tags=tags or [],
        public_description=public_description,
    )
    state.combat.effects.append(effect)
    return {
        "ok": True,
        "effect_id": effect.id,
        "target": combatant.display_name,
        "target_id": combatant.combatant_id,
        "label": effect.label,
        "timing": effect.timing,
        "remaining_rounds": effect.remaining_rounds,
        "damage": effect.damage,
        "damage_type": effect.damage_type,
        "tags": effect.tags,
    }


def process_timing(state: GroupState, timing: str, target_id: str = "") -> list[dict[str, Any]]:
    """Apply fixed-timing effects. This first pass supports damage effects.

    More effect types can be added without changing the turn-order API.
    """
    results: list[dict[str, Any]] = []
    remaining: list[EffectState] = []
    for effect in state.combat.effects:
        applies = effect.timing == timing and (not target_id or effect.target_id == target_id)
        applied = False
        if applies and effect.damage:
            try:
                raw = resolve_effect_damage(effect.damage)
            except ValueError as exc:
                results.append({
                    "ok": False,
                    "effect_id": effect.id,
                    "target_id": effect.target_id,
                    "error": f"無法解析效果傷害：{exc}",
                })
            else:
                result = apply_combat_damage(
                    state,
                    effect.target_id,
                    raw,
                    damage_type=effect.damage_type,
                    tags=effect.tags,
                    source_id=effect.source_id,
                )
                result["effect_id"] = effect.id
                results.append(result)
                applied = result.get("ok") is True
        elif applies:
            applied = True
        if applied:
            _tick_effect(effect)
        if effect.remaining_rounds is None or effect.remaining_rounds > 0:
            remaining.append(effect)
    state.combat.effects = remaining
    return results


def _trigger_matches(ability: SpecialAbility, card: EnemyCombatCard) -> bool:
    usage = ability.usage
    if usage.get("per_combat") is not None and usage.get("used_total", 0) >= usage["per_combat"]:
        return False
    if usage.get("per_round") is not None and usage.get("used_this_round", 0) >= usage["per_round"]:
        return False
    if ability.current_cooldown > 0:
        return False
    trigger = ability.trigger or {}
    t = trigger.get("type", "on_enemy_turn")
    if t in ("first_available", "on_enemy_turn"):
        return True
    if t == "hp_below":
        return card.hp <= int(trigger.get("value", card.hp_max))
    if t == "state_missing":
        missing = trigger.get("tag")
        return bool(missing and missing not in card.status_tags)
    return True


def _safe_public_ability_hint(ability: SpecialAbility) -> str:
    reveal = ability.reveal_policy or {}
    public_name = reveal.get("player_facing_name", "")
    if public_name:
        return f"{public_name} 開始生效。"
    return "它展現出某種異常能力，但具體規則仍未明朗。"


def _choose_target(state: GroupState) -> str:
    for c in state.combat.order:
        if c.side == "pc" and not _is_skippable(state, c):
            return c.combatant_id
    return ""


def plan_enemy_turn(state: GroupState, enemy_name: str = "") -> dict[str, Any]:
    combat = state.combat
    if not combat.active or not combat.order:
        return {"ok": False, "error": "目前沒有進行中的戰鬥"}
    combatant = _find_combatant(state, enemy_name) if enemy_name else combat.order[combat.current_index]
    if not combatant or combatant.side != "enemy":
        return {"ok": False, "error": "目前輪到的不是敵人，或找不到指定敵人"}
    card = _card_for(state, combatant)
    if not card:
        return {"ok": False, "error": f"敵人「{combatant.display_name}」沒有戰鬥卡"}

    process_timing(state, "turn_start", combatant.combatant_id)

    target_id = _choose_target(state)
    for ability in sorted(card.abilities, key=lambda a: -a.priority):
        if _trigger_matches(ability, card):
            plan_id = f"plan-{uuid.uuid4().hex[:8]}"
            plan = {
                "ok": True,
                "plan_id": plan_id,
                "enemy_card_id": card.id,
                "enemy": card.name,
                "selected_action": "special_ability",
                "selected_id": ability.id,
                "target_ids": [target_id] if target_id else [],
                "required_rolls": [ability.check] if ability.check else [],
                "private_reason": f"special ability {ability.name} trigger matched; usage={ability.usage}",
                "public_hint": _safe_public_ability_hint(ability),
            }
            combat.plans[plan_id] = plan
            return plan

    attack = next((a for a in card.attacks if a.range_band in ("engaged", "near", "any")), None)
    if attack:
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        plan = {
            "ok": True,
            "plan_id": plan_id,
            "enemy_card_id": card.id,
            "enemy": card.name,
            "selected_action": "attack",
            "selected_id": attack.id,
            "target_ids": [target_id] if target_id else [],
            "required_rolls": [{
                "type": "skill",
                "skill_name": attack.skill_name,
                "skill_value": attack.skill_value,
                "damage": attack.damage,
            }],
            "private_reason": "no usable special ability; selected available attack",
            "public_hint": attack.public_description or f"{card.name} 準備攻擊。",
        }
        combat.plans[plan_id] = plan
        return plan

    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    plan = {
        "ok": True,
        "plan_id": plan_id,
        "enemy_card_id": card.id,
        "enemy": card.name,
        "selected_action": "move",
        "selected_id": "",
        "target_ids": [target_id] if target_id else [],
        "required_rolls": [],
        "private_reason": "no usable special ability or attack in current abstract range",
        "public_hint": f"{card.name} 調整位置，尋找下一次出手機會。",
    }
    combat.plans[plan_id] = plan
    return plan


def resolve_enemy_action(state: GroupState, plan_id: str) -> dict[str, Any]:
    plan = state.combat.plans.get(plan_id)
    if not plan:
        return {"ok": False, "error": f"找不到行動計畫 {plan_id}"}
    if plan.get("resolved"):
        return {"ok": True, "plan_id": plan_id, "resolved": True, "already_resolved": True}
    card = state.combat.enemy_cards.get(plan["enemy_card_id"])
    if not card:
        return {"ok": False, "error": "行動計畫對應的敵人卡不存在"}
    if plan["selected_action"] == "special_ability":
        ability = next((a for a in card.abilities if a.id == plan["selected_id"]), None)
        if ability:
            ability.usage["used_total"] = ability.usage.get("used_total", 0) + 1
            ability.usage["used_this_round"] = ability.usage.get("used_this_round", 0) + 1
            ability.current_cooldown = ability.cooldown_rounds
    plan["resolved"] = True
    return {"ok": True, "plan_id": plan_id, "resolved": True}


def advance_turn(state: GroupState) -> dict:
    combat = state.combat
    if not combat.active or not combat.order:
        return {"ok": False, "error": "目前沒有進行中的戰鬥"}
    if all(_is_skippable(state, c) for c in combat.order):
        return {"ok": False, "error": "所有戰鬥角色都已倒下或暫離，戰鬥應該結束了，請呼叫 end_combat 結束戰鬥"}

    current = combat.order[combat.current_index]
    process_timing(state, "turn_end", current.combatant_id)

    n = len(combat.order)
    for _ in range(n):
        combat.current_index = (combat.current_index + 1) % n
        if combat.current_index == 0:
            combat.round_number += 1
            _reset_round_usage(state)
            process_timing(state, "round_start")
        if not _is_skippable(state, combat.order[combat.current_index]):
            break

    current = combat.order[combat.current_index]
    return {
        "ok": True,
        "round": combat.round_number,
        "current_turn": current.display_name,
        "combatant_id": current.combatant_id,
        "side": current.side,
        "hp": current.hp,
        "hp_max": current.hp_max,
    }


def end_combat(state: GroupState) -> None:
    state.combat = CombatState()


def status_text(state: GroupState, include_private: bool = False) -> str:
    combat = state.combat
    if not combat.active or not combat.order:
        return "目前沒有進行中的戰鬥。"

    lines = [f"戰鬥中 - 第 {combat.round_number} 輪"]
    for i, c in enumerate(combat.order):
        card = _card_for(state, c)
        if card:
            _sync_combatant_from_card(c, card)
        skippable = _is_skippable(state, c)
        marker = "=> " if i == combat.current_index and not skippable else "   "
        away = c.is_pc and not c.defeated and skippable
        tag = "（倒下）" if c.defeated else "（暫離）" if away else ""
        side = "我方" if c.side == "pc" else "隊友" if c.side == "ally" else "敵方"
        line = f"{marker}{c.display_name} [{side}] DEX {c.dex} HP {c.hp}/{c.hp_max}{tag}"
        if include_private and card:
            if card.armor:
                line += " 護甲:" + ",".join(f"{a.label} {a.value}" for a in card.armor)
            if card.abilities:
                line += " 能力:" + ",".join(a.name for a in card.abilities)
            if card.incomplete:
                line += "（戰鬥卡未完整）"
        lines.append(line)
    return "\n".join(lines)
