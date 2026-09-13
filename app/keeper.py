"""The Keeper: an LLM-powered COC7e game master with dice/rule tools.

Provider-agnostic on purpose — the game logic here (tools, system prompt, state
mutation) doesn't know or care whether Claude or Gemini is actually generating
text. See app/providers/ for the per-SDK adapters and LLM_PROVIDER in .env for
which one is active.
"""
from __future__ import annotations

from app import combat, dice
from app.config import LLM_PROVIDER, MAX_LOG_TURNS, MAX_TOOL_ITERATIONS
from app.models import Character, GroupState
from app.providers import anthropic_provider, gemini_provider
from app.state import save_state

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider}

_ATTR_ALIASES = {
    "STR": "str_", "力量": "str_", "CON": "con", "體質": "con", "SIZ": "siz", "體型": "siz",
    "DEX": "dex", "敏捷": "dex", "APP": "app", "外貌": "app", "INT": "int_", "智力": "int_",
    "POW": "pow_", "意志": "pow_", "精神力": "pow_", "EDU": "edu", "教育": "edu",
    "LUCK": "luck", "幸運": "luck",
}

TOOLS = [
    {
        "name": "roll_dice",
        "description": (
            "擲一般骰子（例如傷害骰、物品檢定等），例如 '1d100'、'3d6+2'、'1d4'。"
            "不要自行編造數字，任何需要隨機結果的地方都呼叫此工具。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "骰子表示式，如 1d100、2d6+3"},
                "purpose": {"type": "string", "description": "這次擲骰的用途說明（例如：小刀傷害）"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "skill_check",
        "description": (
            "進行一次 COC7e 技能或屬性百分比檢定（d100 對抗技能值），回傳成功等級"
            "（大失敗/失敗/成功/困難成功/極難成功/大成功）。角色要嘗試任何有不確定性、"
            "有失敗風險的行動時都必須呼叫此工具，不可以自行判定成敗。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string", "description": "調查員角色名稱"},
                "skill": {"type": "string", "description": "技能或屬性名稱，例如：偵查、潛行、STR、POW"},
                "bonus_dice": {"type": "integer", "description": "獎勵骰數量（情境有利時），預設 0"},
                "penalty_dice": {"type": "integer", "description": "懲罰骰數量（情境不利時），預設 0"},
            },
            "required": ["investigator", "skill"],
        },
    },
    {
        "name": "sanity_check",
        "description": (
            "進行理智檢定（SAN check）。用於角色目擊恐怖事物、遭遇超自然現象等場合。"
            "會自動依成功/失敗擲出對應的理智損失並更新角色理智值。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "loss_success": {"type": "string", "description": "檢定成功時的理智損失，如 '0'、'1'、'1d4'"},
                "loss_failure": {"type": "string", "description": "檢定失敗時的理智損失，如 '1d6'、'1d10'"},
            },
            "required": ["investigator", "loss_success", "loss_failure"],
        },
    },
    {
        "name": "adjust_character",
        "description": (
            "調整角色的 HP、MP、SAN 或 LUCK 數值（例如受傷扣血、花費幸運點、恢復精神力）。"
            "field 只能是 hp/mp/san/luck，delta 為正負整數變化量。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "field": {"type": "string", "enum": ["hp", "mp", "san", "luck"]},
                "delta": {"type": "integer", "description": "變化量，扣減用負數"},
            },
            "required": ["investigator", "field", "delta"],
        },
    },
    {
        "name": "set_skill",
        "description": "直接設定角色某項技能的數值（用於角色建立時的手動修正，或技能成長後更新）。一般遊戲過程中很少需要用到。",
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "skill": {"type": "string"},
                "value": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["investigator", "skill", "value"],
        },
    },
    {
        "name": "get_character_sheet",
        "description": "取得角色完整角色卡資料（所有屬性與技能），需要確認細節時使用。",
        "input_schema": {
            "type": "object",
            "properties": {"investigator": {"type": "string"}},
            "required": ["investigator"],
        },
    },
    {
        "name": "start_combat",
        "description": (
            "開始一場正式戰鬥，會依照目前登記角色的 DEX 建立先攻順位。"
            "開戰後用 add_npc_to_combat 加入敵人，再用 advance_combat_turn 依序推進回合。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_npc_to_combat",
        "description": "在戰鬥中加入一個敵人/NPC 戰鬥員，會依 DEX 重新排列先攻順位。若戰鬥還沒開始會自動開始。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dex": {"type": "integer", "description": "敵人的 DEX 值，決定先攻順序；劇本沒寫明可抓 40-60 的一般值"},
                "hp": {"type": "integer", "description": "敵人的最大生命值"},
            },
            "required": ["name", "dex", "hp"],
        },
    },
    {
        "name": "get_combat_status",
        "description": "查詢目前戰鬥的回合數、先攻順位與所有戰鬥員的 HP，以及現在輪到誰的行動。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "advance_combat_turn",
        "description": "把戰鬥推進到下一位戰鬥員的回合（已倒下的會自動跳過）。每次處理完一位戰鬥員的行動後都必須呼叫這個工具，不可以自己心裡默默跳過。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "damage_combatant",
        "description": "調整戰鬥中某位角色或敵人的 HP（受傷用負數，治療用正數）。適用於戰鬥中的任何一方，包含玩家角色與 NPC。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "delta": {"type": "integer"},
            },
            "required": ["name", "delta"],
        },
    },
    {
        "name": "end_combat",
        "description": "結束目前的戰鬥，清除戰鬥狀態（先攻順位、回合數）。戰鬥明確分出勝負或雙方脫離後呼叫。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_private_info",
        "description": (
            "私下告訴某位調查員一段只有他自己知道的資訊（例如：秘密檢定結果、只有他發現的線索、"
            "私人物品內容、跟其他玩家角色無關的祕密）。這段內容只會送到那位玩家自己手上，"
            "群組裡的其他人看不到。呼叫這個工具之後，公開回覆仍然要正常描述場景，"
            "但不能把這段私人內容洩漏在公開回覆裡；可以用中性、不劇透的方式帶過"
            "（例如「他若有所思地看著手上的東西，沒有多說什麼」）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string", "description": "要私訊的調查員角色名稱"},
                "message": {"type": "string", "description": "要私下告訴他的內容"},
            },
            "required": ["investigator", "message"],
        },
    },
]
# Common {name, description, input_schema} shape works unmodified for both Claude
# and Gemini; any provider-specific extras (e.g. Anthropic's cache_control) are
# added by the adapter in app/providers/, not here.


def _find_character(state: GroupState, name: str) -> Character | None:
    if not name:
        return None
    exact = state.get_character_by_name(name)
    if exact:
        return exact
    norm = name.strip().lower()
    for c in state.characters.values():
        if norm and (norm in c.name.lower() or c.name.lower() in norm):
            return c
    return None


def _resolve_skill_value(char: Character, skill_name: str) -> int:
    key = skill_name.strip()
    if key in char.skills:
        return char.skills[key]
    if key.upper() in _ATTR_ALIASES:
        return getattr(char, _ATTR_ALIASES[key.upper()])
    norm = key.replace(" ", "").lower()
    for k, v in char.skills.items():
        kk = k.replace(" ", "").lower()
        if norm == kk or norm in kk or kk in norm:
            return v
    # Unknown skill: register with a modest default so future calls stay consistent.
    default_value = 20
    char.skills[key] = default_value
    return default_value


def _execute_tool(state: GroupState, name: str, tool_input: dict, private_messages: list[tuple[str, str]]) -> dict:
    try:
        if name == "roll_dice":
            r = dice.roll_expression(tool_input["expression"])
            return {"ok": True, "expression": r.expression, "rolls": r.rolls, "modifier": r.modifier, "total": r.total}

        if name == "skill_check":
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            value = _resolve_skill_value(char, tool_input["skill"])
            bonus = int(tool_input.get("bonus_dice") or 0)
            penalty = int(tool_input.get("penalty_dice") or 0)
            r = dice.skill_check(value, bonus_dice=bonus, penalty_dice=penalty)
            save_state(state)
            return {
                "ok": True, "investigator": char.name, "skill": tool_input["skill"],
                "skill_value": r.skill_value, "roll": r.roll, "tier": r.tier, "success": r.success,
            }

        if name == "sanity_check":
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            r = dice.sanity_check(char.san, tool_input.get("loss_success", "0"), tool_input.get("loss_failure", "1d4"))
            char.san = r.san_after
            save_state(state)
            return {
                "ok": True, "investigator": char.name, "roll": r.check.roll, "success": r.check.success,
                "san_before": r.san_before, "san_after": r.san_after, "loss": r.loss,
                "risk_of_madness": r.risk_of_madness, "insane": r.san_after <= 0,
            }

        if name == "adjust_character":
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            field_name = tool_input["field"]
            attr_map = {"hp": ("hp", "hp_max"), "mp": ("mp", "mp_max"), "san": ("san", "san_max"), "luck": ("luck", None)}
            if field_name not in attr_map:
                return {"ok": False, "error": "field 必須是 hp/mp/san/luck 其中之一"}
            cur_attr, max_attr = attr_map[field_name]
            cap = getattr(char, max_attr) if max_attr else 999
            new_val = max(0, min(cap, getattr(char, cur_attr) + int(tool_input["delta"])))
            setattr(char, cur_attr, new_val)
            save_state(state)
            return {"ok": True, "investigator": char.name, "field": field_name, "value": new_val}

        if name == "set_skill":
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            value = max(0, min(100, int(tool_input["value"])))
            char.skills[tool_input["skill"]] = value
            save_state(state)
            return {"ok": True, "investigator": char.name, "skill": tool_input["skill"], "value": value}

        if name == "get_character_sheet":
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            return {"ok": True, "sheet": char.to_dict()}

        if name == "start_combat":
            combat.start_combat(state)
            save_state(state)
            return {"ok": True, "status": combat.status_text(state)}

        if name == "add_npc_to_combat":
            combat.add_npc(state, tool_input["name"], int(tool_input.get("dex", 50)), int(tool_input.get("hp", 10)))
            save_state(state)
            return {"ok": True, "status": combat.status_text(state)}

        if name == "get_combat_status":
            return {"ok": True, "status": combat.status_text(state)}

        if name == "advance_combat_turn":
            result = combat.advance_turn(state)
            save_state(state)
            return result

        if name == "damage_combatant":
            result = combat.damage_combatant(state, tool_input["name"], int(tool_input["delta"]))
            save_state(state)
            return result

        if name == "end_combat":
            combat.end_combat(state)
            save_state(state)
            return {"ok": True}

        if name == "send_private_info":
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            private_messages.append((char.owner_id, tool_input["message"]))
            return {"ok": True, "delivered_to": char.name}

        return {"ok": False, "error": f"未知工具 {name}"}
    except Exception as exc:  # noqa: BLE001 - surfaced back to the model as a tool error
        return {"ok": False, "error": str(exc)}


def _build_static_prompt(state: GroupState) -> str:
    """Role/rules + scenario text. Only changes when a new PDF is loaded, so this is
    the block the Anthropic adapter marks cache_control on — it's the expensive
    part (the full scenario text) and gets reused across an entire session instead
    of re-billed on every single message. (Gemini's context caching isn't wired up
    yet; see app/providers/gemini_provider.py.)"""
    scenario = state.scenario_text or "（尚未載入劇本，請提醒玩家用 /coc 上傳 PDF 劇本）"
    return f"""你是一位主持《克蘇魯的呼喚》第七版（Call of Cthulhu 7th Edition）跑團的守密人（Keeper），正在群組聊天室（LINE 或 Discord）中透過文字對話主持一場遊戲。

# 行為準則
- 全程使用繁體中文，營造洛夫克拉夫特式的懸疑恐怖氛圍，但訊息長度要適合聊天軟體閱讀：每次回覆盡量 3 到 8 句，避免長篇大論、避免使用 Markdown 標題或表格。
- 你手上的「劇本內容」是只有你知道的機密資料。絕對不要主動把劇本裡的謎底、幕後真相或玩家尚未發現的資訊直接告訴玩家，要透過調查、檢定、線索慢慢揭露。
- 任何有不確定性、有失敗可能的行動（技能檢定、屬性對抗、戰鬥命中、說服 NPC 等）都必須呼叫 skill_check 工具判定，不可以自己憑空決定成敗。
- 角色目擊屍體、超自然現象、恐怖景象等會動搖心智的場面時，呼叫 sanity_check 工具。
- 角色受傷、失血、恢復、花費幸運點、消耗魔法值時（非戰鬥中），呼叫 adjust_character 工具更新數值。
- 一般描述性的擲骰（例如傷害骰）用 roll_dice。
- 當敘事中出現「打起來了」的場面（攻擊、被攻擊、追逐戰鬥等），呼叫 start_combat 開始正式戰鬥、用 add_npc_to_combat 加入敵人，進入戰鬥規則的流程（見下方「目前戰鬥狀態」區塊）；小規模、沒有生命危險的推擠拉扯不需要進入正式戰鬥。
- 拿到工具結果後，用生動的敘述把結果包裝成故事講給玩家聽，而不是直接報數字；但可以自然帶出結果（例如「你腳下一滑，重重摔在地上，失去了 3 點理智」）。
- 如果玩家的行動目標不明確，用一兩句話追問，而不是自己幫他們決定要做什麼。
- 角色 HP 降到 0 時描述瀕死或死亡過程；SAN 降到 0 時描述永久性失常的下場。
- 有些資訊只該讓特定調查員知道（秘密檢定結果、只有他發現的線索、私人物品內容等），這種時候呼叫
  send_private_info 私下告訴那位玩家，不要寫進公開回覆裡；公開回覆一樣要正常描述當下場景，
  只是用中性、不劇透的方式帶過那個角色在做什麼，不要讓其他玩家從公開內容反推出私人資訊是什麼。
- **絕對不要在公開回覆裡寫出任何形式的「後設說明」或「條件式旁白」**，例如「（如果骨董商在場，這裡
  就會認出這是卡西迪——但目前無人認得他）」這種句子。這種寫法就算沒直接講出答案，也已經洩漏了「這裡
  有東西可以被特定人物認出來」這個事實本身，等於變相劇透。正確做法：如果符合條件的角色真的在場，
  直接用 send_private_info 告訴那位玩家他認出了什麼；如果沒有符合條件的角色在場，就完全不要提這件事，
  當作沒發生過，等以後有對的人在場、或用其他方式調查到才揭露。公開回覆只寫玩家角色們實際上看到、
  聽到、感受到的內容，不要有任何括號旁白解釋你身為守密人知道但玩家不知道的事。
- 角色卡上如果附了「秘密目標」，那是只有你知道、只屬於那位玩家的私人動機，不要在公開回覆裡提到；
  可以在適當時機透過劇情發展或 NPC 對話委婉暗示、引導那位玩家往那個方向行動，但不要直接講白。
- 角色卡標示「（暫離）」代表玩家目前不在，不管是不是在戰鬥中，都不需要特別等他、也不要主動描述
  他的角色在做什麼；照常推進其他人的劇情就好，他回來（狀態變回正常）之後再自然地把他寫回場景裡。

# 目前劇本內容（機密，僅供你判斷用，勿直接洩漏給玩家）
{scenario}
"""


def _build_dynamic_prompt(state: GroupState) -> str:
    """Character sheets + combat status. Changes every turn (HP/SAN/turn order all
    move), so this stays OUTSIDE the cached block — it's small and cheap to
    resend, and keeping it separate means those changes don't invalidate the much
    larger cached scenario block above."""
    chars_text = "\n\n".join(c.sheet_text() for c in state.characters.values()) or "（目前尚無登記角色）"
    secret_goals = "\n".join(c.keeper_notes_text() for c in state.characters.values() if c.secret_goal)
    secret_block = f"\n\n{secret_goals}" if secret_goals else ""

    combat_block = ""
    if state.combat.active:
        combat_block = f"""

# 目前戰鬥狀態
{combat.status_text(state)}

戰鬥規則：目前正在進行正式戰鬥，一次只處理「輪到的角色」的行動，嚴格按照上面列出的先攻順位進行——
DEX 不同的戰鬥員，行動跟敘述都要照順序來，不能因為劇情方便就打亂順序或把不同 DEX 的人合併敘述成同時
發生；只有 DEX 剛好相同的戰鬥員才可以敘述成同時行動。某位戰鬥員的行動（含擲骰結果）處理完後，必須呼叫
advance_combat_turn 工具推進到下一位，不可以自己在心裡默默跳過或一次處理多人。角色或敵人受傷、死亡要
呼叫 damage_combatant 更新血量；有新敵人加入戰場要呼叫 add_npc_to_combat；有人想讓還沒輪到的角色行動，
禮貌提醒他們要等輪到自己；標示「（暫離）」的角色代表玩家暫時離開，advance_combat_turn 會自動跳過他們，
不用特別等他們；戰鬥明確結束（一方全滅或撤退）時呼叫 end_combat。"""

    return f"""# 目前登記的調查員角色
{chars_text}{secret_block}
{combat_block}
"""


def run_turn(state: GroupState, speaker_name: str, message_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Returns (public_reply_text, private_messages) where private_messages is a
    list of (owner_id, message) pairs queued via the send_private_info tool —
    the caller (app/commands.py) is responsible for actually delivering those
    via a platform-specific DM channel; nothing here sends anything itself."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        return f"（設定錯誤：LLM_PROVIDER=\"{LLM_PROVIDER}\" 不是支援的供應商，請在 .env 設成 anthropic 或 gemini）", []

    static_prompt = _build_static_prompt(state)
    dynamic_prompt = _build_dynamic_prompt(state)
    history = state.log[-MAX_LOG_TURNS * 2 :]
    private_messages: list[tuple[str, str]] = []

    final_text = provider.run_conversation(
        static_prompt,
        dynamic_prompt,
        TOOLS,
        history,
        f"{speaker_name}：{message_text}",
        lambda name, tool_input: _execute_tool(state, name, tool_input, private_messages),
        MAX_TOOL_ITERATIONS,
    )

    state.log.append({"role": "user", "content": f"{speaker_name}：{message_text}"})
    state.log.append({"role": "assistant", "content": final_text})
    if len(state.log) > MAX_LOG_TURNS * 4:
        state.log = state.log[-MAX_LOG_TURNS * 2 :]
    save_state(state)
    return final_text, private_messages
