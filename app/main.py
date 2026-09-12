"""FastAPI webhook server for the LINE COC7e Keeper bot."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    GroupSource,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    RoomSource,
    StickerMessageContent,
    TextMessageContent,
    UserSource,
    VideoMessageContent,
)

from app import combat, creation, dice, keeper, pdf_loader, pregen_extractor
from app.config import LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET
from app.models import OCCUPATIONS, GroupState, generate_investigator
from app.state import load_state, save_state

app = FastAPI(title="LINE COC7e Keeper Bot")

_configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# aiohttp's connector needs a running event loop, so the async LINE clients are
# created lazily on first use inside a request handler rather than at import time.
_async_api_client: AsyncApiClient | None = None
line_bot_api: AsyncMessagingApi | None = None
line_bot_blob_api: AsyncMessagingApiBlob | None = None


def _ensure_line_clients() -> None:
    global _async_api_client, line_bot_api, line_bot_blob_api
    if _async_api_client is None:
        _async_api_client = AsyncApiClient(_configuration)
        line_bot_api = AsyncMessagingApi(_async_api_client)
        line_bot_blob_api = AsyncMessagingApiBlob(_async_api_client)

MAX_LINE_MESSAGE_CHARS = 4800
MAX_REPLY_MESSAGES = 5

_HELP_TEXT = """【COC7e 守密人 Bot 指令】
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


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_LINE_MESSAGE_CHARS] for i in range(0, len(text), MAX_LINE_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


async def _reply(reply_token: str, text: str) -> None:
    messages = [TextMessage(text=c) for c in _chunk_text(text)]
    await line_bot_api.reply_message_with_http_info(
        ReplyMessageRequest(reply_token=reply_token, messages=messages)
    )


def _source_id(source) -> str:
    if isinstance(source, GroupSource):
        return f"group-{source.group_id}"
    if isinstance(source, RoomSource):
        return f"room-{source.room_id}"
    if isinstance(source, UserSource):
        return f"user-{source.user_id}"
    return "unknown"


async def _display_name(source, user_id: str) -> str:
    try:
        if isinstance(source, GroupSource):
            profile = await line_bot_api.get_group_member_profile(source.group_id, user_id)
        elif isinstance(source, RoomSource):
            profile = await line_bot_api.get_room_member_profile(source.room_id, user_id)
        else:
            profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception:
        return user_id[:8] if user_id else "玩家"


@app.post("/callback")
async def callback(request: Request):
    _ensure_line_clients()
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        try:
            await _handle_message_event(event)
        except Exception as exc:  # noqa: BLE001 - keep the webhook alive, surface the error to the group
            try:
                await _reply(event.reply_token, f"發生錯誤了：{exc}")
            except Exception:
                pass
    return "OK"


_UNSUPPORTED_MESSAGE_LABELS: list[tuple[type, str]] = [
    (StickerMessageContent, "貼圖"),
    (ImageMessageContent, "圖片"),
    (VideoMessageContent, "影片"),
    (AudioMessageContent, "語音"),
    (LocationMessageContent, "位置"),
]


async def _handle_message_event(event: MessageEvent) -> None:
    group_id = _source_id(event.source)
    user_id = getattr(event.source, "user_id", "") or ""

    if isinstance(event.message, FileMessageContent):
        await _handle_file_message(event, group_id)
        return

    if not isinstance(event.message, TextMessageContent):
        for msg_type, label in _UNSUPPORTED_MESSAGE_LABELS:
            if isinstance(event.message, msg_type):
                state = load_state(group_id)
                if state.active:
                    await _reply(
                        event.reply_token,
                        f"（守密人目前只讀得懂文字訊息和 PDF 檔案，收到的{label}不會被處理；"
                        "如果裡面有重要內容，麻煩用文字描述一下發生了什麼事。）",
                    )
                return
        return  # unrecognized message type (e.g. flex/template echoes): stay silent

    text = event.message.text.strip()

    if text.startswith("/roll"):
        await _handle_roll_command(event, text)
        return

    if text.startswith("/coc"):
        display_name = await _display_name(event.source, user_id)
        await _handle_coc_command(event, group_id, user_id, display_name, text)
        return

    state = load_state(group_id)
    if not state.active:
        return  # ignore ordinary chit-chat until a scenario is actually loaded and running

    if user_id not in state.characters:
        display_name = await _display_name(event.source, user_id)
        await _reply(event.reply_token, f"{display_name}，你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
        return

    display_name = state.characters[user_id].name
    reply_text = await run_in_threadpool(keeper.run_turn, state, display_name, text)
    await _reply(event.reply_token, reply_text)


async def _handle_file_message(event: MessageEvent, group_id: str) -> None:
    file_name = getattr(event.message, "file_name", "") or ""
    if not file_name.lower().endswith(".pdf"):
        await _reply(event.reply_token, "目前只支援上傳 PDF 劇本檔案喔。")
        return

    content = await line_bot_blob_api.get_message_content(event.message.id)
    try:
        text, low_text_pages = await run_in_threadpool(pdf_loader.extract_text, content)
    except ValueError as exc:
        await _reply(event.reply_token, f"讀取 PDF 失敗：{exc}")
        return

    title = pdf_loader.guess_title(text, file_name=file_name)

    state = load_state(group_id)
    state.scenario_text = text
    state.scenario_title = title
    state.active = True
    save_state(state)

    warning = ""
    if low_text_pages:
        pages_str = "、".join(str(p) for p in low_text_pages)
        warning = (
            f"\n\n⚠️ 第 {pages_str} 頁偵測到文字內容偏少（可能是地圖、手卡或圖片化的內容），"
            "已嘗試自動辨識，但仍建議你人工核對一下；如果有遺漏的重要線索，"
            "之後可以直接把那頁的文字內容貼在群組訊息裡讓守密人知道。"
        )

    await _reply(
        event.reply_token,
        f"已載入劇本《{title}》（{len(text)} 字）。\n"
        "接下來請每位玩家輸入「/coc pc 角色名 職業」建立調查員，職業可選：\n"
        + "、".join(OCCUPATIONS.keys())
        + "\n建好角色後，直接在群組打字描述行動即可開始冒險！"
        + warning,
    )


async def _handle_roll_command(event: MessageEvent, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await _reply(event.reply_token, "用法：/roll 1d100 或 /roll 3d6+2")
        return
    try:
        result = dice.roll_expression(parts[1].strip())
    except ValueError as exc:
        await _reply(event.reply_token, str(exc))
        return
    await _reply(event.reply_token, f"🎲 {result.describe()}")


async def _handle_coc_command(event: MessageEvent, group_id: str, user_id: str, display_name: str, text: str) -> None:
    parts = text.split()
    sub = parts[1] if len(parts) > 1 else "help"

    if sub == "newgame":
        save_state(GroupState(group_id=group_id))
        await _reply(event.reply_token, "已重置這個群組的遊戲狀態。請上傳劇本 PDF 檔案開始新的冒險。")
        return

    if sub == "pc":
        if len(parts) < 3:
            await _reply(event.reply_token, "用法：/coc pc 角色名 [職業]\n可選職業：" + "、".join(OCCUPATIONS.keys()))
            return
        name = parts[2]
        occupation = parts[3] if len(parts) > 3 else None
        state = load_state(group_id)
        char = generate_investigator(name=name, owner_id=user_id, occupation=occupation)
        state.characters[user_id] = char
        save_state(state)
        await _reply(event.reply_token, f"調查員建立完成！\n\n{char.sheet_text()}")
        return

    if sub == "sheet":
        state = load_state(group_id)
        char = state.characters.get(user_id)
        if not char:
            await _reply(event.reply_token, "你還沒有角色，先輸入「/coc pc 角色名 職業」建立一個吧。")
            return
        await _reply(event.reply_token, char.sheet_text())
        return

    if sub == "status":
        state = load_state(group_id)
        if not state.scenario_title:
            await _reply(event.reply_token, "目前還沒有載入任何劇本，上傳 PDF 開始吧。")
            return
        lines = [f"劇本：《{state.scenario_title}》", f"狀態：{'進行中' if state.active else '已結束'}", ""]
        if state.characters:
            for c in state.characters.values():
                lines.append(f"・{c.name}（{c.occupation}）HP {c.hp}/{c.hp_max} SAN {c.san}/{c.san_max} MP {c.mp}/{c.mp_max}")
        else:
            lines.append("（尚無角色）")
        await _reply(event.reply_token, "\n".join(lines))
        return

    if sub == "end":
        state = load_state(group_id)
        state.active = False
        save_state(state)
        await _reply(event.reply_token, "遊戲已結束，遊戲紀錄與角色仍會保留。要開新的一局請用 /coc newgame。")
        return

    if sub == "setskill":
        if len(parts) < 5:
            await _reply(event.reply_token, "用法：/coc setskill 角色名 技能名 數值")
            return
        name, skill, value_str = parts[2], parts[3], parts[4]
        state = load_state(group_id)
        char = state.characters.get(user_id)
        if not char or char.name != name:
            await _reply(event.reply_token, "只能修改你自己建立的角色（角色名稱需完全相符）。")
            return
        try:
            value = max(0, min(100, int(value_str)))
        except ValueError:
            await _reply(event.reply_token, "數值必須是整數。")
            return
        char.skills[skill] = value
        save_state(state)
        await _reply(event.reply_token, f"已將 {name} 的「{skill}」設為 {value}%。")
        return

    if sub == "create":
        action = parts[2] if len(parts) > 2 else None
        state = load_state(group_id)

        if action == "status":
            session = state.creation_sessions.get(user_id)
            if not session:
                await _reply(event.reply_token, "目前沒有進行中的建角流程，輸入「/coc create 角色名 [職業]」開始。")
                return
            await _reply(event.reply_token, creation.status_text(session))
            return

        if action == "done":
            session = state.creation_sessions.get(user_id)
            if not session:
                await _reply(event.reply_token, "目前沒有進行中的建角流程。")
                return
            leftover = session.occ_points_remaining + session.interest_points_remaining
            char = creation.finalize(state, user_id)
            save_state(state)
            note = f"\n（還有 {leftover} 點未分配的技能點數已捨棄）" if leftover else ""
            await _reply(event.reply_token, f"調查員建立完成！\n\n{char.sheet_text()}{note}")
            return

        if action == "cancel":
            ok = creation.cancel(state, user_id)
            save_state(state)
            await _reply(event.reply_token, "已取消建角流程。" if ok else "目前沒有進行中的建角流程。")
            return

        if not action:
            await _reply(event.reply_token, "用法：/coc create 角色名 [職業]\n可選職業：" + "、".join(OCCUPATIONS.keys()))
            return

        if user_id in state.creation_sessions:
            await _reply(event.reply_token, "你已經有一個建角流程進行中了，先用「/coc create done」完成或「/coc create cancel」取消。")
            return

        name = action
        occupation = parts[3] if len(parts) > 3 else None
        session = creation.start_creation(state, user_id, name, occupation)
        save_state(state)
        await _reply(event.reply_token, f"已擲出屬性，開始分配技能點數！\n\n{creation.status_text(session)}")
        return

    if sub == "alloc":
        if len(parts) < 5:
            await _reply(event.reply_token, "用法：/coc alloc occ|int 技能名 點數")
            return
        pool, skill, points_str = parts[2], parts[3], parts[4]
        state = load_state(group_id)
        session = state.creation_sessions.get(user_id)
        if not session:
            await _reply(event.reply_token, "目前沒有進行中的建角流程，先輸入「/coc create 角色名 [職業]」開始。")
            return
        try:
            points = int(points_str)
        except ValueError:
            await _reply(event.reply_token, "點數必須是整數。")
            return
        result = creation.allocate(session, pool, skill, points)
        if not result["ok"]:
            await _reply(event.reply_token, result["error"])
            return
        save_state(state)
        await _reply(event.reply_token, creation.status_text(session))
        return

    if sub == "pregens":
        state = load_state(group_id)
        if not state.scenario_text:
            await _reply(event.reply_token, "目前還沒有載入劇本，上傳 PDF 之後才能抓取內建角色卡。")
            return
        if not state.pregens:
            pregens = await run_in_threadpool(pregen_extractor.extract_pregens, state.scenario_text)
            state.pregens = pregens
            save_state(state)
        if not state.pregens:
            await _reply(event.reply_token, "這份劇本沒有附帶預製調查員角色卡，用 /coc pc 或 /coc create 自己建立角色吧。")
            return
        lines = ["這份劇本內建了以下預製調查員："]
        for i, p in enumerate(state.pregens, start=1):
            lines.append(f"{i}. {p.get('name', '未命名')}（{p.get('occupation', '未知職業')}）")
        lines.append("輸入「/coc usepregen 編號 [自訂名稱]」使用其中一位。")
        await _reply(event.reply_token, "\n".join(lines))
        return

    if sub == "usepregen":
        if len(parts) < 3:
            await _reply(event.reply_token, "用法：/coc usepregen 編號 [自訂名稱]")
            return
        state = load_state(group_id)
        if not state.pregens:
            await _reply(event.reply_token, "還沒有抓取過預製角色，先輸入「/coc pregens」看看有哪些。")
            return
        try:
            idx = int(parts[2])
        except ValueError:
            await _reply(event.reply_token, "編號必須是數字。")
            return
        if not (1 <= idx <= len(state.pregens)):
            await _reply(event.reply_token, f"編號超出範圍，目前有 {len(state.pregens)} 位預製角色。")
            return
        char = pregen_extractor.pregen_to_character(state.pregens[idx - 1], user_id)
        if len(parts) > 3:
            char.name = parts[3]
        state.characters[user_id] = char
        save_state(state)
        await _reply(event.reply_token, f"已使用預製角色！\n\n{char.sheet_text()}")
        return

    if sub == "combat":
        await _handle_combat_subcommand(event, group_id, parts)
        return

    await _reply(event.reply_token, _HELP_TEXT)


async def _handle_combat_subcommand(event: MessageEvent, group_id: str, parts: list[str]) -> None:
    action = parts[2] if len(parts) > 2 else None
    state = load_state(group_id)

    if action == "start":
        combat.start_combat(state)
        save_state(state)
        await _reply(event.reply_token, combat.status_text(state))
        return

    if action == "addnpc":
        if len(parts) < 6:
            await _reply(event.reply_token, "用法：/coc combat addnpc 名稱 DEX HP")
            return
        name, dex_str, hp_str = parts[3], parts[4], parts[5]
        try:
            dex, hp = int(dex_str), int(hp_str)
        except ValueError:
            await _reply(event.reply_token, "DEX 和 HP 必須是整數。")
            return
        combat.add_npc(state, name, dex, hp)
        save_state(state)
        await _reply(event.reply_token, combat.status_text(state))
        return

    if action == "status":
        await _reply(event.reply_token, combat.status_text(state))
        return

    if action == "next":
        result = combat.advance_turn(state)
        save_state(state)
        if not result["ok"]:
            await _reply(event.reply_token, result["error"])
            return
        await _reply(event.reply_token, f"第 {result['round']} 輪，輪到「{result['current_turn']}」了（HP {result['hp']}/{result['hp_max']}）。")
        return

    if action == "damage":
        if len(parts) < 5:
            await _reply(event.reply_token, "用法：/coc combat damage 名稱 增減量（受傷用負數，例如 -5）")
            return
        name, delta_str = parts[3], parts[4]
        try:
            delta = int(delta_str)
        except ValueError:
            await _reply(event.reply_token, "增減量必須是整數。")
            return
        result = combat.damage_combatant(state, name, delta)
        save_state(state)
        if not result["ok"]:
            await _reply(event.reply_token, result["error"])
            return
        tag = "（已倒下）" if result["defeated"] else ""
        await _reply(event.reply_token, f"{result['name']} HP 變為 {result['hp']}/{result['hp_max']}{tag}")
        return

    if action == "end":
        combat.end_combat(state)
        save_state(state)
        await _reply(event.reply_token, "戰鬥已結束。")
        return

    await _reply(
        event.reply_token,
        "用法：\n"
        "/coc combat start → 開始戰鬥\n"
        "/coc combat addnpc 名稱 DEX HP → 加入敵人\n"
        "/coc combat status → 查看目前狀態\n"
        "/coc combat next → 推進到下一位的回合\n"
        "/coc combat damage 名稱 增減量 → 調整 HP\n"
        "/coc combat end → 結束戰鬥",
    )
