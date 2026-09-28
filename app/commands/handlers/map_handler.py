from __future__ import annotations

from app import scene_map as scene_map_engine
from app.legacy_commands import Reply, SendDM, SendDMImage, SendImage
from app.repositories.group_state import load_page_image, load_state
from app.services import mutation_admission


@mutation_admission.guard_async_entry
async def handle_map_command(
    conversation_id: str,
    user_id: str,
    reply: Reply,
    send_image: SendImage,
    parts: list[str],
    *,
    send_dm: SendDM | None = None,
    send_dm_image: SendDMImage | None = None,
    actor_user_id: str | None = None,
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
            location = state.narrative_locations.get(user_id)
            await reply(f"目前在「{location}」（劇情位置，沒有平面圖）。" if location
                        else "目前不在任何有地圖的地點裡（或這份劇本沒有偵測到平面圖）。")
            return False
        scene_map = state.scene_maps.get(current_page)
        room = scene_map_engine.get_room(scene_map, state.current_room_id.get(user_id, "")) if scene_map else None
        if not room:
            await reply("地圖資料異常，目前所在房間找不到對應資料，請確認地圖資料或更正目前位置。")
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
        room = scene_map_engine.get_room(scene_map, scene_map.get("entry_room_id", ""))
        action = f"進入{scene_map.get('location_name') or (room or {}).get('name', '')}（地圖 {page_key} 的入口）"
    elif sub == "leavemap":
        state = load_state(conversation_id)
        action = "離開目前地點，前往劇本記載的出口外"
    else:
        await reply(f"未知的地圖指令：{sub}")
        return False

    character = state.get_active_character(user_id)
    if not state.active or not state.game_started or character is None:
        await reply("請先開始遊戲並使用有效角色，才能移動。")
        return False
    from app import locks
    from app.agents import supervisor
    from app.legacy_commands import _run_post_turn_maintenance_after_output

    # The post is inside the ordering, not after it: ordering the narration
    # but not the reply would still let two turns' messages come out reversed.
    async with locks.narrating_turn(conversation_id):
        public, private, images = await supervisor.run_turn(
            state, user_id, character.name, action, None, "player", conversation_id,
            actor_user_id=actor_user_id or user_id,
        )
        if send_dm is not None and send_dm_image is not None:
            await _run_post_turn_maintenance_after_output(
                conversation_id, reply, public, send_dm, send_image, send_dm_image, private, images)
        else:
            await reply(public)
    return True
