from __future__ import annotations

from app import scene_map as scene_map_engine
from app.repositories.group_state import load_state, save_state, load_page_image
from app.legacy_commands import Reply, SendImage


async def handle_map_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_image: SendImage,
    parts: list[str],
) -> bool:
    sub = parts[1].casefold() if len(parts) > 1 else ""

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
        if not room:
            await reply("地圖資料異常，目前所在房間找不到對應資料，可以用「/coc leavemap」重置。")
            return False
        exits = room.get("exits", [])
        exits_text = "、".join(f"{e.get('label') or e.get('compass')}" for e in exits) or "（沒有記錄到出口）"
        desc = f"\n{room['description']}" if room.get("description") else ""
        location_note = f"第 {current_page} 頁的地圖" if current_page.isdigit() else f"地圖「{current_page}」"
        await reply(f"目前在「{room.get('name', '')}」（{location_note}）{desc}\n出口：{exits_text}")
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
