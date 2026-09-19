"""Central registration point for player-facing Discord help."""
from __future__ import annotations

from app.help_registry import HelpCategory, HelpEntry, register_help_category, register_help_entries


def _register_categories() -> None:
    for category in (
        HelpCategory("character", "角色", "建立、查看與管理調查員。", 10),
        HelpCategory("check", "檢定", "技能、理智與 Luck 檢定。", 20),
        HelpCategory("combat", "戰鬥", "戰鬥流程與 HP 管理。", 30),
        HelpCategory("map", "地圖", "查看與操作劇本地圖。", 40),
        HelpCategory("scenario", "劇本", "劇本、遊戲生命週期與場景設定。", 50),
        HelpCategory("kp", "KP 助手", "共同主持與 KP 專用功能。", 60),
        HelpCategory("other", "其他", "骰子與其他設定。", 70),
    ):
        register_help_category(category)


def _entries() -> list[HelpEntry]:
    return [
        HelpEntry(("character", "pc"), "character", "快速建立調查員", "建立一位自訂調查員。", ("/coc pc 角色名 [職業]",), ("/coc pc 小明 記者",), visibility="when_no_pregens", command=("pc",)),
        HelpEntry(("character", "create"), "character", "互動式建立調查員", "先擲屬性，再分配職業與興趣技能點數。", ("/coc create 角色名 [職業]", "/coc alloc occ|int 技能名 點數", "/coc create status|done|cancel"), ("/coc create 小明 記者",), visibility="when_no_pregens", command=("create",)),
        HelpEntry(("character", "pregens"), "character", "查看預設角色", "查看劇本附帶的預製調查員。", ("/coc pregens",), ("/coc pregens",), visibility="when_scenario_loaded", command=("pregens",)),
        HelpEntry(("character", "pregen"), "character", "查看預設角色詳情", "查看某位預製調查員的完整能力。", ("/coc pregen 編號",), ("/coc pregen 1",), visibility="when_pregens_exist", command=("pregen",)),
        HelpEntry(("character", "usepregen"), "character", "使用預設角色", "選擇一位劇本附帶的預製調查員。", ("/coc usepregen 編號 [自訂名稱]",), ("/coc usepregen 1",), visibility="when_pregens_exist", command=("usepregen",)),
        HelpEntry(("character", "sheet"), "character", "查看角色卡", "查看自己的調查員角色卡。", ("/coc sheet",), ("/coc sheet",), command=("sheet",)),
        HelpEntry(("character", "setskill"), "character", "修改技能", "手動修正自己角色的技能值。", ("/coc setskill 角色名 技能名 數值",), command=("setskill",)),
        HelpEntry(("character", "setconnection"), "character", "設定關鍵背景連結", "設定角色最重要的人、地或物。", ("/coc setconnection 角色名 敘述",), command=("setconnection",)),
        HelpEntry(("character", "alloc"), "character", "分配技能點數", "在互動式建角流程中分配技能點數。", ("/coc alloc occ|int 技能名 點數",), command=("alloc",)),
        HelpEntry(("check", "check"), "check", "技能或理智檢定", "自己擲出守密人要求的檢定，也可主動指定技能。", ("/coc check [技能名] [獎勵骰數] [懲罰骰數]",), ("/coc check 偵查",), command=("check",)),
        HelpEntry(("check", "luck"), "check", "Luck 結果選擇", "選擇是否花費 Luck 改善接近成功的結果。", ("/coc luck skip|regular|hard|extreme",), ("/coc luck regular",), command=("luck",)),
        HelpEntry(("combat", "start"), "combat", "開始戰鬥", "依 DEX 建立戰鬥先攻順位。", ("/coc combat start",), command=("combat", "start")),
        HelpEntry(("combat", "addnpc"), "combat", "加入敵人", "將 NPC／敵人加入目前戰鬥。", ("/coc combat addnpc 名稱 DEX HP",), command=("combat", "addnpc")),
        HelpEntry(("combat", "addally"), "combat", "加入友方 NPC", "將站在我方的 NPC 隊友加入戰鬥。", ("/coc combat addally 名稱 DEX HP",), command=("combat", "addally")),
        HelpEntry(("combat", "status"), "combat", "查看戰鬥狀態", "查看回合與先攻順位。", ("/coc combat status",), command=("combat", "status")),
        HelpEntry(("combat", "next"), "combat", "推進回合", "推進到下一位的回合。", ("/coc combat next",), command=("combat", "next")),
        HelpEntry(("combat", "damage"), "combat", "調整戰鬥 HP", "對戰鬥中的角色或 NPC 套用 HP 增減。", ("/coc combat damage 名稱 增減量",), ("/coc combat damage 深潛者 -4",), command=("combat", "damage")),
        HelpEntry(("combat", "end"), "combat", "結束戰鬥", "結束目前的戰鬥並清除戰鬥狀態。", ("/coc combat end",), command=("combat", "end")),
        HelpEntry(("map", "showpage"), "map", "查看劇本頁面", "查看劇本某一頁的圖片。", ("/coc showpage 頁碼",), ("/coc showpage 16",), command=("showpage",)),
        HelpEntry(("map", "where"), "map", "查看所在位置", "查看地圖引擎追蹤的房間與出口。", ("/coc where",), command=("where",)),
        HelpEntry(("map", "enter"), "map", "進入地圖", "手動進入某一頁的平面圖。", ("/coc enter 頁碼",), command=("enter",)),
        HelpEntry(("map", "leavemap"), "map", "離開地圖追蹤", "離開目前地圖，移動改回由守密人判斷。", ("/coc leavemap",), command=("leavemap",)),
        HelpEntry(("scenario", "newgame"), "scenario", "開始新遊戲", "重置群組狀態並開始新的一局。", ("/coc newgame",), command=("newgame",)),
        HelpEntry(("scenario", "list"), "scenario", "列出劇本庫", "查看可用劇本與目前使用中的劇本。", ("/coc scenario list",), ("/coc scenario list",), command=("scenario", "list")),
        HelpEntry(("scenario", "use"), "scenario", "選用劇本", "從劇本庫選擇目前要使用的劇本。", ("/coc scenario use 劇本ID",), ("/coc scenario use abc123",), notes=("KP-only：只有目前登記的 KP Assistant 可以執行。",), kp_only=True, command=("scenario", "use")),
        HelpEntry(("scenario", "reparse"), "scenario", "重新解析劇本", "重新處理等待中的相似劇本 PDF。", ("/coc scenario reparse",), command=("scenario", "reparse")),
        HelpEntry(("scenario", "cancel"), "scenario", "取消劇本處理", "放棄目前等待處理的相似劇本 PDF。", ("/coc scenario cancel",), command=("scenario", "cancel")),
        HelpEntry(("scenario", "clean"), "scenario", "清理劇本庫", "刪除沒有被任何群組使用的劇本庫項目。", ("/coc scenario clean 劇本ID",), command=("scenario", "clean")),
        HelpEntry(("scenario", "pdf"), "scenario", "處理劇本 PDF", "決定上傳的 PDF 是新劇本或修正目前劇本。", ("/coc pdf new|fix",), command=("pdf",)),
        HelpEntry(("scenario", "status"), "scenario", "查看遊戲狀態", "查看目前劇本與角色狀態。", ("/coc status",), command=("status",)),
        HelpEntry(("scenario", "start"), "scenario", "開始劇情", "角色準備好後，產生劇本開場白。", ("/coc start",), ("/coc start",), visibility="when_scenario_loaded", command=("start",)),
        HelpEntry(("scenario", "end"), "scenario", "結束遊戲", "結束目前這局遊戲。", ("/coc end",), command=("end",)),
        HelpEntry(("scenario", "setpersona"), "scenario", "設定守密人風格", "自訂或重設守密人的敘事風格。", ("/coc setpersona 文字", "/coc setpersona reset"), command=("setpersona",)),
        HelpEntry(("scenario", "era"), "scenario", "設定年代", "設定 1920 年代或現代背景。", ("/coc era 1920|modern",), command=("era",)),
        HelpEntry(("scenario", "index"), "scenario", "重建劇本索引", "手動重建 NPC／怪物與地點索引。", ("/coc index",), visibility="when_scenario_loaded", command=("index",)),
        HelpEntry(("scenario", "away"), "scenario", "暫離遊戲", "標記自己暫時離開；戰鬥中會跳過你的回合。", ("/coc away",), command=("away",)),
        HelpEntry(("scenario", "back"), "scenario", "回到遊戲", "取消暫離狀態並恢復正常參與。", ("/coc back",), command=("back",)),
        HelpEntry(("kp", "kp"), "kp", "登記 KP Assistant", "登記或解除本局的 KP Assistant 身分。", ("/coc kp", "/coc kp quit"), command=("kp",)),
        HelpEntry(("other", "roll"), "other", "單純擲骰", "不經過守密人，直接擲骰。", ("/roll 1d100", "/roll 3d6+2"), ("/roll 1d100",), command=()),
    ]


def register_all_help() -> None:
    from app.help_registry import registry_is_initialized

    if registry_is_initialized():
        return
    _register_categories()
    register_help_entries(_entries())
