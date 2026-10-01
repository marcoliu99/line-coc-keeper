from app import combat
from app.legacy_commands import Reply
from app.repositories.group_state import load_state, save_state
from app.services import mutation_admission


@mutation_admission.guard_async_entry
async def handle_combat_command(conversation_id: str, reply: Reply, parts: list[str], user_id: str = "") -> None:
    action = parts[2].casefold() if len(parts) > 2 else None
    state = load_state(conversation_id)
    if action in {'rollback', 'confirm', 'settle', 'correct', 'reconcile', 'initiative'}:
        await reply('此操作須由 Keeper 發出明確範圍與理由的命令；玩家不能直接變更戰鬥結算或回滾。')
        return
    if state.combat.active and action == 'damage':
        await reply('戰鬥傷害由來源支持的行動流程處理；請描述行動或請 Keeper 明確更正。')
        return
    if state.combat.active and action == 'end':
        await reply('請由 Keeper 取得結算預覽，再明確確認；當前待處理事項仍保留。')
        return
    if state.combat.active and action == 'next':
        await reply('請由 Keeper 完成目前行動後推進；玩家指令不能略過待處理選擇或檢定。')
        return

    if action == "start":
        combat.begin_combat(state)
        save_state(state, reason="combat")
        await reply(combat.status_text(state))
        return

    if action in ("addnpc", "addally"):
        if len(parts) < 6:
            await reply(f"用法：/coc combat {action} 名稱 DEX HP")
            return
        name, dex_str, hp_str = parts[3], parts[4], parts[5]
        try:
            dex, hp = int(dex_str), int(hp_str)
        except ValueError:
            await reply("DEX 和 HP 必須是整數。")
            return
        added = combat.add_combatant(state, name, dex, hp, is_ally=action == "addally")
        if added.reused:
            await reply(
                f"「{added.combatant.name}」已經在戰鬥中且尚未倒下，沒有重複建立第二份——"
                "這隻怪物的血量與狀態沿用原本那份。"
            )
            return
        save_state(state, reason="combat")
        notice = combat.defeated_namesake_notice(added)
        await reply(f"{notice}\n{combat.status_text(state)}" if notice else combat.status_text(state))
        return

    if action == "status":
        await reply(combat.status_text(state))
        return

    if action == "next":
        result = combat.advance_turn(state)
        save_state(state, reason="combat")
        if not result["ok"]:
            await reply(result["error"])
            return
        hp_text = f"HP {result['hp']}/{result['hp_max']}" if result.get("side") != "enemy" else "HP 未公開"
        await reply(f"第 {result['round']} 輪，輪到「{result['current_turn']}」了（{hp_text}）。")
        return

    if action == "damage":
        if len(parts) < 5:
            await reply("用法：/coc combat damage 名稱 增減量（受傷用負數，例如 -5）")
            return
        name, delta_str = parts[3], parts[4]
        try:
            delta = int(delta_str)
        except ValueError:
            await reply("增減量必須是整數。")
            return
        result = combat.damage_combatant(state, name, delta)
        save_state(state, reason="combat")
        if not result["ok"]:
            await reply(result["error"])
            return
        if result.get("side") == "enemy":
            tag = "（已倒下）" if result.get("defeated") else ""
            await reply(f"已調整 {name} 的 HP{tag}。")
        else:
            await reply(f"已調整 {name} 的 HP (現為 {result['hp']}/{result['hp_max']})。")
        return

    if action == "end":
        combat.end_combat(state)
        save_state(state, reason="combat")
        await reply("戰鬥結束，狀態已清除。")
        return

    await reply("未知的戰鬥指令。支援的指令：start, addnpc, addally, status, next, damage, end")
