"""Import-only hidden routes, extracted from explicit canonical source assertions.

No provider is called. Endpoint names must uniquely match the visual inventory.
The accepted English grammar is deliberately narrow: an affirmative whole
sentence asserting a route, optionally prefixed by an explicit When/If clause.
Unrecognized prose never authorizes a route.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

VERSION = 'source-topology-v1'
RouteKind = Literal['hidden_passage', 'secret_door', 'conditional_route', 'breakable_wall',
                    'blocked_passage', 'sealed_door', 'collapsible_barrier']
Authority = Literal['visual', 'scenario_source']
Visibility = Literal['visible', 'hidden']
Availability = Literal['available', 'undiscovered', 'blocked']
ProgressionPolicy = Literal['unspecified', 'retryable', 'fail_forward']
SourceCompass = Literal['', 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'U', 'D']
TopologyError = Literal['unresolved_hidden_endpoints', 'ambiguous_hidden_endpoints', 'missing_route_condition']


class SourceEvidence(TypedDict):
    page: int
    span_start: int
    span_end: int
    span_sha256: str
    canonical_source_sha256: str


class RouteCondition(TypedDict):
    kind: Literal['world_state']
    key: str
    expected: bool
    source_text: str


SourceRoute = TypedDict('SourceRoute', {
    'id': str, 'from': str, 'to': str, 'type': RouteKind,
    'authority': Authority, 'visibility': Visibility, 'availability': Availability,
    'condition': RouteCondition, 'progression_policy': ProgressionPolicy,
    'compass': SourceCompass, 'source_evidence': SourceEvidence,
})


@dataclass(frozen=True)
class SourceTopology:
    routes: list[SourceRoute]
    diagnostics: list[dict[str, str]]


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


_PAGE = re.compile(r'^--- 第 ([1-9][0-9]*) 頁 ---\n', re.MULTILINE)
# Do not search inside a larger sentence: "There is no ..." must never match.
_ASSERTION = re.compile(
    r'(?:(?:When|If) (?P<condition>[^\n.!?]{1,300}),\s+)?'
    r'(?:A|The|There is a) (?P<two_way>two-way )?(?P<hidden>hidden )?'
    r'(?P<kind>hidden passage|secret door|conditional route|breakable wall|blocked passage|sealed door|collapsible barrier) '
    r'(?:(?:connects|links) (?P<origin>.+?) to (?P<target>.+?)|'
    r'(?:leads|runs) from (?P<from>.+?) to (?P<to>.+?))'
    r'(?P<progress>; it is necessary for progress)?[.!]?\Z', re.IGNORECASE)
_KIND: dict[str, RouteKind] = {'hidden passage': 'hidden_passage', 'secret door': 'secret_door',
    'conditional route': 'conditional_route', 'breakable wall': 'breakable_wall',
    'blocked passage': 'blocked_passage', 'sealed door': 'sealed_door', 'collapsible barrier': 'collapsible_barrier'}
_DIRECTIONS: dict[str, SourceCompass] = {'north': 'N', 'northeast': 'NE', 'east': 'E', 'southeast': 'SE',
               'south': 'S', 'southwest': 'SW', 'west': 'W', 'northwest': 'NW', 'up': 'U', 'down': 'D'}
_HIDDEN_KINDS = {'hidden_passage', 'secret_door'}


def route_hash(route: Any) -> str:
    return source_hash(json.dumps(route, sort_keys=True, ensure_ascii=False))


def extract(graph: dict, canonical_source: str) -> SourceTopology:
    """Bind affirmative, explicit routes to exact full-source Unicode spans.

    Callers supply final source only, after its publication integrity gates.
    Labels repeated across floors are ambiguous and cannot authorize endpoints.
    No reciprocal edge, compass, trigger or invisible room is inferred.
    """
    labels = [(str(room.get('visible_label', room.get('name', ''))).strip().casefold(), room['id'])
              for room in graph['rooms']]
    counts = Counter(label for label, _ in labels)
    endpoints = {label: room_id for label, room_id in labels if counts[label] == 1 and label}
    pages = list(_PAGE.finditer(canonical_source))
    routes: list[SourceRoute] = []
    diagnostics: list[dict[str, str]] = []
    seen = set()
    digest = source_hash(canonical_source)
    for index, page in enumerate(pages):
        start = page.end()
        end = pages[index + 1].start() if index + 1 < len(pages) else len(canonical_source)
        # A sentence can wrap across PDF text lines. Paragraph/sentence boundaries
        # keep negated or speculative surrounding prose out of the match.
        for sentence in re.finditer(r'[^.!?]+(?:[.!?]|\Z)', canonical_source[start:end]):
            raw = sentence.group()
            stripped = raw.strip()
            assertion = _ASSERTION.fullmatch(re.sub(r'\s+', ' ', stripped))
            if assertion is None:
                continue
            origin = (assertion['origin'] or assertion['from']).strip().casefold()
            target = (assertion['target'] or assertion['to']).rstrip('.!').strip().casefold()
            compass: SourceCompass = ''
            for direction, canonical in _DIRECTIONS.items():
                suffix = ' to the ' + direction
                if target.endswith(suffix):
                    target, compass = target[:-len(suffix)], canonical
                    break
            condition = assertion['condition'] or ''
            kind = _KIND[assertion['kind'].casefold()]
            if origin not in endpoints or target not in endpoints or origin == target:
                code: TopologyError = ('ambiguous_hidden_endpoints' if counts[origin] > 1 or counts[target] > 1
                                       else 'unresolved_hidden_endpoints')
                diagnostics.append({'code': code, 'span_sha256': source_hash(stripped)})
                continue
            # A conditional route without an explicit trigger is not playable evidence.
            if kind == 'conditional_route' and not condition:
                diagnostics.append({'code': 'missing_route_condition', 'span_sha256': source_hash(stripped)})
                continue
            key = (endpoints[origin], endpoints[target], kind, condition)
            if key in seen:
                continue
            seen.add(key)
            span_start = start + sentence.start() + len(raw) - len(raw.lstrip())
            span_end = start + sentence.end() - len(raw) + len(raw.rstrip())
            evidence: SourceEvidence = {'page': int(page[1]), 'span_start': span_start, 'span_end': span_end,
                'span_sha256': source_hash(canonical_source[span_start:span_end]), 'canonical_source_sha256': digest}
            identity = 'src_' + route_hash([endpoints[origin], endpoints[target], kind, evidence])[:24]
            hidden = kind in _HIDDEN_KINDS or bool(assertion['hidden'])
            progression: ProgressionPolicy = ('fail_forward' if assertion['progress'] else
                                               'retryable' if kind in ('breakable_wall', 'collapsible_barrier') else 'unspecified')
            pairs = [(endpoints[origin], endpoints[target])]
            if assertion['two_way']:
                pairs.append((endpoints[target], endpoints[origin]))
            for origin_id, target_id in pairs:
                edge_id = 'src_' + route_hash([origin_id, target_id, kind, evidence])[:24]
                reverse_compass: dict[SourceCompass, SourceCompass] = {'N': 'S', 'NE': 'SW', 'E': 'W', 'SE': 'NW', 'S': 'N',
                                   'SW': 'NE', 'W': 'E', 'NW': 'SE', 'U': 'D', 'D': 'U', '': ''}
                routes.append({'id': edge_id, 'from': origin_id, 'to': target_id, 'type': kind,
                    'authority': 'scenario_source', 'visibility': 'hidden' if hidden else 'visible',
                    'availability': 'undiscovered' if kind in _HIDDEN_KINDS and not condition else 'blocked',
                    'condition': {'kind': 'world_state', 'key': identity + '_available',
                                  'expected': True, 'source_text': condition},
                    'progression_policy': progression,
                    'compass': compass if origin_id == endpoints[origin] else reverse_compass[compass],
                    'source_evidence': evidence})
    return SourceTopology(routes, diagnostics)


def structurally_valid(graph: dict) -> bool:
    """Validate storage shape only; certificate replay proves source authority."""
    routes = graph.get('source_topology', [])
    if not isinstance(routes, list) or not isinstance(graph.get('rooms'), list):
        return False
    ids = {room['id'] for room in graph['rooms'] if isinstance(room, dict) and isinstance(room.get('id'), str)}
    seen = set()
    for route in routes:
        if not isinstance(route, dict):
            return False
        if (not isinstance(route.get('id'), str) or not re.fullmatch(r'src_[a-f0-9]{24}', route['id'])
                or not isinstance(route.get('from'), str) or route['from'] not in ids
                or not isinstance(route.get('to'), str) or route['to'] not in ids or route['from'] == route['to']
                or route.get('type') not in _KIND.values() or route.get('authority') != 'scenario_source'
                or route.get('visibility') not in ('hidden', 'visible')
                or route.get('availability') not in ('undiscovered', 'blocked')
                or route.get('progression_policy') not in ('unspecified', 'retryable', 'fail_forward')
                or route.get('compass') not in ('', *_DIRECTIONS.values())):
            return False
        condition = route.get('condition')
        if (not isinstance(condition, dict) or condition.get('kind') != 'world_state'
                or not isinstance(condition.get('key'), str)
                or not re.fullmatch(r'src_[a-f0-9]{24}_available', condition['key'])
                or condition.get('expected') is not True
                or not isinstance(condition.get('source_text'), str)
                or (route['type'] == 'conditional_route' and not condition['source_text'])
                or (route['type'] in _HIDDEN_KINDS and route['visibility'] != 'hidden')
                or (route['availability'] == 'undiscovered' and route['type'] not in _HIDDEN_KINDS)):
            return False
        evidence = route.get('source_evidence')
        if (not isinstance(evidence, dict)
                or any(type(evidence.get(key)) is not int for key in ('page', 'span_start', 'span_end'))
                or evidence['page'] < 1 or not 0 <= evidence['span_start'] < evidence['span_end']
                or any(not isinstance(evidence.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', evidence[key])
                       for key in ('span_sha256', 'canonical_source_sha256'))):
            return False
        if route['id'] in seen:
            return False
        seen.add(route['id'])
    return True
