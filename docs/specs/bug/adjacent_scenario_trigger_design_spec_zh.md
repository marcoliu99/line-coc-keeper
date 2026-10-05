# 相鄰劇本觸發的檢索

[English](adjacent_scenario_trigger_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`bug`。狀態：**已實作**。來源：Camp Sunny 五人 500 回合驗證的 CS-002（〈Camp Sunny 5-Player 500-Turn Validation — Fix Specification〉的 Workstream A）。基於 `main_v2` 的 `aa79f22`。

玩家按了接待處的鈴。檢索回傳了接待處場景，卻沒帶到旁邊說明「鈴會發生什麼」的文字，於是 Keeper 自創了一個偵查檢定，沒有執行劇本寫好的後果。行動與其後果可能分在兩個約 400 字的 chunk，而且兩者和玩家的訊息沒有任何共同字詞。

## 契約

1. 文字索引的命中現在會帶上它明顯延續到的相鄰 chunk（`app/scenario_adjacency.py`，在 `scenario_rag._result_rows` 套用）。命中必須與查詢共享至少一個詞，並且符合下列其一：
   * 命中停在句子中間（`hit_ends_mid_sentence`）；
   * 下一個 chunk 在去掉 chunker 重複的重疊文字後，以條件或觸發語開頭，例如 若／如果／當／一旦／if／when／once（`next_opens_with_consequence`）；
   * 命中延續前一個 chunk（`hit_continues_previous`），或本身以條件開頭，所以點名觸發物的前一個 chunk 要放在前面（`hit_opens_with_consequence`）。
2. 擴充有上限：每側 `SCENARIO_RAG_ADJACENT_CHUNKS` 個（預設 1，0 表示關閉），每次搜尋最多四個，不跨過一頁以上，不取呼叫者看不到的 chunk，也不會只因為鄰居存在就加入。本身以條件開頭的命中不會再拉下一個條件（那是另一條規則）。鄰居之後的 chunk 不會被帶出。
3. 搜尋會回報 `adjacent_chunk_count`，被附加的結果列帶有 `adjacent_chunks`（方向、頁碼、原因）。
4. Executor 政策新增：劇本依據寫明玩家行動的直接後果時，照該後果處理，劇本沒有要求檢定就不得改以臨時的偵查、聆聽或幸運檢定取代；依據只點出觸發物、沒寫後果時，用「目前場景＋行動＋物件」做一次聚焦的 `search_scenario`，不要問「接下來會發生什麼」這類寬泛問題。

## 保持不變的契約

索引、embedding 與 v4 記錄庫路徑都沒動，所以既有索引不必重建。排名不變，只有回傳列的文字會變長。執行期程式沒有寫死任何劇本文字、名稱或觸發物。

## 執行方式

`tests/test_scenario_adjacency.py`：鈴／櫃檯人員的 fixture（接待處 → 劇本寫好的 NPC）、另一個劇本與語言的 fixture、不帶出鄰居之後的 chunk、不擴充自足的命中、不跨頁也不帶看不到的鄰居、關閉開關、鄰居本身是命中時不重複、政策文字，以及一道在執行期模組出現 Camp Sunny 劇本或其觸發物名稱時失敗的檢查。

## 未涵蓋

v4 記錄庫路徑依型別化的相依圖而非 chunk 順序取用，本變更不改它。Keeper 模型拿到後是否真的執行後果、而不是自創檢定，屬於模型行為；離線測試證明依據與政策有送到 Executor，無法證明模型的決定，那需要定向的真實執行驗證（本次未執行）。
