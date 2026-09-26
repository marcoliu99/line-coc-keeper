"""Investigator (character) model and quick-generation for COC 7th Edition."""
from __future__ import annotations

import dataclasses
import random
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app import spoiler_policy

# Standard COC7e base skill percentages (subset covering the common cases).
# Dodge and "Language (Own)" are computed per-investigator, not listed here.
# Chinese skill names follow the official terminology table the project
# owner supplied (2026-09-17) — some of these differ from earlier
# ad-hoc translations this project used before that table existed:
# Appraise 估價->鑑定, Fast Talk 話術->快速交談, Navigate 領航->導航,
# Sleight of Hand 巧手->妙手, Pilot 駕駛（其他載具）->駕駛,
# Electrical Repair 電器維修->電氣維修, Language (Other) 外語（其他）->其他語言,
# Operate Heavy Machinery 重機械操作->重型機械操作. Deliberately NOT renamed:
# Fighting (Brawl) stays 格鬥（鬥毆）rather than the table's plain 格鬥, and
# Firearms/Science stay split into their (手槍)/(步槍/霰彈槍) and
# (生物)/(化學)/(物理) variants rather than merged into one bare skill —
# those splits track a real COC7e 7th-edition mechanical distinction (each
# is genuinely a separate skill with its own value), not just a wording
# choice, so collapsing them would be a rules change, not a terminology fix.
# See app/skill_aliases.py for reverse-alias entries keeping the old
# spellings resolvable, and scripts/migrate_skill_names.py for renaming
# these keys in already-persisted characters/pregens once this deploys.
BASE_SKILLS: dict[str, int] = {
    "會計": 5, "人類學": 1, "鑑定": 5, "考古學": 1, "魅惑": 15, "攀爬": 20,
    "信用評級": 0, "克蘇魯神話": 0, "偽裝": 5, "汽車駕駛": 20, "電氣維修": 10,
    "快速交談": 5, "格鬥（鬥毆）": 25, "射擊（手槍）": 20, "射擊（步槍/霰彈槍）": 25,
    "急救": 30, "歷史": 5, "恐嚇": 15, "跳躍": 20, "其他語言": 1, "法律": 5,
    "圖書館使用": 20, "聆聽": 20, "開鎖": 1, "機械維修": 10, "醫學": 1,
    "自然學": 10, "導航": 10, "神秘學": 5, "重型機械操作": 1, "說服": 10,
    "駕駛": 1, "心理學": 10, "精神分析": 1, "騎術": 5, "妙手": 10,
    "偵查": 25, "潛行": 20, "生存": 10, "游泳": 20, "投擲": 20, "追蹤": 10,
    "電腦使用": 5, "科學（生物）": 1, "科學（化學）": 1, "科學（物理）": 1,
}

OCCUPATIONS: dict[str, dict[str, int]] = {
    "記者": {
        "圖書館使用": 70, "說服": 60, "心理學": 50, "快速交談": 60,
        "偵查": 50, "歷史": 40, "汽車駕駛": 40,
    },
    "私家偵探": {
        "偵查": 70, "圖書館使用": 60, "心理學": 60, "潛行": 50,
        "法律": 40, "射擊（手槍）": 50, "說服": 50, "快速交談": 50,
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
        "歷史": 60, "鑑定": 60, "圖書館使用": 60, "偵查": 50,
        "說服": 40, "神秘學": 40, "信用評級": 50,
    },
    "神職人員": {
        "說服": 60, "心理學": 50, "圖書館使用": 50, "快速交談": 40,
        "神秘學": 30, "急救": 30, "信用評級": 40,
    },
    "流浪漢": {
        "潛行": 50, "偵查": 50, "快速交談": 50, "妙手": 40,
        "生存": 50, "格鬥（鬥毆）": 40, "開鎖": 40,
    },
    "藝術家": {
        "藝術／工藝（繪畫）": 60, "魅惑": 50, "心理學": 40, "圖書館使用": 40,
        "歷史": 40, "鑑定": 40, "快速交談": 40,
    },
    "海洋生物學家": {
        "科學（生物）": 70, "游泳": 60, "自然學": 60, "圖書館使用": 60,
        "科學（化學）": 40, "急救": 40, "電腦使用": 40, "導航": 40,
    },
    "FBI探員": {
        "射擊（手槍）": 60, "法律": 50, "心理學": 50, "偵查": 60,
        "格鬥（鬥毆）": 50, "汽車駕駛": 50, "說服": 40, "恐嚇": 40,
    },
}

# Default starting carried items per built-in occupation — see
# docs/references/carry_audit.md's "建角時的靜態審查" section: rather than
# reviewing a player's own equipment claims at creation time (there's nothing
# to review yet; /coc pc and /coc create never asked about gear before this),
# these are pre-authored to already pass the four-point reasonableness check
# for their occupation (era-appropriate 1920s items, obviously justified by
# the job itself, nothing requiring a credit-rating judgment call). Kept to
# flavor/utility items only, deliberately no firearms — a default sidearm
# would need ammo-tracking data (see Character.weapons) this dict has no way
# to supply, and an occupation that plausibly carries one (警察, FBI探員) can
# still add it during play via the dynamic carry-audit rules in
# app/keeper.py's system prompt, same as any other claimed item. Only looked
# up for an occupation that exactly matches one of these keys — a custom
# free-text occupation (str for /coc create's occupation field isn't
# restricted to this dict) simply gets no default items, same as it already
# gets no default skill bonus.
OCCUPATION_EQUIPMENT: dict[str, list[str]] = {
    "記者": ["記者證", "筆記本與鋼筆", "口袋型相機"],
    "私家偵探": ["偵探執照", "放大鏡", "隨身筆記本"],
    "醫生": ["醫師執業證", "醫藥包"],
    "教授": ["教職員證", "鋼筆與筆記本", "懷錶"],
    "警察": ["警徽與委任證", "警棍", "手銬"],
    "骨董商": ["古董商執照", "放大鏡", "鑑定用手套"],
    "神職人員": ["聖職證明", "隨身聖經（或對應信仰經典）"],
    "流浪漢": ["破舊背包", "打火石"],
    "藝術家": ["素描本與炭筆", "小型畫具箱"],
    "海洋生物學家": ["研究機構識別證", "野外筆記本", "採樣瓶"],
    "FBI探員": ["聯邦探員證件", "手銬"],
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
    # Ammo-tracked firearms only — name -> {"ammo": current, "ammo_max": capacity}.
    # Melee/thrown weapons have nothing to track and aren't listed here; the
    # full weapon writeup (damage, range, malfunction number, ...) still lives
    # as descriptive text in `notes`, this is only the "current game state"
    # number that actually changes turn to turn — see app/keeper.py's
    # adjust_ammo tool and sheet_text() below.
    weapons: dict[str, dict[str, int]] = field(default_factory=dict)
    # Free-form carried items worth tracking explicitly (a found letter, a
    # key, a map — anything picked up/used/lost during play) so the Keeper
    # has an authoritative list instead of having to remember or infer what
    # this character is holding. Not a full inventory/carry-weight system —
    # see docs/references/carry_audit.md for that (still unimplemented);
    # this is just "what's the current list", added/removed via keeper.py's
    # add_carried_item/remove_carried_item tools.
    carried_items: list[str] = field(default_factory=list)
    notes: str = ""
    key_connection: str = ""  # "關鍵背景連結★" — a person/place/object this character
    # cannot lose without a saving roll first (see keeper.py's static prompt);
    # player-facing, unlike secret_goal. Set via /coc setconnection.
    secret_goal: str = ""  # personal hook/motivation — Keeper-only, see keeper_notes_text()
    extra_fields: dict[str, Any] = field(default_factory=dict)
    status_tags: list[str] = field(default_factory=list)  # e.g. ["昏迷", "瀕死"]
    away: bool = False  # player stepped out — combat.py auto-skips their turn
    character_id: str = ""
    slot: str = "primary"
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        if not self.character_id:
            self.character_id = f"legacy-user:{self.owner_id}"
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Character:
        char = Character(**data)
        if not char.character_id:
            char.character_id = f"legacy-user:{char.owner_id}"
        return char

    def sheet_text(self) -> str:
        # Player-facing only — never include secret_goal here. This is what
        # /coc sheet and /coc pc's completion message show, both of which
        # reply publicly to the whole conversation; see keeper_notes_text()
        # for the Keeper-only counterpart.
        lines = [
            f"【{self.name}】職業：{self.occupation}（玩家：{self.owner_id}）",
            f"STR {self.str_} CON {self.con} SIZ {self.siz} DEX {self.dex} APP {self.app} INT {self.int_} POW {self.pow_} EDU {self.edu} LUCK {self.luck}",
            f"HP {self.hp}/{self.hp_max}　MP {self.mp}/{self.mp_max}　SAN {self.san}/{self.san_max}　MOV {self.move}　DB {self.damage_bonus}　Build {self.build}",
        ]
        tags = list(self.status_tags)
        if self.away:
            tags.append("暫離")
        if tags:
            lines.append("狀態：" + "、".join(tags))
        if self.weapons:
            lines.append("彈藥：" + "、".join(
                f"{name} {w['ammo']}/{w['ammo_max']}" if "ammo_max" in w else name
                for name, w in self.weapons.items()
            ))
        if self.carried_items:
            lines.append("攜帶物品：" + "、".join(self.carried_items))
        if self.key_connection:
            lines.append(f"★ 關鍵背景連結：{self.key_connection}")
        for key, value in self.extra_fields.items():
            if value not in (None, "", [], {}):
                lines.append(f"{key}：{value}")
        top_skills = sorted(self.skills.items(), key=lambda kv: -kv[1])[:12]
        if top_skills:
            lines.append("主要技能：" + "、".join(f"{k} {v}%" for k, v in top_skills))
        if self.secret_goal and not spoiler_policy.is_privacy_isolation_enabled():
            # §3.4 mechanism #2: with privacy isolation off, the secret goal
            # is allowed to surface in this player-facing formatter too.
            lines.append(f"（隱私隔離已關閉）秘密目標：{self.secret_goal}")
        return "\n".join(lines)

    def static_sheet_text(self) -> str:
        """The part of the sheet that almost never changes turn to turn
        (attributes, occupation, skills) — see app/keeper.py's
        _build_static_prompt, which puts this in the *cached* system-prompt
        block. dynamic_state_text() below is the counterpart: just the
        handful of numbers that actually change, resent fresh every turn."""
        lines = [
            f"【{self.name}】職業：{self.occupation}（玩家：{self.owner_id}）",
            f"STR {self.str_} CON {self.con} SIZ {self.siz} DEX {self.dex} APP {self.app} INT {self.int_} POW {self.pow_} EDU {self.edu}",
            f"MOV {self.move}　DB {self.damage_bonus}　Build {self.build}",
        ]
        if self.key_connection:
            lines.append(f"★ 關鍵背景連結：{self.key_connection}")
        for key, value in self.extra_fields.items():
            if value not in (None, "", [], {}):
                lines.append(f"{key}：{value}")
        top_skills = sorted(self.skills.items(), key=lambda kv: -kv[1])[:12]
        if top_skills:
            lines.append("主要技能：" + "、".join(f"{k} {v}%" for k, v in top_skills))
        return "\n".join(lines)

    def dynamic_state_text(self) -> str:
        """Just the numbers/lists that actually change during play (HP/MP/
        SAN/Luck, ammo, carried items, status) — one line, keyed by name AND
        owner_id so there's no ambiguity about whose state this is even
        with several characters listed back to back. Pairs with
        static_sheet_text() above."""
        parts = [
            f"HP {self.hp}/{self.hp_max}", f"MP {self.mp}/{self.mp_max}",
            f"SAN {self.san}/{self.san_max}", f"LUCK {self.luck}",
        ]
        if self.weapons:
            parts.append("彈藥 " + "、".join(
                f"{name} {w['ammo']}/{w['ammo_max']}" if "ammo_max" in w else name
                for name, w in self.weapons.items()
            ))
        if self.carried_items:
            parts.append("攜帶物品 " + "、".join(self.carried_items))
        tags = list(self.status_tags)
        if self.away:
            tags.append("暫離")
        if tags:
            parts.append("狀態 " + "、".join(tags))
        return f"{self.name}（user_id {self.owner_id}）：" + "　".join(parts)

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

    carried_items: list[str] = []
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
            carried_items = list(OCCUPATION_EQUIPMENT.get(occ_key, []))
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
        carried_items=carried_items,
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
    def from_dict(data: dict[str, Any]) -> CreationSession:
        return CreationSession(**data)


def _generate_sub_id() -> str:
    """Fallback id for an armor/attack/ability entry the LLM didn't (or,
    after _known_fields_only drops an unrecognized key, effectively
    didn't) give an id — see _known_fields_only's docstring."""
    return uuid.uuid4().hex[:8]


def _known_fields_only(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Drops any key not in cls's dataclass fields before constructing it.

    The LLM is the source of armor/attacks/abilities entries for enemy
    combat cards (see app/keeper.py's add_npc_to_combat tool), and a
    plain **data unpack into a dataclass raises TypeError on any
    unexpected key — a real production incident hit this via an armor
    entry using `name` instead of `label` (see docs/specs/bug-add-npc-
    to-combat-armor-schema-crash.md). A model guessing a plausible-but-
    wrong key shouldn't crash the whole tool call; better field-name
    documentation reduces how often this happens (see that same tool's
    description), but this is the backstop for whenever it doesn't.
    `id`/`label`/`name` also have safe defaults on these dataclasses now
    (see each one below) so dropping a key here never trades one crash
    (unexpected keyword) for another (missing required argument)."""
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def _alias_name_to_label(data: dict[str, Any]) -> dict[str, Any]:
    """ArmorRule/AttackRule's display-name field is `label`, not `name` —
    but `name` is a very plausible guess (it's the real field name on
    SpecialAbility, and on add_npc_to_combat's own top-level `name`
    parameter), and is exactly the mistake the real production incident
    this file's docstrings reference made. Recovers the model's actual
    intent instead of just silently dropping it to an empty label."""
    if "label" not in data and "name" in data:
        data = {**data, "label": data["name"]}
    return data


@dataclass
class ArmorRule:
    id: str = field(default_factory=_generate_sub_id)
    label: str = ""
    value: int = 0
    applies_to: str = "all"
    bypass_tags: list[str] = field(default_factory=list)
    public_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ArmorRule:
        return ArmorRule(**_known_fields_only(ArmorRule, _alias_name_to_label(data)))


@dataclass
class AttackRule:
    id: str = field(default_factory=_generate_sub_id)
    label: str = ""
    skill_name: str = "格鬥（鬥毆）"
    skill_value: int = 25
    damage: str = "1D3"
    range_band: str = "engaged"
    max_targets: int = 1
    tags: list[str] = field(default_factory=list)
    ammo_or_uses: int | None = None
    public_description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> AttackRule:
        return AttackRule(**_known_fields_only(AttackRule, _alias_name_to_label(data)))


@dataclass
class SpecialAbility:
    id: str = field(default_factory=_generate_sub_id)
    name: str = ""
    priority: int = 0
    trigger: dict[str, Any] = field(default_factory=dict)
    check: dict[str, Any] = field(default_factory=dict)
    effect: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    cooldown_rounds: int = 0
    current_cooldown: int = 0
    reveal_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SpecialAbility:
        cleaned = dict(_known_fields_only(SpecialAbility, data))
        # trigger/check/effect/usage/reveal_policy are read everywhere as
        # (ability.X or {}).get(...) expecting a dict with specific known
        # sub-keys (see app/combat.py) -- a non-dict value (a plausible
        # LLM mistake, e.g. a plain description string) could never
        # satisfy any of those lookups even if kept, so it's coerced away
        # to {} here -- the same fallback already used when the model
        # omits the field entirely -- instead of constructing successfully
        # and crashing the first time consuming code calls .get() on it.
        for dict_field in ("trigger", "check", "effect", "usage", "reveal_policy"):
            if dict_field in cleaned and not isinstance(cleaned[dict_field], dict):
                cleaned[dict_field] = {}
        return SpecialAbility(**cleaned)


@dataclass
class EffectState:
    id: str = field(default_factory=_generate_sub_id)
    label: str = ""
    source_id: str = ""
    target_id: str = ""
    timing: str = "turn_start"
    remaining_rounds: int | None = None
    damage: str = ""
    damage_type: str = "physical"
    save_or_check: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    public_description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EffectState:
        # Same _known_fields_only backstop as ArmorRule/AttackRule/
        # SpecialAbility above (see that helper's docstring for the real
        # production incident it exists for) — EffectState is built from
        # add_combat_effect's LLM-controlled input the same way, and is
        # also deserialized from every persisted GroupState on load, so an
        # unrecognized key here would crash every load_state call for any
        # group with a surviving combat effect, not just one tool call.
        return EffectState(**_known_fields_only(EffectState, data))


@dataclass
class EnemyCombatCard:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    hp: int = 10
    hp_max: int = 10
    armor: list[ArmorRule] = field(default_factory=list)
    attacks: list[AttackRule] = field(default_factory=list)
    abilities: list[SpecialAbility] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    status_tags: list[str] = field(default_factory=list)
    effect_states: list[EffectState] = field(default_factory=list)
    hidden_notes: str = ""
    public_description: str = ""
    incomplete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": self.aliases,
            "source": self.source,
            "hp": self.hp,
            "hp_max": self.hp_max,
            "armor": [a.to_dict() for a in self.armor],
            "attacks": [a.to_dict() for a in self.attacks],
            "abilities": [a.to_dict() for a in self.abilities],
            "stats": self.stats,
            "skills": self.skills,
            "status_tags": self.status_tags,
            "effect_states": [e.to_dict() for e in self.effect_states],
            "hidden_notes": self.hidden_notes,
            "public_description": self.public_description,
            "incomplete": self.incomplete,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EnemyCombatCard:
        return EnemyCombatCard(
            id=data["id"],
            name=data.get("name", data["id"]),
            aliases=data.get("aliases", []),
            source=data.get("source", {}),
            hp=data.get("hp", data.get("hp_max", 10)),
            hp_max=data.get("hp_max", max(1, data.get("hp", 10))),
            armor=[ArmorRule.from_dict(a) for a in data.get("armor", [])],
            attacks=[AttackRule.from_dict(a) for a in data.get("attacks", [])],
            abilities=[SpecialAbility.from_dict(a) for a in data.get("abilities", [])],
            stats=data.get("stats", {}),
            skills=data.get("skills", {}),
            status_tags=data.get("status_tags", []),
            effect_states=[EffectState.from_dict(e) for e in data.get("effect_states", [])],
            hidden_notes=data.get("hidden_notes", ""),
            public_description=data.get("public_description", ""),
            incomplete=data.get("incomplete", False),
        )


@dataclass
class Combatant:
    """One participant in an active combat's initiative order."""

    name: str = ""
    dex: int = 0
    hp: int = 0
    hp_max: int = 0
    is_pc: bool = False
    is_ally: bool = False  # Keeper-run NPC fighting on the investigators' side
    # (a hired guide, a friendly cultist defector, ...) — distinct from is_pc since
    # it has no Character to sync HP back to, but shares "our side" in status_text.
    defeated: bool = False
    combatant_id: str = ""
    display_name: str = ""
    side: str = ""
    character_id: str = ""
    enemy_card_id: str = ""

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name
        if not self.side:
            self.side = "pc" if self.is_pc else "ally" if self.is_ally else "enemy"
        if not self.combatant_id:
            prefix = "pc" if self.is_pc else "ally" if self.is_ally else "enemy"
            self.combatant_id = f"{prefix}:{self.character_id or self.enemy_card_id or self.name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Combatant:
        # Same _known_fields_only backstop as ArmorRule/AttackRule/
        # SpecialAbility/EffectState above — Combatant is deserialized from
        # every persisted GroupState with an active/recent combat on load,
        # so an unrecognized key (a future field rename, a stray key from
        # some other code path) would otherwise crash load_state entirely
        # for that group, not just fail one tool call.
        return Combatant(**_known_fields_only(Combatant, data))


@dataclass
class CombatState:
    """Formal initiative-order combat tracker for a group. Kept separate from the
    freeform narrative loop so turn order and HP are enforced by code, not left to
    the Keeper's own judgement."""

    active: bool = False
    round_number: int = 0
    order: list[Combatant] = field(default_factory=list)
    current_index: int = 0
    enemy_cards: dict[str, EnemyCombatCard] = field(default_factory=dict)
    effects: list[EffectState] = field(default_factory=list)
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed_timings: list[str] = field(default_factory=list)
    range_bands: dict[str, str] = field(default_factory=dict)

    def retire_character(
        self,
        character_id: str,
        character_name: str | None = None,
        *,
        state: Any | None = None,
    ) -> None:
        """Remove a retired investigator from the live initiative state.

        Retiring a character removes its player binding, but the durable
        character history is intentionally kept in ``characters_by_id``.  A
        combat order is live state rather than history, so it must not retain
        a PC that can no longer take a turn or be selected as an enemy target.
        Older snapshots may have PC combatants without ``character_id``; when
        that happens the exact name is the only available identity, so all
        matching legacy PC entries are removed conservatively rather than
        allowing a possibly retired character to remain actionable.
        The next eligible combatant becomes current when the retired PC was
        the current turn; the state-aware caller also applies the new turn's
        timing effects and skips away/defeated combatants.  The optional state
        argument keeps this model-level cleanup usable for legacy callers that
        only have a CombatState snapshot.
        """
        if not self.order:
            return

        removed = [
            combatant
            for combatant in self.order
            if combatant.is_pc and combatant.character_id == character_id
        ]
        if not removed and character_name:
            removed = [
                combatant
                for combatant in self.order
                if combatant.is_pc
                and not combatant.character_id
                and combatant.name == character_name
            ]
        if not removed:
            return

        removed_ids = {combatant.combatant_id for combatant in removed}
        current_index = self.current_index
        current = self.order[current_index] if 0 <= current_index < len(self.order) else None
        current_was_removed = current is not None and current.combatant_id in removed_ids
        current_id = current.combatant_id if current is not None else ""
        old_order = list(self.order)
        self.order = [combatant for combatant in old_order if combatant.combatant_id not in removed_ids]

        # An unresolved enemy plan/effect must not retain a retired PC as a
        # target.  Other combatants' plans remain valid.
        self.plans = {
            plan_id: plan
            for plan_id, plan in self.plans.items()
            if plan.get("enemy_combatant_id") not in removed_ids
            and not any(target_id in removed_ids for target_id in (plan.get("target_ids") or []))
        }
        self.effects = [effect for effect in self.effects if effect.target_id not in removed_ids]
        self.range_bands = {
            key: value
            for key, value in self.range_bands.items()
            if not any(
                key == target_id
                or key.startswith(f"{target_id}:")
                or key.endswith(f":{target_id}")
                for target_id in removed_ids
            )
        }

        if not self.order:
            self.active = False
            self.current_index = 0
            return

        if current_was_removed:
            # A GroupState-aware caller lets the combat module apply timing and
            # skip away/defeated candidates.  Keep the old pure-CombatState
            # behavior as a compatibility fallback for callers that do not
            # have the owning GroupState available.
            assert current is not None
            old_index = current_index
            if state is not None:
                from app import combat as combat_engine

                combat_engine.finish_retired_current_turn(
                    state,
                    old_order=old_order,
                    old_index=old_index,
                    removed_ids=removed_ids,
                )
                return
            for offset in range(1, len(old_order) + 1):
                candidate = old_order[(old_index + offset) % len(old_order)]
                if candidate.combatant_id not in removed_ids:
                    self.current_index = self.order.index(candidate)
                    break
        else:
            surviving_current = next(
                (combatant for combatant in self.order if combatant.combatant_id == current_id),
                self.order[0],
            )
            self.current_index = self.order.index(surviving_current)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "round_number": self.round_number,
            "order": [c.to_dict() for c in self.order],
            "current_index": self.current_index,
            "enemy_cards": {k: v.to_dict() for k, v in self.enemy_cards.items()},
            "effects": [e.to_dict() for e in self.effects],
            "plans": self.plans,
            "processed_timings": self.processed_timings,
            "range_bands": self.range_bands,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CombatState:
        return CombatState(
            active=data.get("active", False),
            round_number=data.get("round_number", 0),
            order=[Combatant.from_dict(c) for c in data.get("order", [])],
            current_index=data.get("current_index", 0),
            enemy_cards={k: EnemyCombatCard.from_dict(v) for k, v in data.get("enemy_cards", {}).items()},
            effects=[EffectState.from_dict(e) for e in data.get("effects", [])],
            plans=data.get("plans", {}),
            processed_timings=list(data.get("processed_timings", [])),
            range_bands=dict(data.get("range_bands", {})),
        )


@dataclass
class GroupState:
    CURRENT_SCHEMA_VERSION = 2

    @classmethod
    def migrate_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize persisted snapshots before deserialization.

        Version 1 is the first explicit schema. Snapshots written before the
        version field existed are treated as v1 because ``from_dict`` already
        supplies the compatible legacy defaults. Keeping this in one place
        gives future schema changes a tested, explicit migration registry.
        """
        migrated = dict(data)
        version = int(migrated.get("schema_version", 1))
        if version > cls.CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported GroupState schema_version={version}; "
                f"current={cls.CURRENT_SCHEMA_VERSION}"
            )
        migrations = {
            # v0 was the short-lived pre-versioned snapshot shape. Its fields
            # are already covered by from_dict's legacy defaults.
            0: lambda snapshot: {**snapshot, "schema_version": 1},
            1: lambda snapshot: {
                **snapshot,
                "schema_version": 2,
                # Existing response chains predate timeline binding.  Keep
                # the id readable for compatibility; callers must only use it
                # after assigning/validating this metadata.
                "openai_previous_response_timeline_id": snapshot.get(
                    "openai_previous_response_timeline_id", ""
                ),
            },
        }
        while version < cls.CURRENT_SCHEMA_VERSION:
            migrate = migrations.get(version)
            if migrate is None:
                raise ValueError(
                    f"No migration registered for GroupState schema_version={version}"
                )
            migrated = migrate(migrated)
            version += 1
            migrated["schema_version"] = version
        migrated.setdefault("schema_version", cls.CURRENT_SCHEMA_VERSION)
        return migrated

    group_id: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    timeline_id: str = ""
    state_revision: int = 0
    scenario_title: str = ""
    scenario_text: str = ""
    scenario_library_id: str = ""
    scenario_variant_id: str = "original"
    active_chapter_id: str = ""
    context_chapter_ids: list[str] = field(default_factory=list)
    active: bool = False
    kp_assistant_user_id: str = ""
    characters: dict[str, Character] = field(default_factory=dict)  # keyed by owner_id
    characters_by_id: dict[str, Character] = field(default_factory=dict)
    active_character_id_by_user: dict[str, str] = field(default_factory=dict)
    # `log` is the canonical in-game history between players and the Keeper:
    # player actions, Keeper narration, rolls, and other public campaign events.
    log: list[dict[str, str]] = field(default_factory=list)  # [{"role": ..., "content": ...}]

    # Separate KP Assistant out-of-character working memory for future private
    # "KP Assistant <-> AI Keeper" coordination. This is deliberately separate
    # from `log`: it is not part of the public player/Keeper game history, and
    # future maintenance should not fold it into campaign_summary or Memory RAG.
    # This step only adds the data structure and serialization compatibility;
    # it does not change Keeper prompts, run_turn, tool permissions, or runtime
    # behavior yet.
    kp_ooc_log: list[dict[str, str]] = field(default_factory=list)
    # Rolling summary of whatever's been trimmed off the front of `log` so far
    # (see app/keeper.py's run_turn/summarize_log_chunk) — the "campaign so
    # far" recap that survives past MAX_LOG_TURNS*4, so the Keeper doesn't
    # lose track of early plot points once the verbatim log can no longer
    # hold everything. Fed into the (cached) static prompt, not re-summarized
    # every turn — only updated on the rare turn where a trim actually fires.
    campaign_summary: str = ""
    openai_previous_response_id: str = ""
    # A provider-side conversation is valid only inside the timeline that
    # created it.  Empty means that no reusable chain is currently trusted.
    openai_previous_response_timeline_id: str = ""
    creation_sessions: dict[str, CreationSession] = field(default_factory=dict)  # keyed by owner_id
    pregens: list[dict[str, Any]] = field(default_factory=list)  # extracted from scenario PDF, cached
    combat: CombatState = field(default_factory=CombatState)

    # Canonical NPC/monster and location stat index (see app/scenario_index.py),
    # built on demand via /coc index — analogous to `pregens` above but for
    # antagonists/locations instead of playable characters. Empty until someone
    # runs /coc index; the Keeper falls back to reading scenario_text directly
    # when empty, same as before this existed. Each npcs entry looks like
    # {"name", "aliases": [...], "hp", "key_stats", "page", "notes"}; each
    # locations entry {"name", "aliases": [...], "summary", "page"}.
    scenario_npc_index: list[dict[str, Any]] = field(default_factory=list)
    scenario_location_index: list[dict[str, Any]] = field(default_factory=list)

    # Per-group override of the Keeper's tone/persona (see app/keeper.py's
    # DEFAULT_PERSONA and _build_static_prompt) — empty string means "use the
    # built-in default cold-observer persona", set via /coc setpersona.
    keeper_persona: str = ""

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

    # A pending player-owned check requested by Keeper. In the default mode the
    # player resolves it with /coc check or a Discord button; autoroll mode
    # skips this entry for newly requested ordinary checks. Old snapshots remain
    # readable and /coc check resolves them normally.
    pending_checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Bounded audit trail of player checks once finalized, including only
    # committed character attribute changes observed across resolution.
    resolved_check_events: list[dict[str, Any]] = field(default_factory=list)

    # Optional group-level override: ordinary investigator checks remain
    # player-triggered by default. Only the KP Assistant or Discord Keeper may
    # enable this through /coc autoroll on; old snapshots therefore load as
    # False without a migration.
    autoroll_checks: bool = False

    # Same-turn idempotency cache for the optional autoroll path. The key
    # includes the current Keeper turn and normalized tool input, so an LLM
    # retry cannot silently consume a second random roll. It is intentionally
    # bounded by the writer rather than retaining an unbounded campaign log.
    deterministic_check_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    # A rolled check awaiting the player's Luck-spend decision (see app/luck.py
    # and app/commands.py's _finalize_check_result/handle_luck_decision) —
    # keyed by owner_id, cleared once they pick an option (or "skip"). Shape:
    # {"skill_name": str, "display_label": str | None, "value": int, "roll":
    # int, "bonus_dice": int, "penalty_dice": int, "original_tier": str,
    # "options": [{"tier": str, "cost": int}, ...]}.
    pending_luck_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)

    # A pregen has been claimed, but its player has not explicitly rolled
    # LUCK yet. Keyed by owner_id; value is the claimed character_id.
    pending_pregen_luck: dict[str, str] = field(default_factory=dict)

    # Set once /coc start successfully delivers the opening narration (see
    # app/scenario_intro.py and app/commands.py's "start" subcommand) — guards
    # against a second run silently re-narrating the opening and duplicating
    # it in the log. Reset to False by /coc newgame like every other field.
    game_started: bool = False

    # COC7e setting period for this group's campaign — affects which default
    # ammo capacity app/pregen_extractor.py's weapon parsing looks up for a
    # generic weapon category (e.g. "半自動手槍") that doesn't name a specific
    # model: the same generic term means a different real-world gun (and thus
    # a different magazine size) in a 1920s-era game than a modern one. Only
    # two values are meaningful: "1920s" (COC7e's flagship default setting)
    # or "modern"; set via /coc era.
    era: str = "1920s"

    # Extraction results from a PDF re-upload awaiting the GM's choice between
    # "全新劇本" and "修正目前劇本" (see app/commands.py's handle_pdf_upload) —
    # None means no PDF upload is currently pending a decision. Persisted
    # (rather than kept in an in-memory cache) for the same reason
    # pending_checks/pending_luck_decisions are: this bot restarts on almost
    # every deploy, and a GM's re-upload flow can easily still be "waiting on
    # a button click" when that happens. Shape: {"text": str, "title": str,
    # "low_text_pages": list[int], "truncated": bool, "npcs": list, "locations":
    # list, "page_maps": dict[str, dict]} — everything handle_pdf_upload
    # needs to finish the save once the GM picks a mode. Deliberately excludes
    # page images: those get written to disk immediately regardless of which
    # mode is chosen (see handle_pdf_upload), so there's nothing about them to
    # defer.
    pending_pdf_upload: dict[str, Any] | None = None
    pending_scenario_upload: dict[str, Any] | None = None
    staged_pdf_parts: list[dict[str, str]] = field(default_factory=list)
    established_facts: list[dict[str, Any]] = field(default_factory=list)
    known_clues: list[dict[str, Any]] = field(default_factory=list)
    consumed_or_removed_items: list[dict[str, Any]] = field(default_factory=list)
    # Durable audit markers for a state-changing tool whose caller was
    # cancelled after the grace period expired. The mutation may have
    # committed in its worker thread, so these markers say recovery_required
    # rather than claiming the outcome was rolled back.
    tool_recovery_markers: list[dict[str, Any]] = field(default_factory=list)

    def get_character_by_name(self, name: str) -> Character | None:
        for c in self.characters.values():
            if c.name == name:
                return c
        return None

    def all_characters(self) -> list[Character]:
        """Return each persisted character once, including partner/test slots."""
        result: list[Character] = []
        seen: set[str] = set()
        for char in list(self.characters.values()) + list(self.characters_by_id.values()):
            key = char.character_id or f"legacy-user:{char.owner_id}"
            if key not in seen:
                seen.add(key)
                result.append(char)
        return result

    def characters_for_owner(self, owner_id: str) -> list[Character]:
        return [char for char in self.all_characters() if char.owner_id == owner_id]

    def get_active_character(self, owner_id: str) -> Character | None:
        character_id = self.active_character_id_by_user.get(owner_id, "")
        if character_id:
            active = next((char for char in self.all_characters() if char.character_id == character_id), None)
            if active is not None:
                if active.active and active.owner_id == owner_id:
                    return active
                # A stale persisted binding must not resurrect a retired
                # character or cross an ownership boundary. Clear only the
                # matching legacy index; a valid different active character
                # for this owner may still exist.
                self.active_character_id_by_user.pop(owner_id, None)
                legacy = self.characters.get(owner_id)
                if legacy is not None and legacy.character_id == character_id:
                    self.characters.pop(owner_id, None)
        legacy = self.characters.get(owner_id)
        if legacy is not None:
            if legacy.owner_id != owner_id:
                # A legacy owner-index entry can be stale or corrupted after
                # the character-id migration. Never let it expose or activate
                # another player's character through the owner lookup.
                self.characters.pop(owner_id, None)
                self.active_character_id_by_user.pop(owner_id, None)
                return None
            if not legacy.active:
                self.characters.pop(owner_id, None)
                return None
            character_id = legacy.character_id or f"legacy-user:{owner_id}"
            legacy.character_id = character_id
            self.characters_by_id.setdefault(character_id, legacy)
            self.active_character_id_by_user[owner_id] = character_id
        return legacy

    def set_active_character(self, owner_id: str, character_id: str) -> Character:
        character = next(
            (char for char in self.characters_for_owner(owner_id) if char.character_id == character_id),
            None,
        )
        if character is None and not character_id:
            character = self.characters.get(owner_id)
        if character is None:
            raise KeyError(character_id)
        if not character.character_id:
            character.character_id = f"legacy-user:{owner_id}"
            character_id = character.character_id
        for owned in self.characters_for_owner(owner_id):
            owned.active = owned.character_id == character_id
        self.active_character_id_by_user[owner_id] = character_id
        self.characters_by_id[character_id] = character
        # Keep the legacy owner index useful during the migration.
        self.characters[owner_id] = character
        return character

    def retire_active_character(self, owner_id: str, name: str | None = None) -> Character:
        """解除一名玩家目前的角色 binding，但保留角色歷史資料。

        ``characters_by_id`` is the durable character history.  The legacy
        ``characters[owner_id]`` map and ``active_character_id_by_user`` are
        only the current-player indexes, so removing those two bindings lets
        ``get_active_character`` return ``None`` without deleting the sheet.
        """
        character = self.get_active_character(owner_id)
        if character is None:
            raise KeyError(owner_id)
        if name is not None and character.name != name:
            raise ValueError(f"目前使用的角色不是「{name}」。")

        character.active = False
        character.away = False
        self.active_character_id_by_user.pop(owner_id, None)
        # The legacy owner index is a compatibility active-character index;
        # remove it unconditionally so a stale legacy entry cannot resurrect a
        # different character through get_active_character's fallback path.
        self.characters.pop(owner_id, None)
        # A retired character must not leave a stale player decision that can
        # later be consumed after the binding is restored.
        self.pending_checks.pop(owner_id, None)
        self.pending_luck_decisions.pop(owner_id, None)
        # Keep a pending pregen Luck roll: only the player may roll it, and
        # clearing it here would let a later reactivation bypass that rule.
        self.characters_by_id[character.character_id] = character
        self.combat.retire_character(character.character_id, character.name, state=self)
        return character

    def active_characters(self) -> list[Character]:
        """Return the currently selected character for each owner."""
        owners = {char.owner_id for char in self.all_characters()}
        result = []
        for owner_id in owners:
            char = self.get_active_character(owner_id)
            if char is not None and char.active and char not in result:
                result.append(char)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "schema_version": self.schema_version,
            # Preserve an empty legacy value on serialization. The repository
            # save path assigns a real timeline before writing; inventing a
            # compatibility id here would make an old snapshot look newer
            # than it is and could incorrectly widen provider/memory trust.
            "timeline_id": self.timeline_id,
            "state_revision": self.state_revision,
            "scenario_title": self.scenario_title,
            "scenario_text": self.scenario_text,
            "scenario_library_id": self.scenario_library_id,
            "scenario_variant_id": self.scenario_variant_id,
            "active_chapter_id": self.active_chapter_id,
            "context_chapter_ids": self.context_chapter_ids,
            "active": self.active,
            "kp_assistant_user_id": self.kp_assistant_user_id,
            "characters": {k: v.to_dict() for k, v in self.characters.items()},
            "characters_by_id": {k: v.to_dict() for k, v in self.characters_by_id.items()},
            "active_character_id_by_user": self.active_character_id_by_user,
            "log": self.log,
            "kp_ooc_log": self.kp_ooc_log,
            "campaign_summary": self.campaign_summary,
            "openai_previous_response_id": self.openai_previous_response_id,
            # Do not infer trust for a legacy response ID while serializing.
            # Missing chain metadata is deliberately preserved as empty so the
            # provider path will reset it on the next turn instead of silently
            # upgrading an unverified server-side conversation into a trusted
            # chain.
            "openai_previous_response_timeline_id": self.openai_previous_response_timeline_id,
            "creation_sessions": {k: v.to_dict() for k, v in self.creation_sessions.items()},
            "pregens": self.pregens,
            "scenario_npc_index": self.scenario_npc_index,
            "scenario_location_index": self.scenario_location_index,
            "keeper_persona": self.keeper_persona,
            "combat": self.combat.to_dict(),
            "scene_maps": self.scene_maps,
            "current_map_page": self.current_map_page,
            "current_room_id": self.current_room_id,
            "party_facing": self.party_facing,
            "pending_checks": self.pending_checks,
            "resolved_check_events": self.resolved_check_events,
            "autoroll_checks": self.autoroll_checks,
            "deterministic_check_results": self.deterministic_check_results,
            "pending_luck_decisions": self.pending_luck_decisions,
            "pending_pregen_luck": self.pending_pregen_luck,
            "game_started": self.game_started,
            "era": self.era,
            "pending_pdf_upload": self.pending_pdf_upload,
            "pending_scenario_upload": self.pending_scenario_upload,
            "staged_pdf_parts": self.staged_pdf_parts,
            "established_facts": self.established_facts,
            "known_clues": self.known_clues,
            "consumed_or_removed_items": self.consumed_or_removed_items,
            "tool_recovery_markers": self.tool_recovery_markers,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> GroupState:
        data = GroupState.migrate_data(data)
        characters = {k: Character.from_dict(v) for k, v in data.get("characters", {}).items()}
        characters_by_id = {}
        for key, char_data in data.get("characters_by_id", {}).items():
            char = Character.from_dict(char_data)
            if not char.character_id:
                char.character_id = key
            characters_by_id[char.character_id] = char
        active_by_user = dict(data.get("active_character_id_by_user", {}))

        # Keep both compatibility indexes pointed at one in-memory object. New
        # characters can be written to the legacy owner_id map before the next
        # save, so merge them individually even when the ID index is non-empty.
        for owner_id, legacy_char in characters.items():
            character_id = legacy_char.character_id or f"legacy-user:{owner_id}"
            legacy_char.character_id = character_id
            canonical = characters_by_id.get(character_id)
            if canonical is None:
                canonical = legacy_char
                characters_by_id[character_id] = canonical
            characters[owner_id] = canonical
            active_by_user.setdefault(owner_id, character_id)

        return GroupState(
            group_id=data["group_id"],
            schema_version=int(data.get("schema_version", 1)),
            # Keep legacy snapshots without a timeline distinguishable from
            # an explicitly assigned campaign timeline. Callers that need a
            # compatibility search use ``legacy-<group_id>`` locally; the
            # normal repository save/turn path initializes a fresh timeline.
            timeline_id=data.get("timeline_id", ""),
            state_revision=int(data.get("state_revision", 0)),
            scenario_title=data.get("scenario_title", ""),
            scenario_text=data.get("scenario_text", ""),
            scenario_library_id=data.get("scenario_library_id", ""),
            scenario_variant_id=data.get("scenario_variant_id", "original"),
            active_chapter_id=data.get("active_chapter_id", ""),
            context_chapter_ids=data.get("context_chapter_ids", []),
            active=data.get("active", False),
            kp_assistant_user_id=data.get("kp_assistant_user_id", ""),
            characters=characters,
            characters_by_id=characters_by_id,
            active_character_id_by_user=active_by_user,
            log=data.get("log", []),
            kp_ooc_log=data.get("kp_ooc_log", []),
            campaign_summary=data.get("campaign_summary", ""),
            openai_previous_response_id=data.get("openai_previous_response_id", ""),
            openai_previous_response_timeline_id=data.get("openai_previous_response_timeline_id", ""),
            creation_sessions={
                k: CreationSession.from_dict(v) for k, v in data.get("creation_sessions", {}).items()
            },
            pregens=data.get("pregens", []),
            scenario_npc_index=data.get("scenario_npc_index", []),
            scenario_location_index=data.get("scenario_location_index", []),
            keeper_persona=data.get("keeper_persona", ""),
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
            resolved_check_events=(
                [dict(item) for item in data.get("resolved_check_events", [])[-20:] if isinstance(item, dict)]
                if isinstance(data.get("resolved_check_events", []), list)
                else []
            ),
            autoroll_checks=bool(data.get("autoroll_checks", False)),
            deterministic_check_results=(
                data.get("deterministic_check_results", {})
                if isinstance(data.get("deterministic_check_results", {}), dict)
                else {}
            ),
            pending_luck_decisions=data.get("pending_luck_decisions", {}),
            pending_pregen_luck=data.get("pending_pregen_luck", {}),
            game_started=data.get("game_started", False),
            era=data.get("era", "1920s"),
            pending_pdf_upload=data.get("pending_pdf_upload"),
            pending_scenario_upload=data.get("pending_scenario_upload"),
            staged_pdf_parts=data.get("staged_pdf_parts", []),
            established_facts=data.get("established_facts", []),
            known_clues=data.get("known_clues", []),
            consumed_or_removed_items=data.get("consumed_or_removed_items", []),
            tool_recovery_markers=data.get("tool_recovery_markers", []),
        )
