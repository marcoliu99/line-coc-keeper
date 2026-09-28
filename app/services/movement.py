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
from itertools import pairwise
from uuid import uuid4

from app import intent_parser, observability, scene_map
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


def _quote_matches_source(quote: str, source: str) -> bool:
    """Match copied evidence despite PDF/RAG typography and line wrapping.

    Words and other punctuation must still match; this is not fuzzy matching.
    """
    quote_map = str.maketrans({
        "‘": '"', "’": '"', "‚": '"', "‛": '"',
        "“": '"', "”": '"', "„": '"', "‟": '"',
    })
    normalize = lambda value: re.sub(r'\s+', ' ', value.translate(quote_map)).strip()
    return bool(normalize(quote) and normalize(quote) in normalize(source))


def _text_mentions_destination(destination: str, text: str) -> bool:
    """Match a requested place to visible source text without trusting a source ID.

    Keep CJK names as literal substrings. For mixed-language requests such as
    ``Corbitt House 所在街區``, accept the contiguous Latin name in the source.
    This deliberately avoids fuzzy similarity: a nearby but unrelated passage
    cannot authorize an unknown destination.
    """
    normalize = lambda value: re.sub(r'[^\w]+', '', value, flags=re.UNICODE).casefold()
    target = normalize(destination)
    source = normalize(text)
    if target and target in source:
        return True

    words = re.findall(r'[a-z0-9]+', destination.casefold())
    source_words = re.findall(r'[a-z0-9]+', text.casefold())
    if not words or len(words) > len(source_words):
        return False
    return any(source_words[index:index + len(words)] == words
               for index in range(len(source_words) - len(words) + 1))



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
    origin_facing: str = "N"


def position(state: GroupState, owner: str) -> tuple[str, str, str]:
    return (state.current_map_page.get(owner, ''), state.current_room_id.get(owner, ''),
            state.narrative_locations.get(owner, ''))


def source_version(state: GroupState) -> str:
    material = [state.scenario_text, state.scenario_library_id, state.scenario_variant_id,
                state.active_chapter_id, state.scene_maps]
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# Creatures and stand-ins a scenario names nowhere, so no index can list them.
# Only ever consulted for the text *before* a movement verb.
_THIRD_PARTY_SUBJECT_RE = re.compile(
    r'怪物|生物|野獸|影子|黑影|人影|身影|敵人|對方|守衛|警衛|老鼠|那隻|那個東西|那東西|一群'
)
# The player referring to themselves outranks anything else in the prefix, so
# 「我和怪物一起衝進地下室」 stays the player's movement.
_FIRST_PERSON_RE = re.compile(r'我|咱|自己')


def _other_actor_names(state: GroupState, subject: str) -> set[str]:
    own = state.get_active_character(subject)
    own_name = own.name if own else ''
    names: set[str] = set()
    for character in state.characters.values():
        names.add(getattr(character, 'name', '') or '')
    for combatant in state.combat.order:
        if not getattr(combatant, 'is_pc', False):
            names.add(getattr(combatant, 'name', '') or '')
    for npc in state.scenario_npc_index:
        names.add(str(npc.get('name') or ''))
        names.update(str(alias) for alias in (npc.get('aliases') or []))
    # Two characters minimum: a one-character "name" matches far too much.
    return {name for name in names if len(name) >= 2 and name != own_name}


def _third_party_clause(clause: str, state: GroupState, subject: str) -> bool:
    """Does this clause describe someone other than the acting player moving?

    `movement_clauses` drops a clause that *opens* with 他/她/牠/有人, but not
    one with a named subject. 「怪物衝進地下室，我開槍」 kept 「怪物衝進地下室」,
    and propose() would build a movement proposal for the player out of it. The
    Executor then resolves only the gunshot, and executor.py:232 downgrades an
    otherwise resolved turn to `arrival_not_committed` — because a move the
    player never asked for never arrived.

    A spurious proposal breaks the turn; a missing one does not, since the
    Executor can still quote the span and commit_movement will adjudicate it.
    So this rejects only on positive evidence: a prefix naming a known other
    actor, or reading as a third party. A verb-initial clause (「直奔商店」) and
    anything the player refers to themselves in are left alone.
    """
    start = intent_parser.movement_verb_start(clause)
    prefix = clause[:start] if start else ''
    if not prefix or _FIRST_PERSON_RE.search(prefix):
        return False
    if _THIRD_PARTY_SUBJECT_RE.search(prefix):
        return True
    return any(name in prefix for name in _other_actor_names(state, subject))


def propose(state: GroupState, actor: str, subject: str, text: str) -> MovementProposal | None:
    clauses = intent_parser.movement_clauses(text)
    movement_text = next((c for c in clauses
                          if (intent_parser.has_movement_verb(c)
                              or re.search(r'\b(?:go|enter|walk|move|leave)\b', c, re.IGNORECASE))
                          and not re.match(r'^(?:我(?:們)?)?(?:走去|去)買', c)
                          and not _third_party_clause(c, state, subject)), '')
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
    'description': ('裁定並提交玩家明確要求的移動。玩家明確要求前往且 RAG 命中目的地即可抵達；不要只因提到或查到地點就移動。'
                    '地圖協助定位和辨識已知障礙，OCR 缺少路徑不代表不能通行。保留劇本明確的鎖、檢定、遭遇與反應點。'
                    '到達後才能取得目的地物品或套用場景效果。'
                    '需要玩家檢定時先建立 skill_check，再以 prerequisite_check_id 綁定移動，等待最終 Luck。'
                    '沒有地圖也可用劇本地點名稱。敘事需交代移動過程，不得新增未有依據的中途地點或事件。'),
    'input_schema': {'type': 'object', 'properties': {
        'source_span': {'type': 'string', 'description': '若本回合未預先辨識移動，引用玩家原句中的移動子句，由既有 Executor 判讀；不可引用 OOC、假設、觀察或否定句'},
        'movement_kind': {'type': 'string', 'enum': ['local_path', 'scene_transition'],
                          'description': '房間內移動用 local_path；前往另一個劇情地點用 scene_transition。只作意圖提示，地圖 key/入口由程式解析。'},
        'destination': {'type': 'string'},
        'path': {'type': 'array', 'items': {'type': 'string'}, 'description': '可選的房間路徑建議；地圖 OCR 不完整時，不得只因缺少 graph edge 拒絕移動。'},
        'evidence': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'source': {'type': 'string'}, 'quote': {'type': 'string'}}, 'required': ['source', 'quote']}},
        'conditions': {'type': 'string', 'enum': ['clear', 'await_check', 'blocked']},
        'prerequisite_check_id': {'type': 'string'},
    }, 'required': ['movement_kind', 'destination', 'evidence', 'conditions']},
}
PROMPT = '''
Movement uses commit_movement in this existing tool loop, never a final JSON mutation.
Only move when the player's current message explicitly asks them to travel; merely
mentioning a location, a question, or a RAG hit never moves anyone. For an explicit
trip, a relevant current-turn RAG hit for the requested destination is enough to
arrive, even if that RAG record is incomplete for resolving the location's other
rules. Do not require a map edge or ask the model to invent a map key. Maps help
locate rooms and expose known blockers; missing OCR edges are unknown, not proof
that a route is impossible. Preserve explicit locks, checks, encounters and
reaction points. Narrate the travel leg using established facts only; never add
unsupported intermediate locations or events. A check at the origin may establish
entry prerequisites; bind its check_id with conditions=await_check and use the
exact original_span. An indoor check must wait for arrival. After arrival, continue
requested destination-dependent tools before final resolution. Independent
consumption of carried supplies before movement can remain.

Use only the exact source IDs shown under Available movement sources. If that list
is empty, or no listed source contains a usable passage for the destination, call
search_scenario first and cite the returned tool evidence_ref with an exact quote.
After a current-turn search, scenario_context may also cite its returned text;
the server binds that alias only to sources searched during this turn and still
requires an exact quote. Never cite memory or older turns as scenario_context. A
nonempty current-turn RAG hit can support travel even when complete_for_action is
false; that flag concerns other scenario-rule coverage, not whether the named
place was found.
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
    retrieval_sources: set[str] = field(default_factory=set)
    arrived: bool = False
    committed_position: tuple[str, str, str] | None = None
    event_id: str = ''
    rejected: bool = False
    _final_check_id: str = ""
    _final_skill: str = ""
    actor_id: str = ""
    subject_id: str = ""

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
        if name == 'search_scenario' and result.get('ok') and result.get('results'):
            text = str(result['results'])
            self.sources[ref] = text
            # Models often keep using the generic scenario_context label after
            # a search. Bind that alias to current-turn retrieved text only;
            # the source ID remains bound to text retrieved during this turn.
            existing_context = self.sources.get('scenario_context', '')
            self.sources['scenario_context'] = '\n'.join(x for x in (existing_context, text) if x)
            self.retrieval_sources.add(ref)
            self.retrieval_sources.add('scenario_context')

    def _source_content(self, reference: str) -> str | None:
        """Resolve a model citation to text actually supplied during this turn.

        The executor names the initial retrieved context ``scenario_context``.
        Models sometimes add its printed PDF page (for example
        ``scenario_context p.16``); that suffix is descriptive, not a new
        authority, so accept it only when the base source is registered for
        this turn. Non-RAG quotation matching is validated separately.
        """
        if reference in self.sources:
            return self.sources[reference]
        match = re.fullmatch(r'(scenario_context)\s+p\.\s*\d+', reference, re.IGNORECASE)
        return self.sources.get(match.group(1)) if match else None

    def _is_retrieval_source(self, reference: str) -> bool:
        if reference in self.retrieval_sources:
            return True
        return bool(re.fullmatch(r'scenario_context\s+p\.\s*\d+', reference, re.IGNORECASE)
                    and 'scenario_context' in self.retrieval_sources)

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
                origin_facing=state.party_facing.get(self.subject_id, "N"))
            self.proposal = p

        def mutate(latest: GroupState):
            mutation_admission.assert_admitted(latest.group_id, timeline_id=p.timeline_id)
            from app.services.narrative_corrections import blocking_reply
            if blocking_reply(latest, [p.original_span, args]):
                return keeper._StateMutation(_reject('narrative_correction_hold', latest, args, p.original_span), should_save=False)
            error = self.guard(latest, 'commit_movement', args)
            if error:
                return keeper._StateMutation(_reject(error, latest, args, p.original_span), should_save=False)
            # Only the investigator's own player, or the KP Assistant through sudo, may move them.
            if p.actor_id != p.subject_id and p.actor_id != latest.kp_assistant_user_id:
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
                    'sources': {e['source']: self._source_content(e['source']) or '' for e in args['evidence']},
                    'retrieval_sources': list(self.retrieval_sources),
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
                     'movement_kind': result.get('movement_kind', 'scene_transition'),
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
                           and len(e['quote'].strip()) >= 8
                           and (source_text := self._source_content(e['source'])) is not None
                           and (self._is_retrieval_source(e['source'])
                                or _quote_matches_source(e['quote'], source_text)) for e in evidence)):
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
        destination = args.get('destination')
        path = args.get('path', [])
        movement_kind = args.get('movement_kind', '')
        if (not isinstance(destination, str) or not destination.strip() or not isinstance(path, list)
                or movement_kind not in {'', 'local_path', 'scene_transition'}):
            return fail('invalid_movement_target')
        if len(destination) > 200 or len(path) > 30 or not all(isinstance(x, str) for x in path):
            return fail('invalid_movement_path')
        norm = lambda value: re.sub(r'\s+', ' ', value).strip().casefold()
        current_page, current_room, _ = p.origin
        current_map = state.scene_maps.get(current_page, {}) if current_page else {}
        proposed_room = scene_map.get_room(current_map, p.candidate_room) if p.candidate_room else None
        direction = intent_parser.parse_movement_intent(p.original_span)
        if (direction and proposed_room and destination not in {
                p.candidate_room, proposed_room.get('name')}):
            return fail('movement_direction_mismatch')
        movement_clauses = intent_parser.movement_clauses(self.request_text)
        requested_destination = any(
            (intent_parser.has_movement_verb(clause)
             or re.match(r'^(?:我(?:們)?)?(?:再)?(?:到|經)', clause))
            and norm(destination) in norm(clause)
            for clause in movement_clauses
        ) or norm(destination) in norm(p.original_span)
        if not requested_destination and not (
                p.candidate_room and destination in {p.candidate_room,
                    (scene_map.get_room(state.scene_maps.get(p.candidate_page, {}), p.candidate_room) or {}).get('name')}):
            return fail('movement_destination_mismatch')
        facing = state.party_facing.get(p.subject_id, 'N')

        def room_named(map_data: dict, name: str) -> dict | None:
            target = norm(name)
            return next((room for room in map_data.get('rooms', [])
                         if target in {norm(str(room.get('id', ''))), norm(str(room.get('name', '')))}
                         or (norm(str(room.get('name', ''))) and norm(str(room.get('name', ''))) in target)), None)

        current_map = state.scene_maps.get(current_page, {}) if current_page else {}
        target_room = room_named(current_map, destination) if current_map else None
        if not target_room and p.candidate_room and p.candidate_page == current_page:
            target_room = scene_map.get_room(current_map, p.candidate_room)

        # A citation proves where the text came from, not that the text supports
        # this destination. Accept a literal place/name in retrieved text, a
        # verified non-RAG quotation, or a destination already present in a
        # canonical map. Never let an unrelated current-turn RAG source alone
        # authorize an arbitrary named destination.
        mapped_names = [str(map_data.get('location_name', ''))
                        for map_data in state.scene_maps.values()]
        mapped_names.extend(str(room.get('name', ''))
                            for map_data in state.scene_maps.values()
                            for room in map_data.get('rooms', []))
        destination_in_map = any(
            norm(destination) == norm(name) or
            (norm(name) and norm(name) in norm(destination))
            for name in mapped_names
        )
        destination_in_evidence = any(
            (_text_mentions_destination(destination, self._source_content(e['source']) or '')
             if self._is_retrieval_source(e['source']) else
             _text_mentions_destination(destination, e['quote']))
            for e in evidence
        )
        if not destination_in_evidence and not destination_in_map:
            return fail('destination_not_supported_by_evidence')

        # A destination matching a room in the current map is local even if
        # the model mislabeled it as a scene transition. The map is advisory:
        # only explicit gates found on a supplied/known edge block the action.
        if target_room and current_page:
            target_page, target_room_id = current_page, target_room['id']
            steps: list[str] = []
            for step in path:
                _, room_id = step.split(':', 1) if ':' in step else (current_page, step)
                if scene_map.get_room(current_map, room_id):
                    steps.append(room_id)
            if (p.candidate_room and p.candidate_page == current_page
                    and p.candidate_room != target_room_id and p.candidate_room not in steps):
                # If the player named an intermediate destination first, that
                # point must be represented in the submitted route before a
                # later requested destination can be committed.
                return fail('movement_destination_mismatch')
            if target_room_id not in steps:
                steps.append(target_room_id)
            def edge_error(edge: dict) -> str:
                if edge.get('blocked') or (edge.get('locked') and not (
                        check_id and re.search(r'鎖匠|locksmith', self._final_skill, re.IGNORECASE))):
                    return 'passage_blocked'
                if edge.get('requires_check') and edge.get('requires_check') != self._final_skill:
                    return 'movement_required_check_missing'
                if edge.get('reaction') or edge.get('requires_choice'):
                    return 'movement_reaction_point_unresolved'
                return ''

            def find_route(start: str, goal: str, *, traversable_only: bool) -> list[dict] | None:
                rooms = current_map.get('rooms', [])
                room_ids = {str(room.get('id', '')) for room in rooms}
                queue: list[tuple[str, list[dict]]] = [(start, [])]
                visited = {start}
                while queue:
                    room_id, route_edges = queue.pop(0)
                    if room_id == goal:
                        return route_edges
                    room_data = scene_map.get_room(current_map, room_id) or {}
                    for edge in room_data.get('exits', []):
                        next_id = str(edge.get('to', ''))
                        if next_id not in room_ids or next_id in visited:
                            continue
                        if traversable_only and edge_error(edge):
                            continue
                        visited.add(next_id)
                        queue.append((next_id, [*route_edges, edge]))
                return None

            route = [current_room, *steps]
            for origin_id, next_id in pairwise(route):
                origin_data = scene_map.get_room(current_map, origin_id)
                edge = next((e for e in (origin_data or {}).get('exits', [])
                             if e.get('to') == next_id), None)
                if edge is None:
                    # OCR may omit a real connection. But if the map does show
                    # a route between these rooms, resolve it and inspect every
                    # known edge instead of treating the endpoints as adjacent.
                    known_route = find_route(origin_id, next_id, traversable_only=True)
                    if known_route is None:
                        known_route = find_route(origin_id, next_id, traversable_only=False)
                    if known_route is None:
                        continue
                    for known_edge in known_route:
                        error = edge_error(known_edge)
                        if error:
                            return fail(error)
                        if known_edge.get('compass') not in {'U', 'D'}:
                            facing = known_edge.get('compass', facing)
                    continue
                error = edge_error(edge)
                if error:
                    return fail(error)
                if edge.get('compass') not in {'U', 'D'}:
                    facing = edge.get('compass', facing)
        else:
            # Resolve mapped destinations in Python. Prefer an exact scene
            # label; a room on another map is also a valid explicitly requested
            # destination, without requiring a cross-map graph edge.
            matches: list[tuple[str, dict, dict | None]] = []
            for map_key, map_data in state.scene_maps.items():
                location = norm(str(map_data.get('location_name', '')))
                requested = norm(destination)
                if location and requested == location:
                    matches.append((map_key, map_data, None))
                else:
                    room = room_named(map_data, destination)
                    if room:
                        matches.append((map_key, map_data, room))
            if len(matches) == 1:
                target_page, target_map, named_room = matches[0]
                entry_id = target_map.get('entry_room_id', '')
                entry_room = scene_map.get_room(target_map, entry_id) if entry_id else None
                target_room_id = (named_room or entry_room or {}).get('id', '')
            elif len(matches) > 1:
                # Same-name maps may be separate floors or distinct places.
                # Without explicit metadata linking them, choosing one would
                # silently move the investigator to an arbitrary map.
                return fail('ambiguous_mapped_destination')
            else:
                # A supported scenario location with no map remains a valid
                # narrative position.
                target_page, target_room_id = '', ''

        return {'ok': True, 'page': target_page, 'room': target_room_id,
                'facing': facing, 'movement_kind': 'local_path' if target_page == current_page and target_room else 'scene_transition'}


def session_for(state: GroupState, actor: str, subject: str, text: str, rag: str = '') -> MovementSession:
    from app import keeper
    # Incomplete retrieval is insufficient for resolving the scenario's full
    # mechanics, but its exact passages remain valid evidence that a requested
    # destination exists. Movement validates quote provenance separately.
    sources = {'scenario_context': rag} if rag else {}
    retrieval_sources = {'scenario_context'} if sources and keeper.SCENARIO_RAG_ENABLED else set()
    if not keeper.SCENARIO_RAG_ENABLED and state.scenario_text:
        sources['scenario_context'] = keeper._bounded_scenario_context(state.scenario_text)
    for entry in state.established_facts:
        if entry.get('source_event_id') and entry.get('text'):
            sources[f"fact:{entry['source_event_id']}"] = entry['text']
    proposal = propose(state, actor, subject, text)
    return MovementSession(proposal, text, sources, retrieval_sources=retrieval_sources,
                           actor_id=actor, subject_id=subject)


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
    session = MovementSession(p, p.original_span, saved['sources'], set(saved.get('retrieval_sources', [])))
    session._final_check_id = saved['check_id']
    session._final_skill = saved['skill']
    return session.commit(state, {**saved['arguments'], 'conditions': 'clear'})
