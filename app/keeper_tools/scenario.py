"""Keeper scenario records, image delivery, chapter transitions, and retrieval."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app import (
    memory_rag,
    observability,
    scenario_activation,
    scenario_index,
    scenario_library,
    scenario_rag,
    scenario_templates,
    spoiler_policy,
)
from app.config import (
    SCENARIO_RAG_EMBEDDING_MODEL,
    SCENARIO_RAG_EMBEDDING_WEIGHT,
    SCENARIO_RAG_TOP_K,
)
from app.models import GroupState

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall

# Preserve the existing logger name so query diagnostics and log filters do
# not change while handlers move out of keeper.py.
_logger = logging.getLogger("app.keeper")


def allowed_chapter_ids(state: GroupState) -> set[str] | None:
    if not spoiler_policy.is_spoiler_protection_enabled():
        return None
    return set(state.context_chapter_ids)


def record_fact_or_clue(call: ToolCall) -> dict[str, Any]:
    state = call.state
    tool_input = call.input
    name = call.name
    from app import keeper
    field_name = "established_facts" if name == "record_established_fact" else "known_clues"
    text_value = ((tool_input.get("fact") if name == "record_established_fact" else tool_input.get("clue")) or "").strip()
    if not text_value:
        return {"ok": False, "error": "內容不能是空字串"}
    visibility = tool_input.get("visibility", "public")
    if visibility not in ("public", "kp_only"):
        return {"ok": False, "error": "visibility 必須是 public 或 kp_only"}

    def verified_source(target_state: GroupState) -> dict[str, Any] | None:
        record_id = tool_input.get("source_record_id", "")
        quote = tool_input.get("source_quote", "")
        condition = tool_input.get("source_condition", "")
        if not isinstance(record_id, str) or not isinstance(quote, str) or quote.strip() != text_value:
            return None
        source_ref = scenario_templates.match_fact_source(target_state, record_id, quote)
        if source_ref is None:
            return None
        if condition == "resolved_check":
            event_id = tool_input.get("trigger_event_id", "")
            event = next((event for event in target_state.resolved_check_events
                          if event.get("event_id") == event_id
                          and event.get("timeline_id") == target_state.timeline_id
                          and "成功" in str(event.get("outcome", ""))), None)
            if event is None:
                return None
            source_ref = {**source_ref, "trigger_event_id": event_id}
            discovery_receipt = dict(event)
        elif condition == "observed_now":
            if not observability.current_context().get("turn_id"):
                return None
            source_ref = {**source_ref, "observed_turn_id": str(observability.current_context()["turn_id"])}
        elif condition == "unconditional":
            # A scenario rule can be known to the Keeper before the player
            # discovers it. An exact quotation alone cannot make it public.
            if visibility != "kp_only":
                return None
        else:
            return None
        from app.services.canonical_facts import validated_constraints
        return {
            "constraints": validated_constraints(tool_input.get("constraints"), quote),
            **({"discovery_receipt": discovery_receipt} if condition == "resolved_check" else {}),
            "verification_status": "verified", "source_kind": "scenario",
            "source_ref": source_ref, "timeline_id": target_state.timeline_id,
        }

    def _mutate_record(target_state: GroupState) -> Any:
        records = getattr(target_state, field_name)
        source = verified_source(target_state)
        existing = next((record for record in records
                         if record.get("text") == text_value
                         and record.get("visibility", "public") == visibility
                         and record.get("timeline_id", target_state.timeline_id) == target_state.timeline_id), None)
        if existing is not None:
            if source and (existing.get("verification_status") != "verified"
                           or existing.get("source_ref") != source["source_ref"]
                           or existing.get("timeline_id") != target_state.timeline_id):
                existing.setdefault("fact_id", f"fact:{uuid4().hex}")
                existing.update(source)
                return keeper.ToolStateMutation({"recorded": False, "promoted": True,
                                                 "record": existing}, should_save=True)
            return keeper.ToolStateMutation({"recorded": False, "records": records}, should_save=False)
        record = {
            "fact_id": f"fact:{uuid4().hex}",
            "text": text_value,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_event_id": tool_input.get("source_event_id") or uuid4().hex,
            "verification_status": "unverified",
            "visibility": visibility,
            "scene_id": "",
        }
        if source:
            record.update(source)
        records.append(record)
        return keeper.ToolStateMutation({"recorded": True, "record": record}, should_save=True)
    result = keeper.mutate_tool_state(state, _mutate_record)
    return {"ok": True, **result}


def search_scenario_images(call: ToolCall) -> dict[str, Any]:
    state = call.state
    tool_input = call.input
    speaker_role = call.speaker_role
    if not state.scenario_library_id:
        return {"ok": False, "error": "目前沒有選擇劇本庫項目"}
    assets = scenario_library.search_images(
        state.scenario_library_id,
        query=tool_input.get("query", ""),
        image_type=tool_input.get("image_type", ""),
        allowed_chapter_ids=allowed_chapter_ids(state),
    )
    # KP-only assets (see scenario_library._build_image_assets — currently
    # character_sheet pages, which may be NPC/villain stat blocks or a
    # pregen revealing a "secret" connection) are filtered out of what
    # ordinary play (speaker_role != "kp_assistant") can even discover,
    # not just what it can display — a player-facing search shouldn't
    # surface a KP-only page's existence any more than show_scenario_image
    # below should let them actually pull it up. §3.4 mechanism #3: this
    # ownership/visibility filter is privacy isolation, not spoiler
    # protection.
    if speaker_role != "kp_assistant":
        if spoiler_policy.is_privacy_isolation_enabled():
            assets = [a for a in (spoiler_policy.filter_public_record(a) for a in assets) if a is not None]
        else:
            # One WARNING per search call, not one per asset — a
            # library can have dozens of images, and filter_public_record
            # itself logs per-record (see its docstring).
            observability.event(
                "privacy.isolation.disabled", level=logging.WARNING, fn="search_scenario_images"
            )
    return {"ok": True, "assets": [{key: asset.get(key) for key in ("id", "page", "type", "tags", "description", "visibility")} for asset in assets]}


def show_scenario_image(call: ToolCall) -> dict[str, Any]:
    state = call.state
    tool_input = call.input
    speaker_role = call.speaker_role
    image_requests = call.image_requests
    from app import keeper
    if not state.scenario_library_id:
        return {"ok": False, "error": "目前沒有選擇劇本庫項目"}
    page = int(tool_input["page_number"])
    assets = scenario_library.search_images(
        state.scenario_library_id, allowed_chapter_ids=allowed_chapter_ids(state)
    )
    asset = next((item for item in assets if item.get("page") == page), None)
    if asset is None:
        return {"ok": False, "error": "該圖片不在目前章節 Context，不能展示"}
    if speaker_role != "kp_assistant" and spoiler_policy.filter_public_record(asset) is None:
        return {"ok": False, "error": "這一頁是 KP 專用資料，不能在一般遊戲流程中展示給玩家"}
    image_investigator: str = tool_input.get("investigator") or ""
    image_owner_id: str | None = None
    if image_investigator:
        char = keeper.find_character(state, image_investigator)
        if not char:
            return {"ok": False, "error": f"找不到角色「{image_investigator}」"}
        image_owner_id = char.owner_id
    image_requests.append((image_owner_id, page))
    return {"ok": True, "page": page, "asset_type": asset.get("type"), "target": "private" if image_owner_id else "public"}


def advance_scenario_chapter(call: ToolCall) -> dict[str, Any]:
    state = call.state
    from app import keeper
    context_holder: dict[str, Any] = {}
    def _advance(target_state: GroupState) -> dict:
        if not target_state.scenario_library_id:
            return {"ok": False, "error": "目前沒有選擇劇本庫項目"}
        next_id = scenario_library.next_chapter_id(target_state.scenario_library_id, target_state.active_chapter_id)
        if next_id is None:
            return {"ok": False, "error": "目前已是最後一個章節"}
        context = scenario_library.load_context(target_state.scenario_library_id, next_id)
        scenario_activation.install_context_fields(
            target_state, target_state.scenario_library_id, context,
            variant_id=target_state.scenario_variant_id,
            preserve_pregens=True,
        )
        context_holder.update(context)
        # After the assignment, or this reads the previous chapter's maps.
        artifact_notice = scenario_index.report_location_index(
            target_state.scenario_location_index, source="chapter_switch",
            scenario_title=target_state.scenario_title,
            scene_maps=target_state.scene_maps)
        old_timeline_id = target_state.timeline_id or f"legacy-{target_state.group_id}"
        target_state.openai_previous_response_id = ""
        target_state.openai_previous_response_timeline_id = ""
        observability.event(
            "provider.chain.reset",
            reason="scenario_chapter_advance",
            old_timeline_id=old_timeline_id,
            requested_timeline_id=old_timeline_id,
            provider="openai",
        )
        # Carried into the result, or a group that switches chapters
        # into an empty-artifact variant is told nothing at all.
        result = {"ok": True, "active_chapter_id": context["active_chapter_id"],
                  "context_chapter_ids": context["context_chapter_ids"]}
        if artifact_notice:
            result["notice"] = artifact_notice
        return result
    result = keeper.mutate_tool_state(state, _advance)
    if result.get("ok") and context_holder:
        refreshed = scenario_activation.refresh_after_commit(
            state.group_id, state.scenario_library_id, context_holder,
        )
        if not refreshed:
            result["notice"] = (result.get("notice", "") + "\n頁面圖片快取刷新失敗；章節已推進，請聯絡 KP 檢查圖片。").strip()
    return result


def search_scenario(call: ToolCall) -> dict[str, Any]:
    state = call.state
    tool_input = call.input
    speaker_role = call.speaker_role
    if not state.scenario_text:
        return {"ok": False, "error": "目前沒有載入劇本可以搜尋"}
    scenario_query = tool_input.get("query", "")
    # Plain text log, not a structured event field — the query is
    # free-form player-adjacent content (see observability.py's own
    # docstring on why those two channels are kept separate),
    # gated by LOG_TEXT_ENABLED like any other _logger call. Added
    # specifically because "did the Keeper just search the same
    # keyword twice in one turn" was previously impossible to
    # answer from the logs at all: rag.search's structured metrics
    # below only ever captured counts (candidate_count,
    # result_count, ...), never the query text itself.
    _logger.info("search_scenario query=%r", scenario_query)
    scenario_metrics: dict[str, Any] = {}
    with observability.span("rag.search", rag_kind="scenario", top_k=SCENARIO_RAG_TOP_K,
                            embedding_model=SCENARIO_RAG_EMBEDDING_MODEL,
                            embedding_weight=SCENARIO_RAG_EMBEDDING_WEIGHT, metrics=scenario_metrics):
        index, results = scenario_templates.search_for_state(state, scenario_query, top_k=SCENARIO_RAG_TOP_K, metrics=scenario_metrics,
                                                            source=tool_input.get("source", "auto"),
                                                            continuation=tool_input.get("continuation", ""),
                                                            principal=f"{speaker_role}:{tool_input.get('_retrieval_principal', state.kp_assistant_user_id or '')}")
        scenario_metrics.update(
            evidence_chars=sum(len(row["text"]) for row in results),
            budget_omitted=sum(row.get("budget_omitted", 0) for row in results),
            candidate_count=len(getattr(index, "chunks", ())),
            result_count=len(results),
            has_embeddings=getattr(index, "has_embeddings", None),
            index_cache=getattr(index, "index_cache", "unknown"),
        )
    completeness = [row for row in results if 'complete_for_action' in row]
    return {"ok": True, "results": scenario_rag.format_results(results),
            "complete_for_action": all(row['complete_for_action'] for row in completeness) if completeness else None,
            "evidence_record_ids": list({rid for row in completeness for rid in row.get("root_record_ids", [])}),
            "continuation_tokens": [row['continuation_token'] for row in completeness if row.get('continuation_token')]}


def search_memory(call: ToolCall) -> dict[str, Any]:
    state = call.state
    tool_input = call.input
    memory_query = tool_input.get("query", "")
    _logger.info("search_memory query=%r", memory_query)  # see search_scenario's comment above
    memory_metrics: dict[str, Any] = {}
    with observability.span("memory.search", rag_kind="memory", embedding_model=SCENARIO_RAG_EMBEDDING_MODEL,
                            embedding_weight=SCENARIO_RAG_EMBEDDING_WEIGHT, metrics=memory_metrics):
        results = memory_rag.search_memory(
            state.group_id,
            memory_query,
            timeline_id=state.timeline_id or f"legacy-{state.group_id}",
            metrics=memory_metrics,
        )
    return {"ok": True, "results": memory_rag.format_results(results)}
