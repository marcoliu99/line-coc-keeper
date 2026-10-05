from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app import (
    async_utils,
    db,
    memory_rag,
    observability,
    scenario_rag,
    scenario_templates,
)
from app.config import (
    EMBEDDING_REQUEST_TIMEOUT_SECONDS,
    RETRIEVAL_REUSE_TTL_SECONDS,
    SCENARIO_RAG_EMBEDDING_MODEL,
    SCENARIO_RAG_EMBEDDING_WEIGHT,
    SCENARIO_RAG_ENABLED,
    SCENARIO_RAG_TOP_K,
)
from app.domain.models import AgentMessage, SpeakerRole, TurnPayload
from app.keeper_tools import resource_bridge
from app.models import GroupState
from app.services import narrative_corrections, turn_phases

_logger = logging.getLogger(__name__)


def _resolved_events_for_character(
    state: GroupState, user_id: str, character_id: str, conversation_id: str
) -> list[dict[str, Any]]:
    timeline_id = state.timeline_id or f"legacy-{conversation_id}"
    return [
        dict(event)
        for event in state.resolved_check_events[-20:]
        if event.get("owner_id") == user_id
        and event.get("character_id") == character_id
        and event.get("timeline_id") == timeline_id
    ]


@dataclass(frozen=True)
class RetrievalPrefetch:
    """Proactive scenario/memory retrieval carried across the conversation lock.

    Only the retrieval travels. Everything else build_context assembles —
    state, character, resolved events, correction projection — is derived from
    mutable state and must be rebuilt from the snapshot read under the lock.

    `binding` is what the searches actually depended on. If any of it moved
    while the caller waited for the lock, the prefetch is discarded and the
    search runs again rather than answering from a stale scenario.
    """

    rag_context: str
    memory_context: str
    rag_status: str
    memory_status: str
    binding: tuple
    # Monotonic times of the search, so a turn can credit it to its own timeline (it ran while the turn queued).
    started_at: float = 0.0
    finished_at: float = 0.0


_GROUNDING_MAX = 64
_grounding: OrderedDict[tuple[str, str, str], tuple[float, int, RetrievalPrefetch]] = OrderedDict()
_grounding_sequence: dict[str, int] = {}
_grounding_lock = threading.Lock()


def remember_grounding(state: GroupState, user_id: str, message: AgentMessage) -> None:
    """Keep the scenario evidence an action turn gathered, for the continuation that follows its roll.

    Only a successful scenario search is kept. Every call marks "someone searched in this conversation", which is
    what makes an older entry stale for everybody else, and an unsuccessful search also drops this player's own
    older entry: a continuation must not reuse evidence that belongs to an earlier, different action.
    """
    payload = message.payload
    if payload.get("rag_status") != "success":
        with _grounding_lock:
            _grounding_sequence[state.group_id] = _grounding_sequence.get(state.group_id, 0) + 1
            _grounding.pop((state.group_id, state.timeline_id, user_id), None)
        return
    prefetch = RetrievalPrefetch(
        rag_context=payload.get("rag_context", ""), memory_context=payload.get("memory_context", ""),
        rag_status="success", memory_status=payload.get("memory_status", "disabled"),
        binding=retrieval_binding(state, user_id),
    )
    with _grounding_lock:
        sequence = _grounding_sequence[state.group_id] = _grounding_sequence.get(state.group_id, 0) + 1
        key = (state.group_id, state.timeline_id, user_id)
        _grounding[key] = (time.monotonic(), sequence, prefetch)
        _grounding.move_to_end(key)
        while len(_grounding) > _GROUNDING_MAX:
            _grounding.popitem(last=False)


def reusable_grounding(state: GroupState, user_id: str) -> RetrievalPrefetch | None:
    """The evidence this player's last action turn gathered, if nothing it depended on has moved since.

    Not reused when it is older than the TTL, when any other turn in the conversation has searched since, or when
    the scenario, chapter window, summary, memory, timeline, combat state or character differs (the binding).
    """
    key = (state.group_id, state.timeline_id, user_id)
    with _grounding_lock:
        entry = _grounding.get(key)
        current = _grounding_sequence.get(state.group_id, 0)
    if entry is None:
        return None
    stamped, sequence, prefetch = entry
    if time.monotonic() - stamped > RETRIEVAL_REUSE_TTL_SECONDS or sequence != current:
        return None
    if prefetch.binding != retrieval_binding(state, user_id):
        return None
    return prefetch


def retrieval_binding(state: GroupState, user_id: str) -> tuple:
    char = state.get_active_character(user_id)
    # Memory maintenance can append a chunk even when its new prose summary is
    # unchanged. Bind to the persisted source itself, rather than a proxy such
    # as campaign_summary or state_revision (which changes on unrelated turns).
    memory_chunks = db.get_json("memory_chunks", state.group_id) or []
    memory_version = hashlib.sha256(json.dumps(
        memory_chunks, ensure_ascii=False, sort_keys=True,
    ).encode()).digest()
    return (
        state.scenario_variant_id, state.scenario_title, state.scenario_library_id,
        state.active_chapter_id, tuple(state.context_chapter_ids),
        hashlib.sha256(state.scenario_text.encode()).digest(),
        hashlib.sha256(state.campaign_summary.encode()).digest(), memory_version,
        state.timeline_id, state.combat.active, char.character_id if char else None,
    )


async def prefetch_retrieval(
    state: GroupState, user_id: str, display_name: str, text: str,
    resolved_location: dict[str, Any] | None, speaker_role: SpeakerRole, conversation_id: str,
) -> RetrievalPrefetch:
    """Run this turn's retrieval before the conversation lock is taken.

    Runs the same code path as an ordinary turn and keeps only its retrieval,
    so the two cannot drift apart. Building the discarded payload costs well
    under a millisecond; the searches are the ~1s this moves off the lock.
    """
    binding = retrieval_binding(state, user_id)
    started = time.monotonic()
    message = await build_context(
        state=state, user_id=user_id, display_name=display_name, text=text,
        resolved_location=resolved_location, speaker_role=speaker_role,
        conversation_id=conversation_id,
    )
    return RetrievalPrefetch(
        rag_context=message.payload["rag_context"],
        memory_context=message.payload["memory_context"],
        rag_status=message.payload["rag_status"],
        memory_status=message.payload["memory_status"],
        binding=binding, started_at=started, finished_at=time.monotonic(),
    )


def search_scenario_context(
    state: GroupState, user_id: str, speaker_role: SpeakerRole, text: str, *,
    label: str = "context_builder.scenario_rag", accept_lexical: bool = False, top_k: int = SCENARIO_RAG_TOP_K,
    phase_name: str = "initial_retrieval",
) -> tuple[str, str]:
    """One scenario search for ``text``, formatted for the prompt, with its status.

    See app/keeper.py's search_scenario tool for why this is a plain _logger call, not a structured
    event field. The proactive per-turn search is distinct from the Keeper explicitly choosing to
    call the tool, so the log line carries ``label`` to keep the two apart when reading a turn's log.

    A proactive search accepts only a successful semantic source: BM25 remains available to the
    explicit search tool, so lexical fallback context is dropped unless the caller asks for it
    (``accept_lexical``; the one targeted recovery search does).
    """
    _logger.info("%s query=%r", label, text)
    metrics: dict[str, Any] = {}
    with turn_phases.phase(phase_name), observability.span(
        "rag.search",
        rag_kind="scenario",
        top_k=top_k,
        embedding_model=SCENARIO_RAG_EMBEDDING_MODEL,
        embedding_weight=SCENARIO_RAG_EMBEDDING_WEIGHT,
        metrics=metrics,
    ):
        if state.scenario_variant_id and state.scenario_variant_id != "original":
            from app import config, keeper, scenario_retrieval
            model = getattr(config, f"{config.LLM_PROVIDER.upper()}_MODEL", "unknown")
            budget = scenario_retrieval.request_budget(
                [keeper._build_static_prompt(state), keeper._build_dynamic_prompt(state, user_id, speaker_role=speaker_role),
                 keeper._tools_for_speaker_role(speaker_role), text], state.log, model, config.LLM_PROVIDER)
            budget_token = scenario_retrieval.BUDGET.set(min(budget, config.SCENARIO_PROACTIVE_TOKEN_BUDGET))
            model_token = scenario_retrieval.MODEL.set(model)
            try:
                index, results = scenario_templates.search_for_state(state, text, top_k=top_k, metrics=metrics,
                                                                     principal=f"{speaker_role}:{user_id}")
            finally:
                scenario_retrieval.MODEL.reset(model_token)
                scenario_retrieval.BUDGET.reset(budget_token)
        else:
            index, results = scenario_templates.search_for_state(state, text, top_k=top_k, metrics=metrics)
        metrics.update(
            evidence_chars=sum(len(row["text"]) for row in results),
            budget_omitted=sum(row.get("budget_omitted", 0) for row in results),
            candidate_count=len(getattr(index, "chunks", ())),
            result_count=len(results),
            has_embeddings=getattr(index, "has_embeddings", None),
            index_cache=getattr(index, "index_cache", "unknown"),
        )
        if not results:
            return "", "empty"
        if not accept_lexical and (
            metrics.get("has_embeddings") is False
            or metrics.get("query_embedding_status") == "fallback"
        ):
            return "", "fallback"
        return scenario_rag.format_results(results), "success"


async def build_context(
    state: GroupState,
    user_id: str,
    display_name: str,
    text: str,
    resolved_location: dict[str, Any] | None,
    speaker_role: SpeakerRole,
    conversation_id: str,
    prefetched: RetrievalPrefetch | None = None,
) -> AgentMessage:
    """
    Gathers all necessary state, history, RAG, and Memory context for the
    current turn, packaging it into an AgentMessage envelope.
    """
    # Resolve through the active binding instead of the legacy owner index so
    # a stale persisted mapping cannot make a sudo turn use the wrong sheet.
    char = state.get_active_character(user_id)
    if char:
        char = resource_bridge.effective(state, char)

    # 1. RAG Context (Scenario Text)
    # scenario_rag has no single query_scenario() entry point — it's a
    # build/cache-index-then-search API (see app/keeper.py's search_scenario
    # tool for the established call shape: get_index -> search ->
    # format_results). If the user isn't bound to a character yet (e.g. they
    # are just chatting in the lobby), we still run RAG but skip
    # location-based matching — scenario_rag.search has no location
    # parameter of its own; resolved_location stays available separately in
    # the payload for anything downstream that wants it.
    #
    # Gated on SCENARIO_RAG_ENABLED (default off) the same way
    # keeper._build_static_prompt is: when it's off, the full scenario text
    # (or the current chapter window's text, via the scenario library) is
    # already embedded directly in the static prompt, making this proactive
    # search redundant — and, depending on the configured embeddings
    # backend, a real per-turn cost for zero benefit.
    # Skipped entirely during active combat: combat_block (app/keeper.py's
    # dynamic prompt) already carries the full mechanical state — initiative
    # order, combatant HP, enemy abilities — that combat narration actually
    # needs, so proactive scenario/memory RAG's marginal narrative value is
    # low there, while its embeddings-API round trip (2-5s, see
    # docs/specs/enhancement/npc_attack_latency_design_spec.md) is a real, unconditional cost
    # on every combat turn regardless of whether anyone asked a
    # scenario/memory-dependent question. Not a correctness change outside
    # combat: state.combat.active is False for every turn this behaved
    # identically before.
    if prefetched is not None and prefetched.binding != retrieval_binding(state, user_id):
        # The scenario, timeline, combat state or active character moved while
        # the caller queued. Search again rather than narrate from stale
        # evidence; correctness outranks the second this was meant to save.
        observability.event("rag.prefetch.discarded", level=logging.INFO)
        prefetched = None

    rag_task = None
    if prefetched is None and SCENARIO_RAG_ENABLED and state.scenario_text and state.scenario_title and not state.combat.active:
        rag_task = asyncio.create_task(asyncio.to_thread(
            search_scenario_context, state, user_id, speaker_role, text, label="context_builder.scenario_rag",
        ))

    # 2. Memory Context (Past events) — same two-call shape as
    # app/keeper.py's search_memory tool: search_memory -> format_results.
    # See the scenario RAG block above for why this also skips during active
    # combat — this one is the more significant saving in practice, since it
    # runs on essentially every player turn with a bound character
    # (unconditional on SCENARIO_RAG_ENABLED), not just when that flag is on.
    memory_task = None
    if prefetched is None and char and not state.combat.active:
        def _run_memory_rag() -> tuple[str, str]:
            # See _run_scenario_rag's comment above — this one runs on
            # essentially every player turn with a bound character
            # (unconditional on SCENARIO_RAG_ENABLED), so it's very likely
            # to be *the* proactive contributor when a turn's log shows an
            # embeddings call the Keeper never explicitly asked for via the
            # search_memory tool.
            _logger.info("context_builder.memory_rag query=%r", text)
            metrics: dict[str, Any] = {}
            with turn_phases.phase("memory_search"), observability.span(
                "memory.search", rag_kind="memory", embedding_model=SCENARIO_RAG_EMBEDDING_MODEL,
                embedding_weight=SCENARIO_RAG_EMBEDDING_WEIGHT, metrics=metrics,
            ):
                search_kwargs: dict[str, Any] = {"metrics": metrics}
                # Never let a missing legacy timeline turn this production
                # prompt path into an unscoped group-wide memory search. A
                # fresh/legacy state uses its compatibility timeline until a
                # normal save initializes a new explicit timeline.
                search_kwargs["timeline_id"] = state.timeline_id or f"legacy-{conversation_id}"
                results = memory_rag.search_memory(conversation_id, text, **search_kwargs)
                if not results:
                    return "", "empty"
                if (
                    metrics.get("has_embeddings") is False
                    or metrics.get("query_embedding_status") == "fallback"
                ):
                    return "", "fallback"
                return memory_rag.format_results(results), "success"

        memory_task = asyncio.create_task(asyncio.to_thread(_run_memory_rag))

    async def _collect_rag_source(task: asyncio.Task | None, rag_kind: str) -> tuple[str, str]:
        if task is None:
            return "", "disabled"
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), EMBEDDING_REQUEST_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            async_utils.observe_background_task(task, operation=f"rag.{rag_kind}")
            observability.event("rag.source.cancelled", level=logging.WARNING, rag_kind=rag_kind)
            raise
        except asyncio.TimeoutError:
            async_utils.observe_background_task(task, operation=f"rag.{rag_kind}")
            observability.event(
                "rag.source.degraded", level=logging.WARNING,
                rag_kind=rag_kind, status="timeout",
                timeout_ms=EMBEDDING_REQUEST_TIMEOUT_SECONDS * 1000,
            )
            return "", "timeout"
        except Exception as exc:
            observability.event(
                "rag.source.degraded", level=logging.WARNING,
                rag_kind=rag_kind, status="error", error_type=type(exc).__name__,
            )
            _logger.exception("%s RAG failed; continuing without that source", rag_kind)
            return "", "error"
        if isinstance(result, tuple) and len(result) == 2:
            result, status = result
        else:
            status = "success" if result else "empty"
        if status != "success":
            observability.event("rag.source.degraded", level=logging.INFO, rag_kind=rag_kind, status=status)
            return "", status
        if not result:
            return "", "empty"
        return result, status

    # Await both sources at one explicit synchronization point.  The tasks
    # start before gather, so scenario and memory search/embedding can overlap;
    # return_exceptions=True keeps one optional source from discarding the
    # other.  Cancellation is handled by _collect_rag_source and propagated.
    rag_context = prefetched.rag_context if prefetched else ""
    memory_context = prefetched.memory_context if prefetched else ""
    rag_status = prefetched.rag_status if prefetched else "disabled"
    memory_status = prefetched.memory_status if prefetched else "disabled"
    tasks = [task for task in (rag_task, memory_task) if task is not None]
    if tasks:
        collected = await asyncio.gather(
            *(
                _collect_rag_source(task, rag_kind)
                for task, rag_kind in ((rag_task, "scenario"), (memory_task, "memory"))
                if task is not None
            ),
            return_exceptions=True,
        )
        result_by_kind = {
            kind: result
            for kind, result in zip(
                (kind for task, kind in ((rag_task, "scenario"), (memory_task, "memory")) if task is not None),
                collected,
                strict=True,
            )
            if not isinstance(result, BaseException)
        }
        for result in collected:
            if isinstance(result, asyncio.CancelledError):
                raise result
        rag_context, rag_status = result_by_kind.get("scenario", ("", "error"))
        memory_context, memory_status = result_by_kind.get("memory", ("", "error"))

    # 3. Compile the payload
    payload: TurnPayload = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "display_name": display_name,
        "speaker_role": speaker_role,
        "text": text,
        "resolved_location": resolved_location,
        "state": state,  # Reference to the current GroupState
        "character": char,  # Resource-effective copy during managed combat.
        "combat_provisional": resource_bridge.managed(state),
        # Historical finalized outcomes are deliberately separate from this
        # turn's tool results. Filter by owner, active character, and current
        # timeline so switched investigators never inherit each other's sheet history.
        "resolved_check_events": _resolved_events_for_character(
            state, user_id, char.character_id, conversation_id
        ) if char else [],
        "rag_context": rag_context,
        "memory_context": memory_context,
        "rag_status": rag_status,
        "memory_status": memory_status,
        "correction_context": narrative_corrections.projection(state)[0],
    }

    return AgentMessage(payload=payload)
