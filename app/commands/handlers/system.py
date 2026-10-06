from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal

from app import (
    character_matcher,
    checkpoints,
    observability,
    prompt_builder,
    scenario_authoring,
    scenario_index,
    scenario_library,
    scenario_rag,  # noqa: F401 - retained for existing command integration mocks,
    scenario_source_authoring,
    scenario_templates,
    scene_digest,
    spoiler_policy,
)
from app.commands import permissions
from app.commands.types import (
    FormatMention,
    PdfChoice,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
)
from app.config import IMPORT_DIR
from app.keeper_tools import resource_bridge
from app.models import GroupState
from app.repositories import manual_pregens, state_transaction
from app.repositories.group_state import (
    load_state,
    scenario_users,
)
from app.services import (
    correction_adjudication,
    game_opening,
    mutation_admission,
    scenario_lifecycle,
)
from app.services.character_service import (
    OpeningReadiness,
    build_readiness_roster,
    set_away_state,
)
from app.services.post_turn import run_post_turn_maintenance_after_output
from app.services.scenario_ingestion import (
    apply_pdf_upload_choice,
    handle_pdf_upload,
    prepare_merged_pdf,
)


async def _handle_local_import(
    conversation_id: str, user_id: str, reply: Reply, parts: list[str],
    expected_revision: int | None = None,
) -> None:
    state = load_state(conversation_id)
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("匯入伺服器上的 PDF"))
        return
    filename_index = 3 if len(parts) > 1 and parts[1] == "scenario" else 2
    if len(parts) <= filename_index:
        await reply("用法：/coc scenario import 檔名.pdf")
        return
    try:
        pdf_path = scenario_library.safe_import_path(IMPORT_DIR, " ".join(parts[filename_index:]))
        pdf_bytes = pdf_path.read_bytes()
    except (FileNotFoundError, ValueError, OSError):
        await reply("找不到允許匯入的 PDF；只能使用 IMPORT_DIR 內的檔案名稱，不能帶路徑。")
        return
    await reply(f"已讀取伺服器檔案《{pdf_path.name}》，開始解析...")
    await handle_pdf_upload(conversation_id, reply, reply, pdf_bytes, pdf_path.name,
                            expected_revision=expected_revision)


async def _handle_staged_merge(
    conversation_id: str, user_id: str, reply: Reply, parts: list[str],
    expected_revision: int | None = None,
) -> None:
    state = load_state(conversation_id)
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("合併 PDF"))
        return
    refs = parts[3:]
    if not refs:
        await reply("用法：/coc scenario merge 暫存ID1 暫存ID2 ...")
        return
    source = await prepare_merged_pdf(conversation_id, refs)
    if source.error:
        await reply(source.error)
        return
    await scenario_lifecycle.submit_merged_pdf(
        conversation_id, source.payload, source.filename, source.staged_refs,
        handle_pdf_upload, reply, reply, expected_revision=expected_revision,
    )


async def _handle_newgame(conversation_id: str, reply: Reply) -> None:
    """Reset the conversation to a fresh game on a new timeline.

    The reset is a state replacement, so it must not be refused for an
    unrelated revision bump (a background maintenance write). It instead
    re-checks the unsettled-combat guard on the latest state and only re-reads
    the scenario hash when the active scenario changed meanwhile.
    """
    for _attempt in range(3):
        previous = load_state(conversation_id)
        replacement_block = resource_bridge.guard_replacement(previous)
        if replacement_block:
            await reply(replacement_block)
            return
        previous_id = previous.scenario_library_id or None
        previous_hash = ""
        if previous_id:
            try:
                previous_hash = scenario_library.load_context(previous_id)["manifest"].get("content_hash", "")
            except (FileNotFoundError, ValueError):
                pass
        outcome = await state_transaction.amutate(
            conversation_id, _newgame_mutation(conversation_id, previous_id, previous_hash),
            reason="newgame",
        )
        if outcome.ok:
            await reply("已重置這個群組的遊戲狀態。請上傳劇本 PDF，或上傳檔名以 scenario 開頭的 .md 劇本開始新的冒險。")
            return
        if outcome.reason != "scenario_changed":
            break
    latest_block = resource_bridge.guard_replacement(load_state(conversation_id))
    await reply(latest_block or "遊戲狀態剛被其他操作更新，這次指令沒有套用，請再試一次。")


def _newgame_mutation(
    conversation_id: str, previous_id: str | None, previous_hash: str,
) -> Callable[[state_transaction.TxContext], None]:
    def reset(ctx: state_transaction.TxContext) -> None:
        latest = ctx.state
        if resource_bridge.guard_replacement(latest):
            ctx.reject("combat_unsettled")
        if (latest.scenario_library_id or None) != previous_id:
            ctx.reject("scenario_changed")
        manual_pregens.capture_legacy(
            ctx.conn, conversation_id, previous_id, latest.pregens, previous_hash,
        )
        ctx.replace_state(GroupState(group_id=conversation_id))

    return reset


@dataclass(frozen=True)
class _Call:
    """What every /coc system subcommand receives: the arguments of ``handle_system_command``."""

    conversation_id: str
    user_id: str
    reply: Reply
    send_dm: SendDM
    send_image: SendImage
    send_dm_image: SendDMImage
    parts: list[str]
    format_mention: FormatMention
    expected_revision: int | None
    server: permissions.ServerFacts
    opening_mutation_scope: Callable[[], AbstractAsyncContextManager[Any]] | None
    sub: str


async def _scenario_source(call: _Call, state: GroupState) -> None:
    """/coc scenario source export | import | status (KP only)."""
    user_id, reply, send_dm, parts = call.user_id, call.reply, call.send_dm, call.parts
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("管理英文來源"))
        return
    if len(parts) < 5:
        await reply("用法：/coc scenario source export|import|status 劇本ID [檔名或匯出ID]")
        return
    operation, scenario_id = parts[3].casefold(), parts[4]
    try:
        ready_id = ""
        if operation == "export" and len(parts) == 5:
            path = await asyncio.to_thread(scenario_source_authoring.export_source, scenario_id)
            await send_dm(user_id, scenario_source_authoring.export_message(scenario_id, path))
        elif operation == "status" and len(parts) in (5, 6):
            infos = await asyncio.to_thread(scenario_source_authoring.status, scenario_id,
                                           parts[5] if len(parts) == 6 else "")
            await send_dm(user_id, "\n\n".join(scenario_source_authoring.progress_message(x) for x in infos)
                          or "尚未匯出英文整備工作檔。")
            _, problems = await asyncio.to_thread(scenario_source_authoring.inspect_results, scenario_id)
            if problems:
                await send_dm(user_id, "未列入英文匯入選單的檔案：\n" + "\n".join(problems))
            if len(infos) == 1:
                ready_id = infos[0]["published_id"]
        elif operation == "import" and len(parts) >= 6:
            arguments = parts[5:]
            expected = ""
            if len(arguments) >= 3 and arguments[-2] == "--sha256":
                expected = arguments[-1]
                arguments = arguments[:-2]
            info = await asyncio.to_thread(scenario_source_authoring.import_source, scenario_id,
                                           " ".join(arguments), imported_by=user_id, expected_sha256=expected)
            await send_dm(user_id, scenario_source_authoring.progress_message(info))
            ready_id = info["published_id"]
        else:
            await reply("用法：/coc scenario source export|import|status 劇本ID [檔名或匯出ID]")
            return
        if ready_id:
            await reply(scenario_source_authoring.SourceReadyMessage(ready_id, user_id))
        else:
            await reply("英文整備已處理，檔案位置、提示詞或進度已私訊 KP；目前遊戲版本不變。")
    except (OSError, ValueError, KeyError) as exc:
        try:
            await send_dm(user_id, f"英文來源無法處理：{exc}\n請將疑點交回外部 AI，連同原 PDF 修正後再匯入。")
        except Exception:
            logging.getLogger(__name__).exception("English preparation diagnostic DM failed")
            await reply("英文整備私訊未送達，請開啟私訊後重試；詳細資料不會公開。")
        else:
            await reply("英文整備未完成，詳細原因已私訊 KP；目前遊戲版本不變。")
    except Exception:
        # Discord DM failures must never fall back to posting source data.
        await reply("英文整備未完成或私訊未送達，請開啟私訊後重試或查看伺服器紀錄。")
        logging.getLogger(__name__).exception("English preparation or private delivery failed")
    return


async def _scenario_template(call: _Call, state: GroupState) -> None:
    """/coc scenario template status | export | preview | approve | import."""
    (conversation_id, user_id, reply, send_dm, parts) = (
        call.conversation_id, call.user_id, call.reply, call.send_dm, call.parts,
    )
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("管理中文劇本模板"))
        return
    if len(parts) < 5:
        await reply("用法：/coc scenario template status|export|preview|approve|import 劇本ID [版本或檔名]")
        return
    operation, scenario_id = parts[3].casefold(), parts[4]
    try:
        if operation == "export":
            exported = await asyncio.to_thread(scenario_templates.export_template, scenario_id)
            await send_dm(user_id, scenario_templates.export_message(scenario_id, exported))
            await reply("模板已匯出；檔案位置與可複製提示詞已私訊 KP。未呼叫翻譯 API。")
        elif operation == "status":
            info = scenario_templates.status(scenario_id)
            variants = info["variants"]
            lines = ["中文模板：外部準備／匯入校對（不執行翻譯）"]
            for variant in variants:
                lines.append(f"・{variant['variant_id']}：{variant['review_status']}"
                             + ("（來源有效）" if variant["current"] else "（來源已變更）")
                             + f"，{variant.get('record_count', 0)} 筆，"
                             f"{len(variant.get('issues', []))} 個待核對項目")
            notice = scenario_templates.preference_notice(conversation_id, scenario_id)
            if notice:
                lines.append(notice)
            await reply("\n".join(lines))
        elif operation == "preview" and len(parts) >= 6:
            await send_dm(user_id, scenario_templates.preview(scenario_id, parts[5], page=int(parts[6]) if len(parts) > 6 else 1))
            await reply("中文模板預覽已私訊給 KP。")
        elif operation == "approve" and len(parts) >= 6:
            scenario_templates.approve(scenario_id, parts[5], reviewer_id=user_id)
            await reply(f"中文模板 {parts[5]} 已通過校對，可用 /coc scenario use {scenario_id} {parts[5]} 啟用。")
        elif operation == "import" and len(parts) >= 6:
            variant_id = await asyncio.to_thread(scenario_templates.import_markdown, scenario_id, " ".join(parts[5:]))
            try:
                progress = await asyncio.to_thread(scenario_templates.import_progress, scenario_id, " ".join(parts[5:]))
            except (OSError, ValueError):
                progress = "匯入已保存；本次無法讀取進度報告，可重新匯入相同成果查看。"
            if progress:
                await send_dm(user_id, progress)
            if variant_id.startswith("draft:"):
                await send_dm(user_id, f"已保存部分翻譯草稿 {variant_id}；請匯入其餘單元。尚未建立可啟用版本。")
                await reply("部分翻譯草稿已保存，進度已私訊 KP；目前遊玩版本不變。")
            else:
                await reply(f"已匯入中文模板 {variant_id}；請先 status、preview 與 approve。")
        else:
            await reply("用法：/coc scenario template status|export|preview|approve|import 劇本ID [版本或檔名]")
    except scenario_authoring.Diagnostics as exc:
        await send_dm(user_id, f"{exc}\n報告：{exc.report_path}\n請將報告與原匯出工作檔交回網頁 AI 修正。")
        await reply("中文模板校對未通過，詳細報告已私訊 KP；目前遊玩版本不變。")
    except (FileNotFoundError, ValueError, KeyError) as exc:
        await reply(f"中文模板無法處理：{exc}")
    return


async def _scenario_cards(call: _Call, state: GroupState) -> None:
    """/coc scenario cards ..."""
    conversation_id, user_id, reply, parts = call.conversation_id, call.user_id, call.reply, call.parts
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("管理手動角色卡"))
        return
    if len(parts) < 5 or parts[3] not in {"list", "delete"}:
        await reply("用法：/coc scenario cards list 劇本ID | delete 劇本ID 資產ID")
        return
    scenario_id = parts[4]
    if parts[3] == "list":
        assets = manual_pregens.list_assets(conversation_id, scenario_id)
        lines = [f"劇本 {scenario_id} 的手動角色卡："]
        lines.extend(
            f"・{item['asset_id']} {item['pregen'].get('name') or '未命名'} "
            f"({item.get('filename', '舊版合併卡')})"
            for item in assets
        )
        await reply("\n".join(lines) if assets else "這個劇本沒有保存的手動角色卡。")
        return
    if len(parts) < 6:
        await reply("用法：/coc scenario cards delete 劇本ID 資產ID")
        return
    asset_id = parts[5]
    assets = manual_pregens.list_assets(conversation_id, scenario_id)
    target = next((item for item in assets if item["asset_id"] == asset_id), None)
    if target is None:
        await reply("找不到這張手動角色卡資產。")
        return
    if state.scenario_library_id == scenario_id and any(
        p.get("claimed_by") and character_matcher.is_same_character(p, target["pregen"])
        for p in state.pregens
    ):
        await reply("這張角色卡已被認領；請先結束或重開新局，再刪除持久資料。")
        return
    context = None
    if state.scenario_library_id == scenario_id:
        try:
            context = scenario_library.load_context(scenario_id)
        except (FileNotFoundError, ValueError):
            pass
    def remove_card(conn):
        manual_pregens.delete_asset(conn, conversation_id, scenario_id, asset_id)
        if context:
            state.pregens, _ = manual_pregens.install_pool(
                conn, conversation_id, scenario_id, context,
                claimed=[p for p in state.pregens if p.get("claimed_by")],
            )
    state_transaction.commit_snapshot(state, mutate_tx=remove_card)
    await reply(f"已刪除手動角色卡資產 {asset_id}。")
    return


async def _scenario_import(call: _Call, state: GroupState) -> None:
    """/coc scenario import 檔名.pdf"""
    (conversation_id, user_id, reply, parts, expected_revision) = (
        call.conversation_id, call.user_id, call.reply, call.parts, call.expected_revision,
    )
    await _handle_local_import(conversation_id, user_id, reply, parts, expected_revision)


async def _scenario_merge(call: _Call, state: GroupState) -> None:
    """/coc scenario merge ID..."""
    (conversation_id, user_id, reply, parts, expected_revision) = (
        call.conversation_id, call.user_id, call.reply, call.parts, call.expected_revision,
    )
    await _handle_staged_merge(conversation_id, user_id, reply, parts, expected_revision)


async def _scenario_list(call: _Call, state: GroupState) -> None:
    """/coc scenario list"""
    reply = call.reply
    entries = scenario_library.list_scenarios()
    if not entries:
        await reply("劇本庫目前是空的，請先上傳 PDF，或上傳檔名以 scenario 開頭的 .md 劇本。")
        return
    lines = ["劇本庫："]
    for item in entries:
        marker = "（目前使用）" if item["id"] == state.scenario_library_id else ""
        chapters = "、".join(c["title"] for c in item.get("chapters", []) if c.get("kind") == "playable")
        parent = item.get("source_review", {}).get("parent_scenario_id", "")
        lines.append(f"・{item['id']}《{item.get('title', '')}》{marker}" + (f"\n  章節：{chapters}" if chapters else "")
                     + (f"\n  原來源：{parent}" if parent else ""))
    await reply("\n".join(lines))
    return


async def _scenario_reparse(call: _Call, state: GroupState) -> None:
    """/coc scenario reparse"""
    (conversation_id, user_id, reply, expected_revision) = (
        call.conversation_id, call.user_id, call.reply, call.expected_revision,
    )
    if not permissions.may_manage_scenario_lifecycle(state, user_id):
        await reply(permissions.kp_only("重新解析劇本"))
        return
    scenario_result = await scenario_lifecycle.reparse_pending_scenario(
        conversation_id, handle_pdf_upload, reply, reply,
        authorized=lambda current: permissions.may_manage_scenario_lifecycle(current, user_id),
        expected_revision=expected_revision,
    )
    messages = {
        "revision_changed": "遊戲狀態已更新，請重新開啟 Help 操作。",
        "unauthorized": permissions.kp_only("重新解析劇本"),
        "pending_pregen_luck": "目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再重新解析劇本。",
        "pending_absent": "沒有等待重新解析的 PDF。",
        "staged_missing": "暫存 PDF 已不存在，請重新上傳。",
    }
    if scenario_result.reason == "combat_unsettled":
        await reply(scenario_result.title)
    elif scenario_result.reason in messages:
        await reply(messages[scenario_result.reason])
    return


async def _scenario_cancel(call: _Call, state: GroupState) -> None:
    """/coc scenario cancel"""
    conversation_id, user_id, reply = call.conversation_id, call.user_id, call.reply
    if not permissions.may_manage_scenario_lifecycle(state, user_id):
        await reply(permissions.kp_only("取消劇本處理"))
        return
    scenario_result = await scenario_lifecycle.cancel_pending_reparse(
        conversation_id,
        authorized=lambda current: permissions.may_manage_scenario_lifecycle(current, user_id),
    )
    if scenario_result.reason == "unauthorized":
        await reply(permissions.kp_only("取消劇本處理"))
    elif scenario_result.reason == "pending_absent":
        await reply("沒有等待處理的 PDF。")
    else:
        await reply("已放棄本次上傳，既有劇本不受影響。")
    return


async def _scenario_use(call: _Call, state: GroupState) -> None:
    """/coc scenario use 劇本ID [模板版本]"""
    (conversation_id, user_id, reply, parts, expected_revision) = (
        call.conversation_id, call.user_id, call.reply, call.parts, call.expected_revision,
    )
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("選擇劇本"))
        return
    scenario_result = await scenario_lifecycle.activate_existing_scenario(
        conversation_id, parts[3] if len(parts) > 3 else "",
        authorized=lambda current: permissions.is_kp(current, user_id),
        variant_id=parts[4] if len(parts) > 4 else None,
        expected_revision=expected_revision,
    )
    messages = {
        "revision_changed": "遊戲狀態已更新，請重新開啟 Help 操作。",
        "unauthorized": permissions.kp_only("選擇劇本"),
        "pending_pregen_luck": "目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再切換劇本。",
        "pending_submission": "目前仍有待處理的劇本上傳，請先完成或取消該流程後再切換劇本。",
        "identifier_missing": "用法：/coc scenario use 劇本ID（先用 /coc scenario list 查看）",
        "library_missing": "找不到可使用的劇本 ID。請先用 /coc scenario list 查看。",
    }
    if scenario_result.reason == "combat_unsettled":
        await reply(scenario_result.detail)
        return
    if scenario_result.reason == "variant_invalid":
        await reply(f"中文模板無法啟用：{scenario_result.detail}")
        return
    if scenario_result.reason in messages:
        await reply(messages[scenario_result.reason])
        return
    note = "\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。" if scenario_result.stale_cards else ""
    image_notice = "\n頁面圖片快取刷新失敗；劇本已啟用，請聯絡 KP 檢查圖片。" if not scenario_result.image_refreshed else ""
    await reply(f"KP 已選擇《{scenario_result.title}》；目前 Context：{'、'.join(scenario_result.active_chapters)}。{note}"
                + (f"\n{scenario_result.variant_notice}" if scenario_result.variant_notice else "")
                + (f"\n\n{scenario_result.artifact_notice}" if scenario_result.artifact_notice else "") + image_notice)
    return


async def _scenario_clean(call: _Call, state: GroupState) -> None:
    """/coc scenario clean 劇本ID"""
    user_id, reply, parts = call.user_id, call.reply, call.parts
    if not permissions.may_manage_scenario_lifecycle(state, user_id):
        await reply(permissions.kp_only("清理劇本庫"))
        return
    if len(parts) < 4:
        await reply("用法：/coc scenario clean 劇本ID")
        return
    users = scenario_users(parts[3])
    if users:
        await reply("此劇本仍被使用中，請先讓所有使用中的群組切換到其他劇本後再清除。")
        return
    try:
        scenario_library.clean_scenario(parts[3])
    except FileNotFoundError:
        await reply("找不到該劇本 ID。")
        return
    scenario_templates.clean_scenario(parts[3])
    await reply("已清除劇本庫項目。")
    return


_SCENARIO_ACTIONS: dict[str, Callable[[_Call, GroupState], Awaitable[None]]] = {
    "source": _scenario_source,
    "template": _scenario_template,
    "cards": _scenario_cards,
    "import": _scenario_import,
    "merge": _scenario_merge,
    "list": _scenario_list,
    "reparse": _scenario_reparse,
    "cancel": _scenario_cancel,
    "use": _scenario_use,
    "clean": _scenario_clean,
}


async def _checkpoint_command(call: _Call) -> None:
    """/coc checkpoint | checkpoints | rollback (KP only)."""
    conversation_id, user_id, reply, parts, sub = call.conversation_id, call.user_id, call.reply, call.parts, call.sub
    state = load_state(conversation_id)
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("操作回溯節點"))
        return
    if sub == "checkpoint":
        if len(parts) > 2 and parts[2].casefold() == "clean":
            if len(parts) < 4:
                await reply("用法：/coc checkpoint clean <ID 或唯一名稱>")
                return
            try:
                checkpoints.clean_checkpoint(conversation_id, " ".join(parts[3:]))
            except KeyError:
                await reply("找不到這個回溯節點。")
                return
            except ValueError as exc:
                await reply(f"無法清除回溯節點：{exc}")
                return
            await reply("已清除回溯節點。")
            return
        label = " ".join(parts[2:]).strip()
        checkpoint_entry = checkpoints.create_checkpoint(state, label=label, created_by=user_id)
        await reply(f"已建立回溯節點：{checkpoint_entry['checkpoint_id']}（{checkpoint_entry['label']}）。")
        return
    if sub == "checkpoints":
        entries = checkpoints.list_checkpoints(conversation_id)
        if not entries:
            await reply("目前沒有回溯節點。")
            return
        lines = ["回溯節點："]
        for entry in entries:
            lines.append(
                f"・{entry['checkpoint_id']}｜{entry.get('label', '')}｜"
                f"{entry.get('reason', 'manual')}｜{entry.get('created_at', '')}"
            )
        await reply("\n".join(lines))
        return
    if len(parts) < 3:
        await reply("用法：/coc rollback <節點 ID 或唯一名稱>")
        return
    try:
        _restored, checkpoint, pre = checkpoints.rollback(
            conversation_id, " ".join(parts[2:]), actor_id=user_id
        )
    except KeyError:
        await reply("找不到這個回溯節點。")
        return
    except ValueError as exc:
        await reply(f"無法回溯：{exc}")
        return
    await reply(
        f"已回溯到「{checkpoint.get('label', checkpoint['checkpoint_id'])}」；"
        f"本次操作前的狀態已保存為 {pre['checkpoint_id']}。"
    )
    return


async def _digest_command(call: _Call) -> None:
    """/coc digest | digests (KP only)."""
    conversation_id, user_id, reply, parts, sub = call.conversation_id, call.user_id, call.reply, call.parts, call.sub
    state = load_state(conversation_id)
    if not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("查看場景摘要"))
        return
    if sub == "digests":
        entries = scene_digest.list_digests(conversation_id)
        if not entries:
            await reply("目前沒有場景摘要。")
            return
        await reply("\n".join(
            f"・{entry['digest_id']}｜{entry.get('scene_label', '')}｜{entry.get('updated_at', '')}"
            for entry in entries
        ))
        return
    identifier = parts[2] if len(parts) > 2 else ""
    if identifier.casefold() == "clean":
        if len(parts) < 4:
            await reply("用法：/coc digest clean <ID>")
            return
        try:
            scene_digest.clean_digest(conversation_id, parts[3])
        except KeyError:
            await reply("找不到這筆場景摘要。")
            return
        await reply("已清除場景摘要。")
        return
    try:
        digest_entry: dict[str, Any] | None = scene_digest.latest_digest(conversation_id, state.timeline_id)
        if identifier:
            digest_entry = scene_digest.get_digest(conversation_id, identifier)
        if digest_entry is None:
            raise KeyError(identifier)
    except KeyError:
        await reply("找不到這筆場景摘要。")
        return
    # §7.3 mechanism #5: with spoiler protection off, show the whole
    # entry (including the KP-only `private` block) rather than just the
    # public-facing formatter's slice.
    if spoiler_policy.is_spoiler_protection_enabled():
        await reply(str(digest_entry.get("public", {})))
    else:
        await reply(str(digest_entry))
    return


async def _scenario_command(call: _Call) -> None:
    """/coc scenario ...: dispatches on the action word."""
    conversation_id, reply, parts = call.conversation_id, call.reply, call.parts
    action = parts[2].casefold() if len(parts) > 2 else "list"
    state = load_state(conversation_id)
    handler = _SCENARIO_ACTIONS.get(action)
    if handler is not None:
        await handler(call, state)
        return
    await reply("用法：/coc scenario list | use 劇本ID [模板版本] | template status|export|preview|approve|import | clean 劇本ID | reparse | cancel | import 檔名.pdf | merge ID...")


async def _import_command(call: _Call) -> None:
    """/coc import 檔名.pdf"""
    conversation_id, user_id, reply, parts = call.conversation_id, call.user_id, call.reply, call.parts
    await _handle_local_import(conversation_id, user_id, reply, parts)


async def _newgame_command(call: _Call) -> None:
    """/coc newgame"""
    conversation_id, reply = call.conversation_id, call.reply
    await _handle_newgame(conversation_id, reply)


async def _pdf_command(call: _Call) -> None:
    """/coc pdf new | fix"""
    conversation_id, user_id, reply, parts = call.conversation_id, call.user_id, call.reply, call.parts
    state = load_state(conversation_id)
    if not permissions.may_manage_scenario_lifecycle(state, user_id):
        await reply(permissions.kp_only("處理劇本 PDF"))
        return
    choice_word = parts[2].casefold() if len(parts) > 2 else ""
    choices: dict[str, PdfChoice] = {"new": "new", "全新": "new", "全新劇本": "new",
                                    "fix": "fix", "修正": "fix", "修正目前劇本": "fix"}
    choice = choices.get(choice_word)
    if choice is None:
        await reply("用法：「/coc pdf new」開始全新劇本，或「/coc pdf fix」修正/補完目前這份劇本。")
        return
    await reply(await apply_pdf_upload_choice(
        conversation_id, choice,
        authorized=lambda current: permissions.may_manage_scenario_lifecycle(current, user_id),
        unauthorized_message=permissions.kp_only("處理劇本 PDF"),
    ))
    return


async def _kp_command(call: _Call) -> None:
    """/coc kp [quit | transfer | takeover]"""
    (conversation_id, user_id, reply, parts, format_mention, server) = (
        call.conversation_id, call.user_id, call.reply, call.parts, call.format_mention, call.server,
    )
    kp_action: str | None = parts[2].casefold() if len(parts) > 2 else None
    state = load_state(conversation_id)

    if kp_action == "quit":
        if state.kp_assistant_user_id != user_id:
            await reply("你目前不是這局的 KP 助手。")
            return
        state.kp_assistant_user_id = ""
        state.kp_ooc_log = []
        state_transaction.commit_snapshot(state)
        await reply("已解除 KP 助手身分，你現在回到未綁定角色的狀態。")
        # With the seat empty, the Keeper rules on what the KP left open.
        correction_adjudication.schedule(conversation_id, state, reply)
        return

    if kp_action in ("transfer", "takeover"):
        await _change_kp(
            state, "transfer" if kp_action == "transfer" else "takeover", user_id, parts[3] if len(parts) > 3 else None, reply, format_mention,
            too_many_args=len(parts) > 4, server=server,
        )
        return

    if kp_action is not None:
        await reply("用法：/coc kp、/coc kp quit、/coc kp transfer @成員、/coc kp takeover [@成員]")
        return

    if state.kp_assistant_user_id == user_id:
        await reply("你已經是這局的 KP 助手。")
        return
    if state.kp_assistant_user_id:
        await reply("這局已經有一位 KP 助手，不能同時登記第二位。")
        return
    blocker = permissions.kp_seat_blocker(state, user_id)
    if blocker:
        await reply(blocker)
        return

    state.kp_ooc_log = []
    state.kp_assistant_user_id = user_id
    state_transaction.commit_snapshot(state)
    await reply("已登記你為這局的 KP 助手。")
    return


async def _autoroll_command(call: _Call) -> None:
    """/coc autoroll on | off"""
    conversation_id, reply, parts = call.conversation_id, call.reply, call.parts
    state = load_state(conversation_id)
    action = parts[2].casefold() if len(parts) > 2 else "status"
    if action not in {"on", "off", "status", "狀態", "開", "關"} or len(parts) > 3:
        await reply("用法：/coc autoroll on|off（不帶參數可查看目前狀態）")
        return
    if action in {"status", "狀態"}:
        await reply(
            "目前自動擲骰：已開啟。新檢定會由 Keeper/system 立即處理。"
            if state.autoroll_checks
            else "目前自動擲骰：關閉（預設）。新檢定會等待玩家用 /coc check 或按鈕擲骰。"
        )
        return
    state.autoroll_checks = action in {"on", "開"}
    state_transaction.commit_snapshot(state)
    await reply(
        "已開啟自動擲骰；之後新建立的技能、攻擊、SAN、重傷 CON 檢定可由 Keeper/system 立即處理。"
        if state.autoroll_checks
        else "已關閉自動擲骰；之後新建立的角色檢定會等待玩家用 /coc check 或按鈕擲骰。"
    )
    return


async def _status_command(call: _Call) -> None:
    """/coc status"""
    conversation_id, reply = call.conversation_id, call.reply
    state = load_state(conversation_id)
    if not state.scenario_title:
        await reply("目前還沒有載入任何劇本，上傳 PDF 開始吧。")
        return
    lines = [f"劇本：《{state.scenario_title}》", f"狀態：{'進行中' if state.active else '已結束'}", ""]
    if state.characters:
        for committed in state.characters.values():
            c = resource_bridge.effective(state, committed)
            if resource_bridge.participating(state, committed):
                lines.append("【戰鬥暫定數值；尚未結算】")
            lines.append(f"・{c.name}（{c.occupation}）HP {c.hp}/{c.hp_max} SAN {c.san}/{c.san_max} MP {c.mp}/{c.mp_max}")
    else:
        lines.append("（尚無角色）")
    await reply("\n".join(lines))
    return


async def _end_command(call: _Call) -> None:
    """/coc end"""
    conversation_id, reply = call.conversation_id, call.reply
    state = load_state(conversation_id)
    replacement_block = resource_bridge.guard_replacement(state)
    if replacement_block:
        await reply(replacement_block)
        return
    state.active = False
    state.kp_assistant_user_id = ""
    state.kp_ooc_log = []
    state_transaction.commit_snapshot(state)
    await reply("遊戲已結束，遊戲紀錄與角色仍會保留；KP 助手身分也已解除。要開新的一局請用 /coc newgame。")
    return


async def _setpersona_command(call: _Call) -> None:
    """/coc setpersona"""
    conversation_id, reply, parts = call.conversation_id, call.reply, call.parts
    state = load_state(conversation_id)
    if len(parts) < 3:
        current = state.keeper_persona or f"（目前使用預設風格）\n{prompt_builder.DEFAULT_PERSONA}"
        await reply(
            "用法：/coc setpersona <描述守密人語氣風格的文字> → 設定這個群組專屬的守密人語氣\n"
            "/coc setpersona reset → 重設回預設的冷酷旁觀者風格\n\n"
            f"目前設定：\n{current}"
        )
        return
    if parts[2].casefold() == "reset" and len(parts) == 3:
        state.keeper_persona = ""
        state_transaction.commit_snapshot(state)
        await reply("已重設回預設的冷酷旁觀者語氣風格。")
        return
    persona_text = " ".join(parts[2:])
    state.keeper_persona = persona_text
    state_transaction.commit_snapshot(state)
    await reply(f"已設定這個群組的守密人語氣風格：\n{persona_text}\n\n（下一則訊息開始生效；重設回預設風格用 /coc setpersona reset）")
    return


async def _era_command(call: _Call) -> None:
    """/coc era"""
    conversation_id, reply, parts = call.conversation_id, call.reply, call.parts
    state = load_state(conversation_id)
    if len(parts) < 3:
        await reply(
            "用法：/coc era 1920 → 設定 1920 年代經典設定\n"
            "/coc era modern → 設定現代／當代設定\n\n"
            f"目前設定：{'1920 年代' if state.era == '1920s' else '現代／當代'}\n"
            "（影響角色卡上傳時，武器只寫泛稱、沒寫具體型號的情況下，自動補上的預設彈藥容量）"
        )
        return
    era_choice = parts[2].strip().lower()
    era_map = {"1920": "1920s", "1920s": "1920s", "modern": "modern"}
    if era_choice not in era_map:
        await reply("年代設定只接受「1920」或「modern」。")
        return
    state.era = era_map[era_choice]
    state_transaction.commit_snapshot(state)
    await reply(f"已設定這個群組的年代為：{'1920 年代' if state.era == '1920s' else '現代／當代'}。")
    return


async def _index_command(call: _Call) -> None:
    """/coc index"""
    conversation_id, user_id, reply = call.conversation_id, call.user_id, call.reply
    state = load_state(conversation_id)
    if not state.scenario_text:
        await reply("目前還沒有載入劇本，上傳 PDF 之後才能抽取 NPC／怪物與地點索引。")
        return
    index_data = await asyncio.to_thread(scenario_index.extract_scenario_index, state.scenario_text)
    # An unstable extraction that found fewer locations than the text's numbered headings must not replace the
    # previous index; the commit below is conditional on the loaded revision, so the check holds against newer state.
    underflow = scenario_index.location_index_underflow(
        state.scenario_text, index_data["locations"],
        previous_count=len(state.scenario_location_index), source="index_command",
    )
    if underflow is not None:
        expected, extracted = underflow
        kept = "已保留上一版索引。" if state.scenario_npc_index or state.scenario_location_index else "未寫入這份不完整索引。"
        await reply(
            f"⚠️ 索引重建結果不完整：劇本文字明確包含 LOCATION 1–{expected}，但本次只抽出 {extracted} 個地點；{kept}"
            "可以再執行一次 /coc index。"
        )
        return
    # The maps already name their locations; the underflow check above judged the text extraction alone.
    index_data["locations"] = scenario_index.merge_scene_map_locations(index_data["locations"], state.scene_maps)
    state.scenario_npc_index = index_data["npcs"]
    state.scenario_location_index = index_data["locations"]
    state_transaction.commit_snapshot(state)
    if not index_data["npcs"] and not index_data["locations"]:
        await reply("沒有從劇本裡抽出任何有明確數值的 NPC／怪物或地點條目。")
        return
    # §7.1: only the KP Assistant sees the full index
    # (HP/abilities); everyone else gets names only.
    is_privileged = permissions.is_kp(state, user_id)
    if spoiler_policy.is_spoiler_protection_enabled() and not is_privileged:
        safe_index = spoiler_policy.redact_public_scenario_index(index_data)
        lines = [f"已重新建立劇本索引：{len(index_data['npcs'])} 個 NPC／怪物、{len(index_data['locations'])} 個地點。"]
        for n in safe_index["npcs"]:
            lines.append(f"・{n.get('name') or '未知存在'}")
        await reply(
            "\n".join(lines)
            + "\n\n（詳細數值僅供 KP 助手查看，一般玩家只會看到已登場的名稱。）"
        )
        return
    lines = [f"已重新建立劇本索引：{len(index_data['npcs'])} 個 NPC／怪物、{len(index_data['locations'])} 個地點。"]
    for n in index_data["npcs"]:
        hp = n.get("hp")
        hp_note = f"HP {hp}" if isinstance(hp, (int, float)) else "（無 HP 數值）"
        lines.append(f"・{n.get('name') or '未命名'}：{hp_note}")
    await reply("\n".join(lines) + "\n\n之後守密人回覆時會直接參考這份索引，同一隻怪物/NPC 不會再前後數值不一致。這份索引現在上傳劇本 PDF 時就會自動建立，這個指令是手動重建（例如覺得抽取結果不準、或劇本內容之後有更新時再用）。")
    return


async def _away_command(call: _Call) -> None:
    """/coc away"""
    conversation_id, user_id, reply = call.conversation_id, call.user_id, call.reply
    result = await asyncio.to_thread(set_away_state, conversation_id, user_id, True)
    if result.error_text:
        await reply(result.error_text)
        return
    await reply(f"{result.character_name} 已標記為暫離，戰鬥中會自動跳過他的回合，直到輸入「/coc back」回來。")
    return


async def _back_command(call: _Call) -> None:
    """/coc back"""
    conversation_id, user_id, reply = call.conversation_id, call.user_id, call.reply
    result = await asyncio.to_thread(set_away_state, conversation_id, user_id, False)
    if result.error_text:
        await reply(result.error_text)
        return
    await reply(f"{result.character_name} 回來了，恢復正常參與。")
    return


async def _start_command(call: _Call) -> None:
    """/coc start"""
    (conversation_id, user_id, reply, send_dm, send_image, send_dm_image, format_mention, opening_mutation_scope) = (
        call.conversation_id, call.user_id, call.reply, call.send_dm, call.send_image, call.send_dm_image, call.format_mention, call.opening_mutation_scope,
    )
    async def on_readiness(readiness: OpeningReadiness) -> None:
        await reply(build_readiness_roster(readiness, format_mention=format_mention))

    async def render(opening_result: game_opening.OpeningResult) -> None:
        if opening_result.outcome == "silent":
            return
        if opening_result.outcome == "rejected":
            if opening_result.reason == "combat_unsettled":
                await reply(opening_result.text)
            elif opening_result.reason == "no_scenario":
                await reply("目前還沒有載入劇本，請先上傳 PDF 劇本。")
            elif opening_result.reason == "no_characters":
                await reply("目前這個群組還沒有任何調查員，請先用「/coc pc 角色名 職業」或「/coc usepregen 編號」建立角色。")
            elif opening_result.reason == "pending_pregen_luck":
                await reply(f"{opening_result.name} 尚未由玩家擲 LUCK，請相關玩家輸入「/coc luck roll」後才能開始遊戲。")
            elif opening_result.reason == "already_started":
                await reply("這局遊戲已經開始過了，不會重複產生開場白。想重新來一次的話，請用「/coc newgame」開新的一局。")
            elif opening_result.reason == "pending_luck_decision":
                await reply(f"{opening_result.name} 仍在等待 Luck 決定，請先處理後再開始遊戲。")
            elif opening_result.reason == "pending_check":
                await reply(f"{opening_result.name} 尚有待處理的檢定，請先完成後再開始遊戲。")
            elif opening_result.reason in {"source_changed", "timeline_changed", "character_set_changed", "admission_changed"}:
                await reply("開場準備期間劇本或調查員狀態已更新，舊開場未送出；請重新輸入「/coc start」。")
            else:
                raise ValueError(f"unsupported opening rejection: {opening_result.reason!r}")
            return
        if opening_result.outcome == "scripted":
            await reply(opening_result.text)
            if opening_result.check_reason:
                await reply(f"👉 {opening_result.check_reason}——請各自用「/coc check」擲骰。")
            return
        await run_post_turn_maintenance_after_output(
            conversation_id, reply, opening_result.text, send_dm, send_image, send_dm_image,
            list(opening_result.private_messages), list(opening_result.image_requests),
        )
        return

    await game_opening.open_game(
        conversation_id, user_id, on_readiness=on_readiness,
        mutation_scope=opening_mutation_scope, on_completion=render,
    )


_COMMANDS: dict[str, Callable[[_Call], Awaitable[None]]] = {
    "checkpoint": _checkpoint_command,
    "checkpoints": _checkpoint_command,
    "rollback": _checkpoint_command,
    "digest": _digest_command,
    "digests": _digest_command,
    "scenario": _scenario_command,
    "import": _import_command,
    "newgame": _newgame_command,
    "pdf": _pdf_command,
    "kp": _kp_command,
    "autoroll": _autoroll_command,
    "status": _status_command,
    "end": _end_command,
    "setpersona": _setpersona_command,
    "era": _era_command,
    "index": _index_command,
    "away": _away_command,
    "back": _back_command,
    "start": _start_command,
}


@mutation_admission.guard_async_entry
async def handle_system_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    parts: list[str],
    format_mention: FormatMention = lambda owner_id: owner_id,
    *,
    expected_revision: int | None = None,
    server: permissions.ServerFacts = permissions.NO_SERVER_FACTS,
    opening_mutation_scope: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
) -> None:
    sub = parts[1].casefold() if len(parts) > 1 else ""
    if sub in {'newgame', 'end', 'rollback', 'era'} or (
        sub == 'scenario' and len(parts) > 2 and parts[2].casefold() in {'use', 'merge', 'reparse', 'import'}
    ):
        guard_state = load_state(conversation_id)
        replacement_block = resource_bridge.guard_replacement(guard_state)
        if replacement_block:
            await reply(replacement_block)
            return

    handler = _COMMANDS.get(sub)
    if handler is None:
        await reply(f"未知的系統指令：{sub}")
        return
    await handler(_Call(
        conversation_id=conversation_id, user_id=user_id, reply=reply, send_dm=send_dm, send_image=send_image,
        send_dm_image=send_dm_image, parts=parts, format_mention=format_mention,
        expected_revision=expected_revision, server=server, opening_mutation_scope=opening_mutation_scope, sub=sub,
    ))


async def _change_kp(
    state: GroupState,
    action: Literal["transfer", "takeover"],
    user_id: str,
    target_token: str | None,
    reply: Reply,
    format_mention: FormatMention,
    *,
    too_many_args: bool,
    server: permissions.ServerFacts,
) -> None:
    """`/coc kp transfer @member` and `/coc kp takeover [@member]`.

    transfer: the current KP Assistant hands the seat over. takeover: a
    member with Discord's Manage Server permission takes the seat, or
    appoints someone to it, when the KP Assistant is missing. Both are
    announced publicly; the seat's exclusivity rules always apply.
    """
    if too_many_args or (action == "transfer" and target_token is None):
        await reply("用法：/coc kp transfer @成員 或 /coc kp takeover [@成員]")
        return
    new_kp = user_id
    if target_token is not None:
        mentioned = permissions.mentioned_user_id(target_token)
        if mentioned is None:
            await reply("請用 @ 指定一位成員。")
            return
        new_kp = mentioned
    if action == "transfer" and not permissions.is_kp(state, user_id):
        await reply(permissions.kp_only("交接 KP 助手身分"))
        return
    if action == "takeover" and not server.can_manage_server:
        await reply("只有在這個伺服器擁有「管理伺服器」權限的成員，才能接手或指派 KP 助手。")
        return
    if new_kp != user_id and new_kp not in server.member_ids:
        await reply("請用 @ 指定這個伺服器裡的成員。")
        return
    if new_kp == state.kp_assistant_user_id:
        await reply("這位成員已經是這局的 KP 助手。")
        return
    blocker = permissions.kp_seat_blocker(state, new_kp, is_bot=new_kp in server.bot_user_ids)
    if blocker:
        if action == "takeover" and new_kp == user_id:
            blocker += "你可以改用 /coc kp takeover @成員，指派一位沒有在玩的成員擔任 KP 助手。"
        await reply(blocker)
        return
    previous = state.kp_assistant_user_id
    state.kp_assistant_user_id = new_kp
    state.kp_ooc_log = []
    state_transaction.commit_snapshot(state)
    observability.event(
        f"kp.{action}",
        actor_hash=observability.safe_identifier(user_id),
        previous_hash=observability.safe_identifier(previous) if previous else None,
        new_hash=observability.safe_identifier(new_kp),
    )
    replaced = f"（取代原本的 KP 助手 {format_mention(previous)}）" if previous else ""
    if action == "transfer":
        await reply(f"{format_mention(user_id)} 已將 KP 助手交接給 {format_mention(new_kp)}。")
    elif new_kp == user_id:
        await reply(f"{format_mention(user_id)} 已接手成為這局的 KP 助手{replaced}。")
    else:
        await reply(f"{format_mention(user_id)} 指派 {format_mention(new_kp)} 擔任這局的 KP 助手{replaced}。")
