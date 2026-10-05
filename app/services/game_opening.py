"""Own the /coc start transition and its source-bound extraction handoff.

Readiness healing is an independent pre-opening commit. Scripted checks,
history, and game_started share a later authoritative commit; fallback delegates
its final commit to the existing Keeper turn pipeline. Bound sources release
the router's mutation lock only during read-only opening extraction.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app import (
    check_lifecycle,
    keeper,
    locks,
    observability,
    opening_identity,
    scenario_intro,
    turn_commit,
)
from app.agents import supervisor
from app.check_identity import PendingCheckBlocker
from app.keeper_tools import resource_bridge
from app.models import GroupState
from app.repositories import state_transaction
from app.repositories.group_state import load_state
from app.services import history_authority
from app.services.character_service import (
    OpeningReadiness,
    heal_character,
    snapshot_readiness_roster,
)

OpeningOutcome = Literal["rejected", "scripted", "fallback", "silent"]
OpeningRejectionReason = Literal[
    "combat_unsettled", "no_scenario", "no_characters", "pending_pregen_luck", "already_started",
    "source_changed", "timeline_changed", "character_set_changed", "admission_changed",
] | PendingCheckBlocker


@dataclass(frozen=True)
class OpeningResult:
    outcome: OpeningOutcome
    reason: OpeningRejectionReason | None = None
    name: str = ""
    text: str = ""
    check_reason: str = ""
    private_messages: tuple[tuple[str, str], ...] = ()
    image_requests: tuple[tuple[str | None, int], ...] = ()


@dataclass(frozen=True)
class OpeningExtractionToken:
    timeline_id: str
    source_hash: str
    context: opening_identity.OpeningContext
    character_set: opening_identity.OpeningParticipants


@asynccontextmanager
async def _direct_scope(conversation_id: str) -> AsyncIterator[None]:
    """Keep direct, non-Router callers serialized without queue notice/hooks."""
    async with locks.get_conversation_lock(conversation_id):
        yield


def _admission(state: GroupState) -> OpeningResult | None:
    replacement_block = resource_bridge.guard_replacement(state)
    if replacement_block:
        return OpeningResult("rejected", reason="combat_unsettled", text=replacement_block)
    if not state.active or not state.scenario_text:
        return OpeningResult("rejected", reason="no_scenario")
    if not state.characters:
        return OpeningResult("rejected", reason="no_characters")
    if state.pending_pregen_luck:
        names = "、".join(
            state.characters_by_id[character_id].name
            for character_id in state.pending_pregen_luck.values()
            if character_id in state.characters_by_id
        ) or "部分角色"
        return OpeningResult("rejected", reason="pending_pregen_luck", name=names)
    if state.game_started:
        return OpeningResult("rejected", reason="already_started")
    return None


_FALLBACK_PROMPT = (
    "（守密人，遊戲即將開始，劇本沒有寫現成的開場白，需要你自己撰寫一段。這份劇本沒有"
    "明確的「序幕」或「開場」段落可以直接查到，不代表劇本沒有背景資訊——如果目前是檢索模式，"
    "請呼叫 search_scenario 查詢劇本的背景設定、調查員的委託／緣由、故事開始的地點等關鍵字"
    "（例如劇本標題、背景、委託人、開場地點），根據查到的背景資訊撰寫開場白，不要因為查不到"
    "「開場」兩個字面就直接放棄。撰寫一段開場白，把調查員們帶入故事的起點——描述他們此刻"
    "身處的場景、氛圍，以及是什麼把他們捲進這個劇本裡，控制在三百字以內，用第二人稱「你」"
    "對調查員說話。這是遊戲的第一段敘述，還沒有任何人採取行動，不要假設玩家已經做了什麼、"
    "也不要在這段話裡問問題或要求玩家回覆什麼——單純把場景鋪陳出來即可。）"
)


async def open_game(
    conversation_id: str,
    actor_id: str,
    *,
    on_readiness: Callable[[OpeningReadiness], Awaitable[None]],
    mutation_scope: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    on_completion: Callable[[OpeningResult], Awaitable[None]] | None = None,
) -> OpeningResult:
    """Begin once; Router supplies short mutation scopes around prepare/apply.

    The callback is transport-neutral roster data and is awaited after healing
    commits but before extraction starts. The caller renders/delivers the final
    result; it does not prepare, commit, or coordinate either opening path.
    Legacy states without a bound source hash keep the original coarse lock.
    """
    async def complete(result: OpeningResult) -> OpeningResult:
        if on_completion is not None:
            await on_completion(result)
        return result

    async def extract(text: str) -> dict[str, Any]:
        with observability.span("opening.extract"):
            return await asyncio.to_thread(scenario_intro.extract_opening_narration, text)

    async def stale(reason: OpeningRejectionReason) -> OpeningResult:
        observability.event("opening.extract.stale_discarded", reason=reason)
        return await complete(OpeningResult("rejected", reason=reason))

    scope = mutation_scope or (lambda: _direct_scope(conversation_id))

    async with scope():
        state = load_state(conversation_id)
        rejection = _admission(state)
        if rejection:
            return await complete(rejection)
        with locks.get_state_lock(conversation_id):
            state = load_state(conversation_id)
            rejection = _admission(state)
            healed_notes: dict[str, list[str]] = {}
            if not rejection:
                for owner_id, char in state.characters.items():
                    notes = heal_character(char)
                    if notes:
                        healed_notes[owner_id] = notes
                if healed_notes:
                    state_transaction.commit_snapshot(state)
                token = OpeningExtractionToken(
                    state.timeline_id, state.active_scenario_source_hash,
                    opening_identity.context_identity(state),
                    opening_identity.participant_identity(state),
                )
                extraction_text = state.scenario_text
                readiness = snapshot_readiness_roster(state, healed_notes)
        if rejection:
            return await complete(rejection)
        await on_readiness(readiness)
        if not token.source_hash:
            opening_data = await extract(extraction_text)
            result = (_commit_scripted(conversation_id, opening_data)
                      if opening_data["found"] else await _run_fallback(conversation_id, actor_id, state))
            return await complete(result)

    opening_data = await extract(extraction_text)
    async with scope():
        state = load_state(conversation_id)
        if state.game_started:
            return await stale("already_started")
        if state.timeline_id != token.timeline_id:
            return await stale("timeline_changed")
        if state.active_scenario_source_hash != token.source_hash:
            return await stale("source_changed")
        if opening_identity.context_identity(state) != token.context:
            return await stale("source_changed")
        if opening_identity.participant_identity(state) != token.character_set:
            return await stale("character_set_changed")
        rejection = _admission(state)
        if rejection:
            observability.event("opening.extract.stale_discarded", reason="admission")
            return await complete(rejection)
        if opening_data["found"]:
            result = _commit_scripted(conversation_id, opening_data, token=token)
        else:
            result = await _run_fallback(conversation_id, actor_id, state, token=token)
        return await complete(result)


def _apply_scripted_state(state: GroupState, opening_data: dict[str, Any]) -> OpeningResult:
    opening_text = opening_data["text"]
    opening_check = opening_data.get("opening_check")
    if opening_check:
        candidates: dict[str, dict[str, Any]] = {}
        for owner_id, char in state.characters.items():
            if opening_check["type"] == "skill":
                candidates[owner_id] = {
                    "type": "skill", "skill": opening_check["skill"],
                    "skill_value": keeper.resolve_skill_value(
                        char, opening_check["skill"], register_unknown=False,
                    ),
                    "bonus_dice": 0, "penalty_dice": 0,
                    "difficulty": "regular", "pushed": False,
                }
            else:
                candidates[owner_id] = {
                    "type": "sanity",
                    "loss_success": opening_check.get("loss_success", "0"),
                    "loss_failure": opening_check.get("loss_failure", "1d4"),
                }
        registrations = check_lifecycle.register_many(
            state, candidates, source={"action_context": opening_check.get("reason", "")},
        )
        blocked = next(
            ((owner_id, entry.blocker) for owner_id, entry in registrations.items()
             if entry.status == "blocked"), None
        )
        if blocked:
            owner_id, reason = blocked
            assert reason is not None
            return OpeningResult(
                "rejected", reason=reason, name=state.characters[owner_id].name,
            )
    if opening_check and opening_check["type"] == "skill":
        for char in state.characters.values():
            keeper.resolve_skill_value(char, opening_check["skill"])
    turn_id = str(observability.current_context().get("turn_id") or uuid4().hex)
    state.log.append(history_authority.annotate_entry(
        {"role": "user", "content": "守密人：（遊戲開始，請朗讀開場白）"},
        turn_id=turn_id, timeline_id=state.timeline_id,
        record_kind="opening_instruction", authority="claim",
    ))
    state.log.append(history_authority.annotate_entry(
        {"role": "assistant", "content": opening_text},
        turn_id=turn_id, timeline_id=state.timeline_id,
    ))
    state.game_started = True
    return OpeningResult(
        "scripted", text=opening_text,
        check_reason=opening_check.get("reason", "") if opening_check else "",
    )


def _commit_scripted(
    conversation_id: str, opening_data: dict[str, Any],
    *, token: OpeningExtractionToken | None = None,
) -> OpeningResult:
    if token is None:
        with locks.get_state_lock(conversation_id):
            state = load_state(conversation_id)
            if state.game_started:
                return OpeningResult("silent")
            result = _apply_scripted_state(state, opening_data)
            if result.outcome == "scripted":
                state_transaction.commit_snapshot(state)
            return result

    def apply(ctx: state_transaction.TxContext) -> OpeningResult:
        if ctx.state.game_started:
            ctx.reject("already_started")
        if ctx.state.active_scenario_source_hash != token.source_hash:
            ctx.reject("source_changed")
        if opening_identity.context_identity(ctx.state) != token.context:
            ctx.reject("source_changed")
        if opening_identity.participant_identity(ctx.state) != token.character_set:
            ctx.reject("character_set_changed")
        rejection = _admission(ctx.state)
        if rejection:
            ctx.skip_save()
            return rejection
        result = _apply_scripted_state(ctx.state, opening_data)
        if result.outcome != "scripted":
            ctx.skip_save()
        return result

    committed = state_transaction.mutate(
        conversation_id, apply, reason="game_opening",
        expected_timeline=token.timeline_id,
    )
    if committed.outcome is state_transaction.Outcome.STALE_TIMELINE:
        return OpeningResult("rejected", reason="timeline_changed")
    if (committed.outcome is state_transaction.Outcome.REJECTED
            and committed.reason in {"source_changed", "character_set_changed", "already_started"}):
        reason: OpeningRejectionReason = (
            "source_changed" if committed.reason == "source_changed" else
            "character_set_changed" if committed.reason == "character_set_changed" else
            "already_started"
        )
        return OpeningResult("rejected", reason=reason)
    if not committed.ok:
        raise state_transaction.StateTransactionFailed(committed)
    assert committed.value is not None
    return committed.value


async def _run_fallback(
    conversation_id: str, actor_id: str, state: GroupState,
    *, token: OpeningExtractionToken | None = None,
) -> OpeningResult:
    async with locks.narrating_turn(conversation_id):
        fresh_state = keeper.refresh_tool_state(state)
        if fresh_state.game_started:
            return OpeningResult("silent")
        try:
            keeper_reply, private_messages, image_requests = await supervisor.run_turn(
                state=fresh_state,
                user_id=actor_id,
                display_name="守密人",
                text=_FALLBACK_PROMPT,
                resolved_location=None,
                speaker_role="player",
                conversation_id=conversation_id,
                turn_kind="opening_fallback",
                expected_opening_source_hash=token.source_hash if token else None,
                expected_opening_context=token.context if token else None,
                expected_opening_participants=token.character_set if token else None,
            )
        except turn_commit.OpeningStartRejected as exc:
            if exc.reason == "source_changed":
                return OpeningResult("rejected", reason="source_changed")
            if exc.reason == "already_started":
                return OpeningResult("rejected", reason="already_started")
            if exc.reason == "timeline_changed":
                return OpeningResult("rejected", reason="timeline_changed")
            if exc.reason == "character_set_changed":
                return OpeningResult("rejected", reason="character_set_changed")
            return OpeningResult("rejected", reason="admission_changed")
    return OpeningResult(
        "fallback", text=keeper_reply,
        private_messages=tuple(private_messages), image_requests=tuple(image_requests),
    )
