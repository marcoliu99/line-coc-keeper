from __future__ import annotations

import asyncio
import logging

from app import creation, pregen_extractor, spoiler_policy
from app.commands.handlers.transact import Outcome, done, refuse, transact
from app.commands.types import Reply, SendDM
from app.keeper_tools import resource_bridge
from app.legacy_commands import (
    _blocked_by_existing_character,
    _blocked_by_kp_assistant,
    _claim_pregen,
    _pregen_full_sheet_text,
)
from app.models import OCCUPATIONS, GroupState, generate_investigator
from app.repositories.group_state import load_state
from app.services import combat_engine, mutation_admission

_logger = logging.getLogger(__name__)

# Subcommands that may change the game state. Each runs its validation and its
# change against the latest committed state inside one transaction.
_REPLACEMENT_GUARDED = {"switch", "retire", "pc", "create", "alloc", "usepregen", "pregen"}


def _guard_replacement(state: GroupState) -> Outcome | None:
    block = resource_bridge.guard_replacement(state)
    return refuse(block) if block else None


def _switch(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    blocked = _guard_replacement(state)
    if blocked:
        return blocked
    if len(parts) < 3:
        return refuse("用法：/coc switch 角色名（可先用 /coc characters 查看）")
    if user_id in state.pending_pregen_luck:
        return refuse("你還有一位預製角色尚未完成 LUCK 擲骰，請先輸入「/coc luck roll」。")
    name = " ".join(parts[2:]).strip()
    matches = [char for char in state.characters_for_owner(user_id) if char.name == name]
    if not matches:
        return refuse("找不到你擁有的這個角色，請先用「/coc characters」查看角色名稱。")
    if len(matches) > 1:
        return refuse("你有同名角色，請先重新命名，避免切換到錯誤角色。")
    state.set_active_character(user_id, matches[0].character_id)
    return done(f"目前使用角色已切換為「{matches[0].name}」。")


def _retire(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    blocked = _guard_replacement(state)
    if blocked:
        return blocked
    kp_block = _blocked_by_kp_assistant(state, user_id)
    if kp_block:
        return refuse(kp_block)
    requested_name = " ".join(parts[2:]).strip() or None
    try:
        character = state.retire_active_character(
            user_id, requested_name, finish_turn=combat_engine.finish_retired_turn,
        )
    except KeyError:
        return refuse("你目前沒有正在使用的角色。")
    except ValueError as exc:
        return refuse(str(exc))
    return done(
        f"已退出角色「{character.name}」。角色資料仍保留，之後可用「/coc switch {character.name}」重新加入；"
        "目前不再參與遊戲。"
    )


def _pc(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    blocked = _guard_replacement(state)
    if blocked:
        return blocked
    kp_block = _blocked_by_kp_assistant(state, user_id)
    if kp_block:
        return refuse(kp_block)
    if state.pregens:
        return refuse("這份劇本有預製角色，請用「/coc pregens」查看、「/coc pregen 編號」選一位，這份劇本不開放自訂角色。")

    if len(parts) < 3:
        occ_hint = "、".join(OCCUPATIONS.keys())
        if state.scenario_text:
            occ_hint += "\n（想用這份劇本裡的職業？先輸入 /coc pregens 讓守密人讀取劇本裡的角色卡）"
        return refuse("用法：/coc pc 角色名 [職業]\n可選職業：" + occ_hint)

    existing = _blocked_by_existing_character(state, user_id)
    if existing:
        return refuse(existing)

    name = parts[2]
    occupation = parts[3] if len(parts) > 3 else None
    char = generate_investigator(name=name, owner_id=user_id, occupation=occupation)
    state.characters[user_id] = char
    state.set_active_character(user_id, char.character_id)
    return done(f"調查員建立完成！\n\n{char.sheet_text()}")


def _setskill(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    if len(parts) < 5:
        return refuse("用法：/coc setskill 角色名 技能名 數值")
    name, skill, value_str = parts[2], parts[3], parts[4]
    char = state.get_active_character(user_id)
    if not char or char.name != name:
        return refuse("只能修改你自己建立的角色（角色名稱需完全相符）。")
    try:
        value = max(0, min(100, int(value_str)))
    except ValueError:
        return refuse("數值必須是整數。")
    char.skills[skill] = value
    return done(f"已將 {name} 的「{skill}」設為 {value}%。")


def _setconnection(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    if len(parts) < 4:
        return refuse("用法：/coc setconnection 角色名 敘述（例如：/coc setconnection 小明 你失散多年的妹妹）")
    name = parts[2]
    description = " ".join(parts[3:])
    char = state.get_active_character(user_id)
    if not char or char.name != name:
        return refuse("只能修改你自己建立的角色（角色名稱需完全相符）。")
    char.key_connection = description
    return done(f"已將 {name} 的「★ 關鍵背景連結」設為：{description}")


def _create(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    blocked = _guard_replacement(state)
    if blocked:
        return blocked
    action = parts[2] if len(parts) > 2 else None
    kp_block = _blocked_by_kp_assistant(state, user_id)
    if kp_block:
        return refuse(kp_block)

    if action == "status":
        session = state.creation_sessions.get(user_id)
        if not session:
            return refuse("目前沒有進行中的建角流程，輸入「/coc create 角色名 [職業]」開始。")
        return done(creation.status_text(session), save=False)

    if action == "done":
        session = state.creation_sessions.get(user_id)
        if not session:
            return refuse("目前沒有進行中的建角流程。")
        leftover = session.occ_points_remaining + session.interest_points_remaining
        char = creation.finalize(state, user_id)
        if char is None:
            return refuse("建角資料已失效，請重新開始建角流程。")
        note = f"\n（還有 {leftover} 點未分配的技能點數已捨棄）" if leftover else ""
        return done(f"調查員建立完成！\n\n{char.sheet_text()}{note}")

    if action == "cancel":
        cancelled = creation.cancel(state, user_id)
        text = "已取消建角流程。" if cancelled else "目前沒有進行中的建角流程。"
        return Outcome(cancelled, text, save=cancelled)

    if state.pregens:
        return refuse("這份劇本有預製角色，請用「/coc pregens」查看、「/coc pregen 編號」選一位，這份劇本不開放自訂角色。")

    if not action:
        return refuse("用法：/coc create 角色名 [職業]\n可選職業：" + "、".join(OCCUPATIONS.keys()))

    if user_id in state.creation_sessions:
        return refuse("你已經有一個建角流程進行中了，先用「/coc create done」完成或「/coc create cancel」取消。")

    existing = _blocked_by_existing_character(state, user_id)
    if existing:
        return refuse(existing)

    occupation = parts[3] if len(parts) > 3 else None
    session = creation.start_creation(state, user_id, action, occupation)
    return done(f"已擲出屬性，開始分配技能點數！\n\n{creation.status_text(session)}")


def _alloc(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    blocked = _guard_replacement(state)
    if blocked:
        return blocked
    if len(parts) < 5:
        return refuse("用法：/coc alloc occ|int 技能名 點數")
    pool, skill, points_str = parts[2], parts[3], parts[4]
    kp_block = _blocked_by_kp_assistant(state, user_id)
    if kp_block:
        return refuse(kp_block)
    session = state.creation_sessions.get(user_id)
    if not session:
        return refuse("目前沒有進行中的建角流程，先輸入「/coc create 角色名 [職業]」開始。")
    try:
        points = int(points_str)
    except ValueError:
        return refuse("點數必須是整數。")
    result = creation.allocate(session, pool, skill, points)
    if not result["ok"]:
        return refuse(result["error"])
    return done(creation.status_text(session))


def _usepregen(state: GroupState, user_id: str, parts: list[str]) -> Outcome:
    blocked = _guard_replacement(state)
    if blocked:
        return blocked
    if len(parts) < 3:
        return refuse("用法：/coc usepregen 編號 [自訂名稱]")
    kp_block = _blocked_by_kp_assistant(state, user_id)
    if kp_block:
        return refuse(kp_block)
    if not state.pregens:
        return refuse("還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
    try:
        idx = int(parts[2])
    except ValueError:
        return refuse("編號必須是數字。")
    if not (1 <= idx <= len(state.pregens)):
        return refuse(f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
    existing = _blocked_by_existing_character(state, user_id)
    if existing:
        return refuse(existing)
    try:
        char = _claim_pregen(state, idx - 1, user_id, custom_name=parts[3] if len(parts) > 3 else None)
    except ValueError as exc:
        return refuse(str(exc) + " 輸入「/coc pregens」看看還有哪些可選。")
    luck_note = ("請輸入「/coc luck roll」完成玩家 LUCK 擲骰。"
                 if user_id in state.pending_pregen_luck else
                 f"已沿用角色卡的 LUCK {char.luck}，現在可以開始遊戲。")
    return done(
        f"已使用預製角色！\n\n{char.sheet_text()}\n\n{luck_note}",
        value=char.secret_goal,
    )


_MUTATIONS = {
    "switch": _switch,
    "retire": _retire,
    "pc": _pc,
    "setskill": _setskill,
    "setconnection": _setconnection,
    "create": _create,
    "alloc": _alloc,
    "usepregen": _usepregen,
}


@mutation_admission.guard_async_entry
async def handle_character_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    parts: list[str],
) -> bool:
    sub = parts[1].casefold() if len(parts) > 1 else ""
    if sub in _REPLACEMENT_GUARDED:
        guarded_state = load_state(conversation_id)
        replacement_block = resource_bridge.guard_replacement(guarded_state)
        if replacement_block:
            await reply(replacement_block)
            return False

    if sub in _MUTATIONS:
        handler = _MUTATIONS[sub]
        outcome = await transact(
            conversation_id, lambda state: handler(state, user_id, parts), reason="character",
        )
        await reply(outcome.text)
        if sub == "usepregen" and outcome.ok and outcome.value:
            try:
                await send_dm(user_id, f"🤫（私訊）你的秘密目標：{outcome.value}")
            except Exception:
                _logger.exception("send_dm (secret_goal on /coc pregen) failed for user_id=%s", user_id)
        return outcome.ok

    if sub == "characters":
        state = load_state(conversation_id)
        owned = state.characters_for_owner(user_id)
        if not owned:
            await reply("你目前沒有角色。")
            return False
        active = state.get_active_character(user_id)
        lines = ["你的角色："]
        for owned_char in owned:
            marker = "（目前使用）" if active and owned_char.character_id == active.character_id else ""
            lines.append(f"・{owned_char.name} [{owned_char.slot}] {marker}".rstrip())
        await reply("\n".join(lines))
        return True

    if sub == "sheet":
        state = load_state(conversation_id)
        char = state.get_active_character(user_id)
        if not char:
            await reply("你還沒有角色，先輸入「/coc pc 角色名 職業」建立一個吧。")
            return False
        sheet = resource_bridge.effective(state, char).sheet_text()
        if resource_bridge.participating(state, char):
            sheet = "【戰鬥暫定數值；尚未結算】\n" + sheet
        await reply(sheet)
        return True

    if sub == "pregens":
        state = load_state(conversation_id)
        if not state.scenario_text and not state.pregens:
            await reply("目前還沒有載入劇本，上傳 PDF 之後才能抓取內建角色卡（或直接上傳 role_ 開頭的角色卡檔案）。")
            return False
        if not state.pregens:
            # Extraction can call a provider, so it runs before (never inside)
            # the transaction; the transaction only fills a still-empty pool.
            extracted = await asyncio.to_thread(pregen_extractor.extract_pregens, state.scenario_text)

            def fill_pool(latest: GroupState) -> Outcome:
                if not latest.pregens and extracted:
                    latest.pregens = extracted
                    return done()
                return done(save=False)

            await transact(conversation_id, fill_pool, reason="character")
            state = load_state(conversation_id)
        if not state.pregens:
            await reply("這份劇本沒有附帶預製調查員角色卡，用 /coc pc 或 /coc create 自己建立角色吧。")
            return False
        lines = ["這份劇本內建了以下預製調查員："]
        for i, p in enumerate(state.pregens, start=1):
            claimed_by = p.get("claimed_by")
            tag = "（已被選走）" if claimed_by else ""
            lines.append(f"{i}. {p.get('name') or '未命名'}（{p.get('occupation', '未知職業')}）{tag}")
        lines.append("輸入「/coc pregen 編號」查看某位角色的完整能力，或直接「/coc usepregen 編號 [自訂名稱]」使用。")
        await reply("\n".join(lines))
        return True

    if sub == "pregen":
        if len(parts) < 3:
            await reply("用法：/coc pregen 編號（先用 /coc pregens 看編號對照）")
            return False
        state = load_state(conversation_id)
        if not state.pregens:
            await reply("還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
            return False
        try:
            idx = int(parts[2])
        except ValueError:
            await reply("編號必須是數字。")
            return False
        if not (1 <= idx <= len(state.pregens)):
            await reply(f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
            return False
        # §7.2: allowlist-redact before this goes to the whole channel — never
        # emits secret_goal, claimed_by's real user id, or unvetted extra_fields.
        pregen_view = spoiler_policy.redact_public_pregen(state.pregens[idx - 1])
        await reply(_pregen_full_sheet_text(pregen_view, idx))
        return True

    await reply(f"未知的角色管理指令：{sub}")
    return False
