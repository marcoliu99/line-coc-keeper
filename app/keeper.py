"""The Keeper: an LLM-powered COC7e game master with dice/rule tools.

Provider-agnostic on purpose — the game logic here (tools, system prompt, state
mutation) doesn't know or care whether Claude or Gemini is actually generating
text. See app/providers/ for the per-SDK adapters and LLM_PROVIDER in .env for
which one is active.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, fields
from typing import Any, Callable, Generic, TypeVar, overload
from uuid import uuid4

from app import checkpoints, combat, dice, locks, memory_rag, scenario_index, scenario_library, scenario_rag, scene_digest
from app.config import LLM_PROVIDER, MAX_LOG_TURNS, MAX_TOOL_ITERATIONS, SCENE_DIGEST_TURN_INTERVAL, SCENARIO_RAG_ENABLED, SCENARIO_RAG_TOP_K
from app.models import BASE_SKILLS, Character, GroupState
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.skill_aliases import canonical_skill_name
from app.repositories.group_state import clear_page_images, load_state, save_page_image, save_state

_logger = logging.getLogger(__name__)

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}
_T = TypeVar("_T")


@dataclass
class _StateMutation(Generic[_T]):
    value: _T
    should_save: bool = True

# The Keeper's default tone/persona — a plain, importable constant (not a
# leading-underscore private one) rather than hardcoded inline in
# _build_static_prompt, so it can be:
# (a) shown to a GM via /coc setpersona's usage text (see app/commands.py) as
#     a concrete example of what a persona override looks like, and
# (b) overridden per-group via GroupState.keeper_persona (empty string means
#     "use this default" — see _build_static_prompt below), so different
#     scenarios/tables running off the same bot deployment can each set their
#     own Keeper tone instead of every game sharing one hardcoded voice.
DEFAULT_PERSONA = """- 全程使用繁體中文。你是冷酷、嚴肅、精通克蘇魯神話的守密人（Keeper），不是客氣的助理或客服。你的文風精煉、充滿壓迫感、令人窒息且懸疑。
- 絕對不要使用「太好了」、「沒問題」、「祝你好運」或任何過度親切、正向鼓勵的客服語氣——即使檢定成功、劇情進展順利，也不要用歡快、鼓勵的語氣去慶祝，用克制、冷淡的敘述帶過就好，恐怖氛圍不能因為一次成功就鬆懈。
- 面對調查員受傷、San 值狂掉或遭遇恐怖事物時，以冷酷、客觀、帶有感官細節（如鐵鏽味、腐敗氣息、異樣黏稠感、體溫變化、環境聲響）的事實直擊痛點，絕不給予安慰或溫情喊話。
- 訊息長度要適合聊天軟體閱讀：每次回覆盡量 3 到 8 句，避免長篇大論、避免使用 Markdown 標題或表格。"""

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
        "name": "roll_impaling_damage",
        "description": (
            "COC7e 規則：攻擊方的攻擊擲骰達到『極限成功』時（反擊不適用，只用在真正主動出手的"
            "攻擊）造成的加成傷害。武器傷害跟傷害加值都先算到各自的最大可能值；如果攻擊用的是"
            "穿刺武器（刀、劍、長矛、大多數槍械子彈等——尖銳、貫穿型的武器），在最大值之上再"
            "額外擲一次武器本身的傷害骰加上去；非穿刺武器（棍棒、拳頭、鈍器）只算最大值，不會"
            "額外重骰。不要自己心算或編一個數字，呼叫這個工具讓系統正確算出來；一般（非極限）"
            "成功的傷害還是用 roll_dice 正常擲。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "weapon_damage": {"type": "string", "description": "武器傷害骰表示式，例如 '1d8+1'、'1d10'、徒手 '1d3'"},
                "damage_bonus": {
                    "type": "string",
                    "description": "角色的傷害加值（DB），例如 '0'、'-1'、'-2'、'+1d4'、'+1d6'；角色卡上沒特別寫負值就填 '0'",
                },
                "impaling": {"type": "boolean", "description": "這次攻擊的武器是不是穿刺武器，true 才會額外重骰"},
            },
            "required": ["weapon_damage", "damage_bonus", "impaling"],
        },
    },
    {
        "name": "roll_weapon_damage",
        "description": (
            "擲一次一般（非極限成功）命中的武器傷害，自動查角色卡加上他的傷害加值（DB），"
            "不用自己把 DB 拼進骰子表示式（那種寫法系統解析不了，手動相加也容易算錯）。"
            "只用在角色主動攻擊、命中對方的一般傷害；如果這次攻擊擲骰是極限成功（且不是反擊），"
            "改呼叫 roll_impaling_damage，不要用這個；不是武器傷害的一般擲骰（道具、環境傷害等）"
            "還是用 roll_dice。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string", "description": "揮出這次攻擊的角色名稱，用來查詢他的傷害加值（DB）"},
                "weapon_damage": {"type": "string", "description": "武器本身的傷害骰表示式，例如 '1d8+1'、'1d10'、徒手 '1d3'"},
            },
            "required": ["investigator", "weapon_damage"],
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
                "difficulty": {
                    "type": "string",
                    "enum": ["regular", "hard", "extreme"],
                    "description": (
                        "COC7e 難度等級規則：不填或設 'regular'（一般）——對抗的技能/屬性低於 50，"
                        "或這是一般標準的任務，玩家擲出的結果只要達到『成功』（含）以上就算過；"
                        "設 'hard'（困難）——對抗的技能/屬性達到 50 以上，或這件事本來就非常困難，"
                        "玩家這次一定要擲到『困難成功』（含）以上才算過，只擲到『成功』視同失敗；"
                        "設 'extreme'（極難）——對抗的技能/屬性達到 90 以上，或這件事幾乎是人類極限，"
                        "一定要擲到『極難成功』（含）以上才算過。這是任務/對手本身的難度，"
                        "跟 bonus_dice/penalty_dice（角色這次手氣好壞、環境優劣）是兩回事，不要混用——"
                        "困難的任務該設這個欄位，不要用懲罰骰去模擬「這個門檻比較高」。"
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
            "COC7e 規則：如果玩家擲完骰後這次損失達到 5 點以上，系統會自動接著請玩家做一次"
            "INT 檢定判斷是否觸發『短暫瘋狂』（Bout of Madness），不用你自己另外呼叫任何工具、"
            "也不用你自己判斷有沒有觸發——回饋訊息裡會清楚告訴你發生了什麼，你只要照那個結果"
            "接續敘事即可。"
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
            "COC7e 規則：如果這次扣血（field=hp、delta 為負）單次傷害達到角色最大 HP 的一半以上，"
            "系統會自動接著幫玩家註冊一次 CON 檢定判斷會不會當場昏迷（重傷規則），不用你自己另外呼叫"
            "任何工具、也不用你自己判斷有沒有觸發——回傳結果裡會清楚告訴你發生了什麼，你只要照那個"
            "結果接續敘事即可。"
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
        "name": "record_established_fact",
        "description": "記錄已被證實、之後必須保持一致的劇情事實；不是猜測或普通對話。",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string"},
                "visibility": {"type": "string", "enum": ["public", "kp_only"]},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "record_clue",
        "description": "記錄調查員實際取得、之後可能回頭引用的線索。",
        "input_schema": {
            "type": "object",
            "properties": {
                "clue": {"type": "string"},
                "visibility": {"type": "string", "enum": ["public", "kp_only"]},
            },
            "required": ["clue"],
        },
    },
    {
        "name": "add_status_tag",
        "description": (
            "幫角色加上一個持續性的狀態標籤（例如「昏迷」「倒地」「中毒」「著火」），會顯示在角色卡"
            "跟每回合給你看的動態狀態資訊裡，之後不用自己記這個角色目前是不是還處在某種異常狀態。"
            "COC7e 重傷規則觸發時（單次傷害 ≥ 最大 HP 一半），系統會自動幫失敗的 CON 檢定加上「昏迷」"
            "「倒地」，不用你自己另外呼叫這個工具重複加；這個工具是給其他你自己判斷需要持續追蹤的狀態用的。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "tag": {"type": "string", "description": "狀態標籤文字，例如「昏迷」「中毒」"},
            },
            "required": ["investigator", "tag"],
        },
    },
    {
        "name": "remove_status_tag",
        "description": (
            "移除角色身上的一個狀態標籤（狀態解除時用，例如角色甦醒後移除「昏迷」「倒地」、"
            "解毒後移除「中毒」）。狀態標籤不會自己過期，記得在敘事上該解除時主動呼叫這個工具。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investigator": {"type": "string"},
                "tag": {"type": "string", "description": "要移除的標籤文字，需跟加入時的文字相符"},
            },
            "required": ["investigator", "tag"],
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
            "敵人會建立內部戰鬥卡；劇本有護甲、攻擊或特殊能力時要一起填入，不能只填 HP。"
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
                "armor": {
                    "type": "array",
                    "description": "敵人護甲規則；玩家未發現前不要公開具體數字",
                    "items": {"type": "object"},
                },
                "attacks": {
                    "type": "array",
                    "description": "敵人攻擊表，每筆含 id/label/skill_name/skill_value/damage/range_band 等",
                    "items": {"type": "object"},
                },
                "abilities": {
                    "type": "array",
                    "description": "敵人特殊能力，每筆含 id/name/priority/trigger/check/effect/usage/reveal_policy 等",
                    "items": {"type": "object"},
                },
            },
            "required": ["name", "dex", "hp"],
        },
    },
    {
        "name": "get_combat_status",
        "description": "查詢目前戰鬥的回合數、先攻順位與現在輪到誰的行動。一般公開視圖不顯示敵人 HP；KP Assistant 可看 private 視圖。",
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
        "name": "plan_enemy_turn",
        "description": (
            "輪到敵人時先呼叫這個工具。系統會檢查敵人戰鬥卡的特殊能力、觸發條件、使用次數與可用攻擊，"
            "回傳本回合應採取的 plan；不要自行假設敵人一定普通攻擊。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "enemy": {"type": "string", "description": "可省略；省略時使用目前輪到的敵人"},
            },
        },
    },
    {
        "name": "resolve_enemy_action",
        "description": (
            "敵人 plan 對應的行動已敘事/擲骰處理後呼叫，用來消耗特殊能力次數與冷卻。"
            "若 plan 的特殊能力 effect 宣告 on_success=apply_effect，必須把正式檢定結果放在 outcome.success；"
            "只有成功時系統才會建立效果。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "outcome": {
                    "type": "object",
                    "description": "特殊能力檢定的正式結果，例如 {success: true} 或 {success: false}",
                    "properties": {"success": {"type": "boolean"}},
                },
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "apply_combat_damage",
        "description": (
            "套用正式戰鬥傷害，會分開計算 raw damage、護甲抵銷、final damage 與 HP。"
            "玩家未發現前，公開敘事不可洩漏護甲/弱點的精確數值。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "raw_damage": {"type": "integer"},
                "damage_type": {"type": "string", "description": "physical/fire/bullet/melee/magic 等"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_id": {"type": "string"},
            },
            "required": ["target", "raw_damage"],
        },
    },
    {
        "name": "add_combat_effect",
        "description": (
            "替戰鬥中的角色或敵人加入固定時點效果，例如燃燒、流血、場景壓迫。"
            "damage 可填固定整數字串（例如 '1'）或骰式（例如 '1d6+1'）；"
            "效果會在 round/turn timing 由系統正式結算。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "label": {"type": "string"},
                "timing": {
                    "type": "string",
                    "enum": ["round_start", "turn_start", "turn_end", "round_end"],
                },
                "damage": {"type": "string", "description": "固定整數字串或骰式，例如 '1'、'3'、'1d6+1'"},
                "damage_type": {"type": "string", "description": "physical/fire/bullet/melee/magic 等"},
                "remaining_rounds": {"type": "integer", "description": "持續幾次成功觸發；省略表示無限期"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_id": {"type": "string"},
                "public_description": {"type": "string"},
            },
            "required": ["target", "label", "timing"],
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
        "name": "search_scenario_images",
        "description": (
            "依目前載入的兩章劇本 Context 搜尋可展示的圖片資產（地圖、人物肖像、手卡、插圖或角色卡）。"
            "先用這個工具找到正確頁碼，再呼叫 show_scenario_image；不可查詢尚未載入的後續章節。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "圖片描述或關鍵字；留空可列出目前 Context 的圖片"},
                "image_type": {"type": "string", "enum": ["map", "portrait", "handout", "illustration", "character_sheet"], "description": "可選的圖片類別"},
            },
        },
    },
    {
        "name": "advance_scenario_chapter",
        "description": (
            "劇情確實完成目前章節、進入下一個主要場景時才呼叫。會把 Context 從目前章節滑動到下一章及其後一章，"
            "並同步可展示圖片與地圖；不能跳章或用於尚未發生的內容。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },    {
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

_KP_ASSISTANT_ALLOWED_TOOL_NAMES = {
    "get_character_sheet",
    "get_combat_status",
    "search_memory",
    "search_scenario",
    "roll_dice",
    "skill_check",
    "sanity_check",
    "offer_check_choice",
    "npc_skill_check",
    "roll_weapon_damage",
    "roll_impaling_damage",
    "apply_combat_damage",
    "add_combat_effect",
    "search_scenario_images",
    "show_scenario_image",
    "advance_scenario_chapter",
    "record_established_fact",
    "record_clue",
}

_KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES = {
    "skill_check",
    "sanity_check",
    "offer_check_choice",
    "npc_skill_check",
    "roll_weapon_damage",
    "roll_impaling_damage",
    "apply_combat_damage",
    "add_combat_effect",
}

_KP_ROLL_DICE_CONTEXT_PROPERTY = {
    "type": "string",
    "enum": ["game_resolution", "ooc_randomizer"],
    "description": (
        "KP Assistant 使用一般骰子時的主持層用途分類。"
        "'game_resolution' 表示直接解析正式遊戲事件；"
        "'ooc_randomizer' 表示只供 KP 幕後隨機決策使用。"
    ),
}

_KP_OOC_LOG_MAX_MESSAGES = 20


_KP_ASSISTANT_PROMPT = """# KP 助手模式（最高優先級主持指令）

目前這一則訊息的發言者是「KP 助手」，不是玩家角色、調查員、NPC，也不是遊戲世界中的人物。

KP 助手是協助你主持這場 Call of Cthulhu 遊戲的人類共同主持者。他的訊息屬於 OOC（Out of Character）主持層指令、規則補充、劇情修正、事實更正、問題或建議。

你必須遵守以下規則：

1. 不得把 KP 助手的發言解讀成任何角色的台詞、行動、移動、檢定或戰鬥行動。

2. 不得詢問 KP 助手「你要做什麼？」、「你要去哪裡？」或其他只適用於玩家角色的問題。

3. KP 助手的明確主持指令，優先級高於你自己的敘事判斷、劇情推測、NPC 行動選擇與場景安排。
   如果 KP 助手要求你改變、停止、重寫或修正原本準備進行的敘事，你必須依照他的指令處理。

4. 如果 KP 助手指出你先前對劇本、NPC、規則、場景或事件的理解有誤，應把他的更正視為主持層修正，立即依照修正重新判斷，不要堅持先前自己的理解。

5. KP 助手可以補充目前上下文中沒有的主持資訊。除非該資訊與程式提供的 authoritative state 衝突，否則應視為有效的主持資訊。

6. 以下資料屬於程式已確定的 authoritative state，KP 助手不能只靠自然語言要求你竄改：
   - 已完成的擲骰結果與成功等級
   - Map Engine 已確定的位置
   - HP、SAN、MP、Luck 等程式保存的數值
   - 彈藥與其他程式追蹤的角色狀態
   - 正式戰鬥的先攻順位與程式確定的戰鬥狀態
   - 其他工具或規則引擎已回傳為確定事實的結果

   如果 KP 助手的要求與上述 authoritative state 衝突，保留程式確定的事實，並簡短告知 KP 助手衝突之處；除此之外，優先服從 KP 助手。

7. KP 助手本人不是調查員，所以不要替 KP 助手自己建立角色狀態、要求 KP 助手自己做技能／SAN／Luck／戰鬥檢定、加入戰鬥順位或追蹤地圖位置。
   但是，當 KP 助手明確要求某位調查員、NPC，或符合條件的玩家進行正式遊戲流程時，應對指定對象使用已開放的 deterministic tools 建立流程，不要把主持指令誤解成「KP 本人要擲骰」。
   例如：「請 The Tough Guy 做 SAN 1/1D4」應呼叫 sanity_check；「請 Marco 做偵查」應呼叫 skill_check；「讓他選閃避或反擊」應依正式流程先用 npc_skill_check 取得攻擊方結果，再用 offer_check_choice 讓玩家選擇並擲骰。
   一般 deterministic dice resolution 現在可以使用 roll_dice，但每次都必須同時提供 purpose 與 roll_context。purpose 是人類可讀的用途文字，說明這顆骰子實際拿來做什麼；roll_context 只能是機器分類 game_resolution 或 ooc_randomizer，不要自創其他值，也不要把 purpose 當成分類。
   如果骰子是在決定傷害、正式隨機效果、已經發生事件的隨機結果，或遊戲世界內需要 authoritative randomness 的結果，使用 roll_context="game_resolution"。例如「碎玻璃割傷 Marco，骰 1d3 傷害」應呼叫 roll_dice，expression="1d3"，purpose="碎玻璃割傷 Marco 的傷害"，roll_context="game_resolution"；成功時會觸發 Dice Creates Canon，整個造成這顆骰子的 KP 主持指示會正式寫入世界歷史。
   如果骰子只是 KP 幕後挑方案、隨機選劇情方向、自己決定要用哪個 NPC 或點子，且不直接構成目前世界事實，使用 roll_context="ooc_randomizer"。例如「我幕後骰 1d6，1–3 用 NPC A，4–6 用 NPC B」應呼叫 roll_dice，expression="1d6"，purpose="幕後決定下一幕使用哪個 NPC"，roll_context="ooc_randomizer"；這顆骰子雖然真的由 deterministic tool 擲出，但不構成遊戲世界事件，不會觸發 Dice Creates Canon，該 KP turn 仍留在 OOC history。
   正式遊戲事件已確定需要擲普通武器傷害時，可以呼叫 roll_weapon_damage，例如「Marco 開槍命中，骰他的 1d8 武器傷害」；這個工具會依角色 deterministic state 套用該角色的 damage bonus。正式規則已確定要計算極限成功／穿刺類傷害時，可以呼叫 roll_impaling_damage，例如「這次攻擊是極限成功，計算穿刺傷害」。
   武器傷害工具只產生 authoritative 傷害結果；若 KP 助手明確裁定已發生固定傷害、環境傷害或持續效果，必須使用 apply_combat_damage 或 add_combat_effect 走正式戰鬥傷害流程，讓系統保存 raw damage、護甲、重傷與 HP 同步結果。
   KP Assistant 仍不能使用 adjust_character、damage_combatant 等泛用 mutation tools 直接覆寫 HP 或用正負 delta 繞過傷害流程。
   當 KP Assistant 成功觸發正式 deterministic check / damage workflow 時，該輪主持指示會成為正式遊戲歷史，而不再只是 OOC 討論。
   這只允許你建立合法檢定／對抗／傷害流程；不得用自然語言或未開放工具直接覆寫已完成骰點、HP、SAN、Luck、彈藥、物品、地圖位置或戰鬥狀態。

8. 回覆 KP 助手時可以使用正常、直接的主持討論語氣，不需要維持對玩家使用的恐怖小說敘事風格，除非 KP 助手明確要求你產生一段要直接呈現給玩家的敘事。

9. KP 助手若要求你「重新回答」、「改成……」、「不要……」、「接下來……」、「這裡應該……」等，應將其視為對你這位 Keeper 的直接主持指令，而不是遊戲世界中的角色言論。

10. 不要自行降低 KP 助手指令的權重，不要把明確指令僅視為可選建議。除非與 authoritative state 衝突，KP 助手的明確指令必須執行。"""


def find_character(state: GroupState, name: str) -> Character | None:
    if not name:
        return None
    characters = state.all_characters()
    exact = next((char for char in characters if char.name == name), None)
    if exact:
        return exact
    norm = name.strip().lower()
    for c in characters:
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


_NPC_INDEX_FUZZY_THRESHOLD = 0.6  # same calibration as app/scene_map.py's room-name fuzzy match


def _find_npc_index_entry(state: GroupState, name: str) -> dict | None:
    """Looks up `name` (whatever the Keeper called this NPC/monster when
    calling add_npc_to_combat) against state.scenario_npc_index — exact match
    against the entry's name or any alias first, then a difflib fuzzy
    fallback (same threshold as scene_map.py's room-name matching) to still
    catch a name that's missing punctuation or a suffix the Keeper dropped
    (e.g. "深潛者頭目" for an entry named "深潛者（成年頭目）"). Returns None
    if scenario_npc_index is empty (nobody's run /coc index) or nothing
    matches closely enough — callers should trust whatever the Keeper passed
    in that case, same as before this existed."""
    if not name:
        return None
    for entry in state.scenario_npc_index:
        candidates = [entry.get("name", "")] + list(entry.get("aliases") or [])
        if name in candidates:
            return entry

    import difflib

    best_entry = None
    best_ratio = 0.0
    for entry in state.scenario_npc_index:
        candidates = [entry.get("name", "")] + list(entry.get("aliases") or [])
        for candidate in candidates:
            if not candidate:
                continue
            ratio = difflib.SequenceMatcher(None, name, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_entry = entry
    return best_entry if best_ratio >= _NPC_INDEX_FUZZY_THRESHOLD else None


def _sync_state_snapshot(target: GroupState, source: GroupState) -> None:
    for field in fields(GroupState):
        setattr(target, field.name, getattr(source, field.name))


def _refresh_state_snapshot(state: GroupState) -> GroupState:
    with locks.get_state_lock(state.group_id):
        latest_state = load_state(state.group_id)
        _sync_state_snapshot(state, latest_state)
    return state


@overload
def _mutate_and_save_state(state: GroupState, mutator: Callable[[GroupState], _StateMutation[_T]]) -> _T: ...
@overload
def _mutate_and_save_state(state: GroupState, mutator: Callable[[GroupState], _T]) -> _T: ...
def _mutate_and_save_state(state: GroupState, mutator: Callable[[GroupState], Any]) -> Any:
    """Small boundary for Keeper tool state mutation.

    Reloads the latest state under the synchronous state lock, mutates/saves it,
    then refreshes the caller's existing state object so later tools in the same
    Keeper turn see the updated snapshot.

    Two call shapes, both handled by the single implementation below (the
    @overload pair above just tells mypy the actual return type each one
    produces, since the plain-_T signature this used to have couldn't express
    that a mutator returning _StateMutation[_T] makes this function return
    _T, not a _StateMutation object — mypy had no way to know the isinstance
    check below unwraps it before returning): `mutator` can return
    _StateMutation(value, should_save) when a tool needs to skip a genuinely
    no-op save (see add_carried_item/remove_carried_item/add_status_tag/
    remove_status_tag above — should_save=False when the item/tag was already
    (not) present), or return its actual value directly when every call
    always needs a save.
    """
    with locks.get_state_lock(state.group_id):
        latest_state = load_state(state.group_id)
        result = mutator(latest_state)
        should_save = True
        if isinstance(result, _StateMutation):
            should_save = result.should_save
            result = result.value
        if should_save:
            save_state(latest_state, reason="tool")
        _sync_state_snapshot(state, latest_state)
    return result


def _commit_turn_result(
    state: GroupState, log_entries: list[dict[str, str]], openai_response_id: str | None = None
) -> None:
    with locks.get_state_lock(state.group_id):
        latest_state = load_state(state.group_id)
        latest_state.log.extend(log_entries)
        if openai_response_id is not None:
            latest_state.openai_previous_response_id = openai_response_id
        save_state(latest_state, reason="turn")
        _sync_state_snapshot(state, latest_state)


def _commit_kp_ooc_turn_result(state: GroupState, message_text: str, final_text: str) -> None:
    """Persist KP Assistant OOC working memory without touching public history.

    Reloads the latest state under the state lock before appending so this
    ephemeral OOC write cannot overwrite deterministic tool updates that may
    have happened earlier in the same Keeper turn.
    """
    with locks.get_state_lock(state.group_id):
        latest_state = load_state(state.group_id)
        latest_state.kp_ooc_log.extend(
            [
                {"role": "kp_assistant", "content": message_text},
                {"role": "assistant", "content": final_text},
            ]
        )
        latest_state.kp_ooc_log = latest_state.kp_ooc_log[-_KP_OOC_LOG_MAX_MESSAGES:]
        save_state(latest_state, reason="kp_ooc")
        _sync_state_snapshot(state, latest_state)


def _kp_tool_result_creates_canon(tool_name: str, tool_input: dict, result: dict) -> bool:
    if result.get("ok") is not True:
        return False
    if tool_name in _KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES:
        return True
    if tool_name == "roll_dice":
        return tool_input.get("roll_context") == "game_resolution"
    return False


def _validate_kp_roll_dice_context(tool_input: dict) -> str | None:
    if tool_input.get("roll_context") in ("game_resolution", "ooc_randomizer"):
        return None
    return 'KP Assistant 使用 roll_dice 時必須明確指定 roll_context 為 "game_resolution" 或 "ooc_randomizer"。'


def _ensure_auto_combat_checkpoint(state: GroupState) -> None:
    if state.combat.active:
        return
    checkpoints.create_checkpoint(
        state,
        label="開戰前",
        created_by="system",
        reason="auto_combat_start",
        event_id=f"combat-start:{state.group_id}:{state.state_revision}",
    )


def _filter_public_combat_damage_result(result: dict, speaker_role: str) -> dict:
    if speaker_role == "kp_assistant" or result.get("side") != "enemy":
        return result
    public_keys = {
        "ok",
        "name",
        "target",
        "target_id",
        "side",
        "damage_type",
        "final_damage",
        "major_wound_triggered",
        "defeated",
        "public_summary",
        "effect_id",
    }
    return {key: result[key] for key in public_keys if key in result}


def _persist_memory_maintenance_state(
    group_id: str, campaign_summary: str, dropped_chunk: list[dict[str, str]]
) -> None:
    with locks.get_state_lock(group_id):
        latest_state = load_state(group_id)
        # Only apply anything if the front of the freshly-reloaded log still
        # matches what was actually dropped — guards against e.g. a
        # concurrent /coc newgame reset, or another maintenance pass having
        # already trimmed this exact chunk. On a mismatch, skip BOTH the log
        # trim and the campaign_summary update (not just the trim): the
        # summary was derived from `dropped_chunk`, which no longer reflects
        # what's actually at the front of the current log, so applying it
        # anyway would bleed a stale/unrelated summary into whatever state
        # is live now (e.g. a brand-new campaign after /coc newgame
        # inheriting leftover summary text from the campaign it replaced).
        # Skipping entirely costs nothing but retrying this trim on a later
        # turn — never a correctness problem, and never a partial write.
        n = len(dropped_chunk)
        if latest_state.log[:n] == dropped_chunk:
            latest_state.log = latest_state.log[n:]
            latest_state.campaign_summary = campaign_summary
            save_state(latest_state, reason="maintenance")


# Guards against more than one run_post_turn_maintenance pass running
# concurrently for the same group_id — see that function's own docstring.
_maintenance_in_flight: set[str] = set()


def run_scene_digest_maintenance(group_id: str) -> None:
    with locks.get_state_lock(group_id):
        state = load_state(group_id)
        latest = scene_digest.latest_digest(group_id, state.timeline_id)
        chapter_changed = latest is None or latest.get("scene_label") != (state.active_chapter_id or state.scenario_title or "目前場景")
        current_log_length = len(state.log)
        previous_log_length = latest.get("log_length", 0) if latest else 0
        # Log maintenance can intentionally shrink the in-memory log. Treat
        # that as a new baseline; otherwise the old larger watermark would
        # make this subtraction negative and periodic digests would stop.
        log_was_trimmed = latest is not None and current_log_length < previous_log_length
        log_interval_reached = (
            latest is None
            or log_was_trimmed
            or current_log_length - previous_log_length >= SCENE_DIGEST_TURN_INTERVAL
        )
        if not (chapter_changed or log_interval_reached):
            return
        scene_digest.create_digest(state)


def run_post_turn_maintenance(group_id: str) -> None:
    """Called after every turn (see app/commands.py's
    _spawn_post_turn_maintenance, which now fires this as an independent
    background task rather than awaiting it inline). Only does real work
    once the log actually crosses the trim threshold — every other call is a
    cheap no-op. `_maintenance_in_flight` skips a call outright if a pass is
    already running for this group_id: without it, several turns landing
    back-to-back while the log is still above threshold would each spawn
    their own full pass (duplicate LLM summarization + embedding API costs),
    racing on the same log/memory-chunk data — memory_rag.append_memory in
    particular does its own unlocked read-modify-write and is only ever
    called from here, so serializing calls to this function is what actually
    keeps two of its calls from stepping on each other, not any locking
    inside append_memory itself.

    The check-then-add on `_maintenance_in_flight` below is itself wrapped in
    `locks.get_state_lock(group_id)` — this function runs via
    `asyncio.to_thread` (see _spawn_post_turn_maintenance), i.e. on real OS
    worker threads, not just concurrent asyncio tasks, so the GIL making each
    individual `in`/`.add()` call atomic does NOT make the pair atomic: two
    threads could otherwise both observe `group_id not in
    _maintenance_in_flight` before either adds it, both proceed, and run two
    overlapping passes anyway — exactly the failure mode this guard exists
    to prevent."""
    with locks.get_state_lock(group_id):
        if group_id in _maintenance_in_flight:
            return
        _maintenance_in_flight.add(group_id)
    try:
        run_scene_digest_maintenance(group_id)
        with locks.get_state_lock(group_id):
            latest_state = load_state(group_id)
            if len(latest_state.log) <= MAX_LOG_TURNS * 4:
                return
            keep_from = -MAX_LOG_TURNS * 2
            base_summary = latest_state.campaign_summary
            log_snapshot = [dict(message) for message in latest_state.log]
            dropped_chunk = log_snapshot[:keep_from]

        # Rolling summarization (see summarize_log_chunk above): fold the
        # chunk about to be dropped into campaign_summary *before* dropping
        # it, instead of just discarding it — this is the one rare turn every
        # ~MAX_LOG_TURNS*2 turns that pays for an extra (cheap) LLM call, so
        # early plot points survive past what the verbatim log can hold.
        campaign_summary = summarize_log_chunk(base_summary, dropped_chunk)
        # Also persist the chunk's *original* wording into the searchable
        # memory index (app/memory_rag.py) — campaign_summary alone would
        # keep recompressing an already-compressed summary on every future
        # trim, eroding fine detail a little more each pass; this keeps the
        # verbatim text retrievable via search_memory even after that.
        formatted_chunk = "\n".join(f"{m['role']}: {m['content']}" for m in dropped_chunk)
        memory_rag.append_memory(group_id, formatted_chunk)
        _persist_memory_maintenance_state(group_id, campaign_summary, dropped_chunk)
    finally:
        with locks.get_state_lock(group_id):
            _maintenance_in_flight.discard(group_id)


def _execute_tool(
    state: GroupState,
    name: str,
    tool_input: dict,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
    speaker_role: str = "player",
) -> dict:
    try:
        if speaker_role == "kp_assistant" and name == "roll_dice":
            error = _validate_kp_roll_dice_context(tool_input)
            if error:
                return {"ok": False, "error": error}

        if speaker_role == "kp_assistant" and name not in _KP_ASSISTANT_ALLOWED_TOOL_NAMES:
            return {
                "ok": False,
                "error": "KP Assistant turn 只能使用已允許的查詢與主持流程工具，不能直接修改角色 deterministic state 或執行尚未開放的 administrative mutation。",
            }

        if name == "roll_dice":
            roll_result = dice.roll_expression(tool_input["expression"])
            return {
                "ok": True, "expression": roll_result.expression, "rolls": roll_result.rolls,
                "modifier": roll_result.modifier, "total": roll_result.total,
            }

        if name == "roll_impaling_damage":
            try:
                impale_result = dice.calculate_impaling_damage(
                    tool_input["weapon_damage"], tool_input.get("damage_bonus") or "0", bool(tool_input.get("impaling"))
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {
                "ok": True,
                "total": impale_result.total,
                "max_weapon_damage": impale_result.max_weapon_damage,
                "max_damage_bonus": impale_result.max_damage_bonus,
                "impaling": impale_result.impaling,
                "reroll_total": impale_result.reroll.total if impale_result.reroll else None,
                "describe": impale_result.describe(),
            }

        if name == "roll_weapon_damage":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            try:
                weapon_result = dice.roll_weapon_damage(tool_input["weapon_damage"], char.damage_bonus)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {
                "ok": True,
                "investigator": char.name,
                "weapon_damage_roll": weapon_result.weapon_roll.total,
                "damage_bonus": char.damage_bonus,
                "damage_bonus_roll": weapon_result.damage_bonus_total,
                "total": weapon_result.total,
                "describe": weapon_result.describe(),
            }

        if name == "skill_check":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            def _register_pending_skill_check(target_state: GroupState) -> tuple[int, int, int, str]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                value = resolve_skill_value(target_char, tool_input["skill"])
                bonus = int(tool_input.get("bonus_dice") or 0)
                penalty = int(tool_input.get("penalty_dice") or 0)
                difficulty = tool_input.get("difficulty") or "regular"
                if difficulty not in ("regular", "hard", "extreme"):
                    difficulty = "regular"
                target_state.pending_checks[target_char.owner_id] = {
                    "type": "skill", "skill": tool_input["skill"], "skill_value": value,
                    "bonus_dice": bonus, "penalty_dice": penalty, "difficulty": difficulty,
                    "pushed": bool(tool_input.get("pushed", False)),
                }
                return value, bonus, penalty, difficulty
            value, bonus, penalty, difficulty = _mutate_and_save_state(state, _register_pending_skill_check)
            refreshed_char = find_character(state, tool_input.get("investigator", ""))
            return {
                "ok": True, "pending": True, "investigator": refreshed_char.name, "skill": tool_input["skill"],
                "skill_value": value, "bonus_dice": bonus, "penalty_dice": penalty, "difficulty": difficulty,
                "note": "還沒有骰出結果，等玩家自己用 /coc check 擲骰後才會有真正的成敗——不要自己編一個。",
            }

        if name == "offer_check_choice":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            raw_options = tool_input.get("options") or []
            if len(raw_options) < 2:
                return {"ok": False, "error": "options 至少要給兩個選項，只有一個的話請直接用 skill_check"}
            attacker_tier = tool_input.get("attacker_tier")
            def _register_pending_choice(target_state: GroupState) -> list[dict]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                options = []
                for opt in raw_options:
                    # Full skill value, no artificial difficulty adjustment —
                    # confirmed against the official COC7e Fight Back text:
                    # it's a normal opposed roll at the defender's own combat
                    # skill, not a harder version of Dodge. (A prior revision
                    # here halved it as a house-rule approximation; reverted.)
                    value = resolve_skill_value(target_char, opt["skill"])
                    options.append({
                        "label": opt["label"], "skill": opt["skill"], "skill_value": value,
                        "bonus_dice": int(opt.get("bonus_dice") or 0), "penalty_dice": int(opt.get("penalty_dice") or 0),
                    })
                pending_choice = {"type": "choice", "options": options}
                if attacker_tier:
                    pending_choice["attacker_tier"] = attacker_tier
                target_state.pending_checks[target_char.owner_id] = pending_choice
                return options
            options = _mutate_and_save_state(state, _register_pending_choice)
            refreshed_char = find_character(state, tool_input.get("investigator", ""))
            return {
                "ok": True, "pending": True, "investigator": refreshed_char.name, "options": options,
                "note": "還沒有骰出結果，等玩家自己選一個選項、用 /coc check <選項名稱> 擲骰後才會有結果——不要自己選、不要自己編一個。",
            }

        if name == "npc_skill_check":
            skill_value = max(0, min(100, int(tool_input["skill_value"])))
            bonus = int(tool_input.get("bonus_dice") or 0)
            penalty = int(tool_input.get("penalty_dice") or 0)
            npc_roll = dice.skill_check(skill_value, bonus_dice=bonus, penalty_dice=penalty)
            return {"ok": True, "roll": npc_roll.roll, "tier": npc_roll.tier, "skill_value": skill_value}

        if name == "sanity_check":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            loss_success = tool_input.get("loss_success", "0")
            loss_failure = tool_input.get("loss_failure", "1d4")
            def _register_pending_sanity(target_state: GroupState) -> None:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                target_state.pending_checks[target_char.owner_id] = {
                    "type": "sanity", "loss_success": loss_success, "loss_failure": loss_failure,
                }
            _mutate_and_save_state(state, _register_pending_sanity)
            refreshed_char = find_character(state, tool_input.get("investigator", ""))
            return {
                "ok": True, "pending": True, "investigator": refreshed_char.name, "current_san": refreshed_char.san,
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

            def _apply_attribute_delta(target_state: GroupState) -> tuple[int, bool]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                target_cap = getattr(target_char, max_attr) if max_attr else 999
                delta = int(tool_input["delta"])
                new_val = max(0, min(target_cap, getattr(target_char, cur_attr) + delta))
                setattr(target_char, cur_attr, new_val)

                major_wound = False
                # COC7e major wound rule, code-enforced the same way Bout of
                # Madness is (see sanity_check above): a single hit dealing >=
                # half of max HP knocks the investigator unconscious unless they
                # pass a CON roll. Skipped when this hit already dropped HP to
                # 0 or below — RAW already treats that as unconscious/dying on
                # its own, so a second CON check on top would be redundant.
                if field_name == "hp" and delta < 0 and new_val > 0 and -delta >= target_char.hp_max / 2:
                    major_wound = True
                    target_state.pending_checks[target_char.owner_id] = {
                        "type": "skill", "skill": "CON", "skill_value": resolve_skill_value(target_char, "CON"),
                        "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
                        "major_wound_trigger": True,
                    }
                return new_val, major_wound

            new_val, major_wound = _mutate_and_save_state(state, _apply_attribute_delta)
            refreshed_char = find_character(state, tool_input.get("investigator", ""))
            response = {"ok": True, "investigator": refreshed_char.name, "field": field_name, "value": new_val}
            if major_wound:
                response["major_wound"] = True
                response["note"] = (
                    "這次單一傷害達到重傷門檻（≥ 角色最大 HP 一半），COC7e 規則：角色必須做一次 CON 檢定，"
                    "失敗會當場昏迷倒地——系統已經幫玩家註冊這次 CON 檢定，不用你自己判斷結果，"
                    "先描述受到重擊當下的衝擊就好（不要講有沒有昏過去），等玩家輸入 /coc check CON 才知道結果。"
                )
            return response

        if name == "adjust_ammo":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            weapon = tool_input.get("weapon", "")
            entry = char.weapons.get(weapon)
            if entry is None:
                available = "、".join(char.weapons.keys()) or "（沒有登記彈藥的槍械）"
                return {"ok": False, "error": f"「{char.name}」的彈藥欄位裡沒有「{weapon}」，目前有：{available}"}
            if "ammo_max" not in entry:
                # A recognized weapon whose ammo isn't tracked (melee, or an
                # ammo category this project's table doesn't cover) — see
                # pregen_extractor._resolve_weapon_ammo, which stores these as
                # {}. Without this check, `entry["ammo_max"]` below would
                # KeyError instead of giving the Keeper a usable error.
                return {"ok": False, "error": f"「{weapon}」沒有追蹤彈藥數（近戰武器或未登記彈藥表的槍械），不需要（也無法）裝填。"}
            def _apply_ammo_change(target_state: GroupState) -> None:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                target_entry = target_char.weapons.get(weapon)
                if tool_input.get("reload_full"):
                    target_entry["ammo"] = target_entry["ammo_max"]
                else:
                    target_entry["ammo"] = max(0, min(target_entry["ammo_max"], target_entry["ammo"] + int(tool_input.get("delta") or 0)))
            _mutate_and_save_state(state, _apply_ammo_change)
            refreshed_char = find_character(state, tool_input.get("investigator", ""))
            refreshed_entry = refreshed_char.weapons.get(weapon)
            return {"ok": True, "investigator": refreshed_char.name, "weapon": weapon, "ammo": refreshed_entry["ammo"], "ammo_max": refreshed_entry["ammo_max"]}

        if name == "add_carried_item":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            item = tool_input.get("item", "").strip()
            if not item:
                return {"ok": False, "error": "item 不能是空字串"}
            def _mutate_add_item(target_state: GroupState) -> _StateMutation[tuple[str, list[str]]]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                changed = item not in target_char.carried_items
                if changed:
                    target_char.carried_items.append(item)
                return _StateMutation((target_char.name, target_char.carried_items), should_save=changed)
            investigator, carried_items = _mutate_and_save_state(state, _mutate_add_item)
            return {"ok": True, "investigator": investigator, "carried_items": carried_items}

        if name == "remove_carried_item":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            # .strip() to match add_carried_item's own normalization above — otherwise
            # an item with incidental whitespace ("鑰匙 " vs "鑰匙") would silently fail
            # to remove (the no-op-skip logic below would report "unchanged" since the
            # stripped, stored string never string-equals the unstripped one being removed).
            item = tool_input.get("item", "").strip()
            def _mutate_remove_item(target_state: GroupState) -> _StateMutation[tuple[str, list[str]]]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                changed = item in target_char.carried_items
                if changed:
                    target_char.carried_items.remove(item)
                    target_state.consumed_or_removed_items.append({
                        "item": item,
                        "character_id": target_char.owner_id,
                        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "source_event_id": tool_input.get("source_event_id") or uuid4().hex,
                    })
                return _StateMutation((target_char.name, target_char.carried_items), should_save=changed)
            investigator, carried_items = _mutate_and_save_state(state, _mutate_remove_item)
            return {"ok": True, "investigator": investigator, "carried_items": carried_items}

        if name in ("record_established_fact", "record_clue"):
            field_name = "established_facts" if name == "record_established_fact" else "known_clues"
            text_value = ((tool_input.get("fact") if name == "record_established_fact" else tool_input.get("clue")) or "").strip()
            if not text_value:
                return {"ok": False, "error": "內容不能是空字串"}
            visibility = tool_input.get("visibility", "public")
            if visibility not in ("public", "kp_only"):
                return {"ok": False, "error": "visibility 必須是 public 或 kp_only"}
            def _mutate_record(target_state: GroupState) -> _StateMutation[dict]:
                records = getattr(target_state, field_name)
                if any(record.get("text") == text_value and record.get("visibility", "public") == visibility for record in records):
                    return _StateMutation({"recorded": False, "records": records}, should_save=False)
                record = {
                    "text": text_value,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source_event_id": tool_input.get("source_event_id") or uuid4().hex,
                    "visibility": visibility,
                    "scene_id": "",
                }
                records.append(record)
                return _StateMutation({"recorded": True, "record": record}, should_save=True)
            result = _mutate_and_save_state(state, _mutate_record)
            return {"ok": True, **result}

        if name == "add_status_tag":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            tag = tool_input.get("tag", "").strip()
            if not tag:
                return {"ok": False, "error": "tag 不能是空字串"}
            def _mutate_add_tag(target_state: GroupState) -> _StateMutation[tuple[str, list[str]]]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                changed = tag not in target_char.status_tags
                if changed:
                    target_char.status_tags.append(tag)
                return _StateMutation((target_char.name, target_char.status_tags), should_save=changed)
            investigator, tags = _mutate_and_save_state(state, _mutate_add_tag)
            return {"ok": True, "investigator": investigator, "status_tags": tags}

        if name == "remove_status_tag":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            # .strip() to match add_status_tag's own normalization above — otherwise a
            # tag with incidental whitespace ("昏迷 " vs "昏迷") would silently fail to
            # remove (the no-op-skip logic below would report "unchanged" since the
            # stripped, stored string never string-equals the unstripped one being removed).
            tag = tool_input.get("tag", "").strip()
            def _mutate_remove_tag(target_state: GroupState) -> _StateMutation[tuple[str, list[str]]]:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                changed = tag in target_char.status_tags
                if changed:
                    target_char.status_tags.remove(tag)
                return _StateMutation((target_char.name, target_char.status_tags), should_save=changed)
            investigator, tags = _mutate_and_save_state(state, _mutate_remove_tag)
            return {"ok": True, "investigator": investigator, "status_tags": tags}

        if name == "set_skill":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            value = max(0, min(100, int(tool_input["value"])))
            def _mutate_set_skill(target_state: GroupState) -> None:
                target_char = find_character(target_state, tool_input.get("investigator", ""))
                target_char.skills[tool_input["skill"]] = value
            _mutate_and_save_state(state, _mutate_set_skill)
            refreshed_char = find_character(state, tool_input.get("investigator", ""))
            return {"ok": True, "investigator": refreshed_char.name, "skill": tool_input["skill"], "value": value}

        if name == "get_character_sheet":
            _refresh_state_snapshot(state)
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            return {"ok": True, "sheet": char.to_dict()}

        if name == "start_combat":
            def _mutate_start_combat(target_state: GroupState) -> None:
                _ensure_auto_combat_checkpoint(target_state)
                combat.start_combat(target_state)
            _mutate_and_save_state(state, _mutate_start_combat)
            return {"ok": True, "status": combat.status_text(state)}

        if name == "add_npc_to_combat":
            npc_name = tool_input["name"]
            requested_hp = int(tool_input.get("hp", 10))
            def _mutate_add_npc(target_state: GroupState) -> str:
                _ensure_auto_combat_checkpoint(target_state)
                hp = requested_hp
                index_note = ""
                # Code-enforced consistency check, not just a prompt-level ask: if
                # this name matches a /coc index entry, the index's HP wins no
                # matter what the Keeper actually passed — this is what stops the
                # same monster (or the same life stage of one) from silently
                # getting a different HP in a later scene, instead of relying
                # purely on the Keeper remembering to look it up itself.
                index_entry = _find_npc_index_entry(target_state, npc_name)
                if index_entry is not None and isinstance(index_entry.get("hp"), (int, float)):
                    canonical_hp = int(index_entry["hp"])
                    if canonical_hp != hp:
                        index_note = (
                            f"（系統已依 /coc index 索引修正：你傳入的 HP {hp} 跟索引裡「{index_entry.get('name')}」"
                            f"登記的 HP {canonical_hp} 不一致，已強制改用索引值。這隻的數值以索引為準，"
                            "之後同一隻不要再用別的數字。）"
                        )
                        hp = canonical_hp
                combat.add_npc(
                    target_state,
                    npc_name,
                    int(tool_input.get("dex", 50)),
                    hp,
                    is_ally=bool(tool_input.get("is_ally", False)),
                    armor=tool_input.get("armor"),
                    attacks=tool_input.get("attacks"),
                    abilities=tool_input.get("abilities"),
                )
                return index_note
            index_note = _mutate_and_save_state(state, _mutate_add_npc)
            response = {"ok": True, "status": combat.status_text(state)}
            if index_note:
                response["note"] = index_note
            return response

        if name == "get_combat_status":
            _refresh_state_snapshot(state)
            return {"ok": True, "status": combat.status_text(state, include_private=(speaker_role == "kp_assistant"))}

        if name == "advance_combat_turn":
            def _mutate_advance_turn(target_state: GroupState) -> dict:
                return combat.advance_turn(target_state)
            return _mutate_and_save_state(state, _mutate_advance_turn)

        if name == "damage_combatant":
            def _mutate_damage_combatant(target_state: GroupState) -> dict:
                return combat.damage_combatant(target_state, tool_input["name"], int(tool_input["delta"]))
            result = _mutate_and_save_state(state, _mutate_damage_combatant)
            return _filter_public_combat_damage_result(result, speaker_role)

        if name == "plan_enemy_turn":
            def _mutate_plan_enemy_turn(target_state: GroupState) -> dict:
                return combat.plan_enemy_turn(target_state, tool_input.get("enemy", ""))
            return _mutate_and_save_state(state, _mutate_plan_enemy_turn)

        if name == "resolve_enemy_action":
            def _mutate_resolve_enemy_action(target_state: GroupState) -> dict:
                return combat.resolve_enemy_action(
                    target_state,
                    tool_input["plan_id"],
                    outcome=tool_input.get("outcome"),
                )
            return _mutate_and_save_state(state, _mutate_resolve_enemy_action)

        if name == "apply_combat_damage":
            def _mutate_apply_combat_damage(target_state: GroupState) -> dict:
                return combat.apply_combat_damage(
                    target_state,
                    tool_input["target"],
                    int(tool_input["raw_damage"]),
                    damage_type=tool_input.get("damage_type", "physical"),
                    tags=tool_input.get("tags") or [],
                    source_id=tool_input.get("source_id", ""),
                )
            result = _mutate_and_save_state(state, _mutate_apply_combat_damage)
            return _filter_public_combat_damage_result(result, speaker_role)

        if name == "add_combat_effect":
            def _mutate_add_combat_effect(target_state: GroupState) -> dict:
                return combat.add_combat_effect(
                    target_state,
                    tool_input["target"],
                    tool_input["label"],
                    timing=tool_input.get("timing", "turn_start"),
                    damage=tool_input.get("damage", ""),
                    damage_type=tool_input.get("damage_type", "physical"),
                    remaining_rounds=tool_input.get("remaining_rounds"),
                    tags=tool_input.get("tags") or [],
                    source_id=tool_input.get("source_id", ""),
                    public_description=tool_input.get("public_description", ""),
                )
            return _mutate_and_save_state(state, _mutate_add_combat_effect)

        if name == "end_combat":
            def _mutate_end_combat(target_state: GroupState) -> None:
                combat.end_combat(target_state)
            _mutate_and_save_state(state, _mutate_end_combat)
            return {"ok": True}

        if name == "send_private_info":
            char = find_character(state, tool_input.get("investigator", ""))
            if not char:
                return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
            private_messages.append((char.owner_id, tool_input["message"]))
            return {"ok": True, "delivered_to": char.name}

        if name == "search_scenario_images":
            if not state.scenario_library_id:
                return {"ok": False, "error": "目前沒有選擇劇本庫項目"}
            assets = scenario_library.search_images(
                state.scenario_library_id,
                query=tool_input.get("query", ""),
                image_type=tool_input.get("image_type", ""),
                allowed_chapter_ids=set(state.context_chapter_ids),
            )
            # KP-only assets (see scenario_library._build_image_assets — currently
            # character_sheet pages, which may be NPC/villain stat blocks or a
            # pregen revealing a "secret" connection) are filtered out of what
            # ordinary play (speaker_role != "kp_assistant") can even discover,
            # not just what it can display — a player-facing search shouldn't
            # surface a KP-only page's existence any more than show_scenario_image
            # below should let them actually pull it up.
            if speaker_role != "kp_assistant":
                assets = [a for a in assets if a.get("visibility", "public") == "public"]
            return {"ok": True, "assets": [{key: asset.get(key) for key in ("id", "page", "type", "tags", "description", "visibility")} for asset in assets]}

        if name == "show_scenario_image":
            if not state.scenario_library_id:
                return {"ok": False, "error": "目前沒有選擇劇本庫項目"}
            page = int(tool_input["page_number"])
            assets = scenario_library.search_images(
                state.scenario_library_id, allowed_chapter_ids=set(state.context_chapter_ids)
            )
            asset = next((item for item in assets if item.get("page") == page), None)
            if asset is None:
                return {"ok": False, "error": "該圖片不在目前章節 Context，不能展示"}
            if asset.get("visibility", "public") != "public" and speaker_role != "kp_assistant":
                return {"ok": False, "error": "這一頁是 KP 專用資料，不能在一般遊戲流程中展示給玩家"}
            investigator = tool_input.get("investigator")
            owner_id = None
            if investigator:
                char = find_character(state, investigator)
                if not char:
                    return {"ok": False, "error": f"找不到角色「{investigator}」"}
                owner_id = char.owner_id
            image_requests.append((owner_id, page))
            return {"ok": True, "page": page, "asset_type": asset.get("type"), "target": "private" if owner_id else "public"}

        if name == "advance_scenario_chapter":
            def _advance(target_state: GroupState) -> dict:
                if not target_state.scenario_library_id:
                    return {"ok": False, "error": "目前沒有選擇劇本庫項目"}
                next_id = scenario_library.next_chapter_id(target_state.scenario_library_id, target_state.active_chapter_id)
                if next_id is None:
                    return {"ok": False, "error": "目前已是最後一個章節"}
                context = scenario_library.load_context(target_state.scenario_library_id, next_id)
                target_state.scenario_text = context["text"]
                target_state.active_chapter_id = context["active_chapter_id"]
                target_state.context_chapter_ids = context["context_chapter_ids"]
                target_state.scenario_npc_index = context["indexes"].get("npcs", [])
                target_state.scenario_location_index = context["indexes"].get("locations", [])
                target_state.scene_maps = context["scene_maps"]
                target_state.openai_previous_response_id = ""
                clear_page_images(target_state.group_id)
                scenario_library.copy_context_images(
                    target_state.scenario_library_id, context["page_numbers"],
                    lambda page, image: save_page_image(target_state.group_id, page, image),
                )
                return {"ok": True, "active_chapter_id": context["active_chapter_id"], "context_chapter_ids": context["context_chapter_ids"]}
            return _mutate_and_save_state(state, _advance)
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

    static_chars_text = "\n\n".join(c.static_sheet_text() for c in state.active_characters()) or "（目前尚無登記角色）"

    # NPC/monster + location canonical index (see app/scenario_index.py, built
    # on demand via /coc index) — empty until someone runs that command, in
    # which case this whole block disappears and behavior is exactly what it
    # was before this existed. When present, this is the authoritative source
    # for HP/stats of anything listed here, specifically to stop the Keeper
    # from re-deriving (and drifting on) the same NPC/monster's numbers every
    # time it comes up — e.g. quoting a different HP for the same creature in
    # two different scenes, or conflating a monster's different life
    # stages/individuals (a young specimen vs. a mature one) into one entry.
    index_block = ""
    npc_index_text = scenario_index.format_npc_index_block(state.scenario_npc_index)
    location_index_text = scenario_index.format_location_index_block(state.scenario_location_index)
    if npc_index_text or location_index_text:
        index_block = "\n\n# 劇本索引（由 /coc index 抽取，僅列出劇本明確寫出的數值——這是唯一正確來源）"
        if npc_index_text:
            index_block += f"""
## NPC／怪物（務必使用下面列出的數值；同一隻怪物/NPC 全場只能有一組數值，不能因為多次提到就講出不同的 HP。如果同一種生物有多個型態或個體（幼體/成年、雜兵/頭目……），下面會分開列成不同條目——先確認清楚眼前這隻是哪一條目，再照那個條目的數值呼叫 add_npc_to_combat，不要混用不同條目的數字，也不要自己另外編一個。）
{npc_index_text}"""
        if location_index_text:
            index_block += f"""
## 主要地點
{location_index_text}"""
    summary_block = ""
    if state.campaign_summary:
        summary_block = f"""

# 先前劇情摘要（更早之前的對話已經被裁掉，這是那些內容的精簡摘要，記得參考，不要當作沒發生過）
{state.campaign_summary}
如果玩家問起一個具體的人名/地名/物品，這份摘要跟最近的對話都找不到（摘要是壓縮過的，可能已經漏掉細節），
呼叫 search_memory 工具去查更早、還沒被壓縮掉的原始對話內容，不要直接說忘記了或自己編一個答案。"""
    persona_block = state.keeper_persona.strip() or DEFAULT_PERSONA
    return f"""你是一位主持《克蘇魯的呼喚》第七版（Call of Cthulhu 7th Edition）跑團的守密人（Keeper），正在群組聊天室（LINE 或 Discord）中透過文字對話主持一場遊戲。

# 行為準則
{persona_block}

# 敘事節奏紀律
- 一次回覆只推進「一個場景片段」：給出一個具體的反應點就停下來，不要在同一則回覆裡串連多個場景、多個發現、或多輪 NPC 對話。如果發現自己寫到第三段還沒停，代表該收了，把剩下的留到玩家回應之後。
- 開場景介紹、或玩家明確要求整理/回顧時可以例外寫長一點，平常的一來一往不要。
- 同時有多位玩家角色在場時，不要每次回覆都讓所有人一起反應。聚焦在情境自然指向的那一位角色身上，用一句話點名他、停在那裡等他回應（例如「槍口正對著小明——小明，你怎麼辦？」），下一輪再換人，不要一次幫全部人做完決定。
- 主動掌握節奏和張力，不要等玩家問「現在是什麼氣氛」或「該做什麼」才反應——每個片段私下想清楚目前壓在調查員身上的威脅、時限或壓力是什麼，並在敘述裡自然帶出最急迫的那一個，主動用劇情、NPC 的意圖、環境變化把玩家推回劇本主線，而不是被動跟著離題閒聊漂走。但也要老實收掉已經沒意義的張力（例如陷阱已經觸發過的催眠效果），不要為了維持氣氛硬拖。

# 文風
- 用流暢的敘事散文寫場景，把擲骰結果和判定自然編織進句子裡（例如「你屏息潛行，腳步聲被雨聲蓋過——潛行檢定成功」），不要把骰子結果或數值單獨列成一行、條列項目或標籤格式（像是「【檢定結果】」這種）。
- 描述行動或檢定的後續發展時，優先用五感細節（看到什麼、聽到什麼、聞到什麼、觸感、體感反應）具體呈現當下發生了什麼，而不是直接丟出「你成功了」「你失敗了」這種抽象判定字眼——讓玩家從場景細節裡自己讀出結果，比直接宣告結果更有壓迫感、也更符合冷酷旁觀者的口吻。
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
- **難度等級（COC7e 規則，不是憑感覺套用，每次呼叫 skill_check 前都要想一下這條）**：`skill_check` 的
  `difficulty` 參數決定這次判定的門檻，依 RAW 規則判斷——對抗的技能/屬性低於 50、或任務標準時不用填
  （等同 `'regular'`）；對抗的技能/屬性達到 50 以上、或這件事本來就非常困難時設 `'hard'`；對抗的
  技能/屬性達到 90 以上、或幾乎是人類極限時設 `'extreme'`。**只要劇本或你自己敘述裡明確給過對手/
  障礙的技能數字，一律照這個數字判斷，不要漏掉**——例如劇本寫「這名殺手潛行 80%」，玩家要偵查/聆聽
  察覺他時，因為 80 落在 50-89 之間，這次 skill_check 就必須帶 `difficulty='hard'`；如果數字是 92，
  就要帶 `'extreme'`；劇本沒給數字、只是「一般的路人」「普通的鎖」這種標準任務，才維持不填。
  設了之後，玩家這次一定要擲到那個等級（含）以上才算過，只達到較低的等級一律算失敗，系統會自動
  判定、也會正確告訴玩家「有達到某個成功等級，但這次判定門檻更高」。**不要用 bonus_dice/penalty_dice
  去模擬任務難度**——那是角色這次手氣好壞、環境優劣（照明差、匆忙、有人幫忙等），是完全不同的機制，
  兩者可以同時存在（例如「對抗一個技能 70% 的高手，而且你這次很匆忙」就是 `difficulty='hard'` 加上
  `penalty_dice=1`）。

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
- **角色用武器攻擊、命中對方時的傷害**：一般（非極限成功）命中呼叫 roll_weapon_damage（給角色名稱
  跟武器傷害骰，系統會自動查角色的傷害加值 DB 加進去，不用你自己拼骰子表示式或手動加總——
  `roll_dice` 沒辦法解析「武器骰+DB骰」這種混合表示式，硬湊字串只會失敗或算錯）；如果這次攻擊的
  **攻擊擲骰**是極限成功（不是反擊），改呼叫 roll_impaling_damage，讓系統照 COC7e 規則正確算出
  「武器＋傷害加值都算最大值，穿刺武器再額外重骰一次武器傷害」的結果。不是武器傷害的一般描述性
  擲骰（道具檢定、環境傷害等）才用 roll_dice。
- 當敘事中出現「打起來了」的場面（攻擊、被攻擊、追逐戰鬥等），呼叫 start_combat 開始正式戰鬥、用 add_npc_to_combat 加入敵人，進入戰鬥規則的流程（見下方「目前戰鬥狀態」區塊）；小規模、沒有生命危險的推擠拉扯不需要進入正式戰鬥。加入敵人時，若劇本寫了護甲、攻擊、特殊能力、每輪/每戰使用限制或觸發條件，必須放進 add_npc_to_combat 的 armor/attacks/abilities；不要只填 HP 後靠臨場記憶。
- 劇本內容裡如果有些頁面明顯是圖片內容（地圖、平面圖、手卡——這些頁面的文字通常是「[圖片內容描述：...]」或類似的視覺描述，而不是一般敘述文字），當玩家實際看到／拿到那個東西時，呼叫 show_scenario_image 把那一頁的實際圖片秀出來，比純文字描述更清楚；只有特定人該看到的手卡記得帶 investigator 參數只給那個人看。
- 拿到工具結果後，用生動的敘述把結果包裝成故事講給玩家聽，而不是直接報數字；但可以自然帶出結果（例如「你腳下一滑，重重摔在地上，失去了 3 點理智」）。
- 如果玩家的行動目標不明確，用一兩句話追問，而不是自己幫他們決定要做什麼。
- 角色 HP 降到 0 時描述瀕死或死亡過程；SAN 降到 0 時描述永久性失常的下場。
- COC7e 重傷規則：如果 adjust_character 扣血後回傳結果裡有 `major_wound`，系統已經自動幫玩家註冊一次
  CON 檢定（判斷會不會當場昏迷），不用你自己另外呼叫任何工具、也不用你自己判斷有沒有觸發——先描述
  受到這次重擊當下的直接衝擊就好，還不知道會不會昏過去，等玩家自己用 /coc check CON 擲骰、結果出來
  之後你才會收到確定的成敗，照那個結果接續敘事即可，不要自己先講角色昏倒了或撐住了。
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

# 攜帶物合理性審查
- 這是一致性與代入感的審查，不是記帳——只審查**貴重／稀有／管制或違法／跟戰鬥相關**的物品；角色生活水準內的日常小物
  （筆記本、小刀、火柴、一般衣物、零錢）一律直接放行，不要為了瑣碎小事就搬出下面這套規則變成規則說教。
- 落在審查範圍內的物品，用下面四項檢查：(1) **年代／科技**——這個時代/地區真的買得到嗎（1920 年代劇本不該有半自動
  武器、無線電、抗生素這類還沒發明或還不普及的東西）；(2) **來源**——角色的職業、背景、執照，或先前劇情要能解釋
  他為什麼有這個東西（醫生帶醫藥包合理，一般職員突然有一把衝鋒槍不合理）；(3) **負擔能力**——大致對照角色的
  「信用評級」技能值判斷買不買得起，不用真的記帳算現金；(4) **合法性／地域**——管制或違法物品需要合法來源、
  黑市門路，或劇本設定的地點真的買得到。四項都過才允許；有一項不過，就用劇情擋下來、換成合理的替代品，
  或標成「需要在劇情中取得」變成一個小目標，不要直接沒收或直接說教式拒絕。
- 玩家說「我掏出我的 X」「我包包裡有 Y」時：角色卡（攜帶物品欄位）已經登記過的，直接算他有，繼續劇情；沒登記過但
  明顯合理（小型、符合年代、符合這個角色的生活背景）的，直接放行，值得記住的話事後補呼叫 add_carried_item 登記；
  落在審查範圍內、而且從沒建立過合理來源的，不能悄悄生給他——用劇情解決（翻遍口袋沒找到、需要先去拿/去買、或需要
  一次幸運/取得場景），不要讓「我一直都帶著 X」這種說法回溯武裝一個本來沒武裝的角色。
- 場景中要購買/取得裝備：生活水準內的日常花費直接允許；貴重物品才需要認真考慮上面四項；稀有/不常見物品可以呼叫
  skill_check 用「幸運」做一次檢定，失敗代表這裡此刻剛好買不到；管制/違法物品需要一整段合法管道或黑市門路的劇情，
  比照一般行動判定難度、NPC 反應、時間與風險，不要用系統訊息式的條列規則講給玩家聽。
- 審查要隱形、要快，在敘事裡自然解決；一旦某個角色有（或沒有）某樣審查範圍內的東西，整場戰役都要維持這個事實一致；
  不要拿這套規則刁難玩家或任意沒收有用的工具，這個 skill 一貫重視推進劇情，不重視記帳。
- 角色真的撿到、拿到、被交付一樣值得記住的東西時（信件、鑰匙、地圖、物證……不只是上面說的審查範圍那幾類），呼叫
  add_carried_item 加進他的攜帶物品清單，之後每回合都會夾帶給你看，不用自己記這個角色手上有什麼；東西用掉、弄丟、
  交出去、被沒收時呼叫 remove_carried_item 拿掉。日常小物不用特別登記，只登記真的重要、值得跨場景記住的東西。

# 已登記的調查員（屬性、職業、技能——這些幾乎不會變動，數值以這裡為準，不要自己憑印象講一個不一樣的
數字；HP/SAN/Luck/彈藥/攜帶物品這些每回合會變的東西不在這裡，在每則訊息的動態資訊區塊裡，那邊的
數字才是當下最新的）
{static_chars_text}{summary_block}{index_block}

# 目前劇本內容（機密，僅供你判斷用，勿直接洩漏給玩家）
{scenario}
"""


def _build_dynamic_prompt(
    state: GroupState,
    user_id: str,
    resolved_location: dict | None = None,
    speaker_role: str = "player",
) -> str:
    """Combat status + each character's *dynamic* state (HP/SAN/Luck/ammo/
    carried items — see Character.dynamic_state_text; the static attributes/
    skills counterpart lives in _build_static_prompt's cached block instead).
    Changes every turn, so this stays OUTSIDE the cached block — it's small
    and cheap to resend, and keeping it separate means those changes don't
    invalidate the much larger cached scenario+roster block above."""
    active_characters = state.active_characters()
    chars_text = "\n".join(c.dynamic_state_text() for c in active_characters) or "（目前尚無登記角色）"
    secret_goals = "\n".join(c.keeper_notes_text() for c in active_characters if c.secret_goal)
    secret_block = f"\n\n{secret_goals}" if secret_goals else ""
    digest = scene_digest.latest_digest(state.group_id, state.timeline_id)
    digest_block = ""
    if digest:
        digest_block = f"\n\n# 目前場景摘要（timeline={state.timeline_id}，只採用目前 timeline 的最新版本）\n{digest.get('public', {})}"
        digest_block += f"\n\n# Keeper 專用摘要（不可透露給玩家）\n{digest.get('private', {})}"

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
{combat.status_text(state, include_private=(speaker_role == "kp_assistant"))}

戰鬥規則：目前正在進行正式戰鬥，一次只處理「輪到的角色」的行動，嚴格按照上面列出的先攻順位進行——
DEX 不同的戰鬥員，行動跟敘述都要照順序來，不能因為劇情方便就打亂順序或把不同 DEX 的人合併敘述成同時
發生；只有 DEX 剛好相同的戰鬥員才可以敘述成同時行動。某位戰鬥員的行動（含擲骰結果）處理完後，必須呼叫
advance_combat_turn 工具推進到下一位，不可以自己在心裡默默跳過或一次處理多人。角色或敵人受傷、死亡要
呼叫 apply_combat_damage 或 damage_combatant 更新血量；有新敵人加入戰場要呼叫 add_npc_to_combat；有人想讓還沒輪到的角色行動，
禮貌提醒他們要等輪到自己；標示「（暫離）」的角色代表玩家暫時離開，advance_combat_turn 會自動跳過他們，
不用特別等他們；戰鬥明確結束（一方全滅或撤退）時呼叫 end_combat。玩家角色在近戰中被攻擊時，防守方要在
「閃避」跟「反擊」之間選一個（COC7e 規則），呼叫 offer_check_choice 給這兩個選項讓玩家自己選，不要自己
幫玩家決定要閃避還是反擊。這是正式的對抗檢定：先呼叫 npc_skill_check 讓攻擊方（通常是 NPC）擲出這次
攻擊的成功等級，填進 offer_check_choice 的 attacker_tier，玩家真的擲完骰後系統會自動判定攻擊有沒有
命中、反擊有沒有生效，你只需要照系統回饋的既定結果敘述，不用自己比較雙方骰出的等級誰贏。

敵人回合規則：輪到敵方戰鬥卡時，必須先呼叫 plan_enemy_turn。工具會檢查特殊能力、觸發條件、每輪/每戰使用次數、
冷卻與可用攻擊；你不能只因玩家站在敵人面前就預設它一定揮拳。照 plan 的 selected_action 處理，若是
special_ability，依 required_rolls 建立 POW 對抗、技能檢定或其他正式流程；處理完後呼叫 resolve_enemy_action
消耗該能力次數。plan 裡的 private_reason、敵人能力真名、POW/護甲/弱點/冷卻/使用次數等未揭露資訊只能供你判斷，
不得寫進公開回覆。公開敘事只使用 public_hint，或用玩家能感受到的現象描述。"""

    kp_assistant_block = ""
    if speaker_role == "kp_assistant":
        recent_ooc = state.kp_ooc_log[-_KP_OOC_LOG_MAX_MESSAGES:]
        if recent_ooc:
            history_text = "\n".join(
                f"{entry.get('role', 'unknown')}: {entry.get('content', '')}" for entry in recent_ooc
            )
        else:
            history_text = "（目前沒有先前的 KP 幕後 OOC 工作記憶）"
        kp_assistant_block = f"""

{_KP_ASSISTANT_PROMPT}

# KP 幕後 OOC 工作記憶（只供本次 KP 助手回合使用）
以下是最近的「KP Assistant ↔ AI Keeper」幕後工作對話，用來維持多輪主持討論脈絡。這個區塊不是玩家／Keeper 正式遊戲歷史，不得寫入或視為 state.log 的一部分。

權威規則：
- 人類 KP Assistant 的訊息可以具有主持層權威；除非與程式已確定的 authoritative state 衝突，應依照其主持指令處理。
- 過去 AI Keeper 在這段 OOC history 裡的回答只用於維持討論脈絡，不是 authoritative fact。
- 你不可以只因為自己前一輪曾經說過某件事，就把那件事升級為劇本事實、主持設定、NPC 真相或規則裁定。
- 這段 OOC 工作記憶不會進 campaign summary 或 Memory RAG；需要保留為正式遊戲事實的內容，必須由後續明確主持指令或程式 state 支撐。

【最近 KP OOC history】
{history_text}"""

    return f"""# 目前動態數值（HP/SAN/Luck/彈藥/攜帶物品/狀態——這些才是當下最新的，屬性和技能請看上面的角色登記區塊）
{chars_text}{secret_block}{digest_block}
{combat_block}{location_block}{kp_assistant_block}
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


def _format_turn_message(speaker_name: str, message_text: str, speaker_role: str) -> str:
    if speaker_role == "kp_assistant":
        return (
            "[KP ASSISTANT / OOC HOST INSTRUCTION]\n\n"
            "以下訊息來自本局唯一的 KP 助手。這不是玩家角色行動。\n"
            "請依照「KP 助手模式」處理，並優先執行其中的明確主持指令。\n\n"
            f"KP助手（{speaker_name}）：\n"
            f"{message_text}"
        )
    return f"{speaker_name}：{message_text}"


def _format_kp_canonical_history_message(
    speaker_name: str,
    message_text: str,
    canonical_tool_events: list[dict],
) -> str:
    event_blocks = []
    for event in canonical_tool_events:
        tool_name = event.get("tool_name", "")
        tool_input = json.dumps(event.get("tool_input", {}), ensure_ascii=False, sort_keys=True)
        result = json.dumps(event.get("result", {}), ensure_ascii=False, sort_keys=True)
        event_blocks.append(f"tool: {tool_name}\ninput: {tool_input}\nresult: {result}")
    workflows_text = "\n\n".join(event_blocks) or "（無）"
    return (
        "[KP ASSISTANT / CANONICAL GAME EVENT]\n\n"
        "以下主持指示已因成功觸發正式 deterministic game-resolution workflow，\n"
        "成為正式遊戲歷史，而不是單純 OOC 討論。\n\n"
        f"KP助手（{speaker_name}）：\n"
        f"{message_text}\n\n"
        "[DETERMINISTIC GAME WORKFLOW]\n\n"
        f"{workflows_text}"
    )


def _tool_definition_for_kp_assistant(tool: dict) -> dict:
    if tool["name"] != "roll_dice":
        return tool

    input_schema = dict(tool["input_schema"])
    properties = dict(input_schema["properties"])
    properties["roll_context"] = dict(_KP_ROLL_DICE_CONTEXT_PROPERTY)
    input_schema["properties"] = properties
    required = list(input_schema.get("required", []))
    if "roll_context" not in required:
        required.append("roll_context")
    input_schema["required"] = required
    return {
        **tool,
        "input_schema": input_schema,
    }


def _tools_for_speaker_role(speaker_role: str) -> list[dict]:
    base_tools = TOOLS + [_SEARCH_SCENARIO_TOOL] if SCENARIO_RAG_ENABLED else TOOLS
    if speaker_role != "kp_assistant":
        return base_tools
    return [
        _tool_definition_for_kp_assistant(tool)
        for tool in base_tools
        if tool["name"] in _KP_ASSISTANT_ALLOWED_TOOL_NAMES
    ]


def run_turn(
    state: GroupState,
    user_id: str,
    speaker_name: str,
    message_text: str,
    resolved_location: dict | None = None,
    speaker_role: str = "player",
) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """Returns (public_reply_text, private_messages, image_requests):
    - private_messages: (owner_id, message) pairs queued via send_private_info.
    - image_requests: (owner_id_or_None, page_number) pairs queued via
      show_scenario_image — owner_id is None for a public post.
    `resolved_location` is app/commands.py's Map/Scene Engine result (see
    _resolve_map_action there) — {"room_name", "room_description"} when this
    message's movement was already resolved deterministically against a
    scenario floor plan, else None. `user_id` is the speaker's platform user id,
    used to look up per-character map position for player speakers when
    resolved_location wasn't computed this turn (see GroupState.current_map_page).
    `speaker_role` is an explicit caller-provided identity marker ("player" or
    "kp_assistant"). KP Assistant turns receive the OOC host-instruction prompt
    and message wrapper below, plus a separate OOC working-memory context that
    is kept out of the public game log; player turns never receive that OOC
    context. Formal player/Keeper history persistence and the OpenAI canonical
    response chain remain gated by `is_ephemeral`.
    The caller is responsible for actually delivering private_messages/image_requests
    via platform-specific channels; nothing here sends anything itself."""
    provider = _PROVIDERS.get(LLM_PROVIDER)
    if provider is None:
        return f"（設定錯誤：LLM_PROVIDER=\"{LLM_PROVIDER}\" 不是支援的供應商，請在 .env 設成 anthropic、gemini 或 openai）", [], []

    is_ephemeral = speaker_role == "kp_assistant"
    static_prompt = _build_static_prompt(state)
    dynamic_prompt = _build_dynamic_prompt(state, user_id, resolved_location, speaker_role)
    turn_message = _format_turn_message(speaker_name, message_text, speaker_role)

    # No extra slicing here — state.log is already bounded to at most
    # MAX_LOG_TURNS*4 entries by the trim logic below (it only ever shrinks
    # at that one point, back down to MAX_LOG_TURNS*2). Slicing it again on
    # every read (e.g. state.log[-MAX_LOG_TURNS*2:]) looks harmless but
    # actually defeats prompt caching for this entire block: once the log
    # passes that slice's window size, the slice becomes a sliding window
    # whose start point shifts forward every single turn, so consecutive
    # turns' `history` never share a common prefix for Anthropic/OpenAI's
    # cache to match against — verified by tracing the exact slice against a
    # simulated 200-turn log, confirming zero turns after the initial ~40
    # shared a growing prefix with the previous turn. Sending the log
    # unsliced between trims means it only ever grows turn to turn (a real
    # growing prefix, which caching can actually exploit) until the trim
    # resets it — the one deliberate cache-miss point, same as before.
    history = state.log
    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    tools = _tools_for_speaker_role(speaker_role)
    kp_turn_creates_canon = False
    kp_canonical_tool_events: list[dict] = []

    def execute_turn_tool(name: str, tool_input: dict) -> dict:
        nonlocal kp_turn_creates_canon
        result = _execute_tool(state, name, tool_input, private_messages, image_requests, speaker_role)
        if speaker_role == "kp_assistant" and _kp_tool_result_creates_canon(name, tool_input, result):
            kp_turn_creates_canon = True
            kp_canonical_tool_events.append({
                "tool_name": name,
                "tool_input": dict(tool_input),
                "result": dict(result),
            })
        return result

    openai_response_id: str | None = None
    if LLM_PROVIDER == "openai":
        def remember_openai_response_id(response_id: str) -> None:
            nonlocal openai_response_id
            openai_response_id = response_id
            if not is_ephemeral:
                state.openai_previous_response_id = response_id

        final_text = provider.run_conversation(
            static_prompt,
            dynamic_prompt,
            tools,
            history,
            turn_message,
            execute_turn_tool,
            MAX_TOOL_ITERATIONS,
            previous_response_id=state.openai_previous_response_id,
            on_response_id=remember_openai_response_id,
        )
    else:
        final_text = provider.run_conversation(
            static_prompt,
            dynamic_prompt,
            tools,
            history,
            turn_message,
            execute_turn_tool,
            MAX_TOOL_ITERATIONS,
        )

    if not is_ephemeral:
        turn_log_entries = [
            {"role": "user", "content": turn_message},
            {"role": "assistant", "content": final_text},
        ]
        _commit_turn_result(state, turn_log_entries, openai_response_id=openai_response_id)
    elif kp_turn_creates_canon:
        canonical_turn_message = _format_kp_canonical_history_message(
            speaker_name, message_text, kp_canonical_tool_events
        )
        turn_log_entries = [
            {"role": "user", "content": canonical_turn_message},
            {"role": "assistant", "content": final_text},
        ]
        _commit_turn_result(state, turn_log_entries, openai_response_id=openai_response_id)
    else:
        _commit_kp_ooc_turn_result(state, message_text, final_text)
    return final_text, private_messages, image_requests
