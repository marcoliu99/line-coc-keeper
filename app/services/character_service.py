"""Investigator rules the command handlers share: claiming a pregenerated sheet, leaving or rejoining, healing, the readiness roster.

Moved here unchanged from ``legacy_commands``; the callers own the transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app import (
    locks,
    pregen_extractor,
)
from app.keeper_tools import resource_bridge
from app.models import (
    BASE_SKILLS,
    Character,
    GroupState,
)
from app.repositories import state_transaction
from app.repositories.group_state import load_state
from app.services import mutation_admission

if TYPE_CHECKING:
    from app.commands.types import FormatMention


@dataclass
class AwayStateResult:
    character_name: str = ""
    error_text: str = ""


def blocked_by_existing_character(state: GroupState, user_id: str) -> str | None:
    """Returns a rejection message if this player already has a character and
    the current game is still active, else None. Without this, re-running
    /coc pc/create/usepregen (e.g. by accident, or to "try again") would
    silently overwrite the character they're already playing mid-game. /coc end
    (which sets state.active = False) lifts the restriction — characters
    themselves aren't cleared until /coc newgame, but a player is free to
    rebuild once the game they were in has actually ended."""
    if not state.active:
        return None
    char = state.get_active_character(user_id)
    if not char:
        return None
    return (
        f"你已經有角色「{char.name}」了，這局遊戲進行中不能重新建角（避免蓋掉正在用的角色）。"
        "如果真的要換角色，請先讓這局遊戲結束（/coc end），或開新的一局（/coc newgame）。"
    )


def claim_pregen(state: GroupState, index: int, user_id: str, *, custom_name: str | None = None) -> Character:
    """Claim one pregen exactly once and update all state-owned references.

    ``pregen_to_character`` remains a pure constructor so previews and shared
    pregens are safe. Both command routers use this boundary; a repeated claim
    is rejected before a player LUCK roll can be requested twice.
    """
    if not (0 <= index < len(state.pregens)):
        raise ValueError("預製角色編號超出範圍。")
    # A finished game may legitimately let the same player claim a new
    # unclaimed pregen. During an active game the command-level guard already
    # enforces one active investigator; keep the same defensive check here.
    if state.active and state.characters_for_owner(user_id):
        raise ValueError("你目前已經有角色，不能重複認領預製角色。")
    if user_id in state.pending_pregen_luck:
        raise ValueError("你還有一位預製角色尚未完成 LUCK 擲骰，請先輸入「/coc luck roll」。")
    pregen = state.pregens[index]
    claimed_by = pregen.get("claimed_by")
    if claimed_by:
        if claimed_by == user_id:
            raise ValueError("你已經認領過這位預製角色，不能重新骰定。")
        raise ValueError("這位角色已經被其他玩家選走了。")

    sheet_luck = pregen_extractor.pregen_luck_value(pregen.get("luck"))
    char = pregen_extractor.pregen_to_character(
        pregen, user_id, era=state.era, luck=sheet_luck if sheet_luck is not None else 0,
    )
    if custom_name:
        char.name = custom_name
    state.characters[user_id] = char
    state.set_active_character(user_id, char.character_id)
    pregen["claimed_by"] = user_id
    if sheet_luck is None:
        state.pending_pregen_luck[user_id] = char.character_id
    return char


def blocked_by_kp_assistant(state: GroupState, user_id: str) -> str | None:
    if state.kp_assistant_user_id != user_id:
        return None
    return "你目前是這局的 KP 助手，不能同時建立或使用調查員角色。請先使用「/coc kp quit」解除 KP 助手身分。"


def set_away_state(conversation_id: str, user_id: str, away: bool) -> AwayStateResult:
    with locks.get_state_lock(conversation_id):
        mutation_admission.assert_admitted(conversation_id)
        state = load_state(conversation_id)
        char = state.get_active_character(user_id)
        if not char:
            return AwayStateResult(error_text="你還沒有角色。")
        char.away = away
        if char.character_id:
            state.characters_by_id[char.character_id] = char
            state.active_character_id_by_user[user_id] = char.character_id
        if state.characters.get(user_id) and state.characters[user_id].character_id == char.character_id:
            state.characters[user_id].away = away
        state_transaction.commit_snapshot(state)
        return AwayStateResult(character_name=char.name)


def pregen_full_sheet_text(pregen: dict, index: int) -> str:
    """Read-only pregen preview, capped to the same top 12 skills as Character."""
    lines = [
        f"【預製角色 #{index}】{pregen.get('name') or '未命名'}　職業：{pregen.get('occupation', '未知職業')}",
    ]
    attrs = ["str_", "con", "siz", "dex", "app", "int_", "pow_", "edu"]
    labels = {"str_": "STR", "con": "CON", "siz": "SIZ", "dex": "DEX", "app": "APP", "int_": "INT", "pow_": "POW", "edu": "EDU"}
    attr_line = " ".join(f"{labels[a]} {pregen[a]}" for a in attrs if isinstance(pregen.get(a), (int, float)))
    sheet_luck = pregen_extractor.pregen_luck_value(pregen.get("luck"))
    if sheet_luck is not None:
        attr_line += f"{' ' if attr_line else ''}（卡面 LUCK {sheet_luck}，選用時沿用）"
    else:
        attr_line += f"{' ' if attr_line else ''}（LUCK 空白，選用後由玩家擲骰）"
    if attr_line:
        lines.append(attr_line)
    vitals = []
    if isinstance(pregen.get("hp_max"), (int, float)):
        vitals.append(f"HP {pregen['hp_max']}")
    if isinstance(pregen.get("mp_max"), (int, float)):
        vitals.append(f"MP {pregen['mp_max']}")
    if isinstance(pregen.get("san_max"), (int, float)):
        vitals.append(f"SAN {pregen['san_max']}")
    if vitals:
        lines.append("　".join(vitals))
    skills = pregen.get("skills") or {}
    if skills:
        ranked = sorted(skills.items(), key=lambda kv: -kv[1] if isinstance(kv[1], (int, float)) else 0)[:12]
        lines.append("主要技能：" + "、".join(f"{k} {v}%" for k, v in ranked))
    if pregen.get("notes"):
        lines.append(f"背景：{pregen['notes']}")
    if pregen.get("key_connection"):
        lines.append(f"★ 關鍵背景連結：{pregen['key_connection']}")
    for key, value in (pregen.get("extra_fields") or {}).items():
        if value not in (None, "", [], {}):
            lines.append(f"{key}：{value}")
    if pregen.get("claimed_by") or pregen.get("claimed"):
        lines.append("（此角色已被選走）")
    return "\n".join(lines)


def heal_character(char: Character) -> list[str]:
    """Run once per bound character right before /coc start actually opens
    the game (see docs/specs/feature/character_and_dictionary_system_spec.md's Module 7) —
    Character creation today (generate_investigator / pregen_to_character)
    always produces complete derived stats and the full BASE_SKILLS set, so
    a character built through either of those paths should never actually
    trip any of these; this exists for characters built before this
    project's own bug fixes shipped (see docs/changelog.md's Module 2
    entries — pregen_to_character used to only default 閃避/母語, and
    weapons/carried_items used to not get populated at all), which are
    exactly the kind of "known-good fix exists, just never applied
    retroactively" gap this can safely repair on the spot. Mutates `char`
    in place; returns human-readable notes about what got healed or, for
    what genuinely can't be healed, what needs the GM's own attention.
    Caller is responsible for saving state if this list is non-empty."""
    notes: list[str] = []

    missing_skills = [s for s in BASE_SKILLS if s not in char.skills]
    if missing_skills:
        for skill in missing_skills:
            char.skills[skill] = BASE_SKILLS[skill]
        notes.append(f"補上 {len(missing_skills)} 項缺少的官方技能預設值")

    # HP/MP/SAN are pure functions of already-present base attributes (CON+SIZ,
    # POW, POW again) — unlike the 9 base attributes themselves, these are
    # always safe to recompute from data that's still there, never a guess.
    # <=0 can't legitimately happen from real COC7e attribute ranges (see
    # generate_investigator's roll ranges) — only from data built before a
    # fix, or direct DB tampering.
    if char.hp_max <= 0:
        char.hp_max = max(1, (char.con + char.siz) // 10)
        char.hp = min(char.hp, char.hp_max) if char.hp > 0 else char.hp_max
        notes.append("生命值上限異常，已依現有 CON/SIZ 重新算過")
    if char.mp_max <= 0:
        char.mp_max = max(1, char.pow_ // 5)
        char.mp = min(char.mp, char.mp_max) if char.mp > 0 else char.mp_max
        notes.append("魔法值上限異常，已依現有 POW 重新算過")
    if char.san_max <= 0:
        char.san_max = min(char.pow_, 99) or 99
        char.san = min(char.san, char.san_max) if char.san > 0 else char.san_max
        notes.append("理智值上限異常，已依現有 POW 重新算過")

    # The 9 base attributes (STR/CON/SIZ/DEX/APP/INT/POW/EDU/LUCK) have no
    # formula to reconstruct them from — unlike HP/MP/SAN above, there's
    # nothing to safely recompute here. All nine landing on exactly 50 (the
    # extraction pipeline's own fallback default — see pregen_extractor.py's
    # _int_or) is a strong enough coincidence that real attribute rolls or a
    # real scenario's own pregen numbers essentially never produce it, so
    # this is flagged for the GM to manually verify, never silently guessed
    # at or auto-corrected.
    all_nine = (char.str_, char.con, char.siz, char.dex, char.app, char.int_, char.pow_, char.edu, char.luck)
    if len(set(all_nine)) == 1 and all_nine[0] == 50:
        notes.append("⚠️ 9 大屬性剛好全部是 50，可能是舊資料遺失、不是真實數值，建議人工核對角色卡")

    return notes


@dataclass(frozen=True)
class ReadinessInvestigator:
    owner_id: str
    name: str
    occupation: str
    stats: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class OpeningReadiness:
    investigators: tuple[ReadinessInvestigator, ...]
    unclaimed_pregens: int


def snapshot_readiness_roster(
    state: GroupState, healed_notes: dict[str, list[str]],
) -> OpeningReadiness:
    """Materialize the roster before opening work without exposing mutable state."""
    investigators = []
    for owner_id, committed in state.characters.items():
        char = resource_bridge.effective(state, committed)
        weapon_parts = []
        for weapon_name, ammo_info in char.weapons.items():
            if ammo_info.get("ammo_max"):
                weapon_parts.append(f"{weapon_name} ({ammo_info['ammo']}/{ammo_info['ammo_max']})")
            else:
                weapon_parts.append(weapon_name)
        stats = f"HP {char.hp}/{char.hp_max}, SAN {char.san}/{char.san_max}"
        if weapon_parts:
            stats += "，彈藥：" + "、".join(weapon_parts)
        if char.carried_items:
            stats += "，物品：" + "、".join(char.carried_items)
        investigators.append(ReadinessInvestigator(
            owner_id, char.name, char.occupation, stats, tuple(healed_notes.get(owner_id, ())),
        ))
    unclaimed = sum(1 for p in state.pregens if not p.get("claimed_by"))
    return OpeningReadiness(tuple(investigators), unclaimed)


def build_readiness_roster(
    state: GroupState | OpeningReadiness,
    healed_notes: dict[str, list[str]] | None = None,
    format_mention: FormatMention = lambda owner_id: owner_id,
) -> str:
    """The "全團調查員集結就緒名冊" /coc start announces before the opening
    narration — see docs/specs/feature/character_and_dictionary_system_spec.md's Module 7's
    own "範例二" for the format this follows (HP/SAN/weapons/items per
    character, not just name/occupation — a GM glancing at this should be
    able to tell at a glance whether everyone's actually equipped, not just
    who's playing who). `healed_notes` is owner_id -> whatever
    heal_character found for them (empty list if nothing needed fixing).
    `format_mention` renders each owner_id for display (see FormatMention) —
    defaults to the bare id when no Discord mention formatter is supplied."""
    roster = snapshot_readiness_roster(state, healed_notes or {}) if isinstance(state, GroupState) else state
    lines = ["📋 全團調查員集結就緒名冊", ""]
    for char in roster.investigators:
        lines.append(f"・【{char.name}】職業：{char.occupation}（玩家：{format_mention(char.owner_id)}）：{char.stats}")
        for note in char.notes:
            lines.append(f"　　└ {note}")
    if roster.unclaimed_pregens:
        lines.append("")
        lines.append(f"（尚有 {roster.unclaimed_pregens} 位預製角色未被認領，本次以此陣容出戰）")
    return "\n".join(lines)
