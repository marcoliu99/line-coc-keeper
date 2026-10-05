"""An explicit OOC repair is admitted before any gameplay action."""
import asyncio
from unittest.mock import AsyncMock, patch

from app import db, memory_rag, prompt_builder
from app.commands import router
from app.models import Character, GroupState
from app.repositories.group_state import load_state, save_state
from app.services import (
    correction_adjudication,
    correction_summary,
    history_reconciliation,
    narrative_corrections,
    natural_corrections,
)


def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "corrections.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    state = GroupState("natural-correction", timeline_id="timeline-one", game_started=True)
    save_state(state)
    narrative_corrections.record_message(state, "123456", "你聞到煙味；那本日記是重要線索。")
    return state


def test_ooc_admission_separates_action_and_correction():
    assert natural_corrections.classify("我打開門走進去") == ""
    assert natural_corrections.classify("你剛才看見什麼？") == ""
    assert natural_corrections.classify("你剛才說錯了，我要打開門") == "clarify"
    assert natural_corrections.classify("你剛才說錯了，我沒有上樓") == "review"
    assert natural_corrections.classify("你剛才說錯了，腐紙味不是煙味") == "presentation"


def test_harmless_repair_is_presentation_and_invalidates_provider_chain(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.openai_previous_response_id = "old-response"
    reply, handled = natural_corrections.submit(state, "player", "你剛才說錯了，腐紙味不是煙味")
    assert handled and "已依玩家指正" in reply
    saved = load_state(state.group_id)
    assert saved.narrative_corrections[-1]["status"] == "approved"
    assert saved.narrative_corrections[-1]["supersedes"] == ["message:123456"]
    assert saved.log[-1]["authority"] == "presentation"
    assert saved.log[-2]["record_kind"] == "correction_request"
    assert saved.openai_previous_response_id == ""


def test_consequential_dispute_stays_an_allegation(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    reply, handled = natural_corrections.submit(state, "player", "你剛才說錯了，我沒有上樓")
    assert handled and "先核對" in reply
    saved = load_state(state.group_id)
    assert saved.narrative_corrections[-1]["status"] == "pending"
    assert saved.log[-1]["authority"] == "claim"


def test_no_target_receipt_never_mutates_game_state(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    with patch.object(narrative_corrections, "latest_receipt", return_value=None):
        reply, handled = natural_corrections.submit(state, "player", "你剛才說錯了，我沒有上樓")
    assert handled and "/coc correct" in reply
    assert not state.narrative_corrections


def test_explicit_reply_correction_uses_its_receipt_not_latest(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    narrative_corrections.record_message(state, "999999", "較新的守密人訊息")
    reply, handled = natural_corrections.submit(
        state, "player", "你剛才說錯了，腐紙味不是煙味", target_message_id="123456",
    )
    assert handled and "已依玩家指正" in reply
    assert load_state(state.group_id).narrative_corrections[-1]["target_message_id"] == "123456"


def test_only_exact_receipt_memory_is_marked_superseded(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    memory_rag.append_memory(
        state.group_id, "你聞到煙味；那本日記是重要線索。", timeline_id=state.timeline_id,
        source_messages=[{"turn_id": "turn-1"}], embedding=None,
    )
    memory_rag.append_memory(
        state.group_id, "你聞到煙味；那本日記是重要線索。", timeline_id=state.timeline_id,
        source_messages=[{"turn_id": "another-turn"}], embedding=None,
    )
    assert memory_rag.mark_superseded_receipt(
        state.group_id, state.timeline_id, turn_id="turn-1",
        excerpt="你聞到煙味；那本日記是重要線索。", correction_id="fix-1",
    ) == 1
    chunks = db.get_json("memory_chunks", state.group_id)
    assert chunks[0]["superseded_by"] == ["fix-1"]
    assert "superseded_by" not in chunks[1]


def test_summary_rebuild_uses_correction_and_current_revision(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.campaign_summary = "先前錯誤描述"
    reply, _ = natural_corrections.submit(state, "player", "你剛才說錯了，腐紙味不是煙味")
    assert "已依玩家指正" in reply
    with patch.object(correction_summary.keeper, "summarize_log_chunk", return_value="已更正為腐紙味") as summarize:
        asyncio.run(correction_summary.rebuild(state.group_id))
    saved = load_state(state.group_id)
    assert summarize.call_count == 1
    assert saved.campaign_summary == "已更正為腐紙味"
    assert saved.narrative_corrections[-1]["summary_rebuild_status"] == "done"


def test_router_handles_ooc_correction_before_map_or_supervisor(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.active = True
    save_state(state)
    reply = AsyncMock()
    with patch.object(router, "resolve_map_action") as movement, \
         patch.object(router.supervisor, "run_turn", AsyncMock()) as keeper_turn, \
         patch.object(router.correction_adjudication, "schedule") as adjudicate:
        asyncio.run(router._handle_ordinary_text_message_locked(
            state.group_id, "player", AsyncMock(return_value="Player"), reply,
            AsyncMock(), AsyncMock(), AsyncMock(), "你剛才說錯了，我沒有上樓",
        ))
    movement.assert_not_called()
    keeper_turn.assert_not_awaited()
    adjudicate.assert_called_once()
    assert "先核對" in reply.call_args.args[0]


def test_incidental_possession_correction_uses_inventory_without_plot_authority(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.characters["player"] = Character("Ada", "player")
    save_state(state)
    reply, handled = natural_corrections.submit(state, "player", "你剛才說錯了，我之前留下普通筆記本")
    assert handled and "未賦予劇本線索" in reply
    saved = load_state(state.group_id)
    assert "普通筆記本" in saved.characters["player"].carried_items
    assert saved.narrative_corrections[-1]["status"] == "approved"
    assert saved.narrative_corrections[-1]["inventory_item"] == "普通筆記本"
    assert natural_corrections.classify("你剛才說錯了，我之前拿到鑰匙") == "review"


def test_keeper_prompt_accepts_fuzzy_present_action_without_inventing_history():
    prompt = prompt_builder.build_static_prompt(GroupState("prompt-correction"))
    assert "不要求中譯逐字寫出鑰匙與門的配對" in prompt
    assert "不能只因 AI 舊敘事或摘要提過" in prompt


def test_legacy_reconciliation_requires_approved_receipt_and_preserves_log(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.log.append({"role": "assistant", "content": "你聞到煙味；那本日記是重要線索。"})
    state.narrative_corrections.append({
        "id": "approved-old", "status": "approved", "timeline_id": state.timeline_id,
        "target_message_id": "123456", "target_receipt": narrative_corrections.target_receipt(state, "123456"),
    })
    save_state(state)
    assert history_reconciliation.reconcile_group(state.group_id) == {
        "approved_receipts": 1, "newly_marked_log_entries": 1,
    }
    assert "superseded_by" not in load_state(state.group_id).log[-1]
    history_reconciliation.reconcile_group(state.group_id, apply=True)
    saved = load_state(state.group_id)
    assert saved.log[-1]["content"] == "你聞到煙味；那本日記是重要線索。"
    assert saved.log[-1]["superseded_by"] == ["approved-old"]
    assert saved.log[-1]["authority"] == "presentation"


def test_source_backed_key_omission_can_repair_inventory(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.characters["player"] = Character("Ada", "player")
    state.scenario_text = "The landlord hands the investigators the keys to the house."
    state.narrative_corrections.append({
        "id": "key-fix", "status": "pending", "timeline_id": state.timeline_id,
        "target_message_id": "123456", "target_receipt": narrative_corrections.target_receipt(state, "123456"),
        "reporter_id": "player", "issue": "你剛才說錯了，我之前拿到鑰匙",
    })
    save_state(state)
    evidence = {"narration": "你沒有鑰匙", "scenario:1": "The landlord hands the investigators the keys to the house."}
    output = {"decision": "approve", "evidence": ["narration", "scenario:1"],
              "reason": "劇本已交付", "resolution": "Ada 持有鑰匙。",
              "claims": [{"kind": "item", "name": "鑰匙", "investigator": "Ada"}]}
    ruling = correction_adjudication._validated(state, evidence, output)
    assert ruling.item_repair == ("Ada", "鑰匙")
    asyncio.run(correction_adjudication._apply(state.group_id, state.timeline_id, "key-fix", ruling))
    saved = load_state(state.group_id)
    assert "鑰匙" in saved.characters["player"].carried_items
    assert saved.narrative_corrections[-1]["status"] == "approved"
    assert saved.narrative_corrections[-1]["inventory_source_ref"]["scenario_id"] == "scenario-text"


def test_key_mentioned_without_grant_cannot_repair_inventory():
    state = GroupState("key-evidence", timeline_id="t")
    state.characters["player"] = Character("Ada", "player")
    evidence = {"narration": "你沒有鑰匙", "scenario:1": "A key is hidden beneath the cellar floor."}
    output = {"decision": "approve", "evidence": ["narration", "scenario:1"],
              "reason": "玩家說拿過", "resolution": "Ada 持有鑰匙。",
              "claims": [{"kind": "item", "name": "鑰匙", "investigator": "Ada"}]}
    assert correction_adjudication._validated(state, evidence, output).decision == "undecided"


def test_dialogue_is_not_an_error_report():
    assert natural_corrections.classify('你說我該去哪裡？') == ''
    assert natural_corrections.classify('你說我剛才講的話有道理嗎？') == ''


def test_oversized_presentation_correction_cannot_block_play(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    reply, handled = natural_corrections.submit(state, 'player', '你剛才說錯了，煙味' + '味道' * 1000)
    assert handled and '太長' in reply
    assert not state.narrative_corrections
    assert not narrative_corrections.projection(state)[1]


def test_many_presentation_corrections_keep_projection_bounded(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    for i in range(100):
        natural_corrections.submit(state, 'player', f'你剛才說錯了，煙味應是腐紙味 {i}')
    assert not narrative_corrections.projection(state)[1]
    assert len(state.narrative_corrections) < 100


def test_summary_retries_after_revision_race(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    natural_corrections.submit(state, 'player', '你剛才說錯了，腐紙味不是煙味')
    calls = []
    def summarize(*_args):
        calls.append(1)
        if len(calls) == 1:
            latest = load_state(state.group_id)
            latest.log.append({'role': 'user', 'content': 'new action'})
            save_state(latest)
        return '已更正為腐紙味'
    with patch.object(correction_summary.keeper, 'summarize_log_chunk', side_effect=summarize):
        asyncio.run(correction_summary.rebuild(state.group_id))
    assert len(calls) == 2
    assert load_state(state.group_id).narrative_corrections[-1]['summary_rebuild_status'] == 'done'


def test_item_grant_does_not_combine_unrelated_passages(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.characters['player'] = Character(name='Ada', owner_id='player')
    claims = [{'kind': 'item', 'name': '鑰匙', 'investigator': 'Ada'}]
    with patch.object(correction_adjudication, '_claim_holds', return_value=False):
        assert not correction_adjudication._approval_holds(state, ['scenario:1', 'scenario:2'], 'Ada 持有鑰匙', claims,
            {'scenario:1': 'The landlord gives the investigators a map.', 'scenario:2': 'A key is hidden under the floor.'})
        assert not correction_adjudication._approval_holds(state, ['scenario:1'], 'Ada 持有鑰匙', claims,
            {'scenario:1': 'The landlord gives Bob a key.'})
