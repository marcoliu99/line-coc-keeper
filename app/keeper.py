"""The Keeper: an LLM-powered COC7e game master with dice/rule tools.

Provider-agnostic on purpose — the game logic here (tools, system prompt, state
mutation) doesn't know or care whether Claude or Gemini is actually generating
text. See app/providers/ for the per-SDK adapters and LLM_PROVIDER in .env for
which one is active.
"""
from __future__ import annotations

import logging

from app import combat, dice, memory_rag, scenario_rag
from app.config import LLM_PROVIDER, MAX_LOG_TURNS, MAX_TOOL_ITERATIONS, SCENARIO_RAG_ENABLED, SCENARIO_RAG_TOP_K
from app.models import BASE_SKILLS, Character, GroupState
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.skill_aliases import canonical_skill_name
from app.state import save_state

_logger = logging.getLogger(__name__)

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

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
            "『請求』一次 COC7e 技能或屬性百分比檢定——這個工具不會幫玩家骰骰子，"
            "只會記錄下這次檢定要用哪個技能、目標值多少、有沒有獎懲骰，讓玩家自己用 "
            "/coc check 指令擲骰。呼叫完之後，只能敘述『需要做這個檢定』的當下場景，"
            "絕對不能自己編一個成功或失敗的結果——真正的結果會在玩家擲骰後，由系統以"
            "訊息回饋給你，那時候才能描述後續發展。只在調查／偵查類行動、戰鬥相關行動、"
            "或對劇情有重大影響的關鍵時刻才呼叫此工具請玩家檢定；日常瑣碎、明顯不影響"
            "劇情走向的小動作直接敘述就好，不用每次都要求檢定（見下方系統提示的完整說明）。"
            "不管是否呼叫這個工具，都不可以自行判定成敗。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string", "description": "調查員角色名稱"},
                "skill": {"type": "string", "description": "技能或屬性名稱，例如：偵查、潛行、STR、POW"},
                "bonus_dice": {"type": "integer", "description": "獎勵骰數量（情境有利時），預設 0"},
                "penalty_dice": {"type": "integer", "description": "懲罰骰數量（情境不利時），預設 0"},
                "pushed": {
                    "type": "boolean",
                    "description": (
                        "這是不是「孤注一擲」(Pushed Roll，見下方系統提示同名段落) 的重新擲骰——"
                        "玩家第一次檢定失敗、你提議孤注一擲、玩家講了更冒險的做法後才呼叫的那一次，"
                        "設為 true；一般的第一次檢定不要設或設 false。COC7e 規則：孤注一擲的結果"
                        "不能再花 Luck 修改，設對這個欄位系統才擋得住。"
                    ),
                },
            },
            "required": ["investigator", "skill"],
        },
    },
    {
        "name": "offer_check_choice",
        "description": (
            "『請求』一次有多個互斥選項的檢定——用在玩家要在幾個技能之間選一個的情境"
            "（COC7e 規則書的典型例子：近戰中被攻擊時，防守方要選擇『閃避』還是『反擊』，"
            "兩者只能選一個，不能都做）。跟 skill_check 一樣不會幫玩家骰骰子，只記錄下"
            "選項清單，讓玩家自己選一個、用 /coc check <選項名稱> 擲骰。呼叫完之後只能"
            "敘述『需要在這幾個選項裡選一個』的當下場景，不能自己選、不能自己編結果。"
            "如果這是被攻擊時的防守選擇（閃避／反擊），這是正式的 COC7e 對抗檢定：務必先"
            "呼叫 npc_skill_check 幫攻擊方擲出這次攻擊的結果，把回傳的 tier 填進 attacker_tier，"
            "玩家真的擲完骰後，系統會自動比較雙方成功等級判定攻擊有沒有命中、反擊有沒有生效，"
            "不用你自己比較或判定輸贏。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string", "description": "調查員角色名稱"},
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "description": "至少兩個互斥選項",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "選項顯示名稱，例如「閃避」「反擊」"},
                            "skill": {"type": "string", "description": "這個選項要用的技能或屬性名稱"},
                            "bonus_dice": {"type": "integer", "description": "獎勵骰數量，預設 0"},
                            "penalty_dice": {"type": "integer", "description": "懲罰骰數量，預設 0"},
                        },
                        "required": ["label", "skill"],
                    },
                },
                "attacker_tier": {
                    "type": "string",
                    "enum": ["fumble", "fail", "regular", "hard", "extreme", "critical"],
                    "description": (
                        "這是防守方對抗攻擊的選擇（閃避／反擊）時才填：攻擊方這次攻擊的成功等級"
                        "（先呼叫 npc_skill_check 幫攻擊方擲出來，不要自己編）。不是防守情境（單純"
                        "多選一，不涉及被攻擊）就不用填。"
                    ),
                },
            },
            "required": ["investigator", "options"],
        },
    },
    {
        "name": "npc_skill_check",
        "description": (
            "立刻擲一次『沒有玩家可以自己擲骰』那一方（NPC、怪物、敵人）的技能百分比檢定，直接由"
            "程式碼擲骰算出真正的擲骰值和成功等級，回傳給你——不要自己編一個 NPC 的檢定結果。"
            "最常見的用途：offer_check_choice 的對抗檢定情境裡，攻擊方（通常是 NPC）這次攻擊的"
            "結果；也可以用在任何劇本需要 NPC 自己做一次檢定的場合。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_value": {"type": "integer", "description": "NPC 這項技能／屬性的百分比值"},
                "bonus_dice": {"type": "integer", "description": "獎勵骰數量，預設 0"},
                "penalty_dice": {"type": "integer", "description": "懲罰骰數量，預設 0"},
            },
            "required": ["skill_value"],
        },
    },
    {
        "name": "sanity_check",
        "description": (
            "『請求』一次理智檢定（SAN check）——用於角色目擊恐怖事物、遭遇超自然現象等場合，"
            "但跟 skill_check 一樣不會幫玩家骰骰子，只記錄下成功/失敗各自的理智損失公式，"
            "讓玩家自己用 /coc check 擲骰。呼叫完之後只能敘述『需要做理智檢定』的當下，"
            "不能自己編結果或先扣理智，等玩家擲出結果、系統回饋給你之後才描述反應。"
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
        "name": "adjust_ammo",
        "description": (
            "調整角色某把已登記彈藥的槍械目前剩餘彈數（例如開槍後扣彈、換彈匣/裝填後補滿或設成特定數量）。"
            "只對角色卡上『彈藥』欄位已經有的槍械有效（近戰/投擲武器沒有彈藥可調）；weapon 要打角色卡上"
            "顯示的槍械名稱。delta 為正負整數變化量（開一槍通常是 -1，全連發視情境可以扣更多），"
            "reload_full 設 true 會忽略 delta、直接補滿到彈匣容量（換上新彈匣/裝填完畢時用這個更準確）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "weapon": {"type": "string", "description": "角色卡『彈藥』欄位裡的槍械名稱"},
                "delta": {"type": "integer", "description": "彈數變化量，開槍扣彈用負數，預設 0"},
                "reload_full": {"type": "boolean", "description": "true 的話直接補滿彈匣，忽略 delta"},
            },
            "required": ["investigator", "weapon"],
        },
    },
    {
        "name": "add_carried_item",
        "description": (
            "把一樣角色實際拿到、帶在身上的東西加進角色卡的『攜帶物品』清單（例如一封找到的信、一把"
            "鑰匙、一張地圖、一件從犯罪現場拿走的物證）——之後每回合都會夾帶給你看，不用自己記或猜"
            "這個角色手上到底有什麼。item 用簡短、辨識得出來的描述就好，不用寫得像正式物品名稱。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "item": {"type": "string", "description": "簡短描述，例如「一封字跡潦草的信」「地下室鑰匙」"},
            },
            "required": ["investigator", "item"],
        },
    },
    {
        "name": "remove_carried_item",
        "description": "角色用掉、弄丟、交出去、或以其他方式不再持有某樣攜帶物品時，把它從角色卡的『攜帶物品』清單移除。",
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "item": {"type": "string", "description": "要移除的物品描述，需跟 add_carried_item 當初加入時的文字相符或明顯對應"},
            },
            "required": ["investigator", "item"],
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
        "description": (
            "在戰鬥中加入一個 NPC 戰鬥員，會依 DEX 重新排列先攻順位。若戰鬥還沒開始會自動開始。"
            "預設是敵人；如果是站在調查員這邊參戰的 NPC 隊友（例如雇來的嚮導、臨陣倒戈的信徒），"
            "把 is_ally 設成 true，狀態列會顯示成「隊友」而不是「敵方」。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dex": {"type": "integer", "description": "DEX 值，決定先攻順序；劇本沒寫明可抓 40-60 的一般值"},
                "hp": {"type": "integer", "description": "最大生命值"},
                "is_ally": {"type": "boolean", "description": "true 表示這是站在調查員這邊的 NPC 隊友，不是敵人"},
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
    {
        "name": "show_scenario_image",
        "description": (
            "把劇本裡某一頁的實際圖片（例如地圖、平面圖、手卡）秀給玩家看，而不是只用文字描述。"
            "只有在『目前劇本內容』裡看到那一頁被明確標示為圖片/地圖/手卡（劇本文字用"
            "『--- 第 X 頁 ---』標示頁碼）時才能用，不確定那頁有沒有存圖就不要亂猜頁碼。"
            "如果只有某位特定調查員該看到（例如他自己的私人手卡），填 investigator；"
            "要給所有人看到（例如大家一起發現的地圖）就不要填 investigator。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_number": {"type": "integer", "description": "劇本裡的頁碼，對應內文的『第 X 頁』標示"},
                "investigator": {"type": "string", "description": "只給這位調查員看；不填就是公開給所有人看"},
            },
            "required": ["page_number"],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "搜尋很久以前發生、已經不在目前對話紀錄或劇情摘要裡的舊事件——玩家問起一個具體的人名、"
            "地名、物品，但你在『先前劇情摘要』和最近的對話裡都找不到時才用這個工具查詢，不要自己"
            "編一個回答，也不要說『我不記得了』就結束。查詢字詞盡量用具體名詞（人名、地名、物品），"
            "不要問完整句子。如果查無結果，代表這件事可能真的沒發生過，或摘要裡已經有更新的說法，"
            "以摘要／最近對話為準。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要查詢的關鍵字或名詞，例如「卡西迪」「黃銅鑰匙」"},
            },
            "required": ["query"],
        },
    },
]
# Common {name, description, input_schema} shape works unmodified for both Claude
# and Gemini; any provider-specific extras (e.g. Anthropic's cache_control) are
# added by the adapter in app/providers/, not here.

# Only added to the tool list when SCENARIO_RAG_ENABLED (see run_turn below) —
# with the full scenario text already in the cached static prompt (the default),
# this tool would be redundant; it only exists to compensate for that text being
# withheld under RAG mode (see _build_static_prompt's scenario stub above).
_SEARCH_SCENARIO_TOOL = {
    "name": "search_scenario",
    "description": (
        "在劇本全文裡搜尋跟這個查詢最相關的段落（依頁面為單位），回傳前幾筆最符合的內容。"
        "劇本改用檢索模式時（看到『這份劇本改用檢索模式』的提示）必須用這個工具查詢，"
        "不能憑空想像劇本內容；查詢字詞盡量用劇本裡可能出現的具體名詞（人名、地名、物品、關鍵字），"
        "不要問完整句子。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查詢的關鍵字或名詞，例如「卡西迪」「地下室」「儀式」"},
        },
        "required": ["query"],
    },
}


def find_character(state: GroupState, name: str) -> Character | None:
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


def resolve_skill_value(char: Character, skill_name: str) -> int:
    key = skill_name.strip()
    if key in char.skills:
        return char.skills[key]
    if key.upper() in _ATTR_ALIASES:
        return getattr(char, _ATTR_ALIASES[key.upper()])

    # Canonicalize both the query and every existing key (see app/skill_aliases.py)
    # before comparing — catches e.g. "手槍" vs char.skills' own "射擊（手槍）",
    # which used to silently miss each other and fall through to the substring
    # fallback below (or worse, register a brand new duplicate skill).
    canonical_query = canonical_skill_name(key)
    if canonical_query in char.skills:
        return char.skills[canonical_query]
    for k, v in char.skills.items():
        if canonical_skill_name(k) == canonical_query:
            return v

    norm = key.replace(" ", "").lower()
    for k, v in char.skills.items():
        kk = k.replace(" ", "").lower()
        if norm == kk or norm in kk or kk in norm:
            return v

    # Unknown skill: register under its canonical name (not the raw LLM
    # phrasing) so future lookups stay consistent, using the real COC7e base
    # rate when we recognize it instead of always guessing a flat 20.
    default_value = BASE_SKILLS.get(canonical_query, 20)
    char.skills[canonical_query] = default_value
    return default_value


def _execute_tool(
    state: GroupState,
    name: str,
    tool_input: dict,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
) -> dict:
    try:
        if name == "roll_dice":
            r = dice.roll_expression(tool_input["expression"])
            return {"ok": True, "expression": r.expression, "rolls": r.rolls, "modifier": r.modifier, "total": r.total}

        if name == "skill_check":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            value = resolve_skill_value(char, tool_input["skill"])
            bonus = int(tool_input.get("bonus_dice") or 0)
            penalty = int(tool_input.get("penalty_dice") or 0)
            state.pending_checks[char.owner_id] = {
                "type": "skill", "skill": tool_input["skill"], "skill_value": value,
                "bonus_dice": bonus, "penalty_dice": penalty,
                "pushed": bool(tool_input.get("pushed", False)),
            }
            save_state(state)
            return {
                "ok": True, "pending": True, "investigator": char.name, "skill": tool_input["skill"],
                "skill_value": value, "bonus_dice": bonus, "penalty_dice": penalty,
                "note": "還沒有骰出結果，等玩家自己用 /coc check 擲骰後才會有真正的成敗——不要自己編一個。",
            }

        if name == "offer_check_choice":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            raw_options = tool_input.get("options") or []
            if len(raw_options) < 2:
                return {"ok": False, "error": "options 至少要給兩個選項，只有一個的話請直接用 skill_check"}
            options = []
            for opt in raw_options:
                # Full skill value, no artificial difficulty adjustment —
                # confirmed against the official COC7e Fight Back text:
                # it's a normal opposed roll at the defender's own combat
                # skill, not a harder version of Dodge. (A prior revision
                # here halved it as a house-rule approximation; reverted.)
                value = resolve_skill_value(char, opt["skill"])
                options.append({
                    "label": opt["label"], "skill": opt["skill"], "skill_value": value,
                    "bonus_dice": int(opt.get("bonus_dice") or 0), "penalty_dice": int(opt.get("penalty_dice") or 0),
                })
            pending_choice = {"type": "choice", "options": options}
            attacker_tier = tool_input.get("attacker_tier")
            if attacker_tier:
                pending_choice["attacker_tier"] = attacker_tier
            state.pending_checks[char.owner_id] = pending_choice
            save_state(state)
            return {
                "ok": True, "pending": True, "investigator": char.name, "options": options,
                "note": "還沒有骰出結果，等玩家自己選一個選項、用 /coc check <選項名稱> 擲骰後才會有結果——不要自己選、不要自己編一個。",
            }

        if name == "npc_skill_check":
            skill_value = max(0, min(100, int(tool_input["skill_value"])))
            bonus = int(tool_input.get("bonus_dice") or 0)
            penalty = int(tool_input.get("penalty_dice") or 0)
            r = dice.skill_check(skill_value, bonus_dice=bonus, penalty_dice=penalty)
            return {"ok": True, "roll": r.roll, "tier": r.tier, "skill_value": skill_value}

        if name == "sanity_check":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            loss_success = tool_input.get("loss_success", "0")
            loss_failure = tool_input.get("loss_failure", "1d4")
            state.pending_checks[char.owner_id] = {
                "type": "sanity", "loss_success": loss_success, "loss_failure": loss_failure,
            }
            save_state(state)
            return {
                "ok": True, "pending": True, "investigator": char.name, "current_san": char.san,
                "note": "還沒有骰出結果，等玩家自己用 /coc check 擲骰後才會知道有沒有損失理智——不要自己編一個。",
            }

        if name == "adjust_character":
            char = find_character(state, tool_input.get("investigator", ""))
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

        if name == "adjust_ammo":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            weapon = tool_input.get("weapon", "")
            entry = char.weapons.get(weapon)
            if entry is None:
                available = "、".join(char.weapons.keys()) or "（沒有登記彈藥的槍械）"
                return {"ok": False, "error": f"「{char.name}」的彈藥欄位裡沒有「{weapon}」，目前有：{available}"}
            if tool_input.get("reload_full"):
                entry["ammo"] = entry["ammo_max"]
            else:
                entry["ammo"] = max(0, min(entry["ammo_max"], entry["ammo"] + int(tool_input.get("delta") or 0)))
            save_state(state)
            return {"ok": True, "investigator": char.name, "weapon": weapon, "ammo": entry["ammo"], "ammo_max": entry["ammo_max"]}

        if name == "add_carried_item":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            item = tool_input.get("item", "").strip()
            if not item:
                return {"ok": False, "error": "item 不能是空字串"}
            if item not in char.carried_items:
                char.carried_items.append(item)
                save_state(state)
            return {"ok": True, "investigator": char.name, "carried_items": char.carried_items}

        if name == "remove_carried_item":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            item = tool_input.get("item", "")
            if item in char.carried_items:
                char.carried_items.remove(item)
                save_state(state)
            return {"ok": True, "investigator": char.name, "carried_items": char.carried_items}

        if name == "set_skill":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            value = max(0, min(100, int(tool_input["value"])))
            char.skills[tool_input["skill"]] = value
            save_state(state)
            return {"ok": True, "investigator": char.name, "skill": tool_input["skill"], "value": value}

        if name == "get_character_sheet":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            return {"ok": True, "sheet": char.to_dict()}

        if name == "start_combat":
            combat.start_combat(state)
            save_state(state)
            return {"ok": True, "status": combat.status_text(state)}

        if name == "add_npc_to_combat":
            combat.add_npc(
                state,
                tool_input["name"],
                int(tool_input.get("dex", 50)),
                int(tool_input.get("hp", 10)),
                is_ally=bool(tool_input.get("is_ally", False)),
            )
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
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            private_messages.append((char.owner_id, tool_input["message"]))
            return {"ok": True, "delivered_to": char.name}

        if name == "show_scenario_image":
            investigator = tool_input.get("investigator")
            owner_id = None
            if investigator:
                char = find_character(state, investigator)
                if not char:
                    return {"ok": False, "error": f"找不到角色「{investigator}」"}
                owner_id = char.owner_id
            image_requests.append((owner_id, int(tool_input["page_number"])))
            return {"ok": True, "page": tool_input["page_number"], "target": "private" if owner_id else "public"}

        if name == "search_scenario":
            if not state.scenario_text:
                return {"ok": False, "error": "目前沒有載入劇本可以搜尋"}
            index = scenario_rag.get_index(state.group_id, state.scenario_text)
            results = scenario_rag.search(index, tool_input.get("query", ""), top_k=SCENARIO_RAG_TOP_K)
            return {"ok": True, "results": scenario_rag.format_results(results)}

        if name == "search_memory":
            results = memory_rag.search_memory(state.group_id, tool_input.get("query", ""))
            return {"ok": True, "results": memory_rag.format_results(results)}

        return {"ok": False, "error": f"未知工具 {name}"}
    except Exception as exc:  # noqa: BLE001 - surfaced back to the model as a tool error
        return {"ok": False, "error": str(exc)}


def _build_static_prompt(state: GroupState) -> str:
    """Role/rules + scenario text + each character's *static* sheet (attributes,
    occupation, skills — see Character.static_sheet_text). This is the block the
    Anthropic adapter marks cache_control on — it's the expensive part (the full
    scenario text) and gets reused across an entire session instead of re-billed
    on every single message. Only changes when a new PDF is loaded, a character
    joins/leaves, or a skill/attribute is edited (/coc setskill, skill growth,
    ...) — all rare compared to HP/SAN/Luck changing almost every turn, which is
    exactly why those live in _build_dynamic_prompt's uncached block instead: a
    literal copy of the whole roster here on every message would only inflate
    what has to be recomputed/re-billed whenever it changes, for no benefit,
    since dynamic_state_text() already covers what actually needs to be fresh.
    (Gemini's context caching isn't wired up yet; see app/providers/gemini_provider.py.)"""
    if not state.scenario_text:
        scenario = "（尚未載入劇本，請提醒玩家用 /coc 上傳 PDF 劇本）"
    elif SCENARIO_RAG_ENABLED:
        # Full text withheld on purpose — see search_scenario in TOOLS/_execute_tool
        # and app/scenario_rag.py. Keeps this (cached) block small regardless of
        # scenario length, at the cost of the Keeper needing to actually remember
        # to search instead of already having everything in view.
        scenario = (
            "（這份劇本改用檢索模式：完整內容沒有直接放在這裡，需要任何劇本細節"
            "——地點、NPC、線索、數值、劇情走向——都要呼叫 search_scenario 工具查詢，"
            "不要憑空想像或用你自己對「典型 COC 劇本」的印象腦補劇本沒查到的內容。）"
        )
    else:
        scenario = state.scenario_text

    static_chars_text = "\n\n".join(c.static_sheet_text() for c in state.characters.values()) or "（目前尚無登記角色）"
    summary_block = ""
    if state.campaign_summary:
        summary_block = f"""

# 先前劇情摘要（更早之前的對話已經被裁掉，這是那些內容的精簡摘要，記得參考，不要當作沒發生過）
{state.campaign_summary}
如果玩家問起一個具體的人名/地名/物品，這份摘要跟最近的對話都找不到（摘要是壓縮過的，可能已經漏掉細節），
呼叫 search_memory 工具去查更早、還沒被壓縮掉的原始對話內容，不要直接說忘記了或自己編一個答案。"""
    return f"""你是一位主持《克蘇魯的呼喚》第七版（Call of Cthulhu 7th Edition）跑團的守密人（Keeper），正在群組聊天室（LINE 或 Discord）中透過文字對話主持一場遊戲。

# 行為準則
- 全程使用繁體中文，營造洛夫克拉夫特式的懸疑恐怖氛圍，但訊息長度要適合聊天軟體閱讀：每次回覆盡量 3 到 8 句，避免長篇大論、避免使用 Markdown 標題或表格。

# 敘事節奏紀律
- 一次回覆只推進「一個場景片段」：給出一個具體的反應點就停下來，不要在同一則回覆裡串連多個場景、多個發現、或多輪 NPC 對話。如果發現自己寫到第三段還沒停，代表該收了，把剩下的留到玩家回應之後。
- 開場景介紹、或玩家明確要求整理/回顧時可以例外寫長一點，平常的一來一往不要。
- 同時有多位玩家角色在場時，不要每次回覆都讓所有人一起反應。聚焦在情境自然指向的那一位角色身上，用一句話點名他、停在那裡等他回應（例如「槍口正對著小明——小明，你怎麼辦？」），下一輪再換人，不要一次幫全部人做完決定。
- 主動掌握節奏和張力，不要等玩家問「現在是什麼氣氛」或「該做什麼」才反應——每個片段私下想清楚目前壓在調查員身上的威脅、時限或壓力是什麼，並在敘述裡自然帶出最急迫的那一個，主動用劇情、NPC 的意圖、環境變化把玩家推回劇本主線，而不是被動跟著離題閒聊漂走。但也要老實收掉已經沒意義的張力（例如陷阱已經觸發過的催眠效果），不要為了維持氣氛硬拖。

# 文風
- 用流暢的敘事散文寫場景，把擲骰結果和判定自然編織進句子裡（例如「你屏息潛行，腳步聲被雨聲蓋過——潛行檢定成功」），不要把骰子結果或數值單獨列成一行、條列項目或標籤格式（像是「【檢定結果】」這種）。
- 回覆裡不要用條列清單、表格、或「你可以選擇 1/2/3」這種選單式收尾；除非玩家已經卡住很久明確需要選項，否則讓玩家自己決定要做什麼，用一個開放的畫面或 NPC 反應收尾就好。

# 檢定由玩家自己擲骰，不是你代骰
- skill_check／sanity_check 這兩個工具現在只是「請求」一次檢定，不會幫你骰出結果：呼叫之後只會拿到
  目標值、獎懲骰之類的設定資訊，沒有成功或失敗的結果。你要做的是在敘述裡明確講清楚「現在需要一次
  什麼檢定、目標值大概怎樣、有沒有優勢劣勢」，然後停在那裡，等玩家自己輸入 `/coc check` 擲骰。
- **絕對不要自己編一個檢定結果**——不管是「大失敗」「成功」還是任何等級，只要玩家還沒有真的擲出來，
  你就不知道結果，也不能假裝知道。玩家擲骰後，系統會用一則訊息把真正的結果（擲出多少、什麼等級）
  回饋給你，那時候你才能根據那個既定事實描述後續發展——這則訊息裡的結果是不能改的既定事實，
  跟 Map Engine 解析出的位置一樣，你只負責敘述，不負責判定。
- 這個規則的例外只有：`roll_dice`（單純的道具/傷害骰，不是角色的技能檢定，繼續由你直接呼叫）、
  以及本來就不會有玩家角色可以骰的情境（例如純粹的環境描述、劇情事件擲骰）。
- 玩家要在幾個互斥的技能之間自己選一個時（不是你幫他決定，是他要選），呼叫 `offer_check_choice`
  給選項（至少兩個），不要用 `skill_check` 自己決定用哪個技能，也不要自己選好了才呼叫 `skill_check`。

# 孤注一擲（Pushed Roll）
- 玩家的技能或屬性檢定失敗、且情境上還有其他更冒險的做法可以再試一次時，可以主動提議「孤注一擲」：問玩家「你要怎麼豁出去再試一次？」，等玩家講出更激進、風險更高的做法後，再呼叫一次 skill_check『請』玩家孤注一擲重新擲骰，而不是玩家講完就直接算過。這次呼叫 skill_check 一定要把 `pushed` 參數設成 true（COC7e 規則：孤注一擲的結果是最終結果，不能再花 Luck 修改，系統要靠這個參數才擋得住，不設的話玩家還是會看到花 Luck 的選項）。孤注一擲之間必須有時間流逝（幾秒到幾小時，視情境），且失敗要有貨真價實、比第一次更糟的後果，不能是「什麼事都沒發生」。
- 只有技能／屬性檢定可以孤注一擲；理智檢定、幸運檢定、戰鬥的命中/閃避/傷害擲骰都不能重來。
- 你手上的「劇本內容」是只有你知道的機密資料。絕對不要主動把劇本裡的謎底、幕後真相或玩家尚未發現的資訊直接告訴玩家，要透過調查、檢定、線索慢慢揭露。
- 不用每次有不確定性的行動都要求玩家檢定——只在下列情況才呼叫 skill_check 工具『請』玩家檢定：
  (1) 調查／偵查類行動（找線索、辨認事物、專業知識判斷、搜索等）；
  (2) 戰鬥相關行動（攻擊命中、閃避、戰鬥中的技能對抗）；
  (3) 對劇情發展有重大影響的關鍵時刻（可能改變劇情走向的抉擇、逃脫危險、取得關鍵線索、說服關鍵 NPC 等）。
  日常、瑣碎、明顯不會失敗或失敗也不影響劇情的小動作（閒聊、簡單移動、清楚會成功的小事）直接用
  敘事帶過即可，不要為了小事也要求檢定；拿不準的話，優先往上面三類去想，而不是每個行動都檢定。
  不管是否呼叫這個工具，都不可以自己憑空決定成敗，也不可以自己骰。
- 角色目擊屍體、超自然現象、恐怖景象等會動搖心智的場面時，呼叫 sanity_check 工具『請』玩家做理智檢定。
- 角色受傷、失血、恢復、花費幸運點、消耗魔法值時（非戰鬥中），呼叫 adjust_character 工具更新數值。
- 角色卡「彈藥」欄位裡有登記的槍械，每次真的開槍（不管在不在正式戰鬥中）都要呼叫 adjust_ammo 扣彈（一般一發 delta 為 -1，連發視情境扣更多）；角色卡上沒有登記彈藥的武器（近戰、投擲、或角色卡沒寫彈容量的槍）不用呼叫這個工具，正常敘事就好。彈匣打光了要繼續開槍，先敘述「扳機扣下去只有喀一聲」而不是讓子彈生出來；角色花時間裝填/換彈匣後，呼叫 adjust_ammo 並把 reload_full 設 true 補滿。
- 角色真的撿到、拿到、被交付一樣值得記住的東西時（信件、鑰匙、地圖、物證……），呼叫 add_carried_item 加進他的攜帶物品清單，之後每回合都會夾帶給你看，不用自己記這個角色手上有什麼；東西用掉、弄丟、交出去、被沒收時呼叫 remove_carried_item 拿掉。不要讓玩家「我一直都帶著 X」這種說法回溯生出一個從沒記錄過的物品——沒登記過的東西，判斷角色現在合不合理擁有，合理才用 add_carried_item 補登記，不合理就照劇情擋下來。日常小物（筆記本、零錢、一般衣物）不用特別登記，只登記真的重要、值得跨場景記住的東西。
- 一般描述性的擲骰（例如傷害骰）用 roll_dice。
- 當敘事中出現「打起來了」的場面（攻擊、被攻擊、追逐戰鬥等），呼叫 start_combat 開始正式戰鬥、用 add_npc_to_combat 加入敵人，進入戰鬥規則的流程（見下方「目前戰鬥狀態」區塊）；小規模、沒有生命危險的推擠拉扯不需要進入正式戰鬥。
- 劇本內容裡如果有些頁面明顯是圖片內容（地圖、平面圖、手卡——這些頁面的文字通常是「[圖片內容描述：...]」或類似的視覺描述，而不是一般敘述文字），當玩家實際看到／拿到那個東西時，呼叫 show_scenario_image 把那一頁的實際圖片秀出來，比純文字描述更清楚；只有特定人該看到的手卡記得帶 investigator 參數只給那個人看。
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
- 角色卡如果標示「★ 關鍵背景連結」，代表那是這個角色最重要的一段個人連結（人、地、物）。不能不由分說就
  直接摧毀、殺死或永久奪走它——真的走到這個地步時，要先呼叫 skill_check 請玩家自己擲骰搶救（視情境判斷
  合適的技能），玩家真的擲出失敗、連結真的失去時才呼叫 sanity_check 請他做理智檢定，損失設為
  '1'/'1d6'。這個欄位是公開的（不像秘密目標），可以正常寫進公開敘述裡。

# NPC 隊友
- 劇本或玩家安排的 NPC 隊友，要當成「AI 扮演的調查員」來演，不是你（守密人）的傳聲筒或提示機。他們只知道
  自己親眼看到、被告知、或自己實際檢定/調查到的資訊，可以判斷錯誤、有情緒、有自己的個性和小毛病，
  就是一個活生生的角色，不是萬事通。
- 絕對不能借 NPC 隊友的嘴講出守密人專屬的真相、最佳路線、怪物弱點或劇本結構；NPC 隊友如果要分析情況，
  一定要包裝成「他自己的猜測」，而且這個猜測可以是錯的，需要的話讓他自己去問劇本裡的 NPC、查資料、
  或呼叫 skill_check 才能真的拿到資訊，跟玩家角色一樣要走正常流程。
- 每個 NPC 隊友要有明確、符合劇情的理由加入這次調查（受雇、被牽連、專業被找上、自己也有利害關係等），
  介紹登場時簡短說明這一點，不要讓他們憑空冒出來就跟主角情同手足。
- 正式戰鬥中的 NPC 隊友（用 add_npc_to_combat 加入、is_ally 設 true）跟敵人一樣照先攻順位輪流行動，
  即使當下鏡頭焦點在玩家角色身上，也不能讓隊友原地發呆不做事——輪到他們時照樣要有動作、擲骰、反應。

# 已登記的調查員（屬性、職業、技能——這些幾乎不會變動，數值以這裡為準，不要自己憑印象講一個不一樣的
數字；HP/SAN/Luck/彈藥/攜帶物品這些每回合會變的東西不在這裡，在每則訊息的動態資訊區塊裡，那邊的
數字才是當下最新的）
{static_chars_text}{summary_block}

# 目前劇本內容（機密，僅供你判斷用，勿直接洩漏給玩家）
{scenario}
"""


def _build_dynamic_prompt(state: GroupState, user_id: str, resolved_location: dict | None = None) -> str:
    """Combat status + each character's *dynamic* state (HP/SAN/Luck/ammo/
    carried items — see Character.dynamic_state_text; the static attributes/
    skills counterpart lives in _build_static_prompt's cached block instead).
    Changes every turn, so this stays OUTSIDE the cached block — it's small
    and cheap to resend, and keeping it separate means those changes don't
    invalidate the much larger cached scenario+roster block above."""
    chars_text = "\n".join(c.dynamic_state_text() for c in state.characters.values()) or "（目前尚無登記角色）"
    secret_goals = "\n".join(c.keeper_notes_text() for c in state.characters.values() if c.secret_goal)
    secret_block = f"\n\n{secret_goals}" if secret_goals else ""

    location_block = ""
    if resolved_location:
        desc = resolved_location.get("room_description") or ""
        desc_part = f"（{desc}）" if desc else ""
        location_block = f"""

# 地圖引擎已解析出的位置（Map Engine，這是程式算出來的事實，不是你的判斷）
調查員這次的移動已經由地圖引擎依房間圖算出結果：現在人在「{resolved_location.get('room_name', '')}」{desc_part}。
照這個地點來描述場景，不要自己另外猜測或改成別的房間；地圖引擎沒解析出結果時（沒有這個區塊時），才照舊由你自己判斷移動去了哪裡。"""

    current_page = state.current_map_page.get(user_id, "")
    active_map = state.scene_maps.get(current_page) if current_page else None
    if active_map and not resolved_location:
        current_room_id = state.current_room_id.get(user_id, "")
        current_room = next(
            (r for r in active_map.get("rooms", []) if r.get("id") == current_room_id), None
        )
        if current_room:
            exits = current_room.get("exits", [])
            exits_text = "、".join(f"{e.get('label') or e.get('compass')}" for e in exits) or "（沒有記錄到出口）"
            location_block = f"""

# 目前所在房間（地圖引擎追蹤中，這次玩家的移動沒有被解析出新位置）
調查員目前在「{current_room.get('name', '')}」，這個房間記錄到的出口：{exits_text}。
如果玩家這次是想移動到別的房間但地圖引擎沒解析出來，可能是講法比較模糊或那個方向真的沒有路，
用劇情自然帶過或請他講清楚一點，不要憑空移動到地圖引擎沒驗證過的房間。"""

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
不用特別等他們；戰鬥明確結束（一方全滅或撤退）時呼叫 end_combat。玩家角色在近戰中被攻擊時，防守方要在
「閃避」跟「反擊」之間選一個（COC7e 規則），呼叫 offer_check_choice 給這兩個選項讓玩家自己選，不要自己
幫玩家決定要閃避還是反擊。這是正式的對抗檢定：先呼叫 npc_skill_check 讓攻擊方（通常是 NPC）擲出這次
攻擊的成功等級，填進 offer_check_choice 的 attacker_tier，玩家真的擲完骰後系統會自動判定攻擊有沒有
命中、反擊有沒有生效，你只需要照系統回饋的既定結果敘述，不用自己比較雙方骰出的等級誰贏。"""

    return f"""# 目前動態數值（HP/SAN/Luck/彈藥/攜帶物品/狀態——這些才是當下最新的，屬性和技能請看上面的角色登記區塊）
{chars_text}{secret_block}
{combat_block}{location_block}
"""


_SUMMARY_TOOL = {
    "name": "report_summary",
    "description": "回報融合後的劇情進度摘要文字。",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "融合「現有摘要」和「待整合的舊對話」後、更新過的劇情進度摘要，300 字以內。"
                    "必須保留：關鍵道具、重要 NPC 互動、已解決或未解決的任務線、地點變更。"
                ),
            },
        },
        "required": ["summary"],
    },
}


def summarize_log_chunk(current_summary: str, old_messages: list[dict[str, str]]) -> str:
    """Rolling summarization — see run_turn below, called only on the rare
    turn where state.log is about to be trimmed past MAX_LOG_TURNS*4. Folds
    old_messages (the chunk about to be dropped) into current_summary via one
    forced tool call, dispatched through whichever LLM_PROVIDER is configured
    (same analyze_text pattern as app/pregen_extractor.py/scenario_compare.py
    — never hard-coded to one vendor's client, since this project's whole
    point is LLM_PROVIDER being freely switchable).

    Degrades gracefully: no provider configured, the call raises, or it
    returns nothing usable all fall back to returning current_summary
    unchanged (logged, not raised) — a failed summarization should never
    crash the turn or lose the existing summary, only leave it stale."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        return current_summary
    try:
        formatted_history = "\n".join(f"{m['role']}: {m['content']}" for m in old_messages)
        result = provider.analyze_text(
            formatted_history,
            _SUMMARY_TOOL,
            "你是一個 TRPG 遊戲紀錄員。請將「待整合的舊對話」融合進「現有摘要」，"
            "更新成一份精煉的劇情進度摘要，用 report_summary 工具回報。\n\n"
            f"【現有摘要】\n{current_summary or '（目前尚無摘要）'}",
        )
        summary = (result or {}).get("summary", "").strip()
        return summary or current_summary
    except Exception:
        _logger.exception("summarize_log_chunk failed, keeping previous summary unchanged")
        return current_summary


def run_turn(
    state: GroupState, user_id: str, speaker_name: str, message_text: str, resolved_location: dict | None = None
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """Returns (public_reply_text, private_messages, image_requests):
    - private_messages: (owner_id, message) pairs queued via send_private_info.
    - image_requests: (owner_id_or_None, page_number) pairs queued via
      show_scenario_image — owner_id is None for a public post.
    `resolved_location` is app/commands.py's Map/Scene Engine result (see
    _resolve_map_action there) — {"room_name", "room_description"} when this
    message's movement was already resolved deterministically against a
    scenario floor plan, else None. `user_id` is the speaking character's
    owner_id, used to look up their per-character map position when
    resolved_location wasn't computed this turn (see GroupState.current_map_page).
    The caller is responsible for actually delivering private_messages/image_requests
    via platform-specific channels; nothing here sends anything itself."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        return f"（設定錯誤：LLM_PROVIDER=\"{LLM_PROVIDER}\" 不是支援的供應商，請在 .env 設成 anthropic、gemini 或 openai）", [], []

    static_prompt = _build_static_prompt(state)
    dynamic_prompt = _build_dynamic_prompt(state, user_id, resolved_location)
    history = state.log[-MAX_LOG_TURNS * 2 :]
    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    tools = TOOLS + [_SEARCH_SCENARIO_TOOL] if SCENARIO_RAG_ENABLED else TOOLS

    final_text = provider.run_conversation(
        static_prompt,
        dynamic_prompt,
        tools,
        history,
        f"{speaker_name}：{message_text}",
        lambda name, tool_input: _execute_tool(state, name, tool_input, private_messages, image_requests),
        MAX_TOOL_ITERATIONS,
    )

    state.log.append({"role": "user", "content": f"{speaker_name}：{message_text}"})
    state.log.append({"role": "assistant", "content": final_text})
    if len(state.log) > MAX_LOG_TURNS * 4:
        # Rolling summarization (see summarize_log_chunk above): fold the
        # chunk about to be dropped into campaign_summary *before* dropping
        # it, instead of just discarding it — this is the one rare turn every
        # ~MAX_LOG_TURNS*2 turns that pays for an extra (cheap) LLM call, so
        # early plot points survive past what the verbatim log can hold.
        keep_from = -MAX_LOG_TURNS * 2
        dropped_chunk = state.log[:keep_from]
        state.campaign_summary = summarize_log_chunk(state.campaign_summary, dropped_chunk)
        # Also persist the chunk's *original* wording into the searchable
        # memory index (app/memory_rag.py) — campaign_summary alone would
        # keep recompressing an already-compressed summary on every future
        # trim, eroding fine detail a little more each pass; this keeps the
        # verbatim text retrievable via search_memory even after that.
        formatted_chunk = "\n".join(f"{m['role']}: {m['content']}" for m in dropped_chunk)
        memory_rag.append_memory(state.group_id, formatted_chunk)
        state.log = state.log[keep_from:]
    save_state(state)
    return final_text, private_messages, image_requests
