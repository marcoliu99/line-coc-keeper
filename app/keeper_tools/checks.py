"""Keeper player and NPC check handlers."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app import check_lifecycle, dice, luck, resolved_check_consequences
from app.check_identity import new_decision_id
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
    from app import keeper

    services = keeper.check_tool_services
    state = call.state
    tool_input = call.input
    name = call.name
    speaker_role = call.speaker_role
    char = keeper.find_character(state, tool_input.get("investigator", ""))
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
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
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
            value = keeper.resolve_skill_value(target_char, tool_input["skill"], register_unknown=False)
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
            keeper.resolve_skill_value(target_char, tool_input["skill"])
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
        value = keeper.resolve_skill_value(target_char, tool_input["skill"])
        bonus = int(tool_input.get("bonus_dice") or 0)
        penalty = int(tool_input.get("penalty_dice") or 0)
        difficulty = tool_input.get("difficulty") or "regular"
        if difficulty not in ("regular", "hard", "extreme"):
            difficulty = "regular"
        pushed = bool(tool_input.get("pushed", False))
        state_before = services.character_attribute_snapshot(target_char)
        opposed_receipt = opposed_checks.roll_opponent(opposed_request)
        roll = dice.skill_check(value, bonus_dice=bonus, penalty_dice=penalty, required_tier=difficulty)
        opposed_outcome = opposed_checks.resolve(opposed_receipt, roll.tier)
        metadata = check_lifecycle.metadata(target_state, target_char.owner_id, tool_input)
        result: dict[str, Any] = {
            "ok": True,
            "resolved": True,
            "investigator": target_char.name,
            "skill": tool_input["skill"],
            "skill_value": value,
            "bonus_dice": bonus,
            "penalty_dice": penalty,
            "difficulty": difficulty,
            "roll": roll.roll,
            "tier": roll.tier,
            "required_tier": roll.required_tier,
            "success": (opposed_outcome['winner'] == 'player') if opposed_outcome else roll.success,
            "player_check_success": roll.success,
            "check_id": metadata["check_id"],
            "timeline_id": metadata["timeline_id"],
            "action_context": metadata["action_context"],
            "player_declaration": metadata['player_declaration'],
            "action_basis": metadata['action_basis'],
            "opposed_outcome": opposed_checks.public_outcome(opposed_outcome),
            "consequences": consequences,
            "note": (
                "Keeper 已由 deterministic dice engine 擲完這次檢定；請直接依照結果敘事，不要再要求玩家擲攻擊骰或技能骰。"
                if target_state.autoroll_checks
                else "已建立待處理檢定；請讓玩家用 /coc check 或按鈕擲骰，收到結果後再敘事，不要自行判定。"
            ),
        }

        # Always offered whenever there's at least one tier-improving
        # option the player can afford (buyable_options already
        # filters to cost <= luck available) -- no cost cap on top
        # of that; see docs/specs/enhancement-luck-buyup-always-
        # offered.md for why the previous "<=7" near-miss-only gate
        # was removed.
        luck_options = [] if pushed else luck.buyable_options(
            value, roll.roll, roll.tier, target_char.luck, difficulty
        )
        if luck_options:
            decision = {
                "decision_id": new_decision_id(),
                "check_id": metadata["check_id"],
                "timeline_id": metadata["timeline_id"],
                "origin_revision": target_state.state_revision + 1,
                "origin_turn_id": metadata["origin_turn_id"],
                "origin_request_id": metadata["origin_request_id"],
                "created_at": metadata["created_at"],
                "action_context": metadata["action_context"],
                "skill_name": tool_input["skill"],
                "display_label": None,
                "value": value,
                "roll": roll.roll,
                "bonus_dice": bonus,
                "penalty_dice": penalty,
                "original_tier": roll.tier,
                "attacker_tier": None,
                "difficulty": difficulty,
                "options": [{"tier": item.tier, "cost": item.cost} for item in luck_options],
                "major_wound_trigger": False,
                "opposed": opposed_receipt,
                "player_declaration": metadata['player_declaration'],
                "action_basis": metadata['action_basis'],
                "consequences": consequences,
            }
            target_state.pending_luck_decisions[target_char.owner_id] = decision
            result.update({
                "pending_luck": True,
                "opposed_outcome": None,
                "success": None if opposed_receipt else result['success'],
                "decision_id": decision["decision_id"],
                "luck_options": decision["options"],
                "note": (
                    "Keeper 已擲完檢定，有花 Luck 買到更好結果的選項可用。玩家現在只可選擇是否"
                    "花 Luck 修正；玩家不需要、也不可以自行重骰。先不要把最終成敗敘事成不可逆的結果。"
                ),
            })
        elif target_state.autoroll_checks:
            resolved_event_seed = {
                "event_id": metadata["check_id"],
                "check_id": metadata["check_id"],
                "timeline_id": metadata["timeline_id"],
                "owner_id": target_char.owner_id,
                "character_id": target_char.character_id,
                "investigator": target_char.name,
                "skill": tool_input["skill"],
                "skill_value": value,
                "roll": roll.roll,
                "difficulty": difficulty,
                "outcome": f"{roll.tier} {'成功' if roll.success else '失敗'}" +
                (f"；對抗勝方={opposed_outcome['winner']}" if opposed_outcome else ''),
                "opposed_outcome": opposed_outcome,
                "player_declaration": metadata['player_declaration'],
                "action_basis": metadata['action_basis'],
                "success": result["success"],
                "consequences": consequences,
                "state_before": state_before,
            }
        services.remember_check_result(target_state, cache_key, result)
        return services.StateMutation(result, should_save=True)
    result = services.mutate_and_save_state(state, _roll_skill_check)
    if resolved_event_seed is not None and result.get("resolved") and not result.get("pending_luck"):
        services.persist_resolved_check_event(state, resolved_event_seed)
    return result


def offer_check_choice(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    services = keeper.check_tool_services
    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    raw_options = tool_input.get("options") or []
    if len(raw_options) < 2:
        return {"ok": False, "error": "options 至少要給兩個選項，只有一個的話請直接用 skill_check"}
    attacker_tier = tool_input.get("attacker_tier")
    def _register_pending_choice(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        options = services.resolve_defense_options(target_char, raw_options, register_unknown=False)
        # COC7e：攻擊方大成功時沒有任何等級贏得過它，「反擊」選項不成立——這是
        # offer_npc_attack_defense_choice 已有的同一條規則，code review 發現這個
        # 舊版兩步流程（npc_skill_check 先擲、這裡再註冊選項）從未套用，讓仍在用
        # 這個入口的 Keeper 能給玩家一個數學上穩輸的反擊選項，補上同樣的過濾。
        if attacker_tier == "critical":
            filtered_options = [o for o in options if not dice.is_counter_option(o)]
            if not filtered_options:
                return services.StateMutation(
                    {
                        "ok": False,
                        "error": "攻擊方這次擲出大成功，沒有任何成功等級贏得過它，「反擊」選項"
                                 "已不成立；但目前 options 只有反擊，沒有閃避可選，請至少提供一個"
                                 "「閃避」選項後再重新呼叫這個工具。",
                    },
                    should_save=False,
                )
            options = filtered_options
        new_choice: dict[str, Any] = {"type": "choice", "options": options}
        if attacker_tier:
            new_choice["attacker_tier"] = attacker_tier
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
    npc_roll = dice.skill_check(skill_value, bonus_dice=bonus, penalty_dice=penalty)
    return {"ok": True, "roll": npc_roll.roll, "tier": npc_roll.tier, "skill_value": skill_value}


def offer_npc_attack_defense_choice(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    services = keeper.check_tool_services
    state = call.state
    tool_input = call.input
    # Merges what used to be two sequential tool calls (npc_skill_check
    # then offer_check_choice with attacker_tier filled in from its
    # result) into one — see docs/specs/enhancement/npc_attack_latency_design_spec.md.
    # offer_check_choice's attacker_tier field structurally depended on
    # npc_skill_check's return value, forcing the model to make two
    # separate round-trips (see the result, then decide the next call)
    # for the single most common combat exchange (NPC attacks, player
    # picks dodge/counter). Built entirely from the same primitives
    # both original handlers already used below — not new logic, just
    # one fewer LLM round-trip to reach it.
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    raw_options = tool_input.get("options") or []
    if len(raw_options) < 1:
        return {"ok": False, "error": "options 至少要給一個選項"}
    attacker_skill_value = max(0, min(100, int(tool_input["attacker_skill_value"])))
    attacker_bonus = int(tool_input.get("attacker_bonus_dice") or 0)
    attacker_penalty = int(tool_input.get("attacker_penalty_dice") or 0)
    is_ranged = bool(tool_input.get("is_ranged", False))

    def _roll_and_register_defense_choice(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
        # Resolve the existing-pending/reuse decision inside the same
        # freshly-loaded mutator that performs the roll and write. A
        # rejected call therefore never rolls, and there is no gap
        # between checking the pending entry and saving its result.
        options = services.resolve_defense_options(target_char, raw_options, register_unknown=False)
        new_choice: dict[str, Any] = {
            "type": "choice",
            "options": options,
            "attacker_skill_value": attacker_skill_value,
            "attacker_bonus_dice": attacker_bonus,
            "attacker_penalty_dice": attacker_penalty,
            "is_ranged": is_ranged,
            # Check lifecycle compares the full raw request, because
            # server-side filtering may shrink persisted options.
            "raw_option_labels": sorted(str(o.get("label", "")) for o in raw_options),
            "raw_option_request": sorted(
                json.dumps(option, ensure_ascii=False, sort_keys=True, default=str)
                for option in raw_options
            ),
        }
        decision = check_lifecycle.admit(
            target_state, target_char.owner_id, new_choice, duplicate="npc_melee"
        )
        if decision.status == "identical":
            existing = decision.pending or {}
            return services.StateMutation({
                "ok": True, "pending": True, "investigator": target_char.name,
                "options": existing.get("options", options),
                "attacker_roll": existing.get("attacker_roll"),
                "attacker_tier": existing.get("attacker_tier"),
                "check_id": decision.check_id,
                "timeline_id": decision.timeline_id,
                "note": "防守選項相同，重用之前的掷骰結果（防重複）。",
            }, should_save=False)
        if decision.status == "blocked":
            return services.StateMutation(
                _check_registration_error(target_char, decision.blocker), should_save=False
            )

        if is_ranged:
            # COC7e：遠程攻擊不允許「反擊」，只能撲向掩體——跟近戰大成功時濾掉
            # 反擊選項同一個道理，不能只靠 prompt 指示 LLM 別給反擊選項，玩家
            # 還是能用 /coc check 反擊 之類的文字輸入繞過純 UI 層隱藏，所以這裡
            # 也要伺服器端強制過濾（呼應下方近戰 critical 分支的同一個防禦性
            # 設計）。code review 發現：這裡原本完全沒有過濾，若 LLM 違反 prompt
            # 指示仍帶了反擊選項，玩家選中後會被 is_ranged 分支當「撲向掩體」
            # 處理、敘事成撲向掩體結果，跟玩家實際選的「反擊」不符。
            filtered_options = [o for o in options if not dice.is_counter_option(o)]
            if not filtered_options:
                return services.StateMutation(
                    {
                        "ok": False,
                        "error": "遠程攻擊 COC7e 規則不允許「反擊」，但目前 options 只有反擊、"
                                 "沒有「閃避」可選，請至少提供一個「閃避」選項後再重新呼叫這個工具。",
                    },
                    should_save=False,
                )
            options = filtered_options
            new_choice["options"] = options
            # COC7e：遠程攻擊不是對抗檢定，攻擊方的命中判定完全獨立於防守方，
            # 而且要等防守方決定「撲向掩體」有沒有成功，才知道攻擊方這次要不要
            # 多帶一個懲罰骰——所以這裡不能像近戰一樣預先擲攻擊方，必須延後到
            # 玩家觸發防守擲骰的當下才擲（見 app/legacy_commands.py 的
            # _build_check_narration ranged_attacker 分支）。這裡只登記選項跟
            # 攻擊方的技能值/骰數修正，不寫 attacker_tier/attacker_roll。
            check_lifecycle.register(target_state, target_char.owner_id, new_choice, source=tool_input)
            services.resolve_defense_options(target_char, raw_options)
            return services.StateMutation({
                "ok": True, "pending": True, "investigator": target_char.name, "options": options,
                "note": "遠程攻擊：這不是對抗檢定，不會預先擲攻擊方。等待玩家選擇「撲向掩體」並"
                        "觸發擲骰後，系統才會依撲向掩體是否成功決定攻擊方要不要多帶一個懲罰骰，"
                        "再擲攻擊方的命中判定；不要自行判定命中與否，也不要自己先講攻擊方擲出什麼。",
            }, should_save=True)

        npc_roll = dice.skill_check(
            attacker_skill_value, bonus_dice=attacker_bonus, penalty_dice=attacker_penalty
        )
        # COC7e：攻擊方擲出大成功時，沒有任何等級能贏過它，「反擊」選項在規則上
        # 已經不可能成立（閃避仍然可能贏——雙方都大成功時平手，閃避方獲勝，見
        # dice.resolve_opposed 的 is_counter 分支），所以這裡強制濾掉反擊選項，
        # 不能只在 Discord 按鈕顯示層隱藏，否則玩家還是能用 /coc check 反擊 之類
        # 的文字輸入繞過去。
        if npc_roll.tier == "critical":
            filtered_options = [o for o in options if not dice.is_counter_option(o)]
            if not filtered_options:
                return services.StateMutation(
                    {
                        "ok": False,
                        "error": "攻擊方這次擲出大成功，沒有任何成功等級贏得過它，「反擊」選項"
                                 "已不成立；但目前 options 只有反擊，沒有閃避可選，請至少提供一個"
                                 "「閃避」選項後再重新呼叫這個工具。",
                    },
                    should_save=False,
                )
            options = filtered_options
            new_choice["options"] = options
        new_choice["attacker_tier"] = npc_roll.tier
        new_choice["attacker_roll"] = npc_roll.roll  # 保存掷骰結果供後續防重複檢查
        check_lifecycle.register(target_state, target_char.owner_id, new_choice, source=tool_input)
        services.resolve_defense_options(target_char, raw_options)
        return services.StateMutation({
            "ok": True, "pending": True, "investigator": target_char.name, "options": options,
            "attacker_roll": npc_roll.roll, "attacker_tier": npc_roll.tier,
            "note": "攻擊方檢定已經由系統擲好（tier 見上面）；等待玩家選一個防守選項，"
                    "選定後預設由玩家用 /coc check 或按鈕擲防守骰，autoroll 開啟時才由系統代擲，不要自行判定命中與否。",
        }, should_save=True)
    return services.mutate_and_save_state(state, _roll_and_register_defense_choice)


def clear_pending_check(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    services = keeper.check_tool_services
    state = call.state
    tool_input = call.input
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    def _clear_pending_check(target_state: GroupState) -> Any:
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
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
    from app import keeper

    services = keeper.check_tool_services
    state = call.state
    tool_input = call.input
    name = call.name
    speaker_role = call.speaker_role
    char = keeper.find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    loss_success = tool_input.get("loss_success", "0")
    loss_failure = tool_input.get("loss_failure", "1d4")
    owner_id = char.owner_id
    cache_key = services.deterministic_check_cache_key(name, tool_input, owner_id, speaker_role)
    sanity_event_seed: dict[str, Any] | None = None

    def _roll_sanity_check(target_state: GroupState) -> Any:
        nonlocal sanity_event_seed
        target_char = keeper.require_character(target_state, tool_input.get("investigator", ""))
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
        metadata = check_lifecycle.metadata(target_state, target_char.owner_id, tool_input)
        state_before = services.character_attribute_snapshot(target_char)
        sanity_result = dice.sanity_check(target_char.san, loss_success, loss_failure)
        target_char.san = sanity_result.san_after
        result: dict[str, Any] = {
            "ok": True,
            "resolved": True,
            "investigator": target_char.name,
            "current_san": sanity_result.san_before,
            "san_after": sanity_result.san_after,
            "loss": sanity_result.loss,
            "loss_expression": sanity_result.loss_expression,
            "roll": sanity_result.check.roll,
            "tier": sanity_result.check.tier,
            "success": sanity_result.check.success,
            "check_id": metadata["check_id"],
            "timeline_id": metadata["timeline_id"],
            "action_context": metadata["action_context"],
            "note": (
                "Keeper 已由 deterministic dice engine 擲完 SAN 檢定並更新 SAN；不要要求玩家再輸入 /coc check。"
                if target_state.autoroll_checks
                else "已建立待處理 SAN 檢定；請讓玩家用 /coc check 或按鈕擲骰，結果回來前不要扣 SAN。"
            ),
        }
        if sanity_result.risk_of_madness:
            int_value = keeper.resolve_skill_value(target_char, "INT")
            int_result = dice.skill_check(int_value)
            result["madness_int_check"] = {
                "skill_value": int_value,
                "roll": int_result.roll,
                "tier": int_result.tier,
                "success": int_result.success,
            }
            if int_result.success:
                result["madness"] = dice.roll_madness(realtime=True)
                result["note"] = (
                    "Keeper 已完成 SAN 與後續 INT 檢定；損失達 5 點並觸發短暫瘋狂，"
                    "請照 madness 結果敘事，不要再要求玩家擲 INT。"
                )
            else:
                result["note"] = (
                    "Keeper 已完成 SAN 與後續 INT 檢定；INT 未觸發短暫瘋狂，請照結果敘事。"
                )
        sanity_event_seed = {
            "event_id": metadata["check_id"],
            "check_id": metadata["check_id"],
            "timeline_id": metadata["timeline_id"],
            "owner_id": target_char.owner_id,
            "character_id": target_char.character_id,
            "investigator": target_char.name,
            "skill": "SAN",
            "skill_value": sanity_result.san_before,
            "roll": sanity_result.check.roll,
            "difficulty": "regular",
            "outcome": f"{sanity_result.check.tier} {'成功' if sanity_result.check.success else '失敗'}",
            "state_before": state_before,
        }
        services.remember_check_result(target_state, cache_key, result)
        return services.StateMutation(result, should_save=True)
    result = services.mutate_and_save_state(state, _roll_sanity_check)
    if sanity_event_seed is not None and result.get("resolved"):
        services.persist_resolved_check_event(state, sanity_event_seed)
    return result
