"""Prepare PDF/Markdown scenario sources and render their existing upload UX.

Lifecycle admission, pending state, activation, and commit order live in
``scenario_lifecycle``. Comparison and role-sheet uploads retain their source
specific behavior here.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app import (
    locks,
    pdf_loader,
    pregen_extractor,
    scenario_compare,
    scenario_index,
    scenario_library,
    scenario_page_repair,
)
from app.keeper_tools import resource_bridge
from app.models import (
    OCCUPATIONS,
    GroupState,
)
from app.repositories import manual_pregens, page_repairs, state_transaction
from app.repositories.group_state import load_state
from app.services import mutation_admission, scenario_lifecycle

if TYPE_CHECKING:
    from app.commands.types import PdfChoice, Reply


@dataclass(frozen=True)
class MergedPdfSource:
    payload: bytes = b""
    filename: str = ""
    staged_refs: tuple[tuple[str, str], ...] = ()
    error: str = ""


async def stage_pdf_parts(conversation_id: str, parts: list[tuple[str, bytes]]) -> list[dict[str, str]] | None:
    """Stage multipart source bytes and persist their references."""
    if mutation_admission.is_held(conversation_id):
        return None
    staged = []
    for filename, payload in parts:
        key = await asyncio.to_thread(scenario_library.stage_upload, payload)
        staged.append({"key": key, "file_name": filename})

    def stage(ctx: state_transaction.TxContext) -> None:
        ctx.state.staged_pdf_parts.extend(staged)

    async with locks.get_conversation_lock(conversation_id):
        try:
            await state_transaction.amutate(conversation_id, stage, reason="pdf_stage")
        except mutation_admission.MutationHeld:
            # Content-addressed bytes may be shared by another pending source.
            return None
    return staged


async def prepare_merged_pdf(conversation_id: str, refs: list[str]) -> MergedPdfSource:
    """Resolve multipart references and construct a PDF source without consuming it."""
    state = load_state(conversation_id)
    if refs == ["list"]:
        if not state.staged_pdf_parts:
            return MergedPdfSource(error="目前沒有暫存的 PDF part。")
        return MergedPdfSource(error="暫存 PDF：\n" + "\n".join(
            f"・{part['key'][:12]} {part['file_name']}" for part in state.staged_pdf_parts
        ))
    selected: list[dict[str, str]] = []
    for ref in refs:
        matches = [part for part in state.staged_pdf_parts if part["key"].startswith(ref) or part["file_name"] == ref]
        if len(matches) != 1:
            return MergedPdfSource(error=f"暫存 ID／檔名無法唯一對應：{ref}。先用 /coc scenario merge list 查看。")
        if matches[0] not in selected:
            selected.append(matches[0])
    if len(selected) < 2:
        return MergedPdfSource(error="至少要指定兩個 PDF part 才能合併。")
    try:
        payloads = [scenario_library.read_staged_upload(part["key"]) for part in selected]
    except FileNotFoundError:
        return MergedPdfSource(error="其中一個暫存 PDF 已不存在，請重新上傳。")
    from app.pdf_loader import combine_pdfs
    merged = await asyncio.to_thread(combine_pdfs, payloads)
    return MergedPdfSource(
        payload=merged, filename=f"{selected[0]['file_name'].rsplit('.', 1)[0]}_merged.pdf",
        staged_refs=tuple((part["key"], part["file_name"]) for part in selected),
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


def _confirmation_for_result(result: scenario_lifecycle.LifecycleResult, *, choice: bool = False) -> str:
    confirmation = _pdf_upload_confirmation_text(
        result.title, result.text, list(result.low_text_pages), result.truncated,
        result.page_maps or {}, result.indexes or {"npcs": [], "locations": []},
        result.pregen_count, result.artifact_notice,
    )
    variant = f"\n{result.variant_notice}" if result.variant_notice else ""
    image = "\n頁面圖片快取刷新失敗；劇本已啟用，請聯絡 KP 檢查圖片。" if not result.image_refreshed else ""
    stale = "\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。" if result.stale_cards else ""
    return confirmation + (stale + variant + image if choice else variant + image + stale)


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
    expected_timeline: str | None = None,
) -> bool:
    """`reply` acknowledges the upload and `push` delivers the extracted result
    after the potentially long vision/OCR pass. Discord can pass the same
    callback for both; the boolean result indicates whether a new scenario was
    accepted or is waiting for a user choice.

    If a scenario is already running, the lifecycle stores a pending choice
    rather than guessing whether the source is new or a correction. An adapter
    posts the actual buttons — see
    app/discord_transport/controls.py's post_pdf_upload_buttons, the same diff-and-post
    pattern as pending_checks/pending_luck_decisions). Guessing wrong here is
    worse than one extra click: a wrongly-preserved position could point at a
    room that doesn't exist in the new scenario's map at all. A
    conversation's very first upload has no existing scenario to be
    ambiguous against, so it always applies immediately with no button."""
    if not file_name.lower().endswith(".pdf"):
        await reply("目前只支援上傳 PDF 劇本檔案喔。")
        return False

    # Checked before any of the expensive extraction work below — a second PDF
    # landing while an earlier pending_pdf_upload
    # choice is still unresolved would otherwise silently overwrite it, and
    # whichever button the GM clicks afterward (still labelled for the FIRST
    # upload — buttons carry no upload-specific id) would end up applying the
    # SECOND upload's content instead, which is especially bad for "全新劇本"
    # (wipes map position, resets the LLM conversation thread).
    admission = await scenario_lifecycle.admit_submission(
        conversation_id, source_format="pdf", skip_similarity=skip_similarity,
        expected_revision=expected_revision,
    )
    if admission.outcome == "stale":
        await reply("遊戲狀態已更新，請重新開啟 Help 操作。")
        return False
    if admission.reason == "pending_pregen_luck":
        await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本 PDF。")
        return False
    if admission.reason == "pending_choice":
        await reply(
            f"上一次上傳的《{admission.title}》還沒選擇「全新劇本」"
            "還是「修正目前劇本」，請先點上一則訊息的按鈕選完，再上傳這份新的 PDF——不然這份新的"
            "會蓋掉還沒處理的那份，之後點到舊按鈕會套用到錯的內容。"
        )
        return False

    if admission.reason == "similar_pending":
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
            staged = await scenario_lifecycle.stage_similar_pdf(
                conversation_id, pdf_bytes, file_name, preview_title, matches,
                expected_revision=expected_revision,
            )
            if staged.outcome == "stale":
                await reply("遊戲狀態已更新，請重新開啟 Help 操作。")
                return False
            if staged.reason == "pending_pregen_luck":
                await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本 PDF。")
                return False
            if staged.reason == "pending_choice":
                await reply(
                    f"上一次上傳的《{staged.title}》還沒選擇「全新劇本」"
                    "還是「修正目前劇本」，請先點上一則訊息的按鈕選完，再上傳這份新的 PDF——不然這份新的"
                    "會蓋掉還沒處理的那份，之後點到舊按鈕會套用到錯的內容。"
                )
                return False
            if staged.outcome == "rejected":
                await reply("已有一份相似 PDF 等待處理，請先用 /coc scenario reparse 或 /coc scenario cancel。")
                return False
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
    # reconcile against it. The lifecycle folds this result into the
    # persisted candidate pool without regard to upload order.
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
    result = await scenario_lifecycle.submit_published_scenario(
        conversation_id, scenario_id, source_format="pdf",
        low_text_pages=low_text_pages,
        truncated=truncated, expected_revision=expected_revision,
        expected_timeline=expected_timeline,
    )
    if result.outcome == "stale":
        await push("遊戲狀態已更新，這份 PDF 沒有套用；請重新開啟 Help 操作。")
        return False
    if result.reason == "pending_pregen_luck":
        await push("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本 PDF。")
        return False
    if result.outcome == "raced":
        await push(
            f"這份《{title}》來得比較慢——另一份幾乎同時上傳的 PDF 先卡進待確認狀態了，請先處理完"
            "上一則訊息的選擇，再重新上傳這份。"
        )
        return False
    if result.outcome == "pending":
        await push(
            f"這個群組目前正在跑《{result.previous_title}》。新上傳的《{title}》"
            "是要開始一個全新的劇本，還是修正/補完目前這份劇本？請點下面的按鈕選擇——"
            "選錯的代價不小（位置可能對到新劇本裡不存在的房間），拿不準的話選「修正目前劇本」比較安全。"
        )
        return True
    await push(_confirmation_for_result(result))
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

    admission = await scenario_lifecycle.admit_submission(conversation_id, source_format="markdown")
    if admission.reason == "pending_pregen_luck":
        await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本檔案。")
        return False
    if admission.reason == "pending_choice":
        await reply(
            f"上一次上傳的《{admission.title}》還沒選擇「全新劇本」"
            "還是「修正目前劇本」，請先完成上一份劇本的選擇，再上傳新的 Markdown。"
        )
        return False
    if admission.reason == "similar_pending":
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
    result = await scenario_lifecycle.submit_published_scenario(
        conversation_id, scenario_id, source_format="markdown",
    )
    if result.reason == "pending_pregen_luck":
        await push("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再處理新的劇本檔案。")
        return False
    if result.outcome == "raced":
        await push(
            f"這份《{title}》完成得比較慢——另一份劇本先進入待確認狀態了。"
            "請先處理上一份，再重新上傳這份 Markdown。"
        )
        return False
    if result.outcome == "pending":
        await push(
            f"這個群組目前正在跑《{result.previous_title}》。新上傳的《{title}》"
            "是要開始一個全新的劇本，還是修正/補完目前這份劇本？請點下面的按鈕選擇。"
        )
        return True
    await push(_confirmation_for_result(result))
    return True


async def apply_pdf_upload_choice(
    conversation_id: str, choice: PdfChoice, *,
    authorized: Callable[[GroupState], bool] | None = None,
    unauthorized_message: str = "",
) -> str:
    """Resolve a saved scenario choice with lifecycle-owned locking and commit."""
    result = await scenario_lifecycle.resolve_pending_submission(
        conversation_id, choice, authorized=authorized,
    )
    if result.reason == "unauthorized":
        return unauthorized_message
    if result.reason == "pending_absent":
        return "這個上傳選擇已經處理過了，或已經過期失效，請重新上傳劇本檔案。"
    if result.reason == "library_id_absent":
        return "這個待處理上傳缺少劇本庫資料，請重新上傳劇本檔案。"
    if result.reason == "library_missing":
        return "這個待處理劇本庫項目已不存在，請重新上傳劇本檔案。"
    return _confirmation_for_result(result, choice=True)


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


@mutation_admission.guard_async_entry
async def handle_page_repair_upload(
    conversation_id: str,
    reply: Reply,
    file_text: str,
    file_name: str,
) -> None:
    """Overwrite whole pages of the loaded scenario text from a ``repair_``-prefixed .md attachment.

    Like a role card it belongs to the conversation and the scenario, not to the library entry: the pages are saved
    (repositories/page_repairs) and laid over the library text whenever the scenario is loaded again, and uploading the
    same pages again simply overwrites them. The page format is in scenario_page_repair.
    """
    try:
        pages = scenario_page_repair.parse_pages(file_text)
    except scenario_page_repair.PageRepairError as exc:
        await reply(f"「{file_name}」沒有套用：{exc}")
        return
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if not state.scenario_text.strip():
            await reply("目前沒有載入劇本，請先上傳劇本再上傳頁面修復檔。")
            return
        replacement_block = resource_bridge.guard_replacement(state)
        if replacement_block:
            await reply(replacement_block)
            return
        present = scenario_page_repair.present_pages(state.scenario_text)
        total = max(present, default=0)
        if state.scenario_library_id:  # a loaded chapter window holds only some of the scenario's pages
            try:
                total = max(total, int(scenario_library.source_manifest(state.scenario_library_id).get("page_count") or 0))
            except (OSError, ValueError):
                pass
        beyond = sorted(page for page in pages if page > total)
        if beyond:
            await reply(f"「{file_name}」沒有套用：目前劇本只有 {total} 頁，沒有第 {'、'.join(map(str, beyond))} 頁。")
            return
        try:
            repaired = scenario_page_repair.apply_pages(state.scenario_text, pages)
        except scenario_page_repair.PageRepairError as exc:
            await reply(f"「{file_name}」沒有套用：{exc}")
            return
        later = sorted(page for page in pages if page not in present)  # saved now, laid over when that chapter loads
        if repaired == state.scenario_text and not later:
            await reply("這些頁面的內容已經與目前劇本相同，沒有變動。")
            return
        state.scenario_text = repaired
        scenario_id, source_hash = state.scenario_library_id, state.active_scenario_source_hash

        def save_pages(conn):
            if scenario_id:  # a legacy entry without a content hash is bound by the empty hash
                page_repairs.save(conn, conversation_id, scenario_id, source_hash, pages)
        state_transaction.commit_snapshot(state, mutate_tx=save_pages)
    now = "、".join(str(page) for page in sorted(set(pages) & present))
    note = f"已替換第 {now} 頁，" if now else ""
    if later:
        note += f"第 {'、'.join(map(str, later))} 頁不在目前載入的章節裡，已先存下，載入到那一頁時會套用，"
    saved = "之後重新載入這份劇本、或 /coc newgame 後再上傳同一份劇本，也都會套用。" if scenario_id else "這份劇本不在劇本庫裡，修復只套用在目前這場遊戲。"
    await reply(note + "其餘頁面沒有變動，遊戲進度不受影響。" + saved)
