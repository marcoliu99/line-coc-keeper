"""Four replies from the 2026-10-11 soak runs: bosses that never struck, narration that contradicted the attack just
settled, the battle's bookkeeping shown to players, and an empty gun refused without saying so."""
from __future__ import annotations

from app import combat
from app.agents import executor
from app.domain.models import MechanicResult, ObservedOutcome, StateDelta
from app.models import Character, Combatant, CombatState, GroupState
from app.services import prompt_config, turn_delivery, turn_fallback
from tests import combat_calls as calls


def _boss_with_a_told_ability() -> GroupState:
    state = GroupState(group_id="g")
    pc = Character(name="Mark", owner_id="u1", character_id="char-mark", dex=55, hp=12, hp_max=12)
    state.characters, state.characters_by_id = {"u1": pc}, {"char-mark": pc}
    state.active_character_id_by_user = {"u1": "char-mark"}
    combat.start_combat(state)
    combat.add_npc(state, "Corbitt", 60, 14,
                   attacks=[{"id": "knife", "name": "knife", "skill_value": 50, "damage": "1d4", "range_band": "engaged"}],
                   abilities=[{"id": "dominate", "name": "Dominate", "trigger": {"type": "first_available"}}])
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Corbitt")
    return state


def test_an_ability_the_engine_only_tells_is_told_once_and_then_the_enemy_attacks():
    state = _boss_with_a_told_ability()
    first = calls.plan_enemy_turn(state)
    assert first["selected_action"] == "special_ability"
    told = calls.resolve_enemy_action(state, first["plan_id"])
    assert told["ok"] and told["result"]["special_given_up"]
    state.combat.round_number += 1  # its next turn
    second = calls.plan_enemy_turn(state)
    assert second["selected_action"] == "attack" and second["selected_id"] == "knife"


def test_an_enemy_with_nothing_to_attack_with_still_uses_its_ability():
    state = _boss_with_a_told_ability()
    card = state.combat.enemy_cards[next(iter(state.combat.enemy_cards))]
    card.attacks = []
    first = calls.plan_enemy_turn(state)
    calls.resolve_enemy_action(state, first["plan_id"])
    state.combat.round_number += 1
    assert calls.plan_enemy_turn(state)["selected_action"] == "special_ability"


def _facts(*outcomes: ObservedOutcome) -> str:
    result = MechanicResult(success=True, action_type="tool_calls", narrative_facts=["x"], state_delta=StateDelta(),
                            observed_outcomes=list(outcomes))
    return prompt_config.build_mechanic_facts_block(result)


def test_the_narrator_is_told_the_attack_this_turn_settled_before_whose_turn_is_next():
    line = "⚔️ 命中，Corbitt 受到 3 點傷害。"
    block = _facts(ObservedOutcome("tool:1", "declare_combat_action", True, line, "public"))
    assert "【本回合已結算的攻擊：已經發生】" in block and line in block and "不可寫成還沒出手" in block


def test_no_attack_block_for_a_replay_a_refusal_or_another_tool():
    assert "本回合已結算的攻擊" not in _facts(
        ObservedOutcome("tool:1", "declare_combat_action", True, "", "internal"),  # a replay is silenced
        ObservedOutcome("tool:2", "declare_combat_action", False, ".38 Revolver沒有子彈了", "public"),
        ObservedOutcome("tool:3", "skill_check", True, "Mark 的檢定已結算", "public"))


def test_players_do_not_read_the_battles_bookkeeping():
    hp = turn_delivery.observe_tool("adjust_character", {"ok": True, "investigator": "Ada", "field": "hp", "value": 8,
                                                         "provisional": True}, 1)
    attack = turn_delivery.observe_tool("declare_combat_action", {
        "ok": True, "completed": True, "provisional": True,
        "result": {"hit": True, "damage": {"target": "Corbitt", "final_damage": 3}}}, 1)
    for text in (hp.public_text, attack.public_text):
        assert "暫定" not in text and "結算" not in text


def _empty_gun_battle(ammo: int) -> GroupState:
    state = GroupState(group_id="g")
    pc = Character(name="Marcus", owner_id="p2", character_id="legacy-user:p2", hp=12, hp_max=12,
                   weapons={".38 Revolver": {"ammo": ammo, "ammo_max": 6}})
    state.characters, state.characters_by_id = {"p2": pc}, {"legacy-user:p2": pc}
    state.active_character_id_by_user = {"p2": "legacy-user:p2"}
    state.combat = CombatState(active=True, round_number=1, current_index=0, phase="READY", order=[
        Combatant(name="Marcus", side="pc", is_pc=True, combatant_id="pc:legacy-user:p2", character_id="legacy-user:p2",
                  hp=12, hp_max=12),
        Combatant(name="深潛者混血", side="enemy", hp=2, hp_max=11)])
    return state


def test_the_actor_is_told_which_gun_is_empty():
    text = turn_fallback.combat_guidance(_empty_gun_battle(0), "unsupported_action", "legacy-user:p2")
    assert ".38 Revolver 已經沒有子彈" in text and "「深潛者混血」" in text
    assert "沒有子彈" not in turn_fallback.combat_guidance(_empty_gun_battle(3), "unsupported_action", "legacy-user:p2")


def test_a_replayed_attack_never_reaches_the_attack_fact_block():
    """The Executor silences an attack call that changed nothing (a stored receipt replayed for a reused id) before
    the narrator's facts are built from the same outcomes."""
    receipt = {"ok": True, "completed": True, "result": {"hit": True, "damage": {"target": "Corbitt", "final_damage": 3}}}
    observed = [turn_delivery.observe_tool("run_combat_action", receipt, 1)]
    executor._silence_replayed_attack(observed, "run_combat_action", {"v": 1}, {"v": 1})
    assert "本回合已結算的攻擊" not in _facts(*observed)


def test_the_resolved_roll_follow_up_tells_an_enemy_turn_spent_on_a_told_ability():
    step = prompt_config._combat_turn_step({"combat_id": "c", "auto_advanced": {
        "next_actor": "Mark", "round_now": 2, "phase": "READY",
        "enemy_turns": [{"ok": True, "phase": "READY", "narration": "Corbitt 的目光鎖住了你。"}]}})
    assert "敵人這一輪沒有攻擊，而是：Corbitt 的目光鎖住了你。" in step
