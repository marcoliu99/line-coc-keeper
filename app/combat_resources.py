"""Durable provisional resources; callers own atomic repository admission/save.

No persistence, RNG or transport lives here. A Character remains the committed
state until the existing group transaction publishes an absolute settlement.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, Literal, cast
from uuid import uuid4

from app.models import (
    Character,
    CombatState,
    GroupState,
    InjuryState,
    PostcombatObligation,
)

PIPELINE_VERSION = 'combat-working-v1'
CONTINUING_STATE_KEY = 'system:continuing-state'
ResourceField = Literal['hp', 'luck', 'san', 'mp']
EventKind = Literal['resource', 'ammo', 'status', 'injury', 'snapshot', 'correction',
                    'action', 'damage', 'ruling', 'initiative', 'effect', 'timing', 'administrative', 'reconciliation']
RESOURCE_FIELDS = ('hp', 'hp_max', 'luck', 'san', 'san_max', 'mp', 'mp_max',
                   'weapons', 'weapon_instances', 'status_tags', 'injury')


class CombatAdmissionError(ValueError):
    """A legacy/malformed/unclosed battle needs explicit controller admission."""


class SettlementConflict(ValueError):
    """Persistent resources or the preview changed; reconciliation is required."""


def _character_id(character: Character) -> str:
    return character.character_id or f'legacy-user:{character.owner_id}'


def _snapshot(character: Character) -> dict[str, Any]:
    return {name: deepcopy(getattr(character, name)) for name in RESOURCE_FIELDS}


def _managed(state: GroupState) -> CombatState:
    combat = state.combat
    if (not combat.active or combat.pipeline_version != PIPELINE_VERSION or not combat.combat_id
            or set(combat.working_resources) != set(combat.baseline_resources)):
        raise CombatAdmissionError('No safely admitted combat working state')
    if any(p.is_pc and (not p.character_id or p.character_id not in combat.working_resources) for p in combat.order):
        raise CombatAdmissionError('No safely admitted combat working state')
    if combat.phase not in {'READY', 'PLAYER_CHOICE', 'PLAYER_ROLL', 'LUCK_DECISION',
                            'RESOLVE', 'INJURY_CHECK', 'NEEDS_RULING', 'SETTLEMENT'}:
        raise CombatAdmissionError('Combat is closed or has an unsupported phase')
    for snapshots in (combat.baseline_resources, combat.working_resources):
        if any(not isinstance(value, dict) or set(RESOURCE_FIELDS) - value.keys() for value in snapshots.values()):
            raise CombatAdmissionError('Incomplete participant resource evidence')
    return combat


def _find_character(state: GroupState, character_id: str) -> Character:
    character = state.characters_by_id.get(character_id)
    if character is None:
        character = next((c for c in state.characters.values() if _character_id(c) == character_id), None)
    if character is None:
        raise CombatAdmissionError('Participant no longer exists')
    return character


def initialize_working_state(state: GroupState, *, combat_id: str | None = None,
                             new_combat: bool = False) -> CombatState:
    combat = state.combat
    if combat.pipeline_version == PIPELINE_VERSION:
        managed = _managed(state)
        if combat_id and combat_id != managed.combat_id:
            raise CombatAdmissionError('Another combat is already unclosed')
        return managed
    if combat.active and not new_combat:
        raise CombatAdmissionError('Legacy active combat needs explicit admission, not baseline reconstruction')
    if combat.baseline_resources or combat.combat_id:
        raise CombatAdmissionError('Unclosed combat metadata cannot be replaced')
    participants = []
    for entry in combat.order:
        if not entry.is_pc:
            continue
        if not entry.character_id:
            raise CombatAdmissionError('Combat participant requires a stable character ID')
        participants.append(_find_character(state, entry.character_id))
    combat.combat_id = combat_id or f'combat:{uuid4().hex}'
    combat.pipeline_version = PIPELINE_VERSION
    combat.phase = 'READY'
    combat.active = True
    combat.baseline_resources = {_character_id(c): _snapshot(c) for c in participants}
    combat.working_resources = deepcopy(combat.baseline_resources)
    return combat


def admit_character(state: GroupState, character: Character) -> None:
    combat = _managed(state)
    identity = _character_id(character)
    if identity in combat.working_resources:
        return
    admitted = _find_character(state, identity)
    combat.baseline_resources[identity] = _snapshot(admitted)
    combat.working_resources[identity] = _snapshot(admitted)
    combat.revision += 1
    combat.settlement = {}


def effective_character(state: GroupState, character: Character) -> Character:
    if not state.combat.active:
        return character
    combat = _managed(state)
    snapshot = combat.working_resources.get(_character_id(character))
    if snapshot is None:
        return character
    effective = deepcopy(character)
    for name in RESOURCE_FIELDS:
        setattr(effective, name, deepcopy(snapshot[name]))
    return effective


def _existing(combat: CombatState, event_id: str) -> dict[str, Any] | None:
    if not event_id:
        raise ValueError('A stable event ID is required')
    return next((deepcopy(e) for e in combat.events if e['event_id'] == event_id), None)


def record_event(state: GroupState, event_id: str, kind: EventKind, *,
                 data: dict[str, Any] | None = None, reason: str = '') -> dict[str, Any]:
    combat = _managed(state)
    existing = _existing(combat, event_id)
    if existing:
        return existing
    event = {'event_id': event_id, 'kind': kind, 'data': deepcopy(data or {}),
             'reason': reason, 'revision': combat.revision + 1, 'combat_id': combat.combat_id}
    json.dumps(event, allow_nan=False)
    combat.revision += 1
    combat.events.append(event)
    combat.settlement = {}
    return deepcopy(event)


def _working(state: GroupState, character: Character) -> dict[str, Any]:
    combat = _managed(state)
    try:
        return combat.working_resources[_character_id(character)]
    except KeyError as error:
        raise CombatAdmissionError('Character is not admitted to this combat') from error


def _cap(snapshot: dict[str, Any], field: ResourceField, value: int) -> int:
    if field not in {'hp', 'luck', 'san', 'mp'}:
        raise ValueError('Unsupported resource field')
    maximum = 99 if field == 'luck' else snapshot[f'{field}_max']
    return max(0, min(maximum, int(value)))


def set_resource(state: GroupState, character: Character, field: ResourceField, value: int, *,
                 event_id: str, reason: str = '') -> dict[str, Any]:
    combat = _managed(state)
    existing = _existing(combat, event_id)
    if existing:
        return existing['data']
    snapshot = _working(state, character)
    before = snapshot[field]
    after = _cap(snapshot, field, value)
    data = {'character_id': _character_id(character), 'field': field, 'before': before, 'after': after,
            'event_id': event_id, 'operation': 'set', 'amount': value}
    snapshot[field] = after
    for entry in combat.order:
        if field == 'hp' and entry.character_id == _character_id(character):
            entry.hp = after
    record_event(state, event_id, 'resource', data=data, reason=reason)
    return deepcopy(data)


def adjust_resource(state: GroupState, character: Character, field: ResourceField, delta: int, *,
                    event_id: str, reason: str = '') -> dict[str, Any]:
    existing = _existing(_managed(state), event_id)
    if existing:
        return existing['data']
    result = set_resource(state, character, field, _working(state, character)[field] + delta,
                          event_id=event_id, reason=reason)
    # Preserve requested deltas, not merely clamped differences, for corrections.
    state.combat.events[-1]['data'].update(operation='adjust', amount=delta)
    result.update(operation='adjust', amount=delta)
    return result


def adjust_ammo(state: GroupState, character: Character, weapon: str, delta: int = 0, *,
                event_id: str, reason: str = '', reload_full: bool = False) -> dict[str, Any]:
    existing = _existing(_managed(state), event_id)
    if existing:
        return existing['data']
    snapshot = _working(state, character)
    entry = snapshot['weapons'].get(weapon)
    if not entry or 'ammo_max' not in entry or 'ammo' not in entry:
        raise ValueError('Weapon has no tracked ammunition')
    before = entry['ammo']
    after = entry['ammo_max'] if reload_full else max(0, min(entry['ammo_max'], before + delta))
    data = {'character_id': _character_id(character), 'weapon': weapon, 'before': before,
            'after': after, 'event_id': event_id, 'operation': 'reload' if reload_full else 'adjust', 'amount': delta}
    entry['ammo'] = after
    record_event(state, event_id, 'ammo', data=data, reason=reason)
    return deepcopy(data)


def set_status_tag(state: GroupState, character: Character, tag: str, present: bool, *,
                   event_id: str, reason: str = '') -> dict[str, Any]:
    existing = _existing(_managed(state), event_id)
    if existing:
        return existing['data']
    tag = tag.strip()
    if not tag:
        raise ValueError('Status tag cannot be empty')
    snapshot = _working(state, character)
    before = list(snapshot['status_tags'])
    after = [value for value in before if value != tag]
    if present:
        after.append(tag)
    snapshot['status_tags'] = after
    data = {'character_id': _character_id(character), 'before': before, 'after': after,
            'event_id': event_id, 'tag': tag, 'present': present}
    record_event(state, event_id, 'status', data=data, reason=reason)
    return deepcopy(data)


def set_injury(state: GroupState, character: Character, injury: InjuryState, *,
               event_id: str, reason: str = '') -> dict[str, Any]:
    existing = _existing(_managed(state), event_id)
    if existing:
        return existing['data']
    _validate_injury(injury)
    snapshot = _working(state, character)
    data = {'character_id': _character_id(character), 'before': deepcopy(snapshot['injury']),
            'after': deepcopy(injury), 'event_id': event_id}
    snapshot['injury'] = deepcopy(injury)
    record_event(state, event_id, 'injury', data=data, reason=reason)
    return deepcopy(data)


def reconcile_effective_character(state: GroupState, edited_character: Character, *,
                                  event_id: str, reason: str = '') -> dict[str, Any]:
    existing = _existing(_managed(state), event_id)
    if existing:
        return existing['data']
    snapshot = _working(state, edited_character)
    proposed = _snapshot(edited_character)
    _validate_injury(proposed['injury'])
    # Caps and weapon pins are admission evidence, not arbitrary resource adjustments.
    if any(proposed[name] != snapshot[name] for name in ('hp_max', 'mp_max', 'san_max', 'weapon_instances')):
        raise ValueError('Resource adjustment cannot change admitted capacities or weapon definitions')
    if set(proposed['weapons']) != set(snapshot['weapons']):
        raise ValueError('Weapon admission must be explicit')
    for name, entry in proposed['weapons'].items():
        if {k: v for k, v in entry.items() if k != 'ammo'} != {
                k: v for k, v in snapshot['weapons'][name].items() if k != 'ammo'}:
            raise ValueError('Resource adjustment cannot change weapon capacity')
        if 'ammo' in entry:
            entry['ammo'] = max(0, min(entry['ammo_max'], int(entry['ammo'])))
    for resource in ('hp', 'luck', 'san', 'mp'):
        proposed[resource] = _cap(snapshot, resource, proposed[resource])
    data = {'character_id': _character_id(edited_character), 'before': deepcopy(snapshot),
            'after': proposed, 'event_id': event_id}
    snapshot.update(deepcopy(proposed))
    for participant in state.combat.order:
        if participant.character_id == _character_id(edited_character):
            participant.hp = snapshot['hp']
    record_event(state, event_id, 'snapshot', data=data, reason=reason)
    return deepcopy(data)


def record_roll(state: GroupState, roll_id: str,
                result: dict[str, Any] | Callable[[], dict[str, Any]]) -> dict[str, Any]:
    combat = _managed(state)
    if not roll_id:
        raise ValueError('A stable roll ID is required')
    if roll_id in combat.roll_receipts:
        return deepcopy(combat.roll_receipts[roll_id])
    value = result() if callable(result) else result
    if not isinstance(value, dict):
        raise TypeError('Roll receipt must be structured')
    json.dumps(value, allow_nan=False)  # Reject non-durable results before changing state.
    combat.roll_receipts[roll_id] = deepcopy(value)
    combat.revision += 1
    combat.settlement = {}
    return deepcopy(value)


def _pending_empty(state: GroupState) -> None:
    combat = _managed(state)
    owners = {_find_character(state, identity).owner_id for identity in combat.working_resources}
    if (owners & state.pending_checks.keys() or owners & state.pending_luck_decisions.keys()
            or combat.phase in {'PLAYER_CHOICE', 'PLAYER_ROLL', 'LUCK_DECISION', 'INJURY_CHECK', 'NEEDS_RULING', 'RESOLVE'}
            or (combat.interaction and combat.interaction.get('status') not in {'resolved', 'completed', 'cancelled'})):
        raise CombatAdmissionError('Currently due combat work must finish before settlement')
    if any(o.get('character_id') in combat.working_resources and _obligation_due(state, o)
           for o in _prior_obligations(state)):
        raise CombatAdmissionError('Currently due postcombat obligation must be resolved')
    if any(action.get('completed') is False or action.get('status') in {'declared', 'pending', 'resolving'}
           for action in combat.actions.values()):
        raise CombatAdmissionError('Combat action is still unresolved')


def _conflicts(state: GroupState) -> list[str]:
    conflicts = [identity for identity, baseline in state.combat.baseline_resources.items()
                 if _snapshot(_find_character(state, identity)) != baseline]
    continuing = _continuing_state(state)
    if continuing and continuing['obligation_baseline'] != state.postcombat_obligations:
        conflicts.append('postcombat_obligations')
    return conflicts


def get_settlement(state: GroupState, *, obligations: list[PostcombatObligation] | None = None) -> dict[str, Any]:
    _pending_empty(state)
    combat = state.combat
    future = _compose_obligations(state, obligations if obligations is not None else combat.settlement.get('obligations', []))
    _validate_obligations(state, future)
    continuing = _continuing_snapshot(state)
    payload = {'final': combat.working_resources, 'obligations': future, 'revision': combat.revision,
               'continuing_obligations': continuing}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    combat.phase = 'SETTLEMENT'
    combat.settlement = {'settlement_id': f'{combat.combat_id}:settlement:{combat.revision}:{digest[:12]}',
                         'combat_id': combat.combat_id, 'revision': combat.revision,
                         'status': 'preview', 'baseline': deepcopy(combat.baseline_resources),
                         'final': deepcopy(combat.working_resources), 'conflicts': _conflicts(state),
                         'obligations': future, 'continuing_obligations': continuing,
                         'obligation_baseline': deepcopy(state.postcombat_obligations)}
    return deepcopy(combat.settlement)


def _validate_obligations(state: GroupState, obligations: list[PostcombatObligation]) -> None:
    combat = state.combat
    identifiers = set()
    prior = {o.get('obligation_id'): o for o in _prior_obligations(state)}
    for obligation in obligations:
        identity = obligation.get('obligation_id')
        if not identity or identity in identifiers:
            raise CombatAdmissionError('Continuing obligation needs a unique stable identity')
        identifiers.add(identity)
        if identity in prior:
            if obligation != prior[identity]:
                raise SettlementConflict('Reviewed prior obligation projection changed')
            continue  # Existing source battle/status/receipts are retained, not recreated.
        if (obligation.get('combat_id') != combat.combat_id
                or obligation.get('character_id') not in combat.working_resources
                or obligation.get('kind') not in {'dying', 'effect'}
                or obligation.get('status') != 'future' or not isinstance(obligation.get('next_trigger'), dict)
                or not obligation.get('next_trigger') or not obligation.get('rule_source')
                or not obligation.get('stop_condition') or _obligation_due(state, obligation)):
            raise CombatAdmissionError('Future obligation requires identity, future timing, rule source and stop condition')
    continuing = [o for o in obligations if o.get('status') == 'future']
    for identity, snapshot in combat.working_resources.items():
        if (snapshot['injury'].get('dying') and not snapshot['injury'].get('dead')
                and not any(o.get('character_id') == identity and o.get('kind') == 'dying' for o in continuing)):
            raise CombatAdmissionError('Dying participant requires durable future check tracking')
    for effect in combat.effects:
        if ((effect.remaining_rounds is None or effect.remaining_rounds > 0)
                and not any(o.get('kind') == 'effect' and o.get('effect', {}).get('id') == effect.id for o in continuing)):
            raise CombatAdmissionError('Ongoing combat effect requires durable future tracking')


def commit_settlement(state: GroupState, settlement_id: str, *,
                      obligations: list[PostcombatObligation] | None = None) -> dict[str, Any]:
    for receipt in state.closed_combat_receipts.values():
        if receipt.get('settlement_id') == settlement_id:
            return deepcopy(receipt)
    _pending_empty(state)
    combat = state.combat
    preview = combat.settlement
    if (not preview or preview.get('settlement_id') != settlement_id or preview.get('revision') != combat.revision
            or preview.get('final') != combat.working_resources or combat.phase != 'SETTLEMENT'
            or preview.get('continuing_obligations') != _continuing_snapshot(state)):
        raise SettlementConflict('Settlement preview is stale')
    conflicts = _conflicts(state)
    if preview.get('obligation_baseline') != state.postcombat_obligations:
        conflicts.append('postcombat_obligations')
    if conflicts:
        raise SettlementConflict('Persistent participant resources changed: ' + ', '.join(conflicts))
    future = deepcopy(preview.get('obligations', []))
    if obligations is not None and obligations != future:
        raise SettlementConflict('Future obligations changed after the settlement preview')
    _validate_obligations(state, future)
    # Validation completes before any committed state is mutated. The caller's
    # existing group transaction saves Character mirrors, receipt and transfer together.
    for identity, snapshot in combat.working_resources.items():
        character = _find_character(state, identity)
        for name in RESOURCE_FIELDS:
            setattr(character, name, deepcopy(snapshot[name]))
        state.characters_by_id[identity] = character
        for owner, mirror in state.characters.items():
            if _character_id(mirror) == identity:
                state.characters[owner] = character
    state.postcombat_obligations = deepcopy(future)
    receipt = {**deepcopy(preview), 'status': 'committed', 'events': deepcopy(combat.events),
               'roll_receipts': deepcopy(combat.roll_receipts), 'obligations': future,
               'actions': deepcopy(combat.actions), 'processed_timings': list(combat.processed_timings),
               'final_order': [p.to_dict() for p in combat.order],
               'effects': [effect.to_dict() for effect in combat.effects],
               'pipeline_version': combat.pipeline_version}
    state.closed_combat_receipts[combat.combat_id] = receipt
    combat.settlement = deepcopy(receipt)
    combat.phase = 'CLOSED'
    combat.active = False
    combat.interaction = {}
    return deepcopy(receipt)


def rollback_combat(state: GroupState, *, event_id: str, reason: str) -> dict[str, Any]:
    for receipt in state.closed_combat_receipts.values():
        if receipt.get('rollback_event_id') == event_id:
            return deepcopy(receipt)
    combat = _managed(state)
    if not reason.strip():
        raise ValueError('Rollback requires an explicit controller reason')
    restored_obligations = _rollback_obligations(state)
    continuing = _continuing_snapshot(state)
    record_event(state, event_id, 'administrative', data={'decision': 'rollback'}, reason=reason)
    receipt = {'combat_id': combat.combat_id, 'rollback_event_id': event_id, 'status': 'rolled_back',
               'reason': reason, 'events': deepcopy(combat.events), 'roll_receipts': deepcopy(combat.roll_receipts),
               'final': deepcopy(combat.working_resources), 'baseline': deepcopy(combat.baseline_resources),
               'interaction': deepcopy(combat.interaction), 'actions': deepcopy(combat.actions),
               'processed_timings': list(combat.processed_timings),
               'final_order': [p.to_dict() for p in combat.order],
               'pipeline_version': combat.pipeline_version, 'continuing_obligations': continuing}
    state.postcombat_obligations = restored_obligations
    owners = {_find_character(state, identity).owner_id for identity in combat.working_resources}
    for owner in owners:
        # Invalidate only this battle's controls. Prior committed obligations
        # and unrelated owner checks retain their original timing and receipts.
        for key in ('pending_checks', 'pending_luck_decisions'):
            controls = getattr(state, key)
            pending = controls.get(owner)
            if not isinstance(pending, dict) or 'postcombat_context' in pending:
                continue  # A committed prior obligation is not part of this rollback.
            context = pending.get('combat_context')
            if isinstance(context, dict) and context.get('combat_id') == combat.combat_id:
                receipt.setdefault(key, {})[owner] = deepcopy(controls.pop(owner))
    state.closed_combat_receipts[combat.combat_id] = receipt
    combat.settlement = deepcopy(receipt)
    combat.phase = 'ROLLED_BACK'
    combat.active = False
    combat.interaction = {}
    return deepcopy(receipt)


def _replay_resource_event(resources: dict[str, dict[str, Any]], event: dict[str, Any]) -> None:
    data = event['data']
    snapshot = resources.get(data.get('character_id', ''))
    if snapshot is None:
        return
    kind = event['kind']
    if kind == 'resource':
        value = snapshot[data['field']] + data['amount'] if data['operation'] == 'adjust' else data['amount']
        snapshot[data['field']] = _cap(snapshot, data['field'], value)
    elif kind == 'ammo':
        entry = snapshot['weapons'][data['weapon']]
        if data['operation'] == 'reload':
            value = entry['ammo_max']
        elif data['operation'] == 'set':
            value = data['after']
        else:
            value = entry['ammo'] + data['amount']
        entry['ammo'] = max(0, min(entry['ammo_max'], value))
    elif kind == 'status':
        if 'tag' not in data:
            snapshot['status_tags'] = deepcopy(data['after'])
        else:
            snapshot['status_tags'] = [tag for tag in snapshot['status_tags'] if tag != data['tag']]
            if data['present']:
                snapshot['status_tags'].append(data['tag'])
    elif kind == 'injury':
        snapshot['injury'] = deepcopy(data['after'])
    elif kind == 'reconciliation':
        snapshot.clear()
        snapshot.update(deepcopy(data['working_after']))
    elif kind == 'snapshot':
        for field in ('hp', 'luck', 'san', 'mp'):
            snapshot[field] = _cap(snapshot, field, snapshot[field] + data['after'][field] - data['before'][field])
        for weapon, entry in data['after']['weapons'].items():
            if 'ammo' in entry:
                current = snapshot['weapons'][weapon]
                current['ammo'] = max(0, min(current['ammo_max'],
                    current['ammo'] + entry['ammo'] - data['before']['weapons'][weapon]['ammo']))
        for field in ('status_tags', 'injury'):
            if data['after'][field] != data['before'][field]:
                snapshot[field] = deepcopy(data['after'][field])


def correct_event(state: GroupState, target_event_id: str, *, event_id: str,
                  changes: dict[str, Any], reason: str) -> dict[str, Any]:
    combat = _managed(state)
    duplicate = _existing(combat, event_id)
    if duplicate:
        return duplicate
    if not reason.strip():
        raise ValueError('Correction requires an explicit controller reason')
    target = _existing(combat, target_event_id)
    if target is None or target['kind'] in {'correction', 'administrative', 'reconciliation'}:
        raise ValueError('Correction target is not a correctable recorded event')
    corrected = deepcopy(target['data'])
    corrected.update(deepcopy(changes))
    if target['kind'] in {'resource', 'ammo'}:
        allowed = {'amount', 'after'}
        if set(changes) - allowed or any(type(v) is not int for v in changes.values()):
            raise ValueError('Correction must specify exact numeric resource evidence')
        if 'after' in changes:
            corrected['operation'] = 'set'
            if target['kind'] == 'resource':
                corrected['amount'] = changes['after']
    elif target['kind'] == 'status':
        if set(changes) != {'after'} or not isinstance(changes['after'], list):
            raise ValueError('Status correction requires an absolute tag list')
        corrected.pop('tag', None)
        corrected.pop('present', None)
    elif target['kind'] == 'injury':
        if set(changes) != {'after'} or not isinstance(changes['after'], dict):
            raise ValueError('Injury correction requires a structured replacement')
        _validate_injury(changes['after'])
    elif target['kind'] == 'snapshot':
        raise ValueError('Correct individual evidenced mutations, not arbitrary resource snapshots')
    elif set(changes) & {'character_id', 'event_id'}:
        raise ValueError('Correction cannot replace recorded identity')
    # Construct the projection before appending, so invalid evidence cannot
    # partially change a live state even outside the caller's transaction.
    overlays = {e['data']['target_event_id']: e['data']['replacement'] for e in combat.events if e['kind'] == 'correction'}
    overlays[target_event_id] = corrected
    projected = deepcopy(combat.baseline_resources)
    restored = set()
    for event in combat.events:
        if event['kind'] == 'reconciliation':
            identity = event['data']['character_id']
            if identity not in restored:
                projected[identity] = deepcopy(event['data']['baseline_before'])
                restored.add(identity)
    for event in combat.events:
        replacement = {**event, 'data': overlays.get(event['event_id'], event['data'])}
        _replay_resource_event(projected, replacement)
    appended = record_event(state, event_id, 'correction',
                            data={'target_event_id': target_event_id, 'replacement': corrected}, reason=reason)
    combat.working_resources = projected
    for participant in combat.order:
        if participant.character_id in projected:
            participant.hp = projected[participant.character_id]['hp']
    position = next(i for i, e in enumerate(combat.events) if e['event_id'] == target_event_id)
    hp_correction = target['kind'] == 'resource' and target['data'].get('field') == 'hp'
    if hp_correction or target['kind'] not in {'resource', 'ammo', 'status', 'injury'} or any(
            e['kind'] in {'action', 'damage', 'effect', 'reconciliation', 'injury', 'status'}
            for e in combat.events[position + 1:-1]):
        # HP projection cannot decide single-hit injury thresholds. Both reduced
        # fatal damage and increased minor damage need the owning injury rules;
        # stale injury/status events must not certify the corrected source.
        previous_interaction = deepcopy(combat.interaction)
        combat.phase = 'NEEDS_RULING'
        combat.interaction = {'kind': 'correction_reconciliation', 'status': 'pending',
                              'target_event_id': target_event_id, 'event_id': event_id,
                              'character_id': target['data'].get('character_id'),
                              'requires_injury_reconciliation': hp_correction,
                              'previous_interaction': previous_interaction}
    return appended


def _validate_injury(injury: Mapping[str, Any]) -> None:
    if (set(injury) - {'major_wound', 'unconscious', 'dying', 'dead'}
            or any(type(value) is not bool for value in injury.values())):
        raise ValueError('Invalid structured injury')


def reconcile_baseline(state: GroupState, character: Character, *, event_id: str,
                       decision: Literal['keep_working', 'adopt_persistent'], reason: str) -> dict[str, Any]:
    """Apply an explicit reviewed whole-snapshot conflict decision, never a guessed merge."""
    combat = _managed(state)
    duplicate = _existing(combat, event_id)
    if duplicate:
        return duplicate
    if not reason.strip() or decision not in {'keep_working', 'adopt_persistent'}:
        raise ValueError('Baseline reconciliation needs an explicit decision and reason')
    identity = _character_id(character)
    working = _working(state, character)
    persistent = _snapshot(_find_character(state, identity))
    proposed = deepcopy(working if decision == 'keep_working' else persistent)
    event = record_event(state, event_id, 'reconciliation', data={
        'character_id': identity, 'decision': decision,
        'baseline_before': deepcopy(combat.baseline_resources[identity]), 'baseline_after': persistent,
        'working_before': deepcopy(working), 'working_after': proposed}, reason=reason)
    combat.baseline_resources[identity] = persistent
    combat.working_resources[identity] = proposed
    for participant in combat.order:
        if participant.character_id == identity:
            participant.hp = proposed['hp']
    return event


def _continuing_state(state: GroupState) -> dict[str, Any] | None:
    metadata = state.combat.actions.get(CONTINUING_STATE_KEY)
    if metadata is None:
        return None
    if (not metadata.get('completed') or not isinstance(metadata.get('obligation_baseline'), list)
            or not isinstance(metadata.get('working_obligations'), list)):
        raise CombatAdmissionError('Malformed continuing-state projection')
    for records in (metadata['obligation_baseline'], metadata['working_obligations']):
        identifiers = [o.get('obligation_id') for o in records if isinstance(o, dict)]
        if len(identifiers) != len(records) or None in identifiers or len(set(identifiers)) != len(identifiers):
            raise CombatAdmissionError('Malformed continuing-state obligation identity')
    return metadata


def _prior_obligations(state: GroupState) -> list[PostcombatObligation]:
    continuing = _continuing_state(state)
    return continuing['working_obligations'] if continuing else state.postcombat_obligations


def _continuing_snapshot(state: GroupState) -> dict[str, Any] | None:
    continuing = _continuing_state(state)
    return ({'baseline': deepcopy(continuing['obligation_baseline']),
             'working': deepcopy(continuing['working_obligations'])} if continuing else None)


def _obligation_due(state: GroupState, obligation: PostcombatObligation) -> bool:
    if obligation.get('status') in {'due', 'pending'}:
        return True
    trigger = obligation.get('next_trigger', {}).get('round')
    return (obligation.get('status') == 'future' and isinstance(trigger, int)
            and trigger <= state.mechanical_round)


def _compose_obligations(state: GroupState, proposed: list[PostcombatObligation]) -> list[PostcombatObligation]:
    final = deepcopy(_prior_obligations(state))
    known = {o.get('obligation_id'): o for o in final}
    supplied = set()
    for obligation in proposed:
        identity = obligation.get('obligation_id')
        if identity in supplied:
            raise CombatAdmissionError('Continuing obligation identity is duplicated')
        supplied.add(identity)
        if identity in known:
            if known[identity] != obligation:
                raise SettlementConflict('Existing obligation must not be silently reset')
        else:
            final.append(deepcopy(obligation))
    return final


def _rollback_obligations(state: GroupState) -> list[PostcombatObligation]:
    continuing = _continuing_state(state)
    if not continuing:
        return deepcopy(state.postcombat_obligations)
    if continuing['obligation_baseline'] != state.postcombat_obligations:
        raise SettlementConflict('Persistent continuing obligations changed during battle')
    working = cast(list[PostcombatObligation], continuing['working_obligations'])
    if any(_obligation_due(state, obligation) for obligation in working):
        raise CombatAdmissionError('Resolve currently due prior committed consequences before rollback')
    identities = {o.get('obligation_id') for o in working}
    for controls in (state.pending_checks, state.pending_luck_decisions):
        if any(isinstance(pending.get('postcombat_context'), dict)
               and pending['postcombat_context'].get('obligation_id') in identities for pending in controls.values()):
            raise CombatAdmissionError('Prior committed obligation control must finish before rollback')
    restored = deepcopy(state.postcombat_obligations)
    projected = {o['obligation_id']: o for o in working}
    # Roll results survive; resource/injury consequences and processed timings
    # do not. T4 replays the original due trigger at retained logical time.
    for baseline in restored:
        current = projected.get(baseline.get('obligation_id', ''))
        if current is None:
            raise CombatAdmissionError('Prior obligation cannot disappear from rollback evidence')
        cached = baseline.setdefault('roll_receipts', {})
        for identity, roll in current.get('roll_receipts', {}).items():
            if identity in cached and cached[identity] != roll:
                raise SettlementConflict('Original continuing roll receipt cannot be replaced')
            cached[identity] = deepcopy(roll)
    return restored
