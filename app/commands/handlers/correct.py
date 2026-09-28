"""Out-of-character reports about Keeper narration.

Player text here is an allegation, never an established game fact. Only a KP
adjudication can publish a correction, and this handler does not call the
Supervisor, Executor, dice, or gameplay tools.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from app import db
from app.commands import permissions
from app.legacy_commands import Reply
from app.models import GroupState
from app.repositories.group_state import load_state, save_state
from app.services import mutation_admission
from app.services.narrative_corrections import target_receipt

_MESSAGE_URL = re.compile(r"^https://(?:canary\.|ptb\.)?discord\.com/channels/\d+/\d+/(\d+)$")
_MESSAGE_ID = re.compile(r"^\d{5,25}$")
_MAX_ISSUE_LENGTH = 500
_MAX_RESOLUTION_LENGTH = 1000
_MAX_PENDING_PER_GROUP = 12
_MAX_PENDING_PER_REPORTER = 3
_MAX_CLOSED_IN_STATE = 12


def _target_id(token: str) -> str | None:
    if _MESSAGE_ID.fullmatch(token):
        return token
    match = _MESSAGE_URL.fullmatch(token)
    return match.group(1) if match else None


def _find_report(state: GroupState, report_id: str) -> dict | None:
    return next((item for item in _active_reports(state) if item.get("id") == report_id), None)


def _active_reports(state: GroupState) -> list[dict]:
    """A correction can only affect the campaign timeline that created it."""
    return [item for item in state.narrative_corrections
            if item.get("timeline_id", "") == state.timeline_id]


def _prune_adjudicated(state: GroupState) -> None:
    """Bound state size; approved decisions also remain in the canonical log."""
    approved = [item for item in state.narrative_corrections if item.get("status") == "approved"]
    closed = [item for item in state.narrative_corrections if item.get("status") in {"rejected", "withdrawn"}]
    retained = {id(item) for item in approved + closed[-_MAX_CLOSED_IN_STATE:]}
    state.narrative_corrections[:] = [
        item for item in state.narrative_corrections
        if item.get("status") in {"pending", "superseded"} or id(item) in retained
    ]


def _save(state: GroupState) -> None:
    state.openai_previous_response_id = ""
    state.openai_previous_response_timeline_id = ""
    def archive(conn):
        for record in state.narrative_corrections:
            key = f"{state.group_id}:{record.get('timeline_id', '')}:{record['id']}"
            db.set_json_tx(conn, "narrative_correction_archive", key, record)
    save_state(state, reason="narrative_correction", mutate_tx=archive)


@mutation_admission.guard_async_entry
async def handle_correct_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    parts: list[str],
    *,
    referenced_message_id: str | None = None,
) -> None:
    state = load_state(conversation_id)
    is_kp = permissions.is_kp(state, user_id)
    action = parts[2].casefold() if len(parts) > 2 else ""

    if action == "supersede":
        old = _find_report(state, parts[3]) if len(parts) == 5 else None
        replacement = _find_report(state, parts[4]) if len(parts) == 5 else None
        if not is_kp or old is None or replacement is None or old is replacement or old.get("status") != "approved" or replacement.get("status") != "approved":
            await reply("只有 KP 可整併有效更正：/coc correct supersede <舊編號> <取代它的核准編號>")
            return
        old["status"] = "superseded"
        old["superseded_by"] = replacement["id"]
        _save(state)
        await reply(f"更正 #{old['id']} 已由 #{replacement['id']} 取代；原紀錄保留。")
        return

    if action == "hold":
        report = _find_report(state, parts[3]) if len(parts) >= 5 else None
        scope = [x.strip() for x in " ".join(parts[4:]).split("|") if x.strip()]
        if not is_kp or report is None or report.get("status") != "pending" or not scope or len(scope) > 8 or any(len(x) < 2 or len(x) > 80 for x in scope):
            await reply("只有 KP 可標記待核對範圍：/coc correct hold <編號> <地點或實體名稱|別名>（每項 2–80 字，最多 8 項）")
            return
        report["hold_scope"] = scope
        report["held_by"] = user_id
        _save(state)
        await reply("已標記核對範圍；符合指定名稱的行動與狀態工具將暫停。未列出的代稱不保證自動辨識。")
        return

    if action == "list":
        visible = [
            item for item in _active_reports(state)
            if item.get("status") in {"pending", "approved"} and (is_kp or item.get("reporter_id") == user_id)
        ]
        if not visible:
            await reply("目前沒有可查看的待核對敘事異議。")
            return
        lines = ["待核對敘事異議："]
        for item in visible:
            lines.append(f"#{item['id']}｜{item['status']}｜訊息 {item['target_message_id']}｜{item.get('resolution') or item['issue']}")
        for start in range(0, len(lines), 5):
            await reply("\n".join(lines[start:start + 5]))
        return

    if action in {"approve", "reject", "withdraw"}:
        if len(parts) < 4:
            await reply(f"用法：/coc correct {action} <提報編號>" + (" <更正內容>" if action == "approve" else ""))
            return
        report = _find_report(state, parts[3])
        if report is None or report.get("status") != "pending":
            await reply("找不到這筆待核對異議，或已經處理。")
            return
        if action == "withdraw":
            if not is_kp and report.get("reporter_id") != user_id:
                await reply("只有提報者或 KP 可以撤回這筆異議。")
                return
            report["status"] = "withdrawn"
            message = f"敘事異議 #{report['id']} 已撤回。"
        else:
            if not is_kp:
                await reply("只有 KP 可以裁定敘事異議。")
                return
            if action == "approve":
                resolution = " ".join(parts[4:]).strip()
                if not resolution or len(resolution) > _MAX_RESOLUTION_LENGTH:
                    await reply("請提供 1 到 1000 字的公開更正內容：/coc correct approve <提報編號> <更正內容>")
                    return
                report["status"] = "approved"
                report["resolution"] = resolution
                message = (
                    f"【敘事更正 #{report['id']}】先前訊息 {report['target_message_id']} "
                    f"已由 KP 更正：{resolution}"
                )
            else:
                report["status"] = "rejected"
                message = f"敘事異議 #{report['id']} 經 KP 核對後不成立。"
        report["reviewed_by"] = user_id
        report["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if action == "approve":
            state.log.append({"role": "assistant", "content": message})
            # A cached provider conversation may still contain the uncorrected
            # narration. Rebuild the next turn from the corrected local log.
            state.openai_previous_response_id = ""
            state.openai_previous_response_timeline_id = ""
        _prune_adjudicated(state)
        _save(state)
        await reply(message)
        return

    if referenced_message_id:
        target = _target_id(str(referenced_message_id))
        issue = " ".join(parts[2:]).strip()
    else:
        target = _target_id(parts[2]) if len(parts) > 2 else None
        issue = " ".join(parts[3:]).strip()
    if not target or not issue or len(issue) > _MAX_ISSUE_LENGTH:
        await reply(
            "請回覆有問題的 Keeper 訊息並輸入 /coc correct <疑點>；"
            "或使用 /coc correct <訊息 ID／連結> <疑點>（疑點限 500 字）。"
        )
        return

    if not state.timeline_id:
        state.timeline_id = f"timeline-{uuid4().hex[:8]}"

    if not referenced_message_id and len(parts) > 2 and parts[2].startswith("https://"):
        channel_id = parts[2].split("/")[-2]
        if conversation_id != f"discord-channel-{channel_id}":
            await reply("訊息連結必須屬於目前頻道。")
            return
    receipt = target_receipt(state, target)
    if receipt is None:
        await reply("無法確認目標是本頻道、目前時間線的 Keeper 訊息。請回覆可追溯的新訊息；舊訊息可由 KP 使用既有主持修正流程處理。")
        return

    for item in _active_reports(state):
        if (
            item.get("status") == "pending"
            and item.get("reporter_id") == user_id
            and item.get("target_message_id") == target
            and item.get("issue") == issue
        ):
            await reply(f"這筆敘事異議已收到（#{item['id']}），目前待核對。")
            return

    pending = [item for item in _active_reports(state) if item.get("status") == "pending"]
    if len(pending) >= _MAX_PENDING_PER_GROUP or sum(
        item.get("reporter_id") == user_id for item in pending
    ) >= _MAX_PENDING_PER_REPORTER:
        await reply("待核對敘事異議已達上限；請先由 KP 處理，或撤回你不再需要的提報。")
        return

    # The next report is the first write in this timeline; discard inactive
    # records so repeated scenario switches cannot accumulate stale reports.
    state.narrative_corrections[:] = _active_reports(state)
    report = {
        "id": uuid4().hex[:10],
        "target_message_id": target,
        "issue": issue,
        "reporter_id": user_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeline_id": state.timeline_id,
        "conversation_id": conversation_id,
        "target_receipt": receipt,
    }
    state.narrative_corrections.append(report)
    _save(state)
    await reply(f"已收到敘事糾正提報 #{report['id']}，待核對。提報不會改寫劇情或觸發遊戲行動。")
