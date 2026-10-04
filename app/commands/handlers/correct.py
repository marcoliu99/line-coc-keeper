"""Out-of-character reports about Keeper narration.

Player text here is an allegation, never an established game fact. The
group's KP Assistant adjudicates; in a group without one the Keeper rules from
system-held evidence (docs/adr/0001-keeper-adjudicates-corrections-without-kp.md).
This handler does not call the Supervisor, Executor, dice, or gameplay tools.
"""

from __future__ import annotations

import re
from uuid import uuid4

from app.commands import permissions
from app.commands.types import Reply
from app.repositories.group_state import load_state
from app.services import (
    correction_adjudication,
    correction_summary,
    mutation_admission,
    narrative_corrections,
)
from app.services.narrative_corrections import OPEN_STATUSES, target_receipt

_MESSAGE_URL = re.compile(r"^https://(?:canary\.|ptb\.)?discord\.com/channels/\d+/\d+/(\d+)$")
_MESSAGE_ID = re.compile(r"^\d{5,25}$")
_MAX_ISSUE_LENGTH = 500
_MAX_RESOLUTION_LENGTH = 1000
_MAX_PENDING_PER_GROUP = 12
_MAX_PENDING_PER_REPORTER = 3
_MAX_UNVERIFIED_PER_REPORTER = 3


def _target_id(token: str) -> str | None:
    if _MESSAGE_ID.fullmatch(token):
        return token
    match = _MESSAGE_URL.fullmatch(token)
    return match.group(1) if match else None


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
        old = narrative_corrections.find_report(state, parts[3]) if len(parts) == 5 else None
        replacement = narrative_corrections.find_report(state, parts[4]) if len(parts) == 5 else None
        if not is_kp or old is None or replacement is None or old is replacement or old.get("status") != "approved" or replacement.get("status") != "approved":
            await reply("只有 KP 可整併有效更正：/coc correct supersede <舊編號> <取代它的核准編號>")
            return
        narrative_corrections.supersede(state, old, replacement)
        narrative_corrections.save(state)
        correction_summary.schedule(conversation_id)
        await reply(f"更正 #{old['id']} 已由 #{replacement['id']} 取代；原紀錄保留。")
        return

    if action == "hold":
        report = narrative_corrections.find_report(state, parts[3]) if len(parts) >= 5 else None
        scope = [x.strip() for x in " ".join(parts[4:]).split("|") if x.strip()]
        if not is_kp or report is None or report.get("status") not in OPEN_STATUSES or not narrative_corrections.valid_hold_scope(scope):
            await reply("只有 KP 可標記待核對範圍：/coc correct hold <編號> <地點或實體名稱|別名>（每項 2–80 字，最多 8 項）")
            return
        narrative_corrections.hold(report, scope, user_id)
        narrative_corrections.save(state)
        await reply("已標記核對範圍；符合指定名稱的行動與狀態工具將暫停。未列出的代稱不保證自動辨識。")
        return

    if action == "list":
        visible = [
            item for item in narrative_corrections.active(state)
            if item.get("status") in {*OPEN_STATUSES, "approved"} and (is_kp or item.get("reporter_id") == user_id)
        ]
        correction_adjudication.schedule(conversation_id, state, reply)
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
        report = narrative_corrections.find_report(state, parts[3])
        if report is None or report.get("status") not in OPEN_STATUSES:
            await reply("找不到這筆待核對異議，或已經處理。")
            return
        if action == "withdraw":
            if not is_kp and report.get("reporter_id") != user_id:
                await reply("只有提報者或 KP 可以撤回這筆異議。")
                return
            message = narrative_corrections.withdraw(report, user_id)
        else:
            if not is_kp:
                await reply("只有 KP 可以裁定敘事異議。")
                return
            resolution = " ".join(parts[4:]).strip()
            if action == "approve" and (not resolution or len(resolution) > _MAX_RESOLUTION_LENGTH):
                await reply("請提供 1 到 1000 字的公開更正內容：/coc correct approve <提報編號> <更正內容>")
                return
            verdict: narrative_corrections.Verdict = "approve" if action == "approve" else "reject"
            message = narrative_corrections.record_ruling(state, report, verdict, user_id, resolution=resolution)
        narrative_corrections.prune_closed(state)
        narrative_corrections.save(state)
        await reply(message)
        if action == "approve":
            correction_summary.schedule(conversation_id)
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

    for item in narrative_corrections.active(state):
        if (
            item.get("status") in OPEN_STATUSES
            and item.get("reporter_id") == user_id
            and item.get("target_message_id") == target
            and item.get("issue") == issue
        ):
            await reply(f"這筆敘事異議已收到（#{item['id']}），目前待核對。")
            return

    pending = [item for item in narrative_corrections.active(state) if item.get("status") == "pending"]
    if len(pending) >= _MAX_PENDING_PER_GROUP or sum(
        item.get("reporter_id") == user_id for item in pending
    ) >= _MAX_PENDING_PER_REPORTER:
        await reply("待核對敘事異議已達上限；請先由 KP 處理，或撤回你不再需要的提報。")
        return
    # Unverified reports don't count above, so a KP-less group never fills up;
    # this cap stops one player cycling fabricated reports through the Keeper.
    # Their pending reports count too, since each may still become unverified.
    unverified = sum(item.get("status") == "unverified" and item.get("reporter_id") == user_id
                     for item in narrative_corrections.active(state))
    mine_pending = sum(item.get("reporter_id") == user_id for item in pending)
    if unverified and unverified + mine_pending >= _MAX_UNVERIFIED_PER_REPORTER:
        await reply(f"你有 {unverified} 筆守秘人未能證實的異議，加上待核對的已達 3 筆；"
                    "請先撤回其中一筆，或等 KP 裁定後再提報。")
        return

    report = narrative_corrections.new_report(
        state, reporter_id=user_id, issue=issue, target_message_id=target, receipt=receipt,
    )
    narrative_corrections.file_report(state, report)
    narrative_corrections.save(state)
    await reply(f"已收到敘事糾正提報 #{report['id']}，待核對。提報不會改寫劇情或觸發遊戲行動。")
    correction_adjudication.schedule(conversation_id, state, reply)
