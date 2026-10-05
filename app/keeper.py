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
    locks,
    luck,
    memory_rag,
    observability,
    opening_identity,
    scenario_library,
    scene_digest,
    spoiler_policy,
)
from app.checks.skills import resolve_skill_value
from app.config import (
    KP_OOC_LOG_MAX_MESSAGES,
    MAX_LOG_TURNS,
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
    SCENARIO_RAG_ENABLED,
    SCENE_DIGEST_TURN_INTERVAL,
)
from app.keeper_tools import registry as tool_registry
from app.keeper_tools import resource_bridge
from app.keeper_tools.registry import ToolCall
from app.models import Character, GroupState
from app.providers.registry import conversation_provider
from app.repositories import state_transaction
from app.repositories.group_state import load_state
from app.services import combat_actions as combat_act
from app.services import (
    combat_engine,
    history_authority,
    mutation_admission,
    turn_phases,
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
_SUMMARY_TOOL = tool_registry.SUMMARY_TOOL
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


def _ensure_turn_timeline(state: GroupState) -> str:
    """Ensure a turn captures one authoritative timeline before any await.

    Older persisted states may have no timeline at all. If a tool creates a
    timeline only after the provider call starts, the turn would capture the
    fallback ``legacy-*`` value and its final log commit could be rejected as
    a false timeline mismatch. Initialize it before prompt construction and
    refresh the caller's snapshot from the committed row.
    """
    if state.timeline_id:
        return state.timeline_id

    def initialize(ctx: state_transaction.TxContext) -> None:
        # The first write assigns the timeline; if another path already did,
        # there is nothing left to save.
        if ctx.state.timeline_id:
            ctx.skip_save()

    state_transaction.run_snapshot(state, initialize, reason="timeline_init")
    return state.timeline_id


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


class OpeningStartRejected(Exception):
    """A source-bound opening lost its final authoritative start race."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _commit_turn_result(
    state: GroupState,
    log_entries: list[dict[str, Any]],
    openai_response_id: str | None = None,
    *,
    timeline_id: str | None = None,
    invalidate_openai_response_chain: bool = False,
    start_game: bool = False,
    turn_id: str | None = None,
    expected_source_hash: str | None = None,
    expected_opening_context: opening_identity.OpeningContext | None = None,
    expected_opening_participants: opening_identity.OpeningParticipants | None = None,
) -> bool:
    """Append a turn's log entries to the latest committed state.

    The action id is the turn id plus a digest of what is being committed, so a
    delivery or narration retry that reaches this call again with the same
    turn is answered from the action ledger instead of logging it twice, while
    a separate turn (new turn id) or different content is a new action. The
    turn id comes from the caller that owns the turn (``run_turn``), falling
    back to the request context, and is only random when neither exists.
    """
    expected_timeline_id = timeline_id or state.timeline_id or f"legacy-{state.group_id}"
    turn_id = str(turn_id or observability.current_context().get("turn_id") or uuid4().hex)
    fingerprint = state_transaction.request_fingerprint({
        "entries": log_entries, "openai_response_id": openai_response_id,
        "invalidate": invalidate_openai_response_chain, "start_game": start_game,
    })

    def opening_guard(latest_state: GroupState) -> str | None:
        if latest_state.game_started:
            return "already_started"
        if latest_state.active_scenario_source_hash != expected_source_hash:
            return "source_changed"
        if (expected_opening_context is not None
                and opening_identity.context_identity(latest_state) != expected_opening_context):
            return "source_changed"
        if (expected_opening_participants is not None
                and opening_identity.participant_identity(latest_state) != expected_opening_participants):
            return "character_set_changed"
        if not latest_state.active or not latest_state.scenario_text:
            return "no_scenario"
        if not latest_state.characters:
            return "no_characters"
        if latest_state.pending_pregen_luck:
            return "pending_pregen_luck"
        if resource_bridge.guard_replacement(latest_state):
            return "combat_unsettled"
        return None

    def append_entries(ctx: state_transaction.TxContext) -> bool:
        latest_state = ctx.state
        if start_game and latest_state.game_started:
            ctx.skip_save()
            return False
        latest_state.log.extend(
            history_authority.annotate_entry(entry, turn_id=turn_id, timeline_id=ctx.timeline_id)
            for entry in log_entries
        )
        if start_game:
            latest_state.game_started = True
        if invalidate_openai_response_chain:
            latest_state.openai_previous_response_id = ""
            latest_state.openai_previous_response_timeline_id = ""
        elif openai_response_id is not None:
            latest_state.openai_previous_response_id = openai_response_id
            latest_state.openai_previous_response_timeline_id = (
                latest_state.timeline_id or f"legacy-{latest_state.group_id}"
            )
        ctx.stage_event("turn_committed", event_id=f"turn:{turn_id}", entries=len(log_entries))
        return True

    result = state_transaction.commit_for_snapshot(
        state, append_entries, reason="turn", expected_timeline=expected_timeline_id,
        action_id=f"turn:{turn_id}:{fingerprint[:16]}", request_fingerprint=fingerprint,
        latest_state_guard=opening_guard if start_game and expected_source_hash is not None else None,
    )
    if result.outcome is state_transaction.Outcome.STALE_TIMELINE:
        if start_game and expected_source_hash is not None:
            raise OpeningStartRejected("timeline_changed")
        observability.event(
            "state.turn_commit_skipped",
            level=logging.WARNING,
            reason="timeline_mismatch",
            expected_timeline_id=expected_timeline_id,
            current_timeline_id=result.timeline_id,
        )
        return False
    if result.outcome is state_transaction.Outcome.REJECTED and start_game and expected_source_hash is not None:
        raise OpeningStartRejected(result.reason)
    if result.outcome is state_transaction.Outcome.CONFLICT:
        raise state_transaction.StateTransactionFailed(result)
    return result.outcome is state_transaction.Outcome.DUPLICATE or bool(result.value)


def _commit_kp_ooc_turn_result(
    state: GroupState, message_text: str, final_text: str, *, timeline_id: str | None = None
) -> bool:
    """Persist KP Assistant OOC working memory without touching public history.

    Appends to the latest committed state so this ephemeral OOC write cannot
    overwrite deterministic tool updates that happened earlier in the same
    Keeper turn.
    """
    expected_timeline_id = timeline_id or state.timeline_id or f"legacy-{state.group_id}"

    def append_ooc(ctx: state_transaction.TxContext) -> None:
        latest_state = ctx.state
        latest_state.kp_ooc_log.extend(
            [
                {"role": "kp_assistant", "content": message_text},
                {"role": "assistant", "content": final_text},
            ]
        )
        latest_state.kp_ooc_log = latest_state.kp_ooc_log[-KP_OOC_LOG_MAX_MESSAGES:]

    result = state_transaction.commit_for_snapshot(
        state, append_ooc, reason="kp_ooc", expected_timeline=expected_timeline_id,
    )
    if result.outcome is state_transaction.Outcome.STALE_TIMELINE:
        observability.event(
            "state.kp_ooc_commit_skipped",
            level=logging.WARNING,
            reason="timeline_mismatch",
            expected_timeline_id=expected_timeline_id,
            current_timeline_id=result.timeline_id,
        )
        return False
    return True


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


def _persist_memory_maintenance_state(
    group_id: str,
    campaign_summary: str,
    dropped_chunk: list[dict[str, Any]],
    *,
    timeline_id: str,
    base_summary: str,
    source_revision: int,
    idempotency_key: str,
    embedding: list[float] | None,
    prepared: memory_rag.PreparedMemory | None = None,
) -> str:
    """Commit the maintenance trim and memory chunk atomically.

    ``prepared`` is the trim split into parts that each fit one embedding (``memory_rag.prepare_memory``); without
    it the whole trim is one chunk carrying ``embedding``."""
    idempotency_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
    observability.event(
        "maintenance.commit.started",
        timeline_id=timeline_id,
        source_revision=source_revision,
        idempotency_key_hash=idempotency_hash,
    )
    def commit_trim(ctx: state_transaction.TxContext) -> str:
        latest_state = ctx.state
        latest_timeline_id = ctx.timeline_id
        if latest_state.campaign_summary != base_summary:
            observability.event(
                "maintenance.commit_skipped", level=logging.WARNING,
                reason="summary_changed", source_revision=source_revision,
                current_revision=latest_state.state_revision,
                requested_timeline_id=timeline_id,
                current_timeline_id=latest_timeline_id,
                idempotency_key_hash=idempotency_hash,
            )
            ctx.skip_save()
            return "stale_summary"
        memory_row = ctx.conn.execute("SELECT data FROM memory_chunks WHERE key = ?", (group_id,)).fetchone()
        if memory_row is not None:
            try:
                existing_chunks = json.loads(memory_row[0])
            except (TypeError, json.JSONDecodeError):
                existing_chunks = []
            if not isinstance(existing_chunks, list):
                existing_chunks = []
            if any(
                isinstance(item, dict) and idempotency_key in {item.get("idempotency_key"), item.get("parent_id")}
                for item in existing_chunks
            ):
                observability.event(
                    "maintenance.commit_skipped", level=logging.INFO,
                    reason="duplicate_idempotency_key", source_revision=source_revision,
                    current_revision=latest_state.state_revision,
                    requested_timeline_id=timeline_id,
                    current_timeline_id=latest_timeline_id,
                    idempotency_key_hash=idempotency_hash,
                )
                ctx.skip_save()
                return "duplicate"
        # Only apply anything if the front of the freshly-reloaded log still
        # matches what was actually dropped — guards against e.g. a
        # concurrent /coc newgame reset, or another maintenance pass having
        # already trimmed this exact chunk. On a mismatch, skip BOTH the log
        # trim and the campaign_summary update (not just the trim): the
        # summary was derived from `dropped_chunk`, which no longer reflects
        # what's actually at the front of the current log, so applying it
        # anyway would bleed a stale/unrelated summary into whatever state
        # is live now (e.g. a brand-new campaign after /coc newgame
        # inheriting leftover summary text from the campaign it replaced).
        # Skipping entirely costs nothing but retrying this trim on a later
        # turn — never a correctness problem, and never a partial write.
        n = len(dropped_chunk)
        if not dropped_chunk or latest_state.log[:n] != dropped_chunk:
            observability.event(
                "maintenance.commit_skipped", level=logging.WARNING,
                reason="log_prefix_changed", source_revision=source_revision,
                current_revision=latest_state.state_revision,
                requested_timeline_id=timeline_id,
                current_timeline_id=latest_timeline_id,
                idempotency_key_hash=idempotency_hash,
            )
            ctx.skip_save()
            return "stale_log_prefix"
        latest_state.log = latest_state.log[n:]
        latest_state.campaign_summary = campaign_summary
        if prepared is not None:
            memory_appended = memory_rag.append_memory_parts_tx(
                ctx.conn, group_id, prepared, timeline_id=timeline_id,
                idempotency_key=idempotency_key, source_revision=source_revision,
            )
        else:
            memory_appended = memory_rag.append_memory_tx(
                ctx.conn,
                group_id,
                "\n".join(f"{m['role']}: {m['content']}" for m in dropped_chunk),
                timeline_id=timeline_id,
                idempotency_key=idempotency_key,
                source_revision=source_revision,
                embedding=embedding,
                source_messages=history_authority.memory_source_messages(dropped_chunk),
            )
        ctx.set_result({"memory_appended": bool(memory_appended)})
        return "committed"

    try:
        with turn_phases.phase("memory_write"):
            result = state_transaction.mutate(
                group_id, commit_trim, reason="maintenance", expected_timeline=timeline_id,
            )
    except state_transaction.CorruptStateError as exc:
        observability.event(
            "maintenance.commit_skipped",
            level=logging.WARNING,
            reason="corrupt_group_state",
            source_revision=source_revision,
            requested_timeline_id=timeline_id,
            idempotency_key_hash=idempotency_hash,
            error_type=type(exc.__cause__ or exc).__name__,
        )
        return "corrupt_group_state"
    if result.outcome is state_transaction.Outcome.STALE_TIMELINE:
        observability.event(
            "maintenance.commit_skipped", level=logging.WARNING,
            reason="timeline_mismatch", source_revision=source_revision,
            requested_timeline_id=timeline_id,
            current_timeline_id=result.timeline_id,
            idempotency_key_hash=idempotency_hash,
        )
        return "stale_timeline"
    if result.value != "committed":
        return str(result.value)
    observability.event(
        "maintenance.commit_completed", source_revision=source_revision,
        committed_revision=result.revision,
        memory_appended=result.result.get("memory_appended"),
        requested_timeline_id=timeline_id,
        current_timeline_id=result.timeline_id,
        idempotency_key_hash=idempotency_hash,
    )
    return "committed"


# Guards against more than one run_post_turn_maintenance pass running
# concurrently for the same group_id — see that function's own docstring.
_maintenance_in_flight: set[str] = set()


def run_scene_digest_maintenance(group_id: str) -> None:
    with locks.get_state_lock(group_id):
        mutation_admission.assert_admitted(group_id)
        state = load_state(group_id)
        latest = scene_digest.latest_digest(group_id, state.timeline_id)
        chapter_changed = latest is None or latest.get("scene_label") != (state.active_chapter_id or state.scenario_title or "目前場景")
        current_log_length = len(state.log)
        previous_log_length = latest.get("log_length", 0) if latest else 0
        # Log maintenance can intentionally shrink the in-memory log. Treat
        # that as a new baseline; otherwise the old larger watermark would
        # make this subtraction negative and periodic digests would stop.
        log_was_trimmed = latest is not None and current_log_length < previous_log_length
        log_interval_reached = (
            latest is None
            or log_was_trimmed
            or current_log_length - previous_log_length >= SCENE_DIGEST_TURN_INTERVAL
        )
        if not (chapter_changed or log_interval_reached):
            return
        scene_digest.create_digest(state)


def _run_post_turn_maintenance(group_id: str) -> dict[str, object]:
    """Called after every turn (see app/services/post_turn.py's
    spawn_post_turn_maintenance, which now fires this as an independent
    background task rather than awaiting it inline). Only does real work
    once the log actually crosses the trim threshold — every other call is a
    cheap no-op. `_maintenance_in_flight` skips a call outright if a pass is
    already running for this group_id: without it, several turns landing
    back-to-back while the log is still above threshold would each spawn
    their own full pass (duplicate LLM summarization + embedding API costs),
    racing on the same log/memory-chunk data. The worker prepares the summary
    and embedding outside the commit gate, then appends the prepared chunk
    through memory_rag.append_memory_tx inside the same SQLite transaction as
    the state trim. The in-flight guard avoids duplicate slow work; atomicity
    comes from the commit gate, not from this guard alone.

    The check-then-add on `_maintenance_in_flight` below is itself wrapped in
    `locks.get_state_lock(group_id)` — this function runs via
    `asyncio.to_thread` (see post_turn.spawn_post_turn_maintenance), i.e. on real OS
    worker threads, not just concurrent asyncio tasks, so the GIL making each
    individual `in`/`.add()` call atomic does NOT make the pair atomic: two
    threads could otherwise both observe `group_id not in
    _maintenance_in_flight` before either adds it, both proceed, and run two
    overlapping passes anyway — exactly the failure mode this guard exists
    to prevent."""
    with locks.get_state_lock(group_id):
        if group_id in _maintenance_in_flight:
            return {"skipped": True}
        _maintenance_in_flight.add(group_id)
    result: dict[str, object] = {
        "summary_updated": False,
        "embedding_updated": False,
        "state_saved": False,
        "commit_status": "not_started",
    }
    try:
        run_scene_digest_maintenance(group_id)
        try:
            memory_rag.backfill_embeddings(group_id)
        except Exception:  # an optional index must never stop the trim below
            _logger.exception("Memory embedding backfill failed for %s", group_id)
        with locks.get_state_lock(group_id):
            latest_state = load_state(group_id)
            if len(latest_state.log) <= MAX_LOG_TURNS * 4:
                return result
            keep_from = -MAX_LOG_TURNS * 2
            base_summary = latest_state.campaign_summary
            timeline_id = latest_state.timeline_id or f"legacy-{group_id}"
            source_revision = latest_state.state_revision
            log_snapshot = [dict(message) for message in latest_state.log]
            dropped_chunk = log_snapshot[:keep_from]

        # Rolling summarization (see summarize_log_chunk above): fold the
        # chunk about to be dropped into campaign_summary *before* dropping
        # it, instead of just discarding it — this is the one rare turn every
        # ~MAX_LOG_TURNS*2 turns that pays for an extra (cheap) LLM call, so
        # early plot points survive past what the verbatim log can hold.
        campaign_summary = summarize_log_chunk(base_summary, dropped_chunk)
        # Also persist the chunk's *original* wording into the searchable
        # memory index (app/memory_rag.py) — campaign_summary alone would
        # keep recompressing an already-compressed summary on every future
        # trim, eroding fine detail a little more each pass; this keeps the
        # verbatim text retrievable via search_memory even after that.
        formatted_chunk = "\n".join(f"{m['role']}: {m['content']}" for m in dropped_chunk)
        prepared = memory_rag.prepare_memory(dropped_chunk, history_authority.memory_source_messages(dropped_chunk))
        embedding = prepared.embeddings[0] if len(prepared.embeddings) == 1 else None
        chunk_digest = hashlib.sha256(formatted_chunk.encode("utf-8")).hexdigest()[:24]
        commit_status = _persist_memory_maintenance_state(
            group_id,
            campaign_summary,
            dropped_chunk,
            timeline_id=timeline_id,
            base_summary=base_summary,
            source_revision=source_revision,
            idempotency_key=f"{timeline_id}:{source_revision}:{chunk_digest}",
            embedding=embedding,
            prepared=prepared,
        )
        observability.event(
            "maintenance.result.completed",
            source_revision=source_revision,
            requested_timeline_id=timeline_id,
            commit_status=commit_status,
            summary_changed=campaign_summary != base_summary,
            embedding_prepared=all(vector is not None for vector in prepared.embeddings),
            embedding_parts=len(prepared.parts),
            embedding_failure=prepared.failure.reason if prepared.failure else None,
        )
        result["commit_status"] = commit_status
        result["summary_updated"] = commit_status in {"committed", "duplicate"} and campaign_summary != base_summary
        result["embedding_updated"] = commit_status in {"committed", "duplicate"} and all(
            vector is not None for vector in prepared.embeddings)
        result["state_saved"] = commit_status in {"committed", "duplicate"}
        return result
    finally:
        with locks.get_state_lock(group_id):
            _maintenance_in_flight.discard(group_id)


def run_post_turn_maintenance(group_id: str) -> dict[str, object]:
    """``_run_post_turn_maintenance`` with its own phase timeline (embedding, memory search and write)."""
    with turn_phases.timeline("maintenance", turn_id=observability.new_id("maint"), player_id="", campaign_id=group_id):
        return _run_post_turn_maintenance(group_id)


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


def summarize_log_chunk(current_summary: str, old_messages: list[dict[str, Any]]) -> str:
    """Rolling summarization — called only on the rare maintenance
    turn where state.log is about to be trimmed past MAX_LOG_TURNS*4. Folds
    old_messages (the chunk about to be dropped) into current_summary via one
    forced tool call, dispatched through whichever LLM_PROVIDER is configured
    (same analyze_text pattern as app/pregen_extractor.py/scenario_compare.py
    — never hard-coded to one vendor's client, since this project's whole
    point is LLM_PROVIDER being freely switchable).

    Degrades gracefully: no provider configured, the call raises, or it
    returns nothing usable all fall back to returning current_summary
    unchanged (logged, not raised) — a failed summarization should never
    crash the turn or lose the existing summary, only leave it stale."""
    provider = conversation_provider()
    if provider is None:
        return current_summary
    try:
        formatted_history = history_authority.summary_input(old_messages)
        result = provider.analyze_text(
            formatted_history,
            _SUMMARY_TOOL,
            "你是一個 TRPG 遊戲紀錄員。請將「待整合的舊對話」融合進「現有摘要」，"
            "更新成一份精煉的對話與敘事摘要，用 report_summary 工具回報。"
            "已送出敘事只證明當時如此描述；玩家聲明只證明曾如此聲稱。"
            "不得把無來源的物品、數量、位置、線索或 NPC 身分寫成確定世界事實。"
            "僅明確 KP 正典與可核對的已提交事件能作權威；與當前狀態或劇本衝突時以後者為準。\n\n"
            "若有【敘事更正】或 superseded 標記，應移除被取代的舊描述；更正仍是呈現修復，不能自動創造劇本事實。\n\n"
            f"【現有摘要（同樣未經驗證）】\n{current_summary or '（目前尚無摘要）'}",
        )
        summary = (result or {}).get("summary", "").strip()
        return summary or current_summary
    except Exception:
        _logger.exception("summarize_log_chunk failed, keeping previous summary unchanged")
        return current_summary


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
