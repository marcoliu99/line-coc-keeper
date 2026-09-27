"""Trusted request routing, independent of a model's proposed output labels."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.domain.models import AgentMessage

IntentType = Literal['PURE_ROLEPLAY', 'GAMEPLAY_ACTION', 'PLAYER_OOC', 'OOC_ASSISTANT']
_PURE_ROLEPLAY_EXACT = {'好', 'ok', '嗯', '知道', '了解', '收到', '沒問題', '是的', '對'}
_RULES = re.compile(r'(?:為什麼|為何|怎麼|如何|規則|規定|why|how).*(?:骰|檢定|規則|規定|技能|luck|roll|rule)', re.IGNORECASE)
_SELF = re.compile(r'(?:我的|自己(?:的)?|my\s).*(?:角色卡|背景|秘密|數值|技能|背包|sheet|secret)', re.IGNORECASE)
_PARENS = re.compile(r'[（(][^（）()]*[）)]')


@dataclass(frozen=True)
class RequestSpan:
    ref: str
    start: int
    end: int
    text: str
    mode: str
    audience: str = 'public'


@dataclass(frozen=True)
class RouteDecision:
    speaker_role: str
    message_mode: str
    turn_kind: str
    intent: IntentType
    audience: str
    canonical_policy: str
    retrieval_plan: str
    spans: tuple[RequestSpan, ...]

    @property
    def ic_text(self) -> str:
        return '\n'.join(s.text for s in self.spans if s.mode == 'IC')


def route_request(text: str, speaker_role: str, turn_kind: str = 'player_action') -> RouteDecision:
    spans: list[RequestSpan] = []

    def append(start: int, end: int, mode: str) -> None:
        value = text[start:end]
        if not value.strip(' ，,。;；\n'):
            return
        spans.append(RequestSpan(f'span:{len(spans)}', start, end, value, mode,
                                 'player_private' if mode == 'OOC' and _SELF.search(value) else 'public'))

    stripped = text.strip()
    if turn_kind != 'player_action' or speaker_role == 'kp_assistant':
        append(0, len(text), 'IC' if turn_kind != 'player_action' else 'OOC')
    elif re.match(r'^(?:ooc\s*[:：]|場外\s*[:：])', stripped, re.IGNORECASE):
        append(0, len(text), 'OOC')
    else:
        cursor = 0
        for match in _PARENS.finditer(text):
            inner = match.group()[1:-1].strip()
            if (_RULES.search(inner) or _SELF.search(inner)
                    or re.match(r'^(?:ooc\s*[:：]|場外\s*[:：])', inner, re.IGNORECASE)):
                append(cursor, match.start(), 'IC')
                append(match.start(), match.end(), 'OOC')
                cursor = match.end()
        append(cursor, len(text), 'IC')
        if len(spans) == 1 and (_RULES.fullmatch(stripped.rstrip('？?')) or _SELF.fullmatch(stripped.rstrip('？?'))):
            spans.clear()
            append(0, len(text), 'OOC')
    modes = {s.mode for s in spans}
    mode = 'mixed' if len(modes) > 1 else 'OOC' if modes == {'OOC'} else 'IC'
    intent: IntentType = ('OOC_ASSISTANT' if speaker_role == 'kp_assistant' else
                          'PLAYER_OOC' if mode == 'OOC' else
                          'PURE_ROLEPLAY' if stripped.lower() in _PURE_ROLEPLAY_EXACT or not stripped else
                          'GAMEPLAY_ACTION')
    audience = 'player_private' if spans and all(s.audience == 'player_private' for s in spans) else 'public'
    return RouteDecision(speaker_role, mode, turn_kind, intent, audience,
                         'exclude' if mode == 'OOC' else 'validated_segments' if mode == 'mixed' else 'turn',
                         'none' if mode == 'OOC' else 'ic_only', tuple(spans))


def classify_intent(message: AgentMessage) -> IntentType:
    route = message.payload.get('route_decision')
    if not isinstance(route, RouteDecision):
        route = route_request(message.payload.get('text', ''), message.payload.get('speaker_role', 'player'))
    return route.intent
