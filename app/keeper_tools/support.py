"""What the Keeper tool handlers share: character lookup, the state-mutation adapter, the check-result cache and the NPC index.

Split out of app/keeper.py unchanged so that app/keeper_tools no longer imports the module that dispatches to it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Generic, TypeVar, cast, overload

from app import (
    check_lifecycle,
    combat,
    combat_resources,
    dice,
    observability,
    spoiler_policy,
)
from app.checks.skills import resolve_skill_value
from app.keeper_tools import resource_bridge
from app.models import Character, GroupState
from app.repositories import group_state, state_transaction
from app.services import combat_actions as combat_act
from app.services import (
    combat_engine,
    mutation_admission,
)

_logger = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass
class ToolStateMutation(Generic[_T]):
    value: _T
    should_save: bool = True


def find_character(state: GroupState, name: str) -> Character | None:
    if not name:
        return None
    characters = state.all_characters()
    exact = next((char for char in characters if char.name == name), None)
    if exact:
        return exact
    norm = name.strip().lower()
    for c in characters:
        if norm and (norm in c.name.lower() or c.name.lower() in norm):
            return c
    return None


def resolve_active_character_exactly(state: GroupState, ref: str) -> tuple[Character | None, str]:
    """An active investigator named by character id or by an exact (case-insensitive) name; never a partial match.

    Returns ``(character, "")`` or ``(None, reason)`` where reason is ``unknown`` or ``ambiguous:<names>``. A hand-off
    must not reach a similarly named investigator, which ``find_character``'s substring fallback would allow.
    """
    wanted = (ref or "").strip()
    if not wanted:
        return None, "unknown"
    pool = state.active_characters()
    by_id = [char for char in pool if char.character_id and char.character_id == wanted]
    matches = by_id or [char for char in pool if char.name.strip().casefold() == wanted.casefold()]
    if not matches:
        return None, "unknown"
    if len(matches) > 1:
        return None, "ambiguous:" + "、".join(sorted(char.name for char in matches))
    return matches[0], ""


def require_character(state: GroupState, name: str) -> Character:
    character = find_character(state, name)
    if character is None:
        raise ValueError(f"找不到角色「{name}」")
    return character


def mutate_tool_state_once(
    state: GroupState, mutator: Callable[[GroupState], dict[str, Any]], *,
    action_id: str | None, request_fingerprint: str,
) -> tuple[dict[str, Any], bool]:
    """``mutate_tool_state`` for a repeatable mutation that returns its receipt as a JSON-safe dict.

    With an ``action_id`` the receipt is stored in the transaction ledger, so the same call re-sent after a lost reply
    returns the stored receipt (second element True) and changes nothing. Without one it behaves like a plain mutation.
    """
    def run(ctx: state_transaction.TxContext) -> dict[str, Any]:
        receipt = mutator(ctx.state)
        ctx.set_result(receipt)
        return receipt

    outcome = state_transaction.commit_for_snapshot(
        state, run, reason="tool", action_id=action_id, request_fingerprint=request_fingerprint if action_id else None)
    if outcome.outcome is state_transaction.Outcome.STALE_TIMELINE:
        raise mutation_admission.MutationHeld("stale tool timeline")
    if outcome.outcome is state_transaction.Outcome.CONFLICT:
        raise group_state.StateRevisionConflict(
            f"state transaction conflict for {observability.safe_identifier(state.group_id)}: {outcome.reason}")
    if not outcome.ok:
        raise state_transaction.StateTransactionFailed(outcome)
    if outcome.outcome is state_transaction.Outcome.DUPLICATE:
        return dict(outcome.result), True
    return outcome.value, False  # type: ignore[return-value]


def deterministic_check_cache_key(
    tool_name: str,
    tool_input: dict[str, Any],
    owner_id: str,
    speaker_role: str,
) -> str:
    """Return a same-turn idempotency key for a Keeper-owned check.

    The turn id is the important boundary: two attacks in two different player
    turns must roll independently even when their text and skill are identical,
    while a provider retry inside one turn must not roll twice. Direct unit
    calls without an observability turn intentionally skip this cache because
    they do not have a trustworthy request boundary.
    """
    context = observability.current_context()
    turn_id = str(context.get("turn_id", "")).strip()
    if not turn_id:
        return ""
    payload = {
        "turn_id": turn_id,
        "tool": tool_name,
        "input": tool_input,
        "owner_id": owner_id,
        "speaker_role": speaker_role,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def cached_check_result(state: GroupState, cache_key: str) -> dict[str, Any] | None:
    if not cache_key:
        return None
    cached = state.deterministic_check_results.get(cache_key)
    if not isinstance(cached, dict):
        return None
    result = cached.get("result")
    return dict(result) if isinstance(result, dict) else None


def remember_check_result(state: GroupState, cache_key: str, result: dict[str, Any]) -> None:
    if not cache_key:
        return
    state.deterministic_check_results[cache_key] = {
        "result": dict(result),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Keep this cache small. It is only for retries of recent turns, not an
    # audit log; the authoritative public event remains the committed turn.
    if len(state.deterministic_check_results) > 128:
        oldest = next(iter(state.deterministic_check_results))
        state.deterministic_check_results.pop(oldest, None)


def resolve_defense_options(
    char: Character, raw_options: list[dict], *, register_unknown: bool = True
) -> list[dict]:
    """Expand offer_check_choice's raw
    {label, skill, bonus_dice, penalty_dice} option list into one with
    each option's actual resolved skill_value baked in.

    Full skill value, no artificial difficulty adjustment — confirmed
    against the official COC7e Fight Back text: it's a normal opposed
    roll at the defender's own combat skill, not a harder version of
    Dodge. (A prior revision here halved it as a house-rule
    approximation; reverted.)

    Passes "kind" (the caller's optional "dodge"/"counter" tag, see
    dice.is_counter_option) straight through unmodified — this function
    resolves skill_value, it doesn't validate or normalize kind."""
    options = []
    for opt in raw_options:
        value = resolve_skill_value(char, opt["skill"], register_unknown=register_unknown)
        resolved = {
            "label": opt["label"], "skill": opt["skill"], "skill_value": value,
            "bonus_dice": int(opt.get("bonus_dice") or 0), "penalty_dice": int(opt.get("penalty_dice") or 0),
        }
        if "kind" in opt:
            resolved["kind"] = opt["kind"]
        options.append(resolved)
    return options


_NPC_INDEX_FUZZY_THRESHOLD = 0.6  # same calibration as app/scene_map.py's room-name fuzzy match
# Looser, for attaching provenance only (a wrong match never changes a number): 老鼠 against 鼠群 scores exactly 0.5.
ENEMY_SOURCE_FUZZY_THRESHOLD = 0.5


def enemy_source(state: GroupState, given: dict | None, index_entry: dict | None, *, name: str = "",
                 attacks: Sequence[Mapping[str, Any]] | None = None) -> dict | None:
    """The provenance an enemy's attacks need. What the model gave stays; for an enemy the scenario's own NPC index
    lists, or whose stat block the scenario text carries, the rest comes from the loaded scenario, since a model has
    no real revision or hash to quote. An enemy the scenario gives no stats keeps only what the model supplied, so it
    still needs a ruling."""
    if not state.active_scenario_source_hash:
        return given
    if index_entry is None and not scenario_stat_block(state, name, attacks or ()):
        return given
    return {
        "url": f"scenario:{state.scenario_library_id}", "revision": state.active_chapter_id or "scenario",
        "sha256": state.active_scenario_source_hash, "extreme_rule": "maximum",
        **{key: value for key, value in (given or {}).items() if value not in ("", None)},
    }


_CHARACTERISTIC = re.compile(r"(?:^|[\s|])(str|con|siz|dex|pow|int|edu|app|力量|體質|體型|敏捷|意志|智力|教育|外貌)\s*[:：]?\s*\d",
                             re.IGNORECASE)
_HEADING_LINES_ABOVE, _HEADING_MAX_CHARS, _BLOCK_LINES = 6, 80, 4


def _characteristics_at(lines: list[str], index: int) -> set[str]:
    """The distinct characteristics (STR, CON, SIZ …, each with a number) a block starting at ``index`` lists in
    its first lines; an HP line alone, or any single token, is not a stat block."""
    return {match.group(1).lower() for line in lines[index:index + _BLOCK_LINES] for match in _CHARACTERISTIC.finditer(line)}


_BLOCK_MAX_LINES = 80
_DAMAGE_BONUS_WRAPPED = re.compile(r"(?:damagebonus|傷害加值|db)[（(]([+-]?\d*d?\d+)[）)]", re.IGNORECASE)
_DAMAGE_BONUS_TRAILING = re.compile(r"([+-]?\d*d?\d+)(?:damagebonus|傷害加值|db)\b", re.IGNORECASE)
_DAMAGE_BONUS_LINE = re.compile(r"(?:damagebonus|傷害加值)[:：]([+-]?\d*d?\d+)", re.IGNORECASE)
_DAMAGE_BONUS_BARE = re.compile(r"(?:damagebonus|傷害加值|\bdb\b)(?![（(]|[:：])", re.IGNORECASE)


def _stat_blocks(text: str) -> list[tuple[str, str]]:
    """Every stat block in the scenario text as (heading, block text), both casefolded.

    A block starts at the nearest short, non-stat line above its characteristics (at least two of STR/CON/SIZ/DEX/
    POW/INT/EDU/APP with numbers, on that line or the next few, as 7e lays a block out: 「### Walter Corbitt, Undead
    Fiend」, then STR …) and runs to the next block or a bounded number of lines, so its attack lines are in it. A
    line giving HP alone is the kind of card the engine already treats as incomplete, not a block.
    """
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not _CHARACTERISTIC.search(line) or len(_characteristics_at(lines, index)) < 2:
            continue
        if starts and index < starts[-1][0] + _HEADING_LINES_ABOVE + _BLOCK_LINES:
            continue  # the same block's next characteristics line
        for above in range(index - 1, max(-1, index - 1 - _HEADING_LINES_ABOVE), -1):
            candidate = lines[above].strip().strip("#*_ ").strip()
            if not candidate or _CHARACTERISTIC.search(candidate):
                continue
            if len(candidate) <= _HEADING_MAX_CHARS:
                starts.append((above, candidate.casefold()))
            break
    blocks: list[tuple[str, str]] = []
    for position, (start, heading) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        blocks.append((heading, "\n".join(lines[start:min(end, start + _BLOCK_MAX_LINES)]).casefold()))
    return blocks


_ATTACK_LINE = re.compile(r"fighting|格鬥|攻擊|attack|damage|傷害|bite|claw|咬|爪|weapon|武器", re.IGNORECASE)
_ATTACK_WINDOW_LINES = 3


def _attacks_in_block(attacks: Sequence[Mapping[str, Any]], block: str) -> bool:
    """Whether every submitted attack is written in the block as an attack: a numeric skill value given as a
    percentage, or on a line that speaks of fighting, attacks or damage (never a characteristic such as CON 55),
    with the submitted damage expression on that line or the next two. The provenance vouches for values copied
    from the scenario, so an attack the block does not state this way, or one that leaves the skill or the damage
    out (the card would fill in a default), is the model's own and keeps none."""
    # "1D3 + damage bonus(1D4)" is two dice, the weapon's and the creature's damage bonus: the Keeper may submit
    # the weapon's die alone or both, so the bonus wording is folded into its value (given in place, or on the
    # block's own "Damage bonus: +1D4" line) and either spelling is in the block.
    compact = _DAMAGE_BONUS_WRAPPED.sub(r"\1", _DAMAGE_BONUS_TRAILING.sub(r"\1", block.replace(" ", "")))
    if (bonus := _DAMAGE_BONUS_LINE.search(compact)) is not None:
        compact = _DAMAGE_BONUS_BARE.sub(bonus.group(1).lstrip("+"), compact)
    lines = compact.replace("+-", "-").replace("++", "+").splitlines()
    for attack in attacks:
        if not isinstance(attack, Mapping):
            return False
        value = attack.get("skill_value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        damage = str(attack.get("damage") or "").casefold().replace(" ", "")
        if not damage:
            return False
        skill = re.compile(rf"(?<!\d){int(value)}(?!\d)")
        wanted = re.compile(r"(?<!\d)" + re.escape(damage) + r"(?!\d)")
        for index, line in enumerate(lines):
            states_skill = (re.search(rf"(?<!\d){int(value)}\s*[%％]", line) is not None
                            or (_ATTACK_LINE.search(line) is not None and skill.search(line) is not None))
            if states_skill and wanted.search("\n".join(lines[index:index + _ATTACK_WINDOW_LINES])):
                break
        else:
            return False
    return True


def scenario_stat_block(state: GroupState, name: str, attacks: Sequence[Mapping[str, Any]] = ()) -> bool:
    """Whether the loaded scenario text carries a stat block titled with ``name`` that states the ``attacks`` given:
    the Keeper's spelling, trimmed and case-insensitive, without the instance suffix the prompt asks for when several
    of one kind are active (「魚人（左）」, "Cultist 2"), found as a whole word in a stat block's own heading, with
    every submitted attack's skill value and damage dice written in that block. A name the prose only mentions, one
    that merely stands near someone else's block, a short name inside another word ("rat" in "pirate"), or attack
    values the block does not carry prove nothing about what the model supplies, so the enemy keeps what the model
    gave and needs a ruling."""
    wanted = re.sub(r"[（(].*?[）)]\s*$|\s*#?\d+$", "", (name or "").strip()).strip().casefold()
    if not wanted or len(wanted) < 2 or not attacks:
        # Nothing submitted means the card's default unarmed attack would ride on the provenance: no attack, none.
        return False
    # Word-bounded for letters and digits; CJK characters have no word boundary, so a Chinese name is contained.
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])")
    return any(pattern.search(heading) and _attacks_in_block(attacks, block)
               for heading, block in _stat_blocks(state.scenario_text or ""))


def find_npc_index_entry(
    state: GroupState, name: str, *, threshold: float = _NPC_INDEX_FUZZY_THRESHOLD,
) -> dict | None:
    """Looks up `name` (whatever the Keeper called this NPC/monster when
    calling add_npc_to_combat) against state.scenario_npc_index — exact match
    against the entry's name or any alias first, then a difflib fuzzy
    fallback (same threshold as scene_map.py's room-name matching) to still
    catch a name that's missing punctuation or a suffix the Keeper dropped
    (e.g. "深潛者頭目" for an entry named "深潛者（成年頭目）"). Returns None
    if scenario_npc_index is empty (nobody's run /coc index) or nothing
    matches closely enough — callers should trust whatever the Keeper passed
    in that case, same as before this existed."""
    if not name:
        return None
    exact = combat.find_npc_index_entry_exact(state, name)
    if exact is not None:
        return exact

    import difflib

    best_entry = None
    best_ratio = 0.0
    for entry in state.scenario_npc_index:
        candidates = [entry.get("name", "")] + list(entry.get("aliases") or [])
        for candidate in candidates:
            if not candidate:
                continue
            ratio = difflib.SequenceMatcher(None, name, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_entry = entry
    return best_entry if best_ratio >= threshold else None


def refresh_tool_state(state: GroupState) -> GroupState:
    return state_transaction.refresh_snapshot(state)


@overload
def mutate_tool_state(state: GroupState, mutator: Callable[[GroupState], ToolStateMutation[_T]]) -> _T: ...


@overload
def mutate_tool_state(state: GroupState, mutator: Callable[[GroupState], _T]) -> _T: ...


def mutate_tool_state(state: GroupState, mutator: Callable[[GroupState], Any]) -> Any:
    """Keeper-tool adapter over the shared state transaction.

    ``mutator`` runs against the latest committed state inside
    ``state_transaction.mutate`` (conversation lock, ``BEGIN IMMEDIATE``,
    timeline check); the caller's snapshot is then refreshed so later tools in
    the same Keeper turn see the result.

    Two call shapes: ``mutator`` can return ``ToolStateMutation(value,
    should_save)`` when a tool needs to skip a genuinely no-op save (see
    add_carried_item/remove_carried_item/add_status_tag/remove_status_tag —
    ``should_save=False`` when the item/tag was already (not) present), or
    return its actual value directly when every call always needs a save. The
    ``@overload`` pair tells mypy that a ``ToolStateMutation[_T]`` result is
    unwrapped to ``_T``.
    """
    def run(ctx: state_transaction.TxContext) -> Any:
        result = mutator(ctx.state)
        if isinstance(result, ToolStateMutation):
            if not result.should_save:
                ctx.skip_save()
            return result.value
        return result

    return state_transaction.run_snapshot(state, run, reason="tool")


def apply_character_delta_in_state(
    target_state: GroupState, target_char: Character, field_name: str, delta: int,
    cur_attr: str, max_attr: str | None, *, entry_point: str, event_id: str = "", reason: str = "",
) -> tuple[int, bool, dict[str, Any] | None, dict[str, Any] | None]:
    """Shared attribute and major-wound rule within a caller-owned transaction."""
    if target_state.combat.active:
        if not resource_bridge.participating(target_state, target_char):
            raise ValueError('Active combat requires admitted character resource evidence')
        identity = event_id or resource_bridge.mutation_id(entry_point, {
            'investigator': target_char.character_id, 'field': field_name, 'delta': delta,
        })
        if field_name == 'hp' and delta < 0:
            damage_result = combat_engine.handle(target_state, combat_act.SingleHit(
                character=target_char, damage=-delta, event_id=identity, reason=reason or entry_point,
            ))
            if not damage_result.get('ok'):
                return resource_bridge.effective(target_state, target_char).hp, False, None, damage_result
            effective = resource_bridge.effective(target_state, target_char)
            return effective.hp, bool(damage_result.get('major_wound_triggered')), damage_result.get('major_wound_check'), None
        receipt = combat_resources.adjust_resource(
            target_state, target_char, cast(combat_resources.ResourceField, field_name), delta,
            event_id=identity, reason=reason or entry_point,
        )
        return receipt['after'], False, None, None
    target_cap = getattr(target_char, max_attr) if max_attr else 999
    new_val = max(0, min(target_cap, getattr(target_char, cur_attr) + delta))
    is_major_wound = (
        field_name == "hp" and delta < 0 and new_val > 0
        and -delta >= target_char.hp_max / 2
    )
    blocker = (
        check_lifecycle.blocker(target_state, target_char.owner_id)
        if is_major_wound and not target_state.autoroll_checks else None
    )
    if blocker:
        blocked = combat.major_wound_blocked(
            target_state, target_char, blocker, entry_point=entry_point
        )
        return getattr(target_char, cur_attr), False, None, blocked

    setattr(target_char, cur_attr, new_val)
    major_wound = False
    wound_roll: dict[str, Any] | None = None
    if is_major_wound:
        con_value = resolve_skill_value(target_char, "CON")
        major_wound = True
        if target_state.autoroll_checks:
            con_result = dice.skill_check(con_value)
            wound_roll = {
                "skill": "CON", "skill_value": con_value, "roll": con_result.roll,
                "tier": con_result.tier, "success": con_result.success,
            }
            if not con_result.success:
                for tag in ("昏迷", "倒地"):
                    if tag not in target_char.status_tags:
                        target_char.status_tags.append(tag)
        else:
            decision = check_lifecycle.register(
                target_state, target_char.owner_id,
                {
                    "type": "skill", "skill": "CON", "skill_value": con_value,
                    "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
                    "major_wound_trigger": True,
                },
                source={"action_context": f"{target_char.name} 因為重傷需要做 CON 檢定"},
            )
            if decision.status != "admitted":
                raise RuntimeError(f"major-wound CON registration blocked: {decision.blocker}")
    return new_val, major_wound, wound_roll, None


def apply_character_attribute_delta(
    state: GroupState,
    tool_input: dict[str, Any],
    field_name: str,
    cur_attr: str,
    max_attr: str | None,
) -> tuple[int, bool, dict[str, Any] | None, dict[str, Any] | None]:
    """Apply an attribute change and its required CON check in one transaction."""
    def _apply_attribute_delta(
        target_state: GroupState,
    ) -> ToolStateMutation[tuple[int, bool, dict[str, Any] | None, dict[str, Any] | None]]:
        target_char = require_character(target_state, tool_input.get("investigator", ""))
        result = apply_character_delta_in_state(
            target_state, target_char, field_name, int(tool_input["delta"]),
            cur_attr, max_attr, entry_point="adjust_character",
            event_id=str(tool_input.get("event_id") or ""), reason=str(tool_input.get("reason") or ""),
        )
        return ToolStateMutation(result, should_save=result[3] is None)

    return mutate_tool_state(state, _apply_attribute_delta)


def skip_save_if_blocked(result: dict) -> ToolStateMutation[dict]:
    """A hit refused for a blocked major wound changed nothing, so skip the save."""
    return ToolStateMutation(result, should_save="blocked_by" not in result)




def scenario_allowed_chapter_ids(state: GroupState) -> set[str] | None:
    """§3.4 mechanism #4: chapter gating is spoiler protection, not privacy —
    disabled means any chapter's images are searchable. Shared by
    search_scenario_images/show_scenario_image below."""
    if not spoiler_policy.is_spoiler_protection_enabled():
        return None
    return set(state.context_chapter_ids)


# Temporary public seam for check handlers while shared check helpers still
# live in keeper.py. No handler reaches across the module's private boundary.
check_tool_services = SimpleNamespace(
    StateMutation=ToolStateMutation,
    cached_check_result=cached_check_result,
    deterministic_check_cache_key=deterministic_check_cache_key,
    mutate_and_save_state=mutate_tool_state,
    remember_check_result=remember_check_result,
    resolve_defense_options=resolve_defense_options,
)
