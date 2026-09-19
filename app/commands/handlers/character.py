from __future__ import annotations

import asyncio
from typing import Any

from app import creation, pregen_extractor
from app.models import OCCUPATIONS, generate_investigator
from app.repositories.group_state import load_state, save_state
from app.legacy_commands import (
    Reply,
    SendDM,
    _blocked_by_kp_assistant,
    _blocked_by_existing_character,
    _claim_pregen,
    _pregen_full_sheet_text,
)


async def handle_character_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    parts: list[str],
) -> None:
    char = None
    sub = parts[1].casefold() if len(parts) > 1 else ""

    if sub == "characters":
        state = load_state(conversation_id)
        owned = state.characters_for_owner(user_id)
        if not owned:
            await reply("你目前沒有角色。")
            return
        active = state.get_active_character(user_id)
        lines = ["你的角色："]
        for char in owned:
            marker = "（目前使用）" if active and char.character_id == active.character_id else ""
            lines.append(f"・{char.name} [{char.slot}] {marker}".rstrip())
        await reply("\n".join(lines))
        return

    if sub == "switch":
        if len(parts) < 3:
            await reply("用法：/coc switch 角色名（可先用 /coc characters 查看）")
            return
        state = load_state(conversation_id)
        if user_id in state.pending_pregen_luck:
            await reply("你還有一位預製角色尚未完成 LUCK 擲骰，請先輸入「/coc luck roll」。")
            return
        name = " ".join(parts[2:]).strip()
        matches = [char for char in state.characters_for_owner(user_id) if char.name == name]
        if not matches:
            await reply("找不到你擁有的這個角色，請先用「/coc characters」查看角色名稱。")
            return
        if len(matches) > 1:
            await reply("你有同名角色，請先重新命名，避免切換到錯誤角色。")
            return
        state.set_active_character(user_id, matches[0].character_id)
        save_state(state)
        await reply(f"目前使用角色已切換為「{matches[0].name}」。")
        return

    if sub == "pc":
        state = load_state(conversation_id)
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return
        if state.pregens:
            await reply("這份劇本有預製角色，請用「/coc pregens」查看、「/coc pregen 編號」選一位，這份劇本不開放自訂角色。")
            return

        if len(parts) < 3:
            occ_hint = "、".join(OCCUPATIONS.keys())
            if state.scenario_text:
                occ_hint += "\n（想用這份劇本裡的職業？先輸入 /coc pregens 讓守密人讀取劇本裡的角色卡）"
            await reply("用法：/coc pc 角色名 [職業]\n可選職業：" + occ_hint)
            return

        blocked = _blocked_by_existing_character(state, user_id)
        if blocked:
            await reply(blocked)
            return

        name = parts[2]
        occupation = parts[3] if len(parts) > 3 else None

        char = generate_investigator(name=name, owner_id=user_id, occupation=occupation)
        state.characters[user_id] = char
        state.set_active_character(user_id, char.character_id)
        save_state(state)
        await reply(f"調查員建立完成！\n\n{char.sheet_text()}")
        return

    if sub == "sheet":
        state = load_state(conversation_id)
        char = state.get_active_character(user_id)
        if not char:
            await reply("你還沒有角色，先輸入「/coc pc 角色名 職業」建立一個吧。")
            return
        await reply(char.sheet_text())
        return

    if sub == "setskill":
        if len(parts) < 5:
            await reply("用法：/coc setskill 角色名 技能名 數值")
            return
        name, skill, value_str = parts[2], parts[3], parts[4]
        state = load_state(conversation_id)
        char = state.get_active_character(user_id)
        if not char or char.name != name:
            await reply("只能修改你自己建立的角色（角色名稱需完全相符）。")
            return
        try:
            value = max(0, min(100, int(value_str)))
        except ValueError:
            await reply("數值必須是整數。")
            return
        char.skills[skill] = value
        save_state(state)
        await reply(f"已將 {name} 的「{skill}」設為 {value}%。")
        return

    if sub == "setconnection":
        if len(parts) < 4:
            await reply("用法：/coc setconnection 角色名 敘述（例如：/coc setconnection 小明 你失散多年的妹妹）")
            return
        name = parts[2]
        description = " ".join(parts[3:])
        state = load_state(conversation_id)
        char = state.get_active_character(user_id)
        if not char or char.name != name:
            await reply("只能修改你自己建立的角色（角色名稱需完全相符）。")
            return
        char.key_connection = description
        save_state(state)
        await reply(f"已將 {name} 的「★ 關鍵背景連結」設為：{description}")
        return

    if sub == "create":
        action = parts[2] if len(parts) > 2 else None
        state = load_state(conversation_id)
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return

        if action == "status":
            session = state.creation_sessions.get(user_id)
            if not session:
                await reply("目前沒有進行中的建角流程，輸入「/coc create 角色名 [職業]」開始。")
                return
            await reply(creation.status_text(session))
            return

        if action == "done":
            session = state.creation_sessions.get(user_id)
            if not session:
                await reply("目前沒有進行中的建角流程。")
                return
            leftover = session.occ_points_remaining + session.interest_points_remaining
            char = creation.finalize(state, user_id)
            if char is None:
                await reply("建角資料已失效，請重新開始建角流程。")
                return
            save_state(state)
            note = f"\n（還有 {leftover} 點未分配的技能點數已捨棄）" if leftover else ""
            await reply(f"調查員建立完成！\n\n{char.sheet_text()}{note}")
            return

        if action == "cancel":
            ok = creation.cancel(state, user_id)
            save_state(state)
            await reply("已取消建角流程。" if ok else "目前沒有進行中的建角流程。")
            return

        if state.pregens:
            await reply("這份劇本有預製角色，請用「/coc pregens」查看、「/coc pregen 編號」選一位，這份劇本不開放自訂角色。")
            return

        if not action:
            await reply("用法：/coc create 角色名 [職業]\n可選職業：" + "、".join(OCCUPATIONS.keys()))
            return

        if user_id in state.creation_sessions:
            await reply("你已經有一個建角流程進行中了，先用「/coc create done」完成或「/coc create cancel」取消。")
            return

        blocked = _blocked_by_existing_character(state, user_id)
        if blocked:
            await reply(blocked)
            return

        name = action
        occupation = parts[3] if len(parts) > 3 else None
        session = creation.start_creation(state, user_id, name, occupation)
        save_state(state)
        await reply(f"已擲出屬性，開始分配技能點數！\n\n{creation.status_text(session)}")
        return

    if sub == "alloc":
        if len(parts) < 5:
            await reply("用法：/coc alloc occ|int 技能名 點數")
            return
        pool, skill, points_str = parts[2], parts[3], parts[4]
        state = load_state(conversation_id)
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return
        session = state.creation_sessions.get(user_id)
        if not session:
            await reply("目前沒有進行中的建角流程，先輸入「/coc create 角色名 [職業]」開始。")
            return
        try:
            points = int(points_str)
        except ValueError:
            await reply("點數必須是整數。")
            return
        result = creation.allocate(session, pool, skill, points)
        if not result["ok"]:
            await reply(result["error"])
            return
        save_state(state)
        await reply(creation.status_text(session))
        return

    if sub == "pregens":
        state = load_state(conversation_id)
        if not state.scenario_text and not state.pregens:
            await reply("目前還沒有載入劇本，上傳 PDF 之後才能抓取內建角色卡（或直接上傳 role_ 開頭的角色卡檔案）。")
            return
        if not state.pregens:
            pregens = await asyncio.to_thread(pregen_extractor.extract_pregens, state.scenario_text)
            state.pregens = pregens
            save_state(state)
        if not state.pregens:
            await reply("這份劇本沒有附帶預製調查員角色卡，用 /coc pc 或 /coc create 自己建立角色吧。")
            return
        lines = ["這份劇本內建了以下預製調查員："]
        for i, p in enumerate(state.pregens, start=1):
            claimed_by = p.get("claimed_by")
            tag = "（已被選走）" if claimed_by else ""
            lines.append(f"{i}. {p.get('name') or '未命名'}（{p.get('occupation', '未知職業')}）{tag}")
        lines.append("輸入「/coc pregen 編號」查看某位角色的完整能力，或直接「/coc usepregen 編號 [自訂名稱]」使用。")
        await reply("\n".join(lines))
        return

    if sub == "pregen":
        if len(parts) < 3:
            await reply("用法：/coc pregen 編號（先用 /coc pregens 看編號對照）")
            return
        state = load_state(conversation_id)
        if not state.pregens:
            await reply("還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
            return
        try:
            idx = int(parts[2])
        except ValueError:
            await reply("編號必須是數字。")
            return
        if not (1 <= idx <= len(state.pregens)):
            await reply(f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
            return
        await reply(_pregen_full_sheet_text(state.pregens[idx - 1], idx))
        return

    if sub == "usepregen":
        if len(parts) < 3:
            await reply("用法：/coc usepregen 編號 [自訂名稱]")
            return
        state = load_state(conversation_id)
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return
        if not state.pregens:
            await reply("還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
            return
        try:
            idx = int(parts[2])
        except ValueError:
            await reply("編號必須是數字。")
            return
        if not (1 <= idx <= len(state.pregens)):
            await reply(f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
            return
        blocked = _blocked_by_existing_character(state, user_id)
        if blocked:
            await reply(blocked)
            return
        try:
            char = _claim_pregen(state, idx - 1, user_id, custom_name=parts[3] if len(parts) > 3 else None)
        except ValueError as exc:
            await reply(str(exc) + " 輸入「/coc pregens」看看還有哪些可選。")
            return
        save_state(state)
        await reply(f"已使用預製角色！\n\n{char.sheet_text()}\n\n請輸入「/coc luck roll」完成玩家 LUCK 擲骰。")
        if char.secret_goal:
            try:
                await send_dm(user_id, f"🤫（私訊）你的秘密目標：{char.secret_goal}")
            except Exception:
                import logging
                logging.getLogger(__name__).exception("send_dm (secret_goal on /coc pregen) failed for user_id=%s", user_id)
        return

    await reply(f"未知的角色管理指令：{sub}")
