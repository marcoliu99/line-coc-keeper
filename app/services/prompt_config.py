from __future__ import annotations

import re

from app.domain.models import MechanicResult

# 【提示詞集中管理】
# 這個檔案集中管理 Agentic Keeper 流水線裡「真的會呼叫 LLM」的階段用到的提示詞，
# 方便之後要調整語氣、規則或輸出格式時只改這一個地方；app/agents/*.py 只負責準備
# 上下文變數（角色資料、劇本內容、機制結果等），呼叫這裡的 build_* 函式組出最終送
# 給 LLM 的文字，不在 agent 檔案裡另外散落手刻的提示詞片段。
#
# 這個檔案原本是照抄一份完全不同（而且是簡體中文）的舊設計草稿，假設的是一個跟這個
# 專案實際做出來的流水線不同、更精細的架構（獨立的 LLM 意圖分類節點、ReAct 回合
# 計畫節點、Reflection 自檢節點、記憶壓縮節點，且每個節點都用同一大包 JSON 溝通）。
# 這次全部重寫，改成對應 app/agents/ 底下實際存在的 7 個階段：
#
#   context_builder（收集 RAG／記憶上下文，不呼叫 LLM）
#     → intent_router（規則式分類 PURE_ROLEPLAY／GAMEPLAY_ACTION，不呼叫 LLM——
#       刻意維持規則判斷，省下純角色扮演時多打一次 LLM 的成本，這是這個架構設計
#       本身要的效能目標，見 docs/agentic_keeper_design_spec.md）
#     → executor（GAMEPLAY_ACTION 才會走到；呼叫 LLM＋工具，透過
#       app/keeper.py._execute_tool 真的擲骰、真的改狀態並落庫）
#     → state_reducer（純記錄，不呼叫 LLM，也不套用任何狀態變更——真正的變更已經
#       在 executor 呼叫工具時安全完成，這裡重複套用只會製造資料錯亂，見該檔案
#       docstring）
#     → narrator（呼叫 LLM，只負責把機制結果寫成敘事，禁止重新判定）
#     → rule_validator（規則式檢查敘事有沒有洩漏系統細節，不呼叫 LLM）
#     → guard（只有 rule_validator 抓到問題時才呼叫 LLM 重寫一次，最多兩次）
#
# 所以這裡只有 executor／narrator／guard 三個階段的提示詞。intent_router／
# rule_validator 維持規則判斷，這裡沒有對應的提示詞；記憶壓縮已經有
# app/keeper.py 的 summarize_log_chunk／_persist_memory_maintenance_state 在跑
# （app/commands/router.py 每輪透過 _run_post_turn_maintenance_after_output
# 呼叫），不重複做一份。這個專案目前也沒有「AI 生成插圖」功能（show_scenario_image
# 秀的是劇本 PDF 既有的頁面圖片，不是生成的），所以沒有圖片提示詞優化的部分——
# 等真的有生成圖片的功能再回來補。


# ── Executor Agent：判斷要不要呼叫工具、呼叫哪個、怎麼填參數，不負責寫敘事 ──────

EXECUTOR_INSTRUCTION = """你是 TRPG 機制執行者（Executor Agent），下面完整的守密人規則你都要讀，但你的輸出跟
守密人不一樣：你的唯一任務是判斷這句話是否需要呼叫工具（擲骰、技能檢定、理智檢定、
調整角色數值、戰鬥、查詢劇本或記憶等），並實際呼叫對應工具取得真實結果——絕對不要
自己編造擲骰或檢定的數字，一律呼叫工具，工具怎麼選、什麼時候該用哪個難度、哪個規則，
都照下面的完整規則判斷。你的文字輸出只是給下一階段（Narrator Agent）看的內部摘要，
玩家看不到，不需要修飾語氣或寫成故事，也不用管下面規則裡關於敘事風格、防雷、NPC 演出
的部分（那些是 Narrator 的工作），條列說明呼叫了什麼、結果是什麼即可。如果這句話根本
不需要呼叫任何工具（純聊天、純角色扮演、沒有機制動作），就不要呼叫任何工具，直接回覆
「無需機制判定」。

以下是完整的守密人規則（僅供你判斷要不要呼叫工具、呼叫哪個、怎麼填參數，不是要你自己寫敘事）：
"""


def build_executor_static_prompt(keeper_static_prompt: str) -> str:
    """組出 Executor Agent 的 static_system。

    keeper_static_prompt 是呼叫端已經拿到的 app/keeper.py._build_static_prompt(state)
    輸出——那個函式是這個專案角色卡、劇本內容、NPC／地點索引、以及所有工具使用規則
    （技能檢定難度怎麼判斷、孤注一擲、彈藥／傷害規則、攜帶物合理性審查等）持續在維護
    的唯一來源，這裡不重新宣告一份，只在前面接上 Executor 專屬的角色設定跟工作範圍。
    """
    return EXECUTOR_INSTRUCTION + keeper_static_prompt


def build_dynamic_prompt_with_context(keeper_dynamic_prompt: str, rag_context: str, memory_context: str) -> str:
    """組出 dynamic_system：在 app/keeper.py._build_dynamic_prompt(state, ...) 的輸出
    （戰鬥狀態、每位角色當下的 HP/SAN/彈藥等動態數值）後面，附加這回合額外查到的劇本
    片段／過去記憶片段。Executor／Narrator 兩邊都呼叫這個函式，組法完全一樣。"""
    parts = [keeper_dynamic_prompt]
    if rag_context:
        parts.append(f"【劇本相關內容】\n{rag_context}")
    if memory_context:
        parts.append(f"【過去記憶】\n{memory_context}")
    return "\n\n".join(parts)


EXECUTOR_SCENARIO_RAG_POLICY = """【Executor 劇本檢索規則】
先檢查本回合提供的【劇本相關內容】是否已回答目前行動所需的具體劇本事實。內容已明確涵蓋的事實直接重用，不要為了確認或改寫查詢而再次呼叫 search_scenario。
只有在缺少一項會影響本次判定或眼前後果的具體事實時，才呼叫 search_scenario 補查。工具回傳已回答問題後，採用該結果繼續處理；只有另一項不同且會影響本次判定的事實仍未解答時，才再查一次。
若本回合沒有可用的【劇本相關內容】，遇到必須依劇本決定的事實時仍可照常搜尋。若上下文與搜尋結果都沒有說明該事實，保留未知，不要自行補造。
這些規則只決定如何重用劇本資訊，不會自行建立檢定、擲骰、改變角色狀態或推進場景；仍須依玩家實際行動與完整規則決定必要機制。"""


def build_executor_dynamic_prompt_with_context(
    keeper_dynamic_prompt: str,
    rag_context: str,
    memory_context: str,
) -> str:
    """Build Executor context with a turn-scoped policy for reusing proactive
    scenario retrieval while preserving follow-up searches for missing facts.

    The Narrator continues to use ``build_dynamic_prompt_with_context`` and
    does not receive this retrieval policy.
    """
    dynamic_prompt = build_dynamic_prompt_with_context(
        keeper_dynamic_prompt, rag_context, memory_context
    )
    return f"{dynamic_prompt}\n\n{EXECUTOR_SCENARIO_RAG_POLICY}"


def build_resolved_check_history_block(
    events: list[dict], character_values: dict | None
) -> str:
    """Show recent finalized outcomes separately from this turn's mechanics."""
    if not character_values:
        return ""
    lines = [
        "【目前角色數值（權威存檔）】"
        + "、".join(f"{name} {value}" for name, value in character_values.items()),
        "【近期已結算檢定（歷史事件，不代表本回合檢定）】",
    ]
    if not events:
        lines.append("沒有可用的近期檢定事件紀錄。這不代表角色目前數值沒有變化；目前數值以上方存檔為準。")
        return "\n".join(lines)
    for event in events[-5:]:
        lines.append(
            f"- {event.get('investigator', '調查員')}：{event.get('skill', '檢定')} "
            f"{event.get('skill_value', '?')}%，擲出 {event.get('roll', '?')}，"
            f"難度 {event.get('difficulty', 'regular')}，結果 {event.get('outcome', '未知')}。"
        )
        effects = event.get("state_effects", [])
        if effects:
            for effect in effects:
                lines.append(
                    f"  已提交變化：{effect.get('field')} {effect.get('before')} → "
                    f"{effect.get('after')}（{effect.get('delta'):+}）。"
                )
        else:
            lines.append("  此檢定事件沒有記錄到角色數值變化。")
    lines.append("歷史事件與本回合工具結果分開判讀；不得以本回合沒有機制操作否定歷史事件。")
    return "\n".join(lines)


# ── Narrator Agent：只負責把已經確定的機制結果寫成敘事，完全沒有工具 ────────────

NARRATOR_INSTRUCTION = """你是一個 TRPG 守密人（Narrator Agent）。
你的任務是「將已經發生的客觀事實，轉化為沉浸、懸疑且冷酷的敘事文學」。
你沒有權力決定判定成功或失敗、也不能扣除玩家的血量或理智，這些機制已經在前一個階段由系統完成，
你完全沒有工具可以呼叫，也不需要呼叫——下面規則裡提到「呼叫 XX 工具」的部分不適用於你，
純粹當作「這件事在機制上已經處理過了」來理解即可。請嚴格根據輸入的「機制結果（Mechanic
Result）」來描述場景，不要重新判定或改變這些既定事實。

語氣要求：
- 冷酷、嚴肅、帶有壓迫感與克蘇魯神話的未知恐懼。
- 絕對不要使用客服語氣，也不要因為判定成功就過度恭喜玩家。
- 如果沒有提供明確的檢定結果，只是一般對話，請維持 KP 的角色與玩家互動。

事實來源優先順序：
- 劇本內容與下面列出的角色資料是世界事實來源，不得隨意發明劇本沒寫的關鍵線索、NPC、地點或幕後真相。
- 【過去記憶】區塊只是玩家過去經歷的參考，不能拿它推翻或覆蓋這一回合的機制結果——機制結果永遠以
  最新的【系統判定結果】為準。
- 【目前角色數值】是已存檔的權威值；【近期已結算檢定】是先前回合的機制紀錄，與本回合結果分開。不得因
  本回合沒有機制操作，就否認歷史檢定或它明確記錄的數值變化；回答角色數值時採用目前角色數值。

以下是完整的守密人規則（人設、敘事風格、防雷、NPC 演出規範，以及每位角色的資料）：
"""


def build_narrator_static_prompt(keeper_static_prompt: str) -> str:
    """組出 Narrator Agent 的 static_system，做法跟 build_executor_static_prompt
    一樣（複用同一份 keeper_static_prompt），只是前面接的專屬說明不同——Narrator
    不用管工具怎麼呼叫，只需要人設、敘事風格、防雷跟角色資料的部分。"""
    return NARRATOR_INSTRUCTION + keeper_static_prompt


def build_mechanic_facts_block(result: MechanicResult) -> str:
    """GAMEPLAY_ACTION 情境：把 Executor 產出的 MechanicResult 轉成 Narrator
    看得懂的「既定事實」區塊，附加在 dynamic_system 後面。"""
    lines = [
        "【系統判定結果（事實，禁止重新判定或改變）】",
        f"成功與否: {'成功' if result.success else '失敗'}",
        "發生的事實：",
    ]
    lines.extend(f"- {fact}" for fact in result.narrative_facts)
    status = result.check_status
    if status.get("pending"):
        pending = status["pending"]
        lines.extend([
            "【待處理檢定狀態：已建立】",
            f"調查員：{pending.get('investigator', '未知')}",
            f"技能／選項：{pending.get('skill') or pending.get('options') or '見工具結果'}",
            "這是權威狀態。回覆必須明確告知檢定／選擇已建立並等待玩家處理；禁止說尚未建立、沒有待處理檢定，或要求守密人重新建立。",
        ])
    pending_luck = status.get("pending_luck")
    if pending_luck:
        options = pending_luck.get("options") or []
        options_text = "、".join(
            f"/coc luck {option.get('tier')}（花費 {option.get('cost')} 點）"
            for option in options if isinstance(option, dict)
        )
        investigator = pending_luck.get("investigator", "調查員")
        skill = pending_luck.get("skill_name", "檢定")
        lines.extend([
            "【待處理 Luck 決定：骰已擲出，最終結果尚未定案】",
            f"調查員：{investigator}；檢定：{skill}；原始骰值：{pending_luck.get('roll', '未知')}；原始等級：{pending_luck.get('original_tier', '未知')}。",
            f"可用選項：{options_text or '依待處理 Luck 按鈕選擇'}；輸入 /coc luck skip 可保留原骰結果。",
            "必須請玩家完成這筆既有 Luck 決定；禁止要求重新擲骰、建立另一筆檢定，或把骰值說成已定案的成敗。暫停同一行動的後續結果敘述。",
        ])
    resolved = status.get("resolved")
    if resolved:
        outcome = "成功" if resolved.get("success") else "失敗"
        lines.extend([
            "【已結算檢定：結果權威且不得重擲】",
            f"{resolved.get('investigator', '調查員')} 的 {resolved.get('skill', '檢定')}：技能值 {resolved.get('skill_value', '未知')}，擲出 {resolved.get('roll', '未知')}，難度 {resolved.get('difficulty', 'regular')}，等級 {resolved.get('tier', '未知')}，結果 {outcome}。",
            "這筆檢定已結算。不得改成尚未結算、因先攻延後同一擲骰結果、要求再擲一次，或從檢定結果自行推導未提供的傷害、破壞或戰鬥。",
        ])
    if not status.get("pending") and not pending_luck and not resolved:
        lines.append(
            "【檢定狀態：沒有待處理／新建立檢定，也沒有本回合已結算結果】不得指示玩家擲骰、按檢定按鈕或輸入 /coc check；"
            "可以描述尚待處理的行動，但不可暗示已有檢定等待玩家。"
        )
    return "\n".join(lines)


def build_resolved_check_outcome_block(result: dict) -> str:
    """Build a bounded, structured authority block for post-roll narration."""
    outcome = str(result.get("outcome", "結果未知"))
    skill = result.get("skill", "檢定")
    return (
        "【已結算檢定：權威機制結果】\n"
        f"調查員：{result.get('investigator', '未知')}；檢定：{skill}；"
        f"技能值：{result.get('skill_value', '未知')}；擲出 {result.get('roll', '未知')}；"
        f"難度：{result.get('difficulty', 'regular')}；最終結果：{outcome}。\n"
        f"行動情境：{str(result.get('action_context', '')).strip() or '未提供'}\n"
        "這次檢定已由系統擲骰並定案。只敘述這個結果允許的後果；不得重擲或改判、"
        "因戰鬥先攻把這次檢定說成尚未結算，或從骰值自行推導傷害、破壞、敵人現身或戰鬥。"
        "本回合只開放唯讀查詢工具，不得建立新檢定或改動遊戲狀態。"
    )


def enforce_resolved_check_consistency(text: str, result: dict) -> str:
    """Fail closed when post-roll narration says the authoritative roll is unresolved."""
    contradictions = (
        "行動尚未結算", "結果尚未結算", "檢定尚未結算", "還沒輪到", "等輪到",
        "請再擲", "重新擲", "重新建立檢定",
    )
    if not any(phrase in text for phrase in contradictions):
        return text
    outcome = str(result.get("outcome", "結果未知"))
    return (
        f"{result.get('investigator', '調查員')} 的 {result.get('skill', '檢定')} 已結算："
        f"擲出 {result.get('roll', '未知')}，難度 {result.get('difficulty', 'regular')}，"
        f"結果為「{outcome}」。這次結果不得重擲或改判；未由機制結果確認的額外後果尚未發生。"
    )


def enforce_mechanic_check_consistency(text: str, result: MechanicResult) -> str:
    """Enforce check, Luck, and resolved-result state after model narration."""
    status = result.check_status
    pending = status.get("pending")
    if pending:
        denial_phrases = ("尚未建立", "沒有建立", "還沒建立", "沒有待處理", "尚未有待處理")
        if any(phrase in text for phrase in denial_phrases):
            investigator = pending.get("investigator", "調查員")
            skill = pending.get("skill")
            detail = f"「{skill}」" if skill else "這次"
            return f"{investigator} 的{detail}檢定已建立並等待處理。請使用 /coc check 擲骰或選擇。"
        has_check_instruction = "/coc check" in text or "檢定按鈕" in text
        if not has_check_instruction:
            investigator = pending.get("investigator", "調查員")
            skill = pending.get("skill")
            detail = f"{skill} 檢定" if skill else "檢定／選擇"
            return f"{text.rstrip()}\n\n{investigator} 的{detail}已建立，請按檢定按鈕或輸入 /coc check 完成。"
        return text

    pending_luck = status.get("pending_luck")
    if pending_luck:
        if any(phrase in text for phrase in (
            "重新擲", "再擲一次", "重新建立檢定", "結果已定案", "檢定已成功", "檢定失敗",
            "/coc check", "尚未建立", "沒有建立", "沒有待處理",
        )):
            return _pending_luck_fallback(pending_luck)
        if "/coc luck" not in text and "Luck 按鈕" not in text and "幸運按鈕" not in text:
            return f"{text.rstrip()}\n\n{_pending_luck_instruction(pending_luck)}"
        return text

    resolved = status.get("resolved")
    if resolved and any(phrase in text for phrase in ("行動尚未結算", "結果尚未結算", "還沒輪到", "等輪到", "請再擲", "重新擲")):
        investigator = resolved.get("investigator", "調查員")
        skill = resolved.get("skill", "檢定")
        outcome = "成功" if resolved.get("success") else "失敗"
        return (
            f"{investigator} 的 {skill} 檢定已結算：擲出 {resolved.get('roll', '未知')}，"
            f"難度 {resolved.get('difficulty', 'regular')}，結果為{outcome}。"
            "此結果不會重擲或改判；尚未由機制結果確認的額外後果仍未發生。"
        )

    lower_text = text.lower()
    check_command_index = lower_text.find("/coc check")
    negation_pattern = r"(?:不要|勿|不必|不需要|不用|無需|不需)[^，。；！？,;!?\n]{0,5}$"
    command_context = (
        re.split(r"[，。；！？,;!?\n]", text[:check_command_index])[-1]
        if check_command_index >= 0 else ""
    )
    command_is_negated = re.search(negation_pattern, command_context) is not None
    roll_phrases = ("擲骰", "投骰", "擲出結果")
    roll_indices = [index for phrase in roll_phrases for index in [text.find(phrase)] if index >= 0]
    roll_is_negated = any(
        re.search(negation_pattern, re.split(r"[，。；！？,;!?\n]", text[:index])[-1]) is not None
        for index in roll_indices
    )
    asks_for_check = (
        (check_command_index >= 0 and not command_is_negated)
        or ("請按檢定按鈕" in text and not re.search(negation_pattern, text[:text.find("請按檢定按鈕")]))
        or (
            bool(roll_indices)
            and any(word in text for word in ("請", "需要", "可以", "使用", "按鈕"))
            and not roll_is_negated
        )
    )
    if asks_for_check:
        return "這回合沒有建立待處理檢定，目前不需要擲骰或使用 /coc check。請描述你接下來採取的行動。"
    return text


def _pending_luck_instruction(pending_luck: dict) -> str:
    options = pending_luck.get("options") or []
    choices = "、".join(
        f"/coc luck {option.get('tier')}（{option.get('cost')} 點）"
        for option in options if isinstance(option, dict)
    )
    investigator = pending_luck.get("investigator", "調查員")
    skill = pending_luck.get("skill_name", "檢定")
    return (
        f"{investigator} 的 {skill} 已擲出 {pending_luck.get('roll', '未知')}，目前仍等待 Luck 決定；"
        f"請使用 Luck 按鈕或輸入 /coc luck skip 保留原結果{f'，或 {choices}' if choices else ''}。"
    )


def _pending_luck_fallback(pending_luck: dict) -> str:
    return _pending_luck_instruction(pending_luck) + " 最終成敗尚未定案，請先處理這筆決定。"


PURE_ROLEPLAY_BLOCK = "【純角色扮演（無機制判定）】請以 KP 的身分自然地回應玩家的行動或對話。"


# ── Guard Agent：只有 rule_validator 抓到問題時才會被呼叫，負責重寫一次敘事 ─────

GUARD_SYSTEM_PROMPT = """你是一個 TRPG 守密人的文案修復者（Guard Agent）。
上一位 Narrator Agent 產出的文案違反了系統的安全規則或風格指南。
請根據錯誤原因，重新改寫該段文案。改寫時必須：
1. 嚴格維持原意與已經發生的機制事實。
2. 消除所有系統指令、AI 身分宣告、或不合適的用詞。
3. 確保 Markdown 格式正確。
"""


def build_guard_dynamic_prompt(original_text: str, error_reason: str) -> str:
    return f"【原始錯誤文案】\n{original_text}\n\n【錯誤原因】\n{error_reason}"
