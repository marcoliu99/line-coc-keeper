"""Acceptance scenarios B1–B9 for the combat engine, plus the engine's own contract.

Real SQLite through the state transaction, scripted dice through ``app.dice``,
and the public doors a battle is driven through: the Keeper's combat tools, the
player's ``/coc check`` and ``CombatEngine.handle``. B10 (dependency direction
and a clean import in a fresh process) lives in ``test_architecture_combat.py``.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

from app import combat, combat_flow, combat_resources, config, db, dice, tool_dispatch
from app.commands.handlers import checks as check_commands
from app.models import Character, Combatant, CombatState, GroupState
from app.repositories import group_state, state_transaction
from app.services import combat_actions as act
from app.services import combat_engine
from app.services.combat_engine import Mode
from tests import combat_calls
from tests.check_dice import ScriptedDice, module_dice


@pytest.fixture(autouse=True)
def explicit_advance():
    """These scenarios drive initiative by hand; the engine's own advance after a settled action is covered in
    tests/test_combat_turn_friction.py."""
    with patch.object(config, "COMBAT_AUTO_ADVANCE", False):
        yield


SOURCE = {
    "url": "https://example.test/scenario", "revision": "reviewed-v1", "sha256": "abc",
    "attack_mode": "melee", "extreme_rule": "maximum",
}
GROUP = "b-engine"


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()


def _investigator(owner: str, name: str, dex: int, **extra: Any) -> Character:
    return Character(
        name, owner, character_id=f"char:{owner}", hp=10, hp_max=10, san=50, luck=0, mp=10, dex=dex,
        skills={"格鬥（鬥毆）": 60, "閃避": 40, "firearms-handgun": 50},
        weapons=extra.pop("weapons", {}), **extra,
    )


def _save(state: GroupState) -> None:
    state_transaction.mutate_value(state.group_id, lambda ctx: ctx.replace_state(state), reason="test_setup")


def _load() -> GroupState:
    return group_state.load_state(GROUP)


def _battle(
    *players: str, enemies: tuple[tuple[str, int], ...] = (("Cultist", 90),), enemy_hp: int = 20,
    claw_damage: str = "1d3", armor: list[dict[str, Any]] | None = None, weapons: dict[str, Any] | None = None,
    first_enemy: bool = True,
) -> GroupState:
    """A managed battle: ``players`` (owner ids, descending DEX) against reviewed enemies."""
    owners = players or ("p1",)
    characters = {
        owner: _investigator(owner, f"調查員{owner}", 80 - 10 * index, weapons=deepcopy(weapons or {}))
        for index, owner in enumerate(owners)
    }
    state = GroupState(
        GROUP, active=True, timeline_id="timeline-b", characters=characters,
        characters_by_id={c.character_id: c for c in characters.values()},
        active_character_id_by_user={owner: c.character_id for owner, c in characters.items()},
    )
    combat_engine.handle(state, act.Start())
    for name, dex in enemies:
        combat_engine.handle(state, act.AddCombatant(
            name=name, dex=dex, hp=enemy_hp, source=SOURCE, skills={"dodge": 20}, armor=armor,
            attacks=[{"id": "claw", "skill_value": 50, "damage": claw_damage, "range_band": "engaged"}],
        ))
    if first_enemy:
        state.combat.current_index = 0
    _save(state)
    return _load()


def _tool(name: str, arguments: dict[str, Any], actor: str = "p1") -> dict[str, Any]:
    return tool_dispatch.execute_tool(_load(), name, arguments, [], [], actor_id=actor)


def _hp(owner: str = "p1") -> int:
    state = _load()
    return combat_resources.effective_character(state, state.characters[owner]).hp


def _enemy_turn(rolls: list[int], *, damage: int = 2) -> tuple[dict[str, Any], ScriptedDice]:
    """Plan and run the current enemy's attack up to the defender's choice."""
    script = ScriptedDice(rolls)
    with module_dice(script), patch.object(dice.random, "randint", return_value=damage):
        plan = _tool("plan_enemy_turn", {})
        assert plan["ok"], plan
        run = _tool("run_enemy_combat_plan", {"plan_id": plan["plan_id"]})
    return run, script


def _player(text: str, rolls: list[int] | None = None, *, owner: str = "p1", damage: int = 2):
    script = ScriptedDice(rolls or [])
    with module_dice(script), patch.object(dice.random, "randint", return_value=damage):
        outcome = check_commands.resolve_check(GROUP, owner, text)
    return outcome, script


# ---------------------------------------------------------------- B1


def test_b1_a_dodge_that_ties_the_attack_goes_to_the_defender():
    _battle()
    run, _ = _enemy_turn([20])  # claw 50: Hard
    assert run["phase"] == "PLAYER_CHOICE"
    outcome, script = _player("/coc check 閃避", [20])  # one click chooses and rolls; Dodge 40: Hard as well
    assert outcome.should_finalize and script.rolls_taken == 1
    action = next(a for a in _load().combat.actions.values() if a.get("npc_attack_id"))
    assert action["result"] == {"hit": False, "opposed": "tie_defender_wins"}
    assert _hp() == 10


def test_b1_a_fight_back_that_ties_the_attack_goes_to_the_attacker():
    _battle()
    _enemy_turn([20])
    _player("/coc check 反擊", [30])  # brawl 60: Hard, the same tier as the claw
    action = next(a for a in _load().combat.actions.values() if a.get("npc_attack_id"))
    assert action["result"]["opposed"] == "tie_attacker_wins"
    assert _hp() == 8


def test_b1_a_fight_back_that_beats_a_failed_attack_hurts_the_enemy():
    _battle()
    _enemy_turn([90])
    _player("/coc check 反擊", [5])
    state = _load()
    enemy = next(p for p in state.combat.order if p.side == "enemy")
    assert enemy.hp == 18
    assert _hp() == 10


# ---------------------------------------------------------------- B2


def _shoot(rolls: list[int], *, action_id: str = "shot:1", ammo: int = 7):
    weapons = {".45 Automatic": {"ammo": ammo, "ammo_max": 7}}
    state = _load()
    enemy = next(p for p in state.combat.order if p.side == "enemy")
    script = ScriptedDice(rolls)
    with module_dice(script), patch.object(dice.random, "randint", return_value=2):
        declared = _tool("declare_combat_action", {
            "action_id": action_id, "actor_id": "pc:char:p1", "target_id": enemy.combatant_id,
            "weapon_reference": ".45 Automatic", "action_kind": "single_shot", "distance_yards": 5,
        })
    return declared, script, weapons


def test_b2_a_shot_spends_one_round_and_a_retry_spends_none():
    _battle(weapons={".45 Automatic": {"ammo": 7, "ammo_max": 7}}, first_enemy=False)
    declared, script, _ = _shoot([90])  # the cultist's dive at 20% fails
    assert declared["ok"] and declared["phase"] == "PLAYER_ROLL" and script.rolls_taken == 1
    outcome, script = _player("/coc check", [40])  # Ada's shot at 50% hits
    assert outcome.should_finalize and script.rolls_taken == 1
    state = _load()
    assert combat_resources.effective_character(state, state.characters["p1"]).weapons[".45 Automatic"]["ammo"] == 6
    assert state.characters["p1"].weapons[".45 Automatic"]["ammo"] == 7, "the sheet stays committed until settlement"

    again = ScriptedDice([])
    with module_dice(again):
        replay = _tool("run_combat_action", {"action_id": "shot:1"})
        redeclared, _, _ = _shoot([], action_id="shot:1")
    assert again.rolls_taken == 0
    assert replay["completed"] and redeclared["completed"]
    after = _load()
    assert combat_resources.effective_character(after, after.characters["p1"]).weapons[".45 Automatic"]["ammo"] == 6


def test_b2_an_empty_magazine_is_refused_before_any_roll_without_pausing_the_fight():
    """An empty gun is the player's to deal with (reload, or do something else); the fight does not wait on a ruling."""
    _battle(weapons={".45 Automatic": {"ammo": 0, "ammo_max": 7}}, first_enemy=False)
    declared, script, _ = _shoot([])
    assert not declared["ok"] and "沒有子彈了" in declared["error"] and "裝填" in declared["error"]
    assert script.rolls_taken == 0
    state = _load()
    assert state.combat.phase == "READY" and not [a for a in state.combat.actions if not a.startswith("system:")], \
        "nothing declared, nothing to rule on"


def test_b2_an_empty_gun_declared_without_a_distance_is_refused_not_paused_for_the_distance():
    _battle(weapons={".45 Automatic": {"ammo": 0, "ammo_max": 7}}, first_enemy=False)
    state = _load()
    enemy = next(p for p in state.combat.order if p.side == "enemy")
    declared = combat_engine.handle(state, act.Declare(
        action_id="shot-no-range", actor_id="調查員p1", target_id=enemy.combatant_id,
        weapon_reference=".45 Automatic", action_kind="single_shot", distance_yards=None,
    ))
    assert not declared["ok"] and "沒有子彈了" in declared["error"], declared
    assert state.combat.phase == "READY" and "shot-no-range" not in state.combat.actions
    assert combat_resources.effective_character(state, state.characters["p1"]).weapons[".45 Automatic"]["ammo"] == 0


def test_b2_armour_reduces_a_hit_and_a_final_amount_ignores_it():
    state = _battle(armor=[{"label": "皮甲", "value": 2, "applies_to": "all"}], enemy_hp=20)
    enemy = next(p for p in state.combat.order if p.side == "enemy")
    hit = combat_engine.handle(state, act.ApplyDamage(
        target=enemy.combatant_id, raw_damage=5, event_id="armour:1", source_id="test",
    ))
    final = combat_engine.handle(state, act.ApplyDamage(
        target=enemy.combatant_id, raw_damage=5, event_id="armour:2", source_id="test", bypass_armor=True,
    ))
    assert (hit["final_damage"], hit["hp_after"]) == (3, 17)
    assert (final["final_damage"], final["hp_after"]) == (5, 12)
    retry = combat_engine.handle(state, act.ApplyDamage(
        target=enemy.combatant_id, raw_damage=5, event_id="armour:1", source_id="test",
    ))
    assert retry == hit and enemy.hp == 12, "the same entry id is never settled twice"


def test_b2_a_malfunction_still_costs_the_round_and_deals_no_damage():
    _battle(weapons={".45 Automatic": {"ammo": 7, "ammo_max": 7}}, first_enemy=False)
    declared, _, _ = _shoot([90])
    assert declared["ok"]
    outcome, _ = _player("/coc check", [100])  # a 100 reaches this weapon's malfunction number
    state = _load()
    action = state.combat.actions["shot:1"]
    assert outcome.should_finalize and action["result"] == {"hit": False, "malfunction": True}
    effective = combat_resources.effective_character(state, state.characters["p1"])
    assert effective.weapons[".45 Automatic"]["ammo"] == 6
    assert any(tag.startswith("武器故障") for tag in effective.status_tags)
    assert next(p for p in state.combat.order if p.side == "enemy").hp == 20


# ---------------------------------------------------------------- B3


def test_b3_an_npc_attack_that_needs_a_defender_waits_and_settles_nothing():
    _battle()
    run, script = _enemy_turn([20])
    state = _load()
    assert run["phase"] == "PLAYER_CHOICE" and not run["completed"]
    assert script.rolls_taken == 1, "only the attacker rolled; the defence is the player's"
    assert state.pending_checks["p1"]["type"] == "choice"
    assert _hp() == 10
    assert not any(e["kind"] == "damage" for e in state.combat.events)
    assert state.combat.interaction["owner_id"] == "p1"


# ---------------------------------------------------------------- B4


def test_b4_the_same_choice_twice_replays_the_first_answer():
    _battle()
    run, _ = _enemy_turn([20])
    arguments = {"interaction_id": run["interaction"]["interaction_id"], "choice": "dodge"}
    first = _tool("submit_combat_choice", arguments)
    revision = _load().state_revision
    second = _tool("submit_combat_choice", arguments)
    assert first["ok"] and second == first
    assert _load().state_revision == revision, "the repeat wrote nothing"
    assert _load().combat.phase == "PLAYER_ROLL"


def test_b4_a_double_clicked_roll_deals_damage_and_ends_the_action_once():
    _battle()
    _enemy_turn([20])
    script = ScriptedDice([90])  # Dodge fails: the claw hits
    barrier = threading.Barrier(2)

    def click():  # the choice button rolls, so a double click is two concurrent choices
        barrier.wait()
        return check_commands.resolve_check(GROUP, "p1", "/coc check 閃避")

    # Patched once around both clicks: entering the same patch from two threads
    # would let the second one restore the first one's mock as "the original".
    with module_dice(script), patch.object(dice.random, "randint", return_value=2), \
            ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in [pool.submit(click), pool.submit(click)]]
    assert sorted(outcome.should_finalize for outcome in outcomes) == [False, True]
    state = _load()
    assert script.rolls_taken == 1
    assert _hp() == 8
    assert sum(1 for e in state.combat.events if e["kind"] == "damage") == 1
    advanced = _tool("advance_combat_turn", {"actor_id": "enemy:" + state.combat.order[0].enemy_card_id,
                                             "event_id": "advance:1"})
    assert advanced["ok"]
    assert _load().combat.current_index == 1, "the turn moved on exactly once"


# ---------------------------------------------------------------- B5


def test_b5_two_investigators_and_two_enemies_keep_every_action_with_its_owner():
    _battle("p1", "p2", enemies=(("Cultist", 90), ("Ghoul", 60)))
    with patch.object(combat.random, "choice", side_effect=lambda seq: seq[0]):
        run, _ = _enemy_turn([20])
    state = _load()
    defender = run["interaction"]["owner_id"]
    other = "p2" if defender == "p1" else "p1"
    assert set(state.pending_checks) == {defender}, "only the targeted investigator is asked"

    refused, script = _player("/coc check", owner=other)
    assert not refused.should_finalize and refused.reply_text and script.rolls_taken == 0
    stranger = _tool("submit_combat_choice", {
        "interaction_id": run["interaction"]["interaction_id"], "choice": "dodge",
    }, actor=other)
    assert not stranger["ok"]

    _player("/coc check 閃避", [90], owner=defender)
    after = _load()
    assert _hp(defender) == 8
    assert _hp(other) == 10
    assert after.characters[other].hp == 10 and set(after.combat.working_resources) == {
        "char:p1", "char:p2",
    }


# ---------------------------------------------------------------- B6


def test_b6_a_major_wound_waits_for_the_con_check_and_blocks_the_advance():
    _battle(claw_damage="1d6")
    _enemy_turn([20], damage=6)
    outcome, _ = _player("/coc check 閃避", [90], damage=6)
    state = _load()
    assert outcome.should_finalize
    assert state.combat.phase == "INJURY_CHECK" and state.pending_checks["p1"]["skill"] == "CON"
    assert _hp() == 4

    blocked = _tool("advance_combat_turn", {"actor_id": "x", "event_id": "advance:blocked"})
    assert not blocked["ok"], "the turn does not move while the CON check is owed"
    assert _load().combat.current_index == 0

    survived, _ = _player("/coc check", [10])
    assert survived.should_finalize
    after = _load()
    assert after.combat.phase == "READY" and "p1" not in after.pending_checks
    assert not combat_resources.effective_character(after, after.characters["p1"]).injury.get("dead")


def test_b6_a_lethal_hit_kills_without_a_con_check():
    state = _battle()
    investigator = state.characters["p1"]
    hit = combat_engine.handle(state, act.SingleHit(
        character=investigator, damage=10, event_id="lethal:1", reason="test",
    ))
    assert hit["ok"] and not hit["major_wound_triggered"]
    assert combat_resources.effective_character(state, investigator).injury["dead"] is True
    assert "p1" not in state.pending_checks


def test_b6_an_investigator_put_down_while_an_attack_waits_for_the_roll_cannot_finish_it():
    _battle(first_enemy=False)
    enemy = next(p for p in _load().combat.order if p.side == "enemy")
    declared = _tool("declare_combat_action", {
        "action_id": "melee:1", "actor_id": "pc:char:p1", "target_id": enemy.combatant_id,
        "weapon_reference": "unarmed", "action_kind": "melee",
    })
    assert declared["ok"] and declared["phase"] == "PLAYER_ROLL"
    state = _load()
    combat_engine.handle(state, act.SingleHit(
        character=state.characters["p1"], damage=10, event_id="down:1", reason="test",
    ))
    _save(state)
    outcome, script = _player("/coc check", [10])
    assert not outcome.should_finalize and "incapacitated" in outcome.reply_text
    assert script.rolls_taken == 0 and "p1" in _load().pending_checks
    assert not _load().combat.actions["melee:1"]["completed"]


# ---------------------------------------------------------------- B7


def _unsupported_battle() -> GroupState:
    """An active battle saved by a combat format that no longer exists (no working-resource pipeline)."""
    investigator = _investigator("p1", "調查員p1", 80)
    state = GroupState(
        GROUP, active=True, timeline_id="timeline-b", characters={"p1": investigator},
        characters_by_id={investigator.character_id: investigator},
    )
    state.combat = CombatState(
        active=True, round_number=1, current_index=0,
        order=[Combatant(name=investigator.name, display_name=investigator.name, character_id=investigator.character_id,
                         combatant_id="pc:" + investigator.character_id, is_pc=True, dex=80, hp=10, hp_max=10)],
    )
    card = combat.create_enemy_card(
        state, "Cultist", dex=90, hp=20,
        attacks=[{"id": "claw", "skill_value": 50, "damage": "1d3", "range_band": "engaged"}], source=SOURCE,
    )
    combat.add_enemy_card_to_combat(state, card.id)
    state.combat.current_index = 0
    return GroupState.from_dict(state.to_dict())


def test_b7_an_unsupported_battle_is_refused_by_every_action_and_never_converted():
    state = _unsupported_battle()
    before = deepcopy(state.to_dict())
    for action in (
        act.Start(), act.Status(), act.PlanEnemy(), act.Advance(), act.Run("x"),
        act.ApplyDamage(target="Cultist", raw_damage=3), act.Declare(
            action_id="a", actor_id="pc:char:p1", target_id="e", weapon_reference="unarmed",
        ),
    ):
        with pytest.raises(combat_resources.CombatAdmissionError, match="Start a new combat"):
            combat_engine.handle(state, action)
    assert state.to_dict() == before
    assert not combat_resources.is_managed(state)


def test_b7_every_new_battle_started_through_the_engine_is_managed():
    state = GroupState(GROUP, active=True, timeline_id="timeline-b")
    investigator = _investigator("p1", "調查員p1", 80)
    state.characters["p1"] = investigator
    state.characters_by_id[investigator.character_id] = investigator
    state.set_active_character("p1", investigator.character_id)
    combat_engine.handle(state, act.Start())
    assert state.combat.active and combat_resources.is_managed(state)
    assert combat_engine.mode_of(state) is Mode.MANAGED


def test_b7_an_unmanaged_state_never_falls_back_to_another_implementation():
    unsupported = _unsupported_battle()
    with pytest.raises(AssertionError, match="not started through"):
        combat_calls.ops_for(unsupported)
    with pytest.raises(AssertionError, match="not started through"):
        combat_calls.ops_for(GroupState(GROUP))
    assert combat_calls.ops_for(_battle()) is combat_flow.MANAGED_OPS
    assert not hasattr(combat, "LEGACY_OPS") and not hasattr(act, "CloseLegacy")


def test_b7_a_managed_battle_survives_a_save_and_continues():
    _battle()
    _enemy_turn([20])
    restored = _load()
    assert combat_engine.mode_of(restored) is Mode.MANAGED
    assert restored.combat.phase == "PLAYER_CHOICE"
    outcome, _ = _player("/coc check 閃避", [90])
    assert outcome.should_finalize and _hp() == 8


def test_b7_a_control_left_over_from_a_battle_that_is_gone_is_refused_and_rolls_nothing():
    _battle()
    _enemy_turn([20])
    state = _load()
    stale = deepcopy(state.pending_checks["p1"])
    state.combat = CombatState()  # the battle is no longer there; the player's control still is
    _save(state)
    assert combat_engine.mode_of(_load()) is Mode.IDLE
    verdict = combat_engine.handle(_load(), act.ValidatePending(pending=stale, owner_id="p1"))
    assert verdict["ok"] is False
    outcome, script = _player("/coc check")
    assert not outcome.should_finalize and outcome.reply_text and script.rolls_taken == 0
    assert "p1" in _load().pending_checks, "refusing leaves the control exactly where it was"


# ---------------------------------------------------------------- B8


def test_b8_an_explicit_hit_and_the_same_entry_sent_again_settle_once():
    state = _battle()
    first = _tool("adjust_character", {
        "investigator": "調查員p1", "field": "hp", "delta": -3, "event_id": "hit:1", "reason": "falling beam",
    })
    assert first["ok"] and first["value"] == 7
    again = combat_engine.handle(_load(), act.ApplyDamage(
        target="pc:char:p1", raw_damage=3, event_id="hit:1", source_id="falling beam", bypass_armor=True,
    ))
    assert again["ok"] and again["hp_after"] == 7
    assert _hp() == 7
    assert sum(1 for e in _load().combat.events if e["event_id"] == "hit:1") == 1
    assert state.characters["p1"].hp == 10


def test_b8_the_raw_damage_tools_cannot_settle_anything_while_a_battle_runs():
    _battle()
    for name, arguments in (  # retired: a managed battle refused them and nothing else used them
        ("apply_combat_damage", {"target": "Cultist", "raw_damage": 5}),
        ("apply_final_combat_damage", {"target": "Cultist", "final_damage": 5}),
        ("damage_combatant", {"name": "Cultist", "delta": -5}),
    ):
        refused = _tool(name, arguments)
        assert not refused["ok"] and "未知工具" in refused["error"]
    state = _load()
    assert next(p for p in state.combat.order if p.side == "enemy").hp == 20


def test_b8_a_repeated_declaration_returns_the_stored_receipt():
    _battle(first_enemy=False)
    enemy = next(p for p in _load().combat.order if p.side == "enemy")
    declaration = {
        "action_id": "melee:1", "actor_id": "pc:char:p1", "target_id": enemy.combatant_id,
        "weapon_reference": "unarmed", "action_kind": "melee",
    }
    first = _tool("declare_combat_action", declaration)
    revision = _load().state_revision
    second = _tool("declare_combat_action", declaration)
    assert first["ok"] and second == first
    assert _load().state_revision == revision
    clash = _tool("declare_combat_action", {**declaration, "target_id": "pc:char:p1"})
    assert not clash["ok"]


# ---------------------------------------------------------------- B9


def test_b9_a_failed_commit_leaves_the_battle_untouched_and_the_retry_applies_once():
    _battle(weapons={".45 Automatic": {"ammo": 7, "ammo_max": 7}}, first_enemy=False)
    declared, _, _ = _shoot([90])
    assert declared["ok"]
    before = _load().to_dict()
    real_write = group_state.write_state_tx

    def failing_write(*args, **kwargs):
        raise OSError("disk full")

    script = ScriptedDice([40])
    with module_dice(script), patch.object(dice.random, "randint", return_value=2), \
            patch.object(group_state, "write_state_tx", failing_write), pytest.raises(OSError):
        check_commands.resolve_check(GROUP, "p1", "/coc check")
    assert _load().to_dict() == before, "nothing of the shot is visible after a failed commit"

    with patch.object(group_state, "write_state_tx", real_write):
        outcome, _ = _player("/coc check", [40])
    assert outcome.should_finalize
    state = _load()
    assert combat_resources.effective_character(state, state.characters["p1"]).weapons[".45 Automatic"]["ammo"] == 6
    assert sum(1 for e in state.combat.events if e["kind"] == "ammo") == 1


# ---------------------------------------------------------------- the engine's contract


def test_the_mode_is_read_from_the_battle():
    assert combat_engine.mode_of(GroupState(GROUP)) is Mode.IDLE
    assert combat_engine.mode_of(_battle()) is Mode.MANAGED
    with pytest.raises(combat_resources.CombatAdmissionError, match="unsupported legacy combat format"):
        combat_engine.mode_of(_unsupported_battle())


def test_a_managed_action_on_an_idle_conversation_refuses_instead_of_inventing_a_battle():
    state = GroupState(GROUP, active=True, timeline_id="timeline-b")
    investigator = _investigator("p1", "調查員p1", 80)
    for action in (
        act.Run("a1"),
        act.Declare(action_id="a1", actor_id="x", target_id="y", weapon_reference="unarmed"),
        act.RunEffect("e1"),
        act.SingleHit(character=investigator, damage=1, event_id="h1", reason="r"),
        act.SetInitiative(combat_id="", actor_ids=[], event_id="e", reason="r"),
        act.ApplyDamage(target="x", raw_damage=3),
    ):
        assert combat_engine.handle(state, action) == {"ok": False, "error": combat_engine.NO_BATTLE}
    assert not state.combat.active and not state.combat.combat_id


def test_a_damage_string_that_is_not_dice_is_refused_without_starting_a_battle():
    state = GroupState(GROUP, active=True, timeline_id="timeline-b")
    with pytest.raises(ValueError, match="extreme_rule"):
        combat_engine.handle(state, act.AddCombatant(
            name="Knife", dex=50, hp=5, attacks=[{"id": "slash", "damage": "1D4+2；極限成功 6+1D4+2"}],
        ))
    assert not state.combat.active and not state.combat.combat_id
    bad = [{"id": "slash", "damage": "1D4+2；極限成功 6+1D4+2"}]
    for create in (
        lambda: combat.add_npc(state, "Knife", 50, 5, attacks=bad),
        lambda: combat.create_enemy_card(state, "Knife", attacks=bad),
    ):
        with pytest.raises(ValueError, match="extreme_rule"):
            create()
        assert not state.combat.active and not state.combat.combat_id


def test_the_tools_do_not_fabricate_a_battle_when_none_is_running():
    state = GroupState(GROUP, active=True, timeline_id="timeline-b")
    _save(state)
    result = _tool("run_combat_action", {"action_id": "a1"})
    assert result["ok"] is False
    assert not _load().combat.active and not _load().combat.combat_id


def test_administrative_actions_must_name_the_current_battle_and_give_a_reason():
    state = _battle()
    with pytest.raises(ValueError, match="current combat_id"):
        combat_engine.handle(state, act.Rollback(combat_id="combat:other", event_id="e", reason="r"))
    with pytest.raises(ValueError, match="reason"):
        combat_engine.handle(state, act.Rollback(combat_id=state.combat.combat_id, event_id="e", reason=" "))


def test_the_managed_rules_refuse_an_unsupported_battle():
    with pytest.raises(combat.ModeMismatch):
        combat.advance_turn(_unsupported_battle(), ops=combat_flow.MANAGED_OPS)


def test_an_unknown_action_is_a_type_error():
    class Mystery(act.Action[None]):
        pass

    with pytest.raises(TypeError):
        combat_engine.handle(GroupState(GROUP), Mystery())


def test_every_action_has_a_handler():
    import inspect

    declared = {
        cls for _, cls in inspect.getmembers(act, inspect.isclass)
        if issubclass(cls, act.Action) and cls is not act.Action
    }
    assert declared == set(combat_engine._HANDLERS)


def test_a_carried_melee_item_can_be_used_as_a_weapon():
    state = _battle(first_enemy=False)
    state.characters["p1"].carried_items = ["警棍"]
    _save(state)
    enemy = next(p for p in _load().combat.order if p.side == "enemy")
    baton = _tool("declare_combat_action", {
        "action_id": "baton:1", "actor_id": "pc:char:p1", "target_id": enemy.combatant_id, "weapon_reference": "警棍",
    })
    assert baton["ok"] and baton["phase"] == "PLAYER_ROLL", baton
    assert _load().combat.actions["baton:1"]["weapon"]["id"] == "i.weapon.club-small"


def test_a_carried_item_that_contains_the_weapon_name_counts_as_that_weapon():
    state = _battle(first_enemy=False)
    state.characters["p1"].carried_items = ["老舊警棍"]
    _save(state)
    enemy = next(p for p in _load().combat.order if p.side == "enemy")
    declared = _tool("declare_combat_action", {
        "action_id": "baton:2", "actor_id": "pc:char:p1", "target_id": enemy.combatant_id, "weapon_reference": "警棍",
    })
    assert declared["ok"] and declared["phase"] == "PLAYER_ROLL", declared


def test_a_critical_attack_leaves_the_defender_no_fight_back_option():
    _battle()
    run, _ = _enemy_turn([1])  # claw 50: a roll of 1 is a Critical
    assert run["phase"] == "PLAYER_CHOICE"
    assert [o["kind"] for o in _load().pending_checks["p1"]["options"]] == ["dodge"]
    outcome, _ = _player("/coc check 閃避", [20])  # Dodge 40 cannot reach a Critical
    assert outcome.should_finalize
    action = next(a for a in _load().combat.actions.values() if a.get("npc_attack_id"))
    assert action["result"]["hit"] is True


def test_a_non_critical_attack_still_offers_dodge_and_fight_back():
    _battle()
    run, _ = _enemy_turn([20])
    assert run["phase"] == "PLAYER_CHOICE"
    assert [o["kind"] for o in _load().pending_checks["p1"]["options"]] == ["dodge", "counter"]


def test_b2_a_ruling_that_maps_the_shot_onto_an_empty_gun_refuses_it_instead_of_pausing_again():
    _battle(weapons={".45 Automatic": {"ammo": 0, "ammo_max": 7}}, first_enemy=False)
    state = _load()
    enemy = next(p for p in state.combat.order if p.side == "enemy")
    paused = combat_engine.handle(state, act.Declare(
        action_id="shot-unmapped", actor_id="調查員p1", target_id=enemy.combatant_id,
        weapon_reference="some odd pistol", action_kind="single_shot", distance_yards=5,
    ))
    assert paused["phase"] == "NEEDS_RULING", paused
    resumed = combat_engine.handle(state, act.Rule(
        combat_id=state.combat.combat_id, action_id="shot-unmapped", event_id="ruling:map", reason="it is the .45",
        decision="resume", weapon_reference=".45 Automatic", distance_yards=5,
    ))
    assert not resumed["ok"] and "沒有子彈了" in resumed["error"], resumed
    assert state.combat.phase == "READY" and "shot-unmapped" not in state.combat.actions
