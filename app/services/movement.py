"""Arrival commits and causal tool admission within the existing agent loop.

Source citations establish provenance, not universal understanding of narrative
conditions. The Executor adjudicates those conditions; explicit graph gates,
identities, pending checks, current origin and supplied sources are verified here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from app import intent_parser, observability, scenario_retrieval, scene_map
from app.models import GroupState

# Four characters: 「直奔商店」 is the shortest real case seen.
_MIN_PREFIX_SPAN = 4


def _authorized_span(span: str, request_text: str) -> bool:
    """Is this quoted span an in-character movement the player actually wrote?

    A whole clause is authorized, as it always was. So is the *opening* of one,
    provided the opening reads as a movement by itself. 「直奔商店購買油燈跟煤油罐」
    has no comma, so it is one clause, and quoting only 「直奔商店」 — the
    reasonable thing for the Executor to do — was refused every time. Requiring
    the whole clause meant quoting the purchase as part of the movement.

    Deliberately additive: nothing that was authorized before stops being so.
    A prefix must carry its own movement verb or direction, so a bare noun or a
    truncation such as 「直」 is still refused, and a fragment from the middle
    stays out — where one exists, a comma already split it into its own clause.
    """
    clauses = intent_parser.movement_clauses(request_text)
    if span in clauses:
        return True
    if len(span) < _MIN_PREFIX_SPAN or not any(c.startswith(span) for c in clauses):
        return False
    return (intent_parser.has_movement_verb(span)
            or intent_parser.parse_movement_intent(span) is not None)



def _reject(code: str, state: GroupState, args: dict, span: str = '') -> dict:
    """Refuse an arrival, and record what decided it.

    The log used to carry the code alone, which is not enough to act on: an
    `unknown_map` could not be told apart from a page the model invented,
    because neither the page it sent nor the pages that exist were written
    down anywhere. A `no_player_movement_authorization` was worse still — the
    span it rejected was never recorded at all.

    `source_span` and `destination` are player and scenario wording the log
    already carries (the router's command_name, the reducer's facts), so this
    adds no exposure that was not there.
    """
    path = args.get('path')
    observability.event(
        'movement.rejected',
        level=logging.WARNING,
        error_type=code,
        page=str(args.get('page', ''))[:40],
        destination=str(args.get('destination', ''))[:80],
        path_length=len(path) if isinstance(path, list) else None,
        source_span=str(span or args.get('source_span', ''))[:120],
        scene_map_pages=sorted(state.scene_maps)[:20],
    )
    return {'ok': False, 'error': code}


@dataclass(frozen=True)
class MovementProposal:
    proposal_id: str
    timeline_id: str
    actor_id: str
    subject_id: str
    character_id: str
    origin: tuple[str, str, str]
    source_version: str
    original_span: str
    assertion_kind: str = 'action'
    candidate_page: str = ''
    candidate_room: str = ''
    evidence_refs: tuple[str, ...] = ()
    actor_is_keeper: bool = False
    origin_facing: str = "N"


def position(state: GroupState, owner: str) -> tuple[str, str, str]:
    return (state.current_map_page.get(owner, ''), state.current_room_id.get(owner, ''),
            state.narrative_locations.get(owner, ''))


def source_version(state: GroupState) -> str:
    material = [state.scenario_text, state.scenario_library_id, state.scenario_variant_id,
                state.active_chapter_id, state.scene_maps]
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def propose(state: GroupState, actor: str, subject: str, text: str) -> MovementProposal | None:
    clauses = intent_parser.movement_clauses(text)
    movement_text = next((c for c in clauses
                          if (intent_parser.has_movement_verb(c)
                              or re.search(r'\b(?:go|enter|walk|move|leave)\b', c, re.IGNORECASE))
                          and not re.match(r'^(?:我(?:們)?)?(?:走去|去)買', c)), '')
    if not movement_text:
        return None
    char = state.get_active_character(subject)
    if char is None:
        return None
    page, room, _ = position(state, subject)
    candidate_page, candidate_room = '', ''
    direction = intent_parser.parse_movement_intent(movement_text)
    if direction and page:
        result = scene_map.resolve_move(state.scene_maps, page, room, state.party_facing.get(subject, 'N'),
                                        direction['relative_direction'], direction['order'])
        if result.get('ok'):
            candidate_page, candidate_room = result.get('map_key', page), result['room']['id']
    elif page and (target := scene_map.find_room_by_text(state.scene_maps.get(page, {}), movement_text)):
        candidate_page, candidate_room = page, target['id']
    return MovementProposal(uuid4().hex, state.timeline_id, actor, subject, char.character_id,
                            position(state, subject), source_version(state), movement_text,
                            candidate_page=candidate_page, candidate_room=candidate_room, origin_facing=state.party_facing.get(subject, "N"))


TOOL = {
    'name': 'commit_movement',
    'description': ('裁定並提交玩家要求的移動。地圖只是拓樸，先查所有通行條件／反應點。'
                    '到達後才能取得室內物品或套用場景效果；不要把名字命中當作通行許可。'
                    '需要玩家檢定時先建立 skill_check，再以 prerequisite_check_id 綁定移動，等待最終 Luck。'
                    '多段路徑逐一列出，不得跳過遭遇／選擇；沒有地圖也可用劇本地點名稱。'),
    'input_schema': {'type': 'object', 'properties': {
        'source_span': {'type': 'string', 'description': '若本回合未預先辨識移動，引用玩家原句中的移動子句，由既有 Executor 判讀；不可引用 OOC、假設、觀察或否定句'},
        'destination': {'type': 'string'}, 'page': {'type': 'string'},
        'path': {'type': 'array', 'items': {'type': 'string'}, 'description': '從目前位置之後的逐一房間 ID，跨頁用 page:room'},
        'evidence': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'source': {'type': 'string'}, 'quote': {'type': 'string'}}, 'required': ['source', 'quote']}},
        'conditions': {'type': 'string', 'enum': ['clear', 'await_check', 'blocked']},
        'prerequisite_check_id': {'type': 'string'},
    }, 'required': ['destination', 'page', 'path', 'evidence', 'conditions']},
}
PROMPT = '''
Movement uses commit_movement in this existing tool loop, never a final JSON mutation.
The movement candidate is NOT arrival. Cite source IDs with exact quotations covering
passage, locks, triggers and reaction points; continue search if incomplete. Keep
resolved dice final. A check at the origin may establish entry prerequisites; bind
its check_id with conditions=await_check. For an origin prerequisite check, set
action_context to the exact original_span in the movement proposal; an indoor
check must wait for arrival. A later unrelated success is not permission.
After committed arrival, continue any requested dependent tools before your final
resolution. Independent consumption of carried supplies before movement can remain.
'''

# Every non-query tool in a movement request defaults to requiring arrival.
# Only these operations have a documented origin-side use. Output tools count
# as effects: revealing an indoor clue before arrival is also a causality error.
ORIGIN_TOOLS = frozenset({'search_scenario', 'search_memory', 'get_character_sheet',
    'get_combat_status', 'search_scenario_images', 'skill_check', 'offer_check_choice',
    'clear_pending_check', 'commit_movement'})
CURRENT: ContextVar[MovementSession | None] = ContextVar('movement_session', default=None)
OPERATION: ContextVar[tuple[str, dict] | None] = ContextVar('movement_operation', default=None)


@dataclass
class MovementSession:
    proposal: MovementProposal | None
    request_text: str
    sources: dict[str, str] = field(default_factory=dict)
    arrived: bool = False
    committed_position: tuple[str, str, str] | None = None
    event_id: str = ''
    rejected: bool = False
    _final_check_id: str = ""
    _final_skill: str = ""
    actor_id: str = ""
    subject_id: str = ""
    actor_is_keeper: bool = False

    def guard(self, state: GroupState, name: str, args: dict) -> str:
        p = self.proposal
        if p is None:
            return ''
        char = state.get_active_character(p.subject_id)
        if (state.timeline_id != p.timeline_id or char is None or char.character_id != p.character_id
                or source_version(state) != p.source_version):
            return 'movement_context_changed'
        if not self.arrived and state.party_facing.get(p.subject_id, 'N') != p.origin_facing:
            return 'movement_facing_changed'
        expected = self.committed_position if self.arrived else p.origin
        if position(state, p.subject_id) != expected:
            return 'movement_origin_changed'
        if self.arrived:
            if name in {'skill_check', 'sanity_check', 'offer_check_choice'} and (
                    args.get('action_context') == p.original_span
                    or (self._final_skill and args.get('skill') == self._final_skill)):
                return 'resolved_entry_check_must_not_be_repeated'
            return ''
        if name in {'skill_check', 'offer_check_choice'} and args.get('action_context') != p.original_span:
            return 'arrival_required_before_destination_check'
        if name in ORIGIN_TOOLS:
            return ''
        # Server checks carried inventory AND text order, never a model boolean.
        if name == 'remove_carried_item' and args.get('investigator') == char.name:
            item = args.get('item', '')
            prefix = self.request_text.split(p.original_span, 1)[0]
            if item and item in char.carried_items and item in prefix:
                return ''
        return 'arrival_required_before_effect'

    def accept_source(self, name: str, result: dict, ref: str) -> None:
        if (name == 'skill_check' and self.proposal and result.get('ok') and result.get('resolved')
                and result.get('success') and result.get('action_context') == self.proposal.original_span
                and result.get('timeline_id') == self.proposal.timeline_id):
            self._final_check_id = str(result.get('check_id', ''))
            self._final_skill = str(result.get('skill', ''))
        if name == 'search_scenario'  and result.get('ok') and result.get('complete_for_action') is not False:
            self.sources[ref] = str(result.get('results', ''))

    def commit(self, state: GroupState, args: dict) -> dict:
        from app import keeper
        from app.services import mutation_admission
        p = self.proposal
        if p is None:
            span = args.get('source_span', '')
            # Natural language interpretation is proposed by the already-running
            # Executor; preserve an exact IC clause and reject known non-actions.
            char = state.get_active_character(self.subject_id)
            if (not isinstance(span, str) or not span or span not in self.request_text
                    or not _authorized_span(span, self.request_text)
                    or char is None):
                return _reject('no_player_movement_authorization', state, args,
                               span if isinstance(span, str) else '')
            p = MovementProposal(uuid4().hex, state.timeline_id, self.actor_id, self.subject_id,
                char.character_id, position(state, self.subject_id), source_version(state), span,
                actor_is_keeper=self.actor_is_keeper, origin_facing=state.party_facing.get(self.subject_id, "N"))
            self.proposal = p

        def mutate(latest: GroupState):
            mutation_admission.assert_admitted(latest.group_id, timeline_id=p.timeline_id)
            from app.services.narrative_corrections import blocking_reply
            if blocking_reply(latest, [p.original_span, args]):
                return keeper._StateMutation(_reject('narrative_correction_hold', latest, args, p.original_span), should_save=False)
            error = self.guard(latest, 'commit_movement', args)
            if error:
                return keeper._StateMutation(_reject(error, latest, args, p.original_span), should_save=False)
            if p.actor_id != p.subject_id and p.actor_id != latest.kp_assistant_user_id and not p.actor_is_keeper:
                return keeper._StateMutation(_reject('movement_actor_not_authorized', latest, args, p.original_span), should_save=False)
            existing = next((e for e in latest.arrival_events if e.get('proposal_id') == p.proposal_id
                             and e.get('timeline_id') == p.timeline_id), None)
            if existing:
                return keeper._StateMutation({'ok': True, 'arrival': existing, 'duplicate': True}, should_save=False)
            result = self._validate(latest, args)
            if result.get('error'):
                return keeper._StateMutation(result, should_save=False)
            if result.get('waiting'):
                check = latest.pending_checks.get(p.subject_id) or latest.pending_luck_decisions[p.subject_id]
                latest.movement_continuations[p.subject_id] = {
                    'proposal': asdict(p), 'arguments': args,
                    'sources': {e['source']: self.sources[e['source']] for e in args['evidence']},
                    'request_text': self.request_text,
                    'check_id': args['prerequisite_check_id'],
                    'skill': check.get('skill', check.get('skill_name', '')),
                    'decision_id': check.get('decision_id', ''),
                }
                return result
            destination = args['destination']
            page, room = result['page'], result['room']
            if page:
                latest.current_map_page[p.subject_id] = page
                latest.current_room_id[p.subject_id] = room
            else:
                latest.current_map_page.pop(p.subject_id, None)
                latest.current_room_id.pop(p.subject_id, None)
            latest.party_facing[p.subject_id] = result.get('facing', latest.party_facing.get(p.subject_id, 'N'))
            latest.narrative_locations[p.subject_id] = destination
            latest.movement_continuations.pop(p.subject_id, None)
            event = {'event_id': uuid4().hex, 'proposal_id': p.proposal_id, 'timeline_id': p.timeline_id,
                     'actor_id': p.actor_id, 'subject_id': p.subject_id, 'character_id': p.character_id,
                     'origin': list(p.origin), 'destination': destination, 'page': page, 'room': room,
                     'evidence': args['evidence']}
            latest.arrival_events.append(event)
            del latest.arrival_events[:-40]
            return {'ok': True, 'arrival': event}

        result = keeper._mutate_and_save_state(state, mutate)
        if result.get('arrival'):
            self.arrived = True
            self.committed_position = position(state, p.subject_id)
            self.event_id = result['arrival']['event_id']
        self.rejected = not result.get('ok', False)
        return result

    def _validate(self, state: GroupState, args: dict) -> dict:
        p = self.proposal
        assert p is not None
        def fail(code: str) -> dict:
            return _reject(code, state, args, p.original_span)
        evidence = args.get('evidence')
        if (not isinstance(evidence, list) or not evidence or len(evidence) > 20
                or not all(isinstance(e, dict) and isinstance(e.get('quote'), str) and isinstance(e.get('source'), str)
                           and len(e['quote'].strip()) >= 8 and e.get('source') in self.sources
                           and e['quote'] in self.sources[e['source']] for e in evidence)):
            return fail('movement_evidence_missing')
        for e in evidence:
            if e['source'].startswith('fact:') and not any(
                e['source'] == 'fact:' + str(f.get('source_event_id', '')) and e['quote'] in str(f.get('text', ''))
                for f in state.established_facts
            ):
                return fail('movement_play_evidence_changed')
        if args.get('conditions') == 'blocked':
            return fail('passage_blocked')
        check_id = args.get('prerequisite_check_id', '')
        pending = state.pending_checks.get(p.subject_id)
        luck = state.pending_luck_decisions.get(p.subject_id)
        if args.get('conditions') == 'await_check':
            pending = pending or luck
            if not pending or not check_id or pending.get('check_id') != check_id:
                return fail('movement_check_identity_mismatch')
            # Only a check requested for this entry clause can authorize it.
            if pending.get('action_context') != p.original_span or (not luck and pending.get('type') != 'skill'):
                return fail('movement_check_not_bound_to_action')
            return {'ok': True, 'waiting': True, 'check_id': check_id}
        if luck:
            return fail('movement_waits_for_final_luck')
        if args.get('conditions') != 'clear' or pending:
            return fail('movement_prerequisites_unresolved')
        if check_id and self._final_check_id != check_id:
            return fail('movement_requires_bound_final_result')
        page, destination, path = args.get('page'), args.get('destination'), args.get('path')
        if not isinstance(page, str) or not isinstance(destination, str) or not destination.strip() or not isinstance(path, list):
            return fail('invalid_movement_target')
        if len(destination) > 200 or len(path) > 30 or not all(isinstance(x, str) for x in path):
            return fail('invalid_movement_path')
        current_page, current_room, _ = p.origin
        facing = state.party_facing.get(p.subject_id, 'N')
        if p.candidate_room and not page:
            return fail('movement_destination_mismatch')
        visited: list[tuple[str, str]] = []
        if page:
            if p.candidate_room and intent_parser.parse_movement_intent(p.original_span):
                if not path:
                    return fail('movement_direction_mismatch')
                first_page, first_room = path[0].split(':', 1) if ':' in path[0] else (current_page, path[0])
                if (first_page, first_room) != (p.candidate_page, p.candidate_room):
                    return fail('movement_direction_mismatch')
            if page not in state.scene_maps:
                return fail('unknown_map')
            if not current_page:
                current_page, current_room = page, state.scene_maps[page].get('entry_room_id', '')
                if not path or path[0] not in {current_room, f'{page}:{current_room}'}:
                    return fail('map_entry_required')
                visited.append((current_page, current_room))
                path = path[1:]
            for step in path:
                next_page, next_room = step.split(':', 1) if ':' in step else (current_page, step)
                room_data = scene_map.get_room(state.scene_maps.get(current_page, {}), current_room)
                edge = next((e for e in (room_data or {}).get('exits', [])
                             if e.get('to') == (next_room if current_page == next_page else f'{next_page}:{next_room}')), None)
                if edge is None:
                    return fail('disconnected_movement_path')
                if edge.get('blocked') or (edge.get('locked') and (not check_id or not re.search(r'鎖匠|locksmith', self._final_skill, re.IGNORECASE))):
                    return fail('passage_blocked')
                if edge.get('requires_check') and edge.get('requires_check') != self._final_skill:
                    return fail('movement_required_check_missing')
                if edge.get('reaction') or edge.get('requires_choice'):
                    return fail('movement_reaction_point_unresolved')
                facing = edge.get('compass', facing) if edge.get('compass') not in {'U', 'D'} else facing
                current_page, current_room = next_page, next_room
                visited.append((current_page, current_room))
            if p.candidate_room:
                candidate = (p.candidate_page, p.candidate_room)
                if candidate not in visited:
                    return fail('movement_destination_mismatch')
                expected = candidate
                # Preserve explicitly requested onward travel, not model-selected detours.
                clauses = intent_parser.movement_clauses(self.request_text)
                later = clauses[clauses.index(p.original_span) + 1:] if p.original_span in clauses else []
                for clause in later:
                    if not (intent_parser.has_movement_verb(clause) or re.match(r'^(?:我(?:們)?)?(?:再)?(?:到|經)', clause)):
                        continue
                    matches = [(clause.rfind(r['name']), key, r['id'])
                               for key, m in state.scene_maps.items() for r in m.get('rooms', [])
                               if r.get('name') and r['name'] in clause]
                    if matches:
                        _, target_page, target_room = max(matches)
                        expected = (target_page, target_room)
                if (current_page, current_room) != expected:
                    return fail('movement_destination_mismatch')
            room_data = scene_map.get_room(state.scene_maps.get(current_page, {}), current_room)
            if current_page != page or not room_data or destination not in {current_room, room_data.get('name')}:
                return fail('movement_destination_mismatch')
        else:
            if path:
                return fail('mapless_path_must_be_empty')
            if any(destination in {r.get('id'), r.get('name')} for m in state.scene_maps.values() for r in m.get('rooms', [])):
                return fail('known_map_location_requires_path')
            current_page, current_room = '', ''
        # Require source support for the destination itself, not just any
        # arbitrary quote copied from the scenario.
        label = (scene_map.get_room(state.scene_maps[page], current_room) or {}).get('name', destination) if page else destination
        if not any(label in self.sources[e['source']] for e in evidence):
            return fail('destination_not_supported_by_evidence')
        return {'ok': True, 'page': current_page, 'room': current_room, 'facing': facing}


def session_for(state: GroupState, actor: str, subject: str, text: str, rag: str = '', *, actor_is_keeper: bool = False) -> MovementSession:
    from app import keeper
    sources = {'scenario_context': rag} if rag and not scenario_retrieval.incomplete_roots(rag) else {}
    if not keeper.SCENARIO_RAG_ENABLED and state.scenario_text:
        sources['scenario_context'] = keeper._bounded_scenario_context(state.scenario_text)
    for entry in state.established_facts:
        if entry.get('source_event_id') and entry.get('text'):
            sources[f"fact:{entry['source_event_id']}"] = entry['text']
    from dataclasses import replace
    proposal = propose(state, actor, subject, text)
    if proposal:
        proposal = replace(proposal, actor_is_keeper=actor_is_keeper)
    return MovementSession(proposal, text, sources, actor_id=actor, subject_id=subject, actor_is_keeper=actor_is_keeper)


def resume(state: GroupState, subject: str, context: dict) -> dict | None:
    """Consume only a final, deterministically resolved, action-bound check.

    Called before restricted narration. Never roll, infer a replacement check,
    or silently re-adjudicate changed sources/origin.
    """
    saved = state.movement_continuations.get(subject)
    if not saved or context.get('check_id') != saved.get('check_id'):
        return None
    data = dict(saved['proposal'])
    data['origin'] = tuple(data['origin'])
    data['evidence_refs'] = tuple(data.get('evidence_refs', ()))
    p = MovementProposal(**data)
    if (context.get('check_id') != saved['check_id'] or context.get('timeline_id') != p.timeline_id
            or context.get('action_context') != p.original_span or context.get('skill') != saved['skill']
            or state.pending_luck_decisions.get(subject)
            or saved.get('decision_id', '') != context.get('decision_id', '')):
        return _reject('movement_continuation_identity_mismatch', state, saved.get('arguments', {}), p.original_span)
    if not re.search(r'成功|success', str(context.get('outcome', '')), re.IGNORECASE) or re.search(r'失敗|fail', str(context.get('outcome', '')), re.IGNORECASE):
        return _reject('movement_check_failed', state, saved.get('arguments', {}), p.original_span)
    session = MovementSession(p, p.original_span, saved['sources'])
    session._final_check_id = saved['check_id']
    session._final_skill = saved['skill']
    return session.commit(state, {**saved['arguments'], 'conditions': 'clear'})
