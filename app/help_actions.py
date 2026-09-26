"""Reviewed, executable plans for every Discord Help entry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import GroupState

Mode = Literal["direct", "form", "select", "merge", "sudo"]


@dataclass(frozen=True)
class Field:
    label: str
    required: bool = True
    paragraph: bool = False


@dataclass(frozen=True)
class HelpExecution:
    key: str
    path: tuple[str, str]
    label: str
    mode: Mode
    command: str
    fields: tuple[Field, ...] = ()
    source: str = ""
    confirm: bool = False


def _a(
    key: str, path: str, label: str, mode: Mode, command: str,
    *fields: Field, source: str = "", confirm: bool = False,
) -> HelpExecution:
    category, name = path.split("/")
    return HelpExecution(key, (category, name), label, mode, command, fields, source, confirm)


NAME = Field("角色名稱")
OCCUPATION = Field("職業（可留空）", required=False)
INDEX = Field("編號")
DESCRIPTION = Field("描述", paragraph=True)


ACTIONS: tuple[HelpExecution, ...] = (
    # Characters
    _a("pc", "character/pc", "建立角色", "form", "/coc pc", NAME, OCCUPATION),
    _a("create", "character/create", "開始建角", "form", "/coc create", NAME, OCCUPATION),
    _a("create_status", "character/create", "查看進度", "direct", "/coc create status"),
    _a("create_done", "character/create", "完成建角", "direct", "/coc create done", confirm=True),
    _a("create_cancel", "character/create", "取消建角", "direct", "/coc create cancel", confirm=True),
    _a("pregens", "character/pregens", "查看預製角色", "direct", "/coc pregens"),
    _a("pregen", "character/pregen", "查看預製角色", "select", "/coc pregen", source="pregen"),
    _a("usepregen", "character/usepregen", "使用預製角色", "select", "/coc usepregen",
       Field("自訂名稱（可留空）", required=False), source="pregen", confirm=True),
    _a("sheet", "character/sheet", "查看角色卡", "direct", "/coc sheet"),
    _a("setskill", "character/setskill", "修改技能", "select", "/coc setskill",
       Field("技能名稱"), Field("新數值"), source="character"),
    _a("setconnection", "character/setconnection", "設定背景連結", "select", "/coc setconnection",
       DESCRIPTION, source="character"),
    _a("alloc", "character/alloc", "分配技能點數", "select", "/coc alloc",
       Field("技能名稱"), Field("點數"), source="allocation_pool"),
    _a("characters", "character/characters", "列出我的角色", "direct", "/coc characters"),
    _a("switch", "character/switch", "切換角色", "select", "/coc switch", source="character"),
    _a("retire", "character/retire", "退出角色", "select", "/coc retire",
       source="character", confirm=True),
    # Checks
    _a("check", "check/check", "進行待處理檢定", "select", "/coc check", source="pending_check"),
    _a("autoroll_status", "check/autoroll", "查看自動擲骰", "direct", "/coc autoroll"),
    _a("autoroll_on", "check/autoroll", "開啟自動擲骰", "direct", "/coc autoroll on"),
    _a("autoroll_off", "check/autoroll", "關閉自動擲骰", "direct", "/coc autoroll off"),
    _a("luck_roll", "check/luck", "擲 Luck", "direct", "/coc luck roll"),
    _a("luck_skip", "check/luck", "不花 Luck", "direct", "/coc luck skip"),
    _a("luck_regular", "check/luck", "買到一般成功", "direct", "/coc luck regular"),
    _a("luck_hard", "check/luck", "買到困難成功", "direct", "/coc luck hard"),
    _a("luck_extreme", "check/luck", "買到極限成功", "direct", "/coc luck extreme"),
    # Combat
    _a("combat_start", "combat/start", "開始戰鬥", "direct", "/coc combat start", confirm=True),
    _a("combat_addnpc", "combat/addnpc", "加入敵人", "form", "/coc combat addnpc",
       Field("名稱"), Field("DEX"), Field("HP"), confirm=True),
    _a("combat_addally", "combat/addally", "加入友軍", "form", "/coc combat addally",
       Field("名稱"), Field("DEX"), Field("HP"), confirm=True),
    _a("combat_status", "combat/status", "查看戰鬥狀態", "direct", "/coc combat status"),
    _a("combat_next", "combat/next", "推進回合", "direct", "/coc combat next", confirm=True),
    _a("combat_damage", "combat/damage", "調整 HP", "select", "/coc combat damage",
       Field("HP 增減量（負數為傷害）"), source="combatant", confirm=True),
    _a("combat_end", "combat/end", "結束戰鬥", "direct", "/coc combat end", confirm=True),
    # Maps
    _a("showpage", "map/showpage", "輸入劇本頁碼", "form", "/coc showpage", Field("頁碼")),
    _a("showpage_map", "map/showpage", "選已知地圖頁", "select", "/coc showpage", source="map"),
    _a("where", "map/where", "查看位置", "direct", "/coc where"),
    _a("enter", "map/enter", "進入地圖", "select", "/coc enter", source="map"),
    _a("leavemap", "map/leavemap", "離開地圖", "direct", "/coc leavemap", confirm=True),
    # Scenario and game state
    _a("newgame", "scenario/newgame", "開始新遊戲", "direct", "/coc newgame", confirm=True),
    _a("scenario_list", "scenario/list", "列出劇本庫", "direct", "/coc scenario list"),
    _a("scenario_use", "scenario/use", "選擇劇本", "select", "/coc scenario use",
       source="scenario", confirm=True),
    _a("cards_list", "scenario/cards", "列出手動角色卡", "select",
       "/coc scenario cards list", source="scenario"),
    _a("cards_delete", "scenario/cards", "刪除手動角色卡", "select",
       "/coc scenario cards delete", source="scenario_card", confirm=True),
    _a("scenario_reparse", "scenario/reparse", "重新解析", "direct",
       "/coc scenario reparse", confirm=True),
    _a("scenario_cancel", "scenario/cancel", "取消劇本處理", "direct",
       "/coc scenario cancel", confirm=True),
    _a("scenario_clean", "scenario/clean", "清理劇本項目", "select",
       "/coc scenario clean", source="scenario", confirm=True),
    _a("scenario_import", "scenario/import", "匯入伺服器 PDF", "select",
       "/coc scenario import", source="import_pdf", confirm=True),
    _a("scenario_merge", "scenario/merge", "合併暫存 PDF", "merge",
       "/coc scenario merge", confirm=True),
    _a("scenario_merge_list", "scenario/merge", "查看暫存 PDF", "direct",
       "/coc scenario merge list"),
    _a("pdf_new", "scenario/pdf", "作為新劇本", "direct", "/coc pdf new", confirm=True),
    _a("pdf_fix", "scenario/pdf", "修正目前劇本", "direct", "/coc pdf fix", confirm=True),
    _a("status", "scenario/status", "查看遊戲狀態", "direct", "/coc status"),
    _a("start", "scenario/start", "開始劇情", "direct", "/coc start"),
    _a("end", "scenario/end", "結束遊戲", "direct", "/coc end", confirm=True),
    _a("setpersona", "scenario/setpersona", "設定風格", "form",
       "/coc setpersona", DESCRIPTION),
    _a("setpersona_status", "scenario/setpersona", "查看目前風格", "direct",
       "/coc setpersona"),
    _a("setpersona_reset", "scenario/setpersona", "重設風格", "direct",
       "/coc setpersona reset", confirm=True),
    _a("era_1920", "scenario/era", "1920 年代", "direct", "/coc era 1920"),
    _a("era_modern", "scenario/era", "現代", "direct", "/coc era modern"),
    _a("era_status", "scenario/era", "查看目前年代", "direct", "/coc era"),
    _a("index", "scenario/index", "重建劇本索引", "direct", "/coc index", confirm=True),
    _a("away", "scenario/away", "暫離遊戲", "direct", "/coc away"),
    _a("back", "scenario/back", "回到遊戲", "direct", "/coc back"),
    # KP
    _a("kp", "kp/kp", "登記 KP Assistant", "direct", "/coc kp"),
    _a("kp_quit", "kp/kp", "解除 KP 身分", "direct", "/coc kp quit", confirm=True),
    _a("sudo", "kp/sudo", "代玩家操作", "sudo", "/coc sudo", confirm=True),
    _a("checkpoint", "kp/checkpoint", "建立回溯節點", "form",
       "/coc checkpoint", Field("名稱（可留空）", required=False)),
    _a("checkpoint_clean", "kp/checkpoint", "清除回溯節點", "select",
       "/coc checkpoint clean", source="checkpoint", confirm=True),
    _a("checkpoints", "kp/checkpoints", "查看回溯節點", "direct", "/coc checkpoints"),
    _a("rollback", "kp/rollback", "回溯遊戲", "select",
       "/coc rollback", source="checkpoint", confirm=True),
    _a("digest", "kp/digest", "查看最新摘要", "direct", "/coc digest"),
    _a("digest_select", "kp/digest", "查看歷史摘要", "select",
       "/coc digest", source="digest"),
    _a("digest_clean", "kp/digest", "清除摘要", "select",
       "/coc digest clean", source="digest", confirm=True),
    _a("digests", "kp/digests", "列出場景摘要", "direct", "/coc digests"),
    # Other
    _a("roll", "other/roll", "擲骰", "form", "/roll", Field("骰式")),
)

BY_KEY = {action.key: action for action in ACTIONS}


def actions_for(path: tuple[str, ...]) -> tuple[HelpExecution, ...]:
    return tuple(action for action in ACTIONS if action.path == path)


def validate_coverage(paths: set[tuple[str, ...]]) -> None:
    if len(BY_KEY) != len(ACTIONS):
        raise ValueError("duplicate Help action key")
    actual = {action.path for action in ACTIONS}
    if actual != paths:
        raise ValueError(f"Help action coverage mismatch: missing={paths - actual}, extra={actual - paths}")


def options_for(source: str, state: GroupState, user_id: str) -> list[tuple[str, str]]:
    """Return fresh (label, command argument) options for a Help picker."""
    if source == "pregen":
        return [(f"{i}. {item.get('name') or '未命名'}", str(i))
                for i, item in enumerate(state.pregens, 1)]
    if source == "character":
        return [(char.name, char.name) for char in state.characters_for_owner(user_id)]
    if source == "allocation_pool":
        return [("職業技能點", "occ"), ("興趣技能點", "int")]
    if source == "combatant":
        return [(item.display_name, item.combatant_id or item.display_name) for item in state.combat.order]
    if source == "map":
        return [(key, key) for key in sorted(state.scene_maps)]
    if source == "scenario":
        from app import scenario_library
        return [(f"{item.get('title') or '未命名'} ({item['id']})", str(item["id"]))
                for item in scenario_library.list_scenarios() if item.get("id")]
    if source == "import_pdf":
        from pathlib import Path

        from app.config import IMPORT_DIR
        directory = Path(IMPORT_DIR)
        if not directory.is_dir():
            return []
        return [(item.name, item.name) for item in sorted(directory.iterdir())
                if item.is_file() and item.suffix.casefold() == ".pdf"]
    if source == "checkpoint":
        from app import checkpoints
        return [(str(item.get("label") or item["checkpoint_id"]), str(item["checkpoint_id"]))
                for item in checkpoints.list_checkpoints(state.group_id)]
    if source == "digest":
        from app import scene_digest
        return [(str(item.get("scene_label") or item["digest_id"]), str(item["digest_id"]))
                for item in scene_digest.list_digests(state.group_id)]
    if source == "pending_check":
        pending = state.pending_checks.get(user_id)
        if not pending:
            return []
        if pending.get("type") == "choice":
            return [(str(option.get("label") or option.get("skill") or i), str(option.get("label") or option.get("skill") or ""))
                    for i, option in enumerate(pending.get("options", []), 1)
                    if isinstance(option, dict)]
        return [(str(pending.get("skill") or "擲骰"), "")]
    if source == "scenario_card":
        from app import scenario_library
        from app.repositories import manual_pregens
        items: list[tuple[str, str]] = []
        for scenario in scenario_library.list_scenarios():
            scenario_id = scenario.get("id")
            if not isinstance(scenario_id, str):
                continue
            for asset in manual_pregens.list_assets(state.group_id, scenario_id):
                asset_id = asset.get("asset_id")
                if isinstance(asset_id, str):
                    name = asset.get("pregen", {}).get("name") or asset_id
                    items.append((f"{scenario.get('title') or scenario_id}: {name}", f"{scenario_id} {asset_id}"))
        return items
    raise ValueError(f"unknown Help action source: {source}")


def build_command(action: HelpExecution, selected: str = "", field_values: tuple[str, ...] = ()) -> str:
    """Compose only a registered command prefix and validated arguments."""
    if len(field_values) != len(action.fields):
        raise ValueError("wrong number of fields")
    values: list[str] = []
    if selected:
        values.append(selected)
    for field, raw in zip(action.fields, field_values, strict=True):
        value = " ".join(raw.strip().split())
        if field.required and not value:
            raise ValueError(f"請輸入{field.label}。")
        if len(value) > 200:
            raise ValueError(f"{field.label}過長。")
        if value and not field.paragraph and any(char.isspace() for char in value):
            raise ValueError(f"{field.label}不能包含空白。")
        if value:
            values.append(value)
    if any(char in selected for char in "\r\n\t"):
        raise ValueError("選項包含不合法字元。")
    return " ".join((action.command, *values))
