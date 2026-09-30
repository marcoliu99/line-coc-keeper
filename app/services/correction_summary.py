"""Conditional summary rebuild after an approved correction, outside turn locks."""
from __future__ import annotations

import asyncio
import logging

from app import keeper, locks
from app.repositories.group_state import load_state, save_state

_logger = logging.getLogger(__name__)
_running: set[str] = set()


async def rebuild(conversation_id: str) -> None:
    if conversation_id in _running:
        return
    _running.add(conversation_id)
    try:
        state = load_state(conversation_id)
        pending = [r for r in state.narrative_corrections
                   if r.get("timeline_id") == state.timeline_id
                   and r.get("status") == "approved"
                   and r.get("summary_rebuild_status") == "pending"]
        if not pending:
            return
        correction_history = [
            {"role": "assistant", "record_kind": "legacy_mixed", "authority": "presentation",
             "content": str((report.get("target_receipt") or {}).get("excerpt", "")),
             "superseded_by": [report["id"]]}
            for report in pending
        ]
        history = correction_history + state.log[-40:]
        summary = await asyncio.to_thread(keeper.summarize_log_chunk, state.campaign_summary, history)
        async with locks.get_conversation_lock(conversation_id):
            latest = load_state(conversation_id)
            if latest.timeline_id != state.timeline_id or latest.state_revision != state.state_revision:
                return
            if summary and summary != state.campaign_summary:
                latest.campaign_summary = summary
                for report in latest.narrative_corrections:
                    if report.get("id") in {row["id"] for row in pending}:
                        report["summary_rebuild_status"] = "done"
                latest.openai_previous_response_id = ""
                latest.openai_previous_response_timeline_id = ""
                save_state(latest, reason="correction_summary_rebuild")
    except Exception:
        _logger.exception("failed to rebuild summary after correction")
    finally:
        _running.discard(conversation_id)


def schedule(conversation_id: str) -> None:
    task = asyncio.create_task(rebuild(conversation_id))
    from app import async_utils
    async_utils.observe_background_task(task, operation="correction.summary_rebuild")
