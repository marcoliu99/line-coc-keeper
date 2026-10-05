"""Own the /coc start transition while the router holds its conversation lock.

Readiness healing is an independent pre-opening commit. Scripted checks,
history, and game_started share a later snapshot commit; fallback delegates
its final commit to the existing Keeper turn pipeline. Slow extraction stays
inside the router's outer lock, but never holds the synchronous state lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app import check_lifecycle, keeper, locks, observability, scenario_intro
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
) -> OpeningResult:
    """Begin once, preserving the router's existing coarse scheduling lock.

    The callback is transport-neutral roster data and is awaited after healing
    commits but before extraction starts. The caller renders/delivers the final
    result; it does not prepare, commit, or coordinate either opening path.
    """
    state = load_state(conversation_id)
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

    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        healed_notes: dict[str, list[str]] = {}
        for owner_id, char in state.characters.items():
            notes = heal_character(char)
            if notes:
                healed_notes[owner_id] = notes
        if healed_notes:
            state_transaction.commit_snapshot(state)
    await on_readiness(snapshot_readiness_roster(state, healed_notes))

    opening_data: dict[str, Any] = await asyncio.to_thread(
        scenario_intro.extract_opening_narration, state.scenario_text
    )
    if opening_data["found"]:
        return _commit_scripted(conversation_id, opening_data)
    return await _run_fallback(conversation_id, actor_id, state)


def _commit_scripted(conversation_id: str, opening_data: dict[str, Any]) -> OpeningResult:
    opening_text = opening_data["text"]
    opening_check = opening_data.get("opening_check")
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        if state.game_started:
            return OpeningResult("silent")
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
        state_transaction.commit_snapshot(state)
    return OpeningResult(
        "scripted", text=opening_text,
        check_reason=opening_check.get("reason", "") if opening_check else "",
    )


async def _run_fallback(conversation_id: str, actor_id: str, state: GroupState) -> OpeningResult:
    async with locks.narrating_turn(conversation_id):
        fresh_state = keeper.refresh_tool_state(state)
        if fresh_state.game_started:
            return OpeningResult("silent")
        keeper_reply, private_messages, image_requests = await supervisor.run_turn(
            state=fresh_state,
            user_id=actor_id,
            display_name="守密人",
            text=_FALLBACK_PROMPT,
            resolved_location=None,
            speaker_role="player",
            conversation_id=conversation_id,
            turn_kind="opening_fallback",
        )
    return OpeningResult(
        "fallback", text=keeper_reply,
        private_messages=tuple(private_messages), image_requests=tuple(image_requests),
    )
