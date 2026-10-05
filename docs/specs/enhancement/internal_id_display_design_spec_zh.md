# 空的內部 id 標籤不能露給玩家，移除標籤時也不能吃掉後面的句子

[English](internal_id_display_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `7782690`。

## 問題

Dead Boarder 200 回合測試（`NARRATION_OUTSIDE_MUTATION_LOCK=true`，`1fd3ccb`）顯示，待處理檢定的回覆會露出空的 `check_id` 欄位（turn 40、48、100）。`presentation.player_text` 會移除 `check_id=<id>` 與其他內部 id 標籤，但它的樣式要求標籤後面至少有一個字元，所以 `（check_id=）` 原封不動留了下來。我在 `main_v2` 上重現過：`檢定已建立（check_id=）請擲骰` 原樣傳回。

檢查同一個樣式時還發現相反方向的缺陷：值的字元範圍是 `\w`，而它會比對到中文字，所以 `check_id: 請擲骰`（沒有 id、沒有空格）整段被移除，連文字都沒了。

## 變更

- 標籤連同後面的值一起移除，**包含沒有值的情況**。`（check_id=）請擲骰` 變成 `請擲骰`。
- 值限制為 ASCII（`A-Za-z0-9_.:-`），引擎寫出的每種 id 都由這些字元組成。值在第一個非 ASCII 字元就停止，所以 `check_id: 請擲骰` 變成 `請擲骰`，`check_id=check-<hex>請擲骰` 變成 `請擲骰`。
- 分隔符號之後、右括號之前的空白只算空格與 tab。行尾的空標籤不會跨過換行去吃下一行的第一個字（`event_id=` 換行 `See below` 保留 `See below`）。
- `DEBUG_SHOW_INTERNAL_IDS` 仍然完整顯示。

其他不變：裸 id 的樣式、等級名稱的對應、`player_text` 的冪等都和以前一樣。

## 沒做的

- 空標籤從哪裡來。待處理檢定的回覆由 Narrator 或 Keeper 文字依工具輸出寫成；空的 `check_id` 代表欄位有出現但沒有值，這份規格沒有追到來源。顯示端已在每則回覆必經的地方修好；寫出空欄位的產生端仍值得找。
- 移除帶括號的標籤時，可能在兩個有空格的詞之間留下兩個空格。這是既有行為，只影響外觀。

## 驗證

`tests/test_presentation.py`：括號中的空標籤、冒號之後、句尾與行尾的空標籤；id 後面直接接中文；帶括號的空標籤不留下標籤或空括號；除錯時仍顯示空標籤。`ruff check .` 與完整 `pytest` 通過。
