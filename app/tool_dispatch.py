"""Dispatching a Keeper tool call, and which tools a speaker may use.

Split out of app/keeper.py unchanged. execute_tool runs the shared gates (mutation admission and the KP Assistant
dice/allow-list rules) and then the handler registered in app/keeper_tools/registry.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from app import (
    async_utils,
    observability,
)
from app.config import (
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
    SCENARIO_RAG_ENABLED,
)
from app.keeper_tools import registry as tool_registry
from app.keeper_tools import support
from app.keeper_tools.registry import ToolCall
from app.models import GroupState
from app.services import (
    mutation_admission,
)

_logger = logging.getLogger(__name__)


# search_scenario's own description tells players' turns not to look up
# future scenes/secrets (spoiler avoidance) — but the KP Assistant IS the
# human KP's own tool, not a player-facing surface, so that restriction is
# actively wrong for it: a KP legitimately asks it to look ahead (e.g.
# prepping the next encounter). See tool_definition_for_kp_assistant below,
# which swaps this in for that one role instead of the shared description.
_SEARCH_SCENARIO_DESCRIPTION_KP_ASSISTANT = (
    "在劇本全文裡搜尋跟這個查詢最相關的段落（依頁面為單位），回傳前幾筆最符合的內容。"
    "劇本改用檢索模式時（看到『這份劇本改用檢索模式』的提示）必須用這個工具查詢，"
    "不能憑空想像劇本內容；查詢字詞盡量用劇本裡可能出現的具體名詞（人名、地名、物品、關鍵字），"
    "不要問完整句子。"
    "查詢時以「目前這個提問」為單位思考需要哪些劇本資料，不要只查眼前缺"
    "的單一事實——呼叫前先想一想這個提問接下來可能還會用到哪些相關資訊（相關"
    "的 NPC/怪物/地點、目前情境與遭遇、可能的行動與行為模式、相關法術/武器/能"
    "力、使用條件與代價與限制、立即的後續發展），把這些一起包進同一次查詢"
    "裡，不要每個小問題都分開各查一次。"
    "你是 KP 本人專用的助手，不是在對玩家說話，玩家看不到這裡的查詢或結果，"
    "所以可以視 KP 提問的實際需要查詢之後章節、尚未發生的場景、秘密或遭遇"
    "（例如 KP 想先備下一場戲、確認後續劇情），不需要為了避免劇透而保留；"
    "查詢範圍還是要對應 KP 這次實際問的東西，不要沒來由地把整本劇本都撈一遍。"
    "拿到查詢結果後，先仔細看有沒有涵蓋到目前需要的資訊，只有真的缺東西才再查一"
    "次；不要因為想再三確認已經查到的內容而重複查詢——但這不代表只能查一"
    "次，如果一次查詢真的不夠涵蓋這次提問所需的資訊，可以再查。"
)


_KP_ROLL_DICE_CONTEXT_PROPERTY = {
    "type": "string",
    "enum": ["game_resolution", "ooc_randomizer"],
    "description": (
        "KP Assistant 使用一般骰子時的主持層用途分類。"
        "'game_resolution' 表示直接解析正式遊戲事件；"
        "'ooc_randomizer' 表示只供 KP 幕後隨機決策使用。"
    ),
}


def _record_tool_recovery_marker_sync(
    state: GroupState, tool_name: str, tool_input: dict[str, Any]
) -> None:
    """Persist a cancellation marker without storing arbitrary tool input."""
    input_digest = hashlib.sha256(
        json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    marker = {
        "marker_id": uuid4().hex,
        "tool_name": tool_name,
        "input_digest": input_digest,
        "status": "recovery_required",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    def mutator(latest: GroupState) -> None:
        latest.tool_recovery_markers.append(marker)
        del latest.tool_recovery_markers[:-100]

    support.mutate_tool_state(state, mutator)


async def record_tool_recovery_marker(
    state: GroupState, tool_name: str, tool_input: dict[str, Any]
) -> None:
    """Best-effort durable marker for an abandoned mutation."""
    try:
        await asyncio.shield(asyncio.to_thread(
            _record_tool_recovery_marker_sync, state, tool_name, tool_input
        ))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        observability.event(
            "llm.tool.recovery_marker_failed",
            level=logging.ERROR,
            tool_name=observability.tool_name(tool_name),
            error_type=type(exc).__name__,
        )
        _logger.exception("Could not persist recovery marker for %s", tool_name)


async def record_tool_recovery_marker_bounded(
    state: GroupState, tool_name: str, tool_input: dict[str, Any]
) -> None:
    """Start marker persistence without letting it retain cancellation forever.

    A timed-out mutation may still own the synchronous state lock. The marker
    therefore runs in its own observed task: the caller waits only one grace
    period, while the task can safely acquire the lock after the original
    worker finishes. This keeps cancellation bounded without losing the best-
    effort durable marker.
    """
    task = asyncio.create_task(record_tool_recovery_marker(state, tool_name, tool_input))
    try:
        await asyncio.wait_for(asyncio.shield(task), PROVIDER_SHUTDOWN_GRACE_SECONDS)
    except asyncio.TimeoutError:
        async_utils.observe_background_task(task, operation="llm.tool.recovery_marker")
        observability.event(
            "llm.tool.recovery_marker_deferred",
            level=logging.ERROR,
            tool_name=observability.tool_name(tool_name),
            status="timeout",
            timeout_ms=PROVIDER_SHUTDOWN_GRACE_SECONDS * 1000,
        )
    except asyncio.CancelledError:
        async_utils.observe_background_task(task, operation="llm.tool.recovery_marker")
        raise


def parse_kp_manual_canon_trigger(speaker_role: str, message_text: str) -> tuple[bool, str]:
    """Recognize only a leading !/！ on KP Assistant messages."""
    if speaker_role != "kp_assistant" or not message_text:
        return False, message_text
    first_char = unicodedata.normalize("NFKC", message_text[0])
    if first_char != "!":
        return False, message_text
    body = message_text[1:].lstrip()
    if not body:
        return False, message_text
    return True, body


def kp_tool_result_creates_canon(tool_name: str, tool_input: dict, result: dict) -> bool:
    if result.get("ok") is not True:
        return False
    if tool_name in tool_registry.KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES:
        return True
    if tool_name == "roll_dice":
        return tool_input.get("roll_context") == "game_resolution"
    return False


def _validate_kp_roll_dice_context(tool_input: dict) -> str | None:
    if tool_input.get("roll_context") in ("game_resolution", "ooc_randomizer"):
        return None
    return 'KP Assistant 使用 roll_dice 時必須明確指定 roll_context 為 "game_resolution" 或 "ooc_randomizer"。'


def execute_tool(
    state: GroupState,
    name: str,
    tool_input: dict,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
    speaker_role: str = "player",
    actor_id: str = "",
) -> dict:
    mutation_admission.assert_admitted(state.group_id, timeline_id=state.timeline_id)
    try:
        if speaker_role == "kp_assistant" and name == "roll_dice":
            error = _validate_kp_roll_dice_context(tool_input)
            if error:
                return {"ok": False, "error": error}

        if speaker_role == "kp_assistant" and name not in tool_registry.KP_ASSISTANT_ALLOWED_TOOL_NAMES:
            return {
                "ok": False,
                "error": "KP Assistant turn 只能使用已允許的查詢與主持流程工具，不能直接修改角色 deterministic state 或執行尚未開放的 administrative mutation。",
            }

        spec = tool_registry.REGISTRY.get(name)
        if spec is None:
            return {"ok": False, "error": f"未知工具 {name}"}
        return spec.handler(ToolCall(
            state=state, input=tool_input, private_messages=private_messages,
            image_requests=image_requests,
            speaker_role=cast(tool_registry.SpeakerRole, speaker_role), name=name,
            actor_id=actor_id,
        ))
    except Exception as exc:  # noqa: BLE001 - surfaced back to the model as a tool error
        return {"ok": False, "error": str(exc)}


def tool_definition_for_kp_assistant(tool: dict) -> dict:
    if tool["name"] == "search_scenario":
        return {**tool, "description": _SEARCH_SCENARIO_DESCRIPTION_KP_ASSISTANT}
    if tool["name"] != "roll_dice":
        return tool

    input_schema = dict(tool["input_schema"])
    properties = dict(input_schema["properties"])
    properties["roll_context"] = dict(_KP_ROLL_DICE_CONTEXT_PROPERTY)
    input_schema["properties"] = properties
    required = list(input_schema.get("required", []))
    if "roll_context" not in required:
        required.append("roll_context")
    input_schema["required"] = required
    return {
        **tool,
        "input_schema": input_schema,
    }


def tools_for_speaker_role(speaker_role: str) -> list[dict]:
    base_tools = tool_registry.TOOLS + [tool_registry.SEARCH_SCENARIO_TOOL] if SCENARIO_RAG_ENABLED else tool_registry.TOOLS
    if speaker_role != "kp_assistant":
        return base_tools
    return [
        tool_definition_for_kp_assistant(tool)
        for tool in base_tools
        if tool["name"] in tool_registry.KP_ASSISTANT_ALLOWED_TOOL_NAMES
    ]


class CombatStatusToolGate:
    """Withhold a redundant initial status lookup while the prompt snapshot
    is current, then expose it after a successful combat mutation whose result
    did not include a complete status snapshot.

    This is request-local control state only; it never mutates GroupState.
    """

    def __init__(self, state: GroupState):
        self._withhold_status = state.combat.active and bool(state.combat.order)

    @staticmethod
    def _has_complete_status(result: object) -> bool:
        if not isinstance(result, dict):
            return False
        status = result.get("status")
        return isinstance(status, str) and (
            status.startswith("戰鬥中 - 第 ") or status == "目前沒有進行中的戰鬥。"
        )

    def observe_tool_result(self, tool_name: str, result: object) -> None:
        if (
            self._withhold_status
            and tool_name in tool_registry.COMBAT_STATUS_INVALIDATING_TOOLS
            and isinstance(result, dict)
            and result.get("ok") is True
            and not self._has_complete_status(result)
        ):
            self._withhold_status = False

    def tools_for_request(self, tools: list[dict]) -> list[dict]:
        if not self._withhold_status:
            return tools
        return [tool for tool in tools if tool.get("name") != "get_combat_status"]
