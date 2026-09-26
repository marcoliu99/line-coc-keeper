from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from app import (
    character_matcher,
    checkpoints,
    keeper,
    locks,
    observability,
    scenario_index,
    scenario_intro,
    scenario_library,
    scenario_rag,
    scene_digest,
    scene_map,
    spoiler_policy,
)
from app.agents import supervisor
from app.config import IMPORT_DIR
from app.legacy_commands import (
    FormatMention,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
    _build_readiness_roster,
    _heal_character,
    _is_kp_or_keeper,
    _resolve_pdf_upload_choice_locked,
    _run_post_turn_maintenance_after_output,
    _set_character_away_state,
    handle_pdf_upload,
)
from app.models import GroupState
from app.repositories import manual_pregens
from app.repositories.group_state import (
    clear_page_images,
    load_state,
    save_page_image,
    save_state,
    scenario_users,
)


async def _handle_local_import(
    conversation_id: str, user_id: str, reply: Reply, parts: list[str],
    expected_revision: int | None = None,
) -> None:
    state = load_state(conversation_id)
    if state.kp_assistant_user_id != user_id:
        await reply("只有目前登記的 KP Assistant 可以匯入伺服器上的 PDF。")
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
    if state.kp_assistant_user_id != user_id:
        await reply("只有目前登記的 KP Assistant 可以合併 PDF。")
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
    accepted = await handle_pdf_upload(conversation_id, reply, reply, merged, merged_name,
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


async def handle_system_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    parts: list[str],
    format_mention: FormatMention = lambda owner_id: owner_id,
    is_keeper: bool = False,
    expected_revision: int | None = None,
) -> None:
    sub = parts[1].casefold() if len(parts) > 1 else ""

    if sub in ("checkpoint", "checkpoints", "rollback"):
        state = load_state(conversation_id)
        if state.kp_assistant_user_id != user_id and not is_keeper:
            await reply("只有目前登記的 KP Assistant 可以操作回溯節點。")
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
        if state.kp_assistant_user_id != user_id and not is_keeper:
            await reply("只有目前登記的 KP Assistant 可以查看場景摘要。")
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
        if action == "cards":
            if not (is_keeper or state.kp_assistant_user_id == user_id):
                await reply("只有目前的 KP Assistant 或 Discord Keeper 可以管理手動角色卡。")
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
                lines.append(f"・{item['id']}《{item.get('title', '')}》{marker}" + (f"\n  章節：{chapters}" if chapters else ""))
            await reply("\n".join(lines))
            return
        if action == "reparse":
            if not _is_kp_or_keeper(state, user_id, is_keeper):
                await reply("只有目前的 KP Assistant 或 Discord Keeper 可以重新解析劇本。")
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
                if not _is_kp_or_keeper(state, user_id, is_keeper):
                    await reply("只有目前的 KP Assistant 或 Discord Keeper 可以重新解析劇本。")
                    return
                if state.pending_pregen_luck:
                    await reply("目前仍有預製角色等待玩家擲 LUCK，請先完成 `/coc luck roll` 後再重新解析劇本。")
                    return
                pending = state.pending_scenario_upload
                if pending is None:
                    await reply("沒有等待重新解析的 PDF。")
                    return
                try:
                    pdf_bytes = scenario_library.read_staged_upload(pending["key"])
                except FileNotFoundError:
                    state.pending_scenario_upload = None
                    save_state(state)
                    await reply("暫存 PDF 已不存在，請重新上傳。")
                    return
                state.pending_scenario_upload = None
                save_state(state)
                commit_revision = state.state_revision
            candidate_matches = pending.get("matches") or []
            reparse_candidate_id = candidate_matches[0]["id"] if candidate_matches else None
            await handle_pdf_upload(
                conversation_id, reply, reply, pdf_bytes, pending["file_name"],
                skip_similarity=True, reparse_candidate_id=reparse_candidate_id,
                expected_revision=commit_revision if expected_revision is not None else None,
            )
            scenario_library.discard_staged_upload(pending["key"])
            return
        if action == "cancel":
            if not _is_kp_or_keeper(state, user_id, is_keeper):
                await reply("只有目前的 KP Assistant 或 Discord Keeper 可以取消劇本處理。")
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
            if state.kp_assistant_user_id != user_id:
                await reply("只有目前登記的 KP Assistant 可以選擇劇本。")
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
            old_pool = list(state.pregens)
            old_scenario_id = state.scenario_library_id or None
            old_hash = ""
            if old_scenario_id:
                try:
                    old_hash = scenario_library.load_context(old_scenario_id)["manifest"].get("content_hash", "")
                except (FileNotFoundError, ValueError):
                    pass
            state.scenario_library_id = parts[3]
            state.scenario_title = context["manifest"]["title"]
            state.scenario_text = context["text"]
            state.active_chapter_id = context["active_chapter_id"]
            state.context_chapter_ids = context["context_chapter_ids"]
            state.scenario_npc_index = context["indexes"].get("npcs", [])
            state.scenario_location_index = context["indexes"].get("locations", [])
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
            # Pregens belong to the selected library item. Keep live
            # investigators in state.characters, but never leak the previous
            # scenario's pregen pool into this scenario's /coc pregens list.
            state.pregens = context["pregens"]
            state.openai_previous_response_id = ""
            state.openai_previous_response_timeline_id = ""
            state.active = True
            clear_page_images(conversation_id)
            scenario_library.copy_context_images(
                parts[3], context["page_numbers"],
                lambda page, image: save_page_image(conversation_id, page, image),
            )
            install_result: dict[str, bool] = {}
            def install_cards(conn):
                manual_pregens.capture_legacy(
                    conn, conversation_id, old_scenario_id, old_pool, old_hash,
                )
                state.pregens, install_result["stale"] = manual_pregens.install_pool(
                    conn, conversation_id, parts[3], context,
                    bind_unassigned=(old_scenario_id is None),
                )
            save_state(state, mutate_tx=install_cards)
            scenario_rag.schedule_index_prewarm(conversation_id, state.scenario_text)
            note = "\n舊版合併角色卡的劇本來源已變更；請重新匯入原始 role_ 卡。" if install_result.get("stale") else ""
            await reply(f"KP 已選擇《{state.scenario_title}》；目前 Context：{'、'.join(state.context_chapter_ids)}。{note}")
            return
        if action == "clean":
            if not _is_kp_or_keeper(state, user_id, is_keeper):
                await reply("只有目前的 KP Assistant 或 Discord Keeper 可以清理劇本庫。")
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
            await reply("已清除劇本庫項目。")
            return
        await reply("用法：/coc scenario list | use 劇本ID | clean 劇本ID | reparse | cancel | import 檔名.pdf | merge ID...")
        return
    if sub == "import":
        await _handle_local_import(conversation_id, user_id, reply, parts)
        return
    if sub == "newgame":
        previous = load_state(conversation_id)
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
        if not _is_kp_or_keeper(state, user_id, is_keeper):
            await reply("只有目前的 KP Assistant 或 Discord Keeper 可以處理劇本 PDF。")
            return
        choice_word = parts[2].casefold() if len(parts) > 2 else ""
        choice = {"new": "new", "全新": "new", "全新劇本": "new", "fix": "fix", "修正": "fix", "修正目前劇本": "fix"}.get(choice_word)
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
            return

        if kp_action is not None:
            await reply("用法：/coc kp 或 /coc kp quit")
            return

        if state.kp_assistant_user_id == user_id:
            await reply("你已經是這局的 KP 助手。")
            return
        if state.kp_assistant_user_id:
            await reply("這局已經有一位 KP 助手，不能同時登記第二位。")
            return
        if state.get_active_character(user_id) is not None:
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
        choice = parts[2].strip().lower()
        era_map = {"1920": "1920s", "1920s": "1920s", "modern": "modern"}
        if choice not in era_map:
            await reply("年代設定只接受「1920」或「modern」。")
            return
        state.era = era_map[choice]
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
        # §7.1: only the KP Assistant/Discord Keeper sees the full index
        # (HP/abilities); everyone else gets names only.
        is_privileged = state.kp_assistant_user_id == user_id or is_keeper
        if spoiler_policy.is_spoiler_protection_enabled() and not is_privileged:
            safe_index = spoiler_policy.redact_public_scenario_index(index_data)
            lines = [f"已重新建立劇本索引：{len(index_data['npcs'])} 個 NPC／怪物、{len(index_data['locations'])} 個地點。"]
            for n in safe_index["npcs"]:
                lines.append(f"・{n.get('name') or '未知存在'}")
            await reply(
                "\n".join(lines)
                + "\n\n（詳細數值僅供 KP Assistant／Discord Keeper 查看，一般玩家只會看到已登場的名稱。）"
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
            with locks.get_state_lock(conversation_id):
                state = load_state(conversation_id)
                if state.game_started:
                    return
                state.log.append({"role": "user", "content": "守密人：（遊戲開始，請朗讀開場白）"})
                state.log.append({"role": "assistant", "content": opening_text})
                state.game_started = True
                
                opening_check = opening_data.get("opening_check")
                if opening_check:
                    for owner_id, char in state.characters.items():
                        if opening_check["type"] == "skill":
                            value = keeper.resolve_skill_value(char, opening_check["skill"])
                            state.pending_checks[owner_id] = {
                                "type": "skill", "skill": opening_check["skill"], "skill_value": value,
                                "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular", "pushed": False,
                            }
                        else:
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
