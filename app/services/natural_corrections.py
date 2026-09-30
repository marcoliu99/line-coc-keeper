"""Conservative admission of explicit player OOC corrections before gameplay parsing."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from app.models import GroupState
from app.services import history_authority, narrative_corrections

_CORRECTION_CUES = ("你剛才", "你剛剛", "你前面", "剛才你", "剛剛你", "你說我", "守密人剛才", "你說錯", "更正：", "糾正：")
_ERROR_CUES = ("說錯", "講錯", "說成", "敘述", "描述", "搞錯", "誤寫", "誤說", "糾正", "更正")
_PRESENTATION_CUES = ("氣味", "味道", "煙味", "腐紙味", "聲音", "顏色", "只是普通", "不是線索", "不是關鍵", "不重要")
_CONSEQUENTIAL_CUES = ("HP", "SAN", "MP", "LUCK", "血量", "傷害", "骰", "檢定", "門", "鑰匙", "線索", "日記", "背包", "武器", "地點", "上樓", "下樓")
_ACTION_CUES = ("我要", "我現在", "我接著", "我走去", "我去買", "再買")
_INCIDENTAL_ITEM_RE = re.compile(r"我(?:之前|剛才)?(?:已經)?(?:拿了|拿到|帶著|留下|有)(?:一個|一本|一支|一把)?(?P<item>[^，。；]{1,20})")
_PLOT_ITEM_TERMS = ("鑰匙", "槍", "刀", "斧", "炸", "毒", "藥", "現金", "錢", "線索", "證據", "魔法", "科比特", "Corbitt")


def incidental_item(text: str) -> str:
    match = _INCIDENTAL_ITEM_RE.search(text)
    if match is None:
        return ""
    item = match.group("item").strip()
    if (not item or any(term.casefold() in item.casefold() for term in _PLOT_ITEM_TERMS)
            or ("日記" in item and "普通日記" not in item)):
        return ""
    return item


def classify(text: str) -> str:
    """Return empty, clarify, presentation, or review; never infer world truth."""
    if (text.startswith("/") or not any(cue in text for cue in _CORRECTION_CUES)
            or not any(cue in text for cue in _ERROR_CUES)):
        return ""
    if any(cue in text for cue in _ACTION_CUES):
        return "clarify"
    if incidental_item(text):
        return "incidental_item"
    if any(cue in text for cue in _PRESENTATION_CUES) and not any(
        cue in text for cue in _CONSEQUENTIAL_CUES if cue not in {"線索", "日記"}
    ):
        return "presentation"
    return "review"


def submit(state: GroupState, user_id: str, text: str, *, target_message_id: str | None = None) -> tuple[str, bool]:
    """Persist an allegation or narrow presentation repair; no game action runs."""
    kind = classify(text)
    if not kind:
        return "", False
    if kind == "clarify":
        return "你是在場外更正先前敘事，還是要執行新的遊戲行動？請分開說明。", True
    receipt = (narrative_corrections.target_receipt(state, target_message_id)
               if target_message_id else narrative_corrections.latest_receipt(state))
    if receipt is None:
        return "目前找不到這條時間線中可核對的上一則守密人訊息；請回覆那則訊息並使用 /coc correct 說明。", True
    for existing in narrative_corrections.active(state):
        if (existing.get("reporter_id") == user_id and existing.get("issue") == text
                and existing.get("target_message_id") == str(receipt["message_id"])
                and existing.get("status") in {"pending", "unverified", "approved"}):
            return f"這筆場外更正已記錄（#{existing['id']}）。", True
    pending = [row for row in narrative_corrections.active(state) if row.get("status") == "pending"]
    if kind == "review" and (len(pending) >= 10 or sum(row.get("reporter_id") == user_id for row in pending) >= 3):
        return "待核對更正已達上限；請先處理或撤回既有異議。", True
    if kind == "presentation" and any(
        record.get("verification_status") == "verified" and
        any(token in str(record.get("text", "")) and token in text for token in ("日記", "線索"))
        for record in state.known_clues
    ):
        kind = "review"
    item = incidental_item(text) if kind == "incidental_item" else ""
    character = state.get_active_character(user_id) if item else None
    if item and character is None:
        kind = "review"
    if kind in {"presentation", "incidental_item"}:
        effective = [{k: row[k] for k in ("id", "status", "target_message_id", "resolution", "hold_scope") if k in row}
                     for row in narrative_corrections.active(state)
                     if row.get("status") == "approved" or row.get("hold_scope")]
        proposed = {"id": "x" * 10, "status": "approved", "target_message_id": str(receipt["message_id"]),
                    "resolution": text}
        if len(text) > 300 or len(json.dumps(effective + [proposed], ensure_ascii=False)) > narrative_corrections.MAX_CONTEXT_CHARS - 500:
            return "這筆敘事更正太長或有效更正容量已滿；請縮短說明，現有遊戲仍可繼續。", True
    if kind == "review" and (len(pending) >= 10 or sum(row.get("reporter_id") == user_id for row in pending) >= 3):
        return "待核對更正已達上限；請先處理或撤回既有異議。", True
    report = {
        "id": uuid4().hex[:10], "target_message_id": str(receipt["message_id"]),
        "target_receipt": receipt, "issue": text, "reporter_id": user_id,
        "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeline_id": state.timeline_id, "conversation_id": state.group_id,
    }
    state.narrative_corrections[:] = narrative_corrections.active(state)
    state.narrative_corrections.append(report)
    state.log.append(history_authority.annotate_entry(
        {"role": "user", "content": text}, turn_id=str(report["id"]),
        timeline_id=state.timeline_id, record_kind="correction_request", authority="claim",
    ))
    if kind == "incidental_item" and character is not None:
        from app.keeper_tools.inventory import add_carried_item
        from app.keeper_tools.registry import ToolCall

        # Persist the allegation first. The inventory tool reloads and syncs
        # the latest state, so an unsaved correction would otherwise vanish.
        narrative_corrections.save(state)
        result = add_carried_item(ToolCall(
            state, {"investigator": character.name, "item": item}, [], [], "player", "add_carried_item",
        ))
        report = next(row for row in state.narrative_corrections if row.get("id") == report["id"])
        if result.get("ok"):
            report["inventory_item"] = item
            response = narrative_corrections.record_presentation_repair(
                state, report, resolution=f"{character.name} 隨身帶著「{item}」；這只補正持有，未賦予劇本線索或特殊能力。",
            )
        else:
            report["status"] = "pending"
            response = f"已收到更正 #{report['id']}，物品狀態暫未更新；請核對原先行動。"
    elif kind == "presentation":
        resolution = ("先前把普通物件暗示為關鍵線索的敘事已收回；它是否有劇本效果仍以劇本來源為準。"
                      if any(cue in text for cue in ("不是線索", "不是關鍵", "不重要")) else text)
        response = narrative_corrections.record_presentation_repair(state, report, resolution=resolution)
    else:
        response = f"已收到場外更正 #{report['id']}，先核對劇本與已提交狀態；這句不會觸發移動、給物品或重擲檢定。"
    narrative_corrections.save(state)
    return response, True
