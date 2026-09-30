"""One-time, receipt-based cleanup of legacy correction provenance."""
from __future__ import annotations

from app import db
from app.repositories.group_state import load_state
from app.services import narrative_corrections


def group_ids() -> list[str]:
    return db.list_keys("group_states")


def reconcile_group(group_id: str, *, apply: bool = False) -> dict[str, int]:
    """Never infer truth from old prose; only match approved durable receipts."""
    state = load_state(group_id)
    approved = [report for report in state.narrative_corrections
                if report.get("status") == "approved"
                and report.get("timeline_id") == state.timeline_id
                and isinstance(report.get("target_receipt"), dict)]
    before = sum(bool(row.get("superseded_by")) for row in state.log)
    for report in approved:
        narrative_corrections.reconcile_approved_report(state, report)
        report.setdefault("supersedes", [f"message:{report['target_message_id']}"])
        report.setdefault("summary_rebuild_status", "pending")
    marked = sum(bool(row.get("superseded_by")) for row in state.log) - before
    if apply and approved:
        narrative_corrections.save(state)
    return {"approved_receipts": len(approved), "newly_marked_log_entries": marked}
