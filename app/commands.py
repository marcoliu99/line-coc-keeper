"""Platform-agnostic command handling and message routing.

Shared by every front-end adapter (app/main.py for LINE, app/discord_bot.py for
Discord, ...). Nothing in here knows about LINE or Discord — it only deals with
plain strings (conversation_id, user_id, text) and things the adapter supplies:
a `reply` callback to send text back to the conversation, a `get_display_name`
callback (async, since some platforms need an API call for it and some don't),
a `send_dm` callback for whispering a private message to one specific player
(used by the Keeper's send_private_info tool — see app/keeper.py), and
`send_image`/`send_dm_image` for posting a scenario page's actual picture
(publicly or privately) — used by /coc showpage and the Keeper's
show_scenario_image tool, so a map or handout can be shown as a real image
instead of just the Keeper's text description of it.

Per-conversation locking (app/locks.py) is handled here, not by each adapter,
since it protects GroupState file I/O — a game-state concern, not a platform
one. Each top-level entry point below acquires its conversation's lock exactly
once for its full duration (including the blocking Keeper LLM call, offloaded
via asyncio.to_thread); the private helpers they call never lock again
themselves, since asyncio.Lock isn't reentrant.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from app import combat, creation, dice, intent_parser, keeper, locks, luck, pdf_loader, pregen_extractor
from app import scenario_compare, scenario_rag
from app import scene_map as scene_map_engine
from app.config import SCENARIO_RAG_ENABLED
from app.models import OCCUPATIONS, GroupState, generate_investigator
from app.state import clear_page_images, load_page_image, load_state, save_page_image, save_state

_logger = logging.getLogger(__name__)

Reply = Callable[[str], Awaitable[None]]
GetDisplayName = Callable[[], Awaitable[str]]
SendDM = Callable[[str, str], Awaitable[None]]  # (owner_id, text) -> None
# (png_bytes, conversation_id, page_number) -> None, posts publicly. conversation_id
# and page_number are included alongside the raw bytes because LINE can't attach
# bytes directly to an image message — it needs a real HTTPS URL, which its
# adapter builds by pointing back at this server's own /images/... route (see
# app/main.py) rather than using png_bytes at all; Discord's adapter just
# attaches png_bytes and ignores the other two.
SendImage = Callable[[bytes, str, int], Awaitable[None]]
SendDMImage = Callable[[str, bytes, str, int], Awaitable[None]]  # (owner_id, png_bytes, conversation_id, page_number)

HELP_TEXT = """【COC7e 守密人 Bot 指令】
・上傳一份 PDF 劇本檔案 → 載入劇本並開始遊戲

【建立角色，三選一】
・/coc pc 角色名 [職業] → 快速隨機生成一位調查員（一鍵完成，每個人在同一局只能建一次，直到 /coc end）
  可選職業：""" + "、".join(OCCUPATIONS.keys()) + """
・/coc create 角色名 [職業] → 互動式建角：先擲屬性，再自己分配職業/興趣技能點數
  接著用 /coc alloc occ|int 技能名 點數 分配，/coc create status 查看進度，
  /coc create done 完成、/coc create cancel 放棄
・/coc pregens → 查看這份劇本有沒有附帶的預製調查員
・/coc pregen 編號 → 選之前先看某位預製角色的完整屬性與技能
・/coc usepregen 編號 [自訂名稱] → 直接使用某位預製角色（每個人只能用一次，直到 /coc end；每個角色只能被一人選走）

【檢定】
・/coc check → 守密人請你檢定時，自己擲骰（不是守密人幫你骰）；也可以自己主動打 /coc check 技能名 [獎勵骰數] [懲罰骰數]
・如果守密人給的是「閃避 vs 反擊」這種多選一的檢定，用 /coc check <選項名稱> 指定要選哪個（Discord 會直接看到對應的按鈕）
・擲骰結果離成功很近時（差 7 點以內），系統會主動問要不要花 Luck 買到更好的結果，點按鈕或用 /coc luck skip|regular|hard|extreme 回應

【角色管理】
・/coc sheet → 查看自己的角色卡
・/coc status → 查看目前劇本與所有角色狀態
・/coc setskill 角色名 技能名 數值 → 手動修正自己角色的技能值
・/coc setconnection 角色名 敘述 → 設定「★ 關鍵背景連結」（你最重要的人/地/物，守密人不能沒收你搶救的機會）
・/coc away → 標記自己暫離（戰鬥中會自動跳過你的回合）；/coc back → 回來繼續玩
・/coc showpage 頁碼 → 直接看劇本某一頁的實際圖片（地圖、手卡等），守密人提到「第 X 頁」時可以用
・/coc where → 查看地圖引擎追蹤中的目前所在房間與出口
・/coc enter 頁碼 → 手動進入某一頁的平面圖（通常會自動偵測，這是備用手動指令）
・/coc leavemap → 離開目前的地圖追蹤，移動改回完全由守密人判斷

【戰鬥】
・/coc combat start → 開始正式戰鬥（依 DEX 排先攻順位）
・/coc combat addnpc 名稱 DEX HP → 加入一個敵人
・/coc combat addally 名稱 DEX HP → 加入一個站在我方的 NPC 隊友
・/coc combat status → 查看目前回合與先攻順位
・/coc combat next → 推進到下一位的回合
・/coc combat damage 名稱 增減量 → 調整某人的 HP（受傷用負數）
・/coc combat end → 結束戰鬥

【其他】
・/coc newgame → 重置這個群組，開始全新一局
・/coc end → 結束目前這局遊戲
・/roll 1d100 或 /roll 3d6+2 → 單純擲骰，不經過守密人

角色建立好之後，直接在群組裡輸入你的行動或對話，守密人就會接手描述！"""


async def handle_unsupported_message(conversation_id: str, reply: Reply, label: str) -> None:
    """Called by an adapter when it receives a message type it can't hand text
    or a PDF from (sticker, image, voice, etc.) — only speaks up once a game is
    actually active, to avoid being noisy in unrelated chat."""
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        active = state.active
    if active:
        await reply(
            f"（守密人目前只讀得懂文字訊息和 PDF 檔案，收到的{label}不會被處理；"
            "如果裡面有重要內容，麻煩用文字描述一下發生了什麼事。）"
        )


async def handle_pdf_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    pdf_bytes: bytes,
    file_name: str,
) -> None:
    """`reply` must land inside whatever immediate response window the platform
    gives an incoming event (LINE's reply token expires after 60s and is
    single-use); `push` is for the actual result, sent once extraction — which
    can run a vision/OCR pass over every graphic-heavy page and, on a
    picture-heavy scenario, comfortably exceed that window — finishes. On a
    platform with no such constraint (Discord), an adapter can just pass the
    same callback for both."""
    if not file_name.lower().endswith(".pdf"):
        await reply("目前只支援上傳 PDF 劇本檔案喔。")
        return

    await reply("收到了，正在讀取劇本內容（圖片較多的劇本可能要一分鐘左右），請稍候...")

    try:
        text, low_text_pages, truncated, page_images, page_maps = await asyncio.to_thread(
            pdf_loader.extract_text, pdf_bytes
        )
    except ValueError as exc:
        await push(f"讀取 PDF 失敗：{exc}")
        return

    title = pdf_loader.guess_title(text, file_name=file_name)

    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        state.scenario_text = text
        state.scenario_title = title
        state.active = True
        state.pregens = []  # clear the previous scenario's cached pregens — otherwise
        # a group that switches PDFs without running /coc newgame first would keep
        # seeing (and could even build a character off) the old scenario's pregens.
        state.scene_maps = {str(k): v for k, v in page_maps.items()}  # same reasoning —
        # don't let a new scenario keep the old one's floor plans (see app/scene_map.py).
        state.current_map_page = {}
        state.current_room_id = {}
        state.party_facing = {}
        save_state(state)
        clear_page_images(conversation_id)  # same reasoning — don't let a new
        # scenario's /coc showpage 5 show the OLD scenario's page 5.
        for page_number, png_bytes in page_images.items():
            save_page_image(conversation_id, page_number, png_bytes)

    warning = ""
    if low_text_pages:
        pages_str = "、".join(str(p) for p in low_text_pages)
        warning += (
            f"\n\n⚠️ 第 {pages_str} 頁偵測到文字內容偏少（可能是地圖、手卡或圖片化的內容），"
            "已嘗試自動辨識，但仍建議你人工核對一下；如果有遺漏的重要線索，"
            "之後可以直接把那頁的文字內容貼在群組訊息裡讓守密人知道。"
        )
    if truncated:
        warning += (
            f"\n\n⚠️ 這份劇本內容超過長度上限（{len(text)} 字），後半段已經被截斷，"
            "守密人不會知道被截掉的內容；如果是很長的戰役合集，建議拆成幾份小一點的 PDF 分批上傳。"
        )
    map_note = ""
    if page_maps:
        pages_str = "、".join(str(p) for p in sorted(page_maps.keys()))
        map_note = (
            f"\n\n🗺️ 第 {pages_str} 頁偵測到平面圖，已經拆解成房間圖——玩家在裡面移動時"
            "（例如「進入燈塔，檢查右手邊第一個房間」）系統會直接算出正確房間，不用靠守密人自己猜方位。"
            "用 `/coc where` 可以看目前在哪個房間。"
        )

    await push(
        f"已載入劇本《{title}》（{len(text)} 字）。\n"
        "這份劇本如果有附帶預製調查員，建議先輸入「/coc pregens」看看有哪些角色可選、"
        "「/coc pregen 編號」看某位的完整能力——"
        "有內建角色的話，「/coc pc 角色名 職業」就只能從那些角色裡選一個。\n"
        "如果這份劇本沒有內建角色（或想先跳過這步），可以直接用「/coc pc 角色名 職業」"
        "快速生成，這時職業可選：\n"
        + "、".join(OCCUPATIONS.keys())
        + "\n建好角色後，直接在群組打字描述行動即可開始冒險！"
        + warning
        + map_note,
    )


async def handle_map_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    yaml_bytes: bytes,
    file_name: str,
) -> None:
    """A hand-authored alternative to app/scene_map.py's vision-extracted room
    graphs — same reply/push split as handle_pdf_upload above, though parsing
    a small YAML file is fast enough that both callbacks will usually land at
    the same time on any platform. Stored under a "custom_<filename>" key
    (never a bare digit, so it can't collide with a PDF page-number key) —
    `/coc enter custom_<filename>` loads it exactly like any extracted map."""
    try:
        data = yaml.safe_load(yaml_bytes)
    except yaml.YAMLError as exc:
        await reply(f"YAML 格式錯誤，請檢查語法：{exc}")
        return

    import_warnings: list[str] = []
    if isinstance(data, dict) and "nodes" in data and "rooms" not in data:
        # A different, richer authoring convention (node-graph: distances, named
        # routes, terrain, undirected "nearby" links) than this module's native
        # rooms/exits shape — see scene_map.import_node_graph's own docstring.
        data, import_warnings = scene_map_engine.import_node_graph(data)

    errors = scene_map_engine.validate_scene_map(data)
    if errors:
        error_list = "\n".join(f"・{e}" for e in errors)
        await reply(f"這份地圖資料有問題，尚未儲存：\n{error_list}")
        return

    key = f"custom_{Path(file_name).stem}"
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        state.scene_maps[key] = data
        save_state(state)

    entry_room = scene_map_engine.get_room(data, data.get("entry_room_id", ""))
    entry_note = f"，入口房間「{entry_room['name']}」" if entry_room else ""
    warning_note = ""
    if import_warnings:
        warning_note = "\n\n⚠️ 轉換時有幾個地方略過了：\n" + "\n".join(f"・{w}" for w in import_warnings)
    await push(
        f"地圖「{data.get('location_name') or key}」已儲存（{len(data['rooms'])} 個房間{entry_note}）。\n"
        f"用「/coc enter {key}」載入這張地圖。" + warning_note
    )


async def handle_scenario_compare_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    alt_text: str,
    file_name: str,
) -> None:
    """GM-triggered QA check — compares this bot's own extracted scenario_text
    against an independently-produced alternate parse of the same PDF (see
    app/scenario_compare.py), to catch content our pipeline missed or
    garbled. Read-only: doesn't touch GroupState, so no lock/save needed."""
    state = load_state(conversation_id)
    if not state.scenario_text.strip():
        await reply("目前還沒有載入任何劇本，請先上傳劇本 PDF，才有東西可以比對。")
        return

    await reply("收到了，正在比對兩份擷取結果，請稍候...")
    discrepancies = await asyncio.to_thread(scenario_compare.compare_scenario_text, state.scenario_text, alt_text)
    if not discrepancies:
        await push("比對完成，沒有發現明顯的實質內容落差。")
        return

    lines = [f"・{d.get('location_hint', '')}：{d.get('issue', '')}" for d in discrepancies]
    await push(f"比對完成，發現 {len(discrepancies)} 處可能的落差：\n" + "\n".join(lines))


async def handle_role_sheet_upload(
    conversation_id: str,
    reply: Reply,
    file_text: str,
    file_name: str,
) -> None:
    """A hand-transcribed pregen character sheet (【角色資料】/【屬性】/【技能】/...
    sections — see pregen_extractor.parse_role_sheet_text), uploaded as a
    "role_"-prefixed .txt/.md attachment — see app/discord_bot.py's on_message,
    which is what tells this apart from handle_scenario_compare_upload's
    full-scenario alternate-text attachments. A deterministic, GM-verified
    alternative to /coc pregens' LLM-based extraction from the raw scenario
    PDF text. Re-uploading a corrected sheet for the same occupation replaces
    the previous entry rather than duplicating it."""
    pregen = pregen_extractor.parse_role_sheet_text(file_text)
    if pregen is None:
        await reply(f"「{file_name}」看起來不是預期的角色卡格式（找不到【屬性】區塊），沒有儲存。")
        return

    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        existing_index = next(
            (i for i, p in enumerate(state.pregens) if p.get("occupation") == pregen["occupation"]), None
        )
        if existing_index is not None:
            state.pregens[existing_index] = pregen
        else:
            state.pregens.append(pregen)
        save_state(state)

    name_note = f"「{pregen['name']}」" if pregen["name"] else "（姓名由玩家決定）"
    action = "已更新" if existing_index is not None else "已新增"
    await reply(
        f"角色卡{action}：{name_note}，職業「{pregen['occupation']}」，"
        f"{len(pregen['skills'])} 項技能。用「/coc pregens」查看目前所有預製角色。"
    )


async def handle_roll_command(reply: Reply, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await reply("用法：/roll 1d100 或 /roll 3d6+2")
        return
    try:
        result = dice.roll_expression(parts[1].strip())
    except ValueError as exc:
        await reply(str(exc))
        return
    await reply(f"🎲 {result.describe()}")


async def _deliver_side_effects(
    conversation_id: str,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
) -> None:
    """Shared by handle_text_message and handle_check_command — delivers
    whatever the Keeper queued via send_private_info/show_scenario_image.
    Must be called outside any conversation lock: pure I/O, no state access.
    Best-effort: a DM can fail (LINE requires the player to have friended the
    bot; Discord requires them to allow DMs from server members) and we
    deliberately don't fall back to posting the content publicly, since that
    would defeat the entire point of it being private."""
    for owner_id, message in private_messages:
        try:
            await send_dm(owner_id, f"🤫（私訊）{message}")
        except Exception:
            _logger.exception("send_dm failed for owner_id=%s in conversation_id=%s", owner_id, conversation_id)

    for owner_id, page_number in image_requests:
        png_bytes = load_page_image(conversation_id, page_number)
        if not png_bytes:
            continue  # Keeper referenced a page with no stored image — quietly skip
        try:
            if owner_id:
                await send_dm_image(owner_id, png_bytes, conversation_id, page_number)
            else:
                await send_image(png_bytes, conversation_id, page_number)
        except Exception:
            _logger.exception(
                "send_dm_image/send_image failed for owner_id=%s in conversation_id=%s", owner_id, conversation_id
            )


def _skill_names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


_CHECK_TIER_ZH = {
    "fumble": "大失敗", "fail": "失敗", "regular": "成功",
    "hard": "困難成功", "extreme": "極難成功", "critical": "大成功",
}


def _describe_opposed_outcome(defender_name: str, is_counter: bool, defender_tier: str, attacker_tier: str) -> str:
    """COC7e opposed-roll narration for a Dodge/Fight Back choice (see
    dice.resolve_opposed) — always names both sides' tiers explicitly rather
    than just stating the verdict, so it's auditable in the channel, not a
    black box."""
    outcome = dice.resolve_opposed(defender_tier, attacker_tier)
    attacker_zh = _CHECK_TIER_ZH[attacker_tier]
    if outcome == "both_miss":
        return f"對抗檢定：攻擊方「{attacker_zh}」，雙方都沒成功，這次攻擊沒有命中，{defender_name}沒有受傷，也沒有造成傷害。"
    if outcome == "defender_wins":
        if is_counter:
            return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級更高，攻擊被化解，反擊命中，可以對攻擊方造成傷害。"
        return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級更高，成功閃避，沒有受到傷害。"
    tie_note = "（平手，依規則攻擊方獲勝）" if outcome == "tie_attacker_wins" else ""
    counter_note = "，反擊沒有生效" if is_counter else ""
    return f"對抗檢定：攻擊方「{attacker_zh}」，攻擊方成功等級較高{tie_note}，攻擊命中，{defender_name}受到傷害{counter_note}。"


def _build_check_narration(
    char, skill_name: str, display_label: str | None, value: int, r, bonus: int, penalty: int,
    luck_spent: int = 0, original_tier: str | None = None, attacker_tier: str | None = None,
) -> tuple[str, str]:
    """Builds (roll_line, keeper_message) for a resolved skill/choice check —
    shared by the immediate-finalize path and handle_luck_decision (after a
    Luck spend has overridden r.tier). luck_spent > 0 adds a note both humans
    and the Keeper can see that the tier was bought up, not rolled naturally.
    attacker_tier (only set for a Dodge/Fight Back choice — see
    keeper.py's offer_check_choice/npc_skill_check) triggers the COC7e
    opposed-roll comparison, named explicitly in both messages."""
    tier_zh = _CHECK_TIER_ZH[r.tier]
    dice_note = f"（獎勵骰x{bonus}）" if bonus else f"（懲罰骰x{penalty}）" if penalty else ""
    luck_note = ""
    if luck_spent:
        luck_note = f"（花費 {luck_spent} 點 Luck，將結果從「{_CHECK_TIER_ZH[original_tier]}」提升為「{tier_zh}」）"

    opposed_line = ""
    opposed_message = ""
    if attacker_tier is not None:
        is_counter = display_label is not None and "反擊" in display_label
        opposed_text = _describe_opposed_outcome(char.name, is_counter, r.tier, attacker_tier)
        opposed_line = f"\n⚔️ {opposed_text}"
        opposed_message = f"（{opposed_text}）"

    if display_label is not None:
        roll_line = f"🎲 {char.name} 選擇「{display_label}」（{skill_name} {value}%{dice_note}），擲出 {r.roll} → {tier_zh}{luck_note}{opposed_line}"
        keeper_message = (
            f"（{char.name} 在多個選項裡選了「{display_label}」，擲骰做了一次「{skill_name}」檢定："
            f"技能值 {value}%{dice_note}，擲出 {r.roll} → {tier_zh}{luck_note}。這是已經確定的結果，請根據這個結果"
            f"描述後續發展，不要重新判定或改變這個結果，也不要質疑玩家選了哪個選項。）{opposed_message}"
        )
    else:
        roll_line = f"🎲 {char.name} 的「{skill_name}」檢定：{value}%{dice_note}，擲出 {r.roll} → {tier_zh}{luck_note}{opposed_line}"
        keeper_message = (
            f"（{char.name} 擲骰做了一次「{skill_name}」檢定：技能值 {value}%{dice_note}，"
            f"擲出 {r.roll} → {tier_zh}{luck_note}。這是已經確定的結果，請根據這個結果描述後續發展，"
            f"不要重新判定或改變這個結果。）{opposed_message}"
        )
    return roll_line, keeper_message


async def _finalize_check_result(
    conversation_id: str,
    user_id: str,
    state: GroupState,
    char,
    roll_line: str,
    keeper_message: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
) -> None:
    """Shared tail for every resolved check (sanity, choice, plain skill, and
    a Luck-spend decision) — hands the already-determined result to the
    Keeper for narration and delivers whatever it queued."""
    resolved_location = _resolve_map_action(state, user_id, keeper_message)
    keeper_reply, private_messages, image_requests = await asyncio.to_thread(
        keeper.run_turn, state, user_id, char.name, keeper_message, resolved_location
    )
    await reply(f"{roll_line}\n\n{keeper_reply}")
    await _deliver_side_effects(conversation_id, send_dm, send_image, send_dm_image, private_messages, image_requests)


async def handle_check_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
) -> None:
    """/coc check [技能名] [獎勵骰數] [懲罰骰數] — the player's own roll, in
    code, visible to the group immediately, instead of the Keeper (LLM)
    quietly deciding a result. Pairs with keeper.py's skill_check/sanity_check
    tools, which now only *register* a pending check (see GroupState.
    pending_checks) instead of rolling — this command is what actually rolls
    the dice, then feeds the outcome back to the Keeper as an established
    fact for it to narrate, exactly like a normal free-text turn."""
    state = load_state(conversation_id)
    if not state.active:
        await reply("目前沒有進行中的遊戲。")
        return
    char = state.characters.get(user_id)
    if not char:
        await reply("你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
        return

    parts = text.split()
    skill_arg = parts[2] if len(parts) > 2 else None
    pending = state.pending_checks.pop(user_id, None)

    # A pending "choice" check (see keeper.py's offer_check_choice — e.g. 閃避
    # vs 反擊) needs the player to name one of the options; unlike the plain
    # skill/sanity cases below, an unmatched or missing skill_arg here puts
    # the pending check back rather than discarding it, since silently losing
    # the whole choice prompt over a typo would be a worse experience than a
    # skill/sanity mismatch just falling through to a fresh check.
    choice_skill_name = choice_display_label = None
    choice_value = choice_bonus = choice_penalty = None
    choice_attacker_tier = None
    if pending and pending.get("type") == "choice":
        if skill_arg is None:
            state.pending_checks[user_id] = pending
            options_text = "、".join(f"{o['label']}（{o['skill']} {o['skill_value']}%）" for o in pending["options"])
            await reply(f"這是需要選擇的檢定，請輸入「/coc check <選項名稱>」，可選：{options_text}")
            return
        matched = next(
            (o for o in pending["options"]
             if _skill_names_match(o["label"], skill_arg) or _skill_names_match(o["skill"], skill_arg)),
            None,
        )
        if not matched:
            state.pending_checks[user_id] = pending
            options_text = "、".join(o["label"] for o in pending["options"])
            await reply(f"沒有「{skill_arg}」這個選項，可選：{options_text}")
            return
        choice_skill_name, choice_display_label = matched["skill"], matched["label"]
        choice_value, choice_bonus, choice_penalty = matched["skill_value"], matched["bonus_dice"], matched["penalty_dice"]
        choice_attacker_tier = pending.get("attacker_tier")
        pending = None
    elif skill_arg is None:
        if not pending:
            await reply("目前沒有守密人請你做的檢定。用法：/coc check 技能名 [獎勵骰數] [懲罰骰數] 可以自己主動檢定。")
            return
    elif not (pending and pending.get("type") == "skill" and _skill_names_match(pending.get("skill", ""), skill_arg)):
        # Named a skill that doesn't match what was pending (or nothing was
        # pending, or the pending one was a SAN check): a fresh, self-initiated
        # check, bonus/penalty from the command's own args instead.
        pending = None

    if pending and pending.get("type") == "sanity":
        san_before = char.san
        r = dice.sanity_check(san_before, pending.get("loss_success", "0"), pending.get("loss_failure", "1d4"))
        char.san = r.san_after
        save_state(state)
        outcome = "通過" if r.check.success else "失敗"
        roll_line = f"🎲 {char.name} 的理智檢定：SAN {san_before}，擲出 {r.check.roll} → {outcome}，損失 {r.loss} 點理智（現在 SAN {r.san_after}）"
        keeper_message = (
            f"（{char.name} 擲骰做了理智檢定：SAN {san_before} 擲出 {r.check.roll} → {outcome}，"
            f"損失 {r.loss} 點理智，現在 SAN {r.san_after}。這是已經確定的結果，請根據這個結果描述"
            f"角色的反應與後續發展，不要重新判定或改變這個結果。）"
        )
        await _finalize_check_result(conversation_id, user_id, state, char, roll_line, keeper_message, reply, send_dm, send_image, send_dm_image)
        return

    is_pushed = False
    attacker_tier = None
    if choice_skill_name is not None:
        skill_name, value, bonus, penalty = choice_skill_name, choice_value, choice_bonus, choice_penalty
        display_label = choice_display_label
        attacker_tier = choice_attacker_tier
    else:
        if pending:
            skill_name, value, bonus, penalty = pending["skill"], pending["skill_value"], pending["bonus_dice"], pending["penalty_dice"]
            is_pushed = bool(pending.get("pushed", False))
        else:
            skill_name = skill_arg
            value = keeper.resolve_skill_value(char, skill_name)
            bonus = int(parts[3]) if len(parts) > 3 and parts[3].lstrip("-").isdigit() else 0
            penalty = int(parts[4]) if len(parts) > 4 and parts[4].lstrip("-").isdigit() else 0
            save_state(state)  # resolve_skill_value may have registered a new default-value skill
        display_label = None
    r = dice.skill_check(value, bonus_dice=bonus, penalty_dice=penalty)

    # Luck-spend: only proactively offered when it's a near-miss (the cheapest
    # possible upgrade costs <= 7 Luck) — see app/luck.py. Sanity checks are
    # excluded (handled above, already finalized by this point), and so is a
    # Pushed Roll (COC7e optional rule: a pushed reroll's result is final,
    # can't be bought up again with Luck on top of it).
    luck_options = [] if is_pushed else luck.buyable_options(value, r.roll, r.tier, char.luck)
    gate_cost = None if is_pushed else luck.cheapest_cost(value, r.roll, r.tier)
    if luck_options and gate_cost is not None and gate_cost <= 7:
        state.pending_luck_decisions[user_id] = {
            "skill_name": skill_name, "display_label": display_label,
            "value": value, "roll": r.roll, "bonus_dice": bonus, "penalty_dice": penalty,
            "original_tier": r.tier, "attacker_tier": attacker_tier,
            "options": [{"tier": o.tier, "cost": o.cost} for o in luck_options],
        }
        save_state(state)
        options_text = "、".join(f"花 {o.cost} 點 Luck → {_CHECK_TIER_ZH[o.tier]}" for o in luck_options)
        dice_note = f"（獎勵骰x{bonus}）" if bonus else f"（懲罰骰x{penalty}）" if penalty else ""
        check_label = f"選擇「{display_label}」（{skill_name}）" if display_label is not None else f"「{skill_name}」"
        attacker_note = f"\n⚔️ 攻擊方擲出 → {_CHECK_TIER_ZH[attacker_tier]}" if attacker_tier is not None else ""
        await reply(
            f"🎲 {char.name} 的{check_label}檢定：{value}%{dice_note}，擲出 {r.roll} → {_CHECK_TIER_ZH[r.tier]}{attacker_note}\n"
            f"目前 Luck {char.luck} 點，要花 Luck 買到更好的結果嗎？可選：{options_text}\n"
            f"（點下面按鈕，或輸入「/coc luck skip」維持目前結果、「/coc luck regular/hard/extreme」花費對應點數）"
        )
        return

    roll_line, keeper_message = _build_check_narration(
        char, skill_name, display_label, value, r, bonus, penalty, attacker_tier=attacker_tier
    )
    await _finalize_check_result(conversation_id, user_id, state, char, roll_line, keeper_message, reply, send_dm, send_image, send_dm_image)


async def handle_luck_decision(
    conversation_id: str,
    user_id: str,
    choice: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
) -> None:
    """Resolves a pending Luck-spend decision (see handle_check_command above
    and app/luck.py) — either "skip" (keep the natural roll) or a tier name
    ("regular"/"hard"/"extreme") to buy up to, deducting the cost from the
    character's Luck before handing the (possibly improved) result to the
    Keeper exactly like a normal check."""
    state = load_state(conversation_id)
    pending = state.pending_luck_decisions.pop(user_id, None)
    if not pending:
        await reply("目前沒有待決定的 Luck 花費。")
        return
    char = state.characters.get(user_id)
    if not char:
        await reply("找不到你的角色。")
        return

    tier = pending["original_tier"]
    luck_spent = 0
    if choice != "skip":
        option = next((o for o in pending["options"] if o["tier"] == choice), None)
        if not option:
            state.pending_luck_decisions[user_id] = pending  # not a valid/still-affordable option — put it back
            save_state(state)
            options_text = "、".join(f"{o['tier']}（{o['cost']} 點）" for o in pending["options"])
            await reply(f"這不是有效的選項，可選：{options_text}、skip")
            return
        luck_spent = option["cost"]
        char.luck = max(0, char.luck - luck_spent)
        tier = choice
    save_state(state)

    success = tier in ("critical", "extreme", "hard", "regular")
    r = dice.SkillCheckResult(
        skill_value=pending["value"], roll=pending["roll"], bonus_dice=pending["bonus_dice"],
        penalty_dice=pending["penalty_dice"], tier=tier, success=success,
    )
    roll_line, keeper_message = _build_check_narration(
        char, pending["skill_name"], pending["display_label"], pending["value"], r,
        pending["bonus_dice"], pending["penalty_dice"],
        luck_spent=luck_spent, original_tier=pending["original_tier"],
        attacker_tier=pending.get("attacker_tier"),
    )
    await _finalize_check_result(conversation_id, user_id, state, char, roll_line, keeper_message, reply, send_dm, send_image, send_dm_image)


async def handle_text_message(
    conversation_id: str,
    user_id: str,
    get_display_name: GetDisplayName,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
) -> None:
    text = text.strip()

    if text.startswith("/roll"):
        await handle_roll_command(reply, text)  # no group state touched, no lock needed
        return

    if text.startswith("/coc check"):
        if not locks.try_acquire_check(conversation_id, user_id):
            await reply("上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
            return
        try:
            async with locks.get_conversation_lock(conversation_id):
                await handle_check_command(conversation_id, user_id, reply, send_dm, send_image, send_dm_image, text)
        finally:
            locks.release_check(conversation_id, user_id)
        return

    if text.startswith("/coc luck"):
        parts = text.split()
        choice = parts[2] if len(parts) > 2 else "skip"
        if not locks.try_acquire_check(conversation_id, user_id):
            await reply("上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
            return
        try:
            async with locks.get_conversation_lock(conversation_id):
                await handle_luck_decision(conversation_id, user_id, choice, reply, send_dm, send_image, send_dm_image)
        finally:
            locks.release_check(conversation_id, user_id)
        return

    if text.startswith("/coc"):
        async with locks.get_conversation_lock(conversation_id):
            await _handle_coc_command(conversation_id, user_id, reply, send_dm, send_image, text)
        return

    private_messages: list[tuple[str, str]] = []
    image_requests: list[tuple[str | None, int]] = []
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if not state.active:
            return  # ignore ordinary chit-chat until a scenario is actually loaded and running

        if user_id not in state.characters:
            display_name = await get_display_name()
            await reply(f"{display_name}，你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
            return

        display_name = state.characters[user_id].name
        resolved_location = _resolve_map_action(state, user_id, text)
        reply_text, private_messages, image_requests = await asyncio.to_thread(
            keeper.run_turn, state, user_id, display_name, text, resolved_location
        )
        await reply(reply_text)

    await _deliver_side_effects(conversation_id, send_dm, send_image, send_dm_image, private_messages, image_requests)


def _find_scene_map_by_location(state: GroupState, candidate: str) -> tuple[str, dict] | None:
    """Fuzzy match a raw "entering X" text candidate against the location_name
    of any map extracted from this scenario (see app/scene_map.py)."""
    norm = candidate.strip().lower()
    if not norm:
        return None
    for page_key, scene_map in state.scene_maps.items():
        name = str(scene_map.get("location_name", "")).strip().lower()
        if name and (norm == name or norm in name or name in norm):
            return page_key, scene_map
    return None


def _resolve_map_action(state: GroupState, user_id: str, text: str) -> dict | None:
    """Runs the Map/Scene Engine (app/scene_map.py) against a player's raw
    message *before* any LLM call, exactly per this feature's whole point:
    the destination room is computed deterministically in code, not guessed
    by the Keeper from prose. Mutates state.current_map_page/current_room_id/
    party_facing **for this one user_id only** — see GroupState's own
    comment on why position tracking is per-character rather than a single
    shared party location: a scenario might split the group in ways this
    project has no reason to assume in advance, so each character just
    tracks their own position, and "the group" is whatever set of
    characters happens to share a (page, room) right now.

    Returns a small dict for the Keeper prompt (app/keeper.py's
    `resolved_location`), or None if the message didn't trigger a resolvable
    map action (no map loaded, no direction detected, or no matching exit) —
    callers should fall back to letting the Keeper narrate movement itself,
    exactly like before this feature existed."""
    resolved_room: dict | None = None
    current_page = state.current_map_page.get(user_id, "")
    current_room = state.current_room_id.get(user_id, "")
    facing = state.party_facing.get(user_id, "N")

    location_candidate = intent_parser.extract_entered_location(text)
    if location_candidate:
        found = _find_scene_map_by_location(state, location_candidate)
        if found:
            page_key, scene_map = found
            if page_key != current_page:
                current_page = page_key
                facing = "N"
                current_room = scene_map.get("entry_room_id", "")
                state.current_map_page[user_id] = current_page
                state.current_room_id[user_id] = current_room
                state.party_facing[user_id] = facing
                resolved_room = scene_map_engine.get_room(scene_map, current_room)

    active_map = state.scene_maps.get(current_page) if current_page else None
    if active_map:
        movement = intent_parser.parse_movement_intent(text)
        if movement:
            result = scene_map_engine.resolve_move(
                active_map, current_room, facing, movement["relative_direction"], movement["order"],
            )
            if result["ok"]:
                state.current_room_id[user_id] = result["room"]["id"]
                state.party_facing[user_id] = result["facing"]
                resolved_room = result["room"]
            # result["ok"] is False (no matching exit): deliberately not
            # returned as an error here — let the Keeper's own dynamic prompt
            # (see _build_dynamic_prompt) decide how to narrate a blocked or
            # ambiguous direction instead of the engine flatly refusing it.
        elif intent_parser.has_movement_verb(text):
            # No relative-direction word matched, but this still reads as a
            # movement attempt — most often the player named the destination
            # room directly ("我去廚房看看") instead of describing it by
            # direction. Try a free local match against the current map's own
            # room names first (no API call); only fall back to Scenario RAG
            # (a real embeddings call when configured — see scenario_rag.py)
            # if that comes up empty. This is deliberately best-effort: a miss
            # here just falls through to the Keeper narrating movement itself,
            # exactly like before this fallback existed.
            target_room = scene_map_engine.find_room_by_text(active_map, text)
            if target_room is None and SCENARIO_RAG_ENABLED and state.scenario_text:
                target_room = _find_room_via_rag(state, active_map, text)
            if target_room is not None:
                state.current_room_id[user_id] = target_room["id"]
                state.party_facing[user_id] = "N"  # arbitrary jump, no direction to carry forward
                resolved_room = target_room

    if resolved_room is None:
        return None
    char = state.characters.get(user_id)
    return {
        "character_name": char.name if char else "",
        "room_name": resolved_room.get("name", ""),
        "room_description": resolved_room.get("description", ""),
    }


def _find_room_via_rag(state: GroupState, scene_map: dict, text: str) -> dict | None:
    """Scenario RAG fallback for room-name resolution (see
    _resolve_map_action above) — RAG has no concept of room IDs, so the
    connection is made by searching the scenario text for the player's raw
    phrase and checking whether any of the current map's room names appear
    in whichever page(s) came back as relevant. This is genuinely a second
    real API call on top of the Keeper's own turn when embeddings are
    configured (see scenario_rag.py), so it's only reached after the free
    local name match in _resolve_map_action has already failed."""
    index = scenario_rag.get_index(state.group_id, state.scenario_text)
    results = scenario_rag.search(index, text, top_k=3)
    for result in results:
        room = scene_map_engine.find_room_by_text(scene_map, result["text"])
        if room:
            return room
    return None


def _blocked_by_existing_character(state: GroupState, user_id: str) -> str | None:
    """Returns a rejection message if this player already has a character and
    the current game is still active, else None. Without this, re-running
    /coc pc/create/usepregen (e.g. by accident, or to "try again") would
    silently overwrite the character they're already playing mid-game. /coc end
    (which sets state.active = False) lifts the restriction — characters
    themselves aren't cleared until /coc newgame, but a player is free to
    rebuild once the game they were in has actually ended."""
    if not state.active:
        return None
    char = state.characters.get(user_id)
    if not char:
        return None
    return (
        f"你已經有角色「{char.name}」了，這局遊戲進行中不能重新建角（避免蓋掉正在用的角色）。"
        "如果真的要換角色，請先讓這局遊戲結束（/coc end），或開新的一局（/coc newgame）。"
    )


def _pregen_full_sheet_text(pregen: dict, index: int) -> str:
    """Full-detail, read-only preview of a scenario pregen for a player deciding
    whether to claim it — unlike Character.sheet_text() this shows every skill
    the scenario listed (not just the top 12), since the whole point is letting
    someone compare candidates before committing via /coc usepregen. Deliberately
    excludes secret_goal: that's only ever revealed privately after a claim (see
    /coc pc and /coc usepregen), never in a pre-selection preview anyone can run."""
    lines = [
        f"【預製角色 #{index}】{pregen.get('name') or '未命名'}　職業：{pregen.get('occupation', '未知職業')}",
    ]
    attrs = ["str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck"]
    labels = {"str_": "STR", "con": "CON", "siz": "SIZ", "dex": "DEX", "app": "APP", "int_": "INT", "pow_": "POW", "edu": "EDU", "luck": "LUCK"}
    attr_line = " ".join(f"{labels[a]} {pregen[a]}" for a in attrs if isinstance(pregen.get(a), (int, float)))
    if attr_line:
        lines.append(attr_line)
    vitals = []
    if isinstance(pregen.get("hp_max"), (int, float)):
        vitals.append(f"HP {pregen['hp_max']}")
    if isinstance(pregen.get("mp_max"), (int, float)):
        vitals.append(f"MP {pregen['mp_max']}")
    if isinstance(pregen.get("san_max"), (int, float)):
        vitals.append(f"SAN {pregen['san_max']}")
    if vitals:
        lines.append("　".join(vitals))
    skills = pregen.get("skills") or {}
    if skills:
        ranked = sorted(skills.items(), key=lambda kv: -kv[1] if isinstance(kv[1], (int, float)) else 0)
        lines.append("技能：" + "、".join(f"{k} {v}%" for k, v in ranked))
    if pregen.get("notes"):
        lines.append(f"背景：{pregen['notes']}")
    if pregen.get("key_connection"):
        lines.append(f"★ 關鍵背景連結：{pregen['key_connection']}")
    if pregen.get("claimed_by"):
        lines.append("（此角色已被選走）")
    return "\n".join(lines)




async def _handle_coc_command(
    conversation_id: str, user_id: str, reply: Reply, send_dm: SendDM, send_image: SendImage, text: str
) -> None:
    parts = text.split()
    sub = parts[1] if len(parts) > 1 else "help"

    if sub == "newgame":
        save_state(GroupState(group_id=conversation_id))
        await reply("已重置這個群組的遊戲狀態。請上傳劇本 PDF 檔案開始新的冒險。")
        return

    if sub == "pc":
        state = load_state(conversation_id)
        # A scenario with its own pregens is meant to be played with exactly
        # that cast — no custom quick-gen once any exist, only /coc pregen.
        if state.pregens:
            await reply("這份劇本有預製角色，請用「/coc pregens」查看、「/coc pregen 編號」選一位，這份劇本不開放自訂角色。")
            return

        if len(parts) < 3:
            occ_hint = "、".join(OCCUPATIONS.keys())
            if state.scenario_text:
                occ_hint += "\n（想用這份劇本裡的職業？先輸入 /coc pregens 讓守密人讀取劇本裡的角色卡）"
            await reply("用法：/coc pc 角色名 [職業]\n可選職業：" + occ_hint)
            return

        blocked = _blocked_by_existing_character(state, user_id)
        if blocked:
            await reply(blocked)
            return

        name = parts[2]
        occupation = parts[3] if len(parts) > 3 else None

        char = generate_investigator(name=name, owner_id=user_id, occupation=occupation)
        state.characters[user_id] = char
        save_state(state)
        await reply(f"調查員建立完成！\n\n{char.sheet_text()}")
        return

    if sub == "sheet":
        state = load_state(conversation_id)
        char = state.characters.get(user_id)
        if not char:
            await reply("你還沒有角色，先輸入「/coc pc 角色名 職業」建立一個吧。")
            return
        await reply(char.sheet_text())
        return

    if sub == "status":
        state = load_state(conversation_id)
        if not state.scenario_title:
            await reply("目前還沒有載入任何劇本，上傳 PDF 開始吧。")
            return
        lines = [f"劇本：《{state.scenario_title}》", f"狀態：{'進行中' if state.active else '已結束'}", ""]
        if state.characters:
            for c in state.characters.values():
                lines.append(f"・{c.name}（{c.occupation}）HP {c.hp}/{c.hp_max} SAN {c.san}/{c.san_max} MP {c.mp}/{c.mp_max}")
        else:
            lines.append("（尚無角色）")
        await reply("\n".join(lines))
        return

    if sub == "end":
        state = load_state(conversation_id)
        state.active = False
        save_state(state)
        await reply("遊戲已結束，遊戲紀錄與角色仍會保留。要開新的一局請用 /coc newgame。")
        return

    if sub == "setskill":
        if len(parts) < 5:
            await reply("用法：/coc setskill 角色名 技能名 數值")
            return
        name, skill, value_str = parts[2], parts[3], parts[4]
        state = load_state(conversation_id)
        char = state.characters.get(user_id)
        if not char or char.name != name:
            await reply("只能修改你自己建立的角色（角色名稱需完全相符）。")
            return
        try:
            value = max(0, min(100, int(value_str)))
        except ValueError:
            await reply("數值必須是整數。")
            return
        char.skills[skill] = value
        save_state(state)
        await reply(f"已將 {name} 的「{skill}」設為 {value}%。")
        return

    if sub == "setconnection":
        if len(parts) < 4:
            await reply("用法：/coc setconnection 角色名 敘述（例如：/coc setconnection 小明 你失散多年的妹妹）")
            return
        name = parts[2]
        description = " ".join(parts[3:])
        state = load_state(conversation_id)
        char = state.characters.get(user_id)
        if not char or char.name != name:
            await reply("只能修改你自己建立的角色（角色名稱需完全相符）。")
            return
        char.key_connection = description
        save_state(state)
        await reply(f"已將 {name} 的「★ 關鍵背景連結」設為：{description}")
        return

    if sub == "create":
        action = parts[2] if len(parts) > 2 else None
        state = load_state(conversation_id)

        if action == "status":
            session = state.creation_sessions.get(user_id)
            if not session:
                await reply("目前沒有進行中的建角流程，輸入「/coc create 角色名 [職業]」開始。")
                return
            await reply(creation.status_text(session))
            return

        if action == "done":
            session = state.creation_sessions.get(user_id)
            if not session:
                await reply("目前沒有進行中的建角流程。")
                return
            leftover = session.occ_points_remaining + session.interest_points_remaining
            char = creation.finalize(state, user_id)
            save_state(state)
            note = f"\n（還有 {leftover} 點未分配的技能點數已捨棄）" if leftover else ""
            await reply(f"調查員建立完成！\n\n{char.sheet_text()}{note}")
            return

        if action == "cancel":
            ok = creation.cancel(state, user_id)
            save_state(state)
            await reply("已取消建角流程。" if ok else "目前沒有進行中的建角流程。")
            return

        if state.pregens:
            await reply("這份劇本有預製角色，請用「/coc pregens」查看、「/coc pregen 編號」選一位，這份劇本不開放自訂角色。")
            return

        if not action:
            await reply("用法：/coc create 角色名 [職業]\n可選職業：" + "、".join(OCCUPATIONS.keys()))
            return

        if user_id in state.creation_sessions:
            await reply("你已經有一個建角流程進行中了，先用「/coc create done」完成或「/coc create cancel」取消。")
            return

        blocked = _blocked_by_existing_character(state, user_id)
        if blocked:
            await reply(blocked)
            return

        name = action
        occupation = parts[3] if len(parts) > 3 else None
        session = creation.start_creation(state, user_id, name, occupation)
        save_state(state)
        await reply(f"已擲出屬性，開始分配技能點數！\n\n{creation.status_text(session)}")
        return

    if sub == "alloc":
        if len(parts) < 5:
            await reply("用法：/coc alloc occ|int 技能名 點數")
            return
        pool, skill, points_str = parts[2], parts[3], parts[4]
        state = load_state(conversation_id)
        session = state.creation_sessions.get(user_id)
        if not session:
            await reply("目前沒有進行中的建角流程，先輸入「/coc create 角色名 [職業]」開始。")
            return
        try:
            points = int(points_str)
        except ValueError:
            await reply("點數必須是整數。")
            return
        result = creation.allocate(session, pool, skill, points)
        if not result["ok"]:
            await reply(result["error"])
            return
        save_state(state)
        await reply(creation.status_text(session))
        return

    if sub == "pregens":
        state = load_state(conversation_id)
        if not state.scenario_text and not state.pregens:
            await reply("目前還沒有載入劇本，上傳 PDF 之後才能抓取內建角色卡（或直接上傳 role_ 開頭的角色卡檔案）。")
            return
        if not state.pregens:
            pregens = await asyncio.to_thread(pregen_extractor.extract_pregens, state.scenario_text)
            state.pregens = pregens
            save_state(state)
        if not state.pregens:
            await reply("這份劇本沒有附帶預製調查員角色卡，用 /coc pc 或 /coc create 自己建立角色吧。")
            return
        lines = ["這份劇本內建了以下預製調查員："]
        for i, p in enumerate(state.pregens, start=1):
            claimed_by = p.get("claimed_by")
            tag = "（已被選走）" if claimed_by else ""
            lines.append(f"{i}. {p.get('name') or '未命名'}（{p.get('occupation', '未知職業')}）{tag}")
        lines.append("輸入「/coc pregen 編號」查看某位角色的完整能力，或直接「/coc usepregen 編號 [自訂名稱]」使用。")
        await reply("\n".join(lines))
        return

    if sub == "pregen":
        if len(parts) < 3:
            await reply("用法：/coc pregen 編號（先用 /coc pregens 看編號對照）")
            return
        state = load_state(conversation_id)
        if not state.pregens:
            await reply("還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
            return
        try:
            idx = int(parts[2])
        except ValueError:
            await reply("編號必須是數字。")
            return
        if not (1 <= idx <= len(state.pregens)):
            await reply(f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
            return
        await reply(_pregen_full_sheet_text(state.pregens[idx - 1], idx))
        return

    if sub == "usepregen":
        if len(parts) < 3:
            await reply("用法：/coc usepregen 編號 [自訂名稱]")
            return
        state = load_state(conversation_id)
        if not state.pregens:
            await reply("還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
            return
        try:
            idx = int(parts[2])
        except ValueError:
            await reply("編號必須是數字。")
            return
        if not (1 <= idx <= len(state.pregens)):
            await reply(f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
            return
        blocked = _blocked_by_existing_character(state, user_id)
        if blocked:
            await reply(blocked)
            return
        pregen = state.pregens[idx - 1]
        claimed_by = pregen.get("claimed_by")
        if claimed_by and claimed_by != user_id:
            await reply("這位角色已經被其他玩家選走了，輸入「/coc pregens」看看還有哪些可選。")
            return
        char = pregen_extractor.pregen_to_character(pregen, user_id)
        if len(parts) > 3:
            char.name = parts[3]
        state.characters[user_id] = char
        pregen["claimed_by"] = user_id
        save_state(state)
        await reply(f"已使用預製角色！\n\n{char.sheet_text()}")
        if char.secret_goal:
            try:
                await send_dm(user_id, f"🤫（私訊）你的秘密目標：{char.secret_goal}")
            except Exception:
                _logger.exception("send_dm (secret_goal on /coc pregen) failed for user_id=%s", user_id)
        return

    if sub == "away":
        state = load_state(conversation_id)
        char = state.characters.get(user_id)
        if not char:
            await reply("你還沒有角色。")
            return
        char.away = True
        save_state(state)
        await reply(f"{char.name} 已標記為暫離，戰鬥中會自動跳過他的回合，直到輸入「/coc back」回來。")
        return

    if sub == "back":
        state = load_state(conversation_id)
        char = state.characters.get(user_id)
        if not char:
            await reply("你還沒有角色。")
            return
        char.away = False
        save_state(state)
        await reply(f"{char.name} 回來了，恢復正常參與。")
        return

    if sub == "showpage":
        if len(parts) < 3:
            await reply("用法：/coc showpage 頁碼（例如 /coc showpage 16；守密人提到「第 X 頁」時可以用那個數字）")
            return
        try:
            page_number = int(parts[2])
        except ValueError:
            await reply("頁碼必須是數字。")
            return
        png_bytes = load_page_image(conversation_id, page_number)
        if not png_bytes:
            await reply(f"第 {page_number} 頁沒有存圖（可能是純文字頁面，或劇本裡根本沒有這一頁）。")
            return
        await send_image(png_bytes, conversation_id, page_number)
        return

    if sub == "where":
        state = load_state(conversation_id)
        current_page = state.current_map_page.get(user_id, "")
        if not current_page:
            await reply("目前不在任何有地圖的地點裡（或這份劇本沒有偵測到平面圖）。")
            return
        scene_map = state.scene_maps.get(current_page)
        room = scene_map_engine.get_room(scene_map, state.current_room_id.get(user_id, "")) if scene_map else None
        if not room:
            await reply("地圖資料異常，目前所在房間找不到對應資料，可以用「/coc leavemap」重置。")
            return
        exits = room.get("exits", [])
        exits_text = "、".join(f"{e.get('label') or e.get('compass')}" for e in exits) or "（沒有記錄到出口）"
        desc = f"\n{room['description']}" if room.get("description") else ""
        await reply(f"目前在「{room.get('name', '')}」（第 {current_page} 頁的地圖）{desc}\n出口：{exits_text}")
        return

    if sub == "enter":
        if len(parts) < 3:
            await reply("用法：/coc enter 頁碼（先用 /coc showpage 或劇本內文找到平面圖在第幾頁）")
            return
        page_key = parts[2]
        state = load_state(conversation_id)
        scene_map = state.scene_maps.get(page_key)
        if not scene_map:
            available = "、".join(sorted(state.scene_maps.keys())) or "（沒有偵測到任何平面圖）"
            await reply(f"第 {page_key} 頁沒有偵測到平面圖。有地圖資料的頁碼：{available}")
            return
        state.current_map_page[user_id] = page_key
        state.current_room_id[user_id] = scene_map.get("entry_room_id", "")
        state.party_facing[user_id] = "N"
        save_state(state)
        room = scene_map_engine.get_room(scene_map, state.current_room_id[user_id])
        await reply(f"已進入第 {page_key} 頁的地圖，目前在「{room.get('name', '') if room else '未知位置'}」。")
        return

    if sub == "leavemap":
        state = load_state(conversation_id)
        state.current_map_page.pop(user_id, None)
        state.current_room_id.pop(user_id, None)
        state.party_facing.pop(user_id, None)
        save_state(state)
        await reply("已離開目前的地圖追蹤，移動改回完全由守密人自己判斷。")
        return

    if sub == "combat":
        await _handle_combat_subcommand(conversation_id, reply, parts)
        return

    await reply(HELP_TEXT)


async def _handle_combat_subcommand(conversation_id: str, reply: Reply, parts: list[str]) -> None:
    action = parts[2] if len(parts) > 2 else None
    state = load_state(conversation_id)

    if action == "start":
        combat.start_combat(state)
        save_state(state)
        await reply(combat.status_text(state))
        return

    if action in ("addnpc", "addally"):
        if len(parts) < 6:
            await reply(f"用法：/coc combat {action} 名稱 DEX HP")
            return
        name, dex_str, hp_str = parts[3], parts[4], parts[5]
        try:
            dex, hp = int(dex_str), int(hp_str)
        except ValueError:
            await reply("DEX 和 HP 必須是整數。")
            return
        combat.add_npc(state, name, dex, hp, is_ally=(action == "addally"))
        save_state(state)
        await reply(combat.status_text(state))
        return

    if action == "status":
        await reply(combat.status_text(state))
        return

    if action == "next":
        result = combat.advance_turn(state)
        save_state(state)
        if not result["ok"]:
            await reply(result["error"])
            return
        await reply(f"第 {result['round']} 輪，輪到「{result['current_turn']}」了（HP {result['hp']}/{result['hp_max']}）。")
        return

    if action == "damage":
        if len(parts) < 5:
            await reply("用法：/coc combat damage 名稱 增減量（受傷用負數，例如 -5）")
            return
        name, delta_str = parts[3], parts[4]
        try:
            delta = int(delta_str)
        except ValueError:
            await reply("增減量必須是整數。")
            return
        result = combat.damage_combatant(state, name, delta)
        save_state(state)
        if not result["ok"]:
            await reply(result["error"])
            return
        tag = "（已倒下）" if result["defeated"] else ""
        await reply(f"{result['name']} HP 變為 {result['hp']}/{result['hp_max']}{tag}")
        return

    if action == "end":
        combat.end_combat(state)
        save_state(state)
        await reply("戰鬥已結束。")
        return

    await reply(
        "用法：\n"
        "/coc combat start → 開始戰鬥\n"
        "/coc combat addnpc 名稱 DEX HP → 加入敵人\n"
        "/coc combat addally 名稱 DEX HP → 加入站在我方的 NPC 隊友\n"
        "/coc combat status → 查看目前狀態\n"
        "/coc combat next → 推進到下一位的回合\n"
        "/coc combat damage 名稱 增減量 → 調整 HP\n"
        "/coc combat end → 結束戰鬥",
    )
