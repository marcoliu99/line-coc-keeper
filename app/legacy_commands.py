"""Discord command handling and message routing.

The Discord adapter supplies plain strings (conversation_id, user_id, text) and
callbacks for sending text, private messages and images:
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
once for its full duration (including the awaitable Keeper LLM call); the
private helpers they call never lock again themselves, since asyncio.Lock
isn't reentrant.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from app import (
    config,
    dice,
    intent_parser,
    keeper,
    locks,
    luck,
    observability,
    pdf_loader,
    pregen_extractor,
    scenario_compare,
    scenario_index,
    scenario_library,
    scenario_rag,
)
from app import scene_map as scene_map_engine
from app.check_identity import (
    effective_check_id,
    effective_decision_id,
    new_check_id,
    new_decision_id,
)
from app.config import SCENARIO_RAG_ENABLED
from app.models import (
    BASE_SKILLS,
    OCCUPATIONS,
    Character,
    GroupState,
)
from app.repositories.group_state import (
    clear_page_images,
    load_page_image,
    load_state,
    save_page_image,
    save_state,
)

_logger = logging.getLogger(__name__)

Reply = Callable[[str], Awaitable[None]]
GetDisplayName = Callable[[], Awaitable[str]]
# owner_id -> a Discord mention rendered by the adapter.
FormatMention = Callable[[str], str]
SendDM = Callable[[str, str], Awaitable[None]]  # (owner_id, text) -> None
# (png_bytes, conversation_id, page_number) -> None, posts publicly. The
# conversation and page metadata are retained for state-aware image sends.
SendImage = Callable[[bytes, str, int], Awaitable[None]]
SendDMImage = Callable[[str, bytes, str, int], Awaitable[None]]  # (owner_id, png_bytes, conversation_id, page_number)


__all__ = [
    "FormatMention",
    "GetDisplayName",
    "Reply",
    "SendDM",
    "SendDMImage",
    "SendImage",
    "handle_check_command",
    "handle_luck_decision",
    "handle_map_upload",
    "handle_pdf_upload",
    "handle_pregen_luck_roll",
    "handle_role_sheet_upload",
    "handle_roll_command",
    "handle_scenario_compare_upload",
    "handle_unsupported_message",
    "resolve_pdf_upload_choice",
]


def _is_kp_or_keeper(state: GroupState, user_id: str, is_keeper: bool = False) -> bool:
    """Return whether a user may perform group-level scenario administration."""
    return (
        not config.SCENARIO_LIFECYCLE_KP_ONLY
        or is_keeper
        or state.kp_assistant_user_id == user_id
    )

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
    old_timeline_id = state.timeline_id or f"legacy-{state.group_id}"
    new_timeline_id = f"timeline-{uuid4().hex[:8]}"
    observability.event(
        "provider.chain.reset",
        reason="scenario_upload",
        old_timeline_id=old_timeline_id,
        requested_timeline_id=new_timeline_id,
        provider="openai",
    )
    state.openai_previous_response_id = ""
    state.openai_previous_response_timeline_id = ""
    state.timeline_id = new_timeline_id
    state.resolved_check_events.clear()
    # Pending player decisions and deterministic check results are scoped to
    # the old scenario.  Invalidate them together with the timeline so stale
    # typed commands or Discord buttons cannot mutate the new scenario.
    state.pending_checks.clear()
    state.pending_luck_decisions.clear()
    state.deterministic_check_results.clear()
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
    # New scenario: replace the scenario-owned candidate pool. Live
    # investigators remain in state.characters, but unclaimed candidates from
    # the previous PDF must not leak into /coc pregens.
    state.pregens = list(pregens)


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


def _install_library_context(
    state: GroupState,
    scenario_id: str,
    context: dict,
    *,
    preserve_maps: bool = False,
    preserve_pregens: bool = False,
) -> None:
    """Copy the selected chapter window from an immutable library entry into state."""
    state.scenario_library_id = scenario_id
    state.scenario_title = context["manifest"]["title"]
    state.scenario_text = context["text"]
    state.active_chapter_id = context["active_chapter_id"]
    state.context_chapter_ids = context["context_chapter_ids"]
    state.scenario_npc_index = context["indexes"].get("npcs", [])
    state.scenario_location_index = context["indexes"].get("locations", [])
    if not preserve_pregens:
        state.pregens = list(context.get("pregens", []))
    if not preserve_maps:
        state.scene_maps = context["scene_maps"]


def _install_context_images(conversation_id: str, scenario_id: str, context: dict) -> None:
    clear_page_images(conversation_id)
    scenario_library.copy_context_images(
        scenario_id, context["page_numbers"],
        lambda page, image: save_page_image(conversation_id, page, image),
    )


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
    skip_similarity: bool = False,
    reparse_candidate_id: str | None = None,
) -> bool:
    """`reply` acknowledges the upload and `push` delivers the extracted result
    after the potentially long vision/OCR pass. Discord can pass the same
    callback for both; the boolean result indicates whether a new scenario was
    accepted or is waiting for a user choice.

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
        return False

    # Checked before any of the expensive extraction work below (and before
    # clear_page_images, which unconditionally wipes the current scenario's
    # page images) — a second PDF landing while an earlier pending_pdf_upload
    # choice is still unresolved would otherwise silently overwrite it, and
    # whichever button the GM clicks afterward (still labelled for the FIRST
    # upload — buttons carry no upload-specific id) would end up applying the
    # SECOND upload's content instead, which is especially bad for "全新劇本"
    # (wipes map position, resets the LLM conversation thread).
    existing_state = load_state(conversation_id)
    if existing_state.pending_pregen_luck:
        await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本 PDF。")
        return False
    if existing_state.pending_pdf_upload is not None:
        await reply(
            f"上一次上傳的《{existing_state.pending_pdf_upload['title']}》還沒選擇「全新劇本」"
            "還是「修正目前劇本」，請先點上一則訊息的按鈕選完，再上傳這份新的 PDF——不然這份新的"
            "會蓋掉還沒處理的那份，之後點到舊按鈕會套用到錯的內容。"
        )
        return False

    if existing_state.pending_scenario_upload is not None and not skip_similarity:
        await reply("已有一份相似 PDF 等待處理，請先用 /coc scenario reparse 或 /coc scenario cancel。")
        return False

    preview = ""
    if not skip_similarity:
        try:
            preview = await asyncio.to_thread(pdf_loader.extract_preview, pdf_bytes)
        except ValueError as exc:
            await reply(f"無法讀取 PDF 前幾頁：{exc}")
            return False
        preview_title = pdf_loader.guess_title(preview, file_name=file_name)
        matches = await asyncio.to_thread(scenario_library.find_similar, preview_title, preview)
        if matches:
            key = await asyncio.to_thread(scenario_library.stage_upload, pdf_bytes)
            # Reload under the lock right before saving — extract_preview and
            # find_similar above ran unlocked, so the state this function
            # loaded at the top can already be stale by now (an ordinary
            # turn, roll, or combat update landing in between); saving that
            # stale snapshot back would silently revert whatever changed.
            async with locks.get_conversation_lock(conversation_id):
                state = load_state(conversation_id)
                if state.pending_scenario_upload is not None:
                    scenario_library.discard_staged_upload(key)
                    await reply("已有一份相似 PDF 等待處理，請先用 /coc scenario reparse 或 /coc scenario cancel。")
                    return False
                state.pending_scenario_upload = {"key": key, "file_name": file_name, "title": preview_title, "matches": matches}
                save_state(state)
            labels = "、".join(f"{m['id']}《{m['title']}》（{m['score']:.0%}）" for m in matches[:3])
            await reply(f"偵測到相似劇本：{labels}。若要重新解析請輸入 /coc scenario reparse；放棄請輸入 /coc scenario cancel。")
            return False

    await reply("收到了，正在讀取劇本內容；圖片較多的劇本需要較長時間，請稍候...")

    try:
        text, low_text_pages, truncated, page_images, page_maps = await asyncio.to_thread(
            pdf_loader.extract_text, pdf_bytes
        )
    except ValueError as exc:
        await push(f"讀取 PDF 失敗：{exc}")
        return False

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

    if not preview:
        try:
            preview = await asyncio.to_thread(pdf_loader.extract_preview, pdf_bytes)
        except ValueError:
            preview = text[:12_000]
    scenario_id = await asyncio.to_thread(
        scenario_library.save_scenario, pdf_bytes, title=title, filename=file_name,
        preview=preview, text=text, indexes=extracted_index, pregens=pregens,
        page_maps=page_maps, page_images=page_images, reparse_candidate_id=reparse_candidate_id,
    )
    library_context = await asyncio.to_thread(scenario_library.load_context, scenario_id)
    text = library_context["text"]
    extracted_index = library_context["indexes"]
    pregens = library_context["pregens"]
    page_maps = library_context["scene_maps"]

    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        # Do not expose the replacement PDF's images until the GM has chosen
        # new-versus-correction. The immutable library entry already contains
        # them; the selected two-chapter window is copied only on activation.
        if state.pending_pdf_upload is not None:
            raced = True
        elif state.scenario_text.strip():
            raced = False
            state.pending_pdf_upload = {
                "scenario_id": scenario_id,
                "text": text,
                "title": library_context["manifest"]["title"],
                "low_text_pages": low_text_pages,
                "truncated": truncated,
                "npcs": extracted_index["npcs"],
                "locations": extracted_index["locations"],
                "page_maps": {str(k): v for k, v in page_maps.items()},
                "pregens": pregens,
                "active_chapter_id": library_context["active_chapter_id"],
                "context_chapter_ids": library_context["context_chapter_ids"],
            }
            save_state(state)
            current_title = state.scenario_title
            confirmation_pending = True
        else:
            raced = False
            # A group may upload role_ sheets before its first scenario PDF.
            # Keep those manually supplied candidates long enough to merge
            # them with the newly extracted cast after the library context
            # has installed its own pregen pool.
            earlier_manual_pregens = [p for p in state.pregens if p.get("source") == "manual"]
            _apply_new_scenario(state, text, library_context["manifest"]["title"], extracted_index, page_maps, pregens)
            _install_library_context(state, scenario_id, library_context)
            for manual_pregen in earlier_manual_pregens:
                state.pregens, _ = pregen_extractor.reconcile_pregen_into_pool(state.pregens, manual_pregen)
            _install_context_images(conversation_id, scenario_id, library_context)
            save_state(state)
            confirmation_pending = False
            final_pregen_count = len(state.pregens)
    if raced:
        await push(
            f"這份《{title}》來得比較慢——另一份幾乎同時上傳的 PDF 先卡進待確認狀態了，請先處理完"
            "上一則訊息的選擇，再重新上傳這份。"
        )
        return False

    if confirmation_pending:
        await push(
            f"這個群組目前正在跑《{current_title}》。新上傳的《{title}》"
            "是要開始一個全新的劇本，還是修正/補完目前這份劇本？請點下面的按鈕選擇——"
            "選錯的代價不小（位置可能對到新劇本裡不存在的房間），拿不準的話選「修正目前劇本」比較安全。"
        )
        return True

    await push(_pdf_upload_confirmation_text(
        title, text, low_text_pages, truncated, page_maps, extracted_index, final_pregen_count
    ))
    return True


def _resolve_pdf_upload_choice_locked(conversation_id: str, choice: str) -> str:
    """Resolve a pending upload while the caller holds the conversation lock."""
    state = load_state(conversation_id)
    pending = state.pending_pdf_upload
    if pending is None:
        return "這個上傳選擇已經處理過了，或已經過期失效，請重新上傳 PDF。"
    scenario_id = pending.get("scenario_id")
    if not scenario_id:
        return "這個待處理上傳缺少劇本庫資料，請重新上傳 PDF。"
    try:
        context = scenario_library.load_context(scenario_id, pending.get("active_chapter_id", ""))
    except (FileNotFoundError, ValueError):
        state.pending_pdf_upload = None
        save_state(state)
        return "這個待處理劇本庫項目已不存在，請重新上傳 PDF。"
    extracted_index = context["indexes"]
    if choice == "new":
        _apply_new_scenario(
            state, context["text"], context["manifest"]["title"], extracted_index,
            context["scene_maps"], context["pregens"],
        )
    else:
        _apply_scenario_correction(
            state, context["text"], context["manifest"]["title"], extracted_index, context["pregens"]
        )
    _install_library_context(
        state,
        scenario_id,
        context,
        preserve_maps=(choice != "new"),
        preserve_pregens=(choice != "new"),
    )
    _install_context_images(conversation_id, scenario_id, context)
    state.pending_pdf_upload = None
    save_state(state)
    return _pdf_upload_confirmation_text(
        context["manifest"]["title"], context["text"], pending["low_text_pages"], pending["truncated"],
        context["scene_maps"], extracted_index, len(state.pregens),
    )

async def resolve_pdf_upload_choice(
    conversation_id: str,
    choice: str,
    push: Reply,
    user_id: str = "",
    is_keeper: bool = False,
) -> None:
    """Called by Discord's PdfUploadChoiceButton once the GM picks between the
    two options offered by handle_pdf_upload. `choice` must be "new" or "fix";
    the text command remains available as a manual fallback. The actor is
    checked again while holding the conversation lock so a button cannot
    mutate the scenario from an unauthorized account."""
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if not _is_kp_or_keeper(state, user_id, is_keeper):
            await push("只有目前的 KP Assistant 或 Discord Keeper 可以處理劇本 PDF。")
            return
        text = _resolve_pdf_upload_choice_locked(conversation_id, choice)
        state = load_state(conversation_id)
        scenario_text = state.scenario_text
    scenario_rag.schedule_index_prewarm(conversation_id, scenario_text)
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
    Best-effort: a DM can fail if Discord members disallow server-member DMs, and we
    deliberately don't fall back to posting the content publicly, since that
    would defeat the entire point of it being private."""
    for owner_id, message in private_messages:
        try:
            await send_dm(owner_id, f"🤫（私訊）{message}")
        except Exception:
            _logger.exception("send_dm failed for owner_id=%s in conversation_id=%s", owner_id, conversation_id)

    for image_owner_id, page_number in image_requests:
        png_bytes = load_page_image(conversation_id, page_number)
        if not png_bytes:
            continue  # Keeper referenced a page with no stored image — quietly skip
        try:
            if image_owner_id:
                await send_dm_image(image_owner_id, png_bytes, conversation_id, page_number)
            else:
                await send_image(png_bytes, conversation_id, page_number)
        except Exception:
            _logger.exception(
                "send_dm_image/send_image failed for owner_id=%s in conversation_id=%s", image_owner_id, conversation_id
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
    with observability.detached_context(
        maintenance_id=observability.new_id("maintenance"),
        conversation_id=conversation_id,
    ):
        try:
            metrics: dict[str, object] = {}
            with observability.span(
                "maintenance", trigger="post_turn", metrics=metrics,
                slow_threshold_ms=config.LOG_SLOW_OPERATION_MS,
                slow_event="maintenance.slow",
            ):
                result = await asyncio.to_thread(keeper.run_post_turn_maintenance, conversation_id)
                metrics.update(result or {})
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


# Code review: this used to be its own independently-maintained copy of
# dice.TIER_ZH, and had silently drifted from app/discord_bot.py's copy on
# "regular" ("成功" vs "一般成功"). Now a plain alias to the single source.
_CHECK_TIER_ZH = dice.TIER_ZH

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
    check_id: str = ""
    decision_id: str = ""
    timeline_id: str = ""
    action_context: str = ""
    resolved_event: dict | None = None


_CHECK_EVENT_ATTRIBUTE_NAMES = {"hp": "HP", "san": "SAN", "mp": "MP", "luck": "Luck"}


def _character_attribute_snapshot(char) -> dict[str, int]:
    return {name: int(getattr(char, name)) for name in _CHECK_EVENT_ATTRIBUTE_NAMES}


def _resolved_check_event_seed(
    *, check_id: str, timeline_id: str, owner_id: str, character_id: str, investigator: str,
    skill: str, skill_value: int, roll: int, difficulty: str, outcome: str,
    before: dict[str, int], tracked_roll_fields: tuple[str, ...] = (),
) -> dict:
    return {
        "event_id": check_id or new_check_id(),
        "check_id": check_id,
        "timeline_id": timeline_id,
        "owner_id": owner_id,
        "character_id": character_id,
        "investigator": investigator,
        "skill": skill,
        "skill_value": int(skill_value),
        "roll": int(roll),
        "difficulty": str(difficulty),
        "outcome": outcome,
        "state_before": dict(before),
        "tracked_roll_fields": list(tracked_roll_fields),
    }


def _persist_resolved_check_event(conversation_id: str, event_seed: dict) -> None:
    """Record the check and only attribute changes present in committed state."""
    with locks.get_state_lock(conversation_id):
        latest = load_state(conversation_id)
        current_timeline_id = latest.timeline_id or f"legacy-{conversation_id}"
        if current_timeline_id != event_seed["timeline_id"]:
            observability.event(
                "check.event.stale", level=logging.INFO, reason="timeline_mismatch",
                check_id=event_seed["check_id"] or None,
            )
            return
        if any(
            event.get("event_id") == event_seed["event_id"]
            for event in latest.resolved_check_events
            if isinstance(event, dict)
        ):
            return
        char = latest.get_active_character(event_seed["owner_id"])
        if (
            char is None
            or char.name != event_seed["investigator"]
            or char.character_id != event_seed["character_id"]
        ):
            return
        after = _character_attribute_snapshot(char)
        effects = [
            {
                "field": _CHECK_EVENT_ATTRIBUTE_NAMES[field],
                "before": before,
                "after": after[field],
                "delta": after[field] - before,
            }
            for field, before in event_seed["state_before"].items()
            if before != after[field]
        ]
        event = {
            key: value for key, value in event_seed.items() if key != "state_before"
        }
        event["state_effects"] = effects
        event.pop("tracked_roll_fields", None)
        event["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        latest.resolved_check_events.append(event)
        del latest.resolved_check_events[:-20]
        save_state(latest, reason="resolved_check_event")


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
    outcome = dice.resolve_opposed(defender_tier, attacker_tier, is_counter)
    attacker_zh = _CHECK_TIER_ZH[attacker_tier]
    if outcome == "both_miss":
        return f"對抗檢定：攻擊方「{attacker_zh}」，雙方都沒成功，這次攻擊沒有命中，{defender_name}沒有受傷，也沒有造成傷害。"
    if outcome == "defender_wins":
        if is_counter:
            return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級更高，攻擊被化解，反擊命中，可以對攻擊方造成傷害。"
        return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級更高，成功閃避，沒有受到傷害。"
    if outcome == "tie_defender_wins":
        # Only reachable when is_counter is False (Dodge) — resolve_opposed
        # never returns this for a Fight Back choice, where a tie instead
        # favors the attacker (RAW: a tied Dodge protects the fully
        # defensive side, unlike a tied Fight Back).
        return f"對抗檢定：攻擊方「{attacker_zh}」，{defender_name}的成功等級與攻擊方打平（平手，依規則閃避方獲勝），成功閃避，沒有受到傷害。"
    tie_note = "（平手，依規則攻擊方獲勝）" if outcome == "tie_attacker_wins" else ""
    counter_note = "，反擊沒有生效" if is_counter else ""
    return f"對抗檢定：攻擊方「{attacker_zh}」，攻擊方成功等級較高{tie_note}，攻擊命中，{defender_name}受到傷害{counter_note}。"


def _resolve_ranged_defense_outcome(defender_name: str, dive_success: bool, ranged_attacker: dict[str, int]) -> str:
    """COC7e ranged-attack resolution — deliberately NOT dice.resolve_opposed
    (that function is for melee Dodge/Fight Back only; verified against RAW,
    see docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md §2). A ranged
    attack is never an opposed roll: the defender's only option is diving for
    cover, an independent Dodge check that — if successful — gives the
    attacker's shot one penalty die but does not by itself stop the shot.
    The attacker's own roll alone (<=skill value, no Push allowed on a
    firearm attack) determines whether it connects.

    Rolls the attacker's shot exactly once — callers must call this exactly
    once per resolved defender roll (not once per narration message) and
    reuse the returned text everywhere it's needed, or the attacker would be
    rolled twice with potentially different results for the same turn."""
    penalty = ranged_attacker["penalty_dice"] + (1 if dive_success else 0)
    attacker_result = dice.skill_check(
        ranged_attacker["skill_value"], bonus_dice=ranged_attacker["bonus_dice"], penalty_dice=penalty
    )
    attacker_zh = _CHECK_TIER_ZH[attacker_result.tier]
    dive_text = (
        "撲向掩體成功，這次射擊被迫多承受 1 個懲罰骰" if dive_success
        else "撲向掩體失敗，沒有讓攻擊方受到任何懲罰"
    )
    if attacker_result.success:
        return f"遠程攻擊判定：{defender_name}{dive_text}；攻擊方仍然擲出「{attacker_zh}」，命中了，{defender_name}受到傷害。"
    return f"遠程攻擊判定：{defender_name}{dive_text}；攻擊方擲出「{attacker_zh}」，沒有命中，{defender_name}沒有受到傷害。"


def _build_check_narration(
    char, skill_name: str, display_label: str | None, value: int, r, bonus: int, penalty: int,
    luck_spent: int = 0, original_tier: str | None = None, attacker_tier: str | None = None,
    major_wound_trigger: bool = False, ranged_opposed_text: str | None = None, is_counter: bool = False,
) -> tuple[str, str]:
    """Builds (roll_line, keeper_message) for a resolved skill/choice check —
    shared by the immediate-finalize path and handle_luck_decision (after a
    Luck spend has overridden r.tier). luck_spent > 0 adds a note both humans
    and the Keeper can see that the tier was bought up, not rolled naturally.
    attacker_tier (only set for a melee Dodge/Fight Back choice — see
    keeper.py's offer_check_choice/npc_skill_check) triggers the COC7e
    opposed-roll comparison, named explicitly in both messages.
    ranged_opposed_text (only set for a ranged offer_npc_attack_defense_choice
    — see keeper.py's is_ranged branch) is the ALREADY-RESOLVED narration
    from _resolve_ranged_defense_outcome, computed once by the caller (never
    computed in here) since that function rolls the attacker's shot as a side
    effect and must not be invoked more than once per resolved defender roll.
    attacker_tier and ranged_opposed_text are mutually exclusive — a given
    choice check is either the melee opposed-roll path or the ranged path,
    never both.
    is_counter (only meaningful when attacker_tier is set) must be computed
    by the caller from the original option dict via dice.is_counter_option()
    — this function no longer re-derives it from display_label's free text
    (code review: a future label wording change could silently break a bare
    "反擊" in display_label substring match here).
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
        original_zh = _tier_zh_for_tier(original_tier or "regular", getattr(r, "required_tier", "regular"))
        luck_note = f"（花費 {luck_spent} 點 Luck，將結果從「{original_zh}」提升為「{tier_zh}」）"

    opposed_line = ""
    opposed_message = ""
    if attacker_tier is not None:
        opposed_text = _describe_opposed_outcome(char.name, is_counter, r.tier, attacker_tier)
        opposed_line = f"\n⚔️ {opposed_text}"
        opposed_message = f"（{opposed_text}）"
    elif ranged_opposed_text:
        opposed_line = f"\n⚔️ {ranged_opposed_text}"
        opposed_message = f"（{ranged_opposed_text}）"

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
    check_id: str = "",
    decision_id: str = "",
    timeline_id: str = "",
    action_context: str = "",
    resolved_event: dict | None = None,
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
            # The deterministic dice transaction may have finished before the
            # Keeper turn got the per-conversation slot.  Refresh the
            # authoritative snapshot so the provider sees the state that was
            # actually committed, not a stale mutable object from before a
            # concurrent maintenance/state update.
            fresh_state = keeper._refresh_state_snapshot(state)
            current_timeline_id = fresh_state.timeline_id or f"legacy-{conversation_id}"
            if timeline_id and current_timeline_id != timeline_id:
                observability.event(
                    "check.result.stale",
                    level=logging.WARNING,
                    reason="timeline_mismatch",
                    requested_timeline_id=timeline_id,
                    current_timeline_id=current_timeline_id,
                    check_id=check_id or None,
                    decision_id=decision_id or None,
                )
                await reply("這個檢定結果所屬的劇情時間線已經失效，請依目前劇情重新操作。")
                return
            fresh_char = fresh_state.get_active_character(user_id)
            if fresh_char is None:
                await reply("這個檢定結果所屬的角色已經不在目前劇情中，請使用目前有效的角色操作。")
                return
            context_note = action_context or (
                "（未提供原始行動情境；只描述已確定的檢定結果，不要自行編造未確認的場景或行動。）"
            )
            identity_note = ""
            if check_id:
                identity_note += f" check_id={check_id}"
            if decision_id:
                identity_note += f" decision_id={decision_id}"
            if timeline_id:
                identity_note += f" timeline_id={timeline_id}"
            keeper_context_message = (
                f"【檢定結果上下文{identity_note}】\n"
                f"【玩家原始行動情境】{context_note}\n"
                f"{keeper_message}"
            )
            if resolved_event is not None:
                # Preserve changes directly caused by the deterministic roll
                # (SAN loss or Luck spend). For other fields, start observing
                # at the serialized Keeper phase so unrelated changes made
                # while the player was resolving the check aren't attributed
                # to this check.
                keeper_start = _character_attribute_snapshot(fresh_char)
                tracked_fields = set(resolved_event.get("tracked_roll_fields", []))
                for field_name in resolved_event["state_before"]:
                    if field_name not in tracked_fields:
                        resolved_event["state_before"][field_name] = keeper_start[field_name]
            keeper_kwargs = {}
            if resolved_event is not None:
                keeper_kwargs["resolved_check_context"] = {
                    key: resolved_event[key]
                    for key in (
                        "investigator", "skill", "skill_value", "roll", "difficulty",
                        "outcome", "action_context", "check_id", "timeline_id",
                    )
                    if key in resolved_event
                }
            keeper_reply, private_messages, image_requests = await keeper.run_turn(
                fresh_state,
                user_id,
                fresh_char.name,
                keeper_context_message,
                resolved_location,
                "player",
                **keeper_kwargs,
            )
            if resolved_event is not None:
                await asyncio.to_thread(
                    _persist_resolved_check_event, conversation_id, resolved_event
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
        char = state.get_active_character(user_id)
        if not char:
            return _CheckResolution(reply_text="你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
        attributes_before = _character_attribute_snapshot(char)

        parts = text.split()
        skill_arg: str | None = parts[2] if len(parts) > 2 else None
        pending = state.pending_checks.pop(user_id, None)
        timeline_id = state.timeline_id or f"legacy-{conversation_id}"
        if pending:
            # Treat explicit null as an absent legacy timeline.  Converting it
            # with str(...) would produce "None" and incorrectly reject the
            # otherwise valid pending entry.
            pending_timeline_id = str(pending.get("timeline_id") or "").strip()
            if pending_timeline_id and pending_timeline_id != timeline_id:
                observability.event(
                    "check.result.stale",
                    level=logging.WARNING,
                    reason="timeline_mismatch",
                    requested_timeline_id=pending_timeline_id,
                    current_timeline_id=timeline_id,
                    owner_id_hash=observability.safe_identifier(user_id),
                )
                save_state(state)
                return _CheckResolution(reply_text="這個檢定所屬的劇情時間線已經失效，請依目前劇情重新操作。")
        # Keep the original entry separate from `pending`: a valid choice
        # consumes the pending entry into the selected option, but its
        # identity and action context must still follow that same persisted
        # request.  Metadata is computed only after the skill/type match has
        # been validated, so a mismatched command can never reuse old data for
        # a fresh roll.
        pending_entry = pending

        # A pending "choice" check (see keeper.py's offer_check_choice — e.g. 閃避
        # vs 反擊) needs the player to name one of the options; unlike the plain
        # skill/sanity cases below, an unmatched or missing skill_arg here puts
        # the pending check back rather than discarding it, since silently losing
        # the whole choice prompt over a typo would be a worse experience than a
        # skill/sanity mismatch just falling through to a fresh check.
        choice_skill_name = choice_display_label = None
        choice_value: int | None = None
        choice_bonus: int | None = None
        choice_penalty: int | None = None
        choice_attacker_tier = None
        choice_is_counter = False
        # Only set when the pending choice is a ranged offer_npc_attack_defense_choice
        # (see keeper.py's is_ranged branch) — the attacker's roll is deferred until
        # right here, after the defender's own dive-for-cover result is known, rather
        # than pre-rolled like melee's choice_attacker_tier above (which is already
        # rolled by the time the pending entry exists). See _build_check_narration's
        # ranged_attacker param.
        choice_ranged_attacker: dict[str, int] | None = None
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
            choice_value = int(matched["skill_value"])
            choice_bonus = int(matched["bonus_dice"])
            choice_penalty = int(matched["penalty_dice"])
            choice_attacker_tier = pending.get("attacker_tier")
            choice_is_counter = dice.is_counter_option(matched)
            if pending.get("is_ranged"):
                choice_ranged_attacker = {
                    "skill_value": int(pending.get("attacker_skill_value", 0)),
                    "bonus_dice": int(pending.get("attacker_bonus_dice", 0)),
                    "penalty_dice": int(pending.get("attacker_penalty_dice", 0)),
                }
            pending = None
        elif skill_arg is None:
            if not pending:
                return _CheckResolution(
                    reply_text=(
                        "目前沒有待處理的選擇。請先讓 Keeper 建立檢定；玩家用 /coc check 或按鈕擲骰，"
                        "不要在沒有待處理請求時重複送出。"
                    )
                )
        elif pending and pending.get("type") == "sanity":
            state.pending_checks[user_id] = pending
            return _CheckResolution(reply_text="目前等待的是理智檢定，請不要自行指定技能；這筆舊版檢定會由系統處理。")
        elif not (pending and pending.get("type") == "skill" and _skill_names_match(pending.get("skill", ""), skill_arg)):
            if pending:
                state.pending_checks[user_id] = pending
            return _CheckResolution(
                reply_text=(
                    "沒有這個待處理的選擇。請使用正確的選項名稱；角色檢定由玩家用 /coc check 或按鈕擲骰。"
                )
            )

        check_id = effective_check_id(user_id, pending_entry, timeline_id) if pending_entry else new_check_id()
        action_context = str(pending_entry.get("action_context", "")).strip() if pending_entry else ""
        if not action_context:
            recent_user_message = next(
                (
                    str(entry.get("content", "")).strip()
                    for entry in reversed(state.log)
                    if entry.get("role") == "user" and str(entry.get("content", "")).strip()
                ),
                "",
            )
            action_context = recent_user_message[:237] + "..." if len(recent_user_message) > 240 else recent_user_message

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

            if sanity_result.risk_of_madness and not state.autoroll_checks:
                int_value = keeper.resolve_skill_value(char, "INT")
                chained_context = f"{action_context}；因 SAN 損失需要做 INT 檢定"
                if len(chained_context) > 240:
                    chained_context = chained_context[:237] + "..."
                origin_context = observability.current_context()
                state.pending_checks[user_id] = {
                    "type": "skill", "skill": "INT", "skill_value": int_value,
                    "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
                    "madness_trigger": True, "madness_realtime": True,
                    "check_id": new_check_id(), "timeline_id": timeline_id,
                    "origin_revision": state.state_revision + 1,
                    "origin_turn_id": str(origin_context.get("turn_id", "")),
                    "origin_request_id": str(origin_context.get("request_id", "")),
                    "action_context": chained_context,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                roll_line += (
                    "\n⚠️ 這次損失達到 5 點以上，觸發 COC7e「短暫瘋狂」規則：請玩家再用 /coc check INT。"
                )
                keeper_message = (
                    f"（{char.name} 的 SAN 檢定已確定：擲出 {sanity_result.check.roll} → {outcome}，"
                    f"損失 {sanity_result.loss} 點，現在 SAN {sanity_result.san_after}；因為損失達到 5 點以上，"
                    "已建立待處理 INT 檢定，等待玩家擲骰後再判定是否短暫瘋狂。）"
                )
            elif sanity_result.risk_of_madness:
                int_value = keeper.resolve_skill_value(char, "INT")
                int_result = dice.skill_check(int_value)
                if int_result.success:
                    madness = dice.roll_madness(realtime=True)
                    roll_line += (
                        f"\n⚠️ 損失達到 5 點，INT {int_value}% 擲出 {int_result.roll}，觸發短暫瘋狂："
                        f"症狀「{madness['symptom']}」（持續約{madness['duration']}）。"
                    )
                    keeper_message = (
                        f"（{char.name} 的 SAN 檢定已確定：擲出 {sanity_result.check.roll} → {outcome}，"
                        f"損失 {sanity_result.loss} 點，現在 SAN {sanity_result.san_after}；後續 INT 檢定"
                        f"擲出 {int_result.roll}，觸發短暫瘋狂，症狀是「{madness['symptom']}」，持續約"
                        f"{madness['duration']}。請照這個既定結果敘事，不要重新判定。）"
                    )
                else:
                    roll_line += f"\n⚠️ 損失達到 5 點，INT {int_value}% 擲出 {int_result.roll}，未觸發短暫瘋狂。"
                    keeper_message = (
                        f"（{char.name} 的 SAN 檢定已確定：擲出 {sanity_result.check.roll} → {outcome}，"
                        f"損失 {sanity_result.loss} 點，現在 SAN {sanity_result.san_after}；後續 INT 檢定"
                        f"擲出 {int_result.roll}，未觸發短暫瘋狂。請照這個既定結果敘事，不要重新判定。）"
                    )
            else:
                keeper_message = (
                    f"（{char.name} 的理智檢定已由玩家觸發擲骰：SAN {san_before} 擲出 {sanity_result.check.roll} → {outcome}，"
                    f"損失 {sanity_result.loss} 點理智，現在 SAN {sanity_result.san_after}。這是已經確定的結果，"
                    f"請根據這個結果描述角色的反應與後續發展，不要重新判定或改變這個結果。）"
                )
            roll_feedback_text, keeper_header = _build_split_check_feedback(
                char.name, "理智檢定", f"SAN {san_before}", sanity_result.check.roll, outcome
            )
            save_state(state)
            return _CheckResolution(
                state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
                roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True,
                check_id=check_id, timeline_id=timeline_id, action_context=action_context,
                resolved_event=_resolved_check_event_seed(
                    check_id=check_id, timeline_id=timeline_id, owner_id=user_id,
                    character_id=char.character_id,
                    investigator=char.name, skill="SAN", skill_value=san_before,
                    roll=sanity_result.check.roll, difficulty="regular", outcome=outcome,
                    before=attributes_before, tracked_roll_fields=("san",),
                ),
            )

        is_pushed = False
        attacker_tier = None
        ranged_attacker: dict[str, int] | None = None  # see choice_ranged_attacker above
        difficulty = "regular"  # offer_check_choice options and a self-initiated /coc check with no
        # pending Keeper request have no difficulty concept — only a Keeper-registered plain skill_check
        # (see keeper.py's skill_check tool difficulty param) can set this above "regular".
        madness_trigger = False  # only set True for the INT check chained onto a >=5 SAN loss — see above
        madness_realtime = True
        major_wound_trigger = False  # only set True for the CON check chained onto a major wound — see
        # keeper.py's adjust_character tool. Unlike madness_trigger, this does NOT get an early-return
        # branch below: success here is a normal good outcome, so it flows through the ordinary Luck-spend
        # path like any other skill check — only _build_check_narration needs to know about it.
        is_counter = False
        if choice_skill_name is not None:
            skill_name, value, bonus, penalty = choice_skill_name, choice_value, choice_bonus, choice_penalty
            display_label = choice_display_label
            attacker_tier = choice_attacker_tier
            ranged_attacker = choice_ranged_attacker
            is_counter = choice_is_counter
        else:
            if pending:
                skill_name, value, bonus, penalty = pending["skill"], pending["skill_value"], pending["bonus_dice"], pending["penalty_dice"]
                is_pushed = bool(pending.get("pushed", False))
                difficulty = pending.get("difficulty", "regular")
                madness_trigger = bool(pending.get("madness_trigger", False))
                madness_realtime = bool(pending.get("madness_realtime", True))
                major_wound_trigger = bool(pending.get("major_wound_trigger", False))
            else:
                return _CheckResolution(
                    reply_text=(
                        "目前沒有待處理的檢定。請先描述行動讓 Keeper 建立檢定，再用 /coc check 或按鈕擲骰。"
                    )
                )
            display_label = None
        value = int(value or 0)
        bonus = int(bonus or 0)
        penalty = int(penalty or 0)
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
                roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True,
                check_id=check_id, timeline_id=timeline_id, action_context=action_context,
                resolved_event=_resolved_check_event_seed(
                    check_id=check_id, timeline_id=timeline_id, owner_id=user_id,
                    character_id=char.character_id,
                    investigator=char.name, skill="INT", skill_value=value,
                    roll=skill_result.roll, difficulty=difficulty,
                    outcome=f"{skill_result.tier} {'成功' if skill_result.success else '失敗'}",
                    before=attributes_before,
                ),
            )

        # Luck-spend: always offered whenever there's at least one tier-
        # improving option the player can afford (buyable_options already
        # filters to cost <= luck available) — no cost cap on top of that;
        # see docs/specs/enhancement-luck-buyup-always-offered.md for why
        # the previous "<=7" near-miss-only gate was removed. Sanity checks
        # are excluded (handled above, already finalized by this point),
        # and so is a Pushed Roll (COC7e optional rule: a pushed reroll's
        # result is final, can't be bought up again with Luck on top of it).
        luck_options = [] if is_pushed else luck.buyable_options(value, skill_result.roll, skill_result.tier, char.luck, difficulty)
        if luck_options:
            origin_context = observability.current_context()
            state.pending_luck_decisions[user_id] = {
                "decision_id": new_decision_id(), "check_id": check_id, "timeline_id": timeline_id,
                "origin_revision": state.state_revision + 1,
                "origin_turn_id": str(origin_context.get("turn_id", "")),
                "origin_request_id": str(origin_context.get("request_id", "")),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "action_context": action_context,
                "skill_name": skill_name, "display_label": display_label, "is_counter": is_counter,
                "value": value, "roll": skill_result.roll, "bonus_dice": bonus, "penalty_dice": penalty,
                "original_tier": skill_result.tier, "attacker_tier": attacker_tier, "difficulty": difficulty,
                "options": [{"tier": o.tier, "cost": o.cost} for o in luck_options],
                "major_wound_trigger": major_wound_trigger,
                "ranged_attacker": ranged_attacker,
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
                ),
                check_id=check_id, timeline_id=timeline_id, action_context=action_context,
                decision_id=state.pending_luck_decisions[user_id]["decision_id"]
            )

        # Roll the ranged attacker's shot exactly once here (see
        # _resolve_ranged_defense_outcome's docstring) — reused below for
        # both _build_check_narration and roll_feedback_text instead of
        # letting each side call it separately, which would roll twice.
        ranged_opposed_text = (
            _resolve_ranged_defense_outcome(char.name, skill_result.success, ranged_attacker)
            if ranged_attacker is not None else None
        )
        roll_line, keeper_message = _build_check_narration(
            char, skill_name, display_label, value, skill_result, bonus, penalty, attacker_tier=attacker_tier,
            major_wound_trigger=major_wound_trigger, ranged_opposed_text=ranged_opposed_text, is_counter=is_counter,
        )
        opposed_text = ""
        if attacker_tier is not None:
            opposed_text = _describe_opposed_outcome(char.name, is_counter, skill_result.tier, attacker_tier)
        elif ranged_opposed_text:
            opposed_text = ranged_opposed_text
        roll_feedback_text, keeper_header = _build_split_check_feedback(
            char.name, display_label or skill_name, str(value), skill_result.roll, _tier_zh_for_result(skill_result), opposed_text
        )
        save_state(state)
        return _CheckResolution(
            state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
            roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True,
            check_id=check_id, timeline_id=timeline_id, action_context=action_context,
            resolved_event=_resolved_check_event_seed(
                check_id=check_id, timeline_id=timeline_id, owner_id=user_id,
                character_id=char.character_id,
                investigator=char.name, skill=skill_name, skill_value=value,
                roll=skill_result.roll, difficulty=difficulty,
                outcome=f"{skill_result.tier} {'成功' if skill_result.success else '失敗'}",
                before=attributes_before,
            ),
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
) -> bool:
    """Resolve a pending choice or legacy pending check.

    New ordinary skill, attack, and SAN checks are rolled immediately inside
    Keeper tools. This command remains for selecting a pending Dodge/Fight Back
    option and for compatibility with snapshots created before that change;
    it is not a player-owned dice command.
    """
    resolution = await asyncio.to_thread(_resolve_check_deterministically, conversation_id, user_id, text)
    if resolution.reply_text:
        await reply(resolution.reply_text)
        return False
    if not resolution.should_finalize or resolution.state is None or resolution.char is None:
        return False
    await _finalize_check_result(
        conversation_id, user_id, resolution.state, resolution.char, resolution.roll_line, resolution.keeper_message,
        reply, send_dm, send_image, send_dm_image, split_roll_feedback,
        acquire_legacy_for_keeper=acquire_legacy_for_keeper,
        roll_feedback_text=resolution.roll_feedback_text, keeper_header=resolution.keeper_header,
        check_id=resolution.check_id, decision_id=resolution.decision_id,
        timeline_id=resolution.timeline_id, action_context=resolution.action_context,
        resolved_event=resolution.resolved_event,
    )
    return True


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
) -> bool:
    """Resolves a pending Luck-spend decision (see handle_check_command above
    and app/luck.py) — either "skip" (keep the natural roll) or a tier name
    ("regular"/"hard"/"extreme") to buy up to, deducting the cost from the
    character's Luck before handing the (possibly improved) result to the
    Keeper exactly like a normal check."""
    resolution = await asyncio.to_thread(_resolve_luck_decision_deterministically, conversation_id, user_id, choice)
    if resolution.reply_text:
        await reply(resolution.reply_text)
        return False
    if not resolution.should_finalize or resolution.state is None or resolution.char is None:
        return False
    await _finalize_check_result(
        conversation_id, user_id, resolution.state, resolution.char, resolution.roll_line, resolution.keeper_message,
        reply, send_dm, send_image, send_dm_image, split_roll_feedback,
        acquire_legacy_for_keeper=acquire_legacy_for_keeper,
        roll_feedback_text=resolution.roll_feedback_text, keeper_header=resolution.keeper_header,
        check_id=resolution.check_id, decision_id=resolution.decision_id,
        timeline_id=resolution.timeline_id, action_context=resolution.action_context,
        resolved_event=resolution.resolved_event,
    )
    return True


def _resolve_luck_decision_deterministically(
    conversation_id: str, user_id: str, choice: str
) -> _CheckResolution:
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        pending = state.pending_luck_decisions.pop(user_id, None)
        if not pending:
            return _CheckResolution(reply_text="目前沒有待決定的 Luck 花費。")
        char = state.get_active_character(user_id)
        if not char:
            return _CheckResolution(reply_text="找不到你的角色。")
        attributes_before = _character_attribute_snapshot(char)

        timeline_id = state.timeline_id or f"legacy-{conversation_id}"
        pending_timeline_id = str(pending.get("timeline_id") or "").strip()
        if pending_timeline_id and pending_timeline_id != timeline_id:
            observability.event(
                "luck.result.stale",
                level=logging.WARNING,
                reason="timeline_mismatch",
                requested_timeline_id=pending_timeline_id,
                current_timeline_id=timeline_id,
                owner_id_hash=observability.safe_identifier(user_id),
            )
            save_state(state)
            return _CheckResolution(reply_text="這個 Luck 決定所屬的劇情時間線已經失效，請依目前劇情重新操作。")
        decision_id = effective_decision_id(user_id, pending, timeline_id)
        # Keep the narration/result identity aligned with the persisted
        # pending check.  Older Luck entries may not have check_id, so use the
        # same deterministic legacy derivation as the button path instead of
        # generating a new random id during resolution.
        check_id = effective_check_id(user_id, pending, timeline_id)
        action_context = str(pending.get("action_context", "")).strip()[:240]
        if not action_context:
            recent_user_message = next(
                (
                    str(entry.get("content", "")).strip()
                    for entry in reversed(state.log)
                    if entry.get("role") == "user" and str(entry.get("content", "")).strip()
                ),
                "",
            )
            action_context = recent_user_message[:237] + "..." if len(recent_user_message) > 240 else recent_user_message

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
        # Roll the ranged attacker's shot exactly once here, using the FINAL
        # (post-Luck-decision) r.success — see _resolve_ranged_defense_outcome's
        # docstring on why this must not be called more than once.
        ranged_attacker = pending.get("ranged_attacker")
        ranged_opposed_text = (
            _resolve_ranged_defense_outcome(char.name, r.success, ranged_attacker)
            if ranged_attacker is not None else None
        )
        # _build_check_narration can itself mutate char (e.g. appending "昏迷"/
        # "倒地" to status_tags for a failed major_wound_trigger check — see
        # its docstring), so save_state has to happen AFTER this call, not
        # before it: keeper.run_turn's own state commit (_commit_turn_result)
        # does a *fresh* load_state rather than persisting this same `state`
        # object, so any mutation made after an earlier save here would
        # otherwise be silently discarded.
        pending_is_counter = bool(pending.get("is_counter", False))
        roll_line, keeper_message = _build_check_narration(
            char, pending["skill_name"], pending["display_label"], pending["value"], r,
            pending["bonus_dice"], pending["penalty_dice"],
            luck_spent=luck_spent, original_tier=pending["original_tier"],
            attacker_tier=pending.get("attacker_tier"),
            major_wound_trigger=bool(pending.get("major_wound_trigger", False)),
            ranged_opposed_text=ranged_opposed_text, is_counter=pending_is_counter,
        )
        save_state(state)
        outcome_text = _tier_zh_for_tier(tier, required_tier)
        if luck_spent:
            result_line = f"花費 {luck_spent} 點幸運：{pending['roll']} → {outcome_text}"
        else:
            result_line = f"維持原結果：{pending['roll']} → {outcome_text}"
        # Code review: this call used to omit opposed_text entirely, so
        # neither a melee attacker_tier nor a ranged_opposed_text ever
        # reached the deterministic, player-facing split feedback — the shot
        # was rolled above (see ranged_opposed_text's assignment) and the
        # narration got it via _build_check_narration, but the split path's
        # own roll_feedback_text/keeper_header never displays roll_line, so
        # the authoritative outcome was silently absent from it, leaving the
        # player dependent on generated Keeper narration to ever see it.
        opposed_text = ""
        pending_attacker_tier = pending.get("attacker_tier")
        if pending_attacker_tier is not None:
            opposed_text = _describe_opposed_outcome(char.name, pending_is_counter, tier, pending_attacker_tier)
        elif ranged_opposed_text:
            opposed_text = ranged_opposed_text
        roll_feedback_text, keeper_header = _build_split_check_feedback(
            char.name,
            pending["display_label"] or pending["skill_name"],
            str(pending["value"]),
            pending["roll"],
            outcome_text,
            opposed_text,
            result_line=result_line,
        )
        return _CheckResolution(
            state=state, char=char, roll_line=roll_line, keeper_message=keeper_message,
            roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True,
            check_id=check_id, decision_id=decision_id, timeline_id=timeline_id,
            action_context=action_context,
            resolved_event=_resolved_check_event_seed(
                check_id=check_id, timeline_id=timeline_id, owner_id=user_id,
                character_id=char.character_id,
                investigator=char.name, skill=pending["skill_name"],
                skill_value=pending["value"], roll=pending["roll"],
                difficulty=required_tier,
                outcome=f"{r.tier} {'成功' if r.success else '失敗'}" + (f"；花費 Luck {luck_spent}" if luck_spent else ""),
                before=attributes_before, tracked_roll_fields=(("luck",) if luck_spent else ()),
            ),
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
    char = state.get_active_character(user_id)
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
    _logger.info("_find_room_via_rag query=%r", text)  # see app/keeper.py's search_scenario for why
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
    char = state.get_active_character(user_id)
    if not char:
        return None
    return (
        f"你已經有角色「{char.name}」了，這局遊戲進行中不能重新建角（避免蓋掉正在用的角色）。"
        "如果真的要換角色，請先讓這局遊戲結束（/coc end），或開新的一局（/coc newgame）。"
    )


def _claim_pregen(state: GroupState, index: int, user_id: str, *, custom_name: str | None = None) -> Character:
    """Claim one pregen exactly once and update all state-owned references.

    ``pregen_to_character`` remains a pure constructor so previews and shared
    pregens are safe. Both command routers use this boundary; a repeated claim
    is rejected before a player LUCK roll can be requested twice.
    """
    if not (0 <= index < len(state.pregens)):
        raise ValueError("預製角色編號超出範圍。")
    # A finished game may legitimately let the same player claim a new
    # unclaimed pregen. During an active game the command-level guard already
    # enforces one active investigator; keep the same defensive check here.
    if state.active and state.characters_for_owner(user_id):
        raise ValueError("你目前已經有角色，不能重複認領預製角色。")
    if user_id in state.pending_pregen_luck:
        raise ValueError("你還有一位預製角色尚未完成 LUCK 擲骰，請先輸入「/coc luck roll」。")
    pregen = state.pregens[index]
    claimed_by = pregen.get("claimed_by")
    if claimed_by:
        if claimed_by == user_id:
            raise ValueError("你已經認領過這位預製角色，不能重新骰定。")
        raise ValueError("這位角色已經被其他玩家選走了。")

    sheet_luck = pregen_extractor.pregen_luck_value(pregen.get("luck"))
    char = pregen_extractor.pregen_to_character(
        pregen, user_id, era=state.era, luck=sheet_luck if sheet_luck is not None else 0,
    )
    if custom_name:
        char.name = custom_name
    state.characters[user_id] = char
    state.set_active_character(user_id, char.character_id)
    pregen["claimed_by"] = user_id
    if sheet_luck is None:
        state.pending_pregen_luck[user_id] = char.character_id
    return char


async def handle_pregen_luck_roll(conversation_id: str, user_id: str, reply: Reply) -> None:
    """Resolve the player's explicit LUCK roll for a newly claimed pregen."""
    state = load_state(conversation_id)
    character_id = state.pending_pregen_luck.get(user_id)
    if not character_id:
        await reply("目前沒有等待你擲 LUCK 的預製角色；請先用「/coc usepregen 編號」選角。")
        return
    char = state.characters_by_id.get(character_id)
    if char is None:
        state.pending_pregen_luck.pop(user_id, None)
        save_state(state)
        await reply("找不到等待擲 LUCK 的角色，請重新選擇預製角色。")
        return
    char.luck = pregen_extractor.roll_player_luck()
    state.pending_pregen_luck.pop(user_id, None)
    save_state(state)
    await reply(f"🎲 {char.name} 的 LUCK 擲骰結果：{char.luck}。現在可以開始遊戲了。")


def _blocked_by_kp_assistant(state: GroupState, user_id: str) -> str | None:
    if state.kp_assistant_user_id != user_id:
        return None
    return "你目前是這局的 KP 助手，不能同時建立或使用調查員角色。請先使用「/coc kp quit」解除 KP 助手身分。"


def _set_character_away_state(conversation_id: str, user_id: str, away: bool) -> _AwayStateResult:
    with locks.get_state_lock(conversation_id):
        state = load_state(conversation_id)
        char = state.get_active_character(user_id)
        if not char:
            return _AwayStateResult(error_text="你還沒有角色。")
        char.away = away
        if char.character_id:
            state.characters_by_id[char.character_id] = char
            state.active_character_id_by_user[user_id] = char.character_id
        if state.characters.get(user_id) and state.characters[user_id].character_id == char.character_id:
            state.characters[user_id].away = away
        save_state(state)
        return _AwayStateResult(character_name=char.name)


def _pregen_full_sheet_text(pregen: dict, index: int) -> str:
    """Read-only pregen preview, capped to the same top 12 skills as Character."""
    lines = [
        f"【預製角色 #{index}】{pregen.get('name') or '未命名'}　職業：{pregen.get('occupation', '未知職業')}",
    ]
    attrs = ["str_", "con", "siz", "dex", "app", "int_", "pow_", "edu"]
    labels = {"str_": "STR", "con": "CON", "siz": "SIZ", "dex": "DEX", "app": "APP", "int_": "INT", "pow_": "POW", "edu": "EDU"}
    attr_line = " ".join(f"{labels[a]} {pregen[a]}" for a in attrs if isinstance(pregen.get(a), (int, float)))
    sheet_luck = pregen_extractor.pregen_luck_value(pregen.get("luck"))
    if sheet_luck is not None:
        attr_line += f"{' ' if attr_line else ''}（卡面 LUCK {sheet_luck}，選用時沿用）"
    else:
        attr_line += f"{' ' if attr_line else ''}（LUCK 空白，選用後由玩家擲骰）"
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
        ranked = sorted(skills.items(), key=lambda kv: -kv[1] if isinstance(kv[1], (int, float)) else 0)[:12]
        lines.append("主要技能：" + "、".join(f"{k} {v}%" for k, v in ranked))
    if pregen.get("notes"):
        lines.append(f"背景：{pregen['notes']}")
    if pregen.get("key_connection"):
        lines.append(f"★ 關鍵背景連結：{pregen['key_connection']}")
    for key, value in (pregen.get("extra_fields") or {}).items():
        if value not in (None, "", [], {}):
            lines.append(f"{key}：{value}")
    if pregen.get("claimed_by") or pregen.get("claimed"):
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
    defaults to the bare id when no Discord mention formatter is supplied."""
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
