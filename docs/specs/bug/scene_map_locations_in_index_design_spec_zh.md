# 場景地圖的地點名稱要加進地點索引

[English](scene_map_locations_in_index_design_spec.md) | [文件索引](../../README_zh.md)

分類：`bug`。狀態：**implemented**。基準：`main_v2` 的 `37e16bd`。

## 問題

Markdown 劇本載入後地點索引是空的（劇本庫只保留有頁碼的條目，而沒有頁碼標記的文字，LLM 抽取會回報頁碼 0）。匯入 `map_*.yaml` 時地圖存進去了，它頂層的 `location_name` 就是地點名稱，但 `scenario_location_index` 還是空的；`/coc index` 也只從文字重建，從不讀 `scene_maps`。

## 修改

`scenario_index.merge_scene_map_locations(locations, scene_maps)` 回傳原索引，外加每個「索引裡還沒有（比對名稱或別名，`strip` 加 `casefold`）」的場景地圖 `location_name` 一筆 `{name, aliases: [], summary: "", page: 0, source: "scene_map"}`。既有條目不動，重複合併不會再加。上傳時若以同一個 key 覆蓋了舊地圖，為舊地圖名稱建立的那一筆會被移除（除非還有別張地圖用同名）；只有合併本身建立、標記為 `source: scene_map` 的條目會被移除，來自劇本文字的條目永遠不會。它在地圖匯入成功時呼叫（`map_service.handle_map_upload`，同一次狀態 commit；無效的地圖什麼都不改），也在 `scenario_lifecycle` 的修正或 `/coc scenario use` 保留地圖時呼叫（修正執行中的劇本會換掉索引、卻保留自訂地圖），以及 `/coc index` 通過編號標題檢查之後呼叫，所以地圖不會掩蓋文字抽取不完整的問題（[不完整的地點索引重建結果不會取代有效的索引](index_location_underflow_design_spec_zh.md)）。

## 不做

房間名稱不算地點；不做模糊比對、翻譯、從地圖建立 NPC，也不新增 LLM 呼叫；地圖、RAG、劇本庫其他部分都不變。

## 測試

`tests/test_scene_map_location_index.py`：空索引、既有條目不動、別名比對、重複合併、有效與無效的地圖匯入、`/coc index` 合併地圖，以及地圖不能掩蓋不完整的文字抽取。
