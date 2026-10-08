"""One click defends, a turn can always be ended, and the Keeper is told the one thing to do next
(docs/specs/enhancement/combat_turn_friction_design_spec.md). Built on the B-scenario helpers."""
from __future__ import annotations

from unittest.mock import patch

from app import combat, combat_resources, combat_rules, config
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
    with patch.object(config, "COMBAT_AUTO_ADVANCE", False):  # the Keeper ends this one by hand
        _player("/coc check 閃避", [90])  # the claw hits; the enemy's action is complete
    enemy_id = "enemy:" + _load().combat.order[0].enemy_card_id
    advanced = _tool("advance_combat_turn", {"actor_id": "Cultist"})
    assert advanced["ok"], advanced
    assert _load().combat.current_index == 1
    again = _tool("advance_combat_turn", {"actor_id": enemy_id})
    assert again["ok"] and _load().combat.current_index == 1, "the derived id makes a retry a replay"
    assert any(e["event_id"].endswith(f":advance:round1:{enemy_id}:0") for e in _load().combat.events)


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


def test_a_cited_advance_that_moved_the_turn_counts_whoever_it_moved():
    _battle(first_enemy=False)
    state = _load()
    before = turn_resolution.gameplay_snapshot(state)
    after = turn_resolution.gameplay_snapshot(state)
    after["combat"]["current_index"] = (before["combat"]["current_index"] + 1) % len(before["combat"]["order"])
    event = {"name": "advance_combat_turn", "arguments": {"actor_id": "x"}, "result": {"ok": True},
             "inventory_before": {}, "gameplay_before": before, "gameplay_after": after}
    assert turn_resolution._mutation_evidence(state, [event], ["tool:1"], "調查員p1") == (True, False)
    stuck = {**event, "gameplay_after": before}
    assert turn_resolution._mutation_evidence(state, [stuck], ["tool:1"], "調查員p1") == (False, False)
    # A player's message that gets a stuck enemy or ally turn out of the way is still what happened this turn.
    enemy_turn = turn_resolution.gameplay_snapshot(state)
    enemy_turn["combat"]["current_index"] = next(
        i for i, c in enumerate(enemy_turn["combat"]["order"]) if c["side"] == "enemy")
    moved = {**event, "gameplay_before": enemy_turn, "gameplay_after": before}  # back to the investigator
    assert turn_resolution._mutation_evidence(state, [moved], ["tool:1"], "調查員p1") == (True, False)


def test_an_actor_moved_back_in_the_same_round_advances_again_instead_of_replaying():
    _battle("p1", "p2", first_enemy=False)  # p1 current
    first = _tool("advance_combat_turn", {"actor_id": "調查員p1", "skip": True})
    assert first["ok"] and _load().combat.order[_load().combat.current_index].name != "調查員p1"
    state = _load()  # the Keeper re-orders initiative so p1 is current again this round
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.name == "調查員p1")
    _save(state)
    # The engine still counts the earlier skip as this round's action, so the turn is ended without another skip.
    again = _tool("advance_combat_turn", {"actor_id": "調查員p1"})
    assert again["ok"], again
    assert _load().combat.order[_load().combat.current_index].name != "調查員p1", "a real advance, not a replay"
    ids = [e["event_id"] for e in _load().combat.events if ":advance:round1:pc:char:p1:" in e["event_id"]]
    assert ids and ids[-1].endswith(":1")


def test_the_actor_reference_ignores_case_and_whitespace():
    _battle()
    state = _load()
    assert combat.resolve_actor_reference(state, " cultist ") is state.combat.order[state.combat.current_index]
    assert combat.resolve_actor_reference(state, "") is None


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
    # A skip by a shared name could not be told from its own retry once the first Guard is skipped: the tool
    # asks for the exact combatant id (and gives it), and the skip by id then derives its own event id.
    by_name = _tool("advance_combat_turn", {"actor_id": "Guard", "skip": True})
    assert not by_name["ok"] and second in by_name["error"]
    skipped = _tool("advance_combat_turn", {"actor_id": second, "skip": True})
    assert skipped["ok"], skipped
    assert any(e["event_id"].endswith(f":advance:round1:{second}:0") for e in _load().combat.events)
    assert _load().combat.order[_load().combat.current_index].combatant_id != second


def test_an_owed_con_check_tells_the_keeper_not_to_advance():
    block = prompt_config.build_resolved_check_outcome_block(
        {"combat_receipt": {"combat_id": "c1", "action_id": "a", "phase": "INJURY_CHECK", "completed": False}})
    assert "不要推進回合" in block


def test_a_settled_attack_ends_the_turn_and_plays_the_enemy_to_the_defence_choice():
    _battle(first_enemy=False)  # the investigator is up
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    declared = _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                               "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    assert declared["ok"] and declared["phase"] == "PLAYER_ROLL"
    # brawl 60 rolls 10 (Hard); the Cultist's dodge 20 rolls 90 (fail); its claw then rolls 20 on the next turn
    outcome, _ = _player("/coc check", [10, 90, 20])
    assert outcome.should_finalize
    state = _load()
    swing = state.combat.actions["swing"]
    assert swing["completed"] and swing["receipt"]["auto_advanced"]["next_actor"] == "Cultist"
    assert swing["receipt"]["auto_advanced"]["phase"] == "PLAYER_CHOICE" and state.combat.phase == "PLAYER_CHOICE"
    assert state.pending_checks["p1"]["type"] == "choice", "the enemy's turn already reached the player's defence"
    assert sum(1 for e in state.combat.events if e["event_id"].endswith(":advance:auto:swing")) == 1
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["auto_advanced"]["next_actor"] == "Cultist"
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": receipt})
    assert "引擎已自動推進" in block and "不要再呼叫 advance_combat_turn" in block
    again = _tool("advance_combat_turn", {"actor_id": "調查員p1"})
    assert not again["ok"], "the turn already moved; a second advance is refused, not applied"


def test_a_settled_attack_does_not_advance_once_every_enemy_is_down():
    _battle(first_enemy=False, enemy_hp=1)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    outcome, _ = _player("/coc check", [10, 90])  # the hit lands; the enemy's dodge fails
    assert outcome.resolved_event["combat_receipt"]["settlement_ready"] is True
    state = _load()
    assert next(c for c in state.combat.order if c.side == "enemy").defeated
    receipt = state.combat.actions["swing"]["receipt"]
    assert "auto_advanced" not in receipt and receipt["settlement_ready"] is True
    assert state.combat.order[state.combat.current_index].name == "調查員p1" and state.combat.phase == "READY"
    assert "戰鬥可以結算" in combat.status_text(state)
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": {
        "combat_id": state.combat.combat_id, "completed": True, "settlement_ready": True}})
    assert "preview_combat_settlement" in block and "先呼叫 advance_combat_turn" not in block


def test_a_settled_enemy_attack_ends_the_enemys_turn():
    _battle()
    _enemy_turn([20])
    outcome, _ = _player("/coc check 閃避", [90])  # the claw hits
    state = _load()
    assert outcome.should_finalize and state.combat.order[state.combat.current_index].is_pc
    assert outcome.resolved_event["combat_receipt"]["auto_advanced"]["next_actor"] == "調查員p1"


def test_auto_advance_can_be_switched_off():
    _battle()
    _enemy_turn([20])
    with patch.object(config, "COMBAT_AUTO_ADVANCE", False):
        _player("/coc check 閃避", [90])
    state = _load()
    assert state.combat.order[state.combat.current_index].side == "enemy"
    assert "auto_advanced" not in next(a for a in state.combat.actions.values() if a.get("npc_attack_id"))["receipt"]


def test_a_spear_attack_uses_the_base_skill_when_the_sheet_has_none():
    _battle(first_enemy=False)
    state = _load()
    state.characters["p1"].carried_items.append("一把長矛")
    _save(state)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    declared = _tool("declare_combat_action", {"action_id": "thrust", "actor_id": "調查員p1",
                                               "target_id": enemy.combatant_id, "weapon_reference": "長矛"})
    assert declared["ok"] and declared["phase"] == "PLAYER_ROLL", declared
    action = _load().combat.actions["thrust"]
    assert (action["skill"], action["skill_value"], action["damage"]) == ("格鬥（矛）", 20, "1d8+1")


def test_a_thrown_spear_takes_its_range_from_the_throwers_str():
    _battle(first_enemy=False)
    state = _load()
    state.characters["p1"].carried_items.append("投矛")
    state.characters["p1"].str_ = 60  # base range 12 yards
    _save(state)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    near = _tool("declare_combat_action", {"action_id": "throw", "actor_id": "調查員p1", "target_id": enemy.combatant_id,
                                           "weapon_reference": "投矛", "action_kind": "single_shot", "distance_yards": 10})
    assert near["ok"] and near["phase"] == "PLAYER_ROLL", near
    assert _load().combat.actions["throw"]["difficulty"] == "regular"


def test_a_look_up_does_not_spend_the_codex_action_budget():
    from app.providers import codex_provider
    assert not codex_provider.counts_against_tool_budget("search_scenario")
    assert not codex_provider.counts_against_tool_budget("get_weapon_definition")
    assert codex_provider.counts_against_tool_budget("initialize_combat")
    assert codex_provider.counts_against_tool_budget("declare_combat_action")


STAT_BLOCK = ("... The body of Walter Corbitt is buried in the basement ...\n\n### Walter Corbitt, Undead Fiend\n\n"
              "STR 90  CON 115  SIZ 55  INT 80\nPOW 90  DEX 35  APP 05  EDU 80\nHP: 16\nDamage bonus: +1D4\n"
              "Fighting 50% (Hard 25%/Extreme 10%), damage 1D3 + damage bonus\n")


def test_an_enemy_whose_stat_block_the_scenario_carries_gets_its_provenance_without_an_index_entry():
    from app.keeper_tools import support
    from app.models import GroupState
    state = GroupState(group_id="prov", active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                       scenario_text=STAT_BLOCK)
    given = {"attack_mode": "melee"}
    assert support.enemy_source(state, given, None, name="Walter Corbitt")["sha256"] == "abc123"
    assert support.enemy_source(state, given, None, name="walter corbitt")["url"] == "scenario:the-haunting"
    assert support.enemy_source(state, given, None, name="Corbitt 2")["sha256"] == "abc123"  # instance suffix
    assert support.enemy_source(state, given, None, name="Invented Thing") == given  # not in the scenario: a ruling
    assert support.enemy_source(state, given, None, name="W") == given  # one character proves nothing
    # The provenance vouches for the attack values the model copied: they must be written in the named block.
    copied = [{"id": "claw", "skill_value": 50, "damage": "1d3"}]
    assert support.enemy_source(state, given, None, name="Corbitt", attacks=copied)["sha256"] == "abc123"
    invented = [{"id": "claw", "skill_value": 75, "damage": "2d8"}]
    assert support.enemy_source(state, given, None, name="Corbitt", attacks=invented) == given
    assert support.enemy_source(state, given, None, name="Corbitt", attacks=[{"skill_value": 50, "damage": "2d8"}]) == given
    # An attack that leaves the skill or the damage out would be filled with the card's defaults: not copied either.
    for incomplete in ([{}], [{"skill_value": 50}], [{"damage": "1d3"}], [{"skill_value": "50", "damage": "1d3"}]):
        assert support.enemy_source(state, given, None, name="Corbitt", attacks=incomplete) == given, incomplete


def test_a_name_the_prose_only_mentions_or_that_stands_near_anothers_block_gets_no_provenance():
    from app.keeper_tools import support
    from app.models import GroupState
    prose = ("The landlord, Mr. Knott, remembers the Macarios well. A stray dog sleeps on the porch.\n"
             "A pirate captain's portrait hangs in the hall.\n")
    state = GroupState(group_id="prov", active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                       scenario_text=prose + STAT_BLOCK)  # Knott and the pirate sit right above Corbitt's block
    given = {"attack_mode": "melee"}
    assert support.enemy_source(state, given, None, name="Mr. Knott") == given
    assert support.enemy_source(state, given, None, name="rat") == given  # inside "pirate", and not a block's title
    assert support.enemy_source(state, given, None, name="Corbitt")["sha256"] == "abc123"  # the block's own title
    assert support.enemy_source(state, given, None, name="Undead Fiend")["sha256"] == "abc123"  # as the title calls him
    # The same name with its own block, in Chinese or English, is the scenario's enemy.
    state.scenario_text = prose + "\n### 鼠群\n\n力量 35  體質 55  體型 35  敏捷 70\n生命值：9\n格鬥 40%，傷害 1D3\n"
    assert support.enemy_source(state, given, None, name="鼠群（左）")["sha256"] == "abc123"
    state.scenario_text = prose + "\n### RAT PACK\n\nSTR 35  CON 55  SIZ 35  POW 50  DEX 70\nHP: 9\n"
    assert support.enemy_source(state, given, None, name="Rat Pack 2")["sha256"] == "abc123"
    assert support.enemy_source(state, given, None, name="rats") == given  # not a word of the title


def test_the_sheet_s_own_spelling_of_a_weapon_skill_beats_the_base_chance():
    from app import combat_flow
    from app.models import Character
    assert combat_flow._skill(Character(name="A", owner_id="u", skills={"手槍": 60}), "firearms-handgun") == ("射擊（手槍）", 60)
    assert combat_flow._skill(Character(name="A", owner_id="u", skills={}), "firearms-handgun") == ("射擊（手槍）", 20)


def test_an_advance_that_could_not_move_is_reported_as_blocked_not_advanced():
    from app import combat_flow
    _battle(first_enemy=False)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    with patch.object(combat_flow, "advance_combat", return_value={"ok": True, "pending": True, "phase": "INJURY_CHECK"}):
        outcome, _ = _player("/coc check", [10, 90])
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["auto_advance_error"] and not receipt.get("auto_advanced")
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": receipt})
    assert "無法自動推進" in block and "advance_combat_turn" in block


def test_the_bullwhip_is_a_melee_weapon_now():
    whip = combat_rules.resolve_weapon("皮鞭").definition
    assert (whip.attack_mode, whip.base_range_yards, whip.damage, whip.db_policy) == ("melee", None, "1d3", "half")


def test_a_rolled_back_advance_is_reported_as_blocked_even_though_the_objects_changed():
    from copy import deepcopy

    from app import combat_flow
    _battle(first_enemy=False)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})

    def rolled_back(state, **_kwargs):  # what combat._all_or_nothing does: the same battle, fresh objects
        state.combat.order = deepcopy(state.combat.order)
        return {"ok": False, "error": "已有待處理檢定", "blocked_by": "pending_check"}

    with patch.object(combat_flow, "advance_combat", side_effect=rolled_back):
        outcome, _ = _player("/coc check", [10, 90])
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["auto_advance_error"] == "已有待處理檢定" and not receipt.get("auto_advanced")


def test_a_replayed_enemy_plan_or_ruling_receipt_is_not_the_turns_effect():
    _battle()
    state = _load()
    before = turn_resolution.gameplay_snapshot(state)
    replayed = {"name": "run_enemy_combat_plan", "arguments": {"plan_id": "p"}, "inventory_before": {},
                "result": {"ok": True, "combat_id": state.combat.combat_id, "completed": True},
                "gameplay_before": before, "gameplay_after": before}
    assert turn_resolution._mutation_evidence(state, [replayed], ["tool:1"], "調查員p1") == (False, False)
    after = turn_resolution.gameplay_snapshot(state)
    after["combat"]["actions"]["npc:p"] = {"completed": True}
    played = {**replayed, "gameplay_after": after}
    assert turn_resolution._mutation_evidence(state, [played], ["tool:1"], "調查員p1") == (True, False)
    ruling = {"name": "resolve_combat_ruling", "arguments": {}, "inventory_before": {},
              "result": {"ok": True, "combat_id": state.combat.combat_id},
              "gameplay_before": before, "gameplay_after": before}
    assert turn_resolution._mutation_evidence(state, [ruling], ["tool:1"], "調查員p1") == (False, False)
    assert turn_resolution._mutation_evidence(state, [{**ruling, "gameplay_after": after}], ["tool:1"], "調查員p1") == (True, False)


def test_only_a_provider_that_budgets_actions_gets_the_search_rounds_on_top():
    from app.agents import executor
    from app.providers import anthropic_provider, codex_provider
    assert executor.tool_iterations(codex_provider) == config.MAX_TOOL_ITERATIONS + config.SCENARIO_SEARCH_MAX_PER_TURN
    assert executor.tool_iterations(anthropic_provider) == config.MAX_TOOL_ITERATIONS


def test_no_defence_against_a_shot_leaves_the_wounds_con_check_to_the_players_own_click():
    _battle(claw_damage="1d6")
    state = _load()
    enemy = next(c for c in state.combat.order if c.side == "enemy")
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.source.update(attack_mode="single_shot", distance_yards=10, base_range_yards=20)
    card.attacks[0].range_band = "near"
    card.attacks[0].ammo_or_uses = 3
    _save(state)
    run, _ = _enemy_turn([])  # the shot waits for the defender's choice
    assert run["phase"] == "PLAYER_CHOICE"
    assert [o["kind"] for o in _load().pending_checks["p1"]["options"]] == ["dive", "no_defense"]
    outcome, script = _player("/coc check 不閃躲", [20], damage=6)  # the shot hits for 6: a major wound
    assert script.rolls_taken == 1, "only the shot was rolled; the CON check is the player's"
    state = _load()
    assert state.combat.phase == "INJURY_CHECK" and state.pending_checks["p1"]["skill"] == "CON"
    assert "CON" in outcome.reply_text and not outcome.should_finalize
    survived, _ = _player("/coc check", [10])
    assert survived.should_finalize and "p1" not in _load().pending_checks


def test_the_defence_the_choice_registered_is_still_rolled_in_the_same_click():
    _battle()
    _enemy_turn([20])
    outcome, script = _player("/coc check 閃避", [20])
    assert outcome.should_finalize and script.rolls_taken == 1


def test_a_solo_investigators_settled_defence_lets_the_next_enemy_reach_their_choice():
    """The settled check is gone before the engine plays the next enemy, so a second enemy attacking the same
    investigator reaches the defence choice instead of a stale-check refusal."""
    _battle("p1", enemies=(("Cultist", 90), ("Thug", 85)))
    run, _ = _enemy_turn([20])
    assert run["phase"] == "PLAYER_CHOICE"
    outcome, _ = _player("/coc check 閃避", [20, 20])  # the dodge ties; then the Thug's claw rolls 20
    assert outcome.should_finalize
    state = _load()
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["auto_advanced"]["next_actor"] == "Thug" and receipt["auto_advanced"]["phase"] == "PLAYER_CHOICE"
    assert state.pending_checks["p1"]["type"] == "choice" and state.combat.phase == "PLAYER_CHOICE"
    assert all(t["ok"] for t in receipt["auto_advanced"]["enemy_turns"])


def test_a_typed_option_is_matched_exactly_before_by_containment_so_a_negation_is_not_its_opposite():
    from app.checks import narration
    options = [{"kind": "dive", "label": "閃躲", "skill": "閃避"}, {"kind": "no_defense", "label": "不閃躲", "skill": ""}]
    assert narration.match_choice_option(options, "不閃躲")["kind"] == "no_defense"
    assert narration.match_choice_option(options, "閃躲")["kind"] == "dive"
    assert narration.match_choice_option(options, "閃避")["kind"] == "dive"
    assert narration.match_choice_option(options, "dive")["kind"] == "dive"
    assert narration.match_choice_option(options, "閃") is None  # names both: ask, do not pick the first
    assert narration.match_choice_option(options, "") is None


def test_a_heading_over_an_hp_line_alone_is_not_a_stat_block():
    from app.keeper_tools import support
    from app.models import GroupState
    state = GroupState(group_id="prov", active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                       scenario_text="### Rat\n\nHP 10\n\nIt bites.\n")
    given = {"attack_mode": "melee"}
    assert support.enemy_source(state, given, None, name="Rat") == given
    state.scenario_text = "### Rat\n\nSTR 35\nCON 55\nHP 10\n"  # one characteristic per line still is a block
    assert support.enemy_source(state, given, None, name="Rat")["sha256"] == "abc123"


def test_a_torch_hit_hands_the_keeper_the_burn_the_table_states():
    _battle(first_enemy=False)
    state = _load()
    state.characters["p1"].carried_items.append("火把")
    _save(state)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    declared = _tool("declare_combat_action", {"action_id": "torch", "actor_id": "調查員p1",
                                               "target_id": enemy.combatant_id, "weapon_reference": "火把"})
    assert declared["ok"], declared
    outcome, _ = _player("/coc check", [10, 90, 20])  # the swing lands; the enemy's claw follows
    result = _load().combat.actions["torch"]["result"]
    assert result["hit"] and "著火" in result["follow_up"]
    receipt = outcome.resolved_event["combat_receipt"]
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": receipt})
    assert "【武器後續】" in block and "著火" in block


def test_a_crossbow_fired_last_round_is_still_being_reloaded_this_round():
    from app import combat_flow
    from tests.test_combat_flow import battle
    state, _pc, enemy = battle(weapons={"Crossbow": {"ammo": 1, "ammo_max": 1}})
    state.combat.actions["shot1"] = {"action_id": "shot1", "actor_id": "pc:pc1", "target_id": enemy.combatant_id,
                                     "completed": True, "round": 1, "weapon": {"id": "i.weapon.crossbow"}}
    state.combat.round_number = 2
    refused = combat_flow.declare_action(state, action_id="shot2", actor_id="pc:pc1", target_id=enemy.combatant_id,
                                         weapon_reference="Crossbow", action_kind="single_shot", distance_yards=10)
    assert not refused["ok"] and "round 3" in refused["error"] and "shot2" not in state.combat.actions
    assert state.combat.phase != "NEEDS_RULING", "a reload is not a ruling: the fight goes on"
    state.combat.round_number = 3
    allowed = combat_flow.declare_action(state, action_id="shot3", actor_id="pc:pc1", target_id=enemy.combatant_id,
                                         weapon_reference="Crossbow", action_kind="single_shot", distance_yards=10)
    assert allowed["ok"], allowed
