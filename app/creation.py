"""Interactive, step-by-step COC7e character creation.

Unlike `models.generate_investigator` (instant random quick-gen), this lets a
player roll attributes, then spend their own occupation/interest skill points via
chat commands, matching the official build-your-own-investigator flow.
"""
from __future__ import annotations

import random

from app.models import (
    BASE_SKILLS,
    OCCUPATION_EQUIPMENT,
    Character,
    CreationSession,
    GroupState,
    damage_bonus_and_build,
    move_rate,
)
from app.skill_aliases import canonical_skill_name

_SKILL_CAP = 90  # soft cap on any single skill at character creation


def _roll(n: int, sides: int, mult: int = 1) -> int:
    return sum(random.randint(1, sides) for _ in range(n)) * mult


def start_creation(state: GroupState, owner_id: str, name: str, occupation: str | None) -> CreationSession:
    """Roll attributes and open the skill-point allocation pools for a new session."""
    str_ = _roll(3, 6, 5)
    con = _roll(3, 6, 5)
    dex = _roll(3, 6, 5)
    app = _roll(3, 6, 5)
    pow_ = _roll(3, 6, 5)
    siz = _roll(2, 6, 5) + 30
    int_ = _roll(2, 6, 5) + 30
    edu = _roll(2, 6, 5) + 30
    luck = _roll(3, 6, 5)

    skills = dict(BASE_SKILLS)
    skills["閃避"] = dex // 2
    skills["母語"] = edu

    occ_points = edu * 20
    interest_points = int_ * 10

    session = CreationSession(
        name=name,
        owner_id=owner_id,
        occupation=occupation or "自由人",
        str_=str_, con=con, siz=siz, dex=dex, app=app, int_=int_, pow_=pow_, edu=edu, luck=luck,
        occ_points_total=occ_points, occ_points_remaining=occ_points,
        interest_points_total=interest_points, interest_points_remaining=interest_points,
        skills=skills,
    )
    state.creation_sessions[owner_id] = session
    return session


def allocate(session: CreationSession, pool: str, skill: str, points: int) -> dict:
    if pool not in ("occ", "int"):
        return {"ok": False, "error": "點數池只能是 occ（職業技能）或 int（興趣技能）"}
    if points <= 0:
        return {"ok": False, "error": "點數必須是正整數"}

    pool_attr = "occ_points_remaining" if pool == "occ" else "interest_points_remaining"
    remaining = getattr(session, pool_attr)
    if points > remaining:
        return {"ok": False, "error": f"點數不夠，這個點數池還剩 {remaining} 點"}

    # Canonicalize before touching session.skills (see app/pregen_extractor.py's
    # pregen_to_character, which does the same) — otherwise a player typing a
    # common shorthand ("手槍") instead of the official name ("射擊（手槍）")
    # opens a brand new, separate dict entry instead of adding to the one
    # start_creation already seeded from BASE_SKILLS. The points would still
    # look "spent" in this session, but resolve_skill_value's exact-match-first
    # lookup would find the untouched official-name entry at actual skill-check
    # time and silently ignore the shorthand one — the player's points would
    # never actually apply in play.
    skill = canonical_skill_name(skill.strip())

    current_value = session.skills.get(skill, BASE_SKILLS.get(skill, 20))
    room = max(0, _SKILL_CAP - current_value)
    if points > room:
        return {"ok": False, "error": f"「{skill}」目前 {current_value}%，建角階段最高加到 {_SKILL_CAP}%，還可以加 {room} 點"}

    session.skills[skill] = current_value + points
    setattr(session, pool_attr, remaining - points)
    return {
        "ok": True,
        "skill": skill,
        "value": session.skills[skill],
        "occ_remaining": session.occ_points_remaining,
        "interest_remaining": session.interest_points_remaining,
    }


def status_text(session: CreationSession) -> str:
    lines = [
        f"【建角中】{session.name}　職業：{session.occupation}",
        f"STR {session.str_} CON {session.con} SIZ {session.siz} DEX {session.dex} "
        f"APP {session.app} INT {session.int_} POW {session.pow_} EDU {session.edu} LUCK {session.luck}",
        f"職業技能點數剩餘：{session.occ_points_remaining} / {session.occ_points_total}"
        "（輸入「/coc alloc occ 技能名 點數」分配）",
        f"興趣技能點數剩餘：{session.interest_points_remaining} / {session.interest_points_total}"
        "（輸入「/coc alloc int 技能名 點數」分配）",
    ]
    top_skills = sorted(session.skills.items(), key=lambda kv: -kv[1])[:12]
    if top_skills:
        lines.append("目前技能：" + "、".join(f"{k} {v}%" for k, v in top_skills))
    lines.append("點數分配完後輸入「/coc create done」完成建角，或「/coc create cancel」放棄。")
    return "\n".join(lines)


def finalize(state: GroupState, owner_id: str) -> Character | None:
    session = state.creation_sessions.pop(owner_id, None)
    if session is None:
        return None

    hp_max = (session.con + session.siz) // 10
    mp_max = session.pow_ // 5
    san_max = 99
    san = min(session.pow_, san_max)
    db, build = damage_bonus_and_build(session.str_, session.siz)
    move = move_rate(session.str_, session.dex, session.siz)

    char = Character(
        name=session.name,
        owner_id=owner_id,
        occupation=session.occupation,
        str_=session.str_, con=session.con, siz=session.siz, dex=session.dex, app=session.app,
        int_=session.int_, pow_=session.pow_, edu=session.edu, luck=session.luck,
        hp=hp_max, hp_max=hp_max,
        mp=mp_max, mp_max=mp_max,
        san=san, san_max=san_max,
        move=move, damage_bonus=db, build=build,
        skills=dict(session.skills),
        # Only matches when the player typed one of the built-in occupation
        # labels exactly (see OCCUPATION_EQUIPMENT's own docstring in
        # app/models.py) — a custom free-text occupation gets no default
        # items, same as it already gets no default skill bonus in this
        # player-driven creation flow.
        carried_items=list(OCCUPATION_EQUIPMENT.get(session.occupation, [])),
    )
    state.characters[owner_id] = char
    return char


def cancel(state: GroupState, owner_id: str) -> bool:
    return state.creation_sessions.pop(owner_id, None) is not None
