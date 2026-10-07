"""Keeper player and NPC check handlers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import check_lifecycle, resolved_check_consequences
from app.checks import events as check_events
from app.checks import service as check_service
from app.checks.dice_port import DEFAULT_DICE
from app.checks.skills import resolve_skill_value
from app.keeper_tools import resource_bridge, support
from app.models import Character, GroupState
from app.services import opposed_checks

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def _check_registration_error(char: Character, blocker: str | None) -> dict[str, Any]:
    if blocker == "pending_luck_decision":
        return {"ok": False, "error": f"{char.name} 仍在等待 Luck 決定，請先處理 Luck 選項。"}
    return {
        "ok": False,
        "error": f"{char.name} 已經有一筆待處理的檢定，請等玩家先處理完（/coc check 或按鈕選擇）"
                 "才能再要求新的檢定，不要重複呼叫。",
    }


def skill_check(call: ToolCall) -> dict[str, Any]:

    services = support.check_tool_services
    state = call.state
    tool_input = call.input
    name = call.name
    speaker_role = call.speaker_role
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    owner_id = char.owner_id
    cache_key = services.deterministic_check_cache_key(name, tool_input, owner_id, speaker_role)
    # Set by _roll_skill_check only when autoroll fully resolves the
    # check right here with no further player interaction (no Luck
    # buy-up offered) — see the resolved_check_events wiring after
    # services.mutate_and_save_state below for why this can't be persisted
    # from inside the mutator itself.
    resolved_event_seed: dict[str, Any] | None = None

    def _roll_skill_check(target_state: GroupState) -> Any:
        nonlocal resolved_event_seed
        target_char = resource_bridge.effective(target_state, support.require_character(target_state, tool_input.get("investigator", "")))
        consequences = resolved_check_consequences.normalize_authorizations(
            target_state, tool_input.get("consequences")
        )
        opposed_request = opposed_checks.contract(tool_input.get("opposed"))
        if opposed_request and (not isinstance(tool_input.get("action_basis"), str)
                                or not tool_input['action_basis'].strip() or len(tool_input['action_basis']) > 600):
            raise ValueError('對抗檢定須先說明物件狀態、適用規則及觸發轉變。')
        if opposed_request and (tool_input.get('pushed') or tool_input.get('difficulty', 'regular') != 'regular'):
            raise ValueError('對抗檢定以雙方等級比較，不可強推或用固定難度替代。')
        if not target_state.autoroll_checks:
            value = resolve_skill_value(target_char, tool_input["skill"], register_unknown=False)
            bonus = int(tool_input.get("bonus_dice") or 0)
            penalty = int(tool_input.get("penalty_dice") or 0)
            difficulty = tool_input.get("difficulty") or "regular"
            if difficulty not in ("regular", "hard", "extreme"):
                difficulty = "regular"
            new_check: dict[str, Any] = {
                "type": "skill",
                "skill": tool_input["skill"],
                "skill_value": value,
                "bonus_dice": bonus,
                "penalty_dice": penalty,
                "difficulty": difficulty,
                "pushed": bool(tool_input.get("pushed", False)),
            }
            if opposed_request:
                new_check['opposed'] = opposed_request
            new_check['action_basis'] = str(tool_input.get('action_basis', ''))[:600]
            if consequences:
                new_check['consequences'] = consequences
            registration = check_lifecycle.register(
                target_state, target_char.owner_id, new_check,
                duplicate="identical", source=tool_input,
            )
            if registration.status == "blocked":
                return services.StateMutation(
                    _check_registration_error(target_char, registration.blocker), should_save=False
                )
            if registration.status == "identical":
                existing = registration.pending or {}
                return services.StateMutation(
                    {
                        "ok": True, "pending": True, "investigator": target_char.name,
                        "skill": tool_input["skill"], "skill_value": value,
                        "bonus_dice": bonus, "penalty_dice": penalty,
                        "difficulty": difficulty,
                        "note": "已經有相同的待處理檢定（防重複）。",
                        "opposed_pending": bool(existing.get('opposed')),
                    }, should_save=False,
                )
            registered = registration.pending
            assert registered is not None
            resolve_skill_value(target_char, tool_input["skill"])
            if opposed_request:
                registered['opposed'] = opposed_checks.roll_opponent(opposed_request)
            new_check = registered
            return services.StateMutation(
                {
                    "ok": True,
                    "pending": True,
                    "investigator": target_char.name,
                    "skill": tool_input["skill"],
                    "skill_value": value,
                    "bonus_dice": bonus,
                    "penalty_dice": penalty,
                    "difficulty": difficulty,
                    "note": "等待玩家自己用 /coc check 或按鈕擲骰；在結果回來前不要自行判定成敗。",
                    "opposed_pending": bool(new_check.get('opposed')),
                },
                should_save=True,
            )
        prior_result = services.cached_check_result(target_state, cache_key)
        cached = check_lifecycle.reusable_cached_result(
            target_state, target_char.owner_id, prior_result
        )
        if cached is not None:
            return services.StateMutation(cached, should_save=False)
        if prior_result is not None:
            return services.StateMutation({
                "ok": False,
                "error": "這次檢定的狀態已改變；不能重擲已結算的骰，請先確認目前狀態。",
            }, should_save=False)
        admission = check_lifecycle.admit(target_state, target_char.owner_id)
        if admission.status == "blocked":
            return services.StateMutation(
                _check_registration_error(target_char, admission.blocker), should_save=False
            )
        value = resolve_skill_value(target_char, tool_input["skill"])
        bonus = int(tool_input.get("bonus_dice") or 0)
        penalty = int(tool_input.get("penalty_dice") or 0)
        difficulty = tool_input.get("difficulty") or "regular"
        if difficulty not in ("regular", "hard", "extreme"):
            difficulty = "regular"
        pushed = bool(tool_input.get("pushed", False))
        autoroll = check_service.autoroll_skill(
            target_state, target_char, tool_input=tool_input, value=value, bonus=bonus, penalty=penalty,
            difficulty=difficulty, pushed=pushed, consequences=consequences,
            opposed_request=opposed_request,
        )
        result = autoroll.result
        resolved_event_seed = autoroll.event_seed
        result["provisional"] = resource_bridge.participating(target_state, target_char)
        services.remember_check_result(target_state, cache_key, result)
        return services.StateMutation(result, should_save=True)
    result = services.mutate_and_save_state(state, _roll_skill_check)
    if resolved_event_seed is not None and result.get("resolved") and not result.get("pending_luck"):
        check_events.persist_resolved_event(state.group_id, resolved_event_seed, with_origin=True, snapshot=state)
    return result


def offer_check_choice(call: ToolCall) -> dict[str, Any]:

    services = support.check_tool_services
    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    raw_options = tool_input.get("options") or []
    if len(raw_options) < 2:
        return {"ok": False, "error": "options 至少要給兩個選項，只有一個的話請直接用 skill_check"}
    if tool_input.get("attacker_tier"):
        return {"ok": False, "error": "NPC 攻擊的防守選擇由戰鬥引擎建立：先 initialize_combat，輪到敵人時用 plan_enemy_turn → run_enemy_combat_plan"}
    def _register_pending_choice(target_state: GroupState) -> Any:
        target_char = resource_bridge.effective(target_state, support.require_character(target_state, tool_input.get("investigator", "")))
        options = services.resolve_defense_options(target_char, raw_options, register_unknown=False)
        new_choice: dict[str, Any] = {"type": "choice", "options": options}
        decision = check_lifecycle.register(
            target_state, target_char.owner_id, new_choice,
            duplicate="identical", source=tool_input,
        )
        if decision.status == "identical":
            return services.StateMutation({
                "ok": True, "pending": True, "investigator": target_char.name, "options": options,
                "note": "已經有相同的防守選項等待（防重複）。",
            }, should_save=False)
        if decision.status == "blocked":
            return services.StateMutation(
                _check_registration_error(target_char, decision.blocker), should_save=False
            )
        services.resolve_defense_options(target_char, raw_options)
        return services.StateMutation({
            "ok": True, "pending": True, "investigator": target_char.name, "options": options,
            "note": "等待玩家選一個選項；選定後預設由玩家用 /coc check 或按鈕擲骰，只有 autoroll 開啟時才由系統代擲。",
        }, should_save=True)
    return services.mutate_and_save_state(state, _register_pending_choice)


def npc_skill_check(call: ToolCall) -> dict[str, Any]:
    tool_input = call.input
    skill_value = max(0, min(100, int(tool_input["skill_value"])))
    bonus = int(tool_input.get("bonus_dice") or 0)
    penalty = int(tool_input.get("penalty_dice") or 0)
    npc_roll = DEFAULT_DICE.skill_check(skill_value, bonus_dice=bonus, penalty_dice=penalty)
    return {"ok": True, "roll": npc_roll.roll, "tier": npc_roll.tier, "skill_value": skill_value}


def clear_pending_check(call: ToolCall) -> dict[str, Any]:

    services = support.check_tool_services
    state = call.state
    tool_input = call.input
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    def _clear_pending_check(target_state: GroupState) -> Any:
        existing = target_state.pending_checks.get(char.owner_id) or {}
        if existing.get('combat_context') or existing.get('postcombat_context') or existing.get('medical_context'):
            return services.StateMutation({"ok": False, "error": "Owned combat wait requires explicit controller correction"}, should_save=False)
        target_char = resource_bridge.effective(target_state, support.require_character(target_state, tool_input.get("investigator", "")))
        cleared = target_state.pending_checks.pop(target_char.owner_id, None)
        if cleared is None:
            return services.StateMutation(
                {"ok": True, "cleared": False, "investigator": target_char.name,
                 "note": f"{target_char.name} 本來就沒有待處理的檢定，沒有動作。"},
                should_save=False,
            )
        return services.StateMutation({
            "ok": True, "cleared": True, "investigator": target_char.name,
            "cleared_check_type": cleared.get("type", ""),
        }, should_save=True)
    return services.mutate_and_save_state(state, _clear_pending_check)


def sanity_check(call: ToolCall) -> dict[str, Any]:

    services = support.check_tool_services
    state = call.state
    tool_input = call.input
    name = call.name
    speaker_role = call.speaker_role
    char = support.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    loss_success = tool_input.get("loss_success", "0")
    loss_failure = tool_input.get("loss_failure", "1d4")
    owner_id = char.owner_id
    cache_key = services.deterministic_check_cache_key(name, tool_input, owner_id, speaker_role)
    sanity_event_seed: dict[str, Any] | None = None

    def _roll_sanity_check(target_state: GroupState) -> Any:
        nonlocal sanity_event_seed
        target_char = resource_bridge.effective(target_state, support.require_character(target_state, tool_input.get("investigator", "")))
        if not target_state.autoroll_checks:
            decision = check_lifecycle.register(
                target_state, target_char.owner_id,
                {"type": "sanity", "loss_success": loss_success, "loss_failure": loss_failure},
                source=tool_input,
            )
            if decision.status == "blocked":
                return services.StateMutation(
                    _check_registration_error(target_char, decision.blocker), should_save=False
                )
            return services.StateMutation(
                {
                    "ok": True, "pending": True,
                    "investigator": target_char.name,
                    "current_san": target_char.san,
                    "note": "等待玩家自己用 /coc check 或按鈕擲 SAN；在結果回來前不要自行扣 SAN 或判定瘋狂。",
                },
                should_save=decision.should_save,
            )
        prior_result = services.cached_check_result(target_state, cache_key)
        cached = check_lifecycle.reusable_cached_result(
            target_state, target_char.owner_id, prior_result
        )
        if cached is not None:
            return services.StateMutation(cached, should_save=False)
        if prior_result is not None:
            return services.StateMutation({
                "ok": False,
                "error": "這次檢定的狀態已改變；不能重擲已結算的骰，請先確認目前狀態。",
            }, should_save=False)
        admission = check_lifecycle.admit(target_state, target_char.owner_id)
        if admission.status == "blocked":
            return services.StateMutation(
                _check_registration_error(target_char, admission.blocker), should_save=False
            )
        autoroll = check_service.autoroll_sanity(
            target_state, target_char, tool_input=tool_input,
            loss_success=loss_success, loss_failure=loss_failure,
        )
        result = autoroll.result
        sanity_event_seed = autoroll.event_seed
        result["provisional"] = resource_bridge.participating(target_state, target_char)
        services.remember_check_result(target_state, cache_key, result)
        return services.StateMutation(result, should_save=True)
    result = services.mutate_and_save_state(state, _roll_sanity_check)
    if sanity_event_seed is not None and result.get("resolved"):
        check_events.persist_resolved_event(state.group_id, sanity_event_seed, with_origin=True, snapshot=state)
    return result
