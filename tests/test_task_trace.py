"""S0 SDK-shaped deterministic baselines. No live latency/accuracy claims."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import config, db, keeper, observability
from app.agents import supervisor
from app.commands import router
from app.domain.models import AgentMessage
from app.models import Character, GroupState
from app.providers import openai_provider, registry
from app.repositories.group_state import save_state
from app.services import task_trace, turn_context


def response(text="", calls=()):
    return SimpleNamespace(id="fake-sdk", output=list(calls), output_text=text, status="completed", usage=None)


@pytest.mark.parametrize("route,expected_requests,expected_rounds", [
    ("roleplay", 1, 0), ("gameplay", 3, 1), ("ordinary_route", 3, 1), ("sudo_route", 3, 1), ("opening_fallback", 1, 0), ("resolved_check_followup", 1, 0),
])
def test_real_adapter_request_baseline(tmp_path, monkeypatch, route, expected_requests, expected_rounds):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    state = GroupState("baseline", timeline_id="timeline-fixture", game_started=True, active=True, kp_assistant_user_id="kp")
    state.characters["u"] = Character("Ada", "u")
    save_state(state)
    outputs = []
    gameplay = route in {"gameplay", "ordinary_route", "sudo_route"}
    if gameplay:
        outputs.extend([
            response(calls=[SimpleNamespace(type="function_call", name="add_carried_item",
                     arguments=json.dumps({"investigator": "Ada", "item": "手電筒"}), call_id="call-1")]),
            response(json.dumps({"disposition": "completed", "actor_character_id": turn_context.character_id(state, "u"),
                                 "evidence_refs": ["tool:1"]})),
        ])
    outputs.append(response("你把筆記收好，安靜地等待。"))
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=outputs)))

    @asynccontextmanager
    async def scope():
        yield client

    async def context(**kwargs):
        return AgentMessage(kwargs)

    async def deliver(group_id, reply, text, *args, **kwargs):
        await reply(text)
    monkeypatch.setattr(openai_provider, "OPENAI_API_KEY", "fake-only")
    monkeypatch.setattr(openai_provider, "_request_scope", scope)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(registry, "CONVERSATION_PROVIDERS", {"openai": openai_provider})
    kind = route if route in {"opening_fallback", "resolved_check_followup"} else "player_action"
    with task_trace.capture() as trace, \
         patch.object(supervisor.context_builder, "build_context", AsyncMock(side_effect=context)), \
         patch.object(supervisor.intent_router, "classify_intent", return_value="GAMEPLAY_ACTION" if gameplay else "PURE_ROLEPLAY"), \
         patch.object(keeper.scene_digest, "latest_digest", return_value=None), \
         patch.object(router, "_run_post_turn_maintenance_after_output", side_effect=deliver):
        if route in {"ordinary_route", "sudo_route"}:
            reply = AsyncMock()
            asyncio.run(router.handle_text_message(state.group_id, "kp" if route == "sudo_route" else "u",
                AsyncMock(return_value="Ada"), reply, AsyncMock(), AsyncMock(), AsyncMock(),
                "/coc sudo u act 拿起手電筒" if route == "sudo_route" else "拿起手電筒",
                allow_opaque_sudo_target=True))
            reply.assert_awaited()
        else:
            asyncio.run(supervisor.run_turn(state, "u", "Ada", "拿起手電筒", None, "player", state.group_id,
                                       turn_kind=kind, resolved_check_context={"roll": 42, "outcome": "成功"}))
    summary = trace.summary()
    assert client.responses.create.await_count == expected_requests
    assert summary["counts"]["generation_requests"] == expected_requests
    assert summary["counts"]["provider_attempts"] == expected_requests
    assert summary["counts"].get("executor_tool_response_rounds", 0) == expected_rounds
    assert summary["counts"].get("guard_calls", 0) == 0
    assert summary["narration_ready_ms"] is not None
    assert summary["mechanical_accuracy"] is None
    assert "拿起手電筒" not in json.dumps(summary, ensure_ascii=False)


def test_trace_counts_retry_without_inventing_an_extra_logical_request():
    with task_trace.capture() as trace:
        observability.event("llm.turn.started", agent="executor")
        observability.event("llm.request.started", logical_request_id="one", model="fixture", reasoning_effort="low")
        observability.event("llm.request.attempt.started", admission_wait_s=0.1)
        observability.event("llm.retry")
        observability.event("llm.request.attempt.started", admission_wait_s=0.2)
        observability.event("pending_button.send.completed", status="stale")
        observability.event("pending_button.send.completed", status="success")
    summary = trace.summary()
    assert summary["counts"]["generation_requests"] == 1
    assert summary["counts"]["provider_attempts"] == 2
    assert summary["counts"]["retry_events"] == 1
    assert summary["counts"]["controls_sent"] == 1
    assert summary["queue_ms"] == 300
    assert summary["requests"][0]["reasoning_effort"] == "low"


@pytest.mark.parametrize('mixed,expected', [(False, 1), (True, 2)])
def test_s2_ooc_and_mixed_actual_adapter_request_shapes(tmp_path, monkeypatch, mixed, expected):
    from app.agents.intent_router import route_request
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    monkeypatch.setattr(db, 'BACKUP_DIR', tmp_path / 'backups')
    db._ensure_tables()
    state = GroupState('segments-trace', timeline_id='t', active=True, game_started=True)
    state.characters['u'] = Character('Ada', 'u')
    save_state(state)
    text = '（OOC：為什麼要骰？）我等待同伴' if mixed else 'OOC：為什麼要骰？'
    outputs = []
    if mixed:
        outputs.append(response(json.dumps({'disposition': 'no_mechanics', 'actor_character_id': turn_context.character_id(state, 'u'), 'evidence_refs': ['state']})))
        outputs.append(response(json.dumps({'schema_version': 1, 'segments': [
            {'text': '說明' if s.mode == 'OOC' else '你等待同伴。', 'source_request_span_refs': [s.ref],
             'proposed_mode': s.mode, 'proposed_event_refs': []} for s in route_request(text, 'player').spans
        ]})))
    else:
        outputs.append(response('場外規則說明。'))
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=outputs)))

    @asynccontextmanager
    async def scope():
        yield client

    async def context(**kwargs):
        assert 'OOC' not in kwargs['text']
        return AgentMessage(kwargs)

    monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'fake-only')
    monkeypatch.setattr(openai_provider, '_request_scope', scope)
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'openai')
    monkeypatch.setattr(registry, 'CONVERSATION_PROVIDERS', {'openai': openai_provider})
    with task_trace.capture() as trace, patch.object(supervisor.context_builder, 'build_context', side_effect=context):
        asyncio.run(supervisor.run_turn(state, 'u', 'Ada', text, None, 'player', state.group_id))
    assert client.responses.create.await_count == expected
    summary = trace.summary()
    assert summary['counts']['generation_requests'] == expected
    assert summary['counts']['provider_attempts'] == expected
    assert summary['counts'].get('guard_calls', 0) == 0
    assert summary['counts'].get('executor_tool_response_rounds', 0) == 0
