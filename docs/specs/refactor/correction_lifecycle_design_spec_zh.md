# 更正生命週期

[English](correction_lifecycle_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**。基於 `main_v2` 的 `ee23aa9`；2026-10-05 架構審查的第三項（「集中更正權限轉換」）。權限規則見 [ADR 0001](../../adr/0001-keeper-adjudicates-corrections-without-kp.md) 與 [ADR 0002](../../adr/0002-separate-presentation-from-world-authority.md)。

玩家的更正是一個主張；只有 KP 助手或 Keeper 的裁定，才會讓它變成遊戲會依據的事實。報告的狀態（`pending`、`unverified`、`approved`、`rejected`、`withdrawn`、`superseded`）原本在四個地方被修改：裁定在 `narrative_corrections`，但 `withdrawn`、`superseded` 與暫停範圍卻直接寫在 `/coc correct` handler 裡；報告也被組了兩次（指令與自然語言路徑，欄位順序不同）；handler 還有「找報告」「這條時間線的報告」「修剪已結案報告」的私有副本。讀的人得自己跨模組拼湊「一個主張什麼時候變成權威」。

## 契約

現在由 `narrative_corrections` 擁有對報告的每一個改動：

| 操作 | 轉換 |
| --- | --- |
| `new_report`、`file_report` | 建立目前時間線的 `pending` 報告並歸檔（同時丟掉非目前時間線的報告） |
| `record_unverified` | `pending` → `unverified`（Keeper 無法證實） |
| `record_ruling` | `pending`／`unverified` → `approved` 或 `rejected`（KP 助手或 Keeper；Keeper 的帶有依據） |
| `record_presentation_repair` | `pending` → `approved`，用於不會升格為世界設定的無害呈現更正 |
| `withdraw` | 開啟中的報告 → `withdrawn` |
| `supersede` | `approved` 的報告 → 被另一個已核准的取代（`superseded`）；標記日誌並排程摘要重建 |
| `hold` | 標記要暫停行動的名稱；從不改變狀態 |
| `prune_closed`、`find_report`、`active` | 限制狀態大小；在目前時間線內查找 |

允許的轉換是一張表（`TRANSITIONS`）：`pending → unverified／approved／rejected／withdrawn`、`unverified → approved／rejected／withdrawn`、`approved → superseded`；rejected、withdrawn、superseded 是終點。表裡沒有的轉換會丟出例外，而不是被套用。

handler 與 `natural_corrections` 只負責解析、檢查權限與上限、組回覆。

## 維持不變

1. 指令名稱、回覆文字、上限（`/coc correct` 為每群 12 筆待處理、每位提報者 3 筆；自然語言更正為 10 與 3，目前就是不同的）與報告的儲存形狀都不變；報告欄位相同，只是改在同一處組出。
2. 權限規則不變：指控本身不會造成機械性暫停、呈現更正不是世界事實、核准之後由 `save` 發布（封存、記憶標註、重設 provider 鏈）。
3. 唯一被移除的是一行多餘的 `report["status"] = "pending"`（物品修補失敗後，報告本來就是 pending）。
4. 既有的更正測試（`test_narrative_correction_command`、`test_narrative_correction_lifecycle`、`test_natural_corrections`、`test_keeper_adjudicates_corrections`）全部通過；其中兩個改為呼叫 `narrative_corrections.prune_closed`，而不是 handler 的私有函式。

## 守門

`tests/test_correction_lifecycle.py` 對照表檢查每一組狀態配對與每個操作。`tests/test_architecture_corrections.py` 在以下情況失敗：處理更正報告的模組自己指派 `["status"]`、自己組報告 dict，或 handler 又長出這些輔助函式的私有副本。

## 未更動

Keeper 的裁定（`correction_adjudication`：證據、主張檢查、劇透過濾）、已驗證事實的投影（`canonical_facts`）與摘要重建（`correction_summary`）各自保留自己的模組。上面兩組上限數值維持原樣；把它們統一成一個政策會改變行為。
