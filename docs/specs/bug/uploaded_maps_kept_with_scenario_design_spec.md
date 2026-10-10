# Uploaded maps are kept with their scenario, like role cards

[繁體中文](uploaded_maps_kept_with_scenario_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `f2d0446`.

## Problem

In the 2026-10-10 Discord session the KP uploaded `map_corbitt_house.yaml` for The Haunting and got 「地圖「Corbitt House」已儲存」. After `/coc newgame` and `/coc kp`, choosing The Haunting from the library answered 「⚠️ 這份劇本沒有樓層圖…樓層圖只能從 PDF 重新解析取得」. The Lightless Beacon maps were lost the same way twice in that session.

There were two causes:

- **The map lived only in the game state.** `handle_map_upload` wrote it to `GroupState.scene_maps`. `/coc newgame` replaces the whole state, so the map was gone. The library has no uploaded maps (`scene_maps.json` keeps page maps only), so choosing the scenario could not bring it back. Role cards (`manual_pregen_assets`) and page repairs (`scenario_page_repairs`) are stored per conversation and scenario and survive a new game. Maps did not.
- **Choosing a scenario dropped the running uploads too.** `_use_existing` installed with `preserve_maps=True`, then `_keep_valid_map_locations(state, context["scene_maps"])` replaced the maps with the library's.

The notice also named only the PDF as a source of floor plans.

## Change

- **A map store like the role cards.** `app/repositories/scenario_maps.py` keeps uploaded maps in `scenario_map_assets`, keyed by conversation and scenario. A map uploaded with no scenario loaded waits under no scenario.
- **Upload saves it.** `handle_map_upload` writes the map to the store in the same transaction as the state. The reply says 「已和《The Haunting》一起保存，之後開新局或重新選這個劇本都會自動帶入。」, or that the map will go with the next scenario loaded. Uploading the same file name replaces the stored map.
- **Every scenario load brings it back.** `scenario_activation.install_context_fields` lays the stored maps over the scenario's own maps. This covers a new upload, a fix, choosing from the library and a chapter change.
- **Choosing a scenario keeps the right maps.** `_use_existing` keeps the scenario's stored maps. When the same scenario is chosen again, it also keeps what is running. Another scenario's uploads are not carried over.
- **Activation saves them for the scenario.** `_commit_activation` stores every uploaded map the scenario runs with, including one from before maps were saved. When no scenario was loaded before, the waiting maps join this one and the waiting entry is dropped, as role cards are bound.
- **The notice** reads 「可上傳 map_*.yaml 地圖檔（會跟劇本一起保存），或從 PDF 重新解析取得。」.

## Not changing

- Maps are keyed by library scenario id, the same as role cards. A changed scenario file uploaded as a new scenario, not as a fix, is a new library entry and starts without them.
- There is no delete command. Uploading the same file name replaces the map.

## Verification

`tests/test_scenario_maps_kept.py`:
- Upload → new game → choose the scenario: the map, its location and no floor-plan notice.
- Choosing the same scenario again keeps a running map and stores it.
- A map uploaded before any scenario joins the first one chosen, and the waiting entry is dropped.
- Another scenario does not take the map; choosing the first scenario again brings it back.
- Uploading the same file name replaces the stored map.
