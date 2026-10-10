# 上傳的地圖像角色卡一樣跟著劇本保存

[English](uploaded_maps_kept_with_scenario_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `f2d0446`。

## 問題

2026-10-10 的 Discord 對局裡，KP 為《The Haunting》上傳了 `map_corbitt_house.yaml`，收到「地圖「Corbitt House」已儲存」。接著 `/coc newgame`、`/coc kp`，再從劇本庫選《The Haunting》，卻得到「⚠️ 這份劇本沒有樓層圖…樓層圖只能從 PDF 重新解析取得」。同一場裡，《The Lightless Beacon》的地圖也這樣丟了兩次。

原因有兩個：

- **地圖只存在這一局的遊戲狀態。** `handle_map_upload` 只寫進 `GroupState.scene_maps`。`/coc newgame` 會整份換掉狀態，地圖就跟著不見。劇本庫的 `scene_maps.json` 只存 PDF 頁面地圖，沒有上傳的地圖，所以選劇本時也拿不回來。角色卡（`manual_pregen_assets`）和頁面修正（`scenario_page_repairs`）都以「對話＋劇本」保存，新局後仍在；地圖沒有。
- **選劇本時連當下的地圖也被清掉。** `_use_existing` 用 `preserve_maps=True` 安裝劇本後，又呼叫 `_keep_valid_map_locations(state, context["scene_maps"])`，把地圖換成劇本庫的版本。

另外，提示只說樓層圖要從 PDF 取得。

## 變更

- **比照角色卡的地圖儲存。** `app/repositories/scenario_maps.py` 把上傳的地圖存在 `scenario_map_assets`，以「對話＋劇本」為鍵。還沒載入劇本時上傳的地圖，先存在「尚無劇本」底下。
- **上傳就保存。** `handle_map_upload` 在寫入狀態的同一筆交易裡，同時存進地圖儲存。回覆會說「已和《The Haunting》一起保存，之後開新局或重新選這個劇本都會自動帶入。」；沒有劇本時則說明會跟下一個載入的劇本一起保存。同檔名重傳會取代原本存的地圖。
- **每次載入劇本都帶回來。** `scenario_activation.install_context_fields` 會把存好的地圖疊在劇本自帶的地圖上，涵蓋新上傳、修正、從劇本庫選擇和換章節。
- **選劇本時保留正確的地圖。** `_use_existing` 保留這個劇本存好的地圖；重選同一個劇本時，也保留當下正在用的地圖。不會把別的劇本的地圖帶過來。
- **啟用時存到劇本底下。** `_commit_activation` 會把這個劇本正在用的上傳地圖都存起來，包括這次修改之前就上傳的。如果之前還沒載入過劇本，等待中的地圖會併入這個劇本並移除等待紀錄，跟角色卡的做法一樣。
- **提示文字**改為「可上傳 map_*.yaml 地圖檔（會跟劇本一起保存），或從 PDF 重新解析取得。」

## 不變更

- 地圖跟角色卡一樣，以劇本庫的劇本 id 為鍵。劇本檔改過內容後選「新劇本」而不是「修正」上傳，會成為新的劇本庫項目，不會帶著原本的地圖。
- 不提供刪除指令；同檔名重傳即可取代。

## 驗證

`tests/test_scenario_maps_kept.py`：
- 上傳 → 開新局 → 選劇本：地圖和它的地點都回來，也不會出現沒有樓層圖的提示。
- 重選同一個劇本會保留當下的地圖，並把它存起來。
- 還沒載入劇本時上傳的地圖，會併入第一個選的劇本，等待紀錄也會移除。
- 別的劇本不會拿到這張地圖；選回原劇本時會帶回來。
- 同檔名重傳會取代存好的地圖。
