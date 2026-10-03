from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal
from uuid import uuid4

from app import (
    character_matcher,
    check_lifecycle,
    checkpoints,
    keeper,
    locks,
    observability,
    pdf_ingestion_drafts,
    scenario_activation,
    scenario_authoring,
    scenario_index,
    scenario_intro,
    scenario_library,
    scenario_rag,  # noqa: F401 - retained for existing command integration mocks
    scenario_source_authoring,
    scenario_templates,
    scene_digest,
    scene_map,
    spoiler_policy,
)
from app.agents import supervisor
from app.commands import permissions
from app.config import IMPORT_DIR
from app.keeper_tools import resource_bridge
from app.legacy_commands import (
    FormatMention,
    PdfChoice,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
    _build_readiness_roster,
    _heal_character,
    _resolve_pdf_upload_choice_locked,
    _run_post_turn_maintenance_after_output,
    _set_character_away_state,
    handle_pdf_upload,
)
from app.models import GroupState
from app.repositories import manual_pregens
from app.repositories.group_state import (
    load_state,
    save_state,
    scenario_users,
)
from app.services import (
    correction_adjudication,
    history_authority,
    mutation_admission,
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
    await handle_pdf_upload(conversation_id, reply, reply, pdf_bytes, pdf_path.name, owner_user_id=user_id,
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
    if refs == ["list"]:
        if not state.staged_pdf_parts:
            await reply("目前沒有暫存的 PDF part。")
            return
        await reply("暫存 PDF：\n" + "\n".join(f"・{p['key'][:12]} {p['file_name']}" for p in state.staged_pdf_parts))
        return
    selected: list[dict[str, str]] = []
    for ref in refs:
        matches = [p for p in state.staged_pdf_parts if p["key"].startswith(ref) or p["file_name"] == ref]
        if len(matches) != 1:
            await reply(f"暫存 ID／檔名無法唯一對應：{ref}。先用 /coc scenario merge list 查看。")
            return
        if matches[0] not in selected:
            selected.append(matches[0])
    if len(selected) < 2:
        await reply("至少要指定兩個 PDF part 才能合併。")
        return
    try:
        payloads = [scenario_library.read_staged_upload(item["key"]) for item in selected]
    except FileNotFoundError:
        await reply("其中一個暫存 PDF 已不存在，請重新上傳。")
        return
    from app.pdf_loader import combine_pdfs
    merged = await asyncio.to_thread(combine_pdfs, payloads)
    merged_name = f"{selected[0]['file_name'].rsplit('.', 1)[0]}_merged.pdf"
    accepted = await handle_pdf_upload(conversation_id, reply, reply, merged, merged_name, owner_user_id=user_id,
                                       expected_revision=expected_revision)
    if not accepted:
        return
    for item in selected:
        scenario_library.discard_staged_upload(item["key"])
    async with locks.get_conversation_lock(conversation_id):
        latest = load_state(conversation_id)
        latest.staged_pdf_parts = [p for p in latest.staged_pdf_parts if p not in selected]
        save_state(latest)
def _replace_scene_maps_preserving_locations(state: GroupState, new_maps: dict) -> None:
    previous_locations = {
        owner_id: (state.current_map_page.get(owner_id, ""), state.current_room_id.get(owner_id, ""))
        for owner_id in set(state.current_map_page) | set(state.current_room_id)
    }
    state.scene_maps = dict(new_maps)
    state.current_map_page = {}
    state.current_room_id = {}
    for owner_id, (map_key, room_id) in previous_locations.items():
        new_map = state.scene_maps.get(map_key)
        if new_map is not None and scene_map.get_room(new_map, room_id) is not None:
            state.current_map_page[owner_id] = map_key
            state.current_room_id[owner_id] = room_id
        else:
            state.party_facing.pop(owner_id, None)


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
) -> None:
    sub = parts[1].casefold() if len(parts) > 1 else ""
    if sub in {'newgame', 'end', 'rollback', 'start', 'era'} or (
        sub == 'scenario' and len(parts) > 2 and parts[2].casefold() in {'use', 'merge', 'reparse', 'import'}
    ):
        guard_state = load_state(conversation_id)
        replacement_block = resource_bridge.guard_replacement(guard_state)
        if replacement_block:
            await reply(replacement_block)
            return

    if sub in ("checkpoint", "checkpoints", "rollback"):
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

    if sub in ("digest", "digests"):
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

    if sub == "scenario":
        action = parts[2].casefold() if len(parts) > 2 else "list"
        state = load_state(conversation_id)
        if action in ("continue", "status", "cancel"):
            if not permissions.may_manage_scenario_lifecycle(state, user_id):
                await reply(permissions.kp_only("管理 PDF 匯入草稿"))
                return
            try:
                draft = pdf_ingestion_drafts.load(conversation_id)
            except (OSError, ValueError) as exc:
                await reply(f"無法讀取匯入草稿：{exc}")
                return
            if draft:
                if action == "status":
                    await reply(pdf_ingestion_drafts.ContinueImportMessage(pdf_ingestion_drafts.progress(draft), draft["draft_id"]))
                elif action == "cancel":
                    pdf_ingestion_drafts.discard(conversation_id, draft["draft_id"])
                    await reply("已取消 PDF 匯入草稿。")
                else:
                    if len(parts) > 3 and parts[3] != draft["draft_id"]:
                        await reply("這個匯入按鈕已過期，請重新查看 /coc scenario status。")
                        return
                    await handle_pdf_upload(
                        conversation_id, reply, reply, pdf_ingestion_drafts.pdf_bytes(draft), draft["file_name"],
                        skip_similarity=True, reparse_candidate_id=draft.get("reparse_candidate_id"),
                        expected_revision=expected_revision, owner_user_id=draft.get("owner_id", ""),
                        resume_draft_id=draft["draft_id"],
                    )
                return
            if action in ("continue", "status"):
                await reply("目前沒有等待處理的 PDF 匯入草稿。")
                return
        if action == "source":
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
        if action == "template":
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
        if action == "cards":
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
                    context = scenario_activation.load_state_context(state)
                except (FileNotFoundError, ValueError):
                    pass
            def remove_card(conn):
                manual_pregens.delete_asset(conn, conversation_id, scenario_id, asset_id)
                if context:
                    state.pregens, _ = manual_pregens.install_pool(
                        conn, conversation_id, scenario_id, context,
                        claimed=[p for p in state.pregens if p.get("claimed_by")],
                    )
            save_state(state, mutate_tx=remove_card)
            await reply(f"已刪除手動角色卡資產 {asset_id}。")
            return
        if action == "import":
            await _handle_local_import(conversation_id, user_id, reply, parts, expected_revision)
            return
        if action == "merge":
            await _handle_staged_merge(conversation_id, user_id, reply, parts, expected_revision)
            return
        if action == "list":
            entries = scenario_library.list_scenarios()
            if not entries:
                await reply("劇本庫目前是空的，請先上傳 PDF。")
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
        if action == "reparse":
            if not permissions.may_manage_scenario_lifecycle(state, user_id):
                await reply(permissions.kp_only("重新解析劇本"))
                return
            if state.pending_pregen_luck:
                await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再重新解析劇本。")
                return
            # Claim and clear the staged item under the conversation lock, then
            # release it before the intentionally long PDF extraction begins.
            async with locks.get_conversation_lock(conversation_id):
                state = load_state(conversation_id)
                if expected_revision is not None and state.state_revision != expected_revision:
                    await reply("遊戲狀態已更新，請重新開啟 Help 操作。")
                    return
                if not permissions.may_manage_scenario_lifecycle(state, user_id):
                    await reply(permissions.kp_only("重新解析劇本"))
                    return
                if state.pending_pregen_luck:
                    await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再重新解析劇本。")
                    return
                pending = state.pending_scenario_upload
                from_published = pending is None
                requested_id = parts[3] if len(parts) > 3 else state.scenario_library_id
                if not from_published and len(parts) > 3:
                    await reply("目前有等待處理的 PDF；請先重新解析或取消，再指定劇本 ID。")
                    return
                if from_published and not requested_id:
                    await reply("沒有可重新解析的劇本或等待重新解析的 PDF。")
                    return
                try:
                    if from_published:
                        pdf_bytes, filename = scenario_library.read_source_pdf(requested_id)
                        pending = {'key': '', 'file_name': filename,
                                   'matches': [{'id': requested_id}]}
                    else:
                        assert pending is not None
                        pdf_bytes = scenario_library.read_staged_upload(pending["key"])
                except FileNotFoundError:
                    state.pending_scenario_upload = None
                    save_state(state)
                    await reply("來源 PDF 已不存在或劇本 ID 無效，請查看 /coc scenario list 或重新上傳。")
                    return
                state.pending_scenario_upload = None
                save_state(state)
                commit_revision = state.state_revision
                claimed_timeline = state.timeline_id
            assert pending is not None
            candidate_matches = pending.get("matches") or []
            reparse_candidate_id = candidate_matches[0]["id"] if candidate_matches else None
            accepted = False
            previous_draft = pdf_ingestion_drafts.load(conversation_id)
            resume_id = (previous_draft["draft_id"] if previous_draft
                         and previous_draft["file_name"] == pending["file_name"]
                         and pdf_ingestion_drafts.pdf_bytes(previous_draft) == pdf_bytes else "")
            try:
                accepted = await handle_pdf_upload(
                    conversation_id, reply, reply, pdf_bytes, pending["file_name"],
                    skip_similarity=True, reparse_candidate_id=reparse_candidate_id, owner_user_id=user_id,
                    resume_draft_id=resume_id,
                    expected_revision=commit_revision if expected_revision is not None else None,
                )
            finally:
                current_draft = pdf_ingestion_drafts.load(conversation_id)
                if from_published:
                    pass  # The published source remains available; no staged choice to restore.
                elif accepted or (current_draft and current_draft.get("report", {}).get("blocked_pages")):
                    scenario_library.discard_staged_upload(pending["key"])
                else:
                    # Do not save the pre-extraction snapshot over concurrent play.
                    # A newer upload or timeline owns its state; keep the source
                    # bytes without resurrecting an old session's pending item.
                    async with locks.get_conversation_lock(conversation_id):
                        recovery_state = load_state(conversation_id)
                        if (recovery_state.timeline_id == claimed_timeline
                                and recovery_state.pending_scenario_upload is None):
                            recovery_state.pending_scenario_upload = pending
                            save_state(recovery_state)
            return
        if action == "cancel":
            if not permissions.may_manage_scenario_lifecycle(state, user_id):
                await reply(permissions.kp_only("取消劇本處理"))
                return
            pending = state.pending_scenario_upload
            if pending is None:
                await reply("沒有等待處理的 PDF。")
                return
            scenario_library.discard_staged_upload(pending.get("key", ""))
            state.pending_scenario_upload = None
            save_state(state)
            await reply("已放棄本次上傳，既有劇本不受影響。")
            return
        if action == "use":
            if not permissions.is_kp(state, user_id):
                await reply(permissions.kp_only("選擇劇本"))
                return
            if state.pending_pregen_luck:
                await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再切換劇本。")
                return
            if state.pending_pdf_upload is not None or state.pending_scenario_upload is not None:
                await reply("目前仍有待處理的劇本上傳，請先完成或取消該流程後再切換劇本。")
                return
            if len(parts) < 4:
                await reply("用法：/coc scenario use 劇本ID（先用 /coc scenario list 查看）")
                return
            try:
                context = scenario_library.load_context(parts[3])
            except (FileNotFoundError, ValueError):
                await reply("找不到可使用的劇本 ID。請先用 /coc scenario list 查看。")
                return
            preference_notice = scenario_templates.preference_notice(conversation_id, parts[3])
            variant_id = parts[4] if len(parts) > 4 else scenario_templates.preferred_variant(conversation_id, parts[3])
            try:
                if variant_id != "original":
                    scenario_templates.require_approved(parts[3], variant_id)
            except (FileNotFoundError, ValueError) as exc:
                await reply(f"中文模板無法啟用：{exc}")
                return
            old_pool = list(state.pregens)
            old_scenario_id = state.scenario_library_id or None
            old_hash = ""
            if old_scenario_id:
                try:
                    old_hash = scenario_library.load_context(old_scenario_id)["manifest"].get("content_hash", "")
                except (FileNotFoundError, ValueError):
                    pass
            scenario_activation.install_context_fields(
                state, parts[3], context, variant_id=variant_id, preserve_maps=True,
            )
            # Selecting a scenario is a new campaign context even when the
            # live investigator sheets are retained.  Old maintenance,
            # memory, and provider results must not bleed into this scenario.
            old_timeline_id = state.timeline_id or f"legacy-{conversation_id}"
            state.timeline_id = f"timeline-{uuid4().hex[:8]}"
            # All player decisions and deterministic-result caches belong to
            # the previous scenario timeline.  Clear them at the reset point
            # so an old Discord button or typed command cannot be consumed by
            # the newly selected scenario.
            state.pending_checks.clear()
            state.pending_luck_decisions.clear()
            state.deterministic_check_results.clear()
            state.resolved_check_events.clear()
            observability.event(
                "provider.chain.reset",
                reason="scenario_use",
                old_timeline_id=old_timeline_id,
                requested_timeline_id=state.timeline_id,
                provider="openai",
            )
            _replace_scene_maps_preserving_locations(state, context["scene_maps"])
            # /coc scenario use assigns both itself, so the upload flow's report
            # never ran here — selecting an already-stored affected variant was
            # the one path that stayed silent.
            artifact_notice = scenario_index.report_location_index(
                state.scenario_location_index, source="scenario_use",
                scenario_title=state.scenario_title, scene_maps=state.scene_maps)
            # Pregens belong to the selected library item. Keep live
            # investigators in state.characters, but never leak the previous
            # scenario's pregen pool into this scenario's /coc pregens list.
            state.openai_previous_response_id = ""
            state.openai_previous_response_timeline_id = ""
            state.active = True
            install_result: dict[str, bool] = {}
            def install_cards(conn):
                manual_pregens.capture_legacy(
                    conn, conversation_id, old_scenario_id, old_pool, old_hash,
                )
                state.pregens, install_result["stale"] = manual_pregens.install_pool(
                    conn, conversation_id, parts[3], context,
                    bind_unassigned=(old_scenario_id is None),
                )
            _, image_refreshed = scenario_activation.commit_and_refresh(
                lambda: save_state(state, mutate_tx=install_cards),
                conversation_id, parts[3], context,
            )
            if len(parts) > 4:
                scenario_templates.select_variant(conversation_id, parts[3], variant_id)
            scenario_templates.schedule_index_prewarm(state)
            note = "\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。" if install_result.get("stale") else ""
            image_notice = "\n頁面圖片快取刷新失敗；劇本已啟用，請聯絡 KP 檢查圖片。" if not image_refreshed else ""
            await reply(f"KP 已選擇《{state.scenario_title}》；目前 Context：{'、'.join(state.context_chapter_ids)}。{note}"
                        + (f"\n{preference_notice}" if preference_notice and len(parts) == 4 else "")
                        + (f"\n\n{artifact_notice}" if artifact_notice else "") + image_notice)
            return
        if action == "clean":
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
        await reply("用法：/coc scenario list | use 劇本ID [模板版本] | template status|export|preview|approve|import | clean 劇本ID | reparse | cancel | import 檔名.pdf | merge ID...")
        return
    if sub == "import":
        await _handle_local_import(conversation_id, user_id, reply, parts)
        return
    if sub == "newgame":
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
        def retain_manual_cards(conn):
            manual_pregens.capture_legacy(
                conn, conversation_id, previous_id, previous.pregens, previous_hash,
            )
        save_state(GroupState(group_id=conversation_id), reason="newgame", mutate_tx=retain_manual_cards)
        await reply("已重置這個群組的遊戲狀態。請上傳劇本 PDF 檔案開始新的冒險。")
        return

    if sub == "pdf":
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
        await reply(_resolve_pdf_upload_choice_locked(conversation_id, choice))
        return

    if sub == "kp":
        kp_action: str | None = parts[2].casefold() if len(parts) > 2 else None
        state = load_state(conversation_id)

        if kp_action == "quit":
            if state.kp_assistant_user_id != user_id:
                await reply("你目前不是這局的 KP 助手。")
                return
            state.kp_assistant_user_id = ""
            state.kp_ooc_log = []
            save_state(state)
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
        save_state(state)
        await reply("已登記你為這局的 KP 助手。")
        return

    if sub == "autoroll":
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
        save_state(state)
        await reply(
            "已開啟自動擲骰；之後新建立的技能、攻擊、SAN、重傷 CON 檢定可由 Keeper/system 立即處理。"
            if state.autoroll_checks
            else "已關閉自動擲骰；之後新建立的角色檢定會等待玩家用 /coc check 或按鈕擲骰。"
        )
        return

    if sub == "status":
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

    if sub == "end":
        state = load_state(conversation_id)
        replacement_block = resource_bridge.guard_replacement(state)
        if replacement_block:
            await reply(replacement_block)
            return
        state.active = False
        state.kp_assistant_user_id = ""
        state.kp_ooc_log = []
        save_state(state)
        await reply("遊戲已結束，遊戲紀錄與角色仍會保留；KP 助手身分也已解除。要開新的一局請用 /coc newgame。")
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
        if parts[2].casefold() == "reset" and len(parts) == 3:
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
        era_choice = parts[2].strip().lower()
        era_map = {"1920": "1920s", "1920s": "1920s", "modern": "modern"}
        if era_choice not in era_map:
            await reply("年代設定只接受「1920」或「modern」。")
            return
        state.era = era_map[era_choice]
        save_state(state)
        await reply(f"已設定這個群組的年代為：{'1920 年代' if state.era == '1920s' else '現代／當代'}。")
        return

    if sub == "index":
        state = load_state(conversation_id)
        if not state.scenario_text:
            await reply("目前還沒有載入劇本，上傳 PDF 之後才能抽取 NPC／怪物與地點索引。")
            return
        index_data = await asyncio.to_thread(scenario_index.extract_scenario_index, state.scenario_text)
        state.scenario_npc_index = index_data["npcs"]
        state.scenario_location_index = index_data["locations"]
        save_state(state)
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

    if sub == "away":
        result = await asyncio.to_thread(_set_character_away_state, conversation_id, user_id, True)
        if result.error_text:
            await reply(result.error_text)
            return
        await reply(f"{result.character_name} 已標記為暫離，戰鬥中會自動跳過他的回合，直到輸入「/coc back」回來。")
        return

    if sub == "back":
        result = await asyncio.to_thread(_set_character_away_state, conversation_id, user_id, False)
        if result.error_text:
            await reply(result.error_text)
            return
        await reply(f"{result.character_name} 回來了，恢復正常參與。")
        return

    if sub == "start":
        state = load_state(conversation_id)
        if not state.active or not state.scenario_text:
            await reply("目前還沒有載入劇本，請先上傳 PDF 劇本。")
            return
        if not state.characters:
            await reply("目前這個群組還沒有任何調查員，請先用「/coc pc 角色名 職業」或「/coc usepregen 編號」建立角色。")
            return
        if state.pending_pregen_luck:
            names = "、".join(
                state.characters_by_id[character_id].name
                for character_id in state.pending_pregen_luck.values()
                if character_id in state.characters_by_id
            ) or "部分角色"
            await reply(f"{names} 尚未由玩家擲 LUCK，請相關玩家輸入「/coc luck roll」後才能開始遊戲。")
            return
        if state.game_started:
            await reply("這局遊戲已經開始過了，不會重複產生開場白。想重新來一次的話，請用「/coc newgame」開新的一局。")
            return

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

        opening_data: dict[str, Any] = await asyncio.to_thread(scenario_intro.extract_opening_narration, state.scenario_text)

        if opening_data["found"]:
            opening_text = opening_data["text"]
            opening_check = opening_data.get("opening_check")
            opening_blocker = ""
            with locks.get_state_lock(conversation_id):
                state = load_state(conversation_id)
                if state.game_started:
                    return
                if opening_check:
                    candidates: dict[str, dict[str, Any]] = {}
                    for owner_id, char in state.characters.items():
                        if opening_check["type"] == "skill":
                            candidates[owner_id] = {
                                "type": "skill", "skill": opening_check["skill"],
                                "skill_value": keeper.resolve_skill_value(char, opening_check["skill"], register_unknown=False),
                                "bonus_dice": 0, "penalty_dice": 0,
                                "difficulty": "regular", "pushed": False,
                            }
                        else:
                            candidates[owner_id] = {
                                "type": "sanity",
                                "loss_success": opening_check.get("loss_success", "0"),
                                "loss_failure": opening_check.get("loss_failure", "1d4"),
                            }
                    registrations = check_lifecycle.register_many(
                        state, candidates, source={"action_context": opening_check.get("reason", "")}
                    )
                    blocked = next(
                        ((owner_id, entry.blocker) for owner_id, entry in registrations.items()
                         if entry.status == "blocked"), None
                    )
                    if blocked:
                        owner_id, reason = blocked
                        character = state.characters[owner_id]
                        if reason == "pending_luck_decision":
                            opening_blocker = f"{character.name} 仍在等待 Luck 決定，請先處理後再開始遊戲。"
                        else:
                            opening_blocker = f"{character.name} 尚有待處理的檢定，請先完成後再開始遊戲。"
                if not opening_blocker:
                    if opening_check and opening_check["type"] == "skill":
                        for char in state.characters.values():
                            keeper.resolve_skill_value(char, opening_check["skill"])
                    turn_id = str(observability.current_context().get("turn_id") or uuid4().hex)
                    state.log.append(history_authority.annotate_entry(
                        {"role": "user", "content": "守密人：（遊戲開始，請朗讀開場白）"},
                        turn_id=turn_id, timeline_id=state.timeline_id,
                        record_kind="opening_instruction", authority="claim",
                    ))
                    state.log.append(history_authority.annotate_entry(
                        {"role": "assistant", "content": opening_text},
                        turn_id=turn_id, timeline_id=state.timeline_id,
                    ))
                    state.game_started = True
                    save_state(state)
            if opening_blocker:
                await reply(opening_blocker)
                return
            await reply(opening_text)
            if opening_check and opening_check.get("reason"):
                await reply(f"👉 {opening_check['reason']}——請各自用「/coc check」擲骰。")
            return

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
        async with locks.narrating_turn(conversation_id):
            fresh_state = keeper._refresh_state_snapshot(state)
            if fresh_state.game_started:
                return
            keeper_reply, private_messages, image_requests = await supervisor.run_turn(
                state=fresh_state,
                user_id=user_id,
                display_name="守密人",
                text=keeper_message,
                resolved_location=None,
                speaker_role="player",
                conversation_id=conversation_id,
                turn_kind="opening_fallback",
            )
        await _run_post_turn_maintenance_after_output(
            conversation_id, reply, keeper_reply, send_dm, send_image, send_dm_image, private_messages, image_requests
        )
        return

    await reply(f"未知的系統指令：{sub}")


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
    save_state(state)
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
