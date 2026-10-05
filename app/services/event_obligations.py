"""Mechanical obligations that a scenario states for an event, found in the evidence a turn was given.

When a scenario says "seeing the hand costs SAN 1/1D4" and the narration shows the hand, the loss is owed
in that turn, not when a player later says they are scared. This module reads only what the scenario wrote:
a sentence with an explicit SAN loss, a stated damage roll with a trigger, or a required skill check with a
trigger. Atmospheric horror without such a rule produces nothing. It parses and decides; it never changes state
(``app/agents/obligation_gate.py`` applies what it finds through the ordinary deterministic tools).
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["sanity_check", "damage", "forced_check"]
KINDS: tuple[Kind, ...] = ("sanity_check", "damage", "forced_check")

_DICE = r"\d*\s*[dD]\s*\d+(?:\s*[+-]\s*\d+)?"
_SAN = re.compile(
    rf"(?:SAN(?:ITY)?(?:\s*CHECK)?|SANC|理智(?:檢定|損失|值)?)\D{{0,12}}?(?P<ok>{_DICE}|\d+)\s*/\s*(?P<bad>{_DICE}|\d+)",
    re.IGNORECASE,
)
_DAMAGE = re.compile(
    rf"(?:受到|造成|承受|損失|takes?|suffers?|loses?)\s*(?P<amount>{_DICE}|\d+)\s*(?:點)?\s*(?:HP|生命值?|傷害|damage|hit points?)",
    re.IGNORECASE,
)
_CHECK = re.compile(
    r"(?:須|必須|需要|需|要)\s*(?:通過|進行|成功|擲)?\s*(?:一次|一個)?\s*(?:(?P<level>困難|極難)\s*)?"
    r"(?P<skill>[一-鿿A-Za-z]{1,10}?)\s*檢定"
    r"|(?:must|needs? to)\s+(?:pass|make|succeed (?:on|at))\s+an?\s+(?:(?P<level_en>hard|extreme)\s+)?(?P<skill_en>[A-Za-z ]{2,20}?)\s+(?:check|roll)",
    re.IGNORECASE,
)
_TRIGGER = re.compile(r"若|如果|假如|倘若|當|一旦|每當|只要|凡是|觸發|踩|碰|觸碰|打開|走進|進入|踏入|經過|\b(?:if|when|once|upon|whenever|touch(?:es)?|opens?|steps?|enters?)\b", re.IGNORECASE)
_COMBAT = re.compile(r"攻擊|戰鬥|格鬥|射擊|武器|反擊|\b(?:attacks?|combat|fights?|weapon|shoots?)\b", re.IGNORECASE)
_REPEATING = re.compile(r"每次|每回合|每輪|每當|\b(?:every time|each time|each round|per round)\b", re.IGNORECASE)
_SKILL_REFUSED = {"SAN", "san", "理智", "幸運", "Luck", "luck", "一次", "進行"}
_LEVELS = {"困難": "hard", "極難": "extreme", "hard": "hard", "extreme": "extreme"}
_HEADER = re.compile(r"^--- (?:原稿補查 · )?第 \d+ 頁 ---$", re.MULTILINE)
_SENTENCE = re.compile(r"(?<=[。！？!?])|(?<=[.;；])\s+|\n+")
# Words that say what the rule is about, not what it is triggered by; they cannot anchor a match.
_GENERIC = re.compile(
    r"調查員|玩家|角色|走進|進入|踏入|經過|任何人|任何|所有人|目擊者|看見者|檢定|進行|必須|需要|需做|須|需|理智|損失|傷害|受到|造成|點數|"
    r"發現|看見|看到|見到|目睹|找到|注意到|打開|拿起|觸碰|碰到|踩到|聽見|聽到|"
    r"若是|如果|假如|倘若|一旦|每當|只要|凡是|當|若|時|的|了|是|在|會|將|並|而|與|和|或|有|被|其|該|此"
)
_ASCII_WORD = re.compile(r"[A-Za-z0-9]{3,}")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_STOP_WORDS = frozenset({
    "the", "and", "this", "that", "with", "when", "once", "upon", "every", "each", "time", "any", "all",
    "investigator", "investigators", "player", "character", "check", "must", "make", "take", "takes", "suffer",
    "suffers", "loses", "lose", "damage", "points", "point", "sanity", "san", "sees", "see", "finds", "find",
    "seeing", "seen", "who", "which", "from", "are", "was", "were", "has", "have", "into", "their", "they", "them", "his", "her",
    "for", "not", "than", "then", "also", "only",
})


@dataclass(frozen=True)
class Obligation:
    kind: Kind
    key: str
    trigger: str
    args: tuple[tuple[str, str], ...]
    anchors: frozenset[str]
    once_per: Literal["investigator", "turn"]

    def arg(self, name: str, default: str = "") -> str:
        return dict(self.args).get(name, default)


def _dice(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _clean(evidence: str) -> str:
    text = evidence.rsplit("【取用完整性】", 1)[0]
    text = _HEADER.sub("", text)
    return re.sub(r"[*_`]+", "", text)


def _terms(text: str) -> frozenset[str]:
    text = _GENERIC.sub(" ", text)
    terms = {word.lower() for word in _ASCII_WORD.findall(text)} - _STOP_WORDS
    for run in _CJK_RUN.findall(text):
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return frozenset(terms)


def _anchors(sentence: str, rule_span: tuple[int, int]) -> frozenset[str]:
    """What the sentence names as the cause: its distinctive words before the rule, or after it when
    nothing precedes it. What follows a check ("otherwise you hear nothing") is its outcome, not its cause."""
    return _terms(sentence[:rule_span[0]]) or _terms(sentence[rule_span[1]:])


def _key(kind: str, sentence: str) -> str:
    return hashlib.sha256(f"{kind}|{' '.join(sentence.split())}".encode()).hexdigest()[:12]


def extract(evidence: str) -> list[Obligation]:
    """Explicit obligations stated by the scenario passages in ``evidence``, in order, without repeats."""
    found: list[Obligation] = []
    seen: set[str] = set()
    for raw in _SENTENCE.split(_clean(evidence)):
        sentence = raw.strip()
        if len(sentence) < 6:
            continue
        repeating: Literal["investigator", "turn"] = "turn" if _REPEATING.search(sentence) else "investigator"
        candidates: list[tuple[Kind, tuple[tuple[str, str], ...], tuple[int, int]]] = []
        if san := _SAN.search(sentence):
            candidates.append(("sanity_check", (("loss_success", _dice(san["ok"])), ("loss_failure", _dice(san["bad"]))), san.span()))
        triggered = bool(_TRIGGER.search(sentence)) and not _COMBAT.search(sentence)
        if triggered and (damage := _DAMAGE.search(sentence)):
            candidates.append(("damage", (("expression", _dice(damage["amount"])),), damage.span()))
        if triggered and not san and (check := _CHECK.search(sentence)):
            skill = (check["skill"] or check["skill_en"] or "").strip()
            level = _LEVELS.get((check["level"] or check["level_en"] or "").lower(), "regular")
            if skill and skill not in _SKILL_REFUSED:
                candidates.append(("forced_check", (("skill", skill), ("difficulty", level)), check.span()))
        for kind, args, span in candidates:
            anchors = _anchors(sentence, span)
            key = _key(kind, sentence)
            if anchors and key not in seen:
                seen.add(key)
                found.append(Obligation(kind, key, sentence[:400], args, anchors, repeating))
    return found


def triggered(obligation: Obligation, narration: str) -> bool:
    """True when the narration shows what the rule is triggered by.

    Every anchor must appear when the rule names one or two things; with more, two thirds of them. Narrating
    one of two named things (the bucket without what is in it) does not reveal the event.
    """
    text = narration.lower()
    present = sum(anchor in text for anchor in obligation.anchors)
    needed = len(obligation.anchors) if len(obligation.anchors) <= 2 else math.ceil(len(obligation.anchors) * 2 / 3)
    return present >= needed


def identity(timeline_id: str, character_id: str, obligation: Obligation, turn_id: str) -> str:
    """Stable id of one application: per investigator and rule, or per turn for a rule that repeats."""
    scope = turn_id if obligation.once_per == "turn" else ""
    return "event-obligation:" + hashlib.sha256(
        f"{timeline_id}|{character_id}|{obligation.kind}|{obligation.key}|{scope}".encode()
    ).hexdigest()[:24]
