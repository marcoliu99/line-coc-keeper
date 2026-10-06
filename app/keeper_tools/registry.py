"""Ordered Keeper tool declarations and capabilities."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.models import SpeakerRole, SystemOrigin
from app.keeper_tools import character as character_handlers
from app.keeper_tools import checks as check_handlers
from app.keeper_tools import combat as combat_handlers
from app.keeper_tools import consequences as consequence_handlers
from app.keeper_tools import dice as dice_handlers
from app.keeper_tools import inventory as inventory_handlers
from app.keeper_tools import managed_combat as managed_handlers
from app.keeper_tools import messaging as messaging_handlers
from app.keeper_tools import scenario as scenario_handlers
from app.models import GroupState
from app.services import opposed_checks


@dataclass
class ToolCall:
    state: GroupState
    input: dict[str, Any]
    private_messages: list[tuple[str, str]]
    image_requests: list[tuple[str | None, int]]
    speaker_role: SpeakerRole
    name: str
    actor_id: str = ""
    # Set by turn code only, never from ``input``: lets the KP Assistant and a verified correction act on a character
    # that is not the acting player's own. None for an ordinary player's call.
    system_origin: SystemOrigin | None = None


def _summary_dispatch_rejected(call: ToolCall) -> dict[str, Any]:
    """The summary schema is for log compression, not Keeper tool dispatch."""
    return {"ok": False, "error": f"未知工具 {call.name}"}


_FOLLOWUP_CONSEQUENCE_SCHEMA: dict[str, Any] = {
    "type": "array", "maxItems": 4,
    "description": (
        "劇本明確規定此檢定結果會立刻觸發傷害或另一個檢定時，在原檢定建立時宣告。"
        "source_quote 須逐字出自目前劇本；when 是此檢定最終成功或失敗。"
        "傷害填 damage_expression 或固定 final_damage；後續檢定填 skill/difficulty。"
        "Dodge 失敗才受傷等連鎖規則可放在 check 的 next_consequences。沒有明確依據就省略。"
    ),
    "items": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "此來源檢定內唯一的後果鍵，例如 bed:dodge"},
            "kind": {"type": "string", "enum": ["damage", "check"]},
            "when": {"type": "string", "enum": ["success", "failure"]},
            "source_quote": {"type": "string", "description": "劇本中直接支持此後果的原句，包含骰式（若有）"},
            "damage_expression": {"type": "string"},
            "final_damage": {"type": "integer"},
            "damage_type": {"type": "string", "enum": ["impact", "fire", "cold", "poison", "other"]},
            "skill": {"type": "string"},
            "difficulty": {"type": "string", "enum": ["regular", "hard", "extreme"]},
            "next_consequences": {
                "type": "array", "maxItems": 4,
                "description": "後續檢定的結果再觸發的規則；例如 Dodge 失敗才承受撞擊傷害",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "kind": {"type": "string", "enum": ["damage"]},
                        "when": {"type": "string", "enum": ["success", "failure"]},
                        "source_quote": {"type": "string"},
                        "damage_expression": {"type": "string"},
                        "final_damage": {"type": "integer"},
                        "damage_type": {"type": "string", "enum": ["impact", "fire", "cold", "poison", "other"]},
                    },
                    "required": ["key", "kind", "when", "source_quote", "damage_type"],
                },
            },
        },
        "required": ["key", "kind", "when", "source_quote"],
    },
}


@dataclass(frozen=True)
class ToolSpec:
    schema: dict[str, Any]
    handler: Callable[[ToolCall], dict[str, Any]]
    read_only: bool = False
    resolved_check_followup: bool = False
    kp_assistant: bool = False
    kp_canonical_game: bool = False
    invalidates_combat_status: bool = False
    bounded_query: bool = False
    creates_check: bool = False
    opening: bool = False
    information_query: bool = False
    followup_only: bool = False


_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        schema={
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
        handler=dice_handlers.roll_dice,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
    ),
    ToolSpec(
        schema={
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
        handler=dice_handlers.roll_impaling_damage,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        kp_canonical_game=True,
    ),
    ToolSpec(
        schema={
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
        handler=dice_handlers.roll_weapon_damage,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        kp_canonical_game=True,
    ),
    ToolSpec(
        schema={
                "name": "skill_check",
                "description": (
                    "建立一次 COC7e 技能或屬性百分比檢定。預設不會替玩家擲骰，只會記錄技能、目標值、"
                    "獎懲骰與難度，讓玩家用 /coc check 或 Discord 按鈕擲骰；收到結果後才依照 authoritative"
                    "結果敘事。只有群組明確用 /coc autoroll on 開啟時，才會由 deterministic dice engine"
                    "立即擲骰並回傳 roll、成功等級與 success。只在調查／偵查、戰鬥或重大劇情行動時呼叫；"
                    "日常小動作直接敘述即可，不要自行編造結果。"
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
                                "第一次檢定失敗、你提議孤注一擲、玩家講了更冒險的做法後才呼叫的那一次，"
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
                        "action_context": {
                            "type": "string",
                            "description": "用一句不超過 240 字的短句記錄角色正在什麼情境做什麼，供 Keeper 收到系統結果後接續敘事；不要放完整劇本或 prompt。",
                        },
                        "consequences": _FOLLOWUP_CONSEQUENCE_SCHEMA,
                        "opposed": opposed_checks.SCHEMA,
                        "action_basis": {"type": "string", "description": "目前物件狀態、適用規則引用與觸發轉變；不改寫玩家宣告。對抗檢定必填，最多 600 字。"},
                    },
                    "required": ["investigator", "skill"],
                },
            },
        handler=check_handlers.skill_check,
        kp_assistant=True,
        kp_canonical_game=True,
        creates_check=True,
    ),
    ToolSpec(
        schema={
                "name": "offer_check_choice",
                "description": (
                    "建立一次有多個互斥選項的待處理檢定——用在玩家要在幾個技能之間選一個的一般情境"
                    "（不涉及被 NPC 攻擊）。玩家用按鈕或 /coc check <選項名稱> 選定並觸發玩家擲骰；"
                    "只有 autoroll 開啟時才由系統代擲。不能自己替玩家選或編結果。"
                    "如果這是被 NPC 攻擊時的防守選擇（COC7e 的『閃避』還是『反擊』），改用"
                    "offer_npc_attack_defense_choice——那個工具會直接處理攻擊方的檢定，不用"
                    "你自己先呼叫 npc_skill_check 再把結果填回這裡。"
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
                                    "kind": {
                                        "type": "string",
                                        "enum": ["dodge", "counter"],
                                        "description": (
                                            "如果這個選項是 COC7e 的閃避或反擊，填 dodge 或 counter；"
                                            "系統靠這個欄位判斷是不是反擊，比單看 label 文字更準確。"
                                            "不是閃避／反擊的一般選項（跟 offer_check_choice 的其他用途一樣）"
                                            "可以不填。"
                                        ),
                                    },
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
                        "action_context": {
                            "type": "string",
                            "description": "用一句不超過 240 字的短句記錄角色正在什麼情境做什麼，供 Keeper 收到系統結果後接續敘事。",
                        },
                    },
                    "required": ["investigator", "options"],
                },
            },
        handler=check_handlers.offer_check_choice,
        kp_assistant=True,
        kp_canonical_game=True,
        creates_check=True,
    ),
    ToolSpec(
        schema={
                "name": "npc_skill_check",
                "description": (
                    "立刻擲一次『沒有玩家可以自己擲骰』那一方（NPC、怪物、敵人）的技能百分比檢定，直接由"
                    "程式碼擲骰算出真正的擲骰值和成功等級，回傳給你——不要自己編一個 NPC 的檢定結果。"
                    "如果是『NPC 攻擊玩家、玩家要在閃避／反擊之間選一個』的對抗檢定情境，改用"
                    "offer_npc_attack_defense_choice（一次呼叫就包含這一步，不用先呼叫這個工具）；"
                    "這個工具留給其他劇本需要 NPC 自己做一次檢定、但不是那個特定防守選擇流程的場合。"
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
        handler=check_handlers.npc_skill_check,
        kp_assistant=True,
        kp_canonical_game=True,
    ),
    ToolSpec(
        schema={
                "name": "offer_npc_attack_defense_choice",
                "description": (
                    "『請求』一次「被 NPC 攻擊時的防守選擇」——COC7e 對抗檢定的完整標準流程。近戰跟遠程"
                    "在 COC7e 規則下走完全不同的判定機制（見 is_ranged 參數），這個工具會依 is_ranged 自動"
                    "選對的機制擲骰，不用你自己先呼叫 npc_skill_check、也不用自己編。跟 offer_check_choice "
                    "一樣只記錄選項清單，讓玩家選一個；玩家選定後用 /coc check 觸發防守方擲骰並自動判定"
                    "（autoroll 開啟時才可由系統代擲）。呼叫完之後只能敘述『被攻擊、需要在這幾個選項裡選一個』"
                    "的當下場景，不能自己選、不能自己編結果、不能自己講攻擊有沒有命中。"
                    "options 要不要給『反擊』選項看攻擊距離：近戰（engaged）才能反擊，給「閃避」「反擊」"
                    "兩個選項；遠程攻擊（near/any，例如槍械、投擲武器）COC7e 規則不允許反擊，只能給"
                    "「閃避」一個選項——這種情況 options 只給一個是合法的，不要為了湊兩個選項硬塞一個假的"
                    "反擊選項。如果只是一般多選一（不是被攻擊的防守情境），改用 offer_check_choice。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "investigator": {"type": "string", "description": "調查員角色名稱"},
                        "is_ranged": {
                            "type": "boolean",
                            "description": (
                                "這次攻擊是不是遠程（槍械、投擲武器等）。COC7e 規則：遠程攻擊不是對抗檢定——"
                                "攻擊方單獨擲自己的技能檢定決定有沒有命中（不能孤注一擲），防守方唯一能做的"
                                "是『撲向掩體』，是防守方自己獨立的閃避檢定，成功的話會讓攻擊方這次射擊多"
                                "承受一個懲罰骰，但不會直接讓攻擊落空。近戰才是雙方比較成功等級的對抗檢定。"
                                "近戰填 false 或省略；遠程一定要填 true，不要漏填讓系統誤判成近戰。"
                            ),
                        },
                        "options": {
                            "type": "array",
                            "minItems": 1,
                            "description": "互斥選項；近戰通常是閃避+反擊兩個，遠程攻擊沒有反擊，只給閃避一個",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "description": "選項顯示名稱，例如「閃避」「反擊」"},
                                    "skill": {"type": "string", "description": "這個選項要用的技能或屬性名稱"},
                                    "bonus_dice": {"type": "integer", "description": "獎勵骰數量，預設 0"},
                                    "penalty_dice": {"type": "integer", "description": "懲罰骰數量，預設 0"},
                                    "kind": {
                                        "type": "string",
                                        "enum": ["dodge", "counter"],
                                        "description": (
                                            "這個選項是閃避還是反擊，請務必填寫（dodge 或 counter）——系統"
                                            "靠這個欄位判斷平手規則、大成功時要不要過濾掉這個選項，比單看"
                                            "label 文字更準確可靠。"
                                        ),
                                    },
                                },
                                "required": ["label", "skill", "kind"],
                            },
                        },
                        "attacker_skill_value": {"type": "integer", "description": "攻擊方（NPC）這次攻擊技能的百分比值"},
                        "attacker_bonus_dice": {"type": "integer", "description": "攻擊方獎勵骰數量，預設 0"},
                        "attacker_penalty_dice": {"type": "integer", "description": "攻擊方懲罰骰數量，預設 0"},
                        "action_context": {
                            "type": "string",
                            "description": "用一句不超過 240 字的短句記錄角色正在什麼情境做什麼，供 Keeper 收到系統結果後接續敘事。",
                        },
                    },
                    "required": ["investigator", "options", "attacker_skill_value"],
                },
            },
        handler=check_handlers.offer_npc_attack_defense_choice,
        kp_assistant=True,
        kp_canonical_game=True,
        creates_check=True,
    ),
    ToolSpec(
        schema={
                "name": "clear_pending_check",
                "description": (
                    "取消某位角色目前『待處理』的 skill/SAN/CON 檢定或互斥選擇。預設模式下 skill_check／sanity_check"
                    "會建立 pending；選擇項目還沒被玩家用 /coc check 或按鈕解決前，"
                    "這個工具不會擲骰、不會判定成敗，只會把它從等待清單移除。用在原本要求的選擇已經因劇情推進、"
                    "戰鬥結束、角色離場等原因不再需要玩家回應的情況——例如威脅已經解除、角色已經倒下、"
                    "或你判斷這筆選擇不用再等玩家回覆了。若新檢定被舊 pending 擋下，且確認那筆真的過時，"
                    "才用這個工具清掉它；有效的選擇不要清掉。"
                    "你自己口頭更正一筆先前建立的檢定時也要同步更正待處理狀態：先用這個工具清掉舊項目，"
                    "再依原本建立它的流程重新登記正確版本。若舊項目是 skill_check／sanity_check 建立的單一檢定，"
                    "用正確參數重新呼叫原本的 skill_check／sanity_check；若舊項目是 offer_check_choice 建立的互斥選擇，"
                    "用修正後的完整選項重新呼叫 offer_check_choice；若舊項目是 offer_npc_attack_defense_choice 建立的防守選擇，"
                    "用修正後的完整防守選項及攻擊情境重新呼叫 offer_npc_attack_defense_choice。"
                    "不要把互斥選項改成單一 skill_check／sanity_check，否則會丟失其他選項或對抗攻擊脈絡；"
                    "也不能只在敘述裡說『這筆不算』卻留著舊的待處理檢定，否則玩家之後 /coc check 會擲到你已經說不算的那一筆。"
                    "角色目前沒有待處理的檢定時呼叫這個工具是安全的 no-op，不會出錯。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "investigator": {"type": "string", "description": "調查員角色名稱"},
                    },
                    "required": ["investigator"],
                },
            },
        handler=check_handlers.clear_pending_check,
        kp_assistant=True,
        kp_canonical_game=True,
    ),
    ToolSpec(
        schema={
                "name": "sanity_check",
                "description": (
                    "建立一次理智檢定（SAN check）——用於角色目擊恐怖事物、遭遇超自然現象等場合。"
                    "預設只記錄成功/失敗的理智損失公式，讓玩家用 /coc check 擲骰後才更新 SAN；"
                    "只有 /coc autoroll on 時才立即由系統擲骰、更新 SAN，並處理必要的 INT 與短暫瘋狂。"
                    "不要自行編結果或先扣理智。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "investigator": {"type": "string"},
                        "loss_success": {"type": "string", "description": "檢定成功時的理智損失，如 '0'、'1'、'1d4'"},
                        "loss_failure": {"type": "string", "description": "檢定失敗時的理智損失，如 '1d6'、'1d10'"},
                        "action_context": {
                            "type": "string",
                            "description": "用一句不超過 240 字的短句記錄角色正在什麼情境做什麼，供 Keeper 收到系統結果後接續敘事。",
                        },
                    },
                    "required": ["investigator", "loss_success", "loss_failure"],
                },
            },
        handler=check_handlers.sanity_check,
        kp_assistant=True,
        kp_canonical_game=True,
        creates_check=True,
    ),
    ToolSpec(
        schema={
                "name": "adjust_character",
                "description": (
                    "調整角色的 HP、MP、SAN 或 LUCK 數值（例如受傷扣血、花費幸運點、恢復精神力）。"
                    "field 只能是 hp/mp/san/luck，delta 為正負整數變化量。戰鬥中須填 event_id 與來源 reason；攻擊應使用 declare_combat_action，不可自行猜傷害。"
                    "COC7e 規則：如果這次扣血（field=hp、delta 為負）單次傷害達到角色最大 HP 的一半以上，"
                    "預設會替玩家註冊一次 CON 檢定，等玩家輸入 /coc check CON；只有 /coc autoroll on 才立即"
                    "代擲並回傳結果。不要自行判斷重傷檢定結果。"
                    "只能用在 investigator 參數指名的那位角色自己的數值變化（例如角色自己受傷、花費自己"
                    "的幸運點）；不能拿來記錄或暫存跟這位角色無關的擲骰結果（例如別人的傷害骰、環境效果"
                    "骰），也不能用來對敵人造成傷害——敵人傷害一律用 apply_combat_damage（傳未扣護甲的"
                    "raw_damage）、apply_final_combat_damage（傳已扣除護甲的 final_damage，避免重複扣"
                    "護甲）或 damage_combatant。"
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
        handler=character_handlers.adjust_character,
    ),
    ToolSpec(
        schema={
            "name": "apply_resolved_check_damage",
            "description": (
                "提交已結算檢定觸發的非戰鬥傷害。須引用來源 check/event 和預先授權的 consequence_key；"
                "若給 damage_expression，Python 一次擲骰並原子扣 HP；若給 final_damage，必須是來源"
                "預先授權的固定值。重試只回傳原收據，不重擲或重扣。不可代替戰鬥傷害工具。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "investigator": {"type": "string"},
                    "damage_expression": {"type": "string"},
                    "final_damage": {"type": "integer"},
                    "damage_type": {"type": "string", "enum": ["impact", "fire", "cold", "poison", "other"]},
                    "source_check_id": {"type": "string"},
                    "source_event_id": {"type": "string"},
                    "consequence_key": {"type": "string"},
                    "cause": {"type": "string"},
                },
                "required": ["investigator", "damage_type", "source_check_id", "source_event_id",
                             "consequence_key", "cause"],
            },
        },
        handler=consequence_handlers.apply_resolved_check_damage,
        resolved_check_followup=True,
        followup_only=True,
    ),
    ToolSpec(
        schema={
            "name": "create_triggered_check",
            "description": (
                "建立已結算檢定依劇本觸發的新檢定，例如 Spot Hidden 成功後的 Dodge。"
                "必須引用來源 check/event 和預先授權的 consequence_key；只建立玩家 pending，"
                "即使 autoroll 開啟也不代骰。不能重建原檢定。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "investigator": {"type": "string"},
                    "skill": {"type": "string"},
                    "difficulty": {"type": "string", "enum": ["regular", "hard", "extreme"]},
                    "trigger_check_id": {"type": "string"},
                    "trigger_event_id": {"type": "string"},
                    "trigger_condition": {"type": "string"},
                    "consequence_key": {"type": "string"},
                    "action_context": {"type": "string"},
                },
                "required": ["investigator", "skill", "difficulty", "trigger_check_id",
                             "trigger_event_id", "trigger_condition", "consequence_key", "action_context"],
            },
        },
        handler=consequence_handlers.create_triggered_check,
        resolved_check_followup=True,
        creates_check=True,
        followup_only=True,
    ),
    ToolSpec(
        schema={
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
        handler=inventory_handlers.adjust_ammo,
    ),
    ToolSpec(
        schema={
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
        handler=inventory_handlers.add_carried_item,
    ),
    ToolSpec(
        schema={
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
        handler=inventory_handlers.remove_carried_item,
    ),
    ToolSpec(
        schema={
                "name": "transfer_item",
                "description": (
                    "一位調查員把攜帶物品交給另一位調查員時使用，一次完成：要嘛兩邊背包都改變，要嘛都不變。"
                    "交接一律用這個，不要分開呼叫 remove_carried_item 再 add_carried_item。"
                    "from 與 to 填角色名稱（需完全相符）；只有給出者自己的玩家能交出他的物品；"
                    "quantity 省略時為 1，同名物品要交出多份時一次填完。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string", "description": "給出者的角色名稱"},
                        "to": {"type": "string", "description": "接收者的角色名稱"},
                        "item": {"type": "string", "description": "要交出的物品，需與給出者背包裡的文字相符"},
                        "quantity": {"type": "integer", "minimum": 1, "description": "交出幾份，省略為 1"},
                        "source_event_id": {"type": "string"},
                    },
                    "required": ["from", "to", "item"],
                },
            },
        handler=inventory_handlers.transfer_item,
        kp_assistant=True,
        kp_canonical_game=True,
    ),
    ToolSpec(
        schema={
                "name": "record_established_fact",
                "description": "記錄已被證實、之後必須保持一致的劇情事實；不是猜測或普通對話。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "visibility": {"type": "string", "enum": ["public", "kp_only"]},
                        "source_record_id": {"type": "string", "description": "劇本紀錄 ID；沒有可核對來源時省略"},
                        "source_quote": {"type": "string", "description": "來源紀錄中的原句；不得改寫"},
                        "source_condition": {"type": "string", "enum": ["unconditional", "observed_now", "resolved_check"]},
                        "trigger_event_id": {"type": "string", "description": "條件為已結算檢定時的持久 event_id"},
                        "constraints": {"type": "object", "description": "可從來源原句核對的數量約束，不能猜值", "additionalProperties": False,
                            "properties": {"entity": {"type": "string"}, "unit": {"type": "string"}, "quantity": {"type": "integer", "minimum": 0}},
                            "required": ["entity", "unit", "quantity"]},
                    },
                    "required": ["fact"],
                },
            },
        handler=scenario_handlers.record_fact_or_clue,
        kp_assistant=True,
    ),
    ToolSpec(
        schema={
                "name": "record_clue",
                "description": "記錄調查員實際取得、之後可能回頭引用的線索。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "clue": {"type": "string"},
                        "visibility": {"type": "string", "enum": ["public", "kp_only"]},
                        "source_record_id": {"type": "string", "description": "劇本紀錄 ID；沒有可核對來源時省略"},
                        "source_quote": {"type": "string", "description": "來源紀錄中的原句；不得改寫"},
                        "source_condition": {"type": "string", "enum": ["unconditional", "observed_now", "resolved_check"]},
                        "trigger_event_id": {"type": "string", "description": "條件為已結算檢定時的持久 event_id"},
                        "constraints": {"type": "object", "description": "可從來源原句核對的數量約束，不能猜值", "additionalProperties": False,
                            "properties": {"entity": {"type": "string"}, "unit": {"type": "string"}, "quantity": {"type": "integer", "minimum": 0}},
                            "required": ["entity", "unit", "quantity"]},
                    },
                    "required": ["clue"],
                },
            },
        handler=scenario_handlers.record_fact_or_clue,
        kp_assistant=True,
    ),
    ToolSpec(
        schema={
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
        handler=inventory_handlers.add_status_tag,
    ),
    ToolSpec(
        schema={
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
        handler=inventory_handlers.remove_status_tag,
    ),
    ToolSpec(
        schema={
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
        handler=character_handlers.set_skill,
    ),
    ToolSpec(
        schema={
                "name": "get_character_sheet",
                "description": "取得角色完整角色卡資料（所有屬性與技能），需要確認細節時使用。",
                "input_schema": {
                    "type": "object",
                    "properties": {"investigator": {"type": "string"}},
                    "required": ["investigator"],
                },
            },
        handler=character_handlers.get_character_sheet,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        bounded_query=True,
        opening=True,
        information_query=True,
    ),
    ToolSpec(
        schema={
                "name": "start_combat",
                "description": (
                    "開始一場正式戰鬥，會依照目前登記角色的 DEX 建立先攻順位。"
                    "有敵人啟動時（一隻也一樣）請改用 initialize_combat 一次開戰並登記，不要只開戰；"
                    "戰鬥中途才加入的敵人用 add_npc_to_combat。之後用 advance_combat_turn 依序推進回合。"
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
        handler=combat_handlers.start_combat,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
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
                        "dex": {"type": "integer", "description": "DEX 值，決定先攻順序；必須來自已核對來源或明確裁定，缺少時暫停。"},
                        "hp": {"type": "integer", "description": "最大生命值"},
                        "is_ally": {"type": "boolean", "description": "true 表示這是站在調查員這邊的 NPC 隊友，不是敵人"},
                        "armor": {
                            "type": "array",
                            "description": (
                                "敵人護甲規則，每筆含 id/label/value/applies_to/bypass_tags/public_hint 等"
                                "（不是 name——欄位名稱是 label，不要跟 attacks/abilities 的 name 搞混）；"
                                "玩家未發現前不要公開具體數字，public_hint 可用中性描述代替。"
                            ),
                            "items": {"type": "object"},
                        },
                        "attacks": {
                            "type": "array",
                            "description": (
                                "敵人攻擊表，每筆含 id/label/skill_name/skill_value/damage/range_band 等。"
                                "range_band 決定這招是不是近戰——沒寫預設是 engaged（近戰），拳頭、小刀、"
                                "長矛這類真的要貼身的攻擊可以不寫；只要是有距離的攻擊（手槍、步槍、弓箭、"
                                "投擲武器等）務必明確填 near，不然會被系統當成近戰，玩家被打時會多出一個"
                                "COC7e 規則不允許的『反擊』選項。不受距離限制的攻擊（法術、詛咒、心靈攻擊等）"
                                "填 any。"
                            ),
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
        handler=combat_handlers.add_npc_to_combat,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "initialize_combat",
                "description": (
                    "開啟戰鬥並一次登錄本次遭遇的所有敵方 NPC，並依 DEX 計算先攻順位——用於戰鬥剛開始、"
                    "一隻或多隻敵人已登場的情況（一隻就是只有一筆的 enemies），取代連續呼叫 start_combat 加 add_npc_to_combat。"
                    "同種怪物每一隻都要給不同的顯示名稱（例如「魚人（左）」／「魚人（右）」），不要用同一個"
                    "名字填多筆——系統只會在偵測到同名時才自動編號，那是最後手段，不是預設做法。"
                    "戰鬥已在進行、中途才加入的新敵人，改用 add_npc_to_combat。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "enemies": {
                            "type": "array",
                            "minItems": 1,
                            "description": "本次遭遇的所有敵方 NPC／怪物名單，每一隻同種怪物都要給不同的顯示名稱",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "dex": {"type": "integer", "description": "DEX 值，決定先攻順序；必須來自已核對來源或明確裁定，缺少時暫停。"},
                                    "hp": {"type": "integer", "description": "最大生命值"},
                                    "is_ally": {"type": "boolean", "description": "true 表示這是站在調查員這邊的 NPC 隊友，不是敵人"},
                                    "armor": {
                                        "type": "array",
                                        "description": (
                                            "敵人護甲規則，每筆含 id/label/value/applies_to/bypass_tags/public_hint 等"
                                            "（不是 name——欄位名稱是 label，不要跟 attacks/abilities 的 name 搞混）；"
                                            "玩家未發現前不要公開具體數字，public_hint 可用中性描述代替。"
                                        ),
                                        "items": {"type": "object"},
                                    },
                                    "attacks": {
                                        "type": "array",
                                        "description": (
                                            "敵人攻擊表，每筆含 id/label/skill_name/skill_value/damage/range_band 等。"
                                            "range_band 決定這招是不是近戰——沒寫預設是 engaged（近戰），拳頭、小刀、"
                                            "長矛這類真的要貼身的攻擊可以不寫；只要是有距離的攻擊（手槍、步槍、弓箭、"
                                            "投擲武器等）務必明確填 near，不然會被系統當成近戰，玩家被打時會多出一個"
                                            "COC7e 規則不允許的『反擊』選項。不受距離限制的攻擊（法術、詛咒、心靈攻擊等）"
                                            "填 any。"
                                        ),
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
                    },
                    "required": ["enemies"],
                },
            },
        handler=combat_handlers.initialize_combat,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "get_combat_status",
                "description": "查詢目前戰鬥的回合數、先攻順位與現在輪到誰的行動；戰鬥結束後也可查最近一場的結算證據。一般公開視圖不顯示敵人 HP；KP Assistant 可看 private 視圖。",
                "input_schema": {"type": "object", "properties": {}},
            },
        handler=combat_handlers.get_combat_status,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        bounded_query=True,
        opening=True,
        information_query=True,
    ),
    ToolSpec(
        schema={
                "name": "advance_combat_turn",
                "description": "把戰鬥推進到下一位戰鬥員的回合（已倒下的會自動跳過）。每次處理完一位戰鬥員的行動後都必須呼叫這個工具，不可以自己心裡默默跳過。",
                "input_schema": {"type": "object", "properties": {}},
            },
        handler=combat_handlers.advance_combat_turn,
        resolved_check_followup=True,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "damage_combatant",
                "description": (
                    "調整戰鬥中某位角色或敵人的 HP（受傷用負數，治療用正數）。適用於戰鬥中的任何一方，"
                    "包含玩家角色與 NPC。負數 delta 會走正式傷害流程並套用護甲；若輸入的是已計算完成、不可再扣護甲的"
                    "最終傷害，請改用 apply_final_combat_damage。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "delta": {"type": "integer"},
                    },
                    "required": ["name", "delta"],
                },
            },
        handler=combat_handlers.damage_combatant,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
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
        handler=combat_handlers.plan_enemy_turn,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "resolve_enemy_action",
                "description": (
                    "敵人 plan 對應的行動已敘事/擲骰處理後呼叫；特殊能力會消耗次數與冷卻，攻擊命中時會在此正式套用傷害。"
                    "若 plan 的特殊能力 effect 宣告 on_success=apply_effect，必須把正式檢定結果放在 outcome.success；"
                    "攻擊則傳 outcome.hit 與 outcome.damage；只有成功的正式結果才會改變戰鬥狀態。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "outcome": {
                            "type": "object",
                            "description": "特殊能力使用 success；攻擊使用 hit 與命中後的非負整數 damage。",
                            "properties": {
                                "success": {"type": "boolean"},
                                "hit": {"type": "boolean"},
                                "damage": {"type": "integer", "minimum": 0},
                                "damage_type": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "required": ["plan_id"],
                },
            },
        handler=combat_handlers.resolve_enemy_action,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "apply_combat_damage",
                "description": (
                    "套用正式戰鬥傷害。raw_damage 是尚未扣除護甲的原始傷害，系統會計算護甲抵銷、final damage 與 HP。"
                    "若傷害數字已經是扣除護甲後的最終值，改用 apply_final_combat_damage，避免重複扣除護甲。"
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
        handler=combat_handlers.apply_combat_damage,
        resolved_check_followup=True,
        kp_assistant=True,
        kp_canonical_game=True,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "apply_final_combat_damage",
                "description": (
                    "套用已經確定的最終傷害數字；final_damage 已包含護甲等減免，不會再次扣除護甲。"
                    "仍會正式更新戰鬥 HP、傷害觸發與重傷檢定。只有在傷害數字已是最終值時使用；"
                    "若要由系統依目標護甲計算，請用 apply_combat_damage 並傳入 raw_damage。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "final_damage": {"type": "integer", "minimum": 0},
                        "damage_type": {"type": "string", "description": "physical/fire/bullet/melee/magic 等"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "source_id": {"type": "string"},
                    },
                    "required": ["target", "final_damage"],
                },
            },
        handler=combat_handlers.apply_final_combat_damage,
        resolved_check_followup=True,
        kp_assistant=True,
        kp_canonical_game=True,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
                "name": "add_combat_effect",
                "description": (
                    "替戰鬥中的角色、敵人、全體或環境加入固定時點效果，例如燃燒、流血、場景壓迫。"
                    "target 可填角色名稱、all/全體或 environment/環境；環境效果可作為全場狀態，傷害效果請指定角色或全體。"
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
        handler=combat_handlers.add_combat_effect,
        kp_assistant=True,
        kp_canonical_game=True,
    ),
    ToolSpec(
        schema={
                "name": "end_combat",
                "description": "取得目前戰鬥結算預覽；另用 confirm_combat_settlement 明確確認。不能清除尚未處理的檢定或未來義務。",
                "input_schema": {"type": "object", "properties": {}},
            },
        handler=combat_handlers.end_combat,
        invalidates_combat_status=True,
    ),
    ToolSpec(
        schema={
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
        handler=messaging_handlers.send_private_info,
        opening=True,
    ),
    ToolSpec(
        schema={
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
        handler=scenario_handlers.search_scenario_images,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        bounded_query=True,
        opening=True,
        information_query=True,
    ),
    ToolSpec(
        schema={
                "name": "advance_scenario_chapter",
                "description": (
                    "劇情確實完成目前章節、進入下一個主要場景時才呼叫。會把 Context 從目前章節滑動到下一章及其後一章，"
                    "並同步可展示圖片與地圖；不能跳章或用於尚未發生的內容。"
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
        handler=scenario_handlers.advance_scenario_chapter,
        kp_assistant=True,
    ),
    ToolSpec(
        schema={
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
        handler=scenario_handlers.show_scenario_image,
        kp_assistant=True,
        opening=True,
    ),
    ToolSpec(
        schema={
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
        handler=scenario_handlers.search_memory,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        bounded_query=True,
        opening=True,
        information_query=True,
    ),
    ToolSpec(
        schema={
            "name": "search_scenario",
            "description": (
                "在劇本全文裡搜尋跟這個查詢最相關的段落（依頁面為單位），回傳前幾筆最符合的內容。"
                "劇本改用檢索模式時（看到『這份劇本改用檢索模式』的提示）必須用這個工具查詢，"
                "不能憑空想像劇本內容；查詢字詞盡量用劇本裡可能出現的具體名詞（人名、地名、物品、關鍵字），"
                "不要問完整句子。"
                "查詢時以「目前這個事件/場景」為單位思考需要哪些劇本資料，不要只查眼前缺"
                "的單一事實——呼叫前先想一想這個事件接下來可能還會用到哪些相關資訊（相關"
                "的 NPC/怪物/地點、目前情境與遭遇、可能的行動與行為模式、相關法術/武器/能"
                "力、使用條件與代價與限制、立即的後續發展），把這些一起包進同一次查詢"
                "裡，不要每個小問題都分開各查一次。查詢範圍要涵蓋這個事件需要的東西，但"
                "不要查到之後才會發生的場景、秘密或遭遇，避免劇透也避免資料過大。拿到查"
                "詢結果後，先仔細看有沒有涵蓋到目前需要的資訊，只有真的缺東西才再查一"
                "次；不要因為想再三確認已經查到的內容而重複查詢——但這不代表只能查一"
                "次，如果一次查詢真的不夠涵蓋這個事件所需的資訊，可以再查。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查詢的關鍵字或名詞，例如「卡西迪」「地下室」「儀式」"},
                    "continuation": {"type": "string", "description": "同一查詢的續取識別；缺少必要依據時續取。complete_for_action=false 時不得據此執行機制。"},
                    "source": {"type": "string", "enum": ["auto", "original"],
                               "description": "預設 auto 中文優先；中文有命中但缺少裁決依據時用 original 補查原稿。查詢可含原文名稱及缺少的護甲、特殊能力、觸發條件、代價、每輪/每戰限制；命中不代表完整，未查到不等於不存在。"},
                },
                "required": ["query"],
            },
        },
        handler=scenario_handlers.search_scenario,
        read_only=True,
        resolved_check_followup=True,
        kp_assistant=True,
        bounded_query=True,
        opening=True,
        information_query=True,
    ),
    ToolSpec(
        schema={
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
        },
        handler=_summary_dispatch_rejected,
        read_only=True,
        resolved_check_followup=True,
        bounded_query=True,
        opening=True,
    ),
)

_SPECS += (
    ToolSpec(schema={'name': 'declare_combat_action', 'description': '宣告來源支持的近戰或單發攻擊；系統使用固定武器表與既有角色數值。不得傳入命中、傷害或骰值。', 'input_schema': {'type': 'object', 'properties': {'action_id': {'type': 'string'}, 'actor_id': {'type': 'string'}, 'target_id': {'type': 'string'}, 'weapon_reference': {'type': 'string'}, 'action_kind': {'type': 'string', 'enum': ['melee', 'single_shot']}, 'distance_yards': {'type': 'number', 'minimum': 0}}, 'required': ['action_id', 'actor_id', 'target_id', 'weapon_reference']}}, handler=managed_handlers.declare_combat_action, creates_check=True, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'run_combat_action', 'description': '恢復既有戰鬥行動；沿用原擲骰紀錄與玩家選擇。', 'input_schema': {'type': 'object', 'properties': {'action_id': {'type': 'string'}}, 'required': ['action_id']}}, handler=managed_handlers.run_combat_action, creates_check=True, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'submit_combat_choice', 'description': '提交目前玩家所屬防禦選擇；不得替其他玩家選擇。', 'input_schema': {'type': 'object', 'properties': {'interaction_id': {'type': 'string'}, 'choice': {'type': 'string', 'enum': ['dodge', 'counter', 'dive', 'no_defense']}}, 'required': ['interaction_id', 'choice']}}, handler=managed_handlers.submit_combat_choice, creates_check=True, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'preview_combat_settlement', 'description': '取得暫定資源差異與結算ID；待處理檢定/Luck/當前醫療後果未完成時不能結算。', 'input_schema': {'type': 'object', 'properties': {}, 'required': []}}, handler=managed_handlers.preview_combat_settlement, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'confirm_combat_settlement', 'description': 'Keeper 明確確認目前結算預覽，原子發布資源並轉移未來事項；不需要真人KP註冊。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'settlement_id': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['combat_id', 'settlement_id', 'reason']}}, handler=managed_handlers.confirm_combat_settlement, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'rollback_combat', 'description': 'Keeper 明確回滾未结算戰鬥並保留紀錄；普通玩家命令不能直接呼叫。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['combat_id', 'event_id', 'reason']}}, handler=managed_handlers.rollback_combat, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'correct_combat_event', 'description': 'Keeper 明確附加更正紀錄；保留原骰，後續無法成立則暫停裁定。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}, 'target_event_id': {'type': 'string'}, 'changes': {'type': 'object', 'properties': {'after': {}, 'amount': {'type': 'integer'}, 'delta': {'type': 'integer'}}}}, 'required': ['combat_id', 'event_id', 'reason', 'target_event_id', 'changes']}}, handler=managed_handlers.correct_combat_event, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'reconcile_combat_baseline', 'description': '外部持久角色變更衝突時，Keeper 明確選擇保留暫定值或採用持久值；產生新預覽。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}, 'investigator': {'type': 'string'}, 'decision': {'type': 'string', 'enum': ['keep_working', 'adopt_persistent']}}, 'required': ['combat_id', 'event_id', 'reason', 'investigator', 'decision']}}, handler=managed_handlers.reconcile_combat_baseline, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'change_combat_initiative', 'description': 'Keeper 只在已完成行動邊界調整先攻，待處理選擇/檢定/Luck時禁止。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}, 'order': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['combat_id', 'event_id', 'reason', 'order']}}, handler=managed_handlers.change_combat_initiative, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'process_postcombat_obligations', 'description': '明確推進邏輯遊戲回合，處理戰鬥後醫療/效果事項；沿用紀錄，不受現實時間觸發。', 'input_schema': {'type': 'object', 'properties': {'logical_round': {'type': 'integer', 'minimum': 0}, 'event_id': {'type': 'string'}}, 'required': ['logical_round', 'event_id']}}, handler=managed_handlers.process_postcombat_obligations, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'get_damage_severity', 'description': '只查詢明確來源/裁定的severity ID與固定骰式；火/毒/溺水等敘述不能推斷類別。', 'input_schema': {'type': 'object', 'properties': {'severity_id': {'type': 'string', 'enum': ['minor', 'moderate', 'severe', 'deadly', 'terminal', 'splat']}}, 'required': ['severity_id']}}, handler=managed_handlers.get_damage_severity, read_only=True, bounded_query=True, information_query=True, kp_assistant=True),
)

_SPECS += (
    ToolSpec(schema={'name': 'declare_combat_effect', 'description': 'Keeper 明確指定來源/裁定的傷害severity及範圍、觸發、停止條件；特殊規則未支持時暫停。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'effect_id': {'type': 'string'}, 'target_id': {'type': 'string'}, 'severity_id': {'type': 'string', 'enum': ['minor','moderate','severe','deadly','terminal','splat']}, 'scope': {'type': 'string','enum': ['incident','round']}, 'timing': {'type': 'string','enum': ['round_start','turn_start','turn_end','round_end']}, 'defense': {'type': 'string','enum': ['none']}, 'special_rule': {'type': 'string'}, 'stop_condition': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['combat_id','effect_id','target_id','severity_id','stop_condition','reason']}}, handler=managed_handlers.declare_combat_effect, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'stop_combat_effect', 'description': 'Keeper 根據明確停止條件結束效果；保留原紀錄。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'effect_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['combat_id','effect_id','event_id','reason']}}, handler=managed_handlers.stop_combat_effect, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'close_legacy_combat', 'description': 'Keeper 明確關閉沒有安全基準的舊版戰鬥；保留歷史與待處理證據，不猜測戰前值。', 'input_schema': {'type': 'object','properties': {'event_id': {'type': 'string'},'reason': {'type': 'string'}},'required': ['event_id','reason']}}, handler=managed_handlers.close_legacy_combat, invalidates_combat_status=True),
)


_SPECS += (
    ToolSpec(schema={'name': 'resolve_combat_ruling', 'description': 'Keeper 明確裁定暫停的行動：指定已核對的武器ID或物理距離後恢復，或明確取消；不接受任意命中、傷害或骰值。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'action_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}, 'decision': {'type': 'string', 'enum': ['resume','cancel']}, 'weapon_reference': {'type': 'string'}, 'distance_yards': {'type': 'number','minimum': 0}}, 'required': ['combat_id','action_id','event_id','reason','decision']}}, handler=managed_handlers.resolve_combat_ruling, invalidates_combat_status=True),
    ToolSpec(schema={'name': 'reconcile_combat_correction', 'description': 'Keeper 明確核對更正後的傷害/重傷狀態與受影響行動；保留原骰與原選擇，不默默丟棄。', 'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'}, 'event_id': {'type': 'string'}, 'reason': {'type': 'string'}, 'injury_by_character': {'type': 'object', 'additionalProperties': {'type': 'object','properties': {'major_wound': {'type': 'boolean'},'unconscious': {'type': 'boolean'},'dying': {'type': 'boolean'},'dead': {'type': 'boolean'}}, 'additionalProperties': False}}, 'acknowledge_action_ids': {'type': 'array','items': {'type': 'string'}}}, 'required': ['combat_id','event_id','reason','injury_by_character','acknowledge_action_ids']}}, handler=managed_handlers.reconcile_combat_correction, invalidates_combat_status=True),
)

_SPECS += (
    ToolSpec(schema={'name': 'get_weapon_definition', 'description': '離線查詢已核對武器ID/精確名稱/明確別名，返回骰式、DB、距離表及來源；含糊描述返回候選，不推定持有或彈藥。', 'input_schema': {'type': 'object','properties': {'reference': {'type': 'string'}},'required': ['reference']}}, handler=managed_handlers.get_weapon_definition, read_only=True, bounded_query=True, information_query=True, kp_assistant=True),
)

for _spec in _SPECS:
    if _spec.schema['name'] in {'add_npc_to_combat', 'initialize_combat'}:
        _source_schema = {'type': 'object', 'properties': {
            'url': {'type': 'string'}, 'revision': {'type': 'string'}, 'sha256': {'type': 'string'},
            'attack_mode': {'type': 'string', 'enum': ['melee', 'single_shot']},
            'extreme_rule': {'type': 'string', 'enum': ['maximum', 'impale']},
        }, 'required': ['url', 'revision', 'sha256', 'attack_mode', 'extreme_rule']}
        _properties = _spec.schema['input_schema']['properties']
        if _spec.schema['name'] == 'initialize_combat':
            _properties = _properties['enemies']['items']['properties']
        _properties['source'] = _source_schema
        _properties['skills'] = {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0},
                                 'description': '已核對NPC技能值，例如dodge；不得推測預設閃避'}
    if _spec.schema['name'] == 'advance_combat_turn':
        _spec.schema['input_schema']['properties'].update({'actor_id': {'type': 'string'}, 'event_id': {'type': 'string'}})
    if _spec.schema['name'] in {'adjust_character', 'adjust_ammo', 'add_status_tag', 'remove_status_tag'}:
        _spec.schema['input_schema']['properties'].update({
            'event_id': {'type': 'string', 'description': '穩定操作識別；重試沿用，相同數值的新操作須用新ID'},
            'reason': {'type': 'string', 'description': '明確的來源與調整原因'},
        })

_SPECS += (
    ToolSpec(schema={
        'name': 'run_combat_effect',
        'description': '恢復已宣告的來源支持incident，沿用效果時點/骰值，不接受新的傷害或結果。',
        'input_schema': {'type': 'object', 'properties': {'combat_id': {'type': 'string'},
                          'effect_id': {'type': 'string'}}, 'required': ['combat_id', 'effect_id']},
    }, handler=managed_handlers.run_combat_effect, creates_check=True, invalidates_combat_status=True),
    ToolSpec(schema={
        'name': 'run_enemy_combat_plan',
        'description': '執行plan_enemy_turn既有來源支持計畫；系統擲骰並等待原玩家防禦，不能外傳命中或傷害。',
        'input_schema': {'type': 'object', 'properties': {'plan_id': {'type': 'string'}}, 'required': ['plan_id']},
    }, handler=managed_handlers.run_enemy_combat_plan, creates_check=True, invalidates_combat_status=True),
    ToolSpec(schema={
        'name': 'request_stabilization_check',
        'description': '宣告自己的急救行動，綁定目前病患與瀕死事項；待玩家擲骰或使用既有autoroll流程。',
        'input_schema': {'type': 'object', 'properties': {
            'healer_character_id': {'type': 'string'}, 'character_id': {'type': 'string'},
            'event_id': {'type': 'string'}, 'reason': {'type': 'string'},
        }, 'required': ['healer_character_id', 'character_id', 'event_id', 'reason']},
    }, handler=managed_handlers.request_stabilization_check, creates_check=True, invalidates_combat_status=True),
    ToolSpec(schema={
        'name': 'stabilize_investigator',
        'description': 'Keeper 依目前時間線已記錄成功急救檢定穩定瀕死調查員；不接受外傳結果、不清除死亡或重傷。',
        'input_schema': {'type': 'object', 'properties': {
            'character_id': {'type': 'string'}, 'source_check_id': {'type': 'string'},
            'event_id': {'type': 'string'}, 'reason': {'type': 'string'},
        }, 'required': ['character_id', 'source_check_id', 'event_id', 'reason']},
    }, handler=managed_handlers.stabilize_investigator, invalidates_combat_status=True),
)

REGISTRY: dict[str, ToolSpec] = {spec.schema["name"]: spec for spec in _SPECS}
if len(REGISTRY) != len(_SPECS):
    raise ValueError("duplicate Keeper tool name")

TOOLS: list[dict[str, Any]] = [
    spec.schema for spec in _SPECS
    if spec.schema["name"] not in {"search_scenario", "report_summary"} and not spec.followup_only
]
SEARCH_SCENARIO_TOOL = REGISTRY["search_scenario"].schema
SUMMARY_TOOL = REGISTRY["report_summary"].schema


def names_with(property_name: str) -> frozenset[str]:
    return frozenset(name for name, spec in REGISTRY.items() if getattr(spec, property_name))


READ_ONLY_TOOL_NAMES = names_with("read_only")
RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES = names_with("resolved_check_followup")
KP_ASSISTANT_ALLOWED_TOOL_NAMES = names_with("kp_assistant")
KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES = names_with("kp_canonical_game")
COMBAT_STATUS_INVALIDATING_TOOLS = names_with("invalidates_combat_status")
BOUNDED_QUERY_TOOLS = names_with("bounded_query")
CHECK_REGISTRATION_TOOLS = names_with("creates_check")
OPENING_TOOL_NAMES = names_with("opening")
CHECK_CREATION_TOOLS = names_with("creates_check")
INFORMATION_QUERY_TOOLS = names_with("information_query")
