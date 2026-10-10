"""Formal initiative-order combat tracking plus enemy combat cards.

The public API deliberately keeps the old entry points (`start_combat`,
`add_npc`, `advance_turn`, `damage_combatant`, `status_text`) so existing
commands and Keeper tools keep working. Internally, enemies now get an
`EnemyCombatCard` with armor, attacks, special abilities, usage counters, and
effect state. That gives the Keeper a code-owned plan for an enemy turn instead
of defaulting to "the nearest investigator gets punched".
"""
from __future__ import annotations

import logging
import random
import re
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Any, Protocol

from app import (
    check_lifecycle,
    checkpoints,
    combat_resources,
    dice,
    observability,
    spoiler_policy,
)
from app.check_identity import PendingCheckBlocker
from app.models import (
    ArmorRule,
    AttackRule,
    Character,
    CombatAction,
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
    for owner_id, char in state.characters.items():
        if not char.character_id:
            char.character_id = f"legacy-user:{owner_id}"
        canonical = state.characters_by_id.get(char.character_id)
        if canonical is None:
            state.characters_by_id[char.character_id] = char
        else:
            state.characters[owner_id] = canonical
        state.active_character_id_by_user.setdefault(owner_id, char.character_id)


def active_characters(state: GroupState):
    _ensure_character_identity(state)
    by_id = state.characters_by_id or {c.character_id: c for c in state.characters.values()}
    active_ids = set(state.active_character_id_by_user.values())
    if not active_ids:
        return list(state.characters.values())
    return [c for cid, c in by_id.items() if cid in active_ids and c.active]


def _seed_from_characters(state: GroupState) -> list[Combatant]:
    combatants = [
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
    return sorted(combatants, key=lambda combatant: -combatant.dex)


NO_ONE_CAN_FIGHT = ("所有調查員都已倒下（HP 0）或不在場，沒有人能參戰：不要開戰，"
                    "改以敘事處理劇本寫的後果（例如敵人怎麼對待倒地的調查員）。")


def refuse_fight_without_investigators(state: GroupState) -> None:
    """A new fight with no investigator able to act has no player turn and can never settle: refuse it."""
    if not state.combat.active and active_characters(state) and not _seed_from_characters(state):
        raise combat_resources.CombatAdmissionError(NO_ONE_CAN_FIGHT)


def _ensure_started(state: GroupState) -> None:
    _ensure_character_identity(state)
    if not state.combat.active:
        state.last_combat_report = {}
        if state.combat.combat_id and state.combat.phase not in {'CLOSED', 'ROLLED_BACK'}:
            raise combat_resources.CombatAdmissionError('Another combat is unclosed')
        state.combat = CombatState(active=True, round_number=1, order=_seed_from_characters(state), current_index=0)
        combat_resources.initialize_working_state(state, new_combat=True)
        combat_resources.admit_continuing_state(state)
        # A new battle has no effects yet, so its first fixed timing has
        # nothing to apply; recording it as processed is all that is left.
        state.combat.processed_timings.append(timing_key(state, "round_start", ""))
        _mark_round_start_abilities(state)


def start_combat(state: GroupState) -> CombatState:
    _ensure_started(state)
    combat_resources.initialize_working_state(state)
    return state.combat


logger = logging.getLogger(__name__)

# What an armor rule is matched against. Every weapon hit the engine resolves is "physical"; "all" covers it and
# anything else; "magic" is armor that stops only spells, so no weapon hit.
ARMOR_SCOPES = ("all", "physical", "magic")
_PHYSICAL_WORDS = ("non-magic", "nonmagic", "non_magic", "physical", "weapon", "melee", "ranged", "bullet", "blow",
                   "物理", "實體", "非魔法", "武器", "近戰", "射擊")
_MAGIC_WORDS = ("magic", "spell", "魔法", "法術")
# Wording that negates or excepts magic ("non magical", "all attacks except for magic", "not affected by magic",
# "魔法以外") is armor against everything but spells: a negation word (a whole word in English, so "cannot" is not
# "not") together with any magic word reads so.
_NEGATION = re.compile(r"\b(?:non|not|except|excluding|without|but|unless|other than)\b|以外|除外|之外|非|除了|不含|不受")


_DAMAGE_TYPE_TOKEN = re.compile(r"[a-z][a-z_]*")
_ALL_WORDS = ("any", "anything", "everything", "all_attacks", "default", "none")


def armor_scope(raw: Any) -> str:
    """The scope an armor rule applies to, from whatever the Keeper wrote. The entry used to be free text, and a
    Keeper reading "armor against non-magical attacks" wrote that: a scope the engine never matched, so the armor
    covered nothing. Anything not a known scope reads as ``all``, wording for weapons or non-magical as ``physical``,
    spells as ``magic``; a single word such as ``fire`` stays the damage type it names, matched exactly as before."""
    text = str(raw or "").strip().lower()
    if text in ARMOR_SCOPES:
        return text
    magic = any(word in text for word in _MAGIC_WORDS)
    if (magic and _NEGATION.search(text)) or any(word in text for word in _PHYSICAL_WORDS):
        return "physical"
    if magic:
        return "magic"
    if _DAMAGE_TYPE_TOKEN.fullmatch(text) and text not in _ALL_WORDS:
        return text  # a specific damage type an attack or effect may carry ("fire"), matched exactly as before
    return "all"


def _default_attack() -> AttackRule:
    return AttackRule(id="unarmed", label="徒手攻擊", skill_name="格鬥（鬥毆）", skill_value=25, damage="1D3", range_band="engaged")


def _coerce_armor(raw: list[dict[str, Any]] | None) -> list[ArmorRule]:
    """Armor rules for a new card. A value written as dice ("2D6", Corbitt's Flesh Ward) is rolled once, here, when
    the enemy is registered: the Keeper does not roll it in public first, and a turn retried does not roll it again."""
    rules = []
    for entry in raw or []:
        data = dict(entry)
        value = data.get("value", 0)
        if isinstance(value, str) and not value.strip().isdigit():
            try:
                rolled = dice.roll_expression(value.strip())
            except ValueError:
                raise ValueError(
                    f"護甲「{data.get('label') or data.get('id') or ''}」的 value 只能填數字或骰子表示式（例如 2D6），收到 {value!r}。"
                ) from None
            data["value"], data["rolled_from"] = max(0, rolled.total), value.strip()
        else:
            data["value"] = int(value)
        data["applies_to"] = armor_scope(data.get("applies_to"))
        rules.append(ArmorRule.from_dict(data))
    return rules


def _log_armor(enemy: str, rules: list[ArmorRule]) -> None:
    """One log line per registered rule, so a run's log shows what the Keeper wrote; no player text carries it."""
    for rule in rules:
        logger.info("combat.armor.registered enemy=%s label=%s value=%d applies_to=%s depletes=%s rolled_from=%s",
                    enemy, rule.label or rule.id, rule.value, rule.applies_to, rule.depletes, rule.rolled_from or "-")


def _coerce_attacks(raw: list[dict[str, Any]] | None) -> list[AttackRule]:
    attacks = [AttackRule.from_dict(a) for a in (raw or [])]
    for attack in attacks:
        try:
            dice.max_expression_value(attack.damage)
        except ValueError:
            raise ValueError(
                f"攻擊「{attack.label or attack.id}」的 damage 只能填骰子表示式（例如 1D4+2），收到 {attack.damage!r}。"
                "極限成功的額外傷害不要寫在 damage；請在 source.extreme_rule 填 maximum，穿刺類武器填 impale。"
            ) from None
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
    coerced_attacks = _coerce_attacks(attacks)  # refuse a bad damage string before the fight is touched
    _ensure_started(state)
    card = EnemyCombatCard(
        id=_enemy_card_id(name),
        name=name,
        aliases=[],
        source=source or {},
        hp=max(0, hp),
        hp_max=max(1, hp),
        armor=_coerce_armor(armor),
        attacks=coerced_attacks,
        abilities=_coerce_abilities(abilities),
        stats={"DEX": dex, **(stats or {})},
        skills=skills or {},
        hidden_notes=hidden_notes,
        public_description=public_description,
        incomplete=incomplete,
    )
    _log_armor(name, card.armor)
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
    # A card entering after the initial round has already passed its
    # round_start window. It will become eligible at the next round boundary.
    # Cards added during the initial setup still get the first round marker.
    if state.combat.round_number == 1 and not any(
        key.startswith("1:") and ":turn_start:" in key
        for key in state.combat.processed_timings
    ):
        _mark_round_start_abilities(state)
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
    source: dict[str, Any] | None = None,
    skills: dict[str, int] | None = None,
) -> CombatState:
    if not is_ally:
        _coerce_attacks(attacks)  # refuse a bad damage string before the fight is touched
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
        source=source,
        skills=skills,
        incomplete=not attacks and not abilities,
    )
    return add_enemy_card_to_combat(state, card.id)


def find_combatant(state: GroupState, name: str) -> Combatant | None:
    """Look up a combatant by name/id, preferring an exact match and only
    falling back to substring matching when that's unambiguous.

    Plain substring matching alone (the old behavior) picks whichever
    combatant happens to appear first in `state.combat.order` that has ANY
    matching field — including two distinct live enemies whose names
    overlap (e.g. "深潛者" vs "深潛者頭目"), which can silently apply
    damage/effects to the wrong one with no error at all. An exact match is
    checked first and always wins when present; substring matching is kept
    as a fallback (callers do rely on referring to a combatant by a partial
    name, e.g. an NPC's short name instead of its full registered display
    name) but only returns a result when exactly one combatant matches that
    way — an ambiguous partial name returns None (not-found) instead of
    guessing, same as find_live_enemy's own docstring recommends for this
    class of problem.

    Several exact matches happen when a defeated enemy's name was added
    again (add_combatant numbers the newcomer's display name, but `name`
    stays shared): the living one is the one a name refers to."""
    norm = _normalize(name)
    if not norm:
        return None

    def _fields(c: Combatant) -> list[str]:
        return [c.name, c.display_name, c.combatant_id, c.enemy_card_id, c.character_id]

    exact = [c for c in state.combat.order if any(norm == _normalize(n) for n in _fields(c) if n)]
    if exact:
        return next((c for c in exact if not c.defeated), exact[0])

    substring_matches = [
        c for c in state.combat.order
        if any(norm in _normalize(n) or _normalize(n) in norm for n in _fields(c) if n)
    ]
    if len(substring_matches) == 1:
        return substring_matches[0]
    return None


def find_live_enemy(state: GroupState, name: str) -> Combatant | None:
    """A non-defeated enemy-side combatant whose name/display_name exactly
    matches `name` (after normalization), or None.

    Deliberately EXACT matching only, unlike `find_combatant`'s bidirectional
    substring matching — used by add_npc's caller to stop the Keeper from
    creating a second independent HP pool for a monster it already added
    (e.g. re-searching a scenario NPC and calling add_npc_to_combat again
    for the same name instead of noticing it's already in the fight). A
    defeated combatant with the same name is not matched, so a monster
    narratively returning after being killed can still be added fresh.

    Substring matching here would be actively harmful for this specific use:
    two distinct live enemies that happen to share a substring (e.g.
    "Cultist" and "Cultist Leader", or a base creature and its separately
    indexed boss variant) would falsely collide, silently preventing the
    second one from ever entering combat — the opposite failure mode from
    the one this function exists to prevent. Callers that need to also catch
    the same NPC re-added under a different scenario-index alias (e.g. "柯
    比特" vs "Walter Corbitt") should call this once per known alias rather
    than relying on substring overlap to bridge them — see
    find_live_enemy_by_any_alias.
    """
    norm = _normalize(name)
    if not norm:
        return None
    for c in state.combat.order:
        if c.side != "enemy" or c.defeated:
            continue
        if norm == _normalize(c.name) or (c.display_name and norm == _normalize(c.display_name)):
            return c
    return None


def find_npc_index_entry_exact(state: GroupState, name: str) -> dict | None:
    """Exact-match-only lookup of `name` against state.scenario_npc_index's
    entry names/aliases — no fuzzy fallback. support.find_npc_index_entry
    layers a fuzzy fallback on top of this for its HP-consistency check,
    where a wrong match only ever corrects a number. find_live_enemy_by_any_alias
    needs the exact version: it resolves every known alias of the requested
    name to also catch the same NPC re-added under a different alias, and a
    fuzzy mismatch there (e.g. matching "深潛者頭目" to the wrong sibling
    entry "深潛者（幼體）" instead of "深潛者（成年頭目）") would pull in an
    unrelated entry's aliases and use them to wrongly block a genuinely
    different enemy from being added.

    Matches case/whitespace-insensitively (same normalization as _normalize)
    so a name that differs from the registered index entry only in case or
    spacing still resolves — without this, two calls for the same NPC using
    slightly different capitalization of an alias would fail to expand to the
    same candidate set, silently reopening the duplicate-HP-pool bug this
    whole lookup exists to help prevent."""
    if not name:
        return None
    norm = _normalize(name)
    for entry in state.scenario_npc_index:
        candidates = [entry.get("name", "")] + list(entry.get("aliases") or [])
        if any(norm == _normalize(c) for c in candidates if c):
            return entry
    return None


def _index_alias_names(state: GroupState, name: str) -> set[str]:
    """`name` plus, if it exactly matches a /coc index entry, every other
    known name of that entry."""
    candidate_names = {name}
    index_entry = find_npc_index_entry_exact(state, name)
    if index_entry is not None:
        # scenario_npc_index is populated from an LLM's structured tool-call
        # output (app/scenario_index.py) — its schema declares "name"/
        # "aliases" as strings, but nothing enforces that at the Python
        # level once it's persisted. A non-string item here (e.g. a nested
        # object for a malformed alias) would raise TypeError from set.add/
        # update below — filtering to strings keeps this lookup best-effort.
        raw_candidates = [index_entry.get("name", name), *(index_entry.get("aliases") or [])]
        candidate_names.update(c for c in raw_candidates if isinstance(c, str))
    return candidate_names


def find_live_enemy_by_any_alias(state: GroupState, name: str) -> Combatant | None:
    """A non-defeated enemy-side combatant matching `name` or, if `name`
    exactly matches a /coc index entry, any of that entry's other known
    aliases — deliberately exact-match only at every step (see
    find_live_enemy's docstring for why substring/fuzzy matching would be
    actively harmful here)."""
    for candidate in _index_alias_names(state, name):
        existing = find_live_enemy(state, candidate)
        if existing is not None:
            return existing
    return None


def _defeated_enemy_by_any_alias(state: GroupState, name: str) -> Combatant | None:
    """A defeated enemy that `name`, or any /coc index alias of it, refers to."""
    names = {_normalize(n) for n in _index_alias_names(state, name)} - {""}
    for c in state.combat.order:
        if c.side == "enemy" and c.defeated and (_normalize(c.name) in names or _normalize(c.display_name) in names):
            return c
    return None


def _number_if_shared(state: GroupState, added: Combatant) -> None:
    """Give `added` a numbered display name (`深潛者 2`, …) when another
    combatant already shows the same one, so the two can be told apart.
    `name` is left as is: index lookups and HP canonicalisation use it."""
    others = [c for c in state.combat.order if c is not added]
    shown = {_normalize(c.display_name) for c in others}
    if _normalize(added.display_name) not in shown:
        return
    number = 2
    while _normalize(f"{added.name} {number}") in shown:
        number += 1
    added.display_name = f"{added.name} {number}"
    if added.side == "ally" and any(c.combatant_id == added.combatant_id for c in others):
        # Ally ids are built from the name, so same-named allies would share one.
        added.combatant_id = f"ally:{added.display_name}"


def _checkpoint_before_combat(state: GroupState) -> None:
    """Save a 開戰前 checkpoint when a fight is about to start, so rollback
    can return to the moment before it."""
    if state.combat.active:
        return
    checkpoints.create_checkpoint(
        state,
        label="開戰前",
        created_by="system",
        reason="auto_combat_start",
        event_id=f"combat-start:{state.group_id}:{state.state_revision}",
    )


def begin_combat(state: GroupState) -> CombatState:
    """Start combat, saving a 開戰前 checkpoint first if it isn't running yet."""
    _checkpoint_before_combat(state)
    return start_combat(state)


@dataclass(frozen=True)
class AddedCombatant:
    """What add_combatant did with a request."""

    combatant: Combatant  # the one added, or the live enemy reused instead
    reused: bool  # True: that enemy was already in the fight; nothing was added
    defeated_namesake: Combatant | None = None  # a defeated enemy the name also refers to
    completed_card: bool = False  # reused, and the attacks or abilities it was missing were filled in


def defeated_namesake_notice(added: AddedCombatant) -> str:
    """Tell whoever added `added` that its name also matches a defeated enemy."""
    namesake, new = added.defeated_namesake, added.combatant
    if namesake is None:
        return ""
    return (
        f"「{namesake.display_name}」先前已在這場戰鬥中被打倒；"
        f"已加入一隻新的「{new.display_name}」（HP {new.hp}）。"
    )


def _complete_card(
    state: GroupState,
    combatant: Combatant,
    *,
    attacks: list[dict[str, Any]] | None,
    abilities: list[dict[str, Any]] | None,
    source: dict[str, Any] | None,
    armor: list[dict[str, Any]] | None = None,
) -> bool:
    """Give an enemy registered without attacks or abilities the ones a second registration of it brings, so the
    turn it has been giving up is played from the next round, and its armor if it had none (rolled now, once). Its
    HP and state stay; a complete card is not changed."""
    card = card_for(state, combatant)
    if card is None or not card.incomplete or not (attacks or abilities):
        return False
    coerced_attacks, coerced_armor = _coerce_attacks(attacks), _coerce_armor(armor) if armor and not card.armor else None
    card.attacks = coerced_attacks
    if coerced_armor is not None:
        card.armor = coerced_armor
        _log_armor(card.name, coerced_armor)
    card.abilities = _coerce_abilities(abilities)
    card.source = {**card.source, **(source or {})}
    card.incomplete = False
    return True


def add_combatant(
    state: GroupState,
    name: str,
    dex: int,
    hp: int,
    *,
    is_ally: bool = False,
    armor: list[dict[str, Any]] | None = None,
    attacks: list[dict[str, Any]] | None = None,
    abilities: list[dict[str, Any]] | None = None,
    force_new_instance: bool = False,
    source: dict[str, Any] | None = None,
    skills: dict[str, int] | None = None,
) -> AddedCombatant:
    """Add an NPC or ally to the fight, starting it (with its checkpoint) if needed.

    An enemy who is already in the fight and not defeated, under `name` or
    any /coc index alias of it, is not added again, so the same monster never
    gets a second, independent HP pool; that combatant is reused instead.
    `force_new_instance` is used only after a batch has identified a second
    separately supplied individual matching one already processed in that
    same batch. Other callers retain the normal active-enemy reuse guard.
    Allies are never de-duplicated. A defeated enemy's name can be added
    again, since a second monster of the same kind may arrive, but the result
    reports the defeated namesake so the caller can ask whether it's really a
    new one, and the newcomer gets a numbered display name.
    """
    _coerce_attacks(attacks)  # a bad damage string is refused before the fight is touched
    if not state.combat.active:
        begin_combat(state)
    if not is_ally and not force_new_instance:
        existing = find_live_enemy_by_any_alias(state, name)
        if existing is not None:
            return AddedCombatant(existing, reused=True, completed_card=_complete_card(
                state, existing, attacks=attacks, abilities=abilities, source=source, armor=armor))
    namesake = None if is_ally else _defeated_enemy_by_any_alias(state, name)
    _checkpoint_before_combat(state)
    before = {id(c) for c in state.combat.order}
    add_npc(state, name, dex, hp, is_ally=is_ally, armor=armor, attacks=attacks, abilities=abilities, source=source, skills=skills)
    # add_npc may also seed the investigators when it starts the fight.
    added = next(c for c in state.combat.order if id(c) not in before and not c.is_pc)
    _number_if_shared(state, added)
    return AddedCombatant(added, reused=False, defeated_namesake=namesake)


def card_for(state: GroupState, combatant: Combatant) -> EnemyCombatCard | None:
    if combatant.enemy_card_id:
        return state.combat.enemy_cards.get(combatant.enemy_card_id)
    return None


def _sync_combatant_from_card(combatant: Combatant, card: EnemyCombatCard) -> None:
    combatant.hp = card.hp
    combatant.hp_max = card.hp_max
    combatant.defeated = card.hp <= 0


def sync_pc_hp(state: GroupState, combatant: Combatant) -> None:
    if combatant.is_pc:
        if combatant.character_id and combatant.character_id in state.characters_by_id:
            state.characters_by_id[combatant.character_id].hp = combatant.hp
            return
        pc = state.get_character_by_name(combatant.name)
        if pc:
            pc.hp = combatant.hp


def _pc_for_combatant(state: GroupState, combatant: Combatant):
    if not combatant.is_pc:
        return None
    if combatant.character_id and combatant.character_id in state.characters_by_id:
        return state.characters_by_id[combatant.character_id]
    return state.get_character_by_name(combatant.name)


def major_wound_pc(
    state: GroupState, combatant: Combatant, final_damage: int, hp_after: int
) -> Character | None:
    """The investigator this hit gives a major wound, if any.

    A major wound includes hits reaching zero HP. A fatal single hit needs
    no CON check because death is already established.
    """
    pc = _pc_for_combatant(state, combatant)
    if not pc or final_damage >= pc.hp_max or final_damage < pc.hp_max / 2:
        return None
    return pc


def major_wound_blocked(
    state: GroupState, pc: Character, blocker: PendingCheckBlocker, *, entry_point: str
) -> dict[str, Any]:
    """Reject a major-wound hit whose CON check the player can't take yet.

    A player holds one pending check at a time and none during a Luck
    decision, so the owed CON check has nowhere to go. The hit is refused
    before any state changes and the Keeper re-applies it once the existing
    check resolves (docs/specs/bug/major_wound_con_check_gate_design_spec.md).
    """
    existing = state.pending_checks.get(pc.owner_id) or state.pending_luck_decisions.get(pc.owner_id) or {}
    observability.event(
        "combat.major_wound.blocked",
        blocked_by=blocker,
        entry_point=entry_point,
        check_id=existing.get("check_id") or existing.get("decision_id"),
        owner_id_hash=observability.safe_identifier(pc.owner_id),
    )
    if blocker == "pending_check":
        reason, next_step = "已有待處理檢定", "請先完成現有檢定，再重新套用傷害。"
    else:
        reason, next_step = "仍在等待 Luck 決定", "請先處理 Luck 選項，再重新套用傷害。"
    return {
        "ok": False,
        "blocked_by": blocker,
        "investigator": pc.name,
        "error": f"{pc.name} {reason}；為避免遺失重傷必須的 CON 檢定，本次傷害未套用。{next_step}",
    }


def planned_damage(
    state: GroupState,
    combatant: Combatant,
    raw_damage: int,
    damage_type: str,
    tags: list[str],
    bypass_armor: bool,
) -> tuple[int, str, int]:
    """Armor, armor label and final damage for a hit, without applying it."""
    card = card_for(state, combatant)
    armor, armor_label = (0, "") if bypass_armor else _armor_reduction(card, damage_type, tags)
    return armor, armor_label, max(0, int(raw_damage) - armor)


def major_wound_block_for(
    state: GroupState,
    target_name: str,
    raw_damage: int,
    *,
    damage_type: str = "physical",
    tags: list[str] | None = None,
    bypass_armor: bool = False,
) -> tuple[Character, PendingCheckBlocker] | None:
    """The investigator and reason a hit must be refused, before it is applied."""
    if state.autoroll_checks:
        return None
    combatant = find_combatant(state, target_name)
    if not combatant:
        return None
    _, _, final = planned_damage(state, combatant, raw_damage, damage_type, tags or [], bypass_armor)
    pc = major_wound_pc(state, combatant, final, max(0, combatant.hp - final))
    if pc is None:
        return None
    blocker = check_lifecycle.blocker(state, pc.owner_id)
    return (pc, blocker) if blocker else None


def _resolve_major_wound_check(
    state: GroupState, combatant: Combatant, final_damage: int, hp_after: int
) -> dict[str, Any] | None:
    """Resolve or register a PC's major-wound CON check according to policy.

    Combat damage is a Keeper-owned game event, but the investigator's CON
    roll is player-owned by default. ``/coc autoroll on`` is the explicit
    group-level exception. The HP mutation and pending registration remain in
    the same state mutation so a concurrent turn cannot lose either one.
    """
    pc = major_wound_pc(state, combatant, final_damage, hp_after)
    if pc is None:
        return None

    if not state.autoroll_checks:
        # Callers refuse a blocked hit before mutating (major_wound_block_for);
        # reaching here blocked is a bug, so say so rather than drop the check.
        blocker = check_lifecycle.blocker(state, pc.owner_id)
        if blocker:
            raise RuntimeError(f"major-wound CON registration blocked: {blocker}")
        decision = check_lifecycle.register(
            state, pc.owner_id,
            {
                "type": "skill", "skill": "CON", "skill_value": pc.con,
                "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
                "major_wound_trigger": True,
            },
            source={"action_context": f"{pc.name} 因為重傷需要做 CON 檢定"},
        )
        if decision.status != "admitted":
            raise RuntimeError(f"major-wound CON registration blocked: {decision.blocker}")
        return {"pending": True, "skill": "CON", "skill_value": pc.con}

    con_result = dice.skill_check(pc.con)
    if not con_result.success:
        for tag in ("昏迷", "倒地"):
            if tag not in pc.status_tags:
                pc.status_tags.append(tag)
    return {
        "skill": "CON",
        "skill_value": pc.con,
        "roll": con_result.roll,
        "tier": con_result.tier,
        "success": con_result.success,
    }


def _best_armor(card: EnemyCombatCard | None, damage_type: str, tags: list[str]) -> ArmorRule | None:
    if not card:
        return None
    best: ArmorRule | None = None
    tag_set = set(tags or [])
    hit = str(damage_type or "").strip().lower()  # the scope is read lowercased, so the hit's type is too
    for armor in card.armor:
        # Read through armor_scope here too: a card saved by an older version carries the Keeper's wording as is.
        if armor_scope(armor.applies_to) not in ("all", hit):
            continue
        if set(armor.bypass_tags) & tag_set:
            continue
        if armor.value > (best.value if best else 0):
            best = armor
    return best


def _armor_reduction(card: EnemyCombatCard | None, damage_type: str, tags: list[str]) -> tuple[int, str]:
    best = _best_armor(card, damage_type, tags)
    return (best.value, best.label) if best else (0, "")


def armor_note(state: GroupState, combatant: Combatant) -> str:
    """What a registration reports about an enemy's armor, never its value: the Keeper and the log see that it is
    there (and was rolled in secret), the player is not told the number."""
    card = card_for(state, combatant)
    if card is None or not card.armor:
        return ""
    return "有護甲（暗擲，數值不公開）" if any(a.rolled_from for a in card.armor) else "有護甲（數值不公開）"


def wear_armor(state: GroupState, combatant: Combatant, damage_type: str, tags: list[str], absorbed: int) -> int | None:
    """Take what a hit's armor absorbed off armor that wears away (Flesh Ward); the points it has left, else None."""
    armor = _best_armor(card_for(state, combatant), damage_type, tags)
    if armor is None or not armor.depletes or absorbed <= 0:
        return None
    armor.value = max(0, armor.value - absorbed)
    return armor.value


def apply_combat_damage(
    state: GroupState,
    target_name: str,
    raw_damage: int,
    *,
    ops: ModeOps,
    damage_type: str = "physical",
    tags: list[str] | None = None,
    source_id: str = "",
    bypass_armor: bool = False,
    entry_point: str = "apply_combat_damage",
    event_id: str = "",
) -> dict[str, Any]:
    return ops.apply_damage(
        state, target_name, raw_damage, damage_type=damage_type, tags=tags, source_id=source_id,
        bypass_armor=bypass_armor, entry_point=entry_point, event_id=event_id,
    )


def damage_combatant(state: GroupState, name: str, delta: int, *, ops: ModeOps) -> dict:
    combatant = find_combatant(state, name)
    if not combatant:
        return {"ok": False, "error": f"戰鬥中找不到「{name}」"}

    if delta < 0:
        return apply_combat_damage(state, name, -delta, ops=ops, entry_point="damage_combatant")

    before = combatant.hp
    combatant.hp = max(0, min(combatant.hp_max, combatant.hp + delta))
    combatant.defeated = combatant.hp <= 0
    card = card_for(state, combatant)
    if card:
        card.hp = combatant.hp
    ops.sync_hp(state, combatant)

    return {
        "ok": True,
        "name": combatant.display_name,
        "side": combatant.side,
        "hp": combatant.hp,
        "hp_max": combatant.hp_max,
        "hp_before": before,
        "defeated": combatant.defeated,
    }


def character_for_combatant(state: GroupState, combatant: Combatant) -> Character | None:
    if combatant.character_id and combatant.character_id in state.characters_by_id:
        return state.characters_by_id[combatant.character_id]
    # Legacy combat snapshots did not persist character_id. Only use a name
    # fallback when it is unambiguous across current and historical sheets;
    # retire_character() handles the mutation side conservatively when names
    # collide, so an ambiguous stale entry is never silently rebound here.
    matches = [character for character in state.all_characters() if character.name == combatant.name]
    return matches[0] if len(matches) == 1 else None


def current_actor(state: GroupState) -> Combatant | None:
    """The combatant whose turn it is, or None when there is no order or the index is stale."""
    combat = state.combat
    if combat.order and 0 <= combat.current_index < len(combat.order):
        return combat.order[combat.current_index]
    return None


def resolve_actor_reference(state: GroupState, reference: str) -> Combatant | None:
    """The combatant a Keeper's ``actor_id`` names: the current actor when any of its own fields match (whitespace
    and case aside, as every lookup here), else the global lookup. Same-named combatants share a name, so the one
    whose turn it is wins."""
    current = current_actor(state)
    wanted = _normalize(reference)
    if current is not None and wanted and wanted in {
            _normalize(current.combatant_id), _normalize(current.character_id),
            _normalize(current.name), _normalize(current.display_name)}:
        return current
    return find_combatant(state, reference) if wanted else None


def completed_actions_this_round(state: GroupState, combatant_id: str, *, include_skips: bool = True) -> list[CombatAction]:
    """The actions ``combatant_id`` completed in the current round, the engine's one notion of "has acted"."""
    combat = state.combat
    return [a for a in combat.actions.values()
            if a.get('actor_id') == combatant_id and a.get('completed') and a.get('round') == combat.round_number
            and (include_skips or a.get('kind') != 'skip')]


UNPLAYABLE_ENEMY_HINT = ('If the scenario gives this enemy attacks, register them with add_npc_to_combat '
                         '(same name, with attacks and source) so it can act next round.')


def enemy_turn_blocker(state: GroupState, combatant: Combatant) -> str:
    """Why the engine cannot play this enemy's turn, or an empty string when it can.

    An enemy registered with HP alone (``/coc combat addnpc``, or a card given neither attacks nor abilities) is
    ``incomplete``: the enemy flow refuses to run it, so its turn can only be given up.
    """
    if combatant.side != 'enemy':
        return ''
    card = card_for(state, combatant)
    if card is None:
        return 'this enemy has no combat card'
    if card.incomplete:
        return 'this enemy was registered without attacks or abilities'
    return ''


def is_skippable(state: GroupState, combatant: Combatant) -> bool:
    if combatant.defeated:
        return True
    if combatant.is_pc:
        character = character_for_combatant(state, combatant)
        if character is not None:
            return not character.active or character.away
    return False


def finish_retired_current_turn(
    state: GroupState,
    *,
    old_order: list[Combatant],
    old_index: int,
    removed_ids: set[str],
    ops: ModeOps,
) -> None:
    """Initialize the next usable turn after the current PC is retired.

    ``CombatState.retire_character`` owns order/effect cleanup because it is
    part of the persisted model. Turn timing, however, needs the complete
    ``GroupState`` to determine whether a PC is away or defeated and to apply
    effects. This helper bridges those responsibilities without calling
    ``advance_turn``: the retired PC never gets a turn-end phase, while the
    next eligible combatant receives its turn-start phase exactly once.
    """
    combat = state.combat
    if not combat.active or not combat.order:
        return

    # If retiring the current combatant leaves only away/defeated entries,
    # preserve the all-skippable state for the existing explicit end-combat
    # guard. Do this before looking for a wrapped candidate: otherwise a
    # candidate at the start of the old order would apply round_end and
    # round_start timing for a round in which nobody can act.
    if all(is_skippable(state, combatant) for combatant in combat.order):
        combat.current_index = 0
        return

    wrapped = False
    for offset in range(1, len(old_order) + 1):
        candidate = old_order[(old_index + offset) % len(old_order)]
        if candidate.combatant_id in removed_ids:
            continue

        current = next(
            (item for item in combat.order if item.combatant_id == candidate.combatant_id),
            None,
        )
        if current is None:
            continue
        combat.current_index = combat.order.index(current)

        candidate_wrapped = old_index + offset >= len(old_order)
        if candidate_wrapped and not wrapped:
            wrapped = True
            process_timing(state, "round_end", ops=ops)
            combat.round_number += 1
            _reset_round_usage(state)
            process_timing(state, "round_start", ops=ops)
            _mark_round_start_abilities(state)

        if is_skippable(state, current):
            continue

        process_timing(state, "turn_start", current.combatant_id, ops=ops)
        if not is_skippable(state, current):
            return

    # There is no eligible participant. Keep combat intact so the existing
    # all-skippable guard can tell the KP to end combat explicitly.


def _reset_round_usage(state: GroupState) -> None:
    for card in state.combat.enemy_cards.values():
        for ability in card.abilities:
            ability.usage["used_this_round"] = 0
            if ability.current_cooldown > 0:
                ability.current_cooldown -= 1


def tick_effect(effect: EffectState) -> None:
    if effect.remaining_rounds is not None:
        effect.remaining_rounds -= 1


def timing_key(state: GroupState, timing: str, target_id: str) -> str:
    return f"{state.combat.round_number}:{state.combat.current_index}:{timing}:{target_id or '*'}"


def _round_start_trigger_tag(ability_id: str) -> str:
    return f"_trigger:round_start:{ability_id}"


def damage_taken_trigger_tag() -> str:
    return "_trigger:on_damage_taken"


def _mark_round_start_abilities(state: GroupState) -> None:
    for card in state.combat.enemy_cards.values():
        for ability in card.abilities:
            if (ability.trigger or {}).get("type") == "round_start":
                tag = _round_start_trigger_tag(ability.id)
                if tag not in card.status_tags:
                    card.status_tags.append(tag)


def _range_rank(range_band: str) -> int:
    return {"engaged": 0, "near": 1, "far": 2, "any": 99}.get((range_band or "engaged").lower(), 0)


def _target_in_abstract_range(state: GroupState, card: EnemyCombatCard, target_id: str, trigger: dict[str, Any]) -> bool:
    required = (trigger.get("range_band") or trigger.get("range") or trigger.get("max_range") or "any").lower()
    if required in ("any", "near_or_audible", "audible"):
        return True
    current = (
        state.combat.range_bands.get(f"{card.id}:{target_id}")
        or state.combat.range_bands.get(f"{target_id}:{card.id}")
        or trigger.get("current_range")
        or "engaged"
    )
    return _range_rank(current) <= _range_rank(required)


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
    normalized_target = (target_name or "").strip().lower()
    is_environment = normalized_target in {"environment", "global", "環境", "場景"}
    is_all = normalized_target in {"all", "全體", "所有人"}
    combatant = None if is_environment or is_all else find_combatant(state, target_name)
    if not is_environment and not is_all and combatant is None:
        return {"ok": False, "error": f"戰鬥中找不到「{target_name}」"}
    if timing not in {"round_start", "turn_start", "turn_end", "round_end"}:
        return {"ok": False, "error": f"不支援的效果時點：{timing}"}
    if remaining_rounds is not None and remaining_rounds < 1:
        return {"ok": False, "error": "remaining_rounds 必須大於 0，或省略表示無限期"}
    damage_error = _validate_effect_damage_expression(damage)
    if damage_error:
        return {"ok": False, "error": f"無法解析效果傷害：{damage_error}"}

    if is_environment:
        target_id = "__environment__"
        target_label = "環境"
    elif is_all:
        target_id = "__all__"
        target_label = "全體"
    else:
        # Keep the invariant explicit for callers and static type checkers.
        if combatant is None:
            return {"ok": False, "error": f"戰鬥中找不到「{target_name}」"}
        target_id = combatant.combatant_id
        target_label = combatant.display_name
    effect = EffectState(
        id=f"effect-{uuid.uuid4().hex[:8]}",
        label=label,
        source_id=source_id,
        target_id=target_id,
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
        "target": target_label,
        "target_id": target_id,
        "label": effect.label,
        "timing": effect.timing,
        "remaining_rounds": effect.remaining_rounds,
        "damage": effect.damage,
        "damage_type": effect.damage_type,
        "tags": effect.tags,
    }


def process_timing(state: GroupState, timing: str, target_id: str = "", *, ops: ModeOps) -> list[dict[str, Any]]:
    """Apply fixed-timing effects. This first pass supports damage effects.

    More effect types can be added without changing the turn-order API.
    """
    return ops.process_timing(state, timing, target_id)


def _trigger_matches(ability: SpecialAbility, card: EnemyCombatCard, state: GroupState, target_id: str) -> bool:
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
    if t == "round_start":
        return _round_start_trigger_tag(ability.id) in card.status_tags
    if t == "on_damage_taken":
        return damage_taken_trigger_tag() in card.status_tags
    if t == "target_in_range":
        return bool(target_id and _target_in_abstract_range(state, card, target_id, trigger))
    if t == "hp_below":
        return card.hp <= int(trigger.get("value", card.hp_max))
    if t == "state_missing":
        missing = trigger.get("tag")
        return bool(missing and missing not in card.status_tags)
    return False


def _safe_public_ability_hint(ability: SpecialAbility) -> str:
    reveal = ability.reveal_policy or {}
    public_name = reveal.get("player_facing_name", "")
    if public_name:
        return f"{public_name} 開始生效。"
    return "它展現出某種異常能力，但具體規則仍未明朗。"


def _target_range(state: GroupState, enemy_id: str, target_id: str) -> str:
    return (
        state.combat.range_bands.get(f"{enemy_id}:{target_id}")
        or state.combat.range_bands.get(f"{target_id}:{enemy_id}")
        or ""
    ).lower()


def _attack_can_reach_target(state: GroupState, enemy_id: str, target_id: str, attack: AttackRule) -> bool:
    """Return whether an attack's abstract range can reach the selected target.

    An unset range is legacy state with no explicit distance information, so it
    remains usable. Explicitly marked ``far`` targets must not be hit by a
    close-range attack just because an attack exists on the combat card.
    """
    if not target_id:
        return False
    if attack.range_band.lower() == "any":
        return True
    current = _target_range(state, enemy_id, target_id)
    if not current:
        return True
    return _range_rank(current) <= _range_rank(attack.range_band)


def _planning_signature(state: GroupState, card: EnemyCombatCard) -> str:
    """Capture state that can invalidate an unresolved enemy plan."""
    combat = state.combat
    combatants = tuple(
        (item.combatant_id, item.hp, item.defeated, is_skippable(state, item))
        for item in combat.order
    )
    abilities = tuple(
        (ability.id, tuple(sorted(ability.usage.items())), ability.current_cooldown)
        for ability in card.abilities
    )
    return repr((card.hp, tuple(card.status_tags), abilities, tuple(sorted(combat.range_bands.items())), combatants))


def _choose_target(state: GroupState, enemy_id: str) -> str:
    valid = [
        c.combatant_id
        for c in state.combat.order
        if c.side == "pc" and not is_skippable(state, c)
    ]
    if not valid:
        return ""

    # Distance is a tactical signal, not a hard aggro table. Players can
    # deliberately protect a fragile investigator by engaging the enemy, while
    # equal-distance targets remain unpredictable instead of following
    # initiative order forever.
    for preferred_band in ("engaged", "near"):
        candidates = [
            target_id for target_id in valid
            if _target_range(state, enemy_id, target_id) == preferred_band
        ]
        if candidates:
            return random.choice(candidates)
    return random.choice(valid)


def plan_enemy_turn(state: GroupState, enemy_name: str = "", *, ops: ModeOps) -> dict[str, Any]:
    refusal = ops.refuse_planning(state)
    if refusal:
        return refusal
    return _all_or_nothing(state, lambda: _plan_enemy_turn(state, enemy_name, ops), ops)


def _plan_enemy_turn(state: GroupState, enemy_name: str, ops: ModeOps) -> dict[str, Any]:
    combat = state.combat
    if not combat.active or not combat.order:
        return {"ok": False, "error": "目前沒有進行中的戰鬥"}
    combatant = find_combatant(state, enemy_name) if enemy_name else combat.order[combat.current_index]
    if not combatant or combatant.side != "enemy":
        return {"ok": False, "error": "目前輪到的不是敵人，或找不到指定敵人"}
    card = card_for(state, combatant)
    if not card:
        return {"ok": False, "error": f"敵人「{combatant.display_name}」沒有戰鬥卡"}

    _process_timing_or_stop(state, "turn_start", ops, combatant.combatant_id)
    if is_skippable(state, combatant):
        return {
            "ok": True,
            "plan_id": "",
            "enemy": combatant.display_name,
            "selected_action": "none",
            "selected_id": "",
            "target_ids": [],
            "required_rolls": [],
            "private_reason": "turn_start effect defeated this enemy before it could act",
            "public_hint": f"{combatant.display_name} 已無法行動。",
        }

    existing_plan = next(
        (
            plan for plan in combat.plans.values()
            if not plan.get("resolved")
            and plan.get("enemy_card_id") == card.id
            and plan.get("round_number") == combat.round_number
            and plan.get("current_index") == combat.current_index
            and plan.get("planning_signature") == _planning_signature(state, card)
        ),
        None,
    )
    if existing_plan is not None:
        return existing_plan

    target_id = _choose_target(state, card.id)
    for ability in sorted(card.abilities, key=lambda a: -a.priority):
        if _trigger_matches(ability, card, state, target_id):
            plan_id = f"plan-{uuid.uuid4().hex[:8]}"
            plan = {
                "ok": True,
                "plan_id": plan_id,
                "enemy_card_id": card.id,
                "enemy_combatant_id": combatant.combatant_id,
                "enemy": card.name,
                "selected_action": "special_ability",
                "selected_id": ability.id,
                "target_ids": [target_id] if target_id else [],
                "round_number": combat.round_number,
                "current_index": combat.current_index,
                "planning_signature": _planning_signature(state, card),
                "required_rolls": [ability.check] if ability.check else [],
                "private_reason": f"special ability {ability.name} trigger matched; usage={ability.usage}",
                "public_hint": _safe_public_ability_hint(ability),
            }
            combat.plans[plan_id] = plan
            return plan

    attack = next(
        (
            candidate for candidate in card.attacks
            if candidate.range_band.lower() in ("engaged", "near", "any")
            and _attack_can_reach_target(state, card.id, target_id, candidate)
        ),
        None,
    )
    if attack:
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        plan = {
            "ok": True,
            "plan_id": plan_id,
            "enemy_card_id": card.id,
            "enemy_combatant_id": combatant.combatant_id,
            "enemy": card.name,
            "selected_action": "attack",
            "selected_id": attack.id,
            "target_ids": [target_id] if target_id else [],
            "round_number": combat.round_number,
            "current_index": combat.current_index,
            "planning_signature": _planning_signature(state, card),
            "required_rolls": [{
                "type": "skill",
                "skill_name": attack.skill_name,
                "skill_value": attack.skill_value,
                "damage": attack.damage,
                "range_band": attack.range_band,
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
        "enemy_combatant_id": combatant.combatant_id,
        "enemy": card.name,
        "selected_action": "move",
        "selected_id": "",
        "target_ids": [target_id] if target_id else [],
        "round_number": combat.round_number,
        "current_index": combat.current_index,
        "planning_signature": _planning_signature(state, card),
        "required_rolls": [],
        "private_reason": "no usable special ability or attack in current abstract range",
        "public_hint": f"{card.name} 調整位置，尋找下一次出手機會。",
    }
    combat.plans[plan_id] = plan
    return plan


def _apply_ability_effect(
    state: GroupState,
    ability: SpecialAbility,
    target_ids: list[str],
) -> dict[str, Any]:
    """Materialize a successful ability's declared persistent effect."""
    effect_spec = ability.effect or {}
    if effect_spec.get("on_success") != "apply_effect":
        return {"ok": True, "applied": False}

    target_id = next(
        (candidate for candidate in target_ids if any(c.combatant_id == candidate for c in state.combat.order)),
        "",
    )
    if not target_id:
        return {"ok": False, "error": "特殊能力成功，但找不到效果目標"}
    timing = effect_spec.get("timing", "turn_start")
    if timing not in {"round_start", "turn_start", "turn_end", "round_end"}:
        return {"ok": False, "error": f"特殊能力效果時點不支援：{timing}"}
    remaining_rounds = effect_spec.get("remaining_rounds")
    if remaining_rounds is not None and remaining_rounds < 1:
        return {"ok": False, "error": "特殊能力效果 remaining_rounds 必須大於 0"}
    damage = str(effect_spec.get("damage", ""))
    damage_error = _validate_effect_damage_expression(damage)
    if damage_error:
        return {"ok": False, "error": f"無法解析特殊能力效果傷害：{damage_error}"}

    effect_id = effect_spec.get("effect_id") or f"ability-effect:{ability.id}"
    if any(effect.id == effect_id for effect in state.combat.effects):
        return {"ok": True, "applied": False, "already_applied": True, "effect_id": effect_id}
    effect = EffectState(
        id=effect_id,
        label=effect_spec.get("label") or ability.name,
        source_id=ability.id,
        target_id=target_id,
        timing=timing,
        remaining_rounds=remaining_rounds,
        damage=damage,
        damage_type=effect_spec.get("damage_type", "mental"),
        save_or_check=effect_spec.get("save_or_check", {}),
        tags=effect_spec.get("tags", []),
        public_description=effect_spec.get("public_description", ""),
    )
    state.combat.effects.append(effect)
    return {
        "ok": True,
        "applied": True,
        "effect_id": effect.id,
        "target_id": target_id,
        "label": effect.label,
    }


class TimingBlocked(Exception):
    """A fixed-timing effect was refused for a blocked major wound."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result.get("error", ""))
        self.result = result


class ModeOps(Protocol):
    """The steps of a turn and of damage that belong to the working-resource pipeline.

    The rules below never ask which mode they are in; the combat engine passes
    the implementation down. The only one is ``combat_flow.MANAGED_OPS``.
    """

    def process_timing(self, state: GroupState, timing: str, target_id: str) -> list[dict[str, Any]]: ...

    def apply_damage(
        self, state: GroupState, target_name: str, raw_damage: int, *, damage_type: str,
        tags: list[str] | None, source_id: str, bypass_armor: bool, entry_point: str, event_id: str,
    ) -> dict[str, Any]: ...

    def sync_hp(self, state: GroupState, combatant: Combatant) -> None:
        """Carry a combatant's hit points over to the character they belong to."""
        ...

    def refuse_planning(self, state: GroupState) -> dict[str, Any] | None: ...

    def refuse_advance(self, state: GroupState) -> dict[str, Any] | None: ...

    def round_wrapped(self, state: GroupState) -> None:
        """Called when advancing wraps past the last combatant; may raise ``TimingBlocked``."""
        ...

    def check_timing_result(self, result: dict[str, Any]) -> None:
        """Inspect one fixed-timing result; may raise ``TimingBlocked``."""
        ...

    def after_timing(self, state: GroupState) -> None:
        """Called after a fixed timing ran; may raise ``TimingBlocked``."""
        ...

    def keep_rolls(self, restored: GroupState, snapshot: dict[str, Any], retained_rolls: dict[str, Any]) -> None:
        """Re-attach dice already drawn when a blocked advance is rolled back."""
        ...


class ModeMismatch(RuntimeError):
    """An unmanaged battle was handed to the managed rules; it must go through the combat engine."""


def _process_timing_or_stop(state: GroupState, timing: str, ops: ModeOps, target_id: str = "") -> None:
    """process_timing for automatic turn advancement: stop at a blocked hit."""
    for result in process_timing(state, timing, target_id, ops=ops):
        if result.get("blocked_by"):
            raise TimingBlocked(result)
        ops.check_timing_result(result)
    ops.after_timing(state)


def _all_or_nothing(state: GroupState, step: Callable[[], dict[str, Any]], ops: ModeOps) -> dict[str, Any]:
    """Run a turn-advancing `step`, or leave `state` exactly as it was.

    Advancing runs several timings in a row (turn_end, round_end,
    round_start, turn_start). If one of them would give a major wound whose
    CON check the player can't take yet, moving on anyway would silently
    postpone the hit to the effect's next timing. Instead nothing advances,
    and the caller is told to resolve the check and advance again.
    """
    snapshot = deepcopy(state.to_dict())
    try:
        return step()
    except TimingBlocked as blocked:
        retained_rolls = deepcopy(state.combat.roll_receipts)
        if blocked.result.get('retain_due'):
            return {'ok': blocked.result['blocked_by'] == 'pending_check',
                    'pending': blocked.result['blocked_by'] == 'pending_check',
                    'error': blocked.result['error'], 'blocked_by': blocked.result['blocked_by'],
                    'phase': state.combat.phase, 'interaction': deepcopy(state.combat.interaction)}
        restored = GroupState.from_dict(snapshot)
        ops.keep_rolls(restored, snapshot, retained_rolls)
        for field in fields(GroupState):
            setattr(state, field.name, getattr(restored, field.name))
        result = blocked.result
        name = result.get("investigator", "調查員")
        if result["blocked_by"] == "pending_check":
            reason, next_step = "已有待處理檢定", "請先完成現有檢定"
        else:
            reason, next_step = "仍在等待 Luck 決定", "請先處理 Luck 選項"
        return {
            "ok": False,
            "blocked_by": result["blocked_by"],
            "effect_id": result.get("effect_id"),
            "error": (
                f"持續效果這時會讓{name}受重傷，但{name}{reason}；為避免遺失重傷必須的 CON 檢定，"
                f"回合沒有推進。{next_step}，再推進回合。"
            ),
        }


def _move_to_next_available(state: GroupState, ops: ModeOps) -> bool:
    combat = state.combat
    n = len(combat.order)
    for _ in range(n):
        next_index = (combat.current_index + 1) % n
        wrapped = next_index == 0
        if wrapped:
            ops.round_wrapped(state)
            _process_timing_or_stop(state, "round_end", ops)
            combat.round_number += 1
            _reset_round_usage(state)
            _process_timing_or_stop(state, "round_start", ops)
            _mark_round_start_abilities(state)
        combat.current_index = next_index
        if not is_skippable(state, combat.order[combat.current_index]):
            return True
    return False


def advance_turn(state: GroupState, *, ops: ModeOps) -> dict:
    refusal = ops.refuse_advance(state)
    if refusal:
        return refusal
    return _all_or_nothing(state, lambda: _advance_turn(state, ops), ops)


def _advance_turn(state: GroupState, ops: ModeOps) -> dict:
    combat = state.combat
    if not combat.active or not combat.order:
        return {"ok": False, "error": "目前沒有進行中的戰鬥"}
    if all(is_skippable(state, c) for c in combat.order):
        return {"ok": False, "error": "所有戰鬥角色都已倒下或暫離，戰鬥應該結束了，請呼叫 end_combat 結束戰鬥"}

    current = combat.order[combat.current_index]
    _process_timing_or_stop(state, "turn_end", ops, current.combatant_id)

    if not _move_to_next_available(state, ops):
        return {"ok": False, "error": "所有戰鬥角色都已倒下或暫離，戰鬥應該結束了，請呼叫 end_combat 結束戰鬥"}

    current = combat.order[combat.current_index]
    _process_timing_or_stop(state, "turn_start", ops, current.combatant_id)
    while is_skippable(state, current):
        if not _move_to_next_available(state, ops):
            return {"ok": False, "error": "所有戰鬥角色都已倒下或暫離，戰鬥應該結束了，請呼叫 end_combat 結束戰鬥"}
        current = combat.order[combat.current_index]
        _process_timing_or_stop(state, "turn_start", ops, current.combatant_id)
    return {
        "ok": True,
        "round": combat.round_number,
        "current_turn": current.display_name,
        "combatant_id": current.combatant_id,
        "side": current.side,
        "hp": current.hp,
        "hp_max": current.hp_max,
    }


def end_combat(state: GroupState) -> dict[str, Any] | None:
    """Leave an idle battle slot empty; a battle that is still running needs explicit closure."""
    if state.combat.active:
        raise combat_resources.CombatAdmissionError(combat_resources.UNSUPPORTED_COMBAT_FORMAT)
    state.combat = CombatState()
    return None


def last_ended_combat_evidence(state: GroupState, *, include_private: bool) -> dict[str, Any]:
    """Project the final combat state without leaking enemy HP to players."""
    report = state.last_combat_report
    if (
        state.combat.active or not report.get("ended")
        or report.get("timeline_id") != state.timeline_id
        or report.get("scenario_library_id") != state.scenario_library_id
        or report.get("scenario_title") != state.scenario_title
    ):
        return {}
    include_private = include_private or not spoiler_policy.is_privacy_isolation_enabled()
    members = []
    for member in report.get("combatants", []):
        projected = {key: member[key] for key in ("name", "side", "defeated") if key in member}
        if include_private or member.get("side") != "enemy":
            projected.update({key: member[key] for key in ("hp", "hp_max") if key in member})
        members.append(projected)
    damage = dict(report.get("last_damage") or {})
    if not include_private and damage.get("side") == "enemy":
        for key in ("final_damage", "hp_before", "hp_after"):
            damage.pop(key, None)
    return {"ended": True, "combatants": members, "last_damage": damage}


def status_text(state: GroupState, include_private: bool = False, *, provisional: bool = False) -> str:
    # §3.4 mechanism #7: with privacy isolation off, enemy HP/armor/abilities
    # are treated as always visible, regardless of what the caller asked for.
    include_private = include_private or not spoiler_policy.is_privacy_isolation_enabled()
    combat = state.combat
    if not combat.active or not combat.order:
        return "目前沒有進行中的戰鬥。"

    lines = [f"戰鬥中 - 第 {combat.round_number} 輪" + ("（戰鬥暫定；尚未結算）" if provisional else "")]
    for i, c in enumerate(combat.order):
        card = card_for(state, c)
        if card:
            _sync_combatant_from_card(c, card)
        skippable = is_skippable(state, c)
        marker = "=> " if i == combat.current_index and not skippable else "   "
        character = character_for_combatant(state, c) if c.is_pc else None
        retired = character is not None and not character.active
        away = c.is_pc and not c.defeated and not retired and skippable
        tag = "（倒下）" if c.defeated else "（已退出）" if retired else "（暫離）" if away else ""
        side = "我方" if c.side == "pc" else "隊友" if c.side == "ally" else "敵方"
        hp_text = f"HP {c.hp}/{c.hp_max}" if include_private or c.side != "enemy" else "HP 未公開"
        line = f"{marker}{c.display_name} [{side}] DEX {c.dex} {hp_text}{tag}"
        if include_private and card:
            if card.armor:
                line += " 護甲:" + ",".join(f"{a.label} {a.value}" for a in card.armor)
            if card.abilities:
                line += " 能力:" + ",".join(a.name for a in card.abilities)
            if card.incomplete:
                line += "（戰鬥卡未完整）"
        lines.append(line)
    enemies = [c for c in combat.order if c.side == "enemy"]
    party = [c for c in combat.order if c.side in ("pc", "ally")]
    if enemies and all(c.defeated for c in enemies):
        lines.append("敵方已全數倒下，戰鬥可以結算了。")
    elif party and all(is_skippable(state, c) for c in party):
        lines.append("我方已全數倒下或離場，戰鬥可以結算了。")
    return "\n".join(lines)
