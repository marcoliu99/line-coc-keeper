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
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from app import combat, creation, dice, intent_parser, keeper, locks, luck, pdf_loader, pregen_extractor
from app import scenario_compare, scenario_index, scenario_intro, scenario_rag
from app import scene_map as scene_map_engine
from app.config import SCENARIO_RAG_ENABLED
from app.models import BASE_SKILLS, OCCUPATIONS, Character, GroupState, generate_investigator
from app.state import clear_page_images, load_page_image, load_state, save_page_image, save_state

_logger = logging.getLogger(__name__)

Reply = Callable[[str], Awaitable[None]]
GetDisplayName = Callable[[], Awaitable[str]]
# (owner_id) -> a platform-appropriate way to reference that player in text —
# Discord supplies "<@{owner_id}>" (a real clickable mention, resolved
# client-side, no API call needed); an adapter with nothing better (LINE has
# no equivalent lightweight mention token) can default to the bare owner_id,
# which is also this type's default via _build_readiness_roster's own
# parameter default — kept adapter-injected rather than hardcoded here so
# this module stays platform-agnostic (see app/main.py, the LINE adapter,
# which shares every function in this file with app/discord_bot.py).
FormatMention = Callable[[str], str]
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
・/coc start → 角色都建好、準備開始時輸入，守密人會生成開場白帶大家進入劇情（優先用劇本自己寫的開場文字，沒有才自動生成）

【KP 助手】
・/coc kp → 登記自己為本局唯一的 KP 助手
・/coc kp quit → 解除自己的 KP 助手身分
・每局只能有一位 KP 助手；KP 助手與調查員角色互斥

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
・/coc index → 手動重建 NPC／怪物與地點索引（上傳劇本 PDF 時已經會自動建立一次，這個指令是需要重建時才用）
・/coc setpersona <文字> → 自訂這個群組守密人的語氣風格（預設是冷酷旁觀者），/coc setpersona reset 重設回預設
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


def _merge_extracted_pregens(state: GroupState, pregens: list[dict]) -> None:
    """Reconciles freshly LLM-extracted pregens (see handle_pdf_upload, which
    now always runs extract_pregens at upload time — not lazily behind
    /coc pregens, which used to mean a manually-uploaded role_ card sitting
    in state.pregens FIRST would make /coc pregens' own `if not
    state.pregens:` guard skip extraction entirely, so the scenario's own
    cast never even got a chance to reconcile against it) into state.pregens
    via the same identity-matching merge a manual role-sheet upload uses
    (pregen_extractor.reconcile_pregen_into_pool) — never a blind
    overwrite, and never simply discarded either. This is what makes upload
    order irrelevant: a role card uploaded before OR after the scenario PDF
    ends up correctly merged with (or kept alongside) the scenario's own
    embedded pregens either way, matching Module 4's design. The only place
    that still does a full wipe is /coc newgame (a fresh, all-defaults
    GroupState()) — a PDF upload, new scenario or corrected, never has to
    guess "is this pregen pool stale" the way an earlier version of this
    function did (it used to unconditionally reset state.pregens = [] on
    every new-scenario upload, which — this was reported directly — could
    also destroy a role card uploaded moments before the group's very first
    PDF, since that path has no existing scenario to be ambiguous against
    and so never even offers the new-vs-correction button)."""
    for pregen in pregens:
        state.pregens, _ = pregen_extractor.reconcile_pregen_into_pool(state.pregens, pregen)


def _apply_new_scenario(
    state: GroupState,
    text: str,
    title: str,
    extracted_index: dict[str, list],
    page_maps: dict,
    pregens: list[dict],
) -> None:
    """"全新劇本" (see handle_pdf_upload/resolve_pdf_upload_choice below),
    and also what a conversation's very first-ever PDF upload does, since
    there's no existing position to protect yet in that case either way."""
    state.scenario_text = text
    state.scenario_title = title
    state.active = True
    state.openai_previous_response_id = ""
    state.game_started = False  # a new scenario hasn't had its own /coc start opening yet —
    # otherwise a group re-uploading a different PDF mid-campaign without running /coc newgame
    # first would find /coc start permanently refusing ("already started") for the new scenario.
    state.kp_ooc_log = []  # new scenario must not inherit the previous scenario's KP OOC memory
    state.scenario_npc_index = extracted_index["npcs"]
    state.scenario_location_index = extracted_index["locations"]
    state.scene_maps = {str(k): v for k, v in page_maps.items()}  # same reasoning —
    # don't let a new scenario keep the old one's floor plans (see app/scene_map.py).
    state.current_map_page = {}
    state.current_room_id = {}
    state.party_facing = {}
    _merge_extracted_pregens(state, pregens)


def _apply_scenario_correction(
    state: GroupState, text: str, title: str, extracted_index: dict[str, list], pregens: list[dict]
) -> None:
    """"修正目前劇本" — updates the scenario's own text/index (the corrected
    content) but deliberately leaves scene_maps/current_map_page/
    current_room_id/party_facing/openai_previous_response_id/game_started
    untouched. That's exactly what "protect the party's existing position
    and progress" means when the scenario hasn't actually restarted — see
    docs/character_and_dictionary_system_spec.md's Module 1. pregens is NOT
    in that protected list — a corrected PDF's own re-extracted cast is
    reconciled in (see _merge_extracted_pregens), the same as _apply_new_
    scenario, since fixing e.g. a garbled stat in an embedded pregen is
    exactly the kind of correction this mode exists for; reconciliation
    (not a wipe) is what keeps an already-claimed pregen's claimed_by intact
    through that update. Page images are handled by the caller,
    unconditionally, before this ever runs — see handle_pdf_upload's own
    comment on why they don't depend on this choice at all."""
    state.scenario_title = title
    state.scenario_text = text
    state.scenario_npc_index = extracted_index["npcs"]
    state.scenario_location_index = extracted_index["locations"]
    _merge_extracted_pregens(state, pregens)


def _pdf_upload_confirmation_text(
    title: str,
    text: str,
    low_text_pages: list[int],
    truncated: bool,
    page_maps: dict,
    extracted_index: dict,
    pregen_count: int,
) -> str:
    """Shared by the immediate (first-ever upload) and deferred (button-
    resolved) paths through handle_pdf_upload — the message is identical
    either way, just built at a different point in the flow."""
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
        # Keys may be plain ints (fresh from pdf_loader.extract_text) or
        # strings (round-tripped through pending_pdf_upload's JSON storage —
        # see resolve_pdf_upload_choice) — sort numerically either way so a
        # page 10 doesn't sort before page 2.
        pages_str = "、".join(str(p) for p in sorted(page_maps.keys(), key=lambda k: int(k)))
        map_note = (
            f"\n\n🗺️ 第 {pages_str} 頁偵測到平面圖，已經拆解成房間圖——玩家在裡面移動時"
            "（例如「進入燈塔，檢查右手邊第一個房間」）系統會直接算出正確房間，不用靠守密人自己猜方位。"
            "用 `/coc where` 可以看目前在哪個房間。"
        )
    index_note = ""
    npc_count = len(extracted_index["npcs"])
    if npc_count:
        index_note = (
            f"\n\n📇 已自動建立劇本索引（{npc_count} 個 NPC／怪物"
            + (f"、{len(extracted_index['locations'])} 個地點" if extracted_index["locations"] else "")
            + "）——守密人之後提到這些對象時會直接照索引的數值講，同一隻不會前後不一致。"
            "劇本內容之後如果有更新，重新跑一次「/coc index」可以重建。"
        )

    if pregen_count:
        # Extraction (see handle_pdf_upload) already ran by the time this
        # message is built, so this can state a fact instead of the old
        # conditional "先輸入 /coc pregens 看看有沒有" hedge.
        pregen_note = (
            f"這份劇本內建了 {pregen_count} 位預製調查員，輸入「/coc pregens」查看、"
            "「/coc pregen 編號」看某位的完整能力——有內建角色的話，"
            "「/coc pc 角色名 職業」就只能從那些角色裡選一個。\n"
        )
    else:
        pregen_note = (
            "這份劇本沒有偵測到內建的預製調查員，直接用「/coc pc 角色名 職業」快速生成即可，"
            "這時職業可選：\n" + "、".join(OCCUPATIONS.keys()) + "\n"
        )

    return (
        f"已載入劇本《{title}》（{len(text)} 字）。\n"
        + pregen_note
        + "建好角色後，直接在群組打字描述行動即可開始冒險！"
        + warning
        + map_note
        + index_note
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
    same callback for both.

    If a scenario is already running (state.scenario_text non-empty), this
    doesn't guess whether the new upload is a genuinely new scenario or a
    corrected re-upload of the same one — it stashes the extraction into
    state.pending_pdf_upload and asks the GM to pick, via
    resolve_pdf_upload_choice (an adapter posts the actual buttons — see
    app/discord_bot.py's _post_pdf_upload_buttons, the same diff-and-post
    pattern as pending_checks/pending_luck_decisions). Guessing wrong here is
    worse than one extra click: a wrongly-preserved position could point at a
    room that doesn't exist in the new scenario's map at all. A
    conversation's very first upload has no existing scenario to be
    ambiguous against, so it always applies immediately with no button."""
    if not file_name.lower().endswith(".pdf"):
        await reply("目前只支援上傳 PDF 劇本檔案喔。")
        return

    # Checked before any of the expensive extraction work below (and before
    # clear_page_images, which unconditionally wipes the current scenario's
    # page images) — a second PDF landing while an earlier pending_pdf_upload
    # choice is still unresolved would otherwise silently overwrite it, and
    # whichever button the GM clicks afterward (still labelled for the FIRST
    # upload — buttons carry no upload-specific id) would end up applying the
    # SECOND upload's content instead, which is especially bad for "全新劇本"
    # (wipes map position, resets the LLM conversation thread).
    existing_state = load_state(conversation_id)
    if existing_state.pending_pdf_upload is not None:
        await reply(
            f"上一次上傳的《{existing_state.pending_pdf_upload['title']}》還沒選擇「全新劇本」"
            "還是「修正目前劇本」，請先點上一則訊息的按鈕選完，再上傳這份新的 PDF——不然這份新的"
            "會蓋掉還沒處理的那份，之後點到舊按鈕會套用到錯的內容。"
        )
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

    # Built automatically here rather than left to a manual /coc index run —
    # a NPC/monster stat index nobody remembered to build is indistinguishable
    # from this feature not existing at all. Same text-only analyze_text call
    # /coc index itself makes (see app/scenario_index.py), just triggered at
    # upload time instead of on demand; degrades to {"npcs": [], "locations":
    # []} on any failure (no provider configured, extraction call failing),
    # same as before this existed — never blocks the upload from succeeding.
    extracted_index = await asyncio.to_thread(scenario_index.extract_scenario_index, text)

    # Also extracted eagerly, at upload time, rather than lazily behind the
    # first /coc pregens call the old code waited for — that lazy trigger
    # had its own bug: /coc pregens only ever calls extract_pregens when
    # state.pregens is currently empty, so a role_ card uploaded before
    # anyone ran /coc pregens would make that guard skip extraction forever,
    # and the scenario's own embedded cast would never even get a chance to
    # reconcile against it. See _merge_extracted_pregens for how this result
    # gets folded into state.pregens without regard to upload order.
    pregens = await asyncio.to_thread(pregen_extractor.extract_pregens, text)

    async with locks.get_conversation_lock(conversation_id):

        # Page images update the same way regardless of which mode a GM later
        # picks for an ambiguous re-upload (see below) — applied immediately,
        # unconditionally, so there's nothing image-related left inside the
        # pending choice to defer. Done INSIDE get_conversation_lock (moved
        # here from before the lock) — two PDFs landing for the same
        # conversation close together both run their (unlocked, concurrent)
        # extraction, and without this serialization their image writes can
        # interleave (A clears, B clears+writes, A writes-after-B) leaving
        # /coc showpage serving the wrong upload's pages for whichever
        # scenario the locked state update below actually ends up current.
        clear_page_images(conversation_id)  # don't let a new scenario's /coc
        # showpage 5 show the OLD scenario's page 5.

        for page_number, png_bytes in page_images.items():
            save_page_image(conversation_id, page_number, png_bytes)

        state = load_state(conversation_id)
        # Re-checked here, INSIDE the lock, not just the early check above —
        # the early check runs before extraction (which can take up to ~a
        # minute per the ack message below), so two PDFs landing close
        # together both pass it before either has a pending_pdf_upload saved
        # yet, then race for this lock. Without re-checking here, whichever
        # one acquires the lock second would silently overwrite the first
        # one's still-unresolved pending_pdf_upload — same clobbering bug the
        # early check exists to prevent, just via the concurrent path instead
        # of the sequential one.
        if state.pending_pdf_upload is not None:
            raced = True
        elif state.scenario_text.strip():
            raced = False
            state.pending_pdf_upload = {
                "text": text,
                "title": title,
                "low_text_pages": low_text_pages,
                "truncated": truncated,
                "npcs": extracted_index["npcs"],
                "locations": extracted_index["locations"],
                "page_maps": {str(k): v for k, v in page_maps.items()},
                "pregens": pregens,
            }
            save_state(state)
            current_title = state.scenario_title
            confirmation_pending = True
        else:
            raced = False
            _apply_new_scenario(state, text, title, extracted_index, page_maps, pregens)
            save_state(state)
            confirmation_pending = False
            final_pregen_count = len(state.pregens)

    if raced:
        await push(
            f"這份《{title}》來得比較慢——另一份幾乎同時上傳的 PDF 先卡進待確認狀態了，請先處理完"
            "上一則訊息的選擇，再重新上傳這份。"
        )
        return

    if confirmation_pending:
        await push(
            f"這個群組目前正在跑《{current_title}》。新上傳的《{title}》"
            "是要開始一個全新的劇本，還是修正/補完目前這份劇本？請點下面的按鈕選擇——"
            "選錯的代價不小（位置可能對到新劇本裡不存在的房間），拿不準的話選「修正目前劇本」比較安全。"
        )
        return

    await push(_pdf_upload_confirmation_text(
        title, text, low_text_pages, truncated, page_maps, extracted_index, final_pregen_count
    ))


def _resolve_pdf_upload_choice_locked(conversation_id: str, choice: str) -> str:
    """Body of resolve_pdf_upload_choice, factored out so it can be called
    from a context that already holds get_conversation_lock (see the "/coc
    pdf new"/"/coc pdf fix" text-command path below, which runs inside
    _handle_coc_command — itself already called under that same lock by
    handle_text_message; asyncio.Lock isn't reentrant, so calling the
    lock-acquiring version from in there would deadlock). Returns the
    confirmation text to send; the caller does the actual reply/push."""
    state = load_state(conversation_id)
    pending = state.pending_pdf_upload
    if pending is None:
        return "這個上傳選擇已經處理過了，或已經過期失效，請重新上傳 PDF。"
    extracted_index = {"npcs": pending["npcs"], "locations": pending["locations"]}
    pending_pregens = pending.get("pregens", [])
    if choice == "new":
        _apply_new_scenario(
            state, pending["text"], pending["title"], extracted_index, pending["page_maps"], pending_pregens
        )
    else:
        _apply_scenario_correction(state, pending["text"], pending["title"], extracted_index, pending_pregens)
    state.pending_pdf_upload = None
    save_state(state)
    final_pregen_count = len(state.pregens)
    return _pdf_upload_confirmation_text(
        pending["title"], pending["text"], pending["low_text_pages"], pending["truncated"],
        pending["page_maps"], extracted_index, final_pregen_count,
    )


async def resolve_pdf_upload_choice(conversation_id: str, choice: str, push: Reply) -> None:
    """Called by an adapter's button callback (see app/discord_bot.py's
    PdfUploadChoiceButton) once the GM picks between the two options
    handle_pdf_upload's pending_pdf_upload flow offers. `choice` must be
    "new" or "fix". LINE has no button/interaction mechanism, so it has no
    caller for this — see the "/coc pdf new"/"/coc pdf fix" text-command
    path in _handle_coc_command, which every platform (including Discord,
    for parity) can use instead."""
    async with locks.get_conversation_lock(conversation_id):
        text = _resolve_pdf_upload_choice_locked(conversation_id, choice)
    await push(text)


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
    PDF text. Re-uploading a sheet that character_matcher.is_same_character
    judges to be the same investigator as an existing pool entry reconciles
    into it (merging if the two came from different sources, replacing if
    the same — see pregen_extractor.reconcile_pregen_into_pool) instead of
    always dead-reckoning on an exact occupation-string match, which used to
    incorrectly collide two different players both wanting to play e.g. a
    "警察"."""
    pregen = pregen_extractor.parse_role_sheet_text(file_text)
    if pregen is None:
        await reply(f"「{file_name}」看起來不是預期的角色卡格式（找不到【屬性】區塊），沒有儲存。")
        return

    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        state.pregens, action = pregen_extractor.reconcile_pregen_into_pool(state.pregens, pregen)
        save_state(state)

    name_note = f"「{pregen['name']}」" if pregen["name"] else "（姓名由玩家決定）"
    action_note = {"added": "已新增", "replaced": "已更新", "merged": "已與現有角色比對成功，完成擇優融合"}[action]
    await reply(
        f"角色卡{action_note}：{name_note}，職業「{pregen['occupation']}」，"
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


# Background maintenance tasks (see _spawn_post_turn_maintenance below) have
# to be kept referenced somewhere until they finish, or asyncio is free to
# garbage-collect a still-running Task out from under itself. This set exists
# purely to hold that reference; each task removes itself once done.
_pending_maintenance_tasks: set[asyncio.Task] = set()


def _spawn_post_turn_maintenance(conversation_id: str) -> None:
    """Fires keeper.run_post_turn_maintenance as an independent background
    task instead of awaiting it inline. It used to be awaited from *inside*
    the Keeper turn lock (and, on most call paths, the coarser per-
    conversation lock too) — see _run_post_turn_maintenance_after_output
    below. The player already has their reply by the time this runs; when a
    trim actually fires (roughly every MAX_LOG_TURNS*2 turns), this call
    makes a real LLM summarization request (2-5s) plus an embeddings API call
    (300-800ms) synchronously in a worker thread, which meant the *next*
    message for this conversation — even from a different player, doing
    something with nothing to do with campaign_summary or memory indexing —
    sat blocked behind that lock for however long maintenance happened to
    take.

    Detaching this from the turn-level locks is only safe because
    keeper.run_post_turn_maintenance was hardened to tolerate running fully
    unlocked around its own slow LLM/embedding calls: a per-group_id
    in-flight guard keeps two passes for the same conversation from ever
    overlapping, and its persist step re-derives what to trim from a freshly
    reloaded state.log (content-matched against the chunk it actually
    summarized) instead of blindly overwriting with a pre-computed snapshot
    — otherwise a concurrent turn's _commit_turn_result landing in the gap
    while maintenance is mid-flight would have its new log entries silently
    discarded when maintenance's stale snapshot got written back. See
    keeper.py's run_post_turn_maintenance/_persist_memory_maintenance_state
    docstrings for the details; this was found and confirmed by data-loss
    reproduction during PR review, not from first-principles design."""
    task = asyncio.create_task(_run_post_turn_maintenance_safely(conversation_id))
    _pending_maintenance_tasks.add(task)
    task.add_done_callback(_pending_maintenance_tasks.discard)


async def _run_post_turn_maintenance_safely(conversation_id: str) -> None:
    try:
        await asyncio.to_thread(keeper.run_post_turn_maintenance, conversation_id)
    except Exception:
        _logger.exception("post-turn maintenance failed (background) for conversation_id=%s", conversation_id)


async def _run_post_turn_maintenance_after_output(
    conversation_id: str,
    reply: Reply,
    public_message: str,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    private_messages: list[tuple[str, str]],
    image_requests: list[tuple[str | None, int]],
    run_maintenance: bool = True,
) -> None:
    try:
        await reply(public_message)
        await _deliver_side_effects(conversation_id, send_dm, send_image, send_dm_image, private_messages, image_requests)
    finally:
        if run_maintenance:
            _spawn_post_turn_maintenance(conversation_id)


def _skill_names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


_CHECK_TIER_ZH = {
    "fumble": "大失敗", "fail": "失敗", "regular": "成功",
    "hard": "困難成功", "extreme": "極難成功", "critical": "大成功",
}

NATURAL_1_BONUS_PROMPT = (
    "【大成功額外獎勵】\n"
    "玩家本次 d100 檢定擲出自然 1，取得大成功。除了正常處理這次成功應得到的結果外，"
    "請根據當前劇本、場景與玩家行動，自行給予一個合理、有限的額外 bonus。\n"
    "優先考慮：提升資訊品質、提高效率、避免眼前危險、取得位置／情境優勢，"
    "或其他立即生效且不需要後續追蹤的額外收益。\n"
    "不要因此跳過核心挑戰、直接揭露尚未應該知道的劇本核心秘密、改寫既有劇本事實，"
    "或給予需要在未來回合記住與兌現的延後機械效果。"
)


def _tier_zh_for_tier(tier: str, required: str) -> str:
    """Human-readable outcome for one (tier, required_tier) pair, accounting
    for a required difficulty tier (dice.SkillCheckResult.required_tier —
    see keeper.py's skill_check tool's `difficulty` param) higher than the
    roll's own intrinsic tier. COC7e: a task flagged Hard/Extreme needs a
    roll of at least that tier to count as a success at all — a Regular-tier
    roll against a Hard-required task is simply a failure, not a partial
    success, and must be displayed as one rather than misleadingly showing
    "成功" for a check that actually failed."""
    if required == "regular" or tier not in ("regular", "hard"):
        return _CHECK_TIER_ZH[tier]
    if dice.TIER_RANK[tier] >= dice.TIER_RANK[required]:
        return _CHECK_TIER_ZH[tier]
    required_zh = {"hard": "困難成功", "extreme": "極難成功"}[required]
    return f"失敗（擲骰達到「{_CHECK_TIER_ZH[tier]}」，但這次判定需要至少「{required_zh}」）"


def _tier_zh_for_result(r) -> str:
    return _tier_zh_for_tier(r.tier, getattr(r, "required_tier", "regular"))


def _natural_1_bonus_prompt_for_result(r: dice.SkillCheckResult) -> str:
    """Return the Natural 1 bonus prompt only when the original SkillCheckResult.roll is 1.

    This deliberately checks the raw roll instead of tier == "critical", so a
    later Luck-spend tier upgrade cannot be mistaken for a natural 1.
    """
    return NATURAL_1_BONUS_PROMPT if r.roll == 1 else ""


@dataclass
class _CheckResolution:
    state: GroupState | None = None
    char: object | None = None
    roll_line: str = ""
    keeper_message: str = ""
    roll_feedback_text: str = ""
    keeper_header: str = ""
    reply_text: str = ""
    should_finalize: bool = False


@dataclass
class _MapActionResolution:
    context: dict | None = None
    needs_rag: bool = False


@dataclass
class _AwayStateResult:
    character_name: str = ""
    error_text: str = ""


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
    major_wound_trigger: bool = False,
) -> tuple[str, str]:
    """Builds (roll_line, keeper_message) for a resolved skill/choice check —
    shared by the immediate-finalize path and handle_luck_decision (after a
    Luck spend has overridden r.tier). luck_spent > 0 adds a note both humans
    and the Keeper can see that the tier was bought up, not rolled naturally.
    attacker_tier (only set for a Dodge/Fight Back choice — see
    keeper.py's offer_check_choice/npc_skill_check) triggers the COC7e
    opposed-roll comparison, named explicitly in both messages.
    major_wound_trigger (see keeper.py's adjust_character tool) is the CON
    check chained onto a single hit dealing >= half max HP — unlike the Bout
    of Madness INT check this flows through the normal Luck-spend path
    (success here is a plain good outcome), so the status_tags side effect
    on failure has to live here, in the one place both the immediate and the
    Luck-spend-decision paths converge, rather than in an early-return branch."""
    tier_zh = _tier_zh_for_result(r)
    dice_note = f"（獎勵骰x{bonus}）" if bonus else f"（懲罰骰x{penalty}）" if penalty else ""
    luck_note = ""
    if luck_spent:
        original_zh = _tier_zh_for_tier(original_tier, getattr(r, "required_tier", "regular"))
        luck_note = f"（花費 {luck_spent} 點 Luck，將結果從「{original_zh}」提升為「{tier_zh}」）"

    opposed_line = ""
    opposed_message = ""
    if attacker_tier is not None:
        is_counter = display_label is not None and "反擊" in display_label
        opposed_text = _describe_opposed_outcome(char.name, is_counter, r.tier, attacker_tier)
        opposed_line = f"\n⚔️ {opposed_text}"
        opposed_message = f"（{opposed_text}）"

    major_wound_line = ""
    major_wound_message = ""
    if major_wound_trigger:
        if r.success:
            major_wound_line = "\n💪 重傷 CON 檢定通過，勉強撐住意識，沒有昏迷"
            major_wound_message = (
                "（這次「CON」檢定是 COC7e 重傷規則：這次單一傷害達到角色最大 HP 一半以上，本來有"
                "當場昏迷的風險，但檢定通過了，角色勉強撐住意識——請描述角色忍痛維持行動能力的樣子，"
                "這仍然是一次重傷，不要讓角色表現得行動如常。）"
            )
        else:
            for tag in ("昏迷", "倒地"):
                if tag not in char.status_tags:
                    char.status_tags.append(tag)
            major_wound_line = "\n💥 重傷 CON 檢定失敗，角色當場昏迷倒地！"
            major_wound_message = (
                "（這次「CON」檢定是 COC7e 重傷規則：這次單一傷害達到角色最大 HP 一半以上，檢定失敗，"
                "角色當場昏迷倒地——已經加上「昏迷」「倒地」狀態標籤。請描述角色失去意識倒下的過程；"
                "昏迷期間角色沒辦法自主行動或說話，直到有人處理或角色之後自然甦醒，記得呼叫 "
                "remove_status_tag 移除這兩個標籤。）"
            )

    if display_label is not None:
        roll_line = f"🎲 {char.name} 選擇「{display_label}」（{skill_name} {value}%{dice_note}），擲出 {r.roll} → {tier_zh}{luck_note}{opposed_line}{major_wound_line}"
        keeper_message = (
            f"（{char.name} 在多個選項裡選了「{display_label}」，擲骰做了一次「{skill_name}」檢定："
            f"技能值 {value}%{dice_note}，擲出 {r.roll} → {tier_zh}{luck_note}。這是已經確定的結果，請根據這個結果"
            f"描述後續發展，不要重新判定或改變這個結果，也不要質疑玩家選了哪個選項。）{opposed_message}{major_wound_message}"
        )
    else:
        roll_line = f"🎲 {char.name} 的「{skill_name}」檢定：{value}%{dice_note}，擲出 {r.roll} → {tier_zh}{luck_note}{opposed_line}{major_wound_line}"
        keeper_message = (
            f"（{char.name} 擲骰做了一次「{skill_name}」檢定：技能值 {value}%{dice_note}，"
            f"擲出 {r.roll} → {tier_zh}{luck_note}。這是已經確定的結果，請根據這個結果描述後續發展，"
            f"不要重新判定或改變這個結果。）{opposed_message}{major_wound_message}"
        )
    natural_1_bonus_prompt = _natural_1_bonus_prompt_for_result(r)
    if natural_1_bonus_prompt:
        keeper_message = f"{keeper_message}\n\n{natural_1_bonus_prompt}"
    return roll_line, keeper_message


def _build_split_check_feedback(
    character_name: str,
    check_label: str,
    value_text: str,
    roll: int,
    outcome_text: str,
    opposed_text: str = "",
    result_line: str | None = None,
) -> tuple[str, str]:
    opposed_line = f"\n⚔️ {opposed_text}" if opposed_text else ""
    result_line = result_line or f"{roll} → {outcome_text}"
    roll_feedback_text = (
        f"🎲 {character_name}｜{check_label} {value_text}\n"
        f"{result_line}{opposed_line}\n\n"
        "後續結果由守密人處理中……"
    )
    keeper_header = f"🎭 {character_name}｜{check_label}{outcome_text}"
    return roll_feedback_text, keeper_header


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
    split_roll_feedback: bool = False,
    acquire_legacy_for_keeper: bool = False,
    roll_feedback_text: str = "",
    keeper_header: str = "",
) -> None:
    """Shared tail for every resolved check (sanity, choice, plain skill, and
    a Luck-spend decision) — hands the already-determined result to the
    Keeper for narration and delivers whatever it queued."""
    if split_roll_feedback:
        await reply(roll_feedback_text or roll_line)

    async def run_keeper_phase() -> None:
        # Deliberately NOT running keeper_message through _resolve_map_action:
        # keeper_message is a system-generated result narration (e.g. "（角色
        # 擲骰做了一次「CON」檢定...）"), not the player's own words — but
        # intent_parser.has_movement_verb's trigger list is broad enough (a
        # bare "去"/"走" is enough) that ordinary narration text can trip it
        # by accident (e.g. major-wound's "...請描述角色失去意識倒下的過程"
        # contains "去"). When that happens, _resolve_map_action_core falls
        # back to fuzzy-matching the *entire* narration text against every
        # room name on the current map — any short, common room name (臥室,
        # 書房, ...) that happens to appear as a substring anywhere in that
        # text gets treated as "the player just moved there", handed to the
        # Keeper as an authoritative Map Engine result it's told not to
        # second-guess. That's a real, observed bug (an apparent teleport to
        # an unrelated room right after a skill/sanity check), not a
        # theoretical one. Passing None here costs nothing useful: the Keeper
        # still learns the character's actual current room from state.
        # current_map_page/current_room_id via _build_dynamic_prompt's own
        # "resolved_location is None" fallback block — it just won't be
        # mislabeled as a fresh Map Engine move this check never made.
        resolved_location = None
        async with locks.get_keeper_turn_lock(conversation_id):
            keeper_reply, private_messages, image_requests = await asyncio.to_thread(
                keeper.run_turn, state, user_id, char.name, keeper_message, resolved_location, "player"
            )
            if split_roll_feedback:
                public_message = f"{keeper_header}\n\n{keeper_reply}" if keeper_header else keeper_reply
            else:
                public_message = f"{roll_line}\n\n{keeper_reply}"
            await _run_post_turn_maintenance_after_output(
                conversation_id,
                reply,
                public_message,
                send_dm,
                send_image,
                send_dm_image,
                private_messages,
                image_requests,
            )

    if acquire_legacy_for_keeper:
        async with locks.get_conversation_lock(conversation_id):
            await run_keeper_phase()
    else:
        await run_keeper_phase()


def _resolve_check_deterministically(conversation_id: str, user_id: str, text: str) -> _CheckResolution:
    """Resolve /coc check without invoking the Keeper.

    This consumes/restores pending check state, rolls local dice, creates Luck
    pending decisions when applicable, and saves any deterministic state changes.
    Keeper narration stays in handle_check_command's async tail.
    """
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        if not state.active:
            return _CheckResolution(reply_text="目前沒有進行中的遊戲。")
        char = state.characters.get(user_id)
        if not char:
            return _CheckResolution(reply_text="你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")

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
                return _CheckResolution(reply_text=f"這是需要選擇的檢定，請輸入「/coc check <選項名稱>」，可選：{options_text}")
            matched = next(
                (o for o in pending["options"]
                 if _skill_names_match(o["label"], skill_arg) or _skill_names_match(o["skill"], skill_arg)),
                None,
            )
            if not matched:
                state.pending_checks[user_id] = pending
                options_text = "、".join(o["label"] for o in pending["options"])
                return _CheckResolution(reply_text=f"沒有「{skill_arg}」這個選項，可選：{options_text}")
            choice_skill_name, choice_display_label = matched["skill"], matched["label"]
            choice_value, choice_bonus, choice_penalty = matched["skill_value"], matched["bonus_dice"], matched["penalty_dice"]
            choice_attacker_tier = pending.get("attacker_tier")
            pending = None
        elif skill_arg is None:
            if not pending:
                return _CheckResolution(reply_text="目前沒有守密人請你做的檢定。用法：/coc check 技能名 [獎勵骰數] [懲罰骰數] 可以自己主動檢定。")
        elif not (pending and pending.get("type") == "skill" and _skill_names_match(pending.get("skill", ""), skill_arg)):
            # Named a skill that doesn't match what was pending (or nothing was
            # pending, or the pending one was a SAN check): a fresh, self-initiated
            # check, bonus/penalty from the command's own args instead.
            pending = None

        if pending and pending.get("type") == "sanity":
            san_before = char.san
            sanity_result = dice.sanity_check(
                san_before, pending.get("loss_success", "0"), pending.get("loss_failure", "1d4")
            )
            char.san = sanity_result.san_after
            outcome = "通過" if sanity_result.check.success else "失敗"
            roll_line = (
                f"🎲 {char.name} 的理智檢定：SAN {san_before}，擲出 {sanity_result.check.roll} → {outcome}，"
                f"損失 {sanity_result.loss} 點理智（現在 SAN {sanity_result.san_after}）"
            )

            if sanity_result.risk_of_madness:
                # COC7e Bout of Madness: losing 5+ SAN in one go triggers a
                # separate INT check — chained the same way a Luck-spend decision
                # chains onto a check's result, registered as a fresh pending
                # check the player rolls themselves (never silently resolved by
                # the Keeper). See dice.roll_madness's own docstring for why
                # *succeeding* this INT check is the outcome that triggers
                # madness, not failing it — easy to get backwards.
                int_value = keeper.resolve_skill_value(char, "INT")
                state.pending_checks[user_id] = {
                    "type": "skill", "skill": "INT", "skill_value": int_value,
                    "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
                    "madness_trigger": True, "madness_realtime": True,
                }
                roll_line += (
                    "\n⚠️ 這次損失達到 5 點以上，觸發 COC7e「短暫瘋狂」規則：需要做一次 INT 檢定——"
                    "成功代表當場理解了這份恐怖、陷入短暫瘋狂；失敗代表壓抑下來，沒有當場失常。"
                    "請輸入 /coc check INT。"
                )
                keeper_message = (
                    f"（{char.name} 擲骰做了理智檢定：SAN {san_before} 擲出 {sanity_result.check.roll} → {outcome}，"
                    f"損失 {sanity_result.loss} 點理智，現在 SAN {sanity_result.san_after}。這次損失達到 5 點以上，"
                    f"觸發 COC7e「短暫瘋狂」規則的 INT 檢定，系統已經請玩家去骰，你只能先描述受到這波衝擊當下的"
                    f"直接反應，還不知道會不會當場失常，等 INT 檢定結果出來才能繼續描述後續——不要自己"
                    f"先講角色失常了或平安無事。）"
                )
            else:
                keeper_message = (
                    f"（{char.name} 擲骰做了理智檢定：SAN {san_before} 擲出 {sanity_result.check.roll} → {outcome}，"
                    f"損失 {sanity_result.loss} 點理智，現在 SAN {sanity_result.san_after}。這是已經確定的結果，"
                    f"請根據這個結果描述角色的反應與後續發展，不要重新判定或改變這個結果。）"
                )
            roll_feedback_text, keeper_header = _build_split_check_feedback(
                char.name, "理智檢定", f"SAN {san_before}", sanity_result.check.roll, outcome
            )
            save_state(state)
            return _CheckResolution(
                state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
                roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True
            )

        is_pushed = False
        attacker_tier = None
        difficulty = "regular"  # offer_check_choice options and a self-initiated /coc check with no
        # pending Keeper request have no difficulty concept — only a Keeper-registered plain skill_check
        # (see keeper.py's skill_check tool difficulty param) can set this above "regular".
        madness_trigger = False  # only set True for the INT check chained onto a >=5 SAN loss — see above
        madness_realtime = True
        major_wound_trigger = False  # only set True for the CON check chained onto a major wound — see
        # keeper.py's adjust_character tool. Unlike madness_trigger, this does NOT get an early-return
        # branch below: success here is a normal good outcome, so it flows through the ordinary Luck-spend
        # path like any other skill check — only _build_check_narration needs to know about it.
        if choice_skill_name is not None:
            skill_name, value, bonus, penalty = choice_skill_name, choice_value, choice_bonus, choice_penalty
            display_label = choice_display_label
            attacker_tier = choice_attacker_tier
        else:
            if pending:
                skill_name, value, bonus, penalty = pending["skill"], pending["skill_value"], pending["bonus_dice"], pending["penalty_dice"]
                is_pushed = bool(pending.get("pushed", False))
                difficulty = pending.get("difficulty", "regular")
                madness_trigger = bool(pending.get("madness_trigger", False))
                madness_realtime = bool(pending.get("madness_realtime", True))
                major_wound_trigger = bool(pending.get("major_wound_trigger", False))
            else:
                skill_name = skill_arg
                value = keeper.resolve_skill_value(char, skill_name)
                bonus = int(parts[3]) if len(parts) > 3 and parts[3].lstrip("-").isdigit() else 0
                penalty = int(parts[4]) if len(parts) > 4 and parts[4].lstrip("-").isdigit() else 0
                save_state(state)  # resolve_skill_value may have registered a new default-value skill
            display_label = None
        skill_result = dice.skill_check(value, bonus_dice=bonus, penalty_dice=penalty, required_tier=difficulty)

        if madness_trigger:
            # Bout of Madness INT check (see the "sanity" branch above that
            # registered this) — resolved separately from the generic skill-check
            # path below since a *success* here means rolling a real madness
            # table, not just narrating a plain check result; also deliberately
            # skips the Luck-spend flow entirely (spending Luck to push this
            # check toward success would be pushing toward the *worse* outcome
            # for the character, backwards from what Luck-spend normally means).
            tier_zh = _tier_zh_for_result(skill_result)
            if skill_result.success:
                madness = dice.roll_madness(realtime=madness_realtime)
                roll_line = (
                    f"🎲 {char.name} 的 INT 檢定：{value}%，擲出 {skill_result.roll} → {tier_zh}\n"
                    f"💥 觸發短暫瘋狂（Bout of Madness）！症狀擲骰 {madness['roll']} → 「{madness['symptom']}」"
                    f"（持續約{madness['duration']}）"
                )
                keeper_message = (
                    f"（{char.name} 的 INT 檢定{tier_zh}，觸發了短暫瘋狂：症狀是「{madness['symptom']}」"
                    f"——{madness['guidance']}，持續約{madness['duration']}。這是已經確定的結果，"
                    f"請照這個症狀具體描述角色接下來的失常行為，不要自己另外編一個症狀，也不要忽略這個結果。）"
                )
            else:
                roll_line = f"🎲 {char.name} 的 INT 檢定：{value}%，擲出 {skill_result.roll} → {tier_zh}\n（INT 檢定失敗，勉強壓下這股衝擊，沒有當場失常）"
                keeper_message = (
                    f"（{char.name} 的 INT 檢定{tier_zh}，沒有觸發短暫瘋狂——角色勉強壓下了這股衝擊，"
                    f"不需要描述任何失常行為，可以正常繼續劇情，但可以帶一點事後的心理陰影或後怕細節。）"
                )
            roll_feedback_text, keeper_header = _build_split_check_feedback(
                char.name, "INT", str(value), skill_result.roll, tier_zh
            )
            save_state(state)
            return _CheckResolution(
                state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
                roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True
            )

        # Luck-spend: only proactively offered when it's a near-miss (the cheapest
        # possible upgrade costs <= 7 Luck) — see app/luck.py. Sanity checks are
        # excluded (handled above, already finalized by this point), and so is a
        # Pushed Roll (COC7e optional rule: a pushed reroll's result is final,
        # can't be bought up again with Luck on top of it).
        luck_options = [] if is_pushed else luck.buyable_options(value, skill_result.roll, skill_result.tier, char.luck, difficulty)
        gate_cost = None if is_pushed else luck.cheapest_cost(value, skill_result.roll, skill_result.tier, difficulty)
        if luck_options and gate_cost is not None and gate_cost <= 7:
            state.pending_luck_decisions[user_id] = {
                "skill_name": skill_name, "display_label": display_label,
                "value": value, "roll": skill_result.roll, "bonus_dice": bonus, "penalty_dice": penalty,
                "original_tier": skill_result.tier, "attacker_tier": attacker_tier, "difficulty": difficulty,
                "options": [{"tier": o.tier, "cost": o.cost} for o in luck_options],
                "major_wound_trigger": major_wound_trigger,
            }
            save_state(state)
            options_text = "、".join(f"花 {o.cost} 點 Luck → {_CHECK_TIER_ZH[o.tier]}" for o in luck_options)
            dice_note = f"（獎勵骰x{bonus}）" if bonus else f"（懲罰骰x{penalty}）" if penalty else ""
            check_label = f"選擇「{display_label}」（{skill_name}）" if display_label is not None else f"「{skill_name}」"
            attacker_note = f"\n⚔️ 攻擊方擲出 → {_CHECK_TIER_ZH[attacker_tier]}" if attacker_tier is not None else ""
            return _CheckResolution(
                reply_text=(
                    f"🎲 {char.name} 的{check_label}檢定：{value}%{dice_note}，擲出 {skill_result.roll} → {_tier_zh_for_result(skill_result)}{attacker_note}\n"
                    f"目前 Luck {char.luck} 點，要花 Luck 買到更好的結果嗎？可選：{options_text}\n"
                    f"（點下面按鈕，或輸入「/coc luck skip」維持目前結果、「/coc luck regular/hard/extreme」花費對應點數）"
                )
            )

        roll_line, keeper_message = _build_check_narration(
            char, skill_name, display_label, value, skill_result, bonus, penalty, attacker_tier=attacker_tier,
            major_wound_trigger=major_wound_trigger,
        )
        opposed_text = ""
        if attacker_tier is not None:
            opposed_text = _describe_opposed_outcome(char.name, display_label is not None and "反擊" in display_label, skill_result.tier, attacker_tier)
        roll_feedback_text, keeper_header = _build_split_check_feedback(
            char.name, display_label or skill_name, str(value), skill_result.roll, _tier_zh_for_result(skill_result), opposed_text
        )
        save_state(state)
        return _CheckResolution(
            state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
            roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True
        )


async def handle_check_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
    split_roll_feedback: bool = False,
    acquire_legacy_for_keeper: bool = False,
) -> None:
    """/coc check [技能名] [獎勵骰數] [懲罰骰數] — the player's own roll, in
    code, visible to the group immediately, instead of the Keeper (LLM)
    quietly deciding a result. Pairs with keeper.py's skill_check/sanity_check
    tools, which now only *register* a pending check (see GroupState.
    pending_checks) instead of rolling — this command is what actually rolls
    the dice, then feeds the outcome back to the Keeper as an established
    fact for it to narrate, exactly like a normal free-text turn."""
    resolution = await asyncio.to_thread(_resolve_check_deterministically, conversation_id, user_id, text)
    if resolution.reply_text:
        await reply(resolution.reply_text)
        return
    if not resolution.should_finalize or resolution.state is None or resolution.char is None:
        return
    await _finalize_check_result(
        conversation_id, user_id, resolution.state, resolution.char, resolution.roll_line, resolution.keeper_message,
        reply, send_dm, send_image, send_dm_image, split_roll_feedback,
        acquire_legacy_for_keeper=acquire_legacy_for_keeper,
        roll_feedback_text=resolution.roll_feedback_text, keeper_header=resolution.keeper_header
    )


async def handle_luck_decision(
    conversation_id: str,
    user_id: str,
    choice: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    split_roll_feedback: bool = False,
    acquire_legacy_for_keeper: bool = False,
) -> None:
    """Resolves a pending Luck-spend decision (see handle_check_command above
    and app/luck.py) — either "skip" (keep the natural roll) or a tier name
    ("regular"/"hard"/"extreme") to buy up to, deducting the cost from the
    character's Luck before handing the (possibly improved) result to the
    Keeper exactly like a normal check."""
    resolution = await asyncio.to_thread(_resolve_luck_decision_deterministically, conversation_id, user_id, choice)
    if resolution.reply_text:
        await reply(resolution.reply_text)
        return
    if not resolution.should_finalize or resolution.state is None or resolution.char is None:
        return
    await _finalize_check_result(
        conversation_id, user_id, resolution.state, resolution.char, resolution.roll_line, resolution.keeper_message,
        reply, send_dm, send_image, send_dm_image, split_roll_feedback,
        acquire_legacy_for_keeper=acquire_legacy_for_keeper,
        roll_feedback_text=resolution.roll_feedback_text, keeper_header=resolution.keeper_header
    )


def _resolve_luck_decision_deterministically(
    conversation_id: str, user_id: str, choice: str
) -> _CheckResolution:
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        pending = state.pending_luck_decisions.pop(user_id, None)
        if not pending:
            return _CheckResolution(reply_text="目前沒有待決定的 Luck 花費。")
        char = state.characters.get(user_id)
        if not char:
            return _CheckResolution(reply_text="找不到你的角色。")

        tier = pending["original_tier"]
        luck_spent = 0
        if choice != "skip":
            option = next((o for o in pending["options"] if o["tier"] == choice), None)
            if not option:
                state.pending_luck_decisions[user_id] = pending  # not a valid option — put it back
                save_state(state)
                options_text = "、".join(f"{o['tier']}（{o['cost']} 點）" for o in pending["options"])
                return _CheckResolution(reply_text=f"這不是有效的選項，可選：{options_text}、skip")
            luck_spent = option["cost"]
            if char.luck < luck_spent:
                state.pending_luck_decisions[user_id] = pending
                save_state(state)
                return _CheckResolution(reply_text=f"目前 Luck 只有 {char.luck} 點，不足以花費 {luck_spent} 點。")
            char.luck -= luck_spent
            tier = choice

        required_tier = pending.get("difficulty", "regular")
        success = dice.TIER_RANK[tier] >= dice.TIER_RANK[required_tier]
        r = dice.SkillCheckResult(
            skill_value=pending["value"], roll=pending["roll"], bonus_dice=pending["bonus_dice"],
            penalty_dice=pending["penalty_dice"], tier=tier, success=success, required_tier=required_tier,
        )
        # _build_check_narration can itself mutate char (e.g. appending "昏迷"/
        # "倒地" to status_tags for a failed major_wound_trigger check — see
        # its docstring), so save_state has to happen AFTER this call, not
        # before it: keeper.run_turn's own state commit (_commit_turn_result)
        # does a *fresh* load_state rather than persisting this same `state`
        # object, so any mutation made after an earlier save here would
        # otherwise be silently discarded.
        roll_line, keeper_message = _build_check_narration(
            char, pending["skill_name"], pending["display_label"], pending["value"], r,
            pending["bonus_dice"], pending["penalty_dice"],
            luck_spent=luck_spent, original_tier=pending["original_tier"],
            attacker_tier=pending.get("attacker_tier"),
            major_wound_trigger=bool(pending.get("major_wound_trigger", False)),
        )
        save_state(state)
        outcome_text = _tier_zh_for_tier(tier, required_tier)
        if luck_spent:
            result_line = f"花費 {luck_spent} 點幸運：{pending['roll']} → {outcome_text}"
        else:
            result_line = f"維持原結果：{pending['roll']} → {outcome_text}"
        roll_feedback_text, keeper_header = _build_split_check_feedback(
            char.name,
            pending["display_label"] or pending["skill_name"],
            str(pending["value"]),
            pending["roll"],
            outcome_text,
            result_line=result_line,
        )
        return _CheckResolution(
            state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
            roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True
        )


async def handle_text_message(
    conversation_id: str,
    user_id: str,
    get_display_name: GetDisplayName,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
    format_mention: FormatMention = lambda owner_id: owner_id,
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
            await _handle_coc_command(
                conversation_id, user_id, reply, send_dm, send_image, send_dm_image, text, format_mention
            )
        return

    # KP Assistant is optional. Only when this conversation currently has a KP
    # Assistant do ordinary Keeper turns use the priority gate; otherwise we
    # intentionally bypass it and preserve the original conversation-lock path.
    scheduling_state = load_state(conversation_id)
    if not scheduling_state.kp_assistant_user_id:
        async with locks.get_conversation_lock(conversation_id):
            await _handle_ordinary_text_message_locked(
                conversation_id, user_id, get_display_name, reply, send_dm, send_image, send_dm_image, text
            )
        return

    is_kp_priority = scheduling_state.kp_assistant_user_id == user_id
    async with locks.get_keeper_priority_gate(conversation_id, is_kp=is_kp_priority):
        async with locks.get_conversation_lock(conversation_id):
            await _handle_ordinary_text_message_locked(
                conversation_id, user_id, get_display_name, reply, send_dm, send_image, send_dm_image, text
            )


async def _handle_ordinary_text_message_locked(
    conversation_id: str,
    user_id: str,
    get_display_name: GetDisplayName,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
) -> None:
    """Handle an ordinary non-command text message.

    The caller must already hold get_conversation_lock(conversation_id). This
    function always reloads state itself; any pre-gate scheduling snapshot is
    only a priority hint and never authoritative game state.
    """
    state = load_state(conversation_id)
    if not state.active or not state.game_started:
        # Ordinary text only becomes in-character play after a scenario is
        # loaded AND /coc start has actually begun the game. PDF upload sets
        # active=True during GM setup; /coc end sets active=False.
        return

    is_kp_assistant = state.kp_assistant_user_id == user_id
    if is_kp_assistant:
        display_name = await get_display_name()
        speaker_role = "kp_assistant"
        resolved_location = None
    elif user_id not in state.characters:
        display_name = await get_display_name()
        await reply(f"{display_name}，你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
        return
    else:
        display_name = state.characters[user_id].name
        speaker_role = "player"
        resolved_location = await asyncio.to_thread(_resolve_map_action_transaction, conversation_id, user_id, text)

    async with locks.get_keeper_turn_lock(conversation_id):
        reply_text, private_messages, image_requests = await asyncio.to_thread(
            keeper.run_turn, state, user_id, display_name, text, resolved_location, speaker_role
        )
        await _run_post_turn_maintenance_after_output(
            conversation_id,
            reply,
            reply_text,
            send_dm,
            send_image,
            send_dm_image,
            private_messages,
            image_requests,
            run_maintenance=not is_kp_assistant,
        )


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


def _map_position_snapshot(state: GroupState, user_id: str) -> tuple[str, str, str]:
    return (
        state.current_map_page.get(user_id, ""),
        state.current_room_id.get(user_id, ""),
        state.party_facing.get(user_id, "N"),
    )


def _save_if_map_position_changed(state: GroupState, user_id: str, before: tuple[str, str, str]) -> None:
    if _map_position_snapshot(state, user_id) != before:
        save_state(state)


def _resolve_map_action_transaction(conversation_id: str, user_id: str, text: str) -> dict | None:
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        before = _map_position_snapshot(state, user_id)
        result = _resolve_map_action_core(state, user_id, text, allow_rag=False)
        if not result.needs_rag:
            _save_if_map_position_changed(state, user_id, before)
            return result.context

        rag_page = state.current_map_page.get(user_id, "")
        rag_map = state.scene_maps.get(rag_page) if rag_page else None
        rag_text = state.scenario_text

    rag_room_id = None
    if rag_map and rag_text:
        rag_room = _find_room_via_rag(conversation_id, rag_text, rag_map, text)
        rag_room_id = rag_room.get("id") if rag_room else None

    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        before = _map_position_snapshot(state, user_id)
        result = _resolve_map_action_core(state, user_id, text, allow_rag=False)
        if result.needs_rag and rag_room_id and state.current_map_page.get(user_id, "") == rag_page:
            active_map = state.scene_maps.get(rag_page)
            rag_room = scene_map_engine.get_room(active_map, rag_room_id) if active_map else None
            if rag_room is not None:
                result = _resolve_map_action_core(state, user_id, text, allow_rag=False, rag_target_room=rag_room)
        _save_if_map_position_changed(state, user_id, before)
        return result.context


def _resolve_map_action_core(
    state: GroupState,
    user_id: str,
    text: str,
    *,
    allow_rag: bool = True,
    rag_target_room: dict | None = None,
) -> _MapActionResolution:
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
    needs_rag = False

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
                state.scene_maps, current_page, current_room, facing, movement["relative_direction"], movement["order"],
            )
            if result["ok"]:
                if "map_key" in result:  # crossed into a different map — see scene_map.py's module docstring
                    state.current_map_page[user_id] = result["map_key"]
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
            target_room = rag_target_room or scene_map_engine.find_room_by_text(active_map, text)
            if target_room is None and SCENARIO_RAG_ENABLED and state.scenario_text:
                if allow_rag:
                    target_room = _find_room_via_rag(state.group_id, state.scenario_text, active_map, text)
                else:
                    needs_rag = True
            if target_room is not None:
                state.current_room_id[user_id] = target_room["id"]
                state.party_facing[user_id] = "N"  # arbitrary jump, no direction to carry forward
                resolved_room = target_room

    if resolved_room is None:
        return _MapActionResolution(needs_rag=needs_rag)
    char = state.characters.get(user_id)
    return _MapActionResolution(
        context={
            "character_name": char.name if char else "",
            "room_name": resolved_room.get("name", ""),
            "room_description": resolved_room.get("description", ""),
        },
        needs_rag=needs_rag,
    )


def _find_room_via_rag(group_id: str, scenario_text: str, scene_map: dict, text: str) -> dict | None:
    """Scenario RAG fallback for room-name resolution (see
    _resolve_map_action above) — RAG has no concept of room IDs, so the
    connection is made by searching the scenario text for the player's raw
    phrase and checking whether any of the current map's room names appear
    in whichever page(s) came back as relevant. This is genuinely a second
    real API call on top of the Keeper's own turn when embeddings are
    configured (see scenario_rag.py), so it's only reached after the free
    local name match in _resolve_map_action has already failed."""
    index = scenario_rag.get_index(group_id, scenario_text)
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


def _blocked_by_kp_assistant(state: GroupState, user_id: str) -> str | None:
    if state.kp_assistant_user_id != user_id:
        return None
    return "你目前是這局的 KP 助手，不能同時建立或使用調查員角色。請先使用「/coc kp quit」解除 KP 助手身分。"


def _set_character_away_state(conversation_id: str, user_id: str, away: bool) -> _AwayStateResult:
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        char = state.characters.get(user_id)
        if not char:
            return _AwayStateResult(error_text="你還沒有角色。")
        char.away = away
        save_state(state)
        return _AwayStateResult(character_name=char.name)


def _pregen_full_sheet_text(pregen: dict, index: int) -> str:
    """Read-only preview of a scenario pregen for a player deciding whether to
    claim it. Skills are capped at the top 12 by value, same as Character.
    sheet_text() — a scenario's own pregen sheet can list 50+ skills (every
    BASE_SKILLS entry plus whatever it customized), and dumping the whole
    list here just to compare candidates is exactly the kind of "全倒" wall
    of mostly-base-value numbers that makes a preview harder to read, not
    easier. Deliberately excludes secret_goal: that's only ever revealed
    privately after a claim (see /coc pc and /coc usepregen), never in a
    pre-selection preview anyone can run."""
    lines = [
        f"【預製角色 #{index}】{pregen.get('name') or '未命名'}　職業：{pregen.get('occupation', '未知職業')}",
    ]
    # LUCK deliberately excluded from `attrs` below — /coc usepregen always
    # rolls a fresh LUCK for whoever claims this slot (see
    # pregen_extractor.pregen_to_character), so showing the PDF's printed
    # value here as if it were a fixed stat would mislead a player comparing
    # candidates into thinking that's what they'll actually get.
    attrs = ["str_", "con", "siz", "dex", "app", "int_", "pow_", "edu"]
    labels = {"str_": "STR", "con": "CON", "siz": "SIZ", "dex": "DEX", "app": "APP", "int_": "INT", "pow_": "POW", "edu": "EDU"}
    attr_line = " ".join(f"{labels[a]} {pregen[a]}" for a in attrs if isinstance(pregen.get(a), (int, float)))
    if isinstance(pregen.get("luck"), (int, float)):
        attr_line += f"{' ' if attr_line else ''}（卡面 LUCK {pregen['luck']}，取用時將重新骰定）"
    elif attr_line:
        attr_line += "（LUCK 將於取用時骰定）"
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
        top_skills = sorted(skills.items(), key=lambda kv: -kv[1] if isinstance(kv[1], (int, float)) else 0)[:12]
        lines.append("主要技能：" + "、".join(f"{k} {v}%" for k, v in top_skills))
    if pregen.get("notes"):
        lines.append(f"背景：{pregen['notes']}")
    if pregen.get("key_connection"):
        lines.append(f"★ 關鍵背景連結：{pregen['key_connection']}")
    if pregen.get("claimed_by"):
        lines.append("（此角色已被選走）")
    return "\n".join(lines)


def _heal_character(char: Character) -> list[str]:
    """Run once per bound character right before /coc start actually opens
    the game (see docs/character_and_dictionary_system_spec.md's Module 7) —
    Character creation today (generate_investigator / pregen_to_character)
    always produces complete derived stats and the full BASE_SKILLS set, so
    a character built through either of those paths should never actually
    trip any of these; this exists for characters built before this
    project's own bug fixes shipped (see docs/changelog.md's Module 2
    entries — pregen_to_character used to only default 閃避/母語, and
    weapons/carried_items used to not get populated at all), which are
    exactly the kind of "known-good fix exists, just never applied
    retroactively" gap this can safely repair on the spot. Mutates `char`
    in place; returns human-readable notes about what got healed or, for
    what genuinely can't be healed, what needs the GM's own attention.
    Caller is responsible for saving state if this list is non-empty."""
    notes: list[str] = []

    missing_skills = [s for s in BASE_SKILLS if s not in char.skills]
    if missing_skills:
        for skill in missing_skills:
            char.skills[skill] = BASE_SKILLS[skill]
        notes.append(f"補上 {len(missing_skills)} 項缺少的官方技能預設值")

    # HP/MP/SAN are pure functions of already-present base attributes (CON+SIZ,
    # POW, POW again) — unlike the 9 base attributes themselves, these are
    # always safe to recompute from data that's still there, never a guess.
    # <=0 can't legitimately happen from real COC7e attribute ranges (see
    # generate_investigator's roll ranges) — only from data built before a
    # fix, or direct DB tampering.
    if char.hp_max <= 0:
        char.hp_max = max(1, (char.con + char.siz) // 10)
        char.hp = min(char.hp, char.hp_max) if char.hp > 0 else char.hp_max
        notes.append("生命值上限異常，已依現有 CON/SIZ 重新算過")
    if char.mp_max <= 0:
        char.mp_max = max(1, char.pow_ // 5)
        char.mp = min(char.mp, char.mp_max) if char.mp > 0 else char.mp_max
        notes.append("魔法值上限異常，已依現有 POW 重新算過")
    if char.san_max <= 0:
        char.san_max = min(char.pow_, 99) or 99
        char.san = min(char.san, char.san_max) if char.san > 0 else char.san_max
        notes.append("理智值上限異常，已依現有 POW 重新算過")

    # The 9 base attributes (STR/CON/SIZ/DEX/APP/INT/POW/EDU/LUCK) have no
    # formula to reconstruct them from — unlike HP/MP/SAN above, there's
    # nothing to safely recompute here. All nine landing on exactly 50 (the
    # extraction pipeline's own fallback default — see pregen_extractor.py's
    # _int_or) is a strong enough coincidence that real attribute rolls or a
    # real scenario's own pregen numbers essentially never produce it, so
    # this is flagged for the GM to manually verify, never silently guessed
    # at or auto-corrected.
    all_nine = (char.str_, char.con, char.siz, char.dex, char.app, char.int_, char.pow_, char.edu, char.luck)
    if len(set(all_nine)) == 1 and all_nine[0] == 50:
        notes.append("⚠️ 9 大屬性剛好全部是 50，可能是舊資料遺失、不是真實數值，建議人工核對角色卡")

    return notes


def _build_readiness_roster(
    state: GroupState, healed_notes: dict[str, list[str]], format_mention: FormatMention = lambda owner_id: owner_id
) -> str:
    """The "全團調查員集結就緒名冊" /coc start announces before the opening
    narration — see docs/character_and_dictionary_system_spec.md's Module 7's
    own "範例二" for the format this follows (HP/SAN/weapons/items per
    character, not just name/occupation — a GM glancing at this should be
    able to tell at a glance whether everyone's actually equipped, not just
    who's playing who). `healed_notes` is owner_id -> whatever
    _heal_character found for them (empty list if nothing needed fixing).
    `format_mention` renders each owner_id for display (see FormatMention) —
    defaults to the bare id, same as before this parameter existed, for any
    caller that doesn't have a better option (LINE)."""
    lines = ["📋 全團調查員集結就緒名冊", ""]
    for owner_id, char in state.characters.items():
        weapon_parts = []
        for weapon_name, ammo_info in char.weapons.items():
            if ammo_info.get("ammo_max"):
                weapon_parts.append(f"{weapon_name} ({ammo_info['ammo']}/{ammo_info['ammo_max']})")
            else:
                weapon_parts.append(weapon_name)  # untracked ammo — see app/pregen_extractor.py
        stats = f"HP {char.hp}/{char.hp_max}, SAN {char.san}/{char.san_max}"
        if weapon_parts:
            stats += "，彈藥：" + "、".join(weapon_parts)
        if char.carried_items:
            stats += "，物品：" + "、".join(char.carried_items)
        lines.append(f"・【{char.name}】職業：{char.occupation}（玩家：{format_mention(owner_id)}）：{stats}")
        for note in healed_notes.get(owner_id, []):
            lines.append(f"　　└ {note}")
    unclaimed = sum(1 for p in state.pregens if not p.get("claimed_by"))
    if unclaimed:
        lines.append("")
        lines.append(f"（尚有 {unclaimed} 位預製角色未被認領，本次以此陣容出戰）")
    return "\n".join(lines)



async def _handle_coc_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
    format_mention: FormatMention = lambda owner_id: owner_id,
) -> None:
    parts = text.split()
    sub = parts[1] if len(parts) > 1 else "help"

    if sub == "newgame":
        save_state(GroupState(group_id=conversation_id))
        await reply("已重置這個群組的遊戲狀態。請上傳劇本 PDF 檔案開始新的冒險。")
        return

    if sub == "pdf":
        # Text-command equivalent of Discord's PdfUploadChoiceButton (see
        # _resolve_pdf_upload_choice_locked) — LINE has no button/interaction
        # mechanism at all, so without this a pending_pdf_upload on LINE (a
        # scenario already running, a second PDF lands) had no way to ever
        # get resolved: handle_pdf_upload would stash it and just sit there
        # forever, scenario_text never updating, every further PDF upload
        # refused by the "resolve the pending one first" guard. Works on
        # Discord too, as a text-based fallback alongside the buttons.
        choice_word = parts[2] if len(parts) > 2 else ""
        choice = {"new": "new", "全新": "new", "全新劇本": "new", "fix": "fix", "修正": "fix", "修正目前劇本": "fix"}.get(choice_word)
        if choice is None:
            await reply("用法：「/coc pdf new」開始全新劇本，或「/coc pdf fix」修正/補完目前這份劇本。")
            return
        await reply(_resolve_pdf_upload_choice_locked(conversation_id, choice))
        return

    if sub == "kp":
        action = parts[2] if len(parts) > 2 else None
        state = load_state(conversation_id)

        if action == "quit":
            if state.kp_assistant_user_id != user_id:
                await reply("你目前不是這局的 KP 助手。")
                return
            state.kp_assistant_user_id = ""
            state.kp_ooc_log = []
            save_state(state)
            await reply("已解除 KP 助手身分，你現在回到未綁定角色的狀態。")
            return

        if action is not None:
            await reply("用法：/coc kp 或 /coc kp quit")
            return

        if state.kp_assistant_user_id == user_id:
            await reply("你已經是這局的 KP 助手。")
            return
        if state.kp_assistant_user_id:
            await reply("這局已經有一位 KP 助手，不能同時登記第二位。")
            return
        if user_id in state.characters:
            await reply("KP 助手與調查員角色互斥；你已經有調查員角色，不能登記為 KP 助手。")
            return
        if user_id in state.creation_sessions:
            await reply("KP 助手與建角流程互斥；你正在進行互動式建角，請先輸入「/coc create cancel」取消後再登記 KP 助手。")
            return

        state.kp_ooc_log = []
        state.kp_assistant_user_id = user_id
        save_state(state)
        await reply("已登記你為這局的 KP 助手。")
        return

    if sub == "pc":
        state = load_state(conversation_id)
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return
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
        state.kp_assistant_user_id = ""
        state.kp_ooc_log = []
        save_state(state)
        await reply("遊戲已結束，遊戲紀錄與角色仍會保留；KP 助手身分也已解除。要開新的一局請用 /coc newgame。")
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

    if sub == "setpersona":
        state = load_state(conversation_id)
        if len(parts) < 3:
            current = state.keeper_persona or f"（目前使用預設風格）\n{keeper.DEFAULT_PERSONA}"
            await reply(
                "用法：/coc setpersona <描述守密人語氣風格的文字> → 設定這個群組專屬的守密人語氣\n"
                "/coc setpersona reset → 重設回預設的冷酷旁觀者風格\n\n"
                f"目前設定：\n{current}"
            )
            return
        if parts[2] == "reset" and len(parts) == 3:
            state.keeper_persona = ""
            save_state(state)
            await reply("已重設回預設的冷酷旁觀者語氣風格。")
            return
        persona_text = " ".join(parts[2:])
        state.keeper_persona = persona_text
        save_state(state)
        await reply(f"已設定這個群組的守密人語氣風格：\n{persona_text}\n\n（下一則訊息開始生效；重設回預設風格用 /coc setpersona reset）")
        return

    if sub == "era":
        state = load_state(conversation_id)
        if len(parts) < 3:
            await reply(
                "用法：/coc era 1920 → 設定 1920 年代經典設定\n"
                "/coc era modern → 設定現代／當代設定\n\n"
                f"目前設定：{'1920 年代' if state.era == '1920s' else '現代／當代'}\n"
                "（影響角色卡上傳時，武器只寫泛稱、沒寫具體型號的情況下，自動補上的預設彈藥容量）"
            )
            return
        choice = parts[2].strip().lower()
        era_map = {"1920": "1920s", "1920s": "1920s", "modern": "modern"}
        if choice not in era_map:
            await reply("年代設定只接受「1920」或「modern」。")
            return
        state.era = era_map[choice]
        save_state(state)
        await reply(f"已設定這個群組的年代為：{'1920 年代' if state.era == '1920s' else '現代／當代'}。")
        return

    if sub == "create":
        action = parts[2] if len(parts) > 2 else None
        state = load_state(conversation_id)
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return

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
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return
        session = state.creation_sessions.get(user_id)
        if not session:
            await reply("目前沒有進行中的建角流程，先輸入「/coc create 角色名 [職業]」開始。")
            return
        try:
            points = int(points_str)
        except ValueError:
            await reply("點數必須是整數。")
            return
        allocation_result = creation.allocate(session, pool, skill, points)
        if not allocation_result["ok"]:
            await reply(allocation_result["error"])
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
        blocked = _blocked_by_kp_assistant(state, user_id)
        if blocked:
            await reply(blocked)
            return
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
        char = pregen_extractor.pregen_to_character(pregen, user_id, era=state.era)
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

    if sub == "start":
        state = load_state(conversation_id)
        if not state.active or not state.scenario_text:
            await reply("目前還沒有載入劇本，請先上傳 PDF 劇本。")
            return
        if not state.characters:
            await reply("目前這個群組還沒有任何調查員，請先用「/coc pc 角色名 職業」或「/coc usepregen 編號」建立角色。")
            return
        if state.game_started:
            await reply("這局遊戲已經開始過了，不會重複產生開場白。想重新來一次的話，請用「/coc newgame」開新的一局。")
            return

        # Readiness gate (see docs/character_and_dictionary_system_spec.md's
        # Module 7): heal whatever _heal_character can safely fix on every
        # bound character, then announce the roster — before the opening
        # narration, as its own message — so the GM sees exactly who's
        # playing what and what (if anything) got quietly repaired, rather
        # than that only surfacing later as a confusing mid-game symptom.
        # Guarded by get_state_lock like the two save_state calls further
        # below in this same subcommand — /coc check's self-initiated check
        # path only gates on state.active (not game_started), so a Keeper
        # turn's background maintenance task can still be in flight here even
        # during the lobby phase; an unguarded load-mutate-save would risk a
        # lost update against that task's own state_lock-guarded save.
        with locks.get_state_lock(conversation_id):
            state = load_state(conversation_id)
            healed_notes: dict[str, list[str]] = {}
            for owner_id, char in state.characters.items():
                notes = _heal_character(char)
                if notes:
                    healed_notes[owner_id] = notes
            if healed_notes:
                save_state(state)
        await reply(_build_readiness_roster(state, healed_notes, format_mention))

        # Prefer the scenario's own read-aloud opening text (see
        # app/scenario_intro.py) over having the Keeper improvise one — many
        # published scenarios already wrote exactly this, tuned for tone and
        # hook by the scenario's own author.
        extracted = await asyncio.to_thread(scenario_intro.extract_opening_narration, state.scenario_text)

        if extracted["found"]:
            opening_text = extracted["text"]
            with locks.get_state_lock(conversation_id):
                state = load_state(conversation_id)  # reload: the extraction call may have taken a while
                if state.game_started:
                    return  # someone else already ran /coc start while this one was in flight
                # Every other code path that appends to state.log does so in a
                # user/assistant pair (see keeper.py's _commit_turn_result) —
                # this has to keep that invariant too, not just append a lone
                # assistant entry. Anthropic's Messages API requires the
                # *first* message in a conversation to have role "user"; a
                # log that starts with (or only contains) an "assistant"
                # entry makes every subsequent turn raise on the next
                # run_conversation call, and since that failure happens
                # before _commit_turn_result ever runs, state.log never
                # advances past it — the whole game is stuck until /coc newgame.
                state.log.append({"role": "user", "content": "守密人：（遊戲開始，請朗讀開場白）"})
                state.log.append({"role": "assistant", "content": opening_text})
                state.game_started = True
                # Some published scenarios' opening text itself demands an
                # immediate check ("everyone roll a Spot Hidden") rather than
                # that only coming up once play is under way — see
                # app/scenario_intro.py's opening_check. Registered the same
                # way app/keeper.py's skill_check/sanity_check tools do
                # (state.pending_checks, one entry per bound character), so
                # the existing pending_checks diff-and-post machinery in
                # app/discord_bot.py posts real buttons for it automatically
                # — no separate button-posting path needed here.
                opening_check = extracted.get("opening_check")
                if opening_check:
                    for owner_id, char in state.characters.items():
                        if opening_check["type"] == "skill":
                            value = keeper.resolve_skill_value(char, opening_check["skill"])
                            state.pending_checks[owner_id] = {
                                "type": "skill", "skill": opening_check["skill"], "skill_value": value,
                                "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular", "pushed": False,
                            }
                        else:  # "sanity"
                            state.pending_checks[owner_id] = {
                                "type": "sanity",
                                "loss_success": opening_check.get("loss_success", "0"),
                                "loss_failure": opening_check.get("loss_failure", "1d4"),
                            }
                save_state(state)
            await reply(opening_text)
            if opening_check and opening_check.get("reason"):
                await reply(f"👉 {opening_check['reason']}——請各自用「/coc check」擲骰。")
            return

        # No usable read-aloud text in the scenario — fall back to a normal
        # Keeper turn (same run_turn/_commit_turn_result path as any other
        # message) with a meta/out-of-character instruction instead of a
        # player's line, so the Keeper improvises the scene-setting itself.
        keeper_message = (
            "（守密人，遊戲即將開始，劇本沒有寫現成的開場白，需要你自己撰寫一段。這份劇本沒有"
            "明確的「序幕」或「開場」段落可以直接查到，不代表劇本沒有背景資訊——如果目前是檢索模式，"
            "請呼叫 search_scenario 查詢劇本的背景設定、調查員的委託／緣由、故事開始的地點等關鍵字"
            "（例如劇本標題、背景、委託人、開場地點），根據查到的背景資訊撰寫開場白，不要因為查不到"
            "「開場」兩個字面就直接放棄。撰寫一段開場白，把調查員們帶入故事的起點——描述他們此刻"
            "身處的場景、氛圍，以及是什麼把他們捲進這個劇本裡，控制在三百字以內，用第二人稱「你」"
            "對調查員說話。這是遊戲的第一段敘述，還沒有任何人採取行動，不要假設玩家已經做了什麼、"
            "也不要在這段話裡問問題或要求玩家回覆什麼——單純把場景鋪陳出來即可。）"
        )
        async with locks.get_keeper_turn_lock(conversation_id):
            keeper_reply, private_messages, image_requests = await asyncio.to_thread(
                keeper.run_turn, state, user_id, "守密人", keeper_message, None
            )
        with locks.get_state_lock(conversation_id):
            state = load_state(conversation_id)
            state.game_started = True
            save_state(state)
        await _run_post_turn_maintenance_after_output(
            conversation_id, reply, keeper_reply, send_dm, send_image, send_dm_image, private_messages, image_requests
        )
        return

    if sub == "index":
        state = load_state(conversation_id)
        if not state.scenario_text:
            await reply("目前還沒有載入劇本，上傳 PDF 之後才能抽取 NPC／怪物與地點索引。")
            return
        # A single `reply` here, not reply-then-push: unlike handle_pdf_upload
        # (which gets separate reply/push callbacks specifically because a
        # LINE reply token is single-use and only lasts 60s), _handle_coc_command
        # only has one `reply` callback to work with — same constraint the
        # "pregens" subcommand above lives with, so this follows the same
        # single-reply-at-the-end shape rather than sending a "please wait"
        # message first.
        extracted = await asyncio.to_thread(scenario_index.extract_scenario_index, state.scenario_text)
        state.scenario_npc_index = extracted["npcs"]
        state.scenario_location_index = extracted["locations"]
        save_state(state)
        if not extracted["npcs"] and not extracted["locations"]:
            await reply("沒有從劇本裡抽出任何有明確數值的 NPC／怪物或地點條目。")
            return
        lines = [f"已重新建立劇本索引：{len(extracted['npcs'])} 個 NPC／怪物、{len(extracted['locations'])} 個地點。"]
        for n in extracted["npcs"]:
            hp = n.get("hp")
            hp_note = f"HP {hp}" if isinstance(hp, (int, float)) else "（無 HP 數值）"
            lines.append(f"・{n.get('name') or '未命名'}：{hp_note}")
        await reply("\n".join(lines) + "\n\n之後守密人回覆時會直接參考這份索引，同一隻怪物/NPC 不會再前後數值不一致。這份索引現在上傳劇本 PDF 時就會自動建立，這個指令是手動重建（例如覺得抽取結果不準、或劇本內容之後有更新時再用）。")
        return

    if sub == "away":
        away_result = await asyncio.to_thread(_set_character_away_state, conversation_id, user_id, True)
        if away_result.error_text:
            await reply(away_result.error_text)
            return
        await reply(f"{away_result.character_name} 已標記為暫離，戰鬥中會自動跳過他的回合，直到輸入「/coc back」回來。")
        return

    if sub == "back":
        back_result = await asyncio.to_thread(_set_character_away_state, conversation_id, user_id, False)
        if back_result.error_text:
            await reply(back_result.error_text)
            return
        await reply(f"{back_result.character_name} 回來了，恢復正常參與。")
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
        # current_page is a raw PDF page number for a vision-extracted map, or a
        # "custom_<filename>" key for a hand-authored YAML upload — showing the
        # literal key (not just "第 X 頁", which reads oddly for the custom_ case
        # and is also what a map author needs to write a cross-map exit
        # "to": "<this key>:<room_id>" pointing at this map from another one).
        location_note = f"第 {current_page} 頁的地圖" if current_page.isdigit() else f"地圖「{current_page}」"
        await reply(f"目前在「{room.get('name', '')}」（{location_note}）{desc}\n出口：{exits_text}")
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
        turn_result = combat.advance_turn(state)
        save_state(state)
        if not turn_result["ok"]:
            await reply(turn_result["error"])
            return
        await reply(
            f"第 {turn_result['round']} 輪，輪到「{turn_result['current_turn']}」了"
            f"（HP {turn_result['hp']}/{turn_result['hp_max']}）。"
        )
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
        damage_result = combat.damage_combatant(state, name, delta)
        save_state(state)
        if not damage_result["ok"]:
            await reply(damage_result["error"])
            return
        tag = "（已倒下）" if damage_result["defeated"] else ""
        await reply(f"{damage_result['name']} HP 變為 {damage_result['hp']}/{damage_result['hp_max']}{tag}")
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
