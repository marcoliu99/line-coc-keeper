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


def _text(line: str, stamp: str = "2026-10-05T04:55:09.925Z") -> str:
    return f"{stamp} INFO app.turn {line}"


def test_the_reader_parses_what_the_runtime_writes(monkeypatch, caplog):
    monkeypatch.setattr(config, "LOG_ENABLED", False)
    caplog.set_level(logging.INFO, logger="app.turn")
    with turn_phases.timeline("turn", turn_id="turn_abc", player_id="u1", campaign_id="discord-channel-1", queue_wait_ms=40):
        turn_phases.note(route="gameplay_action", fallback="internal_error")
        with turn_phases.phase("executor_llm"):
            pass
    from app import logging_config
    (record,) = [r for r in caplog.records if r.name == "app.turn"]
    for formatter in (logging_config.StructuredFormatter(), logging_config.TextFormatter()):
        row = report.parse_line(formatter.format(record))
        assert row is not None, type(formatter).__name__
        assert row["turn_id"] == "turn_abc" and row["route"] == "gameplay_action"
        assert row["fallback"] == "internal_error" and row["queue_wait_ms"] == 40.0 and row["wall_ms"] >= 0


def _formatted(formatter, message: str, logger: str = "app.turn") -> str:
    record = logging.LogRecord(logger, logging.INFO, __file__, 1, message, (), None)
    return formatter.format(record)


def test_both_log_formats_are_read(monkeypatch):
    """The real formatters, not hand-written lines: the default LOG_FORMAT is json."""
    from app import logging_config
    for formatter in (logging_config.StructuredFormatter(), logging_config.TextFormatter()):
        row = report.parse_line(_formatted(formatter, LINES[1]))
        assert row is not None, type(formatter).__name__
        assert row["turn_id"] == "t2" and row["fallback"] == "executor_no_action" and row["wall_ms"] == 60000.0
        assert row["_stamp"].startswith("20")
        # a successful turn ends in a numeric field; the JSON envelope's closing brace must not break it
        assert report.parse_line(_formatted(formatter, LINES[0]))["wall_ms"] == 40000.0


def test_a_text_log_with_context_appended_is_read():
    line = ("2026-10-05T04:55:09.925Z INFO app.turn " + LINES[2]
            + " request_id=req_1 conversation_id=abc turn_id=t3")
    row = report.parse_line(line)
    assert row["turn_id"] == "t3" and row["fallback"] == "internal_error" and row["request_id"] == "req_1"


def test_another_logger_cannot_make_a_turn_up():
    """A logged Keeper reply may contain anything, including this marker."""
    from app import logging_config
    for formatter in (logging_config.StructuredFormatter(), logging_config.TextFormatter()):
        assert report.parse_line(_formatted(formatter, LINES[0], logger="app.discord_transport.delivery")) is None
    assert report.parse_line("INFO reply: " + LINES[0]) is None
    assert report.parse_line(LINES[0]) is None  # the bare message without an envelope


def test_noise_is_ignored():
    for noise in ("", "ordinary log line", "{", "{}", '{"logger":"app.turn"}', '{"logger":"app.turn","message":"x"}',
                  "2026-10-05T04:55:09.925Z INFO app.turn turn.summary turn_id=x",
                  "2026-10-05T04:55:09.925Z INFO app.turn turn.summary turn_id=x wall_ms=abc"):
        assert report.parse_line(noise) is None


def test_the_summary_counts_what_a_harness_counting_exceptions_misses():
    data = report.summarize([report.parse_line(_text(line)) for line in LINES])
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
    data = report.summarize([report.parse_line(_text(line)) for line in LINES])
    assert data["wall_ms"]["p50"] == 40000.0  # the 25 s continuation is not a player's wait for an action


def test_cli_reads_a_directory_and_prints_json(tmp_path, capsys):
    (tmp_path / "a.log").write_text("\n".join(_text(line) for line in LINES[:3]) + "\nunrelated\n", encoding="utf-8")
    sub = tmp_path / "older"
    sub.mkdir()
    (sub / "b.log").write_text("\n".join(_text(line) for line in LINES[3:]) + "\n", encoding="utf-8")
    assert report.main([str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lines"] == 5


def test_since_drops_older_stamped_lines(tmp_path, capsys):
    (tmp_path / "x.log").write_text(
        _text(LINES[0], "2026-10-05T04:00:00.000Z") + "\n" + _text(LINES[1], "2026-10-05T06:00:00.000Z") + "\n",
        encoding="utf-8")
    assert report.main([str(tmp_path), "--json", "--since", "2026-10-05T05:00"]) == 0
    assert json.loads(capsys.readouterr().out)["lines"] == 1


def test_no_summary_lines_is_an_error_not_a_zero_report(tmp_path, capsys):
    (tmp_path / "x.log").write_text("nothing here\n", encoding="utf-8")
    assert report.main([str(tmp_path)]) == 2
    assert "LOG_TEXT_ENABLED" in capsys.readouterr().err
    assert report.main([str(tmp_path / "missing")]) == 2


# --- failed model requests and turns that raised ---------------------------------------------------------------

FAILED = [
    {"event": "llm.turn.failed", "timestamp": "2026-10-05T16:14:46.611Z", "turn_id": "t3", "agent": "executor",
     "provider": "codex", "status": "timeout", "error_type": "TimeoutError", "duration_ms": 120017.5, "tool_call_count": 2},
    {"event": "llm.turn.failed", "timestamp": "2026-10-05T17:08:22.054Z", "turn_id": "t9", "agent": "executor",
     "provider": "codex", "status": "error", "error_type": "PermissionError", "duration_ms": 120003.5},
    {"event": "llm.turn.failed", "timestamp": "2026-10-05T17:25:08.243Z", "turn_id": "t11", "agent": "executor",
     "provider": "codex", "status": "error", "error_type": "CodexError", "duration_ms": 51958.6},
]


def test_a_failed_model_request_is_read_from_the_structured_log():
    parsed = report.parse_failure(json.dumps(FAILED[0]))
    assert parsed["error_type"] == "TimeoutError" and parsed["tool_call_count"] == 2 and parsed["agent"] == "executor"
    for noise in ("", "not json", "{}", json.dumps({"event": "llm.turn.completed"}), "2026 INFO app.turn turn.summary x"):
        assert report.parse_failure(noise) is None


def test_failures_are_counted_apart_from_the_turn_summaries():
    rows = [report.parse_line(_text(line)) for line in LINES]  # one summary says internal_error
    failures = [report.parse_failure(json.dumps(f)) for f in FAILED]
    data = report.summarize(rows, failures)
    failed = data["failed_requests"]
    assert failed["total"] == 3 and failed["after_tool_calls"] == 1
    assert failed["by_error"] == {"executor/TimeoutError": 1, "executor/PermissionError": 1, "executor/CodexError": 1}
    assert failed["turn_summaries_marked_internal_error"] == 1
    text = report.render(data)
    assert "3 failed, 1 of them after tool calls" in text and "only 1 turn summary line(s) say internal_error" in text


def test_without_failure_events_the_report_has_no_such_section():
    data = report.summarize([report.parse_line(_text(line)) for line in LINES])
    assert data["failed_requests"]["total"] == 0 and "llm.turn.failed" not in report.render(data)


def test_the_cli_reads_failures_from_a_json_log(tmp_path, capsys):
    (tmp_path / "runtime.jsonl").write_text(
        "\n".join(json.dumps(f) for f in FAILED) + "\n" + "\n".join(_text(line) for line in LINES) + "\n", encoding="utf-8")
    assert report.main([str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["failed_requests"]["total"] == 3


def test_a_turn_that_raises_is_an_internal_error_in_its_summary(monkeypatch, caplog):
    monkeypatch.setattr(config, "LOG_ENABLED", False)
    caplog.set_level(logging.INFO, logger="app.turn")
    with pytest.raises(RuntimeError), turn_phases.timeline("turn", turn_id="turn_x", player_id="u1", campaign_id="c"):
        raise RuntimeError("boom")
    (line,) = [r.getMessage() for r in caplog.records if r.name == "app.turn"]
    assert "fallback=internal_error" in line and "error=RuntimeError" in line


def test_a_reason_recorded_before_the_raise_is_kept(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.turn")
    with pytest.raises(RuntimeError), turn_phases.timeline("turn", turn_id="turn_x", player_id="u1", campaign_id="c"):
        turn_phases.note(fallback="tool_failure")
        raise RuntimeError("boom")
    (line,) = [r.getMessage() for r in caplog.records if r.name == "app.turn"]
    assert "fallback=tool_failure" in line and "error=RuntimeError" in line
