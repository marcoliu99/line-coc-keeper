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
from typing import Awaitable, Callable

from app import combat, creation, dice, keeper, locks, pdf_loader, pregen_extractor
from app.models import OCCUPATIONS, GroupState, generate_investigator
from app.state import clear_page_images, load_page_image, load_state, save_page_image, save_state

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
・/coc pc 角色名 [職業] → 快速隨機生成一位調查員（一鍵完成）
  可選職業：""" + "、".join(OCCUPATIONS.keys()) + """
・/coc create 角色名 [職業] → 互動式建角：先擲屬性，再自己分配職業/興趣技能點數
  接著用 /coc alloc occ|int 技能名 點數 分配，/coc create status 查看進度，
  /coc create done 完成、/coc create cancel 放棄
・/coc pregens → 查看這份劇本有沒有附帶的預製調查員；/coc usepregen 編號 [自訂名稱] 直接使用

【角色管理】
・/coc sheet → 查看自己的角色卡
・/coc status → 查看目前劇本與所有角色狀態
・/coc setskill 角色名 技能名 數值 → 手動修正自己角色的技能值
・/coc away → 標記自己暫離（戰鬥中會自動跳過你的回合）；/coc back → 回來繼續玩
・/coc showpage 頁碼 → 直接看劇本某一頁的實際圖片（地圖、手卡等），守密人提到「第 X 頁」時可以用

【戰鬥】
・/coc combat start → 開始正式戰鬥（依 DEX 排先攻順位）
・/coc combat addnpc 名稱 DEX HP → 加入一個敵人
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
        text, low_text_pages, truncated, page_images = await asyncio.to_thread(pdf_loader.extract_text, pdf_bytes)
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

    await push(
        f"已載入劇本《{title}》（{len(text)} 字）。\n"
        "接下來請每位玩家輸入「/coc pc 角色名 職業」建立調查員，職業可選：\n"
        + "、".join(OCCUPATIONS.keys())
        + "\n建好角色後，直接在群組打字描述行動即可開始冒險！"
        + warning,
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
        reply_text, private_messages, image_requests = await asyncio.to_thread(
            keeper.run_turn, state, display_name, text
        )
        await reply(reply_text)

    # Delivered outside the lock — pure I/O, no state access needed. Best-effort:
    # a DM can fail (LINE requires the player to have friended the bot; Discord
    # requires them to allow DMs from server members) and we deliberately don't
    # fall back to posting the content publicly, since that would defeat the
    # entire point of it being private.
    for owner_id, message in private_messages:
        try:
            await send_dm(owner_id, f"🤫（私訊）{message}")
        except Exception:
            pass

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
            pass


def _find_pregen_by_occupation(state: GroupState, occupation: str) -> dict | None:
    """Fuzzy match a requested occupation against the currently loaded scenario's
    cached pregens (state.pregens — reset on every new PDF upload, so this only
    ever matches against whatever scenario is active right now)."""
    if not occupation:
        return None
    norm = occupation.strip().lower()
    for p in state.pregens:
        occ = str(p.get("occupation", "")).strip().lower()
        if occ and (norm == occ or norm in occ or occ in norm):
            return p
    return None


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
        scenario_occupations = list(dict.fromkeys(p.get("occupation") for p in state.pregens if p.get("occupation")))

        if len(parts) < 3:
            occ_hint = "、".join(OCCUPATIONS.keys())
            if scenario_occupations:
                occ_hint += "\n這份劇本裡的職業（技能會參考劇本內建角色卡）：" + "、".join(scenario_occupations)
            elif state.scenario_text:
                occ_hint += "\n（想用這份劇本裡的職業？先輸入 /coc pregens 讓守密人讀取劇本裡的角色卡）"
            await reply("用法：/coc pc 角色名 [職業]\n可選職業：" + occ_hint)
            return

        name = parts[2]
        occupation = parts[3] if len(parts) > 3 else None
        pregen_match = _find_pregen_by_occupation(state, occupation) if occupation else None
        occupation_skills = pregen_match.get("skills") if pregen_match else None
        secret_goal = (pregen_match.get("secret_goal") or "") if pregen_match else ""
        char = generate_investigator(
            name=name, owner_id=user_id, occupation=occupation,
            occupation_skills=occupation_skills, secret_goal=secret_goal,
        )
        state.characters[user_id] = char
        save_state(state)
        note = "\n（技能參考自劇本內建角色卡）" if pregen_match else ""
        await reply(f"調查員建立完成！\n\n{char.sheet_text()}{note}")
        if secret_goal:
            try:
                await send_dm(user_id, f"🤫（私訊）你的秘密目標：{secret_goal}")
            except Exception:
                pass
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

        if not action:
            await reply("用法：/coc create 角色名 [職業]\n可選職業：" + "、".join(OCCUPATIONS.keys()))
            return

        if user_id in state.creation_sessions:
            await reply("你已經有一個建角流程進行中了，先用「/coc create done」完成或「/coc create cancel」取消。")
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
        if not state.scenario_text:
            await reply("目前還沒有載入劇本，上傳 PDF 之後才能抓取內建角色卡。")
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
            lines.append(f"{i}. {p.get('name', '未命名')}（{p.get('occupation', '未知職業')}）")
        lines.append("輸入「/coc usepregen 編號 [自訂名稱]」使用其中一位。")
        await reply("\n".join(lines))
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
        char = pregen_extractor.pregen_to_character(state.pregens[idx - 1], user_id)
        if len(parts) > 3:
            char.name = parts[3]
        state.characters[user_id] = char
        save_state(state)
        await reply(f"已使用預製角色！\n\n{char.sheet_text()}")
        if char.secret_goal:
            try:
                await send_dm(user_id, f"🤫（私訊）你的秘密目標：{char.secret_goal}")
            except Exception:
                pass
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

    if action == "addnpc":
        if len(parts) < 6:
            await reply("用法：/coc combat addnpc 名稱 DEX HP")
            return
        name, dex_str, hp_str = parts[3], parts[4], parts[5]
        try:
            dex, hp = int(dex_str), int(hp_str)
        except ValueError:
            await reply("DEX 和 HP 必須是整數。")
            return
        combat.add_npc(state, name, dex, hp)
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
        "/coc combat status → 查看目前狀態\n"
        "/coc combat next → 推進到下一位的回合\n"
        "/coc combat damage 名稱 增減量 → 調整 HP\n"
        "/coc combat end → 結束戰鬥",
    )
