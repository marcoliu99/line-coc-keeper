"""One click defends, a turn can always be ended, and the Keeper is told the one thing to do next
(docs/specs/enhancement/combat_turn_friction_design_spec.md). Built on the B-scenario helpers."""
from __future__ import annotations

from unittest.mock import patch

from app import combat, combat_flow, combat_resources, combat_rules, config, dice
from app.agents import tool_gateway
from app.commands.handlers import combat as combat_command
from app.discord_transport import controls
from app.keeper_tools import resource_bridge
from app.services import combat_actions as act
from app.services import combat_engine, prompt_config, turn_delivery, turn_resolution
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


def test_an_enemy_the_engine_cannot_play_gives_up_its_turn_when_the_turn_reaches_it():
    # The Haunting soak (2026-10-09): Corbitt, then his floating knife, registered without attacks; every one of the
    # next 60-odd lines retried run_enemy_combat_plan on that turn and the fight never moved again.
    _battle("p1", enemies=(("Cultist", 90),), first_enemy=False)
    _hp_only_enemy("Thing", dex=75)  # acts right after the investigator
    state = _load()
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.is_pc)
    _save(state)
    advanced = _tool("advance_combat_turn", {"actor_id": "調查員p1", "skip": True})
    assert advanced["ok"], advanced
    given_up = advanced["skipped_enemy_turns"]
    assert [g["name"] for g in given_up] == ["Thing"] and "add_npc_to_combat" in given_up[0]["hint"]
    assert _load().combat.order[_load().combat.current_index].name != "Thing"
    assert "p1" in _load().pending_checks, "the Cultist after it played its turn: the investigator is to defend"


def test_registering_the_skipped_enemy_again_with_attacks_lets_it_act():
    _battle("p1", enemies=(("Cultist", 90),), first_enemy=False)
    _hp_only_enemy("Thing", dex=75)
    thing = next(c for c in _load().combat.order if c.name == "Thing")
    with patch.object(dice.random, "randint", return_value=4):
        added = _tool("add_npc_to_combat", {"name": "Thing", "dex": 75, "hp": 5,
                                            "attacks": [{"name": "Claw", "skill_value": 40, "damage": "1D4"}],
                                            "armor": [{"label": "ward", "value": "2D6", "depletes": True}]})
    assert "已補上" in str(added), added
    assert "有護甲（暗擲，數值不公開）" in str(added) and "8" not in str(added.get("note", "")), \
        "the registration says the armor is there, never its value"
    ward = combat.card_for(_load(), thing).armor
    assert [(a.value, a.rolled_from, a.depletes) for a in ward] == [(8, "2D6", True)], "the armor comes with the attacks"
    state = _load()
    assert [c.name for c in state.combat.order].count("Thing") == 1, "the same enemy, not a second one"
    card = combat.card_for(state, next(c for c in state.combat.order if c.name == "Thing"))
    assert card is not None and not card.incomplete and [(a.skill_value, a.damage) for a in card.attacks] == [(40, "1D4")]
    assert combat.enemy_turn_blocker(state, thing) == ""
    again = _tool("add_npc_to_combat", {"name": "Thing", "dex": 75, "hp": 5,
                                        "attacks": [{"name": "Bite", "skill_value": 90, "damage": "1D8"}]})
    assert "已補上" not in str(again), "a complete card is not rewritten by a later registration"
    assert [a.skill_value for a in combat.card_for(_load(), thing).attacks] == [40]


def test_the_follow_up_after_a_settled_roll_is_told_which_enemy_gave_up_its_turn():
    step = prompt_config._combat_turn_step({"auto_advanced": {
        "next_actor": "調查員p1", "round_now": 2, "phase": "READY",
        "skipped_enemy_turns": [{"name": "柯比特操縱的匕首", "reason": "x", "hint": "y"}]}})
    assert "柯比特操縱的匕首" in step and "不要敘事它出手" in step and "initialize_combat" in step


def test_a_fight_that_opens_on_an_enemy_the_engine_cannot_play_starts_with_the_next_turn():
    from app.models import GroupState
    from tests.test_combat_engine import _investigator

    character = _investigator("p1", "調查員p1", 50)
    _save(GroupState(GROUP, active=True, characters={"p1": character},
                     characters_by_id={character.character_id: character},
                     active_character_id_by_user={"p1": character.character_id}))
    started = _tool("initialize_combat", {"enemies": [{"name": "柯比特操縱的匕首", "hp": 1, "dex": 99}]})
    assert started["ok"], started
    assert started["opening_enemy_turn"]["skipped_enemy_turns"][0]["name"] == "柯比特操縱的匕首"
    assert _load().combat.order[_load().combat.current_index].is_pc, "the investigator acts instead of a stall"


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
    assert "結算" in block and "先呼叫 advance_combat_turn" not in block


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
    copied = [{"id": "claw", "skill_value": 50, "damage": "1d3"}]
    invented = [{"id": "claw", "skill_value": 75, "damage": "2d8"}]
    # By name: the block's heading names the enemy, whatever attack values the Keeper wrote down.
    assert support.enemy_source(state, given, None, name="Walter Corbitt", attacks=copied)["sha256"] == "abc123"
    assert support.enemy_source(state, given, None, name="walter corbitt", attacks=invented)["url"] == "scenario:the-haunting"
    assert support.enemy_source(state, given, None, name="Corbitt 2", attacks=invented)["sha256"] == "abc123"  # instance suffix
    # By attack values: a name the heading does not carry (a translation) is found by what the block states.
    assert support.enemy_source(state, given, None, name="柯比特", attacks=copied)["sha256"] == "abc123"
    assert support.enemy_source(state, given, None, name="柯比特", attacks=invented) == given
    assert support.enemy_source(state, given, None, name="Invented Thing", attacks=invented) == given
    assert support.enemy_source(state, given, None, name="W", attacks=invented) == given  # one character proves nothing
    # No attack submitted means the card's default unarmed attack would ride on the provenance: none is granted.
    assert support.enemy_source(state, given, None, name="Walter Corbitt") == given
    assert support.enemy_source(state, given, None, name="Walter Corbitt", attacks=[]) == given
    assert support.enemy_source(state, given, None, name="柯比特", attacks=[{"skill_value": 50, "damage": "1d3+100"}]) == given
    # "1D3 + damage bonus(1D4)" is two dice: the Keeper may submit the weapon's die or both, in either spelling.
    for both in ("1d3+1d4", "1D3 + 1D4", "1d3"):
        assert support.enemy_source(state, given, None, name="柯比特", attacks=[{"skill_value": 50, "damage": both}])["sha256"] == "abc123", both
    zh = GroupState(group_id="prov", active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                    scenario_text="### 鼠群\n\n力量 35  體質 55\n格鬥 40%，傷害 1D3 + 傷害加值（-1）\n")
    assert support.enemy_source(zh, given, None, name="鼠群", attacks=[{"skill_value": 40, "damage": "1d3-1"}])["sha256"] == "abc123"
    zh.scenario_text = "### Rat Pack\n\nSTR 35  CON 55\nFighting 40%, damage 1D3 + damage bonus(-1)\n"
    assert support.enemy_source(zh, given, None, name="鼠群", attacks=[{"skill_value": 40, "damage": "1D3 - 1"}])["sha256"] == "abc123"
    # A number the block gives as a characteristic, not as an attack, proves no attack: CON 55 is not a 55% Laser.
    laser = GroupState(group_id="prov", active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                       scenario_text="### Thing\n\nSTR 40  CON 55  SIZ 60\nHP 11\nBite 30%, damage 1d3\n")
    assert support.enemy_source(laser, given, None, name="Laser", attacks=[{"skill_value": 55, "damage": "1d3"}]) == given
    assert support.enemy_source(laser, given, None, name="Laser", attacks=[{"skill_value": 30, "damage": "1d3"}])["sha256"] == "abc123"
    # An attack that leaves the skill or the damage out would be filled with the card's defaults, even by name.
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
    invented = [{"id": "bite", "skill_value": 75, "damage": "2d8"}]
    assert support.enemy_source(state, given, None, name="Mr. Knott", attacks=invented) == given
    assert support.enemy_source(state, given, None, name="rat", attacks=invented) == given  # inside "pirate", not a title
    assert support.enemy_source(state, given, None, name="Corbitt", attacks=invented)["sha256"] == "abc123"  # the block's title
    assert support.enemy_source(state, given, None, name="Undead Fiend", attacks=invented)["sha256"] == "abc123"  # as the title calls him
    # The same name with its own block, in Chinese or English, is the scenario's enemy.
    state.scenario_text = prose + "\n### 鼠群\n\n力量 35  體質 55  體型 35  敏捷 70\n生命值：9\n格鬥 40%，傷害 1D3\n"
    assert support.enemy_source(state, given, None, name="鼠群（左）", attacks=invented)["sha256"] == "abc123"
    state.scenario_text = prose + "\n### RAT PACK\n\nSTR 35  CON 55  SIZ 35  POW 50  DEX 70\nHP: 9\nFighting 40%, damage 1D3\n"
    assert support.enemy_source(state, given, None, name="Rat Pack 2", attacks=invented)["sha256"] == "abc123"
    assert support.enemy_source(state, given, None, name="rats", attacks=invented) == given  # not a word of the title
    # The soak run's 「鼠群」 against the English heading: the block's own values find it.
    bite = [{"id": "teeth_claws", "skill_value": 40, "damage": "1d3"}]
    assert support.enemy_source(state, given, None, name="鼠群", attacks=bite)["sha256"] == "abc123"


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
    assert "命中，調查員p1 受到 6 點傷害" in outcome.reply_text, "the player learns what the shot did before the CON roll"
    survived, _ = _player("/coc check", [10])
    assert survived.should_finalize and "p1" not in _load().pending_checks
    assert "命中，調查員p1 受到 6 點傷害" in survived.resolved_event["combat_receipt"]["blow"], "and so does the narrator"
    assert "⚔️" not in survived.roll_feedback_text, "the player is not told the same blow twice"


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
    bite = [{"id": "bite", "skill_value": 30, "damage": "1d3"}]
    assert support.enemy_source(state, given, None, name="Rat", attacks=bite) == given
    state.scenario_text = "### Rat\n\nSTR 35\nCON 55\nHP 10\nBite 30%, damage 1d3\n"  # one characteristic per line still is a block
    assert support.enemy_source(state, given, None, name="Rat", attacks=bite)["sha256"] == "abc123"


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
    # A paused shot with an unknown reference, then mapped to the crossbow by a ruling, cannot skip the reload either.
    paused = combat_flow.declare_action(state, action_id="shot2b", actor_id="pc:pc1", target_id=enemy.combatant_id,
                                        weapon_reference="the thing in my hands", action_kind="single_shot", distance_yards=10)
    assert not paused["ok"] and paused["phase"] == "NEEDS_RULING"
    mapped = combat_flow.resolve_ruling(state, action_id="shot2b", event_id="ruling:shot2b", reason="it is the crossbow",
                                        decision="resume", weapon_reference="Crossbow")
    assert not mapped["ok"] and "round 3" in mapped["error"] and state.combat.actions["shot2b"]["needs_ruling"]
    combat_flow.resolve_ruling(state, action_id="shot2b", event_id="cancel:shot2b", reason="reloading", decision="cancel")
    state.combat.round_number = 3
    allowed = combat_flow.declare_action(state, action_id="shot3", actor_id="pc:pc1", target_id=enemy.combatant_id,
                                         weapon_reference="Crossbow", action_kind="single_shot", distance_yards=10)
    assert allowed["ok"], allowed


def _declare_with_pack(items: list[str], weapon_reference: str, action_id: str, **extra) -> dict:
    _battle(first_enemy=False)
    state = _load()
    state.characters["p1"].carried_items.extend(items)
    _save(state)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    return _tool("declare_combat_action", {"action_id": action_id, "actor_id": "調查員p1",
                                           "target_id": enemy.combatant_id, "weapon_reference": weapon_reference,
                                           **extra})


def test_a_carried_shuriken_does_not_prove_a_sword():
    sword = _declare_with_pack(["手裏劍"], "劍", "sword")
    assert not sword["ok"] and sword["phase"] == "NEEDS_RULING" and "owned" in sword["error"]
    # Read the way the declaration is, the pack's "一把生鏽的小刀" is Knife, Small and "刀" names every knife.
    assert _declare_with_pack(["一把生鏽的小刀"], "小刀", "knife")["ok"]
    assert _declare_with_pack(["刀"], "小刀", "knife2")["ok"]
    # One spear is the thrusting and the thrown weapon: a carried 「長矛」 can be thrown as 「投矛」.
    declared = _declare_with_pack(["長矛"], "投矛", "throw", action_kind="single_shot", distance_yards=5)
    assert declared["ok"], declared
    # An entry the catalog knows is what it resolves to, even when another weapon's name is inside its text.
    sword = _declare_with_pack(["thrusting sword"], "Sword", "sword2")
    assert not sword["ok"] and sword["phase"] == "NEEDS_RULING"
    assert _declare_with_pack(["thrusting sword"], "rapier", "rapier")["ok"]


def test_a_skip_in_a_one_combatant_fight_needs_an_explicit_id_even_by_combatant_id():
    from app.keeper_tools import combat as combat_tools
    state = _battle()
    lone = state.combat.order[0]
    for other in state.combat.order[1:]:
        other.defeated = True
    assert combat_tools._skip_needs_explicit_id(state, lone, lone.combatant_id)
    assert combat_tools._skip_needs_explicit_id(state, lone, lone.name)



def test_no_defence_that_settles_the_shot_is_narrated_from_its_receipt():
    _battle()
    state = _load()
    enemy = next(c for c in state.combat.order if c.side == "enemy")
    card = state.combat.enemy_cards[enemy.enemy_card_id]
    card.source.update(attack_mode="single_shot", distance_yards=10, base_range_yards=20)
    card.attacks[0].range_band = "near"
    card.attacks[0].ammo_or_uses = 3
    _save(state)
    _enemy_turn([])
    outcome, script = _player("/coc check 不閃躲", [20], damage=2)  # the shot hits for 2: no wound check owed
    assert outcome.should_finalize and script.rolls_taken == 1
    event = outcome.resolved_event
    assert event["no_roll"] and event["combat_receipt"]["completed"]
    assert event["combat_receipt"]["auto_advanced"]["next_actor"] == "調查員p1", "the enemy's turn ended itself"
    block = prompt_config.build_resolved_check_outcome_block(event)
    assert "未擲骰" in block and "【戰鬥下一步】" in block
    after = _load()
    assert combat_resources.effective_character(after, after.characters["p1"]).hp == 8


def test_no_defence_is_narrated_even_when_the_next_enemy_already_waits_on_the_same_investigator():
    _battle("p1", enemies=(("Gunman", 90), ("Thug", 85)))
    state = _load()
    gunman = next(c for c in state.combat.order if c.name == "Gunman")
    card = state.combat.enemy_cards[gunman.enemy_card_id]
    card.source.update(attack_mode="single_shot", distance_yards=10, base_range_yards=20)
    card.attacks[0].range_band = "near"
    card.attacks[0].ammo_or_uses = 3
    _save(state)
    _enemy_turn([])
    outcome, _ = _player("/coc check 不閃躲", [20, 20], damage=2)  # the shot lands; the Thug's claw then rolls 20
    assert outcome.should_finalize, "the settled shot reaches narration although the Thug's attack now waits"
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["auto_advanced"]["next_actor"] == "Thug" and receipt["auto_advanced"]["phase"] == "PLAYER_CHOICE"
    assert _load().pending_checks["p1"]["type"] == "choice"


def test_a_stuck_enemy_turn_after_an_auto_advance_names_the_ruling_tool_the_narrator_has():
    from app.keeper_tools import registry
    assert "resolve_combat_ruling" in registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": {
        "combat_id": "c", "completed": True,
        "auto_advanced": {"next_actor": "Thing", "round_now": 2, "phase": "NEEDS_RULING",
                          "enemy_turn": {"ok": False, "error": "NPC special/movement plan requires an explicit ruling"}}}})
    assert "resolve_combat_ruling" in block and "取消" in block and "advance_combat_turn skip" in block


def test_a_firearm_registered_under_one_spelling_is_fired_by_its_chinese_alias():
    from app import combat_flow
    from tests.test_combat_flow import battle
    state, _pc, enemy = battle(weapons={"Crossbow": {"ammo": 1, "ammo_max": 1}})
    declared = combat_flow.declare_action(state, action_id="bolt", actor_id="pc:pc1", target_id=enemy.combatant_id,
                                          weapon_reference="十字弓", action_kind="single_shot", distance_yards=10)
    assert declared["ok"], declared
    assert state.combat.actions["bolt"]["ammo_key"] == "Crossbow"


def test_a_side_that_falls_during_the_auto_advance_makes_the_receipt_settlement_ready():
    from app import combat_flow
    _battle(first_enemy=False)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})

    def effect_kills_the_last_enemy(state, **_kwargs):  # a round-start burn finishing the enemy as the turn moves
        for combatant in state.combat.order:
            if combatant.side == "enemy":
                combatant.defeated = True
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.side == "enemy")
        return {"ok": True}

    with patch.object(combat_flow, "advance_combat", side_effect=effect_kills_the_last_enemy):
        outcome, _ = _player("/coc check", [10, 90])  # the swing misses: the enemy's dodge beats it
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["settlement_ready"] is True
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": receipt})
    assert "結算" in block and "現在輪到" not in block


def test_a_specialisation_written_the_way_players_write_it_is_the_investigators_own_value():
    from app import combat_flow
    from app.models import Character
    sheet = Character("調查員", "p", skills={"衝鋒槍": 60, "斧": 45, "格鬥": 70, "射擊": 55})
    assert combat_flow._skill(sheet, "firearms-submachine-gun") == ("射擊（衝鋒槍）", 60)
    assert combat_flow._skill(sheet, "fighting-axe") == ("格鬥（斧）", 45)
    # A bare family name names no specialisation: the sword is still at its base chance.
    assert combat_flow._skill(sheet, "fighting-sword") == ("格鬥（劍）", 20)


def test_an_enemy_without_a_listed_dodge_takes_the_blow_instead_of_fighting_back():
    # The soak run's rat pack: registered with attacks only and no reviewed source. It used to need counter
    # provenance and stalled every swing on a ruling; NPCs no longer Fight Back, so the swing just lands.
    _battle(first_enemy=False, enemy_hp=1)
    state = _load()
    card = next(iter(state.combat.enemy_cards.values()))
    card.skills, card.source = {}, {}
    _save(state)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    outcome, script = _player("/coc check", [10])  # one roll: the attack; the enemy does not defend
    assert outcome.should_finalize and script.rolls_taken == 1
    swing = _load().combat.actions["swing"]
    assert swing["completed"] and not swing.get("needs_ruling") and swing["defense_kind"] == "no_defense"
    assert next(c for c in _load().combat.order if c.side == "enemy").defeated


def test_a_roll_that_sets_off_the_scenarios_fight_can_start_it_in_the_follow_up():
    # The Haunting live run: finding the knife made it rise and strike, but the follow-up narrator had no tool to
    # start the fight, said so to the player, and the next turn's defence had nothing to defend against.
    from app.agents import tool_gateway
    from app.keeper_tools import registry
    assert "initialize_combat" in registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES
    assert "initialize_combat" in {t["name"] for t in tool_gateway.tools_for_speaker_role("player")}
    instruction = prompt_config.build_tool_enabled_narrator_static_prompt("", "resolved_check_followup")
    assert "initialize_combat" in instruction and "不要自己擲攻擊" in instruction


def test_a_fight_is_not_started_once_every_investigator_is_down():
    # The Haunting replay: Evelyn fell to 0 HP, the fight settled, and a later roll started a new one with Corbitt
    # alone; no player turn ever came, and 58 turns stalled on rulings and refused skips.
    _battle(first_enemy=False)
    state = _load()
    combat_resources.rollback_combat(state, event_id="test:rollback", reason="test")
    state.characters["p1"].hp = 0
    _save(state)
    corbitt = {"name": "Walter Corbitt", "dex": 35, "hp": 16,
               "attacks": [{"id": "knife", "skill_value": 90, "damage": "1d4+2"}]}
    refused = _tool("initialize_combat", {"enemies": [corbitt]})
    assert not refused["ok"] and "沒有人能參戰" in refused["error"] and not _load().combat.active
    alone = _tool("add_npc_to_combat", {"name": "Walter Corbitt", "dex": 35, "hp": 16})
    assert not alone["ok"] and not _load().combat.active
    # The engine refuses it on every path, not only the Keeper's two tools: a bare start, or the table's own command.
    import pytest
    with pytest.raises(combat_resources.CombatAdmissionError):
        combat_engine.handle(_load(), act.Start())
    state = _load()
    told = combat_command._apply_combat_command(state, ["/coc", "combat", "addnpc", "Walter Corbitt", "35", "16"])
    assert "沒有人能參戰" in told.text and not state.combat.active
    state = _load()
    state.characters["p1"].hp = 5
    _save(state)
    assert _tool("initialize_combat", {"enemies": [corbitt]})["ok"]


def test_corbitt_is_registered_with_his_floating_knife_and_attacks_with_it():
    # The Haunting soaks (2026-10-09): Corbitt registered without attacks gave up every turn. His block lists none
    # for the knife; the scenario has it roll his POW 90 against Dodge for 1D4+2, impaling on an Extreme success.
    from app.models import GroupState
    from tests.test_combat_engine import _investigator

    character = _investigator("p1", "調查員p1", 50)
    _save(GroupState(GROUP, active=True, characters={"p1": character},
                     characters_by_id={character.character_id: character},
                     active_character_id_by_user={"p1": character.character_id},
                     active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                     scenario_text=STAT_BLOCK))
    bare = {"name": "Walter Corbitt", "dex": 35, "hp": 16}
    refused = _tool("initialize_combat", {"enemies": [bare]})
    assert not refused["ok"] and "attacks" in refused["enemies"][0]["error"] and not _load().combat.active
    assert not _tool("add_npc_to_combat", bare)["ok"]
    assert not _tool("add_npc_to_combat", {**bare, "attacks": [{}], "abilities": [{}]})["ok"], "empty objects"
    state = _load()  # the scenario index lists his Chinese name as an alias of the block's English heading
    state.scenario_npc_index = [{"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 16}]
    _save(state)
    assert not _tool("add_npc_to_combat", {**bare, "name": "柯比特"})["ok"]
    knife = {"label": "浮空匕首", "skill_name": "POW", "skill_value": 90, "damage": "1D4+2", "tags": ["impale"]}
    # A second Corbitt in the same batch is a new instance, not a re-registration: it needs attacks too.
    batch = _tool("initialize_combat", {"enemies": [{**bare, "attacks": [knife]}, bare]})
    assert [e["ok"] for e in batch["enemies"]] == [True, False] and "attacks" in batch["enemies"][1]["error"]
    state = _load()
    assert [c.side for c in state.combat.order].count("enemy") == 1
    corbitt = next(c for c in state.combat.order if c.side == "enemy")
    card = combat.card_for(state, corbitt)
    assert card is not None and not card.incomplete and card.source["sha256"] == "abc123"
    assert card.attacks[0].tags == ["impale"] and combat.enemy_turn_blocker(state, corbitt) == ""
    assert state.combat.order[state.combat.current_index].is_pc, "DEX 50 acts before Corbitt's 35"
    # Her turn ends; Corbitt's knife attacks, and she is asked to defend against a 90.
    assert _tool("advance_combat_turn", {"actor_id": "調查員p1", "skip": True})["ok"]
    state = _load()
    assert state.pending_checks["p1"]["attacker_name"] == "Walter Corbitt"
    knife_attack = next(a for a in state.combat.actions.values() if a.get("npc_attack_id"))
    assert knife_attack["weapon"]["extreme_rule"] == "impale", "an Extreme hit impales: 6 + 1D4+2"


def test_an_enemy_attack_with_malformed_tags_still_attacks():
    _battle("p1", enemies=(("Cultist", 90),), first_enemy=False)
    state = _load()
    card = combat.card_for(state, next(c for c in state.combat.order if c.side == "enemy"))
    for tags in (None, "impale", 7):
        card.attacks[0].tags = tags  # type: ignore[assignment]  # what an unvalidated tool call can store
        _save(state)
        state = _load()
        state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.side == "enemy")
        _save(state)
        plan = _tool("plan_enemy_turn", {"enemy": "Cultist"})
        ran = _tool("run_enemy_combat_plan", {"plan_id": plan["plan_id"]})
        assert ran["ok"], (tags, ran)
        state = _load()
        assert state.pending_checks.get("p1"), tags
        state.pending_checks.clear()
        state.combat.actions.clear()
        state.combat.interaction = None
        state.combat.phase = "READY"
        card = combat.card_for(state, next(c for c in state.combat.order if c.side == "enemy"))


def test_the_blow_that_downs_the_last_investigator_settles_the_fight_and_ends_the_scenario():
    # The Haunting soak (2026-10-09): Evelyn fell at 0 HP and the Keeper took five more player lines to preview and
    # confirm the settlement; until then every line got "not fully handled".
    _battle(claw_damage="1d3")
    _enemy_turn([20])  # claw 50: Hard
    outcome, _ = _player("/coc check 閃避", [90], damage=10)  # the dodge fails; 10 damage takes all 10 HP
    state = _load()
    assert not state.combat.active, "settled at once, no preview or confirm left for the Keeper"
    assert not state.active and state.characters["p1"].hp == 0
    receipt = outcome.resolved_event["combat_receipt"]
    assert "劇本到此結束" in receipt["scenario_ended"]
    assert "尚未結算" not in outcome.roll_line and "戰鬥已結算" in outcome.roll_line
    step = prompt_config._combat_turn_step(receipt)
    assert "/coc newgame" in step and "不要再推進劇情" in step


def test_a_fight_with_an_investigator_still_standing_is_not_settled_for_the_keeper():
    _battle("p1", "p2")
    _enemy_turn([20])
    _player("/coc check 閃避", [90], damage=10)
    assert _load().combat.active, "p2 still stands: the fight goes on"


def test_a_party_the_keeper_already_downed_is_not_settled_by_a_rejected_step():
    # The Keeper's own HP changes leave the fight open for review; a later call the engine rejects settles nothing.
    _battle(first_enemy=False)
    for step, delta in enumerate((-4, -4, -2)):  # each blow under the major-wound threshold
        assert _tool("adjust_character", {"investigator": "調查員p1", "field": "hp", "delta": delta,
                                          "event_id": f"blow:{step}", "reason": "x"})["ok"]
    assert _load().combat.active
    rejected = _tool("advance_combat_turn", {"actor_id": "nobody"})
    assert not rejected["ok"] and _load().combat.active and _load().active


def test_only_a_party_down_at_zero_is_ready_to_settle_by_itself():
    # Settling must end the scenario: an investigator away is not counted, and with nobody present nothing settles.
    _battle(first_enemy=False)
    state = _load()
    assert not combat_engine._ready_to_settle_party_down(state)
    state.characters["p1"].away = True
    assert not combat_engine._ready_to_settle_party_down(state), "everyone away is not everyone down"
    state.characters["p1"].away = False
    combat_resources.adjust_resource(state, state.characters["p1"], "hp", -10, event_id="test:zero", reason="test")
    assert combat_engine._ready_to_settle_party_down(state)


def test_the_keeper_gets_an_enemys_whole_stat_block_by_name_or_indexed_alias():
    # The Haunting soaks (2026-10-09): the Keeper searched the scenario for Corbitt's attack until the per-turn search
    # cap ended the turn, three player lines in a row, before it registered him.
    from app.keeper_tools import registry
    from app.models import GroupState
    from app.providers import codex_provider

    state = GroupState(GROUP, active=True, active_scenario_source_hash="abc123", scenario_library_id="the-haunting",
                       scenario_text=STAT_BLOCK,
                       scenario_npc_index=[{"name": "Walter Corbitt", "aliases": ["柯比特"], "hp": 16}])
    _save(state)
    for name in ("Walter Corbitt", "柯比特", "corbitt"):
        found = _tool("get_enemy_stat_block", {"name": name})
        assert found["ok"] and found["heading"] == "Walter Corbitt, Undead Fiend", (name, found)
        assert "Fighting 50%" in found["stat_block"] and "STR 90" in found["stat_block"], "as the scenario writes it"
    missing = _tool("get_enemy_stat_block", {"name": "鼠群"})
    assert not missing["ok"] and missing["stat_block_headings"] == ["Walter Corbitt, Undead Fiend"]
    assert not codex_provider.counts_against_tool_budget("get_enemy_stat_block")
    assert "get_enemy_stat_block" in registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES


def _warded_enemy(value, *, depletes: bool = True) -> tuple:
    with patch.object(dice.random, "randint", return_value=4):
        state = _battle(armor=[{"id": "ward", "label": "Flesh Ward", "value": value, "depletes": depletes}])
    enemy = next(c for c in state.combat.order if c.side == "enemy")
    return state, enemy


def test_armor_written_as_dice_is_rolled_once_when_the_enemy_is_registered():
    """Corbitt's "Roll 2D6 for his armor": the engine rolls it at registration, out of the player's sight."""
    state, enemy = _warded_enemy("2D6")
    armor = state.combat.enemy_cards[enemy.enemy_card_id].armor[0]
    assert (armor.value, armor.rolled_from) == (8, "2D6")
    assert _load().combat.enemy_cards[enemy.enemy_card_id].armor[0].value == 8, "kept on the card, not rolled again"


def test_armor_that_wears_away_loses_what_it_absorbs():
    state, enemy = _warded_enemy("2D6")  # 8 points
    first = combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:1")
    assert first["final_damage"] == 0 and state.combat.enemy_cards[enemy.enemy_card_id].armor[0].value == 3
    second = combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:2")
    assert second["final_damage"] == 2 and state.combat.enemy_cards[enemy.enemy_card_id].armor[0].value == 0
    third = combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:3")
    assert third["final_damage"] == 5 and not third["armor_label"]
    replay = combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:1")
    assert replay == first and state.combat.enemy_cards[enemy.enemy_card_id].armor[0].value == 0, "a replay wears nothing"


def test_ordinary_armor_does_not_wear():
    state, enemy = _warded_enemy(3, depletes=False)
    combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:1")
    assert state.combat.enemy_cards[enemy.enemy_card_id].armor[0].value == 3


def test_armor_that_is_neither_a_number_nor_dice_is_refused():
    state = _battle()
    try:
        combat.create_enemy_card(state, "Thing", armor=[{"label": "hide", "value": "thick"}])
    except ValueError as error:
        assert "2D6" in str(error)
    else:
        raise AssertionError("a value that is not dice must be refused")


def test_the_keepers_secret_roll_is_not_shown_to_the_player():
    rolled = {"ok": True, "expression": "2d6", "total": 7}
    hidden = turn_delivery.observe_tool("roll_dice", rolled, 1, {"expression": "2d6", "secret": True})
    shown = turn_delivery.observe_tool("roll_dice", rolled, 1, {"expression": "2d6"})
    assert (hidden.public_text, hidden.audience) == ("", "internal")
    assert "總值 7" in shown.public_text and shown.audience == "public"
    secret = {**rolled, "secret": True}  # what roll_dice returns for a secret roll
    assert turn_delivery.observe_tool("roll_dice", secret, 1, {}).audience == "internal"
    fact = tool_gateway._describe_tool_call("roll_dice", secret)
    assert "7" not in fact and "不公開" in fact, "the narrator is not handed the number either"


def test_a_dodge_that_succeeds_below_the_attacks_tier_is_told_as_the_hit_it_is():
    """A Dodge that rolls a success can still be hit; the player and the narrator are told it landed, not dodged."""
    _battle()
    _enemy_turn([20])  # claw 50: Hard
    outcome, _ = _player("/coc check 閃避", [35])  # Dodge 40: a plain success, below the claw's Hard
    state = _load()
    assert combat_resources.effective_character(state, state.characters["p1"]).hp == 8
    line = "Cultist的攻擊「困難成功」對上調查員p1的閃避「一般成功」：命中，調查員p1 受到 2 點傷害。"
    assert line in outcome.roll_feedback_text
    receipt = outcome.resolved_event["combat_receipt"]
    assert receipt["blow"] == line
    block = prompt_config.build_resolved_check_outcome_block({"combat_receipt": receipt})
    assert "【這一擊的結果】" + line in block and "不代表躲開" in block


def test_a_dodge_that_keeps_the_blow_off_says_so():
    _battle()
    _enemy_turn([20])
    outcome, _ = _player("/coc check 閃避", [20])  # Hard as well: the tie goes to the Dodge
    assert "Cultist的攻擊「困難成功」對上調查員p1的閃避「困難成功」：這一擊沒有命中。" in outcome.roll_feedback_text


def test_the_luck_offer_on_a_defence_says_what_tier_keeps_the_blow_off():
    _battle()
    state = _load()
    combat_resources.adjust_resource(state, state.characters["p1"], "luck", 50, event_id="test:luck", reason="test")
    _save(state)
    _enemy_turn([20])  # claw 50: Hard
    outcome, _ = _player("/coc check 閃避", [35])  # a plain success: Luck could buy Hard
    assert "Cultist的攻擊是「困難成功」，要「困難成功」以上才躲得開。目前 Luck" in outcome.reply_text


def test_the_players_own_hit_names_what_it_did():
    _battle(first_enemy=False)
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    outcome, _ = _player("/coc check", [10, 90, 20])  # brawl Hard; the Cultist's dodge fails
    assert "閃避「失敗」：命中，Cultist 受到" in outcome.roll_feedback_text


def test_the_tier_a_defence_needs_follows_the_opposed_roll_rules():
    assert dice.defence_tier_needed("hard", is_counter=False) == "hard"  # a tied Dodge goes to the defender
    assert dice.defence_tier_needed("hard", is_counter=True) == "extreme"  # a tied Fight Back goes to the attacker
    assert dice.defence_tier_needed("fail", is_counter=True) == "regular"
    assert dice.defence_tier_needed("critical", is_counter=True) is None


def test_a_hit_the_armor_stops_entirely_is_not_called_partial():
    _battle(first_enemy=False, armor=[{"id": "hide", "label": "hide", "value": 5}])
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    outcome, _ = _player("/coc check", [30, 90, 20])  # brawl Hard; the Cultist's dodge fails; 1D3 can't pass 5
    assert "命中，但傷害全被 Cultist 的護甲擋下。" in outcome.roll_feedback_text
    assert "部分" not in outcome.roll_feedback_text


def test_a_hit_that_does_no_damage_before_armor_is_not_credited_to_the_armor():
    _battle(first_enemy=False, armor=[{"id": "hide", "label": "hide", "value": 5}])
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    _tool("declare_combat_action", {"action_id": "swing", "actor_id": "調查員p1",
                                    "target_id": enemy.combatant_id, "weapon_reference": "unarmed"})
    nothing = dice.WeaponDamageResult("1d3", "-1", dice.RollResult("1d3", [1], 0, 1), None, -1, 0)  # 1 - 1: no damage
    with patch.object(combat_flow.dice, "roll_weapon_damage", return_value=nothing):
        outcome, _ = _player("/coc check", [30, 90, 20])
    assert "命中，Cultist 受到 0 點傷害。" in outcome.roll_feedback_text and "護甲" not in outcome.roll_feedback_text


def test_a_dodged_blow_that_owes_a_con_roll_is_not_narrated_again_after_it():
    """The defence roll is narrated with the blow; the CON roll after it does not hand the narrator the same hit."""
    _battle(claw_damage="1d6")
    _enemy_turn([20])  # claw 50: Hard
    dodged, _ = _player("/coc check 閃避", [35], damage=6)  # a plain success: hit for 6 of 10 HP, a major wound
    assert "命中，調查員p1 受到 6 點傷害" in dodged.resolved_event["combat_receipt"]["blow"]
    assert _load().pending_checks["p1"]["skill"] == "CON"
    con, _ = _player("/coc check", [10])
    assert not con.resolved_event["combat_receipt"]["blow"]


def test_an_empty_gun_is_told_to_the_player_instead_of_a_tool_failure():
    from app.domain.models import MechanicResult, StateDelta, TurnResolution

    refused = {"ok": False, "provisional": True,  # what the managed declaration tool returns
               "error": "左輪沒有子彈了（剩 0 發）：這一槍開不出去。要先裝填（身上有子彈的話），或這一輪改做別的事。"}
    outcome = turn_delivery.observe_tool("declare_combat_action", refused, 1, {})
    assert outcome.audience == "public" and not outcome.success
    assert outcome.public_text == refused["error"], "nothing was declared, so nothing is provisional"
    result = MechanicResult(success=False, action_type="tool_calls", narrative_facts=[], state_delta=StateDelta(),
                            turn_resolution=TurnResolution(disposition="incomplete", validation_code="model_incomplete"),
                            observed_outcomes=[outcome], tool_calls=(("declare_combat_action", False),),
                            fallback_reason="tool_failure")
    text = prompt_config.enforce_mechanic_check_consistency("", result)
    assert text == refused["error"] and "工具" not in text


def test_each_hit_the_armor_stops_is_logged_for_the_keeper_not_shown(caplog):
    """A run's armor can be checked from its log afterwards: what each hit's armor stopped and what is left."""
    state, enemy = _warded_enemy("2D6")  # 8 points
    with caplog.at_level("INFO", logger="app.combat_flow"):
        first = combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:1")
        combat_flow.apply_managed_damage(state, enemy.combatant_id, 5, event_id="t:2")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("combat.armor")]
    assert "blocked=5 left=3" in lines[0] and "blocked=3 left=0" in lines[1], lines
    assert "Flesh Ward" not in first["public_summary"] and "3" not in first["public_summary"]
