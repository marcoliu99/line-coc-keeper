"""A choice label is typed whole, spaces and all (Camp Sunny rerun7: 「以 STR」 was read as an option called 「以」)."""
from __future__ import annotations

from app.checks import narration
from app.checks import service as check_service
from app.commands.router import _sudo_check_matches_pending
from app.models import Character, GroupState
from tests.check_dice import ScriptedDice

OPTIONS = [
    {"label": "以 STR 抓住", "skill": "STR", "skill_value": 60, "kind": "str", "bonus_dice": 0, "penalty_dice": 0},
    {"label": "以 SIZ 擋住", "skill": "SIZ", "skill_value": 50, "kind": "siz", "bonus_dice": 0, "penalty_dice": 0},
]


def test_the_whole_phrase_names_the_option_and_so_does_its_first_word_alone() -> None:
    assert narration.match_typed_choice(OPTIONS, ["以", "STR", "抓住"])["kind"] == "str"
    assert narration.match_typed_choice(OPTIONS, ["以", "SIZ", "擋住"])["kind"] == "siz"
    assert narration.match_typed_choice(OPTIONS, ["STR"])["kind"] == "str"  # the skill, as before
    assert narration.match_typed_choice(OPTIONS, ["STR", "謝謝"])["kind"] == "str"  # trailing words are ignored
    assert narration.match_typed_choice(OPTIONS, ["以"]) is None  # "以" alone names both
    assert narration.match_typed_choice(OPTIONS, []) is None


def _state() -> GroupState:
    state = GroupState(group_id="g", timeline_id="t", active=True)
    state.characters["u1"] = Character(name="調查員", owner_id="u1", hp=10, hp_max=10, san=50, luck=50,
                                       skills={"偵查": 60}, str_=60, siz=50)
    state.pending_checks["u1"] = {"type": "choice", "options": [dict(o) for o in OPTIONS], "check_id": "c1",
                                  "action_context": "抓住門把", "timeline_id": "t"}
    return state


def test_a_typed_label_with_spaces_picks_the_option_it_names() -> None:
    state = _state()
    outcome = check_service.resolve_player_check(state, "u1", "/coc check 以 SIZ 擋住", dice_port=ScriptedDice([30]))
    assert "沒有「" not in outcome.reply_text and "SIZ" in outcome.reply_text


def test_a_label_that_matches_nothing_is_echoed_whole_with_the_options() -> None:
    state = _state()
    outcome = check_service.resolve_player_check(state, "u1", "/coc check 以 DEX 跳開", dice_port=ScriptedDice([30]))
    assert "沒有「以 DEX 跳開」這個選項" in outcome.reply_text and "以 STR 抓住" in outcome.reply_text
    assert "u1" in state.pending_checks  # a mismatch leaves the choice standing


def test_a_delegated_check_may_carry_the_whole_label() -> None:
    pending = {"type": "choice", "options": OPTIONS}
    assert _sudo_check_matches_pending(pending, ("以", "STR", "抓住"))
    assert _sudo_check_matches_pending(pending, ("STR",))
    assert not _sudo_check_matches_pending({"type": "skill", "skill": "偵查"}, ("偵查", "謝謝"))
