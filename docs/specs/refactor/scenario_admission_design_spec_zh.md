# 劇本載入與啟用

[English](scenario_admission_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**。基於 `main_v2` 的 `f88135d`（2026-10-05）；2026-10-05 架構審查的第一項。

一份劇本庫的劇本有四個入口進到對話：PDF 上傳、Markdown 上傳、遊戲進行中上傳後的「全新劇本／修正目前劇本」選擇，以及 `/coc scenario use`。每個入口都重複同一份知識：哪些待處理狀態會擋住載入、怎麼讀舊劇本的內容雜湊、*安裝章節 → 退場舊角色卡 → 安裝新卡池 → 提交 → 發布頁面圖片* 的順序，以及後面接的提示文字。這個順序要改的時候（例如加入 Markdown 入口那次），每一份都要改。

現在由 `app/services/scenario_admission.py` 擁有這個順序。入口只提供準備好的劇本庫項目和自己的措辭，狀態轉換由這個模組負責。

## 介面

| 函式 | 做什麼 |
| --- | --- |
| `pending_block(state, include_similar=True)` | 為什麼現在不能載入劇本：`pregen_luck`、`upload_choice` 或 `similar_upload`（依各入口檢查的順序），或 `None`。措辭由入口決定。 |
| `content_hash(scenario_id)` | 劇本庫項目已存的內容雜湊；讀不到時為 `""`。 |
| `admit_upload(...)` | 在對話鎖內：`stale_revision`、`raced`（已有一個選擇在等）、`needs_choice`（遊戲進行中；上傳被存進 `pending_pdf_upload`）或 `activated`（第一份上傳，立即套用）。 |
| `activate_pending_choice(...)` | 把存起來的上傳當成全新劇本或目前劇本的修正來處理。 |
| `activate_selected(...)` | `/coc scenario use`：新的戰役脈絡，保留現有調查員。 |
| `commit_activation(...)` | 退場舊角色卡、安裝新卡池、透過 `state_transaction` 提交，然後才發布頁面圖片。回傳帶兩個提示（`image_note`、`card_note`）的 `Activation`。 |
| `apply_new_scenario`、`apply_scenario_correction`、`apply_scenario_use`、`install_library_context`、`replace_scene_maps_preserving_locations` | 狀態轉換本身，原樣搬到這裡。 |

`scenario_activation` 仍是底下那一層（複製欄位、提交後發布圖片）。`scenario_ingestion` 和 `/coc scenario` handler 是 adapter：解析、檢查權限、抽取、組回覆。推進章節（`advance_scenario_chapter`）保留角色卡和地圖、以工具 mutation 提交，所以仍自己呼叫 `install_context_fields` 與 `refresh_after_commit`。

## 維持不變

1. 玩家看得到的文字沒有改：每則訊息、順序與後綴都相同（選擇的回覆仍把角色卡提示放在版本提示之前，上傳回覆則放最後）。
2. 抽取、劇本庫、Keeper 權限、狀態交易邊界與儲存內容都沒動。寫入仍用 `commit_snapshot`，頁面圖片仍在提交之後才發布。
3. `tests/test_ingestion_trace.py` 把首次上傳、相似重傳、角色卡、Markdown 上傳、全新／修正選擇與 `/coc scenario use` 對照**重構前**錄下的 trace；輸出完全相同。

## 守門

`tests/test_scenario_admission.py` 在真實 SQLite 上涵蓋 `admit_upload` 的每種結果、兩種選擇、`/coc scenario use` 與圖片刷新失敗。`tests/test_architecture_scenario_admission.py` 在以下情況失敗：載入模組以外有人呼叫 `commit_and_refresh`、`install_context_fields` 被載入模組和章節推進以外的地方呼叫，或搬走的轉換又出現私有副本。

## 未更動

角色卡上傳仍自己安裝角色卡池，章節推進也保留自己的轉換；兩者都與上面四個入口不同。同一份審查的 PDF 頁面決策、更正權限與劇本儲存三項是各自獨立的變更。
