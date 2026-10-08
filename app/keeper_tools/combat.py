"""Keeper combat tool handlers. Combat rules remain in app.combat."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from app import combat, combat_resources
from app.keeper_tools import managed_combat, resource_bridge, support
from app.models import ArmorRule, AttackRule, GroupState, SpecialAbility
from app.services import combat_actions as act
from app.services import combat_engine

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def _reviewed_skills(payload):
    if payload is None:
        return None
    if (not isinstance(payload, dict) or any(not isinstance(key, str) or not key.strip()
                                            or type(value) is not int or value < 0
                                            for key, value in payload.items())):
        raise ValueError('NPC skills require explicit nonnegative integer source values')
    return dict(payload)


def start_combat(call: ToolCall) -> dict[str, Any]:

    state = call.state

    def mutate(target_state: GroupState) -> None:
        combat_engine.handle(target_state, act.Start())

    support.mutate_tool_state(state, mutate)
    return {"ok": True, "status": combat_engine.handle(state, act.Status())}


def add_npc_to_combat(call: ToolCall) -> dict[str, Any]:

    state = call.state
    tool_input = call.input
    npc_name = tool_input["name"]
    requested_hp = int(tool_input["hp"])

    def mutate(target_state: GroupState) -> Any:
        hp = requested_hp
        index_note = ""
        # The indexed HP is authoritative for a matching scenario NPC.
        index_entry = support.find_npc_index_entry(target_state, npc_name)
        if index_entry is not None and isinstance(index_entry.get("hp"), (int, float)):
            canonical_hp = int(index_entry["hp"])
            if canonical_hp != hp:
                index_note = (
                    f"（系統已依 /coc index 索引修正：你傳入的 HP {hp} 跟索引裡「{index_entry.get('name')}」"
                    f"登記的 HP {canonical_hp} 不一致，已強制改用索引值。這隻的數值以索引為準，"
                    "之後同一隻不要再用別的數字。）"
                )
                hp = canonical_hp
        added = combat_engine.handle(target_state, act.AddCombatant(
            name=npc_name,
            dex=int(tool_input["dex"]),
            hp=hp,
            is_ally=bool(tool_input.get("is_ally", False)),
            armor=tool_input.get("armor"),
            attacks=tool_input.get("attacks"),
            abilities=tool_input.get("abilities"),
            source=support.enemy_source(target_state, tool_input.get("source"), support.find_npc_index_entry(
                target_state, npc_name, threshold=support.ENEMY_SOURCE_FUZZY_THRESHOLD)),
            skills=_reviewed_skills(tool_input.get("skills")),
        ))
        if added.reused:
            return support.ToolStateMutation(
                f"（系統偵測到「{added.combatant.name}」已經在戰鬥中且尚未倒下，沒有重複建立第二份——"
                "這隻怪物的血量與狀態沿用原本那份，之後不要為同一隻怪物再呼叫一次 "
                "add_npc_to_combat。）",
                should_save=False,
            )
        if added.defeated_namesake is not None:
            new = added.combatant
            index_note += (
                f"（{combat.defeated_namesake_notice(added)}"
                f"如果這其實是同一隻，請明確更正「{new.display_name}」的建立紀錄，"
                "並依原本倒下的狀態敘事。）"
            )
        return support.ToolStateMutation(index_note, should_save=True)

    index_note = support.mutate_tool_state(state, mutate)
    response = {"ok": True, "status": combat_engine.handle(state, act.Status())}
    if index_note:
        response["note"] = index_note
    return response


def _open_with_enemy_turn(state: GroupState) -> dict[str, Any] | None:
    """A fight that opens on an enemy's turn plays that turn, the way advancing to an enemy does.

    Only before anyone has acted. Anything the enemy's action then waits on (a defence choice, a ruling) is
    reported like any enemy turn.
    """
    battle = state.combat
    if not battle.active or not battle.order or battle.interaction:
        return None
    if any(not key.startswith("system:") for key in battle.actions):
        return None
    current = battle.order[min(battle.current_index, len(battle.order) - 1)]
    if current.side != "enemy" or current.defeated:
        return None
    plan = combat_engine.handle(state, act.PlanEnemy(current.display_name))
    if not plan.get("ok"):
        return plan
    return combat_engine.handle(state, act.RunEnemyPlan(plan["plan_id"]))


def initialize_combat(call: ToolCall) -> dict[str, Any]:

    state = call.state
    enemies = call.input.get("enemies") or []
    opening: dict[str, Any] = {}

    def mutate(target_state: GroupState) -> Any:
        results: list[dict[str, Any]] = []
        started_here = not target_state.combat.active
        seen_batch_ids: set[str] = set()
        added_any = False
        for entry in enemies:
            # A bad entry is reported, not fatal: the array's other entries
            # still get added, per the spec's decided partial-success design.
            try:
                requested_name = entry["name"].strip()
                if not requested_name:
                    raise ValueError("enemy name is empty")
                hp = int(entry["hp"])
                dex = int(entry["dex"])
                for field, rule_type in (
                    ("armor", ArmorRule), ("attacks", AttackRule), ("abilities", SpecialAbility),
                ):
                    rules = entry.get(field)
                    if rules is not None:
                        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
                            raise ValueError(f"{field} must be an array of objects")
                        for rule in rules:
                            rule_type.from_dict(rule)
                index_note = ""
                index_entry = support.find_npc_index_entry(target_state, requested_name)
                if index_entry is not None and isinstance(index_entry.get("hp"), (int, float)):
                    canonical_hp = int(index_entry["hp"])
                    if canonical_hp != hp:
                        index_note = f"HP {hp} 已依 /coc index 修正為 {canonical_hp}"
                        hp = canonical_hp
                matching = combat.find_live_enemy_by_any_alias(target_state, requested_name)
                added = combat_engine.handle(target_state, act.AddCombatant(
                    name=requested_name, dex=dex, hp=hp,
                    is_ally=bool(entry.get("is_ally", False)),
                    armor=entry.get("armor"), attacks=entry.get("attacks"), abilities=entry.get("abilities"),
                    source=support.enemy_source(target_state, entry.get("source"), support.find_npc_index_entry(
                        target_state, requested_name, threshold=support.ENEMY_SOURCE_FUZZY_THRESHOLD)),
                    skills=_reviewed_skills(entry.get("skills")),
                    force_new_instance=(
                        matching is not None and matching.combatant_id in seen_batch_ids
                    ),
                ))
            except (AttributeError, KeyError, ValueError, TypeError) as exc:
                name = entry.get("name") if isinstance(entry, dict) else None
                results.append({"name": name, "ok": False, "error": str(exc)})
                continue
            seen_batch_ids.add(added.combatant.combatant_id)
            added_any = added_any or not added.reused
            result: dict[str, Any] = {
                "name": added.combatant.display_name, "ok": True, "reused": added.reused,
            }
            if index_note:
                result["note"] = index_note
            results.append(result)
        if started_here and target_state.combat.active:
            # The single-add path preserves the first actor while sorting.
            # Before the first turn, the full roster's highest DEX acts first.
            target_state.combat.current_index = 0
            opening.clear()
            if added_any and (first_turn := _open_with_enemy_turn(target_state)) is not None:
                opening.update(first_turn)
        return support.ToolStateMutation(results, should_save=added_any)

    entry_results = support.mutate_tool_state(state, mutate)
    response = {
        "ok": any(entry["ok"] for entry in entry_results),
        "status": combat_engine.handle(state, act.Status()),
        "enemies": entry_results,
    }
    if opening:
        response["opening_enemy_turn"] = managed_combat.public_result(
            opening, include_private=call.speaker_role == "kp_assistant")
    return response


def get_combat_status(call: ToolCall) -> dict[str, Any]:

    support.refresh_tool_state(call.state)
    response = {
        "ok": True, "provisional": resource_bridge.managed(call.state),
        "status": combat_engine.handle(
            call.state, act.Status(include_private=(call.speaker_role == "kp_assistant")),
        ),
    }
    if resource_bridge.managed(call.state):
        managed = call.state.combat
        response['control'] = {
            'combat_id': managed.combat_id, 'revision': managed.revision, 'phase': managed.phase,
            'current_actor_id': managed.order[managed.current_index].combatant_id if managed.order else '',
            'interaction': {key: managed.interaction[key] for key in (
                'kind', 'combat_id', 'action_id', 'interaction_id', 'owner_id', 'check_id', 'check_role',
                'character_id', 'requires_injury_reconciliation',
            ) if key in managed.interaction},
            'actions': [{key: action[key] for key in (
                'action_id', 'actor_id', 'target_id', 'completed', 'needs_ruling', 'weapon_reference',
            ) if key in action} for action in managed.actions.values()],
            'correctable_events': [{key: event[key] for key in ('event_id', 'kind', 'revision')}
                                   for event in managed.events
                                   if event['kind'] not in {'administrative', 'reconciliation', 'correction', 'snapshot'}],
            'retained_controls': [{'owner_id': owner, 'check_id': entry.get('check_id'),
                                   'decision_id': entry.get('decision_id'), 'type': entry.get('type')}
                                  for entries in (call.state.pending_checks, call.state.pending_luck_decisions)
                                  for owner, entry in entries.items()
                                  if owner in {call.state.characters_by_id[i].owner_id
                                               for i in managed.working_resources}],
            'plans': [{key: plan.get(key) for key in (
                'plan_id', 'enemy_combatant_id', 'round_number', 'selected_action', 'selected_id', 'target_ids',
            )} for plan in managed.plans.values()],
            'settlement_id': managed.settlement.get('settlement_id', ''),
            'participants': [{'combatant_id': p.combatant_id, 'character_id': p.character_id,
                              'name': p.display_name} for p in managed.order],
        }
        response['working_changes'] = {
            identity: {field: {'baseline': baseline[field], 'effective': managed.working_resources[identity][field]}
                       for field in ('hp', 'luck', 'san', 'mp', 'weapons', 'status_tags', 'injury')
                       if baseline[field] != managed.working_resources[identity][field]}
            for identity, baseline in managed.baseline_resources.items()
        }
    response['postcombat_controls'] = [{key: obligation.get(key) for key in (
        'obligation_id', 'combat_id', 'character_id', 'kind', 'status', 'next_trigger',
    )} for obligation in call.state.postcombat_obligations if obligation.get('status') != 'resolved']
    obligations = combat_engine.handle(call.state, act.Obligations())
    medical_events = list(call.state.resolved_check_events)
    medical_events.extend(action['medical_receipt'] for action in call.state.combat.actions.values()
                          if action.get('medical_receipt'))
    medical_events.extend(action['medical_receipt']
                          for closed in call.state.closed_combat_receipts.values()
                          if closed.get('status') == 'committed'
                          for action in closed.get('actions', {}).values() if action.get('medical_receipt'))
    eligible = {}
    for event in medical_events:
        context = event.get('medical_context', {})
        patient = call.state.characters_by_id.get(context.get('character_id', ''))
        active_ids = sorted(o['obligation_id'] for o in obligations
                            if o.get('character_id') == context.get('character_id')
                            and o.get('kind') == 'dying' and o.get('status') != 'resolved')
        if (event.get('timeline_id') == call.state.timeline_id and event.get('success') is True
                and event.get('skill') in {'急救', 'First Aid'} and not event.get('stabilization_receipt')
                and patient and resource_bridge.effective(call.state, patient).injury.get('dying')
                and active_ids and active_ids == context.get('obligation_ids')):
            eligible[event['check_id']] = {key: event.get(key) for key in (
                'check_id', 'character_id', 'investigator', 'skill', 'success', 'medical_context')}
    response['medical_check_receipts'] = list(eligible.values())
    ended = combat.last_ended_combat_evidence(
        call.state, include_private=(call.speaker_role == "kp_assistant")
    )
    if ended:
        response["last_ended_combat"] = managed_combat.public_result(
            ended, include_private=call.speaker_role == "kp_assistant")
    return response


def advance_combat_turn(call: ToolCall) -> dict[str, Any]:

    def mutate(target_state: GroupState) -> Any:
        if resource_bridge.managed(target_state):
            managed = target_state.combat
            current = managed.order[managed.current_index] if managed.order else None
            tool_input = dict(call.input)
            if not str(tool_input.get('event_id') or '').strip():
                # One advance per actor per round, so the id can be derived from the actor the Keeper names: a retry
                # of the same advance replays it, and the Keeper no longer has to invent an id it was refused for
                # omitting. An unknown reference falls back to the current actor so the refusal can name them.
                if current is None:
                    return support.ToolStateMutation({'ok': False, 'error': '目前沒有進行中的戰鬥'}, should_save=False)
                named = combat.find_combatant(target_state, tool_input.get('actor_id', '')) or current
                tool_input['event_id'] = f'{managed.combat_id}:advance:round{managed.round_number}:{named.combatant_id}'
            if tool_input.get('skip'):
                skipper = combat.find_combatant(target_state, tool_input.get('actor_id', ''))
                owner = combat.character_for_combatant(target_state, skipper) if skipper and skipper.is_pc else None
                blocker = combat.enemy_turn_blocker(target_state, skipper) if skipper is not None else ''
                if skipper is not None and skipper.side == 'enemy' and skipper is current and not blocker:
                    return support.ToolStateMutation({'ok': False, 'error': (
                        'Only the acting investigator can skip their own turn. This is an enemy turn: '
                        'run plan_enemy_turn then run_enemy_combat_plan, and advance without skip afterwards')},
                        should_save=False)
                # The engine plays neither an NPC ally's turn nor an enemy it cannot run, and nobody owns those turns:
                # the Keeper gives them up so the fight moves on. An investigator's turn is still the player's own.
                unowned_npc = skipper is not None and skipper is current and (skipper.side != 'enemy' or blocker) \
                    and not skipper.is_pc
                if not unowned_npc and (owner is None or not call.actor_id or owner.owner_id != call.actor_id):
                    return support.ToolStateMutation(
                        {'ok': False, 'error': 'Only the acting investigator can skip their own turn'}, should_save=False)
            before = deepcopy(target_state.to_dict())
            result = combat_engine.handle(target_state, act.Advance(
                actor_id=tool_input.get('actor_id', ''),
                event_id=resource_bridge.mutation_id(call.name, tool_input),
                skip=bool(tool_input.get('skip')),
            ))
            return support.ToolStateMutation(result, should_save=target_state.to_dict() != before)
        return support.skip_save_if_blocked(combat_engine.handle(target_state, act.Advance()))

    return managed_combat.public_result(support.mutate_tool_state(call.state, mutate),
                                        include_private=call.speaker_role == 'kp_assistant')


def damage_combatant(call: ToolCall) -> dict[str, Any]:

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        if target_state.combat.active:
            return support.ToolStateMutation({'ok': False, 'error': 'Managed/source-bound combat requires its action or explicit effect runner; legacy raw outcome is not authoritative'}, should_save=False)
        return support.skip_save_if_blocked(combat_engine.handle(
            target_state, act.DamageCombatant(tool_input["name"], int(tool_input["delta"])),
        ))

    result = support.mutate_tool_state(call.state, mutate)
    return support.filter_public_combat_damage_result(result, call.speaker_role)


def plan_enemy_turn(call: ToolCall) -> dict[str, Any]:

    def mutate(target_state: GroupState) -> Any:
        return support.skip_save_if_blocked(
            combat_engine.handle(target_state, act.PlanEnemy(call.input.get("enemy", "")))
        )

    return support.mutate_tool_state(call.state, mutate)


def resolve_enemy_action(call: ToolCall) -> dict[str, Any]:

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        if target_state.combat.active:
            return support.ToolStateMutation({'ok': False, 'error': 'Managed/source-bound combat requires its action or explicit effect runner; legacy raw outcome is not authoritative'}, should_save=False)
        return support.skip_save_if_blocked(combat_engine.handle(
            target_state,
            act.ResolveEnemy(plan_id=tool_input["plan_id"], outcome=tool_input.get("outcome")),
        ))

    return support.mutate_tool_state(call.state, mutate)


def apply_combat_damage(call: ToolCall) -> dict[str, Any]:

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        if target_state.combat.active:
            return support.ToolStateMutation({'ok': False, 'error': 'Managed/source-bound combat requires its action or explicit effect runner; legacy raw outcome is not authoritative'}, should_save=False)
        return support.skip_save_if_blocked(combat_engine.handle(target_state, act.ApplyDamage(
            target=tool_input["target"],
            raw_damage=int(tool_input["raw_damage"]),
            damage_type=tool_input.get("damage_type", "physical"),
            tags=tool_input.get("tags") or [],
            source_id=tool_input.get("source_id", ""),
        )))

    result = support.mutate_tool_state(call.state, mutate)
    return support.filter_public_combat_damage_result(result, call.speaker_role)


def apply_final_combat_damage(call: ToolCall) -> dict[str, Any]:

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        if target_state.combat.active:
            return support.ToolStateMutation({'ok': False, 'error': 'Managed/source-bound combat requires its action or explicit effect runner; legacy raw outcome is not authoritative'}, should_save=False)
        return support.skip_save_if_blocked(combat_engine.handle(target_state, act.ApplyDamage(
            target=tool_input["target"],
            raw_damage=int(tool_input["final_damage"]),
            damage_type=tool_input.get("damage_type", "physical"),
            tags=tool_input.get("tags") or [],
            source_id=tool_input.get("source_id", ""),
            bypass_armor=True,
            entry_point="apply_final_combat_damage",
        )))

    result = support.mutate_tool_state(call.state, mutate)
    return support.filter_public_combat_damage_result(result, call.speaker_role)


def add_combat_effect(call: ToolCall) -> dict[str, Any]:

    tool_input = call.input

    def mutate(target_state: GroupState) -> Any:
        if target_state.combat.active:
            return support.ToolStateMutation({'ok': False, 'error': 'Managed/source-bound combat requires its action or explicit effect runner; legacy raw outcome is not authoritative'}, should_save=False)
        return combat_engine.handle(target_state, act.AddEffect(
            target=tool_input["target"],
            label=tool_input["label"],
            timing=tool_input.get("timing", "turn_start"),
            damage=tool_input.get("damage", ""),
            damage_type=tool_input.get("damage_type", "physical"),
            remaining_rounds=tool_input.get("remaining_rounds"),
            tags=tool_input.get("tags") or [],
            source_id=tool_input.get("source_id", ""),
            public_description=tool_input.get("public_description", ""),
        ))

    return support.mutate_tool_state(call.state, mutate)


def end_combat(call: ToolCall) -> dict[str, Any]:
    if resource_bridge.managed(call.state):
        return managed_combat.preview_combat_settlement(call)
    return {"ok": False, "error": combat_resources.UNSUPPORTED_COMBAT_FORMAT}
