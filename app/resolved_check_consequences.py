"""Source-bound, idempotent consequences of already settled investigator checks.

The Keeper decides which scenario rule applies before requesting the original
check. Python stores that typed decision with source text, then validates the
settled outcome and actor before a follow-up tool may change state.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Literal, NotRequired, TypedDict, cast

from app.models import Character, GroupState

ConsequenceKind = Literal["damage", "check"]
TriggerOutcome = Literal["success", "failure"]
DamageType = Literal["impact", "fire", "cold", "poison", "other"]
Difficulty = Literal["regular", "hard", "extreme"]


class ConsequenceRule(TypedDict):
    key: str
    kind: ConsequenceKind
    when: TriggerOutcome
    source_quote: str
    damage_type: NotRequired[DamageType]
    damage_expression: NotRequired[str]
    final_damage: NotRequired[int]
    skill: NotRequired[str]
    difficulty: NotRequired[Difficulty]
    next_consequences: NotRequired[list[ConsequenceRule]]


_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_EXPRESSION = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.IGNORECASE)
_DAMAGE_TYPES = frozenset({"impact", "fire", "cold", "poison", "other"})


def _source_text(text: str) -> str:
    return " ".join(re.sub(r"[*_`]+", "", text).casefold().split())


def canonical_expression(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def normalize_authorizations(
    state: GroupState, raw: Any, *, nested: bool = False,
) -> list[ConsequenceRule]:
    """Validate a plan while the originating check is being registered.

    Exact source text proves the referenced rule is present in this active
    scenario. The Keeper still adjudicates its meaning; a later model call
    cannot introduce a new rule or change its outcome condition.
    """
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 4:
        raise ValueError("consequences 必須是最多四項的清單")
    scenario = _source_text(state.scenario_text)
    if not scenario and raw:
        raise ValueError("沒有劇本來源，不能預先授權檢定後果")
    normalized: list[ConsequenceRule] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("每項檢定後果必須是物件")
        key = item.get("key")
        kind = item.get("kind")
        when = item.get("when")
        quote = item.get("source_quote")
        if not isinstance(key, str) or not _KEY.fullmatch(key) or key in seen:
            raise ValueError("檢定後果 key 無效或重複")
        if kind not in {"damage", "check"} or when not in {"success", "failure"}:
            raise ValueError("檢定後果 kind/when 無效")
        if not isinstance(quote, str) or len(quote.strip()) < 20 or _source_text(quote) not in scenario:
            raise ValueError("檢定後果的 source_quote 不在目前劇本中")
        seen.add(key)
        rule: ConsequenceRule = {"key": key, "kind": cast(ConsequenceKind, kind), "when": cast(TriggerOutcome, when),
                                "source_quote": quote.strip()[:800]}
        if kind == "damage":
            damage_type = item.get("damage_type")
            if damage_type not in _DAMAGE_TYPES:
                raise ValueError("非戰鬥傷害的 damage_type 無效")
            expression = item.get("damage_expression")
            amount = item.get("final_damage")
            if (expression is None) == (amount is None):
                raise ValueError("傷害規則必須只指定骰式或固定傷害其中一種")
            rule["damage_type"] = cast(DamageType, damage_type)
            if expression is not None:
                if not isinstance(expression, str):
                    raise ValueError("傷害骰式無效")
                canonical = canonical_expression(expression)
                match = _EXPRESSION.fullmatch(canonical)
                if (not match or not 1 <= int(match.group(1) or "1") <= 100
                        or not 2 <= int(match.group(2)) <= 1000):
                    raise ValueError("傷害骰式無效")
                if canonical not in canonical_expression(_source_text(quote)):
                    raise ValueError("傷害骰式未出現在來源引文中")
                rule["damage_expression"] = canonical
            else:
                if type(amount) is not int or amount < 0:
                    raise ValueError("固定傷害必須是非負整數")
                if not re.search(rf"(?<!\d){amount}(?!\d)", quote):
                    raise ValueError("固定傷害數值未出現在來源引文中")
                rule["final_damage"] = amount
        else:
            skill = item.get("skill")
            difficulty = item.get("difficulty", "regular")
            if not isinstance(skill, str) or not skill.strip() or len(skill) > 80:
                raise ValueError("後續檢定技能無效")
            if difficulty not in {"regular", "hard", "extreme"}:
                raise ValueError("後續檢定難度無效")
            rule.update({"skill": skill.strip(), "difficulty": cast(Difficulty, difficulty)})
            next_rules = item.get("next_consequences")
            if next_rules is not None:
                if nested:
                    raise ValueError("後續檢定不能再遞迴建立後果計畫")
                rule["next_consequences"] = normalize_authorizations(state, next_rules, nested=True)
        normalized.append(rule)
    return normalized


def persist_origin(state: GroupState, event: dict[str, Any]) -> bool:
    """Persist a settled result's pre-authorized plan before follow-up starts.

    Caller holds the state lock and saves the snapshot. False means no plan
    existed or the same origin was already recorded.
    """
    plans = event.get("consequences")
    if not isinstance(plans, list) or not plans:
        return False
    event_id = str(event.get("event_id") or "")
    if not event_id or event.get("timeline_id") != state.timeline_id:
        return False
    char = state.get_active_character(str(event.get("owner_id") or ""))
    if char is None or char.character_id != event.get("character_id"):
        return False
    if not isinstance(event.get("success"), bool):
        return False
    origin = {
        "event_id": event_id,
        "check_id": event.get("check_id"),
        "timeline_id": event.get("timeline_id"),
        "owner_id": event.get("owner_id"),
        "character_id": event.get("character_id"),
        "investigator": event.get("investigator"),
        "skill": event.get("skill"),
        "success": event.get("success"),
        "authorizations": deepcopy(plans),
    }
    existing = state.check_consequence_origins.get(event_id)
    if existing is not None:
        if existing != origin:
            raise ValueError("同一來源檢定事件的後果授權已變更")
        return False
    state.check_consequence_origins[event_id] = origin
    return True


def consequence_identity(
    state: GroupState, event_id: str, key: str, character_id: str,
) -> str:
    timeline = state.timeline_id or f"legacy-{state.group_id}"
    return json.dumps([timeline, event_id, key, character_id], ensure_ascii=False)


def request_fingerprint(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def authorized(
    state: GroupState, *, actor_id: str, investigator: str, check_id: str,
    event_id: str, key: str, kind: ConsequenceKind,
) -> tuple[Character, dict[str, Any], dict[str, Any]]:
    origin = state.check_consequence_origins.get(event_id)
    if not origin or origin.get("check_id") != check_id:
        raise ValueError("找不到已結算且已授權的來源檢定")
    if origin.get("timeline_id") != state.timeline_id:
        raise ValueError("來源檢定屬於過期的劇情時間線")
    if not actor_id or actor_id != origin.get("owner_id"):
        raise ValueError("只有來源檢定的玩家可提交後續結果")
    char = state.get_active_character(actor_id)
    if char is None or char.character_id != origin.get("character_id") or char.name != investigator:
        raise ValueError("來源檢定的調查員身分已改變")
    rule = next((entry for entry in origin.get("authorizations", [])
                 if entry.get("key") == key and entry.get("kind") == kind), None)
    if rule is None:
        raise ValueError("來源檢定沒有授權此後果")
    expected_success = rule.get("when") == "success"
    if origin.get("success") is not expected_success:
        raise ValueError("來源檢定的結果未觸發此後果")
    return char, origin, rule
