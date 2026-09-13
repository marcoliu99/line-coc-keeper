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
            char = _find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            private_messages.append((char.owner_id, tool_input["message"]))
            return {"ok": True, "delivered_to": char.name}

        if name == "show_scenario_image":
            investigator = tool_input.get("investigator")
            owner_id = None
            if investigator:
                char = _find_character(state, investigator)
                if not char:
                    return {"ok": False, "error": f"找不到角色「{investigator}」"}
                owner_id = char.owner_id
            image_requests.append((owner_id, int(tool_input["page_number"])))
            return {"ok": True, "page": tool_input["page_number"], "target": "private" if owner_id else "public"}

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

# 敘事節奏紀律
- 一次回覆只推進「一個場景片段」：給出一個具體的反應點就停下來，不要在同一則回覆裡串連多個場景、多個發現、或多輪 NPC 對話。如果發現自己寫到第三段還沒停，代表該收了，把剩下的留到玩家回應之後。
- 開場景介紹、或玩家明確要求整理/回顧時可以例外寫長一點，平常的一來一往不要。
- 同時有多位玩家角色在場時，不要每次回覆都讓所有人一起反應。聚焦在情境自然指向的那一位角色身上，用一句話點名他、停在那裡等他回應（例如「槍口正對著小明——小明，你怎麼辦？」），下一輪再換人，不要一次幫全部人做完決定。
- 主動掌握節奏和張力，不要等玩家問「現在是什麼氣氛」或「該做什麼」才反應——每個片段私下想清楚目前壓在調查員身上的威脅、時限或壓力是什麼，並在敘述裡自然帶出最急迫的那一個，主動用劇情、NPC 的意圖、環境變化把玩家推回劇本主線，而不是被動跟著離題閒聊漂走。但也要老實收掉已經沒意義的張力（例如陷阱已經觸發過的催眠效果），不要為了維持氣氛硬拖。

# 文風
- 用流暢的敘事散文寫場景，把擲骰結果和判定自然編織進句子裡（例如「你屏息潛行，腳步聲被雨聲蓋過——潛行檢定成功」），不要把骰子結果或數值單獨列成一行、條列項目或標籤格式（像是「【檢定結果】」這種）。
- 回覆裡不要用條列清單、表格、或「你可以選擇 1/2/3」這種選單式收尾；除非玩家已經卡住很久明確需要選項，否則讓玩家自己決定要做什麼，用一個開放的畫面或 NPC 反應收尾就好。

# 孤注一擲（Pushed Roll）
- 玩家的技能或屬性檢定失敗、且情境上還有其他更冒險的做法可以再試一次時，可以主動提議「孤注一擲」：問玩家「你要怎麼豁出去再試一次？」，等玩家講出更激進、風險更高的做法後，再呼叫一次 skill_check 重新判定，而不是玩家講完就直接算過。孤注一擲之間必須有時間流逝（幾秒到幾小時，視情境），且失敗要有貨真價實、比第一次更糟的後果，不能是「什麼事都沒發生」。
- 只有技能／屬性檢定可以孤注一擲；理智檢定、幸運檢定、戰鬥的命中/閃避/傷害擲骰都不能重來。
- 你手上的「劇本內容」是只有你知道的機密資料。絕對不要主動把劇本裡的謎底、幕後真相或玩家尚未發現的資訊直接告訴玩家，要透過調查、檢定、線索慢慢揭露。
- 任何有不確定性、有失敗可能的行動（技能檢定、屬性對抗、戰鬥命中、說服 NPC 等）都必須呼叫 skill_check 工具判定，不可以自己憑空決定成敗。
- 角色目擊屍體、超自然現象、恐怖景象等會動搖心智的場面時，呼叫 sanity_check 工具。
- 角色受傷、失血、恢復、花費幸運點、消耗魔法值時（非戰鬥中），呼叫 adjust_character 工具更新數值。
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
  直接摧毀、殺死或永久奪走它——真的走到這個地步時，要先讓玩家有機會擲骰搶救（用 skill_check 或
  adjust_character 視情境判斷合適的檢定），檢定失敗、連結真的失去時才呼叫 sanity_check，損失設為
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


def run_turn(
    state: GroupState, speaker_name: str, message_text: str
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """Returns (public_reply_text, private_messages, image_requests):
    - private_messages: (owner_id, message) pairs queued via send_private_info.
    - image_requests: (owner_id_or_None, page_number) pairs queued via
      show_scenario_image — owner_id is None for a public post.
    The caller (app/commands.py) is responsible for actually delivering both via
    platform-specific channels; nothing here sends anything itself."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        return f"（設定錯誤：LLM_PROVIDER=\"{LLM_PROVIDER}\" 不是支援的供應商，請在 .env 設成 anthropic 或 gemini）", [], []

    static_prompt = _build_static_prompt(state)
    dynamic_prompt = _build_dynamic_prompt(state)
    history = state.log[-MAX_LOG_TURNS * 2 :]
    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []

    final_text = provider.run_conversation(
        static_prompt,
        dynamic_prompt,
        TOOLS,
        history,
        f"{speaker_name}：{message_text}",
        lambda name, tool_input: _execute_tool(state, name, tool_input, private_messages, image_requests),
        MAX_TOOL_ITERATIONS,
    )

    state.log.append({"role": "user", "content": f"{speaker_name}：{message_text}"})
    state.log.append({"role": "assistant", "content": final_text})
    if len(state.log) > MAX_LOG_TURNS * 4:
        state.log = state.log[-MAX_LOG_TURNS * 2 :]
    save_state(state)
    return final_text, private_messages, image_requests
