"""A Sanity roll for every witness from one call, a backticked roll command counts, and the settled line names the roll."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.agents import tool_gateway
from app.domain.models import AgentMessage
from app.keeper_tools import registry
from app.models import Character, GroupState
from app.services import turn_delivery


def _state() -> GroupState:
    state = GroupState(group_id="g", timeline_id="t", game_started=True)
    for owner, name in (("a", "Marco"), ("b", "Ken"), ("c", "Ally")):
        state.characters[owner] = Character(name=name, owner_id=owner)
    return state


def test_one_sanity_call_rolls_for_every_witness_once():
    rolled: list[str] = []

    def fake_tool(_state, name, args, *_rest, **_kw):
        rolled.append(args["investigator"])
        assert "witnesses" not in args  # each roll is the plain single call
        return {"ok": True, "resolved": True, "investigator": args["investigator"], "roll": 40, "current_san": 50,
                "san_after": 48, "loss": 2, "tier": "fail"}

    facts: list[str] = []
    outcomes: list = []
    with patch.object(tool_gateway.tool_dispatch, "execute_tool", fake_tool):
        execute = tool_gateway.make_tool_executor(_state(), [], [], "player", facts, observed_outcomes=outcomes, actor_id="a")
        result = asyncio.run(execute("sanity_check", {
            "investigator": "Marco", "witnesses": ["Ken", "marco", "Ally", "Ken", ""],
            "loss_success": "0", "loss_failure": "1d4", "action_context": "看見屍體",
        }))
    assert rolled == ["Marco", "Ken", "Ally"]  # the actor is not rolled twice under another spelling
    assert result["investigator"] == "Marco" and [w["investigator"] for w in result["witnesses"]] == ["Ken", "Ally"]
    assert len(facts) == 3 and all("Ken" in o.public_text or "Marco" in o.public_text or "Ally" in o.public_text
                                   for o in outcomes)


def test_a_lone_witness_is_the_plain_call():
    with patch.object(tool_gateway.tool_dispatch, "execute_tool", lambda *a, **k: {"ok": True, "pending": True}):
        execute = tool_gateway.make_tool_executor(_state(), [], [], "player", [], actor_id="a")
        result = asyncio.run(execute("sanity_check", {"investigator": "Marco", "loss_success": "0", "loss_failure": "1"}))
    assert result == {"ok": True, "pending": True}


def test_the_keeper_is_offered_the_witness_list():
    schema = registry.REGISTRY["sanity_check"].schema["input_schema"]
    assert schema["properties"]["witnesses"]["type"] == "array" and "witnesses" not in schema["required"]


def test_a_backticked_roll_command_already_tells_the_player():
    ref = turn_delivery.InteractionRef("check", "p1", "x", "t")
    assert ref.told_in("請輸入 `/coc check` 擲骰。")
    assert not ref.told_in("請輸入 `/coc checkpoint`。")


def test_the_settled_line_names_whose_roll_and_which_skill():
    state = _state()
    msg = AgentMessage({"state": state, "user_id": "a", "resolved_check_context": {
        "investigator": "Marco", "skill": "偵查", "roll": 52, "outcome": "regular 成功", "check_id": "c1"}})
    reply, _ = turn_delivery.finalize(msg, "你看見了。")
    assert "已結算檢定：Marco 的偵查，骰值 52，結果為「一般成功」。" in reply, reply


def test_a_refused_primary_does_not_hide_the_witnesses_who_rolled():
    def fake_tool(_state, name, args, *_rest, **_kw):
        if args["investigator"] == "Marco":
            return {"ok": False, "error": "Marco 已有待處理的檢定"}
        return {"ok": True, "resolved": True, "investigator": args["investigator"], "roll": 40}

    with patch.object(tool_gateway.tool_dispatch, "execute_tool", fake_tool):
        execute = tool_gateway.make_tool_executor(_state(), [], [], "player", [], actor_id="a")
        result = asyncio.run(execute("sanity_check", {"investigator": "Marco", "witnesses": ["Ken"],
                                                      "loss_success": "0", "loss_failure": "1"}))
    assert result["ok"] and result["investigator"] == "Ken"
    assert [w.get("error") for w in result["witnesses"]] == ["Marco 已有待處理的檢定"]


def test_a_choice_settled_without_a_roll_does_not_read_as_a_zero_roll():
    msg = AgentMessage({"state": _state(), "user_id": "a", "resolved_check_context": {
        "investigator": "Marco", "skill": "不閃躲", "roll": 0, "outcome": "不閃躲，攻擊已結算", "no_roll": True,
        "check_id": "c2"}})
    reply, _ = turn_delivery.finalize(msg, "爪子落下。")
    assert "已結算：Marco 選擇「不閃躲」，未擲骰。" in reply and "骰值 0" not in reply, reply
