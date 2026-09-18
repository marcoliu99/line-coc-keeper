from __future__ import annotations

import logging
from typing import Any

from app import combat, dice
from app.models import GroupState
from app.skill_aliases import canonical_skill_name

_logger = logging.getLogger(__name__)

# Define the condensed high-level tools
HIGH_LEVEL_TOOLS = [
    {
        "name": "mechanic_action",
        "description": "執行所有的骰子檢定與機制動作，包含一般擲骰、技能檢定、理智檢定。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["roll_dice", "skill_check", "sanity_check"],
                    "description": "要執行的動作類型"
                },
                "expression": {"type": "string", "description": "骰子表示式，如 1d100"},
                "skill_name": {"type": "string", "description": "技能名稱（如 偵查、鬥毆）"},
                "target_character": {"type": "string", "description": "要檢定的角色名稱"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "character_action",
        "description": "調整角色的生命值(HP)、理智(SAN)、魔法值(MP)或狀態。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["adjust_hp", "adjust_san", "adjust_mp", "add_status", "remove_status"],
                },
                "target_character": {"type": "string", "description": "要調整的角色名稱"},
                "amount": {"type": "integer", "description": "調整的數值（受傷用負數）"},
                "status_name": {"type": "string", "description": "狀態名稱（如 中毒、重傷）"}
            },
            "required": ["action", "target_character"]
        }
    },
    {
        "name": "inventory_action",
        "description": "給予或移除角色的物品。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add_item", "remove_item"],
                },
                "target_character": {"type": "string", "description": "角色名稱"},
                "item_name": {"type": "string", "description": "物品名稱"}
            },
            "required": ["action", "target_character", "item_name"]
        }
    },
    {
        "name": "combat_action",
        "description": "控制戰鬥流程（開始、推進回合、結束）與戰鬥傷害。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start_combat", "advance_turn", "damage_combatant", "end_combat"],
                },
                "target": {"type": "string", "description": "目標（傷害時使用）"},
                "amount": {"type": "integer", "description": "傷害量（負數）"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "information_action",
        "description": "查詢劇本資訊、玩家記憶或出示圖片。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search_scenario", "search_memory", "show_image"],
                },
                "query": {"type": "string", "description": "查詢關鍵字"},
                "image_name": {"type": "string", "description": "要出示的圖片名稱"}
            },
            "required": ["action"]
        }
    }
]

def execute_tool(state: GroupState, tool_name: str, args: dict[str, Any]) -> str:
    """
    Executes the high-level tool by delegating to the appropriate low-level mechanics.
    Returns a string result to be fed back to the LLM.
    """
    action = args.get("action")
    
    if tool_name == "mechanic_action":
        if action == "roll_dice":
            expr = args.get("expression", "1d100")
            result = dice.roll_expression(expr)
            return f"Roll {expr}: {result['total']} (Details: {result['details']})"
        elif action == "skill_check":
            # Simplified mock for the gateway until full state reducer is wired
            skill = canonical_skill_name(args.get("skill_name", ""))
            return f"Requested skill check for {args.get('target_character')} on {skill}."
        elif action == "sanity_check":
            return f"Requested sanity check for {args.get('target_character')}."
            
    elif tool_name == "character_action":
        target = args.get("target_character")
        amt = args.get("amount", 0)
        return f"Adjusted {target}'s {action} by {amt}."

    elif tool_name == "inventory_action":
        return f"Executed {action} for {args.get('target_character')} on {args.get('item_name')}."

    elif tool_name == "combat_action":
        return f"Executed combat action: {action}."

    elif tool_name == "information_action":
        return f"Executed information action: {action} with query {args.get('query')}."

    return "Unknown tool or action."
