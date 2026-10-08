from app import combat
from app.commands.handlers.transact import Outcome, done, refuse, transact
from app.commands.types import Reply
from app.models import GroupState
from app.repositories.group_state import load_state
from app.services import combat_actions as act
from app.services import combat_engine, mutation_admission


def _apply_combat_command(state: GroupState, parts: list[str]) -> Outcome:
    action = parts[2].casefold() if len(parts) > 2 else None
    if state.combat.active and action == 'damage':
        return refuse('戰鬥傷害由來源支持的行動流程處理；請描述行動或請 Keeper 明確更正。')
    if state.combat.active and action == 'end':
        return refuse('請由 Keeper 取得結算預覽，再明確確認；當前待處理事項仍保留。')
    if state.combat.active and action == 'next':
        # A player asking to move on usually wants to know what the fight is waiting for: say so, with the status.
        status = combat_engine.handle(state, act.Status())
        return refuse(f'{status}\n戰鬥中由守密人推進回合；標 => 的是目前輪到的人，待處理的選擇或檢定要先完成。')

    if action == "start":
        combat_engine.handle(state, act.Start())
        return done(combat_engine.handle(state, act.Status()))

    if action in ("addnpc", "addally"):
        if len(parts) < 6:
            return refuse(f"用法：/coc combat {action} 名稱 DEX HP")
        name, dex_str, hp_str = parts[3], parts[4], parts[5]
        try:
            dex, hp = int(dex_str), int(hp_str)
        except ValueError:
            return refuse("DEX 和 HP 必須是整數。")
        added = combat_engine.handle(state, act.AddCombatant(name=name, dex=dex, hp=hp, is_ally=action == "addally"))
        if added.reused:
            return refuse(
                f"「{added.combatant.name}」已經在戰鬥中且尚未倒下，沒有重複建立第二份——"
                "這隻怪物的血量與狀態沿用原本那份。"
            )
        notice = combat.defeated_namesake_notice(added)
        status = combat_engine.handle(state, act.Status())
        return done(f"{notice}\n{status}" if notice else status)

    if action == "next":
        result = combat_engine.handle(state, act.Advance())
        if not result["ok"]:
            return Outcome(False, result["error"])
        hp_text = f"HP {result['hp']}/{result['hp_max']}" if result.get("side") != "enemy" else "HP 未公開"
        return done(f"第 {result['round']} 輪，輪到「{result['current_turn']}」了（{hp_text}）。")

    if action == "damage":
        if len(parts) < 5:
            return refuse("用法：/coc combat damage 名稱 增減量（受傷用負數，例如 -5）")
        name, delta_str = parts[3], parts[4]
        try:
            delta = int(delta_str)
        except ValueError:
            return refuse("增減量必須是整數。")
        result = combat_engine.handle(state, act.DamageCombatant(name, delta))
        if not result["ok"]:
            return Outcome(False, result["error"])
        if result.get("side") == "enemy":
            tag = "（已倒下）" if result.get("defeated") else ""
            return done(f"已調整 {name} 的 HP{tag}。")
        return done(f"已調整 {name} 的 HP (現為 {result['hp']}/{result['hp_max']})。")

    if action == "end":
        combat.end_combat(state)
        return done("戰鬥結束，狀態已清除。")

    return refuse("未知的戰鬥指令。支援的指令：start, addnpc, addally, status, next, damage, end")


@mutation_admission.guard_async_entry
async def handle_combat_command(conversation_id: str, reply: Reply, parts: list[str], user_id: str = "") -> None:
    action = parts[2].casefold() if len(parts) > 2 else None
    if action in {'rollback', 'confirm', 'settle', 'correct', 'reconcile', 'initiative'}:
        await reply('此操作須由 Keeper 發出明確範圍與理由的命令；玩家不能直接變更戰鬥結算或回滾。')
        return
    if action == "status":
        await reply(combat_engine.handle(load_state(conversation_id), act.Status()))
        return
    outcome = await transact(conversation_id, lambda state: _apply_combat_command(state, parts), reason="combat")
    await reply(outcome.text)
