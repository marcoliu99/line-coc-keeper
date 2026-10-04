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
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from app import (
    dice,
    intent_parser,
    locks,
    observability,
    pdf_loader,
    pregen_extractor,
    scenario_activation,
    scenario_compare,
    scenario_index,
    scenario_library,
    scenario_rag,
    scenario_templates,
)
from app import scene_map as scene_map_engine
from app.commands.types import (
    FormatMention,
    GetDisplayName,
    PdfChoice,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
)
from app.config import SCENARIO_RAG_ENABLED
from app.keeper_tools import resource_bridge
from app.models import (
    BASE_SKILLS,
    OCCUPATIONS,
    Character,
    GroupState,
)
from app.repositories import manual_pregens, state_transaction
from app.repositories.group_state import (
    load_state,
)
from app.services import mutation_admission

_logger = logging.getLogger(__name__)

__all__ = [
    "FormatMention",
    "GetDisplayName",
    "Reply",
    "SendDM",
    "SendDMImage",
    "SendImage",
    "handle_map_upload",
    "handle_pdf_upload",
    "handle_pregen_luck_roll",
    "handle_role_sheet_upload",
    "handle_roll_command",
    "handle_scenario_compare_upload",
    "handle_unsupported_message",
    "resolve_pdf_upload_choice",
]


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
    replacement_block = resource_bridge.guard_replacement(state)
    if replacement_block:
        raise ValueError(replacement_block)
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
    state.check_consequence_origins.clear()
    state.check_consequence_receipts.clear()
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
    docs/specs/feature/character_and_dictionary_system_spec.md's Module 1. pregens is NOT
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
    scenario_index.report_location_index(
        state.scenario_location_index, source="correction", scenario_title=title)
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
    scenario_activation.install_context_fields(
        state, scenario_id, context, preserve_maps=preserve_maps,
        preserve_pregens=preserve_pregens,
    )


def _pdf_upload_confirmation_text(
    title: str,
    text: str,
    low_text_pages: list[int],
    truncated: bool,
    page_maps: dict,
    extracted_index: dict,
    pregen_count: int,
    artifact_notice: str = "",
) -> str:
    """Shared by the immediate (first-ever upload) and deferred (button-
    resolved) paths through handle_pdf_upload — the message is identical
    either way, just built at a different point in the flow."""
    warning = ""
    # Whatever the reporter decided, verbatim. Re-deciding here is how the
    # missing-floor-plan case went unseen: this only looked at the index.
    if artifact_notice:
        warning += "\n\n" + artifact_notice
    if low_text_pages:
        pages_str = "、".join(str(p) for p in low_text_pages)
        warning += (
            f"\n\n⚠️ 第 {pages_str} 頁有解析品質待核對項目（文字不足、版面歧義或辨識結果），"
            "請對照原稿確認，尤其是表格數值與跨頁規則；如有缺漏，請修正來源後重新解析。"
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


@mutation_admission.guard_async_entry
async def handle_pdf_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    pdf_bytes: bytes,
    file_name: str,
    skip_similarity: bool = False,
    reparse_candidate_id: str | None = None,
    expected_revision: int | None = None,
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
    if expected_revision is not None and existing_state.state_revision != expected_revision:
        await reply("遊戲狀態已更新，請重新開啟 Help 操作。")
        return False
    previous_content_hash = ""
    if existing_state.scenario_library_id:
        try:
            previous_content_hash = scenario_library.load_context(
                existing_state.scenario_library_id
            )["manifest"].get("content_hash", "")
        except (FileNotFoundError, ValueError):
            pass
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
                if expected_revision is not None and state.state_revision != expected_revision:
                    scenario_library.discard_staged_upload(key)
                    await reply("遊戲狀態已更新，請重新開啟 Help 操作。")
                    return False
                if state.pending_scenario_upload is not None:
                    scenario_library.discard_staged_upload(key)
                    await reply("已有一份相似 PDF 等待處理，請先用 /coc scenario reparse 或 /coc scenario cancel。")
                    return False
                state.pending_scenario_upload = {"key": key, "file_name": file_name, "title": preview_title, "matches": matches}
                state_transaction.commit_snapshot(state)
            labels = "、".join(f"{m['id']}《{m['title']}》（{m['score']:.0%}）" for m in matches[:3])
            await reply(f"偵測到相似劇本：{labels}。若要重新解析請輸入 /coc scenario reparse；放棄請輸入 /coc scenario cancel。")
            return False

    await reply("收到了，正在讀取劇本內容；圖片較多的劇本需要較長時間，請稍候...")

    parse_quality: dict = {}
    try:
        text, low_text_pages, truncated, page_images, page_maps = await asyncio.to_thread(
            pdf_loader.extract_text, pdf_bytes, quality_report=parse_quality
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
        parse_quality=parse_quality,
    )
    library_context = await asyncio.to_thread(scenario_library.load_context, scenario_id)
    text = library_context["text"]
    extracted_index = library_context["indexes"]
    pregens = library_context["pregens"]
    page_maps = library_context["scene_maps"]

    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if expected_revision is not None and state.state_revision != expected_revision:
            await push("遊戲狀態已更新，這份 PDF 沒有套用；請重新開啟 Help 操作。")
            return False
        # Do not expose the replacement PDF's images until the GM has chosen
        # new-versus-correction. The immutable library entry already contains
        # them; the selected two-chapter window is copied only on activation.
        if state.pending_pdf_upload is not None:
            raced = True
        elif state.scenario_text.strip():
            raced = False
            state.pending_pdf_upload = {
                "scenario_id": scenario_id,
                "previous_content_hash": previous_content_hash,
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
            state_transaction.commit_snapshot(state)
            current_title = state.scenario_title
            confirmation_pending = True
        else:
            raced = False
            old_pool = list(state.pregens)
            _apply_new_scenario(state, text, library_context["manifest"]["title"], extracted_index, page_maps, pregens)
            _install_library_context(state, scenario_id, library_context)
            install_result: dict[str, bool] = {}
            def install_first(conn):
                manual_pregens.capture_legacy(conn, conversation_id, None, old_pool)
                state.pregens, install_result["stale"] = manual_pregens.install_pool(
                    conn, conversation_id, scenario_id, library_context, bind_unassigned=True,
                )
            _, image_refreshed = scenario_activation.commit_and_refresh(
                lambda: state_transaction.commit_snapshot(state, mutate_tx=install_first),
                conversation_id, scenario_id, library_context,
            )
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

    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    await push(_pdf_upload_confirmation_text(
        title, text, low_text_pages, truncated, page_maps, extracted_index, final_pregen_count,
        scenario_index.report_location_index(
            state.scenario_location_index, source="pdf_upload",
            scenario_title=state.scenario_title, scene_maps=state.scene_maps),
    ) + (f"\n{variant_notice}" if variant_notice else "")
      + ("\n頁面圖片快取刷新失敗；劇本已啟用，請聯絡 KP 檢查圖片。" if not image_refreshed else "")
      + ("\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。" if install_result.get("stale") else ""))
    return True


def _resolve_pdf_upload_choice_locked(conversation_id: str, choice: PdfChoice) -> str:
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
        state_transaction.commit_snapshot(state)
        return "這個待處理劇本庫項目已不存在，請重新上傳 PDF。"
    extracted_index = context["indexes"]
    old_pool = list(state.pregens)
    old_scenario_id = state.scenario_library_id or None
    old_hash = ""
    if old_scenario_id == scenario_id:
        old_hash = pending.get("previous_content_hash", "")
    elif old_scenario_id:
        try:
            old_hash = scenario_library.load_context(old_scenario_id)["manifest"].get("content_hash", "")
        except (FileNotFoundError, ValueError):
            pass
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
    state.pending_pdf_upload = None
    install_result: dict[str, bool] = {}
    def install_selected(conn):
        manual_pregens.capture_legacy(conn, conversation_id, old_scenario_id, old_pool, old_hash)
        claimed = [p for p in old_pool if p.get("claimed_by")] if choice != "new" else []
        state.pregens, install_result["stale"] = manual_pregens.install_pool(
            conn, conversation_id, scenario_id, context,
            bind_unassigned=(old_scenario_id is None), claimed=claimed,
        )
    _, image_refreshed = scenario_activation.commit_and_refresh(
        lambda: state_transaction.commit_snapshot(state, mutate_tx=install_selected),
        conversation_id, scenario_id, context,
    )
    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    return _pdf_upload_confirmation_text(
        context["manifest"]["title"], context["text"], pending["low_text_pages"], pending["truncated"],
        context["scene_maps"], extracted_index, len(state.pregens),
        scenario_index.report_location_index(
            state.scenario_location_index, source="pdf_upload",
            scenario_title=state.scenario_title, scene_maps=state.scene_maps),
    ) + ("\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。" if install_result.get("stale") else "") + (f"\n{variant_notice}" if variant_notice else "") + ("\n頁面圖片快取刷新失敗；劇本已啟用，請聯絡 KP 檢查圖片。" if not image_refreshed else "")

@mutation_admission.guard_async_entry
async def resolve_pdf_upload_choice(
    conversation_id: str,
    choice: PdfChoice,
    push: Reply,
    user_id: str = "",
) -> None:
    """Called by Discord's PdfUploadChoiceButton once the GM picks between the
    two options offered by handle_pdf_upload. `choice` must be "new" or "fix";
    the text command remains available as a manual fallback. The actor is
    checked again while holding the conversation lock so a button cannot
    mutate the scenario from an unauthorized account."""
    # Imported here: app.commands imports this module at package import time.
    from app.commands import permissions

    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if not permissions.may_manage_scenario_lifecycle(state, user_id):
            await push(permissions.kp_only("處理劇本 PDF"))
            return
        text = _resolve_pdf_upload_choice_locked(conversation_id, choice)
        state = load_state(conversation_id)
    scenario_templates.schedule_index_prewarm(state)
    await push(text)


@mutation_admission.guard_async_entry
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
        state_transaction.commit_snapshot(state)

    entry_room = scene_map_engine.get_room(data, data.get("entry_room_id", ""))
    entry_note = f"，入口房間「{entry_room['name']}」" if entry_room else ""
    warning_note = ""
    if import_warnings:
        warning_note = "\n\n⚠️ 轉換時有幾個地方略過了：\n" + "\n".join(f"・{w}" for w in import_warnings)
    await push(
        f"地圖「{data.get('location_name') or key}」已儲存（{len(data['rooms'])} 個房間{entry_note}）。\n"
        f"用「/coc enter {key}」載入這張地圖。" + warning_note
    )


@mutation_admission.guard_async_entry
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


@mutation_admission.guard_async_entry
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
        replacement_block = resource_bridge.guard_replacement(state)
        if replacement_block:
            await reply(replacement_block)
            return
        scenario_id = state.scenario_library_id or None
        try:
            context = scenario_library.load_context(scenario_id) if scenario_id else None
        except (FileNotFoundError, ValueError):
            context = None
            scenario_id = None
        old_pool = list(state.pregens)
        result: dict[str, str] = {}
        def save_manual(conn):
            manual_pregens.capture_legacy(
                conn, conversation_id, scenario_id, old_pool,
                context["manifest"].get("content_hash", "") if context else "",
            )
            result["asset_id"], result["action"] = manual_pregens.store_upload(
                conn, conversation_id, scenario_id, pregen, file_name,
            )
            if context and scenario_id:
                claimed = [p for p in old_pool if p.get("claimed_by")]
                state.pregens, _ = manual_pregens.install_pool(
                    conn, conversation_id, scenario_id, context, claimed=claimed,
                )
            else:
                state.pregens, _ = pregen_extractor.reconcile_pregen_into_pool(old_pool, pregen)
        try:
            state_transaction.commit_snapshot(state, mutate_tx=save_manual)
        except ValueError as exc:
            await reply(str(exc))
            return

    name_note = f"「{pregen['name']}」" if pregen["name"] else "（姓名由玩家決定）"
    action_note = "已更新" if result["action"] == "updated" else "已新增"
    await reply(
        f"角色卡{action_note}（資產 ID：{result['asset_id']}）：{name_note}，職業「{pregen['occupation']}」，"
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


@dataclass
class _MapActionResolution:
    context: dict | None = None
    needs_rag: bool = False


@dataclass
class _AwayStateResult:
    character_name: str = ""
    error_text: str = ""


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
        state_transaction.commit_snapshot(state)


def _resolve_map_action_transaction(conversation_id: str, user_id: str, text: str) -> dict | None:
    with locks.get_state_lock(conversation_id):
        mutation_admission.assert_admitted(conversation_id)
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


@mutation_admission.guard_async_entry
async def handle_pregen_luck_roll(conversation_id: str, user_id: str, reply: Reply) -> None:
    """Resolve the player's explicit LUCK roll for a newly claimed pregen."""
    state = load_state(conversation_id)
    if state.combat.active:
        await reply('戰鬥中不能補建未驗證的角色 Luck；原待處理事項仍保留。')
        return
    character_id = state.pending_pregen_luck.get(user_id)
    if not character_id:
        await reply("目前沒有等待你擲 LUCK 的預製角色；請先用「/coc usepregen 編號」選角。")
        return
    char = state.characters_by_id.get(character_id)
    if char is None:
        state.pending_pregen_luck.pop(user_id, None)
        state_transaction.commit_snapshot(state)
        await reply("找不到等待擲 LUCK 的角色，請重新選擇預製角色。")
        return
    char.luck = pregen_extractor.roll_player_luck()
    state.pending_pregen_luck.pop(user_id, None)
    state_transaction.commit_snapshot(state)
    await reply(f"🎲 {char.name} 的 LUCK 擲骰結果：{char.luck}。現在可以開始遊戲了。")


def _blocked_by_kp_assistant(state: GroupState, user_id: str) -> str | None:
    if state.kp_assistant_user_id != user_id:
        return None
    return "你目前是這局的 KP 助手，不能同時建立或使用調查員角色。請先使用「/coc kp quit」解除 KP 助手身分。"


def _set_character_away_state(conversation_id: str, user_id: str, away: bool) -> _AwayStateResult:
    with locks.get_state_lock(conversation_id):
        mutation_admission.assert_admitted(conversation_id)
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
        state_transaction.commit_snapshot(state)
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
    the game (see docs/specs/feature/character_and_dictionary_system_spec.md's Module 7) —
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
    narration — see docs/specs/feature/character_and_dictionary_system_spec.md's Module 7's
    own "範例二" for the format this follows (HP/SAN/weapons/items per
    character, not just name/occupation — a GM glancing at this should be
    able to tell at a glance whether everyone's actually equipped, not just
    who's playing who). `healed_notes` is owner_id -> whatever
    _heal_character found for them (empty list if nothing needed fixing).
    `format_mention` renders each owner_id for display (see FormatMention) —
    defaults to the bare id when no Discord mention formatter is supplied."""
    lines = ["📋 全團調查員集結就緒名冊", ""]
    for owner_id, committed in state.characters.items():
        char = resource_bridge.effective(state, committed)
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
