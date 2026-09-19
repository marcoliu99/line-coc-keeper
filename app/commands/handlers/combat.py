from typing import Any

from app import combat
from app import checkpoints
from app.repositories.group_state import load_state, save_state
from app.legacy_commands import Reply


async def handle_combat_command(conversation_id: str, reply: Reply, parts: list[str]) -> None:
    action = parts[2] if len(parts) > 2 else None
    state = load_state(conversation_id)

    if action == "start":
        if not state.combat.active:
            checkpoints.create_checkpoint(
                state,
                label="開戰前",
                created_by="system",
                reason="auto_combat_start",
                event_id=f"combat-start:{conversation_id}:{state.state_revision}",
            )
        combat.start_combat(state)
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
        combat.add_npc(state, name, dex, hp, is_ally=(action == "addally"))
        save_state(state, reason="combat")
        await reply(combat.status_text(state))
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
        await reply(f"第 {result['round']} 輪，輪到「{result['current_turn']}」了（HP {result['hp']}/{result['hp_max']}）。")
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
        await reply(f"已調整 {name} 的 HP (現為 {result['hp']}/{result['hp_max']})。")
        return

    if action == "end":
        combat.end_combat(state)
        save_state(state, reason="combat")
        await reply("戰鬥結束，狀態已清除。")
        return

    await reply("未知的戰鬥指令。支援的指令：start, addnpc, addally, status, next, damage, end")
