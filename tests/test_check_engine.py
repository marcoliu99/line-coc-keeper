"""Acceptance scenarios C1–C11 for the check engine (``app.checks``).

Real SQLite through the state transaction, scripted dice through the dice port,
and the three doors a check can come through: ``/coc check`` text, a button
click (the same call with split feedback) and a Keeper tool in autoroll mode.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app import db, dice, observability, tool_dispatch
from app.checks import events as check_events
from app.checks import luck as luck_policy
from app.checks.models import CheckOutcome
from app.commands.handlers import checks as check_commands
from app.models import Character, GroupState
from app.repositories import group_state, state_transaction
from tests.check_dice import ScriptedDice, module_dice

SOURCE = (
    "if successful the player may attempt a Dodge roll to avoid being hit by the bed. "
    "If the investigator is struck by the bed, the fall costs the victim 1D6 + 2 hit points."
)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()


def _investigator(owner: str, name: str = "", *, luck: int = 50, san: int = 50, **skills: int) -> Character:
    return Character(
        name=name or f"調查員{owner}", owner_id=owner, hp=10, hp_max=10, san=san, luck=luck,
        skills=skills or {"偵查": 60, "閃避": 45},
    )


def _game(group: str, *owners: str, autoroll: bool = False, scenario_text: str = "", **character: Any) -> GroupState:
    state = GroupState(
        group_id=group, timeline_id="timeline-a", active=True, autoroll_checks=autoroll,
        scenario_text=scenario_text,
    )
    for owner in owners:
        state.characters[owner] = _investigator(owner, **character)
    return state


def _skill_pending(skill: str = "偵查", value: int = 60, **extra: Any) -> dict[str, Any]:
    return {
        "type": "skill", "skill": skill, "skill_value": value, "bonus_dice": 0, "penalty_dice": 0,
        "difficulty": "regular", **extra,
    }


def _store(state: GroupState) -> GroupState:
    group_state.save_state(state)
    return group_state.load_state(state.group_id)


def _load(group: str) -> GroupState:
    return group_state.load_state(group)


def _run_door(group: str, owner: str, text: str, *, split: bool) -> list[str]:
    """``handle_check_command`` end to end: roll, narration hand-off, event, delivery."""
    replies: list[str] = []

    async def reply(message: str) -> None:
        replies.append(message)

    with (
        patch.object(check_commands.supervisor, "run_turn", AsyncMock(return_value=("敘事", [], []))),
        patch.object(check_commands, "run_post_turn_maintenance_after_output", AsyncMock()),
    ):
        asyncio.run(check_commands.handle_check_command(
            group, owner, reply, AsyncMock(), AsyncMock(), AsyncMock(), text, split_roll_feedback=split,
        ))
    return replies


def _event_facts(group: str) -> dict[str, Any]:
    saved = _load(group)
    event = saved.resolved_check_events[-1]
    char = next(iter(saved.characters.values()))
    return {
        **{key: event[key] for key in ("skill", "skill_value", "roll", "difficulty", "outcome", "success")},
        "state_effects": event["state_effects"],
        "hp_san_luck": (char.hp, char.san, char.luck),
        "pending_checks": saved.pending_checks,
        "pending_luck": saved.pending_luck_decisions,
    }


# ---------------------------------------------------------------- C1


def test_c1_command_button_and_tool_settle_the_same_intent_identically():
    facts: dict[str, dict[str, Any]] = {}
    for door in ("command", "button", "tool"):
        group = f"c1-{door}"
        state = _game(group, "u1", autoroll=door == "tool", luck=0)
        if door != "tool":
            state.pending_checks["u1"] = _skill_pending()
        _store(state)
        script = ScriptedDice([30])
        with module_dice(script):
            if door == "tool":
                result = tool_dispatch.execute_tool(
                    _load(group), "skill_check", {"investigator": "調查員u1", "skill": "偵查"}, [], [],
                    speaker_role="player",
                )
                assert result["ok"] and result["resolved"]
            else:
                _run_door(group, "u1", "/coc check", split=door == "button")
        assert script.rolls_taken == 1
        facts[door] = _event_facts(group)
    assert facts["command"] == facts["button"] == facts["tool"]
    assert facts["command"]["outcome"] == "hard 成功"


def test_c1_luck_offer_is_the_same_for_every_door():
    offers: dict[str, Any] = {}
    for door in ("command", "button", "tool"):
        group = f"c1-luck-{door}"
        state = _game(group, "u1", autoroll=door == "tool", luck=50, 偵查=50)
        if door != "tool":
            state.pending_checks["u1"] = _skill_pending(value=50)
        _store(state)
        with module_dice(ScriptedDice([55])):
            if door == "tool":
                result = tool_dispatch.execute_tool(
                    _load(group), "skill_check", {"investigator": "調查員u1", "skill": "偵查"}, [], [],
                    speaker_role="player",
                )
                assert result["pending_luck"] is True
            else:
                _run_door(group, "u1", "/coc check", split=door == "button")
        saved = _load(group)
        decision = saved.pending_luck_decisions["u1"]
        offers[door] = (decision["original_tier"], decision["roll"], decision["options"], saved.characters["u1"].luck)
    assert offers["command"] == offers["button"] == offers["tool"]
    assert offers["command"][0] == "fail" and offers["command"][2], "a 55 against 50 is buyable"
    assert offers["command"][3] == 50, "offering Luck spends none of it"


# ---------------------------------------------------------------- C2


@pytest.mark.parametrize(("skill", "roll", "tier", "success"), [
    (60, 1, "critical", True),
    (60, 12, "extreme", True),
    (60, 13, "hard", True),
    (60, 30, "hard", True),
    (60, 31, "regular", True),
    (60, 60, "regular", True),
    (60, 61, "fail", False),
    (60, 95, "fail", False),
    (60, 96, "fail", False),
    (60, 100, "fumble", False),
    (40, 95, "fail", False),
    (40, 96, "fumble", False),
    (40, 100, "fumble", False),
    (50, 96, "fail", False),
])
def test_c2_tier_boundaries(skill, roll, tier, success):
    state = _game("c2", "u1", luck=0, 偵查=skill)
    state.pending_checks["u1"] = _skill_pending(value=skill)
    _store(state)
    script = ScriptedDice([roll])
    outcome = check_commands.resolve_check("c2", "u1", "/coc check", dice_port=script)
    assert outcome.should_finalize
    assert outcome.resolved_event["outcome"] == f"{tier} {'成功' if success else '失敗'}"


def test_c2_bonus_penalty_and_required_tier_reach_the_dice_unchanged():
    state = _game("c2-options", "u1", luck=0)
    state.pending_checks["u1"] = _skill_pending(bonus_dice=1, penalty_dice=0, difficulty="hard")
    _store(state)
    script = ScriptedDice([40])
    outcome = check_commands.resolve_check("c2-options", "u1", "/coc check", dice_port=script)
    assert script.skill_calls == [(60, {"bonus_dice": 1, "penalty_dice": 0, "required_tier": "hard"})]
    # 40 is a regular success for a 60% skill, but the task needs Hard: a failure, shown as one.
    assert outcome.resolved_event["success"] is False
    assert outcome.resolved_event["outcome"] == "regular 失敗"
    assert "至少「困難成功」" in outcome.roll_line


# ---------------------------------------------------------------- C3


def test_c3_autoroll_off_registers_a_pending_check_without_rolling_or_applying_anything():
    state = _game("c3", "u1", scenario_text=SOURCE)
    _store(state)
    plan = {
        "key": "bed:hit", "kind": "damage", "when": "failure", "damage_expression": "1d6+2",
        "damage_type": "impact", "source_quote": SOURCE,
    }
    script = ScriptedDice([])  # any roll fails the test
    with module_dice(script):
        result = tool_dispatch.execute_tool(
            _load("c3"), "skill_check", {"investigator": "調查員u1", "skill": "偵查", "consequences": [plan]},
            [], [], speaker_role="player",
        )
    saved = _load("c3")
    assert result["ok"] and result["pending"] and not result.get("resolved")
    assert script.rolls_taken == 0
    assert saved.pending_checks["u1"]["consequences"][0]["key"] == "bed:hit"
    assert saved.characters["u1"].hp == 10
    assert saved.resolved_check_events == [] and saved.check_consequence_origins == {}


# ---------------------------------------------------------------- C4


def test_c4_second_submission_of_a_consumed_pending_check_never_rolls_again():
    state = _game("c4", "u1", luck=0)
    state.pending_checks["u1"] = _skill_pending()
    _store(state)
    script = ScriptedDice([30])
    first = check_commands.resolve_check("c4", "u1", "/coc check", dice_port=script)
    second = check_commands.resolve_check("c4", "u1", "/coc check", dice_port=script)
    assert first.should_finalize and not second.should_finalize
    assert "目前沒有待處理" in second.reply_text
    assert script.rolls_taken == 1


def test_c4_concurrent_double_click_rolls_once():
    state = _game("c4-race", "u1", luck=0)
    state.pending_checks["u1"] = _skill_pending()
    _store(state)
    script = ScriptedDice([30])
    barrier = threading.Barrier(2)

    def click() -> CheckOutcome:
        barrier.wait()
        return check_commands.resolve_check("c4-race", "u1", "/coc check", dice_port=script)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in [pool.submit(click), pool.submit(click)]]
    assert sorted(outcome.should_finalize for outcome in outcomes) == [False, True]
    assert script.rolls_taken == 1
    assert _load("c4-race").pending_checks == {}


def test_c4_resent_luck_button_spends_once():
    state = _game("c4-luck", "u1", luck=50, 偵查=50)
    state.pending_checks["u1"] = _skill_pending(value=50)
    _store(state)
    check_commands.resolve_check("c4-luck", "u1", "/coc check", dice_port=ScriptedDice([55]))
    decision = _load("c4-luck").pending_luck_decisions["u1"]
    cost = next(option["cost"] for option in decision["options"] if option["tier"] == "regular")
    barrier = threading.Barrier(2)

    def click() -> CheckOutcome:
        barrier.wait()
        return check_commands.resolve_luck("c4-luck", "u1", "regular")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in [pool.submit(click), pool.submit(click)]]
    assert sorted(outcome.should_finalize for outcome in outcomes) == [False, True]
    saved = _load("c4-luck")
    assert saved.characters["u1"].luck == 50 - cost
    assert saved.pending_luck_decisions == {}


# ---------------------------------------------------------------- C5


def test_c5_sanity_check_never_offers_luck_and_a_luck_request_is_refused():
    state = _game("c5", "u1", luck=50, san=50)
    state.pending_checks["u1"] = {"type": "sanity", "loss_success": "0", "loss_failure": "1d4"}
    _store(state)
    outcome = check_commands.resolve_check(
        "c5", "u1", "/coc check", dice_port=ScriptedDice([90], san_losses=[3]),
    )
    assert outcome.should_finalize
    saved = _load("c5")
    assert saved.pending_luck_decisions == {}
    assert (saved.characters["u1"].san, saved.characters["u1"].luck) == (47, 50)

    refused = check_commands.resolve_luck("c5", "u1", "regular")
    assert "目前沒有待決定的 Luck" in refused.reply_text and not refused.should_finalize
    after = _load("c5").characters["u1"]
    assert (after.san, after.luck) == (47, 50)


@pytest.mark.parametrize(("kind", "pushed", "allow_luck", "allowed"), [
    ("skill", False, True, True),
    ("choice", False, True, True),
    ("major_wound", False, True, True),
    ("skill", True, True, False),
    ("sanity", False, True, False),
    ("madness", False, True, False),
    ("skill", False, False, False),
])
def test_c5_luck_policy_table(kind, pushed, allow_luck, allowed):
    assert luck_policy.luck_allowed(kind, pushed=pushed, allow_luck=allow_luck) is allowed


def test_c5_a_major_wound_con_check_outside_combat_keeps_offering_luck():
    """The CON check chained onto a non-combat major wound has always offered Luck.

    Only the injury checks the combat engine registers (``allow_luck: False``) are Luck-free;
    this pins the behaviour the policy documents, so a change to it has to be deliberate.
    """
    state = _game("c5-wound", "u1", luck=50, CON=50)
    state.pending_checks["u1"] = _skill_pending("CON", 50, major_wound_trigger=True)
    _store(state)

    outcome = check_commands.resolve_check("c5-wound", "u1", "/coc check", dice_port=ScriptedDice([55]))

    assert not outcome.should_finalize
    decision = _load("c5-wound").pending_luck_decisions["u1"]
    assert decision["major_wound_trigger"] is True
    assert decision["options"]


# ---------------------------------------------------------------- C6


def test_c6_valid_luck_spend_deducts_once_and_records_the_effect():
    state = _game("c6", "u1", luck=50, 偵查=50)
    state.pending_checks["u1"] = _skill_pending(value=50)
    _store(state)
    check_commands.resolve_check("c6", "u1", "/coc check", dice_port=ScriptedDice([55]))
    decision = _load("c6").pending_luck_decisions["u1"]
    cost = next(option["cost"] for option in decision["options"] if option["tier"] == "regular")

    settled = check_commands.resolve_luck("c6", "u1", "regular")
    check_events.persist_resolved_event("c6", settled.resolved_event)

    saved = _load("c6")
    event = saved.resolved_check_events[-1]
    assert saved.characters["u1"].luck == 50 - cost
    assert event["success"] is True and f"花費 Luck {cost}" in event["outcome"]
    assert event["state_effects"] == [{"field": "Luck", "before": 50, "after": 50 - cost, "delta": -cost}]


def test_c6_balance_spent_by_another_action_refuses_the_spend_without_a_partial_deduction():
    state = _game("c6-race", "u1", luck=50, 偵查=50)
    state.pending_checks["u1"] = _skill_pending(value=50)
    _store(state)
    check_commands.resolve_check("c6-race", "u1", "/coc check", dice_port=ScriptedDice([55]))

    def spend_elsewhere(ctx: state_transaction.TxContext) -> None:
        ctx.state.characters["u1"].luck = 2

    state_transaction.mutate_value("c6-race", spend_elsewhere, reason="test")
    revision = _load("c6-race").state_revision
    refused = check_commands.resolve_luck("c6-race", "u1", "regular")
    assert _load("c6-race").state_revision == revision, "a refusal writes nothing"

    assert "Luck 只有 2 點" in refused.reply_text and not refused.should_finalize
    saved = _load("c6-race")
    assert saved.characters["u1"].luck == 2
    assert saved.pending_luck_decisions["u1"], "the decision stays open for a cheaper choice"
    kept = check_commands.resolve_luck("c6-race", "u1", "skip")
    assert kept.should_finalize and _load("c6-race").characters["u1"].luck == 2


def test_c6_unknown_tier_is_refused_and_leaves_the_decision_untouched():
    state = _game("c6-bad", "u1", luck=50, 偵查=50)
    state.pending_checks["u1"] = _skill_pending(value=50)
    _store(state)
    check_commands.resolve_check("c6-bad", "u1", "/coc check", dice_port=ScriptedDice([55]))
    before = _load("c6-bad")
    refused = check_commands.resolve_luck("c6-bad", "u1", "extreme-plus")
    assert "這不是有效的選項" in refused.reply_text
    after = _load("c6-bad")
    assert after.pending_luck_decisions["u1"] == before.pending_luck_decisions["u1"]
    assert after.state_revision == before.state_revision, "a refusal writes nothing"


# ---------------------------------------------------------------- C7


def test_c7_settled_check_leads_to_a_new_check_and_non_combat_damage_with_the_original_event_kept():
    state = _game("c7", "u1", luck=0, scenario_text=SOURCE)
    state.pending_checks["u1"] = _skill_pending(consequences=[
        {"key": "bed:dodge", "kind": "check", "when": "success", "skill": "閃避", "difficulty": "regular",
         "source_quote": SOURCE},
        {"key": "bed:hit", "kind": "damage", "when": "failure", "damage_expression": "1d6+2",
         "damage_type": "impact", "source_quote": SOURCE},
    ])
    _store(state)
    spot = check_commands.resolve_check("c7", "u1", "/coc check", dice_port=ScriptedDice([20]))
    check_events.persist_consequence_origin("c7", spot.resolved_event)
    check_events.persist_resolved_event("c7", spot.resolved_event)
    original_id = spot.resolved_event["event_id"]

    created = tool_dispatch.execute_tool(_load("c7"), "create_triggered_check", {
        "investigator": "調查員u1", "skill": "閃避", "difficulty": "regular", "trigger_check_id": original_id,
        "trigger_event_id": original_id, "trigger_condition": "Spot Hidden success reveals the flying bed",
        "consequence_key": "bed:dodge", "action_context": "察覺床架襲來後閃避",
    }, [], [], actor_id="u1")
    assert created["ok"] and created["pending"]
    dodge = check_commands.resolve_check("c7", "u1", "/coc check", dice_port=ScriptedDice([90]))
    check_events.persist_resolved_event("c7", dodge.resolved_event)

    saved = _load("c7")
    ids = [event["event_id"] for event in saved.resolved_check_events]
    assert ids[0] == original_id and len(set(ids)) == 2, "a new check id; the original event stays"
    assert saved.resolved_check_events[1]["caused_by_check_id"] == original_id
    assert saved.resolved_check_events[1]["skill"] == "閃避"

    # A failed Dodge opens the damage consequence of the *original* rule on a fresh authorisation.
    saved.check_consequence_origins[original_id]["success"] = False
    _store(saved)
    request = {
        "investigator": "調查員u1", "damage_expression": "1d6+2", "damage_type": "impact",
        "source_check_id": original_id, "source_event_id": original_id,
        "consequence_key": "bed:hit", "cause": "The bed throws the investigator",
    }
    with patch("app.dice.random.randint", return_value=2):
        first = tool_dispatch.execute_tool(_load("c7"), "apply_resolved_check_damage", request, [], [], actor_id="u1")
    with patch("app.dice.random.randint", side_effect=AssertionError("damage rerolled")):
        again = tool_dispatch.execute_tool(_load("c7"), "apply_resolved_check_damage", request, [], [], actor_id="u1")
    assert first["ok"] and again["ok"] and first["damage"] == again["damage"] == 4
    assert _load("c7").characters["u1"].hp == 6, "outside combat, once"


# ---------------------------------------------------------------- C8


def test_c8_five_point_san_loss_chains_one_int_check_and_one_madness_roll():
    state = _game("c8", "u1", luck=50, san=50, INT=60)
    state.pending_checks["u1"] = {"type": "sanity", "loss_success": "0", "loss_failure": "1d6"}
    _store(state)
    san_script = ScriptedDice([90], san_losses=[6])
    san = check_commands.resolve_check("c8", "u1", "/coc check", dice_port=san_script)
    assert san.should_finalize and "短暫瘋狂" in san.roll_line
    chained = _load("c8").pending_checks["u1"]
    assert chained["skill"] == "INT" and chained["madness_trigger"] is True
    assert chained["caused_by_check_id"] == san.check_id and chained["check_id"] != san.check_id
    assert _load("c8").characters["u1"].san == 44, "the SAN loss is applied once, before the INT check"

    int_script = ScriptedDice([20], madness=[4])
    chained_result = check_commands.resolve_check("c8", "u1", "/coc check INT", dice_port=int_script)
    assert chained_result.should_finalize and "觸發短暫瘋狂" in chained_result.roll_line
    assert int_script.rolls_taken == 1 and int_script.madness_calls == 1
    assert chained_result.resolved_event["caused_by_check_id"] == san.check_id
    saved = _load("c8")
    assert saved.pending_checks == {} and saved.pending_luck_decisions == {}
    assert saved.characters["u1"].san == 44

    again = check_commands.resolve_check("c8", "u1", "/coc check INT", dice_port=int_script)
    assert not again.should_finalize and int_script.rolls_taken == 1, "no second INT roll"


def test_c8_autoroll_sanity_tool_rolls_the_int_check_inline_exactly_once():
    state = _game("c8-auto", "u1", autoroll=True, luck=50, san=50, INT=60)
    _store(state)
    script = ScriptedDice([90, 20], san_losses=[6], madness=[7])
    with module_dice(script):
        result = tool_dispatch.execute_tool(
            _load("c8-auto"), "sanity_check",
            {"investigator": "調查員u1", "loss_success": "0", "loss_failure": "1d6"}, [], [],
            speaker_role="player",
        )
    assert result["san_after"] == 44 and result["madness_int_check"]["roll"] == 20
    assert result["madness"]["roll"] == 7
    assert script.rolls_taken == 2 and script.madness_calls == 1
    saved = _load("c8-auto")
    assert saved.characters["u1"].san == 44 and saved.pending_checks == {}
    assert saved.resolved_check_events[-1]["state_effects"] == [{"field": "SAN", "before": 50, "after": 44, "delta": -6}]


# ---------------------------------------------------------------- C9


def test_c9_five_simultaneous_pendings_only_touch_their_own_actor():
    owners = [f"p{n}" for n in range(1, 6)]
    state = _game("c9", *owners, luck=0)
    for index, owner in enumerate(owners):
        state.pending_checks[owner] = _skill_pending(skill="偵查", value=60, check_id=f"check-{index}")
    state.characters["bystander"] = _investigator("bystander", luck=0)
    state.pending_checks["bystander"] = _skill_pending(value=60, check_id="check-bystander")
    _store(state)
    barrier = threading.Barrier(len(owners))

    def click(owner: str) -> CheckOutcome:
        barrier.wait()
        return check_commands.resolve_check("c9", owner, "/coc check", dice_port=ScriptedDice([30]))

    with ThreadPoolExecutor(max_workers=len(owners)) as pool:
        outcomes = dict(zip(owners, pool.map(click, owners), strict=True))
    for index, owner in enumerate(owners):
        assert outcomes[owner].should_finalize and outcomes[owner].check_id == f"check-{index}"
        assert outcomes[owner].resolved_event["owner_id"] == owner
    saved = _load("c9")
    assert set(saved.pending_checks) == {"bystander"}
    assert saved.pending_checks["bystander"]["check_id"] == "check-bystander"


def test_c9_a_second_check_for_one_player_does_not_clear_another_players_pending():
    state = _game("c9-keep", "p1", "p2", luck=0)
    state.pending_checks["p2"] = _skill_pending(check_id="check-p2")
    _store(state)
    registered = tool_dispatch.execute_tool(
        _load("c9-keep"), "skill_check", {"investigator": "調查員p1", "skill": "偵查"}, [], [],
        speaker_role="player",
    )
    assert registered["ok"] and registered["pending"]
    saved = _load("c9-keep")
    assert set(saved.pending_checks) == {"p1", "p2"}
    assert saved.pending_checks["p2"]["check_id"] == "check-p2"


# ---------------------------------------------------------------- C10


def test_c10_a_pushed_roll_is_final_and_never_offers_luck():
    state = _game("c10", "u1", luck=50, 偵查=50)
    state.pending_checks["u1"] = _skill_pending(value=50, pushed=True)
    _store(state)
    outcome = check_commands.resolve_check("c10", "u1", "/coc check", dice_port=ScriptedDice([55]))
    assert outcome.should_finalize and _load("c10").pending_luck_decisions == {}


def test_c10_opposed_checks_keep_their_limits():
    state = _game("c10-opposed", "u1", autoroll=True, luck=0)
    _store(state)
    opposed = {
        "opponent_skill": "POW", "opponent_value": 90, "tie_winner": "opponent",
        "source": "Having Hold of the Knife, p. 11", "on_win": "The player holds the flying knife.",
        "on_loss": "The knife escapes; resolve its attack separately.",
    }
    base = {"investigator": "調查員u1", "skill": "偵查", "opposed": opposed, "action_basis": "Flying knife; p. 11"}
    for extra in ({"pushed": True}, {"difficulty": "hard"}):
        refused = tool_dispatch.execute_tool(
            _load("c10-opposed"), "skill_check", {**base, **extra}, [], [], speaker_role="player",
        )
        assert refused["ok"] is False and "對抗檢定" in refused["error"]


# ---------------------------------------------------------------- C11


def test_c11_a_retried_continuation_records_the_check_and_its_origin_once():
    state = _game("c11", "u1", luck=0, scenario_text=SOURCE)
    state.pending_checks["u1"] = _skill_pending(consequences=[
        {"key": "bed:dodge", "kind": "check", "when": "success", "skill": "閃避", "difficulty": "regular",
         "source_quote": SOURCE},
    ])
    _store(state)
    script = ScriptedDice([30])
    settled = check_commands.resolve_check("c11", "u1", "/coc check", dice_port=script)
    for _ in range(3):  # narration retried three times
        check_events.persist_consequence_origin("c11", settled.resolved_event)
        check_events.persist_resolved_event("c11", settled.resolved_event)
    saved = _load("c11")
    assert script.rolls_taken == 1
    assert len(saved.resolved_check_events) == 1
    assert list(saved.check_consequence_origins) == [settled.resolved_event["event_id"]]
    revision = saved.state_revision
    check_events.persist_resolved_event("c11", settled.resolved_event)
    assert _load("c11").state_revision == revision, "a retry writes nothing"


def test_c11_tool_autoroll_retry_returns_the_cached_result_without_rolling():
    state = _game("c11-tool", "u1", autoroll=True, luck=0)
    _store(state)
    script = ScriptedDice([30])
    request = {"investigator": "調查員u1", "skill": "偵查"}
    with module_dice(script), observability.context(turn_id="turn-retry"):
        first = tool_dispatch.execute_tool(_load("c11-tool"), "skill_check", request, [], [], speaker_role="player")
        second = tool_dispatch.execute_tool(_load("c11-tool"), "skill_check", request, [], [], speaker_role="player")
    assert first == second
    assert script.rolls_taken == 1
    assert len(_load("c11-tool").resolved_check_events) == 1


def test_narration_failure_after_a_committed_roll_does_not_reroll_on_retry():
    state = _game("c11-narration", "u1", luck=0)
    state.pending_checks["u1"] = _skill_pending()
    _store(state)
    script = ScriptedDice([30])

    async def reply(message: str) -> None:
        pass

    with (
        module_dice(script),
        patch.object(check_commands.supervisor, "run_turn", AsyncMock(side_effect=RuntimeError("provider down"))),
        pytest.raises(RuntimeError),
    ):
        asyncio.run(check_commands.handle_check_command(
            "c11-narration", "u1", reply, AsyncMock(), AsyncMock(), AsyncMock(), "/coc check",
        ))
    assert script.rolls_taken == 1
    assert _load("c11-narration").pending_checks == {}, "the roll is committed before narration starts"
    retry = check_commands.resolve_check("c11-narration", "u1", "/coc check", dice_port=script)
    assert not retry.should_finalize and script.rolls_taken == 1


def test_dice_module_evaluate_roll_matches_skill_check_classification():
    for skill in (10, 40, 50, 60, 99):
        for roll in range(1, 101):
            with patch("app.dice.roll_percentile_with_dice_pool", return_value=roll):
                assert dice.skill_check(skill) == dice.evaluate_roll(skill, roll)
