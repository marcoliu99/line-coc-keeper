"""Investigator (character) model and quick-generation for COC 7th Edition."""
from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict
from typing import Any

# Standard COC7e base skill percentages (subset covering the common cases).
# Dodge and "Language (Own)" are computed per-investigator, not listed here.
BASE_SKILLS: dict[str, int] = {
    "會計": 5, "人類學": 1, "估價": 5, "考古學": 1, "魅惑": 15, "攀爬": 20,
    "信用評級": 0, "克蘇魯神話": 0, "偽裝": 5, "汽車駕駛": 20, "電器維修": 10,
    "話術": 5, "格鬥（鬥毆）": 25, "射擊（手槍）": 20, "射擊（步槍/霰彈槍）": 25,
    "急救": 30, "歷史": 5, "恐嚇": 15, "跳躍": 20, "外語（其他）": 1, "法律": 5,
    "圖書館使用": 20, "聆聽": 20, "開鎖": 1, "機械維修": 10, "醫學": 1,
    "自然學": 10, "領航": 10, "神秘學": 5, "重機械操作": 1, "說服": 10,
    "駕駛（其他載具）": 1, "心理學": 10, "精神分析": 1, "騎術": 5, "巧手": 10,
    "偵查": 25, "潛行": 20, "生存": 10, "游泳": 20, "投擲": 20, "追蹤": 10,
    "電腦使用": 5, "科學（生物）": 1, "科學（化學）": 1, "科學（物理）": 1,
}

OCCUPATIONS: dict[str, dict[str, int]] = {
    "記者": {
        "圖書館使用": 70, "說服": 60, "心理學": 50, "話術": 60,
        "偵查": 50, "歷史": 40, "汽車駕駛": 40,
    },
    "私家偵探": {
        "偵查": 70, "圖書館使用": 60, "心理學": 60, "潛行": 50,
        "法律": 40, "射擊（手槍）": 50, "說服": 50, "話術": 50,
    },
    "醫生": {
        "醫學": 70, "急救": 80, "心理學": 50, "說服": 40,
        "科學（生物）": 50, "信用評級": 60, "偵查": 40,
    },
    "教授": {
        "圖書館使用": 80, "歷史": 60, "心理學": 50, "神秘學": 40,
        "說服": 50, "偵查": 40, "信用評級": 50,
    },
    "警察": {
        "射擊（手槍）": 60, "格鬥（鬥毆）": 60, "偵查": 60, "法律": 50,
        "心理學": 40, "汽車駕駛": 50, "急救": 40, "恐嚇": 50,
    },
    "骨董商": {
        "歷史": 60, "估價": 60, "圖書館使用": 60, "偵查": 50,
        "說服": 40, "神秘學": 40, "信用評級": 50,
    },
    "神職人員": {
        "說服": 60, "心理學": 50, "圖書館使用": 50, "話術": 40,
        "神秘學": 30, "急救": 30, "信用評級": 40,
    },
    "流浪漢": {
        "潛行": 50, "偵查": 50, "話術": 50, "巧手": 40,
        "生存": 50, "格鬥（鬥毆）": 40, "開鎖": 40,
    },
    "藝術家": {
        "藝術／工藝（繪畫）": 60, "魅惑": 50, "心理學": 40, "圖書館使用": 40,
        "歷史": 40, "估價": 40, "話術": 40,
    },
    "海洋生物學家": {
        "科學（生物）": 70, "游泳": 60, "自然學": 60, "圖書館使用": 60,
        "科學（化學）": 40, "急救": 40, "電腦使用": 40, "領航": 40,
    },
    "FBI探員": {
        "射擊（手槍）": 60, "法律": 50, "心理學": 50, "偵查": 60,
        "格鬥（鬥毆）": 50, "汽車駕駛": 50, "說服": 40, "恐嚇": 40,
    },
}


def _roll(expr_dice: int, expr_sides: int, mult: int = 1) -> int:
    return sum(random.randint(1, expr_sides) for _ in range(expr_dice)) * mult


@dataclass
class Character:
    name: str
    owner_id: str
    occupation: str = "自由人"

    str_: int = 50
    con: int = 50
    siz: int = 50
    dex: int = 50
    app: int = 50
    int_: int = 50
    pow_: int = 50
    edu: int = 50
    luck: int = 50

    hp: int = 10
    hp_max: int = 10
    mp: int = 10
    mp_max: int = 10
    san: int = 50
    san_max: int = 99
    move: int = 8
    damage_bonus: str = "0"
    build: int = 0

    skills: dict[str, int] = field(default_factory=dict)
    notes: str = ""
    key_connection: str = ""  # "關鍵背景連結★" — a person/place/object this character
    # cannot lose without a saving roll first (see keeper.py's static prompt);
    # player-facing, unlike secret_goal. Set via /coc setconnection.
    secret_goal: str = ""  # personal hook/motivation — Keeper-only, see keeper_notes_text()
    status_tags: list[str] = field(default_factory=list)  # e.g. ["昏迷", "瀕死"]
    away: bool = False  # player stepped out — combat.py auto-skips their turn

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Character":
        return Character(**data)

    def sheet_text(self) -> str:
        # Player-facing only — never include secret_goal here. This is what
        # /coc sheet and /coc pc's completion message show, both of which
        # reply publicly to the whole conversation; see keeper_notes_text()
        # for the Keeper-only counterpart.
        lines = [
            f"【{self.name}】職業：{self.occupation}（玩家：{self.owner_id}）",
            f"STR {self.str_} CON {self.con} SIZ {self.siz} DEX {self.dex} "
            f"APP {self.app} INT {self.int_} POW {self.pow_} EDU {self.edu} LUCK {self.luck}",
            f"HP {self.hp}/{self.hp_max}　MP {self.mp}/{self.mp_max}　SAN {self.san}/{self.san_max}　"
            f"MOV {self.move}　DB {self.damage_bonus}　Build {self.build}",
        ]
        tags = list(self.status_tags)
        if self.away:
            tags.append("暫離")
        if tags:
            lines.append("狀態：" + "、".join(tags))
        if self.key_connection:
            lines.append(f"★ 關鍵背景連結：{self.key_connection}")
        top_skills = sorted(self.skills.items(), key=lambda kv: -kv[1])[:12]
        if top_skills:
            lines.append("主要技能：" + "、".join(f"{k} {v}%" for k, v in top_skills))
        return "\n".join(lines)

    def keeper_notes_text(self) -> str:
        """Extra context for the Keeper's own prompt only (see
        app/keeper.py:_build_dynamic_prompt) — never rendered anywhere a player
        would see it. Currently just the secret goal, sent to the player
        privately once when the character is created (see app/commands.py) and
        repeated here so the Keeper can keep nudging toward it narratively
        without re-exposing it in a public reply."""
        if not self.secret_goal:
            return ""
        return f"（{self.name} 的秘密目標，只有你知道，不要在公開回覆裡洩漏：{self.secret_goal}）"


def damage_bonus_and_build(str_: int, siz: int) -> tuple[str, int]:
    total = str_ + siz
    if total <= 64:
        return "-2", -2
    if total <= 84:
        return "-1", -1
    if total <= 124:
        return "0", 0
    if total <= 164:
        return "+1d4", 1
    if total <= 204:
        return "+1d6", 2
    # roughly +1d6 per additional 80 points beyond 204
    extra_steps = (total - 205) // 80 + 2
    return f"+{extra_steps}d6", extra_steps + 1


def move_rate(str_: int, dex: int, siz: int) -> int:
    if dex < siz and str_ < siz:
        return 7
    if dex > siz and str_ > siz:
        return 9
    return 8


def generate_investigator(
    name: str,
    owner_id: str,
    occupation: str | None = None,
    occupation_skills: dict[str, int] | None = None,
    secret_goal: str = "",
) -> Character:
    """Quick-generate a rolled COC7e investigator (classic 3d6 method).

    `occupation_skills`, when given, overrides the built-in OCCUPATIONS lookup —
    used by /coc pc to pull the skill bonus from a scenario's own extracted
    pregen (see app/commands.py) instead of the generic occupation list, so a
    quick-gen character built with e.g. "海洋生物學家" from a specific scenario
    actually gets that scenario's real skill values rather than a made-up label
    with no bonus at all.
    """
    str_ = _roll(3, 6, 5)
    con = _roll(3, 6, 5)
    dex = _roll(3, 6, 5)
    app = _roll(3, 6, 5)
    pow_ = _roll(3, 6, 5)
    siz = _roll(2, 6, 5) + 30
    int_ = _roll(2, 6, 5) + 30
    edu = _roll(2, 6, 5) + 30
    luck = _roll(3, 6, 5)

    hp_max = (con + siz) // 10
    mp_max = pow_ // 5
    san_max = 99
    san = min(pow_, san_max)
    db, build = damage_bonus_and_build(str_, siz)
    move = move_rate(str_, dex, siz)

    skills = dict(BASE_SKILLS)
    skills["閃避"] = dex // 2
    skills["母語"] = edu

    if occupation_skills:
        for skill, value in occupation_skills.items():
            if isinstance(value, (int, float)):
                skills[skill] = max(skills.get(skill, 0), int(value))
        occ_label = occupation or "自由人"
    else:
        occ_key = occupation if occupation in OCCUPATIONS else None
        if occ_key:
            for skill, value in OCCUPATIONS[occ_key].items():
                skills[skill] = max(skills.get(skill, 0), value)
        occ_label = occ_key or (occupation or "自由人")

    return Character(
        name=name,
        owner_id=owner_id,
        occupation=occ_label,
        str_=str_, con=con, siz=siz, dex=dex, app=app, int_=int_, pow_=pow_, edu=edu, luck=luck,
        hp=hp_max, hp_max=hp_max,
        mp=mp_max, mp_max=mp_max,
        san=san, san_max=san_max,
        move=move, damage_bonus=db, build=build,
        skills=skills,
        secret_goal=secret_goal,
    )


@dataclass
class CreationSession:
    """In-progress interactive character creation (rolled attributes, unspent
    occupation/interest skill point pools). One per owner_id, held on GroupState
    until finalized into a real Character."""

    name: str
    owner_id: str
    occupation: str = "自由人"

    str_: int = 0
    con: int = 0
    siz: int = 0
    dex: int = 0
    app: int = 0
    int_: int = 0
    pow_: int = 0
    edu: int = 0
    luck: int = 0

    occ_points_total: int = 0
    occ_points_remaining: int = 0
    interest_points_total: int = 0
    interest_points_remaining: int = 0

    skills: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CreationSession":
        return CreationSession(**data)


@dataclass
class Combatant:
    """One participant in an active combat's initiative order."""

    name: str
    dex: int
    hp: int
    hp_max: int
    is_pc: bool = False
    is_ally: bool = False  # Keeper-run NPC fighting on the investigators' side
    # (a hired guide, a friendly cultist defector, ...) — distinct from is_pc since
    # it has no Character to sync HP back to, but shares "our side" in status_text.
    defeated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Combatant":
        return Combatant(**data)


@dataclass
class CombatState:
    """Formal initiative-order combat tracker for a group. Kept separate from the
    freeform narrative loop so turn order and HP are enforced by code, not left to
    the Keeper's own judgement."""

    active: bool = False
    round_number: int = 0
    order: list[Combatant] = field(default_factory=list)
    current_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "round_number": self.round_number,
            "order": [c.to_dict() for c in self.order],
            "current_index": self.current_index,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CombatState":
        return CombatState(
            active=data.get("active", False),
            round_number=data.get("round_number", 0),
            order=[Combatant.from_dict(c) for c in data.get("order", [])],
            current_index=data.get("current_index", 0),
        )


@dataclass
class GroupState:
    group_id: str
    scenario_title: str = ""
    scenario_text: str = ""
    active: bool = False
    characters: dict[str, Character] = field(default_factory=dict)  # keyed by owner_id
    log: list[dict[str, str]] = field(default_factory=list)  # [{"role": ..., "content": ...}]
    creation_sessions: dict[str, CreationSession] = field(default_factory=dict)  # keyed by owner_id
    pregens: list[dict[str, Any]] = field(default_factory=list)  # extracted from scenario PDF, cached
    combat: CombatState = field(default_factory=CombatState)

    # Map/Scene Engine (see app/scene_map.py) — keyed by page number as a
    # string (JSON object keys are always strings, so this avoids an int/str
    # round-trip mismatch between what's written and what's read back).
    scene_maps: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Per-character position — each entry keyed by owner_id, independent of
    # every other character. Deliberately NOT a single shared "party
    # location": scenarios vary in how (and whether) they split investigators
    # up, so instead of assuming any fixed squad structure, each character
    # just tracks their own map page/room/facing, and a "group" is whatever
    # set of characters happens to share the same (page, room) right now —
    # nothing has to be declared in advance. A character with no entry in
    # current_room_id hasn't entered any tracked map yet.
    current_map_page: dict[str, str] = field(default_factory=dict)  # owner_id -> page key
    current_room_id: dict[str, str] = field(default_factory=dict)  # owner_id -> room id
    party_facing: dict[str, str] = field(default_factory=dict)  # owner_id -> compass, default "N" when absent

    # A check the Keeper asked for but hasn't been rolled yet — keyed by
    # owner_id, cleared once /coc check resolves it. See app/keeper.py's
    # skill_check/sanity_check tools (they register one of these instead of
    # rolling) and app/commands.py's _handle_check_command (the player rolls).
    # Shape: {"type": "skill", "skill": str, "skill_value": int, "bonus_dice":
    # int, "penalty_dice": int} or {"type": "sanity", "loss_success": str,
    # "loss_failure": str}.
    pending_checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get_character_by_name(self, name: str) -> Character | None:
        for c in self.characters.values():
            if c.name == name:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "scenario_title": self.scenario_title,
            "scenario_text": self.scenario_text,
            "active": self.active,
            "characters": {k: v.to_dict() for k, v in self.characters.items()},
            "log": self.log,
            "creation_sessions": {k: v.to_dict() for k, v in self.creation_sessions.items()},
            "pregens": self.pregens,
            "combat": self.combat.to_dict(),
            "scene_maps": self.scene_maps,
            "current_map_page": self.current_map_page,
            "current_room_id": self.current_room_id,
            "party_facing": self.party_facing,
            "pending_checks": self.pending_checks,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GroupState":
        return GroupState(
            group_id=data["group_id"],
            scenario_title=data.get("scenario_title", ""),
            scenario_text=data.get("scenario_text", ""),
            active=data.get("active", False),
            characters={k: Character.from_dict(v) for k, v in data.get("characters", {}).items()},
            log=data.get("log", []),
            creation_sessions={
                k: CreationSession.from_dict(v) for k, v in data.get("creation_sessions", {}).items()
            },
            pregens=data.get("pregens", []),
            combat=CombatState.from_dict(data.get("combat", {})) if data.get("combat") else CombatState(),
            scene_maps=data.get("scene_maps", {}),
            # .get(..., {}) with an isinstance check rather than a bare .get
            # default: a save from before this became per-character tracking
            # left these as plain strings ("" / "N"), which would otherwise
            # silently poison these dicts on load — treat that old shape as
            # "no per-character data yet" instead of crashing or propagating it.
            current_map_page=data["current_map_page"] if isinstance(data.get("current_map_page"), dict) else {},
            current_room_id=data["current_room_id"] if isinstance(data.get("current_room_id"), dict) else {},
            party_facing=data["party_facing"] if isinstance(data.get("party_facing"), dict) else {},
            pending_checks=data.get("pending_checks", {}),
        )
