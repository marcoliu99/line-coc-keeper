"""Keeper player and NPC check handlers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import dice, luck
from app.check_identity import effective_check_id, new_decision_id
from app.models import GroupState
from app.services import opposed_checks

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


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
        opposed_request = opposed_checks.contract(tool_input.get("opposed"))
        if opposed_request and (not isinstance(tool_input.get("action_basis"), str)
                                or not tool_input['action_basis'].strip() or len(tool_input['action_basis']) > 600):
            raise ValueError('對抗檢定須先說明物件狀態、適用規則及觸發轉變。')
        if opposed_request and (tool_input.get('pushed') or tool_input.get('difficulty', 'regular') != 'regular'):
            raise ValueError('對抗檢定以雙方等級比較，不可強推或用固定難度替代。')
        if not target_state.autoroll_checks:
            value = keeper.resolve_skill_value(target_char, tool_input["skill"])
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
            new_check.update(services.pending_check_metadata(target_state, target_char.owner_id, tool_input))
            if opposed_request:
                new_check['opposed'] = opposed_request
            if target_char.owner_id in target_state.pending_luck_decisions:
                return services.StateMutation(
                    {
                        "ok": False,
                        "error": f"{target_char.name} 仍在等待 Luck 決定，請先處理 Luck 選項。",
                    },
                    should_save=False,
                )
            existing = target_state.pending_checks.get(target_char.owner_id)
            if existing:
                if services.is_identical_pending_check(existing, new_check):
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
                            "note": "已經有相同的待處理檢定（防重複）。",
                            "opposed_pending": bool(existing.get('opposed')),
                        },
                        should_save=False,
                    )
                return services.StateMutation(
                    {
                        "ok": False,
                        "error": (
                            f"{target_char.name} 已經有一筆待處理的檢定，請等玩家先處理完（/coc check 或按鈕選擇）"
                            "才能再要求新的檢定，不要重複呼叫。"
                        ),
                    },
                    should_save=False,
                )
            if opposed_request:
                new_check['opposed'] = opposed_checks.roll_opponent(opposed_request)
            target_state.pending_checks[target_char.owner_id] = new_check
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
        cached = services.cached_check_result(target_state, cache_key)
        if cached is not None:
            return services.StateMutation(cached, should_save=False)
        if target_char.owner_id in target_state.pending_checks:
            return services.StateMutation(
                {
                    "ok": False,
                    "error": (
                        f"{target_char.name} 仍有舊版待處理檢定；請先用最新按鈕或 /coc check 選擇完成，"
                        "不要在它完成前開始另一個檢定。"
                    ),
                },
                should_save=False,
            )
        if target_char.owner_id in target_state.pending_luck_decisions:
            return services.StateMutation(
                {
                    "ok": False,
                    "error": f"{target_char.name} 仍在等待 Luck 決定，請先處理 Luck 選項。",
                },
                should_save=False,
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
        metadata = services.pending_check_metadata(target_state, target_char.owner_id, tool_input)
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
        options = services.resolve_defense_options(target_char, raw_options)
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
        new_choice.update(services.pending_check_metadata(target_state, target_char.owner_id, tool_input))
        if attacker_tier:
            new_choice["attacker_tier"] = attacker_tier
        # 先檢查是否已有待處理檢定
        existing = target_state.pending_checks.get(target_char.owner_id)
        if existing:
            # 如果完全相同，直接返回結果而不重新保存（防重複）
            if services.is_identical_pending_check(existing, new_choice):
                return services.StateMutation({
                    "ok": True, "pending": True, "investigator": target_char.name, "options": options,
                    "note": "已經有相同的防守選項等待（防重複）。",
                }, should_save=False)
            # 否則拒絕（已有不同的待處理檢定）
            return services.StateMutation({
                "ok": False,
                "error": f"{target_char.name} 已經有一筆待處理的檢定，請等玩家先處理完（/coc check 或按鈕選擇）才能再要求新的檢定，不要重複呼叫。",
            }, should_save=False)
        # 沒有待處理檢定，註冊新的
        target_state.pending_checks[target_char.owner_id] = new_choice
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
        options = services.resolve_defense_options(target_char, raw_options)
        new_choice: dict[str, Any] = {
            "type": "choice",
            "options": options,
            "attacker_skill_value": attacker_skill_value,
            "attacker_bonus_dice": attacker_bonus,
            "attacker_penalty_dice": attacker_penalty,
            "is_ranged": is_ranged,
            # Code review: the dedup/reuse comparison below must match
            # against what the CALLER asked for, not what ended up
            # persisted after server-side filtering (critical-tier
            # Fight Back removal, ranged Fight Back removal) — those
            # filters can shrink the saved "options" (e.g. to just
            # ["閃避"]) relative to the raw request (["閃避","反擊"]),
            # so comparing against saved "options" made a legitimate
            # identical retry fail to match and fall through to the
            # generic "already pending" rejection instead of reusing
            # the cached roll.
            "raw_option_labels": sorted(str(o.get("label", "")) for o in raw_options),
        }
        new_choice.update(services.pending_check_metadata(target_state, target_char.owner_id, tool_input))
        existing = target_state.pending_checks.get(target_char.owner_id)
        # 防重複：如果已經有完全相同的防守選項且有真實掷骰結果，重用現有結果而不重新掷。
        # 遠程情境的 attacker_roll 永遠是 None（見下方 is_ranged 分支——攻擊方要等
        # 防守方擲完「撲向掩體」才會擲，見 §2.4），所以這個重用條件天生不會對遠程
        # pending 觸發，遠程重複呼叫會自然落到下面的「已有待處理檢定」拒絕分支，
        # 這正是我們要的行為（不會被誤判成「已擲過，重用結果」）。
        if (
            existing
            and existing.get("type") == "choice"
            and existing.get("attacker_roll") is not None
            and existing.get("attacker_skill_value") == attacker_skill_value
            and existing.get("attacker_bonus_dice", 0) == attacker_bonus
            and existing.get("attacker_penalty_dice", 0) == attacker_penalty
            # Code review: is_ranged 沒被比對時，一個先以 is_ranged=False（近戰）
            # 註冊、已經擲出 attacker_roll 的 pending，會在呼叫端只把 is_ranged
            # 改成 True 重試時被誤判成「完全相同、可以重用」——因為前面幾個欄位
            # 剛好都符合。這樣會悄悄延用近戰對抗擲骰的舊結果，讓修正後的遠程呼叫
            # 錯誤地留在近戰 opposed-roll 路徑上，也連帶繞過遠程分支自己的反擊
            # 選項過濾（見下方 is_ranged 分支）。
            and existing.get("is_ranged", False) == is_ranged
        ):
            # 比較防守選項是否相同——用呼叫時的「原始 raw_option_labels」比對，
            # 不是比對 existing 已保存的 options，因為 critical/遠程過濾可能讓
            # 保存的 options 比原始請求少（見上方 new_choice 建構處的說明）；
            # existing 若是舊版沒有 raw_option_labels 欄位的資料，get 回傳 None
            # 不等於任何排序後的 list，安全地直接判定不相符、退回下面的拒絕分支。
            try:
                existing_labels = existing.get("raw_option_labels")
                new_labels = new_choice["raw_option_labels"]
                if existing_labels == new_labels:
                    # 防守選項相同且有真實掷骰結果，重用現有（已套用過濾的）結果
                    persisted_timeline_id = target_state.timeline_id or f"legacy-{target_state.group_id}"
                    return services.StateMutation({
                        "ok": True, "pending": True, "investigator": target_char.name,
                        "options": existing.get("options", options),
                        "attacker_roll": existing.get("attacker_roll"),
                        "attacker_tier": existing.get("attacker_tier"),
                        # The response must carry the same stable
                        # identity as the persisted pending entry.
                        # Reusing a roll must not manufacture a new
                        # check id that the button cannot consume.
                        "check_id": effective_check_id(
                            target_char.owner_id, existing, persisted_timeline_id
                        ),
                        "timeline_id": persisted_timeline_id,
                        "note": "防守選項相同，重用之前的掷骰結果（防重複）。",
                    }, should_save=False)
            except (TypeError, ValueError):
                pass  # 無法排序時，繼續執行新的掷骰
        if existing:
            return services.StateMutation(
                {
                    "ok": False,
                    "error": f"{target_char.name} 已經有一筆待處理的檢定，請等玩家先處理完（/coc check 或按鈕選擇）才能再要求新的檢定，不要重複呼叫。",
                },
                should_save=False,
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
            target_state.pending_checks[target_char.owner_id] = new_choice
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
        target_state.pending_checks[target_char.owner_id] = new_choice
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
            blocked = services.reject_if_check_already_pending(target_state, target_char)
            if blocked is not None:
                return services.StateMutation(blocked, should_save=False)
            target_state.pending_checks[target_char.owner_id] = {
                "type": "sanity",
                "loss_success": loss_success,
                "loss_failure": loss_failure,
                **services.pending_check_metadata(target_state, target_char.owner_id, tool_input),
            }
            return services.StateMutation(
                {
                    "ok": True,
                    "pending": True,
                    "investigator": target_char.name,
                    "current_san": target_char.san,
                    "note": "等待玩家自己用 /coc check 或按鈕擲 SAN；在結果回來前不要自行扣 SAN 或判定瘋狂。",
                },
                should_save=True,
            )
        cached = services.cached_check_result(target_state, cache_key)
        if cached is not None:
            return services.StateMutation(cached, should_save=False)
        if target_char.owner_id in target_state.pending_checks:
            return services.StateMutation(
                {
                    "ok": False,
                    "error": f"{target_char.name} 仍有待處理的防守選擇，請先完成選擇再做 SAN 檢定。",
                },
                should_save=False,
            )
        if target_char.owner_id in target_state.pending_luck_decisions:
            return services.StateMutation(
                {"ok": False, "error": f"{target_char.name} 仍在等待 Luck 決定，請先處理 Luck 選項。"},
                should_save=False,
            )
        metadata = services.pending_check_metadata(target_state, target_char.owner_id, tool_input)
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
