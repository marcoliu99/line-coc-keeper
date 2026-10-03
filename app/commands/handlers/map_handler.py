from __future__ import annotations

from typing import cast

from app import map_routes
from app import scene_map as scene_map_engine
from app.legacy_commands import Reply, SendImage
from app.repositories.group_state import load_page_image, load_state, save_state
from app.services import mutation_admission


@mutation_admission.guard_async_entry
async def handle_map_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_image: SendImage,
    parts: list[str],
) -> bool:
    sub = parts[1].casefold() if len(parts) > 1 else ""

    if sub == 'route':
        outcome_index = 4 if len(parts) > 4 and parts[4] in ('opened', 'discovered', 'failed') else 5
        if len(parts) <= outcome_index or parts[outcome_index] not in ('opened', 'discovered', 'failed'):
            await reply('用法：/coc route 頁碼 route-id [barrier-id] opened|discovered|failed [已裁定的後果]（僅 KP）')
            return False
        try:
            # Explicit KP confirmation after normal action resolution; not a narrative inference.
            result = map_routes.commit_outcome(conversation_id, user_id, parts[2], parts[3], cast(map_routes.RouteOutcome, parts[outcome_index]),
                                               barrier_id=parts[4] if outcome_index == 5 else None,
                                               consequence=' '.join(parts[outcome_index + 1:]))
        except (PermissionError, ValueError, OSError):
            await reply('無法確認路線狀態；需由目前 KP 確認有效來源地圖與已裁定的 action 結果。')
            return False
        await reply('地圖路線狀態已儲存。' if result['availability'] == 'available' else
                    '嘗試結果已儲存；路線仍可再次嘗試，尚未開通。')
        return True

    if sub == 'traverse':
        if len(parts) != 3:
            await reply('用法：/coc traverse segment-id（只可通過目前已開通的路段）')
            return False
        try:
            map_routes.traverse_segment(conversation_id, user_id, parts[2])
        except (ValueError, OSError):
            await reply('目前無法通過此路段；需先到達並確認障礙已開通。')
            return False
        await reply('已通過已開通的路段。')
        return True

    if sub == "showpage":
        if len(parts) < 3:
            await reply("用法：/coc showpage 頁碼（例如 /coc showpage 16；守密人提到「第 X 頁」時可以用那個數字）")
            return False
        try:
            page_number = int(parts[2])
        except ValueError:
            await reply("頁碼必須是數字。")
            return False
        png_bytes = load_page_image(conversation_id, page_number)
        if not png_bytes:
            await reply(f"第 {page_number} 頁沒有存圖（可能是純文字頁面，或劇本裡根本沒有這一頁）。")
            return False
        await send_image(png_bytes, conversation_id, page_number)
        return True

    if sub == "where":
        state = load_state(conversation_id)
        current_page = state.current_map_page.get(user_id, "")
        if not current_page:
            await reply("目前不在任何有地圖的地點裡（或這份劇本沒有偵測到平面圖）。")
            return False
        scene_map = state.scene_maps.get(current_page)
        room = scene_map_engine.get_room(scene_map, state.current_room_id.get(user_id, "")) if scene_map else None
        if not scene_map or not room:
            await reply("地圖資料異常，目前所在房間找不到對應資料，可以用「/coc leavemap」重置。")
            return False
        exits = scene_map_engine.visible_exits(scene_map, room["id"],
            available_routes=map_routes.available_routes(state, current_page))
        exits_text = "、".join(
            f"/coc traverse {e['segment_id']}" if e.get('segment_id') else str(e.get('label') or e.get('compass'))
            for e in exits) or "（沒有記錄到出口）"
        desc = f"\n{room['description']}" if room.get("description") else ""
        location_note = f"第 {current_page} 頁的地圖" if current_page.isdigit() else f"地圖「{current_page}」"
        barriers = map_routes.visible_barriers(state, current_page, room['id'])
        barrier_note = '\n已確認的障礙尚未開通。' if any(b['state'] == 'blocked' for b in barriers) else ''
        position = f"目前在「{room['name']}」" if room.get('name') else '目前位置'
        await reply(f"{position}（{location_note}）{desc}\n出口：{exits_text}{barrier_note}")
        return True

    if sub == "enter":
        if len(parts) < 3:
            await reply("用法：/coc enter 頁碼（先用 /coc showpage 或劇本內文找到平面圖在第幾頁）")
            return False
        page_key = parts[2]
        state = load_state(conversation_id)
        scene_map = state.scene_maps.get(page_key)
        if not scene_map:
            available = "、".join(sorted(state.scene_maps.keys())) or "（沒有偵測到任何平面圖）"
            await reply(f"第 {page_key} 頁沒有偵測到平面圖。有地圖資料的頁碼：{available}")
            return False
        state.current_map_page[user_id] = page_key
        state.current_room_id[user_id] = scene_map.get("entry_room_id", "")
        state.party_facing[user_id] = "N"
        save_state(state)
        room = scene_map_engine.get_room(scene_map, state.current_room_id[user_id])
        await reply(f"已進入第 {page_key} 頁的地圖，目前在「{room.get('name', '') if room else '未知位置'}」。")
        return True

    if sub == "leavemap":
        state = load_state(conversation_id)
        state.current_map_page.pop(user_id, None)
        state.current_room_id.pop(user_id, None)
        state.party_facing.pop(user_id, None)
        save_state(state)
        await reply("已離開目前的地圖追蹤，移動改回完全由守密人自己判斷。")
        return True

    await reply(f"未知的地圖指令：{sub}")
    return False
