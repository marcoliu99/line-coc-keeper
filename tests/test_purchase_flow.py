import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import db, keeper
from app.agents import executor
from app.commands.handlers import purchase as handler
from app.domain.models import AgentMessage
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import prompt_config, purchases, turn_context, turn_resolution


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    state = GroupState(group_id="purchase-test", timeline_id="t", kp_assistant_user_id="kp")
    state.characters["a"] = Character(name="Marco", owner_id="a", skills={"信用評級": 40}, carried_items=["筆記本"])
    state.characters["b"] = Character(name="Ken", owner_id="b")
    group_state.save_state(state)
    return state


def request(**changes):
    return {"investigator": "Marco", "shop": "雜貨店", "arrived": True,
            "arrival_basis": "離開報社，依已確立的路線抵達雜貨店，路程沒有待處理事件。",
            "source": "本局已確立雜貨店販售一般照明用品。", "mode": "lifestyle",
            "affordability": "信用評級 40 可負擔少量普通照明耗材。",
            "items": [{"name": "煤油", "quantity": 2}], "_turn_key": "turn-one", "_owner_id": "a", **changes}


def call(state, **changes):
    return keeper._execute_tool(state, "purchase_items", request(**changes), [], [])


def decision(state, kind, refs):
    return json.dumps({"disposition": kind, "actor_character_id": turn_context.character_id(state, "a"), "evidence_refs": refs})


def test_single_purchase_without_map_records_arrival_and_new_items(state):
    result = call(state)
    assert result["ok"] and result["purchase"]["status"] == "purchased"
    assert not state.current_map_page and not state.current_room_id
    stored = group_state.load_state(state.group_id)
    assert stored.get_active_character("a").carried_items == ["筆記本", "煤油", "煤油"]
    assert stored.get_active_character("a").cash_balances == {}
    assert result["purchase"]["arrival_basis"]
    revision = state.state_revision
    again = call(state)
    assert again["duplicate"] and state.state_revision == revision
    assert state.get_active_character("a").carried_items == ["筆記本", "煤油", "煤油"]


@pytest.mark.parametrize("change", [
    {"arrived": False}, {"source": ""}, {"arrival_basis": ""}, {"affordability": ""},
    {"items": [{"name": "煤油", "quantity": True}]}, {"items": []}, {"_owner_id": "b"},
    {"_turn_key": ""}, {"mode": "cash", "currency": "USD", "items": [{"name": "煤油", "quantity": 1, "unit_price": "NaN"}]},
])
def test_invalid_purchase_has_no_effect(state, change):
    before = group_state.load_state(state.group_id).to_dict()
    result = call(state, **change)
    assert not result["ok"]
    assert group_state.load_state(state.group_id).to_dict() == before


@pytest.mark.parametrize("gate", ["combat", "pending", "luck"])
def test_travel_cannot_skip_outstanding_mechanics(state, gate):
    if gate == "combat":
        state.combat.active = True
    elif gate == "pending":
        state.pending_checks["a"] = {"check_id": "old"}
    else:
        state.pending_luck_decisions["a"] = {"decision_id": "old"}
    group_state.save_state(state)
    assert not call(state)["ok"]
    assert state.get_active_character("a").carried_items == ["筆記本"]


def cash_quote(state):
    result = call(state, mode="cash", currency="USD", items=[{"name": "煤油", "quantity": 2, "unit_price": "1.25"}])
    assert result["ok"]
    return result["purchase"]


def run_command(state, user, *parts):
    reply = AsyncMock()
    asyncio.run(handler.handle(state.group_id, user, reply, ["/coc", *parts]))
    return reply.await_args.args[0]


def test_cash_requires_confirmed_funds_and_player_confirmation(state):
    quote = cash_quote(state)
    assert quote["status"] == "quoted" and quote["total_minor"] == 250
    assert state.get_active_character("a").carried_items == ["筆記本"]
    assert "只有 KP" in run_command(state, "a", "funds", "Marco", "USD", "10.00")
    assert "尚未確認" in run_command(state, "a", "purchase", quote["id"])
    assert "已登記" in run_command(state, "kp", "funds", "Marco", "USD", "10.00")
    assert "沒有屬於" in run_command(state, "b", "purchase", quote["id"])
    assert "支付 USD 2.50" in run_command(state, "a", "purchase", quote["id"])
    stored = group_state.load_state(state.group_id)
    assert stored.get_active_character("a").cash_balances == {"USD": 750}
    assert stored.get_active_character("a").carried_items == ["筆記本", "煤油", "煤油"]
    assert "沒有再次" in run_command(state, "a", "purchase", quote["id"])
    assert group_state.load_state(state.group_id).state_revision == stored.state_revision


def test_insufficient_cash_is_atomic_and_recoverable(state):
    run_command(state, "kp", "funds", "Marco", "USD", "1.00")
    quote = cash_quote(state)
    assert "現金不足" in run_command(state, "a", "purchase", quote["id"])
    stored = group_state.load_state(state.group_id)
    assert stored.get_active_character("a").cash_balances == {"USD": 100}
    assert stored.get_active_character("a").carried_items == ["筆記本"]
    assert stored.commerce["transactions"][quote["id"]]["status"] == "quoted"


def test_persistence_error_cannot_save_half_transaction(state):
    run_command(state, "kp", "funds", "Marco", "USD", "10.00")
    quote = cash_quote(state)
    before = group_state.load_state(state.group_id).to_dict()
    with patch.object(keeper, "_save_state_checked", side_effect=RuntimeError("disk failure")), pytest.raises(RuntimeError):
        run_command(state, "a", "purchase", quote["id"])
    assert group_state.load_state(state.group_id).to_dict() == before


@pytest.mark.parametrize("change", ["timeline", "map", "expired"])
def test_stale_quote_cannot_be_used(state, change):
    quote = cash_quote(state)
    if change == "timeline":
        state.timeline_id = "new"
    elif change == "map":
        state.current_room_id["a"] = "elsewhere"
    else:
        purchases.expire_quotes(state, "a")
    group_state.save_state(state)
    with pytest.raises(ValueError):
        purchases.confirm(state, "a", quote["id"])


def run_executor(state, provider):
    fake = AsyncMock(side_effect=provider)
    with patch.object(executor, "LLM_PROVIDER", "openai"), patch.object(executor, "_PROVIDERS", {"openai": SimpleNamespace(run_conversation=fake)}):
        result = asyncio.run(executor.run_executor(AgentMessage({"state": state, "user_id": "a", "text": "前往購買煤油", "display_name": "Marco", "speaker_role": "player"})))
    assert fake.await_count == 1 and fake.call_args.kwargs["enable_wrapup"] is False
    return result


def test_real_executor_one_tool_purchase_handoff_no_fixed_extra_call(state):
    async def provider(*args, **kwargs):
        result = await args[5]("purchase_items", request())
        assert result["ok"]
        return decision(state, "resolved_without_check", [result["evidence_ref"]])
    result = run_executor(state, provider)
    assert result.turn_resolution.disposition == "resolved_without_check"
    assert [e.type for e in result.events] == ["purchase", "inventory_change"]
    block = prompt_config.build_mechanic_facts_block(result)
    assert "arrival_basis" in block and "不能敘述成一直持有" in block
    assert result.events[1].payload["added"] == ["煤油", "煤油"]


def test_committed_purchase_survives_provider_failure_without_replay(state):
    async def provider(*args, **kwargs):
        await args[5]("purchase_items", request())
        raise RuntimeError("continuation failed")
    result = run_executor(state, provider)
    assert not result.success and result.events
    reply = prompt_config.enforce_mechanic_check_consistency("", result)
    assert "已記錄的變更會保留" in reply and "重擲" not in reply
    assert state.get_active_character("a").carried_items == ["筆記本", "煤油", "煤油"]


def test_readonly_query_is_valid_without_claiming_dice_or_state_effects(state):
    async def provider(*args, **kwargs):
        result = await args[5]("get_character_sheet", {"investigator": "Marco"})
        assert result["ok"]
        return decision(state, "no_mechanics", [result["evidence_ref"]])
    result = run_executor(state, provider)
    assert result.turn_resolution.disposition == "no_mechanics"
    result.turn_resolution.disposition = "incomplete"
    reply = prompt_config.enforce_mechanic_check_consistency("", result)
    assert "已記錄的變更" not in reply and "重擲" not in reply


def test_readonly_label_does_not_hide_state_mutation(state):
    before = turn_resolution.gameplay_snapshot(state)
    state.get_active_character("a").hp -= 1
    after = turn_resolution.gameplay_snapshot(state)
    result = turn_resolution.validate_resolution(decision(state, "no_mechanics", ["tool:1"]), state=state, user_id="a",
        before_pending={}, before_luck={}, before_actor={}, before_gameplay=before,
        tool_events=[{"name": "search_scenario", "result": {"ok": True}, "gameplay_before": before, "gameplay_after": after}], has_scenario=False)
    assert result.validation_code == "no_mechanics_has_effects"


def test_legacy_save_has_no_invented_cash():
    char = Character.from_dict({"name": "Old", "owner_id": "o"})
    assert char.cash_balances == {}
    assert GroupState.from_dict(GroupState(group_id="old").to_dict()).commerce == {}


def test_generic_inventory_cannot_bypass_explicit_purchase(state):
    async def provider(*args, **kwargs):
        result = await args[5]("add_carried_item", {"investigator": "Marco", "item": "煤油"})
        assert not result["ok"] and "purchase_items" in result["error"]
        return decision(state, "blocked", ["state"])
    result = run_executor(state, provider)
    assert result.turn_resolution.disposition == "blocked"
    assert state.get_active_character("a").carried_items == ["筆記本"]


def test_new_narrative_action_expires_quote_without_extra_provider_call(state):
    quote = cash_quote(state)
    async def provider(*args, **kwargs):
        return decision(state, "no_mechanics", ["state"])
    run_executor(state, provider)
    assert state.commerce["transactions"][quote["id"]]["status"] == "expired"
    assert "過期" in run_command(state, "a", "purchase", quote["id"])


def test_duplicate_purchase_handoff_never_fabricates_removed_items(state):
    async def provider(*args, **kwargs):
        await args[5]("purchase_items", request())
        duplicate = await args[5]("purchase_items", request())
        assert duplicate["duplicate"]
        return decision(state, "resolved_without_check", ["tool:1", "tool:2"])
    result = run_executor(state, provider)
    assert result.turn_resolution.disposition == "resolved_without_check"
    changes = [e.payload for e in result.events if e.type == "inventory_change"]
    assert len(changes) == 1 and changes[0]["removed"] == []


def test_changed_retry_cannot_duplicate_a_purchase(state):
    assert call(state)["ok"]
    assert not call(state, arrival_basis="改述同一次抵達")["ok"]
    assert state.get_active_character("a").carried_items == ["筆記本", "煤油", "煤油"]
