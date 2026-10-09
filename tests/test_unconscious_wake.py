"""A knocked-out investigator outside combat waits for a standing companion, or wakes after a time skip when nobody
is left to help (app/services/unconscious_wake.py). The Haunting run: Evelyn failed her major-wound CON check at HP 6,
the fight settled, and every one of the next 162 lines was refused while she lay there."""
from __future__ import annotations

import asyncio

from app.agents import supervisor
from app.models import GroupState
from app.services import unconscious_wake
from tests.test_combat_engine import (  # noqa: F401
    GROUP,
    _investigator,
    _load,
    _save,
    database,
)


def _party(*owners: str, knocked_out: str = "p1") -> GroupState:
    characters = {owner: _investigator(owner, f"調查員{owner}", 50) for owner in owners}
    down = characters[knocked_out]
    down.hp, down.status_tags, down.injury = 6, ["昏迷", "倒地"], {"major_wound": True, "unconscious": True}
    state = GroupState(
        GROUP, active=True, timeline_id="timeline-wake", characters=characters,
        characters_by_id={c.character_id: c for c in characters.values()},
        active_character_id_by_user={owner: c.character_id for owner, c in characters.items()},
    )
    _save(state)
    return _load()


def test_alone_and_knocked_out_the_next_line_wakes_them_with_hp_and_wound_kept():
    state = _party("p1")
    assert unconscious_wake.decide(state, "p1") == "wake"
    unconscious_wake.wake(GROUP, "char:p1", timeline_id="timeline-wake")
    evelyn = _load().characters["p1"]
    assert evelyn.hp == 6 and evelyn.injury == {"major_wound": True, "unconscious": False}
    assert "昏迷" not in evelyn.status_tags and "倒地" not in evelyn.status_tags
    assert unconscious_wake.decide(_load(), "p1") is None, "awake now: lines go to the Keeper as usual"
    assert "敵人趁這段時間做了什麼" in unconscious_wake.wake_note("Evelyn")


def test_a_standing_companion_means_waiting_for_first_aid_not_waking():
    state = _party("p1", "p2")
    assert unconscious_wake.decide(state, "p1") == "wait"
    assert unconscious_wake.decide(state, "p2") is None


def test_hp_zero_or_a_running_fight_is_not_this_rule():
    state = _party("p1")
    state.characters["p1"].hp = 0
    assert unconscious_wake.decide(state, "p1") is None
    state = _party("p1")
    state.combat.active = True
    assert unconscious_wake.decide(state, "p1") is None


def test_the_waiting_investigators_line_is_answered_without_the_executor():
    state = _party("p1", "p2")
    turn = supervisor._Turn(
        state=state, user_id="p1", display_name="調查員p1", text="我掙扎著爬起來。", resolved_location=None,
        speaker_role="player", conversation_id=GROUP, turn_kind="player_action", resolved_check_context=None,
        prefetched_retrieval=None, handoff=None, expected_opening_source_hash=None, expected_opening_context=None,
        expected_opening_participants=None, turn_timeline_id="timeline-wake", turn_id="turn-wait",
    )
    reply, private, images = asyncio.run(supervisor._prepare(turn))
    assert reply == unconscious_wake.wait_reply("調查員p1") and private == [] and images == []


def test_the_lone_investigators_line_wakes_them_and_tells_the_keeper_about_the_time_skip():
    from unittest.mock import patch

    import pytest

    from app.agents import context_builder

    seen: dict = {}

    async def stop(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("context reached")

    turn = supervisor._Turn(
        state=_party("p1"), user_id="p1", display_name="調查員p1", text="我掙扎著爬起來。", resolved_location=None,
        speaker_role="player", conversation_id=GROUP, turn_kind="player_action", resolved_check_context=None,
        prefetched_retrieval=None, handoff=None, expected_opening_source_hash=None, expected_opening_context=None,
        expected_opening_participants=None, turn_timeline_id="timeline-wake", turn_id="turn-wake",
    )
    with patch.object(context_builder, "build_context", stop), pytest.raises(RuntimeError, match="context reached"):
        asyncio.run(supervisor._prepare(turn))
    assert seen["text"] == unconscious_wake.wake_note("調查員p1") + "我掙扎著爬起來。"
    assert not _load().characters["p1"].injury["unconscious"] and not turn.state.characters["p1"].injury["unconscious"]
