"""The turn-summary reader agrees with the line the runtime actually writes."""
from __future__ import annotations

import json
import logging

import pytest

from app import config
from app.services import turn_phases
from scripts import summarize_turn_log as report

LINES = [
    "turn.summary turn_id=t1 kind=turn route=gameplay_action wall_ms=40000.0 queue_wait_ms=0.0 executor_ms=30000.0 narrator_ms=9000.0 other_ms=1000.0",
    "turn.summary turn_id=t2 kind=turn route=gameplay_action wall_ms=60000.0 queue_wait_ms=20000.0 executor_ms=30000.0 narrator_ms=9000.0 other_ms=1000.0 fallback=executor_no_action",
    "turn.summary turn_id=t3 kind=turn route=gameplay_action wall_ms=33000.0 queue_wait_ms=0.0 executor_ms=32000.0 other_ms=1000.0 fallback=internal_error",
    "turn.summary turn_id=t4 kind=continuation route=resolved_check_followup wall_ms=25000.0 queue_wait_ms=0.0 narrator_ms=24000.0 other_ms=1000.0",
    "turn.summary turn_id=t5 kind=turn route=gameplay_action short_circuit=pending_luck wall_ms=12.0 queue_wait_ms=0.0",
]


def test_the_reader_parses_what_the_runtime_writes(monkeypatch, caplog):
    monkeypatch.setattr(config, "LOG_ENABLED", False)
    caplog.set_level(logging.INFO, logger="app.turn")
    with turn_phases.timeline("turn", turn_id="turn_abc", player_id="u1", campaign_id="discord-channel-1", queue_wait_ms=40):
        turn_phases.note(route="gameplay_action", fallback="internal_error")
        with turn_phases.phase("executor_llm"):
            pass
    (message,) = [r.getMessage() for r in caplog.records if r.name == "app.turn"]
    row = report.parse_line(message)
    assert row is not None
    assert row["turn_id"] == "turn_abc" and row["route"] == "gameplay_action" and row["fallback"] == "internal_error"
    assert row["queue_wait_ms"] == 40.0 and row["wall_ms"] >= 0


def test_a_log_prefix_and_other_lines_are_handled():
    assert report.parse_line("2026-10-05 04:55:09,925 INFO app.turn: " + LINES[0])["turn_id"] == "t1"
    for noise in ("", "ordinary log line", "turn.summary", "turn.summary turn_id=x", "turn.summary turn_id=x wall_ms=abc"):
        assert report.parse_line(noise) is None


def test_the_summary_counts_what_a_harness_counting_exceptions_misses():
    data = report.summarize([report.parse_line(line) for line in LINES])
    assert data["turns"] == 4 and data["continuations"] == 1
    assert data["fallbacks"]["total"] == 2
    assert data["fallbacks"]["by_reason"] == {"executor_no_action": 1, "internal_error": 1}
    assert data["fallbacks"]["share_of_lines"] == pytest.approx(0.4)
    assert data["short_circuits"] == {"pending_luck": 1}
    assert data["slowest"][0]["turn_id"] == "t2"
    assert data["wall_ms"]["max"] == 60000.0
    text = report.render(data)
    assert "internal_error" in text and "reports 0" in text


def test_only_ordinary_turns_set_the_waiting_percentiles():
    data = report.summarize([report.parse_line(line) for line in LINES])
    assert data["wall_ms"]["p50"] == 40000.0  # the 25 s continuation is not a player's wait for an action


def test_cli_reads_a_directory_and_prints_json(tmp_path, capsys):
    (tmp_path / "a.log").write_text("\n".join(LINES[:3]) + "\nunrelated\n", encoding="utf-8")
    sub = tmp_path / "older"
    sub.mkdir()
    (sub / "b.log").write_text("\n".join(LINES[3:]) + "\n", encoding="utf-8")
    assert report.main([str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lines"] == 5


def test_since_drops_older_stamped_lines(tmp_path, capsys):
    (tmp_path / "x.log").write_text(
        "2026-10-05T04:00:00 " + LINES[0] + "\n2026-10-05T06:00:00 " + LINES[1] + "\n", encoding="utf-8")
    assert report.main([str(tmp_path), "--json", "--since", "2026-10-05T05:00"]) == 0
    assert json.loads(capsys.readouterr().out)["lines"] == 1


def test_no_summary_lines_is_an_error_not_a_zero_report(tmp_path, capsys):
    (tmp_path / "x.log").write_text("nothing here\n", encoding="utf-8")
    assert report.main([str(tmp_path)]) == 2
    assert "LOG_TEXT_ENABLED" in capsys.readouterr().err
    assert report.main([str(tmp_path / "missing")]) == 2
