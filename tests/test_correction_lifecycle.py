"""A correction report's status changes only through ``narrative_corrections`` (ADR 0001/0002).

The state machine is a table, so a ruling, a withdrawal or a replacement that the table does
not allow is refused rather than silently applied.
"""
from __future__ import annotations

import itertools

import pytest

from app.models import GroupState
from app.services import narrative_corrections as corrections

ALL = tuple(corrections.TRANSITIONS)


def _state(**fields) -> GroupState:
    return GroupState(group_id="g", timeline_id="timeline-a", **fields)


def _report(state: GroupState, status: str = "pending", **fields) -> dict:
    report = corrections.new_report(
        state, reporter_id="u1", issue="你剛才說錯了", target_message_id="900", receipt={"message_id": "900"},
    )
    report["status"] = status
    report.update(fields)
    corrections.file_report(state, report)
    return report


@pytest.mark.parametrize(("start", "end"), list(itertools.product(ALL, ALL)))
def test_a_report_moves_only_along_the_transition_table(start: str, end: str) -> None:
    report = {"id": "r1", "status": start}
    if end in corrections.TRANSITIONS[start]:
        corrections._move(report, end)  # type: ignore[arg-type]
        assert report["status"] == end
    else:
        with pytest.raises(ValueError, match="cannot move"):
            corrections._move(report, end)  # type: ignore[arg-type]
        assert report["status"] == start


def test_closed_reports_are_terminal_and_only_approved_can_be_replaced() -> None:
    assert not corrections.TRANSITIONS["rejected"] | corrections.TRANSITIONS["withdrawn"] | corrections.TRANSITIONS["superseded"]
    assert corrections.TRANSITIONS["approved"] == {"superseded"}
    assert set(corrections.OPEN_STATUSES) == {"pending", "unverified"}


def test_a_new_report_is_a_pending_allegation_on_the_current_timeline() -> None:
    state = _state()
    report = corrections.new_report(
        state, reporter_id="u1", issue="地下室不存在", target_message_id="900", receipt={"message_id": "900"},
    )
    assert (report["status"], report["timeline_id"], report["conversation_id"]) == ("pending", "timeline-a", "g")
    assert (report["reporter_id"], report["issue"], report["target_message_id"]) == ("u1", "地下室不存在", "900")
    assert report["target_receipt"] == {"message_id": "900"} and len(report["id"]) == 10
    assert "resolution" not in report and "hold_scope" not in report


def test_filing_the_first_report_of_a_timeline_discards_the_inactive_ones() -> None:
    state = _state(narrative_corrections=[{"id": "old", "status": "approved", "timeline_id": "timeline-old"}])
    kept = _report(state)
    assert state.narrative_corrections == [kept]


def test_a_ruling_on_a_closed_report_is_refused_and_changes_nothing() -> None:
    state = _state()
    report = _report(state, "rejected")
    with pytest.raises(ValueError):
        corrections.record_ruling(state, report, "approve", "kp", resolution="更正")
    assert report["status"] == "rejected" and not state.log


def test_a_ruling_on_a_pending_or_unverified_report_works_either_way() -> None:
    state = _state()
    for start in corrections.OPEN_STATUSES:
        report = _report(state, start, target_message_id="900")
        message = corrections.record_ruling(state, report, "approve", "kp", resolution="地下室不存在")
        assert report["status"] == "approved" and report["summary_rebuild_status"] == "pending"
        assert "地下室不存在" in message
    assert len(state.log) == 2


def test_the_keeper_can_leave_a_report_unverified_and_a_kp_can_still_rule_on_it() -> None:
    state = _state()
    report = _report(state)
    corrections.record_unverified(report)
    assert report["status"] == "unverified"
    corrections.record_ruling(state, report, "reject", "kp")
    assert report["status"] == "rejected" and report["reviewed_by"] == "kp"


def test_withdrawing_closes_an_open_report_with_the_reviewer() -> None:
    state = _state()
    report = _report(state)
    assert corrections.withdraw(report, "u1") == f"敘事異議 #{report['id']} 已撤回。"
    assert (report["status"], report["reviewed_by"]) == ("withdrawn", "u1")
    with pytest.raises(ValueError):
        corrections.withdraw(report, "u1")


def test_superseding_links_both_corrections_and_marks_the_log_entries() -> None:
    state = _state()
    old = _report(state, "approved")
    new = _report(state, "approved")
    state.log.append({"role": "assistant", "content": "…", "fact_refs": [f"correction:{old['id']}"]})
    state.log.append({"role": "assistant", "content": "unrelated", "fact_refs": []})

    corrections.supersede(state, old, new)

    assert (old["status"], old["superseded_by"]) == ("superseded", new["id"])
    assert new["supersedes"] == [f"correction:{old['id']}"] and new["summary_rebuild_status"] == "pending"
    assert state.log[0]["superseded_by"] == [new["id"]] and "superseded_by" not in state.log[1]


def test_only_an_approved_correction_can_be_replaced_by_another_approved_one() -> None:
    state = _state()
    approved = _report(state, "approved")
    pending = _report(state)
    for old, new in ((pending, approved), (approved, pending), (approved, approved)):
        with pytest.raises(ValueError):
            corrections.supersede(state, old, new)
    assert approved["status"] == "approved" and pending["status"] == "pending"


def test_a_hold_needs_an_open_report_and_never_changes_its_status() -> None:
    state = _state()
    report = _report(state)
    corrections.hold(report, ["地下室"], "kp")
    assert (report["status"], report["hold_scope"], report["held_by"]) == ("pending", ["地下室"], "kp")
    assert corrections.hold_matches(state, "我走進地下室")
    closed = _report(state, "rejected")
    with pytest.raises(ValueError):
        corrections.hold(closed, ["x"], "kp")


def test_pruning_keeps_open_approved_and_superseded_reports_and_the_latest_closed_ones() -> None:
    state = _state(narrative_corrections=[
        *({"id": f"c{n}", "status": "rejected", "timeline_id": "timeline-a"} for n in range(20)),
        {"id": "open", "status": "pending", "timeline_id": "timeline-a"},
        {"id": "ok", "status": "approved", "timeline_id": "timeline-a"},
        {"id": "old", "status": "superseded", "timeline_id": "timeline-a"},
    ])
    corrections.prune_closed(state)
    ids = [row["id"] for row in state.narrative_corrections]
    assert ids[-3:] == ["open", "ok", "old"] and len(ids) == corrections.MAX_CLOSED_IN_STATE + 3
    assert ids[:-3] == [f"c{n}" for n in range(8, 20)]


def test_find_report_only_sees_the_current_timeline() -> None:
    state = _state(narrative_corrections=[{"id": "x", "status": "pending", "timeline_id": "timeline-old"}])
    mine = _report(state)
    assert corrections.find_report(state, mine["id"]) is mine
    assert corrections.find_report(state, "x") is None
