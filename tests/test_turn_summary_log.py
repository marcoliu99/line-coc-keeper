"""Every player-waited turn leaves one plain summary line, even with structured event logging off."""
from __future__ import annotations

import logging

import pytest

from app import config, discord_bot, observability
from app.services import turn_phases


def _run_turn(kind: str = "turn", **notes: str) -> None:
    with turn_phases.timeline(kind, turn_id="turn_abc", player_id="u1", campaign_id="discord-channel-1", queue_wait_ms=40):
        turn_phases.note(**notes)
        with turn_phases.phase("executor_llm"):
            pass


def _summary_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.turn"]


def test_a_turn_logs_one_summary_line_even_when_event_logging_is_off(monkeypatch, caplog):
    monkeypatch.setattr(config, "LOG_ENABLED", False)
    caplog.set_level(logging.INFO, logger="app.turn")
    _run_turn(route="gameplay_action")
    (line,) = _summary_lines(caplog)
    assert line.startswith("turn.summary turn_id=turn_abc kind=turn route=gameplay_action")
    for field in ("wall_ms=", "queue_wait_ms=", "executor_ms=", "other_ms="):
        assert field in line


def test_the_summary_carries_timings_and_ids_but_never_player_identity_or_text(monkeypatch, caplog):
    monkeypatch.setattr(config, "LOG_ENABLED", False)
    caplog.set_level(logging.INFO, logger="app.turn")
    _run_turn()
    (line,) = _summary_lines(caplog)
    assert "u1" not in line.replace("turn_abc", "")  # the player id is not in it


def test_a_fallback_reason_is_named(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.turn")
    _run_turn(route="gameplay_action", fallback="no_scenario_evidence")
    assert "fallback=no_scenario_evidence" in _summary_lines(caplog)[0]


def test_background_maintenance_is_not_summarised(caplog):
    caplog.set_level(logging.INFO, logger="app.turn")
    _run_turn("maintenance")
    assert _summary_lines(caplog) == []


def test_a_continuation_after_a_roll_is_summarised_as_such(caplog):
    caplog.set_level(logging.INFO, logger="app.turn")
    _run_turn("continuation", route="resolved_check_followup")
    assert " kind=continuation " in _summary_lines(caplog)[0]


def test_note_outside_a_turn_is_a_no_op():
    turn_phases.note(route="x")  # must not raise


def test_a_failing_summary_never_fails_the_turn(monkeypatch):
    monkeypatch.setattr(turn_phases, "_log_summary", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _run_turn()  # reported inside the existing guard


@pytest.mark.parametrize(("request_id", "expected"), [("req_0123456789abcdef", "（代碼 abcdef）"), ("", "")])
def test_the_internal_error_message_carries_a_code_the_kp_can_search_for(monkeypatch, request_id, expected):
    monkeypatch.setattr(observability, "current_context", lambda: {"request_id": request_id} if request_id else {})
    assert discord_bot._error_reference() == expected
