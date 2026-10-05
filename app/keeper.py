"""The Keeper: an LLM-powered COC7e game master with dice/rule tools.

Provider-agnostic on purpose — the game logic here (tools, system prompt, state
mutation) doesn't know or care whether Claude or Gemini is actually generating
text. See app/providers/ for the per-SDK adapters and LLM_PROVIDER in .env for
which one is active.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Generic, TypeVar, cast, overload
from uuid import uuid4

from app import (
    async_utils,
    check_lifecycle,
    combat,
    combat_resources,
    dice,
    luck,
    observability,
    scenario_library,
    spoiler_policy,
)
from app.checks.skills import resolve_skill_value
from app.config import (
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
    SCENARIO_RAG_ENABLED,
)
from app.keeper_tools import registry as tool_registry
from app.keeper_tools import resource_bridge
from app.keeper_tools.registry import ToolCall
from app.models import Character, GroupState
from app.repositories import state_transaction
from app.services import combat_actions as combat_act
from app.services import (
    combat_engine,
    mutation_admission,
)

_logger = logging.getLogger(__name__)
# Existing callers patch scenario_library through keeper. Retain the module
# object here during the handler migration.
SCENARIO_LIBRARY_MODULE = scenario_library
# Tests and compatibility callers patch the shared luck module through keeper.
LUCK_MODULE = luck

_T = TypeVar("_T")


@dataclass
class _StateMutation(Generic[_T]):
    value: _T
    should_save: bool = True


TOOLS = tool_registry.TOOLS
_SEARCH_SCENARIO_TOOL = tool_registry.SEARCH_SCENARIO_TOOL
READ_ONLY_TOOL_NAMES = tool_registry.READ_ONLY_TOOL_NAMES
RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES = tool_registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES
_KP_ASSISTANT_ALLOWED_TOOL_NAMES = tool_registry.KP_ASSISTANT_ALLOWED_TOOL_NAMES
_KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES = tool_registry.KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES
_COMBAT_STATUS_INVALIDATING_TOOLS = tool_registry.COMBAT_STATUS_INVALIDATING_TOOLS

# search_scenario's own description tells players' turns not to look up
# future scenes/secrets (spoiler avoidance) — but the KP Assistant IS the
# human KP's own tool, not a player-facing surface, so that restriction is
# actively wrong for it: a KP legitimately asks it to look ahead (e.g.
# prepping the next encounter). See _tool_definition_for_kp_assistant below,
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


def find_character(state: GroupState, name: str) -> Character | None:
    if not name:
        return None
    characters = state.all_characters()
    exact = next((char for char in characters if char.name == name), None)
    if exact:
        return exact
    norm = name.strip().lower()
    for c in characters:
        if norm and (norm in c.name.lower() or c.name.lower() in norm):
            return c
    return None


def require_character(state: GroupState, name: str) -> Character:
    character = find_character(state, name)
    if character is None:
        raise ValueError(f"找不到角色「{name}」")
    return character


def _deterministic_check_cache_key(
    tool_name: str,
    tool_input: dict[str, Any],
    owner_id: str,
    speaker_role: str,
) -> str:
    """Return a same-turn idempotency key for a Keeper-owned check.

    The turn id is the important boundary: two attacks in two different player
    turns must roll independently even when their text and skill are identical,
    while a provider retry inside one turn must not roll twice. Direct unit
    calls without an observability turn intentionally skip this cache because
    they do not have a trustworthy request boundary.
    """
    context = observability.current_context()
    turn_id = str(context.get("turn_id", "")).strip()
    if not turn_id:
        return ""
    payload = {
        "turn_id": turn_id,
        "tool": tool_name,
        "input": tool_input,
        "owner_id": owner_id,
        "speaker_role": speaker_role,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _cached_check_result(state: GroupState, cache_key: str) -> dict[str, Any] | None:
    if not cache_key:
        return None
    cached = state.deterministic_check_results.get(cache_key)
    if not isinstance(cached, dict):
        return None
    result = cached.get("result")
    return dict(result) if isinstance(result, dict) else None


def _remember_check_result(state: GroupState, cache_key: str, result: dict[str, Any]) -> None:
    if not cache_key:
        return
    state.deterministic_check_results[cache_key] = {
        "result": dict(result),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Keep this cache small. It is only for retries of recent turns, not an
    # audit log; the authoritative public event remains the committed turn.
    if len(state.deterministic_check_results) > 128:
        oldest = next(iter(state.deterministic_check_results))
        state.deterministic_check_results.pop(oldest, None)


def _resolve_defense_options(
    char: Character, raw_options: list[dict], *, register_unknown: bool = True
) -> list[dict]:
    """Expand offer_check_choice/offer_npc_attack_defense_choice's raw
    {label, skill, bonus_dice, penalty_dice} option list into one with
    each option's actual resolved skill_value baked in.

    Full skill value, no artificial difficulty adjustment — confirmed
    against the official COC7e Fight Back text: it's a normal opposed
    roll at the defender's own combat skill, not a harder version of
    Dodge. (A prior revision here halved it as a house-rule
    approximation; reverted.)

    Passes "kind" (the caller's optional "dodge"/"counter" tag, see
    dice.is_counter_option) straight through unmodified — this function
    resolves skill_value, it doesn't validate or normalize kind."""
    options = []
    for opt in raw_options:
        value = resolve_skill_value(char, opt["skill"], register_unknown=register_unknown)
        resolved = {
            "label": opt["label"], "skill": opt["skill"], "skill_value": value,
            "bonus_dice": int(opt.get("bonus_dice") or 0), "penalty_dice": int(opt.get("penalty_dice") or 0),
        }
        if "kind" in opt:
            resolved["kind"] = opt["kind"]
        options.append(resolved)
    return options


_NPC_INDEX_FUZZY_THRESHOLD = 0.6  # same calibration as app/scene_map.py's room-name fuzzy match


def _find_npc_index_entry(state: GroupState, name: str) -> dict | None:
    """Looks up `name` (whatever the Keeper called this NPC/monster when
    calling add_npc_to_combat) against state.scenario_npc_index — exact match
    against the entry's name or any alias first, then a difflib fuzzy
    fallback (same threshold as scene_map.py's room-name matching) to still
    catch a name that's missing punctuation or a suffix the Keeper dropped
    (e.g. "深潛者頭目" for an entry named "深潛者（成年頭目）"). Returns None
    if scenario_npc_index is empty (nobody's run /coc index) or nothing
    matches closely enough — callers should trust whatever the Keeper passed
    in that case, same as before this existed."""
    if not name:
        return None
    exact = combat.find_npc_index_entry_exact(state, name)
    if exact is not None:
        return exact

    import difflib

    best_entry = None
    best_ratio = 0.0
    for entry in state.scenario_npc_index:
        candidates = [entry.get("name", "")] + list(entry.get("aliases") or [])
        for candidate in candidates:
            if not candidate:
                continue
            ratio = difflib.SequenceMatcher(None, name, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_entry = entry
    return best_entry if best_ratio >= _NPC_INDEX_FUZZY_THRESHOLD else None


def _refresh_state_snapshot(state: GroupState) -> GroupState:
    return state_transaction.refresh_snapshot(state)


@overload
def _mutate_and_save_state(state: GroupState, mutator: Callable[[GroupState], _StateMutation[_T]]) -> _T: ...
@overload
def _mutate_and_save_state(state: GroupState, mutator: Callable[[GroupState], _T]) -> _T: ...
def _mutate_and_save_state(state: GroupState, mutator: Callable[[GroupState], Any]) -> Any:
    """Keeper-tool adapter over the shared state transaction.

    ``mutator`` runs against the latest committed state inside
    ``state_transaction.mutate`` (conversation lock, ``BEGIN IMMEDIATE``,
    timeline check); the caller's snapshot is then refreshed so later tools in
    the same Keeper turn see the result.

    Two call shapes: ``mutator`` can return ``_StateMutation(value,
    should_save)`` when a tool needs to skip a genuinely no-op save (see
    add_carried_item/remove_carried_item/add_status_tag/remove_status_tag —
    ``should_save=False`` when the item/tag was already (not) present), or
    return its actual value directly when every call always needs a save. The
    ``@overload`` pair tells mypy that a ``_StateMutation[_T]`` result is
    unwrapped to ``_T``.
    """
    def run(ctx: state_transaction.TxContext) -> Any:
        result = mutator(ctx.state)
        if isinstance(result, _StateMutation):
            if not result.should_save:
                ctx.skip_save()
            return result.value
        return result

    return state_transaction.run_snapshot(state, run, reason="tool")


# Public migration seam for handlers in app/keeper_tools/. State still reloads,
# mutates, saves, and refreshes through one authoritative boundary.
ToolStateMutation = _StateMutation
mutate_tool_state = _mutate_and_save_state
refresh_tool_state = _refresh_state_snapshot


def apply_character_delta_in_state(
    target_state: GroupState, target_char: Character, field_name: str, delta: int,
    cur_attr: str, max_attr: str | None, *, entry_point: str, event_id: str = "", reason: str = "",
) -> tuple[int, bool, dict[str, Any] | None, dict[str, Any] | None]:
    """Shared attribute and major-wound rule within a caller-owned transaction."""
    if target_state.combat.active:
        if not resource_bridge.participating(target_state, target_char):
            raise ValueError('Active combat requires admitted character resource evidence')
        identity = event_id or resource_bridge.mutation_id(entry_point, {
            'investigator': target_char.character_id, 'field': field_name, 'delta': delta,
        })
        if field_name == 'hp' and delta < 0:
            damage_result = combat_engine.handle(target_state, combat_act.SingleHit(
                character=target_char, damage=-delta, event_id=identity, reason=reason or entry_point,
            ))
            if not damage_result.get('ok'):
                return resource_bridge.effective(target_state, target_char).hp, False, None, damage_result
            effective = resource_bridge.effective(target_state, target_char)
            return effective.hp, bool(damage_result.get('major_wound_triggered')), damage_result.get('major_wound_check'), None
        receipt = combat_resources.adjust_resource(
            target_state, target_char, cast(combat_resources.ResourceField, field_name), delta,
            event_id=identity, reason=reason or entry_point,
        )
        return receipt['after'], False, None, None
    target_cap = getattr(target_char, max_attr) if max_attr else 999
    new_val = max(0, min(target_cap, getattr(target_char, cur_attr) + delta))
    is_major_wound = (
        field_name == "hp" and delta < 0 and new_val > 0
        and -delta >= target_char.hp_max / 2
    )
    blocker = (
        check_lifecycle.blocker(target_state, target_char.owner_id)
        if is_major_wound and not target_state.autoroll_checks else None
    )
    if blocker:
        blocked = combat.major_wound_blocked(
            target_state, target_char, blocker, entry_point=entry_point
        )
        return getattr(target_char, cur_attr), False, None, blocked

    setattr(target_char, cur_attr, new_val)
    major_wound = False
    wound_roll: dict[str, Any] | None = None
    if is_major_wound:
        con_value = resolve_skill_value(target_char, "CON")
        major_wound = True
        if target_state.autoroll_checks:
            con_result = dice.skill_check(con_value)
            wound_roll = {
                "skill": "CON", "skill_value": con_value, "roll": con_result.roll,
                "tier": con_result.tier, "success": con_result.success,
            }
            if not con_result.success:
                for tag in ("昏迷", "倒地"):
                    if tag not in target_char.status_tags:
                        target_char.status_tags.append(tag)
        else:
            decision = check_lifecycle.register(
                target_state, target_char.owner_id,
                {
                    "type": "skill", "skill": "CON", "skill_value": con_value,
                    "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
                    "major_wound_trigger": True,
                },
                source={"action_context": f"{target_char.name} 因為重傷需要做 CON 檢定"},
            )
            if decision.status != "admitted":
                raise RuntimeError(f"major-wound CON registration blocked: {decision.blocker}")
    return new_val, major_wound, wound_roll, None


def apply_character_attribute_delta(
    state: GroupState,
    tool_input: dict[str, Any],
    field_name: str,
    cur_attr: str,
    max_attr: str | None,
) -> tuple[int, bool, dict[str, Any] | None, dict[str, Any] | None]:
    """Apply an attribute change and its required CON check in one transaction."""
    def _apply_attribute_delta(
        target_state: GroupState,
    ) -> _StateMutation[tuple[int, bool, dict[str, Any] | None, dict[str, Any] | None]]:
        target_char = require_character(target_state, tool_input.get("investigator", ""))
        result = apply_character_delta_in_state(
            target_state, target_char, field_name, int(tool_input["delta"]),
            cur_attr, max_attr, entry_point="adjust_character",
            event_id=str(tool_input.get("event_id") or ""), reason=str(tool_input.get("reason") or ""),
        )
        return _StateMutation(result, should_save=result[3] is None)

    return _mutate_and_save_state(state, _apply_attribute_delta)


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

    _mutate_and_save_state(state, mutator)


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


def _parse_kp_manual_canon_trigger(speaker_role: str, message_text: str) -> tuple[bool, str]:
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


def _kp_tool_result_creates_canon(tool_name: str, tool_input: dict, result: dict) -> bool:
    if result.get("ok") is not True:
        return False
    if tool_name in _KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES:
        return True
    if tool_name == "roll_dice":
        return tool_input.get("roll_context") == "game_resolution"
    return False


def _validate_kp_roll_dice_context(tool_input: dict) -> str | None:
    if tool_input.get("roll_context") in ("game_resolution", "ooc_randomizer"):
        return None
    return 'KP Assistant 使用 roll_dice 時必須明確指定 roll_context 為 "game_resolution" 或 "ooc_randomizer"。'


def _skip_save_if_blocked(result: dict) -> _StateMutation[dict]:
    """A hit refused for a blocked major wound changed nothing, so skip the save."""
    return _StateMutation(result, should_save="blocked_by" not in result)


def _filter_public_combat_damage_result(result: dict, speaker_role: str) -> dict:
    if (
        speaker_role == "kp_assistant"
        or result.get("side") != "enemy"
        or not spoiler_policy.is_privacy_isolation_enabled()
    ):
        return result
    public_keys = {
        "ok",
        "name",
        "target",
        "target_id",
        "side",
        "damage_type",
        "final_damage",
        "major_wound_triggered",
        "defeated",
        "public_summary",
        "effect_id",
    }
    return {key: result[key] for key in public_keys if key in result}


# Additional public helpers for the combat handlers.
find_npc_index_entry = _find_npc_index_entry
skip_save_if_blocked = _skip_save_if_blocked
filter_public_combat_damage_result = _filter_public_combat_damage_result


def _scenario_allowed_chapter_ids(state: GroupState) -> set[str] | None:
    """§3.4 mechanism #4: chapter gating is spoiler protection, not privacy —
    disabled means any chapter's images are searchable. Shared by
    search_scenario_images/show_scenario_image below."""
    if not spoiler_policy.is_spoiler_protection_enabled():
        return None
    return set(state.context_chapter_ids)


# Temporary public seam for check handlers while shared check helpers still
# live in keeper.py. No handler reaches across the module's private boundary.
check_tool_services = SimpleNamespace(
    StateMutation=_StateMutation,
    cached_check_result=_cached_check_result,
    deterministic_check_cache_key=_deterministic_check_cache_key,
    mutate_and_save_state=_mutate_and_save_state,
    remember_check_result=_remember_check_result,
    resolve_defense_options=_resolve_defense_options,
)


def _execute_tool(
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

        if speaker_role == "kp_assistant" and name not in _KP_ASSISTANT_ALLOWED_TOOL_NAMES:
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


def _tool_definition_for_kp_assistant(tool: dict) -> dict:
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


def _tools_for_speaker_role(speaker_role: str) -> list[dict]:
    base_tools = TOOLS + [_SEARCH_SCENARIO_TOOL] if SCENARIO_RAG_ENABLED else TOOLS
    if speaker_role != "kp_assistant":
        return base_tools
    return [
        _tool_definition_for_kp_assistant(tool)
        for tool in base_tools
        if tool["name"] in _KP_ASSISTANT_ALLOWED_TOOL_NAMES
    ]


class _CombatStatusToolGate:
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
            and tool_name in _COMBAT_STATUS_INVALIDATING_TOOLS
            and isinstance(result, dict)
            and result.get("ok") is True
            and not self._has_complete_status(result)
        ):
            self._withhold_status = False

    def tools_for_request(self, tools: list[dict]) -> list[dict]:
        if not self._withhold_status:
            return tools
        return [tool for tool in tools if tool.get("name") != "get_combat_status"]
