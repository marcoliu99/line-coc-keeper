"""One click defends, a turn can always be ended, and the Keeper is told the one thing to do next
(docs/specs/enhancement/combat_turn_friction_design_spec.md). Built on the B-scenario helpers."""
from __future__ import annotations

from app import combat, combat_resources
from app.commands.handlers import combat as combat_command
from app.discord_transport import controls
from app.keeper_tools import resource_bridge
from app.services import combat_actions as act
from app.services import combat_engine, prompt_config, turn_resolution
from tests.test_combat_engine import (  # noqa: F401
    GROUP,
    _battle,
    _enemy_turn,
    _load,
    _player,
    _save,
    _tool,
    database,
)


def test_the_defence_button_chooses_and_rolls_in_one_click():
    _battle()
    _enemy_turn([20])  # claw 50: Hard
    pending = _load().pending_checks["p1"]
    assert pending["attacker_name"] == "Cultist" and pending["attacker_tier"] == "hard"
    assert "需要" in controls.defense_choice_hint(pending)  # the buttons can say what each choice needs
    outcome, script = _player("/coc check 閃避", [20])
    assert outcome.should_finalize and script.rolls_taken == 1 and "p1" not in _load().pending_checks
    # The same click again replays what happened instead of asking for a second roll.
    receipt = resource_bridge.control_receipt(_load(), "p1", pending["check_id"], check_option="#0")
    assert receipt and receipt["kind"] == "choice" and receipt["reply_text"].startswith("已選擇「閃避」。")
    assert "20 → 困難成功" in receipt["reply_text"]


def test_a_near_miss_defence_offers_luck_in_chinese():
    _battle()
    state = _load()  # Luck lives in the battle's working resources once it has started
    combat_resources.adjust_resource(state, state.characters["p1"], "luck", 50, event_id="test:luck", reason="test")
    _save(state)
    _enemy_turn([20])
    outcome, _ = _player("/coc check 閃避", [45])  # Dodge 40: a miss that 5 Luck turns into a success
    assert outcome.reply_text.startswith("已選擇「閃避」。🎲") and "一般成功（5 點）" in outcome.reply_text
    assert "regular" not in outcome.reply_text and "p1" in _load().pending_luck_decisions


def test_advance_derives_its_event_id_and_a_retry_replays():
    _battle()
    _enemy_turn([20])
    _player("/coc check 閃避", [90])  # the claw hits; the enemy's action is complete
    enemy_id = "enemy:" + _load().combat.order[0].enemy_card_id
    advanced = _tool("advance_combat_turn", {"actor_id": "Cultist"})
    assert advanced["ok"], advanced
    assert _load().combat.current_index == 1
    again = _tool("advance_combat_turn", {"actor_id": enemy_id})
    assert again["ok"] and _load().combat.current_index == 1, "the derived id makes a retry a replay"
    assert any(e["event_id"].endswith(f":advance:round1:{enemy_id}:1") for e in _load().combat.events)


def _hp_only_enemy(name: str = "Thing", dex: int = 95) -> None:
    state = _load()
    combat_command._apply_combat_command(state, ["/coc", "combat", "addnpc", name, str(dex), "5"])
    _save(state)


def test_an_enemy_registered_with_hp_alone_can_be_skipped_so_the_fight_moves_on():
    _battle(first_enemy=False)
    _hp_only_enemy()
    state = _load()
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Thing")
    _save(state)
    thing = next(c for c in _load().combat.order if c.name == "Thing")
    assert combat.enemy_turn_blocker(_load(), thing) == "this enemy was registered without attacks or abilities"
    plan = _tool("plan_enemy_turn", {"enemy": "Thing"})
    if plan.get("ok") and plan.get("plan_id"):
        assert not _tool("run_enemy_combat_plan", {"plan_id": plan["plan_id"]})["ok"]
    skipped = _tool("advance_combat_turn", {"actor_id": "Thing", "skip": True})
    assert skipped["ok"], skipped
    assert _load().combat.order[_load().combat.current_index].name != "Thing"


def test_a_complete_enemy_turn_still_cannot_be_skipped_but_an_ally_turn_can():
    _battle()
    refused = _tool("advance_combat_turn", {"actor_id": "Cultist", "skip": True})
    assert not refused["ok"] and "plan_enemy_turn" in refused["error"]
    state = _load()
    combat_engine.handle(state, act.AddCombatant(name="Guard", dex=99, hp=8, is_ally=True))
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "Guard")
    _save(state)
    skipped = _tool("advance_combat_turn", {"actor_id": "Guard", "skip": True})
    assert skipped["ok"], skipped
    assert _load().combat.order[_load().combat.current_index].name != "Guard"


def test_an_advance_that_moved_the_turn_reports_the_stuck_enemy_beside_it():
    _battle("p1", enemies=(("Cultist", 90),), first_enemy=False)
    _hp_only_enemy("Thing", dex=75)  # acts right after the investigator
    state = _load()
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.is_pc)
    _save(state)
    advanced = _tool("advance_combat_turn", {"actor_id": "調查員p1", "skip": True})
    assert advanced["ok"], advanced
    assert advanced["enemy_turn"]["ok"] is False and _load().combat.order[_load().combat.current_index].name == "Thing"
    skipped = _tool("advance_combat_turn", {"actor_id": "Thing", "skip": True})
    assert skipped["ok"] and skipped["skipped"]["name"] == "Thing" and "add_npc_to_combat" in skipped["skipped"]["hint"]


def test_a_cited_advance_counts_only_when_it_ended_the_players_own_turn():
    _battle(first_enemy=False)  # the investigator is current
    state = _load()
    before = turn_resolution.gameplay_snapshot(state)
    after = turn_resolution.gameplay_snapshot(state)
    after["combat"]["current_index"] = (before["combat"]["current_index"] + 1) % len(before["combat"]["order"])
    event = {"name": "advance_combat_turn", "arguments": {"actor_id": "x"}, "result": {"ok": True},
             "inventory_before": {}, "gameplay_before": before, "gameplay_after": after}
    assert turn_resolution._mutation_evidence(state, [event], ["tool:1"], "調查員p1") == (True, False)
    stuck = {**event, "gameplay_after": before}
    assert turn_resolution._mutation_evidence(state, [stuck], ["tool:1"], "調查員p1") == (False, False)
    # Moving the enemy's turn along is not this player's action.
    enemy_turn = turn_resolution.gameplay_snapshot(state)
    enemy_turn["combat"]["current_index"] = next(
        i for i, c in enumerate(enemy_turn["combat"]["order"]) if c["side"] == "enemy")
    moved = {**event, "gameplay_before": enemy_turn, "gameplay_after": after}
    assert turn_resolution._mutation_evidence(state, [moved], ["tool:1"], "調查員p1") == (False, False)


def test_the_settled_check_block_tells_the_keeper_what_the_battle_needs_next():
    done = prompt_config.build_resolved_check_outcome_block(
        {"combat_receipt": {"combat_id": "c1", "action_id": "a", "phase": "READY", "completed": True}})
    assert "advance_combat_turn" in done and "event_id 可省略" in done
    waiting = prompt_config.build_resolved_check_outcome_block(
        {"combat_receipt": {"combat_id": "c1", "action_id": "a", "phase": "PLAYER_CHOICE", "completed": False}})
    assert "不要推進回合" in waiting
    ruling = prompt_config.build_resolved_check_outcome_block(
        {"combat_receipt": {"combat_id": "c1", "action_id": "a", "phase": "NEEDS_RULING", "completed": False}})
    assert "resolve_combat_ruling" in ruling
    assert "【戰鬥下一步】" not in prompt_config.build_resolved_check_outcome_block({"combat_receipt": {}})


def test_next_during_a_battle_says_whose_turn_it_is():
    _battle()
    outcome = combat_command._apply_combat_command(_load(), ["/coc", "combat", "next"])
    assert not outcome.ok and "=>" in outcome.text and "Cultist" in outcome.text


def test_the_second_of_two_same_named_allies_is_skipped_by_name_when_it_is_current():
    _battle()
    state = _load()
    for dex in (99, 98):
        combat_engine.handle(state, act.AddCombatant(name="Guard", dex=dex, hp=8, is_ally=True))
    guards = [i for i, c in enumerate(state.combat.order) if c.name == "Guard"]
    assert len(guards) == 2
    state.combat.current_index = guards[1]
    _save(state)
    second = _load().combat.order[guards[1]].combatant_id
    skipped = _tool("advance_combat_turn", {"actor_id": "Guard", "skip": True})
    assert skipped["ok"], skipped
    assert any(e["event_id"].endswith(f":advance:round1:{second}:0") for e in _load().combat.events)
    assert _load().combat.order[_load().combat.current_index].combatant_id != second


def test_an_owed_con_check_tells_the_keeper_not_to_advance():
    block = prompt_config.build_resolved_check_outcome_block(
        {"combat_receipt": {"combat_id": "c1", "action_id": "a", "phase": "INJURY_CHECK", "completed": False}})
    assert "不要推進回合" in block
