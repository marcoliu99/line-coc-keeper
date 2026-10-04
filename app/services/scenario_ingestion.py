"""Applying an uploaded scenario PDF, a choice about it, a scenario comparison or a role sheet.

Moved here unchanged from ``legacy_commands``: the order of calls, the arguments, the outputs and the warnings are
those of the original. It talks to its caller only through the ``Reply``/``push`` callbacks it is given.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from app import (
    locks,
    pdf_loader,
    pregen_extractor,
    scenario_compare,
    scenario_index,
    scenario_library,
    scenario_templates,
)
from app.keeper_tools import resource_bridge
from app.models import (
    OCCUPATIONS,
)
from app.repositories import manual_pregens, state_transaction
from app.repositories.group_state import load_state
from app.services import mutation_admission, scenario_admission

if TYPE_CHECKING:
    from app.commands.types import PdfChoice, Reply


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
    previous_content_hash = scenario_admission.content_hash(existing_state.scenario_library_id)
    block = scenario_admission.pending_block(existing_state, include_similar=not skip_similarity)
    if block == "pregen_luck":
        await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本 PDF。")
        return False
    if block == "upload_choice":
        assert existing_state.pending_pdf_upload is not None
        await reply(
            f"上一次上傳的《{existing_state.pending_pdf_upload['title']}》還沒選擇「全新劇本」"
            "還是「修正目前劇本」，請先點上一則訊息的按鈕選完，再上傳這份新的 PDF——不然這份新的"
            "會蓋掉還沒處理的那份，之後點到舊按鈕會套用到錯的內容。"
        )
        return False
    if block == "similar_upload":
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

    admission = await scenario_admission.admit_upload(
        conversation_id, scenario_id, library_context,
        previous_content_hash=previous_content_hash, low_text_pages=low_text_pages,
        truncated=truncated, expected_revision=expected_revision,
    )
    if admission.status == "stale_revision":
        await push("遊戲狀態已更新，這份 PDF 沒有套用；請重新開啟 Help 操作。")
        return False
    if admission.status == "raced":
        await push(
            f"這份《{title}》來得比較慢——另一份幾乎同時上傳的 PDF 先卡進待確認狀態了，請先處理完"
            "上一則訊息的選擇，再重新上傳這份。"
        )
        return False
    state = admission.state
    if admission.status == "needs_choice":
        await push(
            f"這個群組目前正在跑《{state.scenario_title}》。新上傳的《{title}》"
            "是要開始一個全新的劇本，還是修正/補完目前這份劇本？請點下面的按鈕選擇——"
            "選錯的代價不小（位置可能對到新劇本裡不存在的房間），拿不準的話選「修正目前劇本」比較安全。"
        )
        return True
    activation = admission.activation
    assert activation is not None

    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    await push(_pdf_upload_confirmation_text(
        title, text, low_text_pages, truncated, page_maps, extracted_index, len(state.pregens),
        scenario_index.report_location_index(
            state.scenario_location_index, source="pdf_upload",
            scenario_title=state.scenario_title, scene_maps=state.scene_maps),
    ) + (f"\n{variant_notice}" if variant_notice else "") + activation.image_note + activation.card_note)
    return True


_MARKDOWN_PAGE_MARKER_RE = re.compile(r"(?m)^-*\s*第\s*\d+\s*頁\s*-*\s*$")


def _markdown_scenario_title(text: str, file_name: str) -> str:
    """Derive a useful title without retaining the routing-only scenario prefix."""
    stem = Path(file_name).stem
    cleaned = re.sub(r"^scenario", "", stem, flags=re.IGNORECASE).strip(" _.-")
    if cleaned:
        return re.sub(r"_+", " ", cleaned).strip()
    for raw_line in text.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", raw_line)
        if match:
            return match.group(1).strip()
    return pdf_loader.guess_title(text)


@mutation_admission.guard_async_entry
async def handle_scenario_markdown_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    markdown_bytes: bytes,
    file_name: str,
) -> bool:
    """Load a scenario_*.md source through the normal scenario lifecycle.

    Markdown is already authoritative text, so this path deliberately skips
    PDF extraction/OCR and visual map/image generation. The original Markdown
    bytes are preserved in the scenario library while scenario.txt receives a
    synthetic page-1 marker only when the file has no page markers of its own.
    """
    lower_name = Path(file_name).name.lower()
    if not lower_name.startswith("scenario") or not lower_name.endswith(".md"):
        await reply("Markdown 劇本檔名需要以 scenario 開頭並使用 .md 副檔名。")
        return False
    try:
        decoded = markdown_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        await reply("Markdown 劇本必須是 UTF-8 編碼，這份檔案無法安全讀取。")
        return False
    decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
    if not decoded.strip():
        await reply("這份 Markdown 劇本沒有文字內容，沒有載入。")
        return False

    text = decoded
    if not _MARKDOWN_PAGE_MARKER_RE.search(text):
        text = "--- 第 1 頁 ---\n" + text

    existing_state = load_state(conversation_id)
    previous_content_hash = scenario_admission.content_hash(existing_state.scenario_library_id)
    block = scenario_admission.pending_block(existing_state)
    if block == "pregen_luck":
        await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本檔案。")
        return False
    if block == "upload_choice":
        assert existing_state.pending_pdf_upload is not None
        await reply(
            f"上一次上傳的《{existing_state.pending_pdf_upload['title']}》還沒選擇「全新劇本」"
            "還是「修正目前劇本」，請先完成上一份劇本的選擇，再上傳新的 Markdown。"
        )
        return False
    if block == "similar_upload":
        await reply("目前仍有一份相似劇本等待處理，請先完成 /coc scenario reparse 或 /coc scenario cancel。")
        return False

    await reply("收到了，正在讀取 Markdown 劇本並建立索引；這條路徑不會執行 PDF OCR。")
    title = _markdown_scenario_title(text, file_name)
    extracted_index = await asyncio.to_thread(scenario_index.extract_scenario_index, text)
    pregens = await asyncio.to_thread(pregen_extractor.extract_pregens, text)
    preview = text[:12_000]
    scenario_id = await asyncio.to_thread(
        scenario_library.save_markdown_scenario,
        markdown_bytes,
        title=title,
        filename=file_name,
        preview=preview,
        text=text,
        indexes=extracted_index,
        pregens=pregens,
        parse_quality={"source_format": "markdown", "text_only": True},
    )
    library_context = await asyncio.to_thread(scenario_library.load_context, scenario_id)
    text = library_context["text"]
    extracted_index = library_context["indexes"]
    pregens = library_context["pregens"]
    page_maps = library_context["scene_maps"]

    admission = await scenario_admission.admit_upload(
        conversation_id, scenario_id, library_context,
        previous_content_hash=previous_content_hash, low_text_pages=[], truncated=False,
        source_format="markdown",
    )
    if admission.status == "raced":
        await push(
            f"這份《{title}》完成得比較慢——另一份劇本先進入待確認狀態了。"
            "請先處理上一份，再重新上傳這份 Markdown。"
        )
        return False
    state = admission.state
    if admission.status == "needs_choice":
        await push(
            f"這個群組目前正在跑《{state.scenario_title}》。新上傳的《{title}》"
            "是要開始一個全新的劇本，還是修正/補完目前這份劇本？請點下面的按鈕選擇。"
        )
        return True
    activation = admission.activation
    assert activation is not None

    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    await push(
        _pdf_upload_confirmation_text(
            title,
            text,
            [],
            False,
            page_maps,
            extracted_index,
            len(state.pregens),
            scenario_index.report_location_index(
                state.scenario_location_index,
                source="markdown_upload",
                scenario_title=state.scenario_title,
                scene_maps=state.scene_maps,
            ),
        )
        + (f"\n{variant_notice}" if variant_notice else "")
        + activation.image_note
        + activation.card_note
    )
    return True


def apply_pdf_upload_choice(conversation_id: str, choice: PdfChoice) -> str:
    """Resolve a pending upload while the caller holds the conversation lock."""
    state = load_state(conversation_id)
    pending = state.pending_pdf_upload
    if pending is None:
        return "這個上傳選擇已經處理過了，或已經過期失效，請重新上傳劇本檔案。"
    scenario_id = pending.get("scenario_id")
    if not scenario_id:
        return "這個待處理上傳缺少劇本庫資料，請重新上傳劇本檔案。"
    try:
        context = scenario_library.load_context(scenario_id, pending.get("active_chapter_id", ""))
    except (FileNotFoundError, ValueError):
        state.pending_pdf_upload = None
        state_transaction.commit_snapshot(state)
        return "這個待處理劇本庫項目已不存在，請重新上傳劇本檔案。"
    extracted_index = context["indexes"]
    activation = scenario_admission.activate_pending_choice(conversation_id, state, pending, context, choice)
    variant_notice = scenario_templates.preference_notice(conversation_id, scenario_id)
    return _pdf_upload_confirmation_text(
        context["manifest"]["title"], context["text"], pending["low_text_pages"], pending["truncated"],
        context["scene_maps"], extracted_index, len(state.pregens),
        scenario_index.report_location_index(
            state.scenario_location_index, source="pdf_upload",
            scenario_title=state.scenario_title, scene_maps=state.scene_maps),
    ) + activation.card_note + (f"\n{variant_notice}" if variant_notice else "") + activation.image_note


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
        # Matching a card to a pregen under another name memoizes the pair in the
        # dictionary table, a write that cannot happen inside the state transaction.
        reconciled_pool = None if context and scenario_id else pregen_extractor.reconcile_pregen_into_pool(old_pool, pregen)[0]
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
                state.pregens = reconciled_pool
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
