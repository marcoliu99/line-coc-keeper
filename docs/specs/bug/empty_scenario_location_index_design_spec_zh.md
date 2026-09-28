# 劇本地點索引為空不得無聲失敗

[English](empty_scenario_location_index_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `c340984`。

## 問題與證據

一個五人調查員隊伍玩了二十五個回合，從未離開起始房間。每一次前往樓梯的嘗試都被拒絕——`我往樓梯口走過去`、`我沿著樓梯慢慢往上走`、`我走進一樓的走廊`、`我推開最近的那扇門`——而敘事卻一直在描述那道樓梯。第 19 回合甚至明說：「沒有看見其他門窗或明顯出口；唯一可辨認的通路，是你們來時那段年久失修的樓梯。」二十五回合中有十四個被拒絕，其中一個的驗證碼是 `arrival_not_committed`。

敘事本身沒有問題：場景細節二十五回合一致、沒有憑空生成房間、攜帶物品持續、同伴被正確認知。**只有移動做不到。**

成因是劇本資料，不是程式。該 group 的狀態帶著：

```text
scenario_location_index: []     scene_maps: {}
current_room_id:         {}     current_location: null
```

而它載入的劇本庫裡，同一份劇本有兩個變體：

| 變體 | `indexes.json` |
| --- | --- |
| `the-haunting-scenario-trimmed-70dbe2a5` | npcs 2、locations 8 |
| `…-ai-950721dc39db75cf`（實際在用的）| **`{}`** |

到達是對著「已知目的地」提交的，因此沒有任何地點被索引時，什麼都無法核對，每一次移動都會被拒絕。劇本的**文字**自始至終完好——檢索找得到樓梯、木板牆、甚至從接縫滲出的氣味——**這正是為什麼這個故障看起來像模型問題而不是資料缺失。**

## 真正擋住移動的是什麼

`app/services/movement.py` 透過 `scene_map.resolve_move(state.scene_maps, ...)` 與 `scene_map.find_room_by_text(state.scene_maps.get(page, {}), ...)` 解析目的地,**完全沒有讀 `scenario_location_index`**。**真正拒絕每一次移動的是空的樓層圖**;空的地點索引只是讓守密人反覆重新推導 NPC 與地點事實,那是另一個、較輕的問題。

這次兩者都空,而 AI 準備版掉的不是一樣而是三樣:

| 變體 | `indexes` | `scene_maps` | `pregens` |
| --- | --- | --- | --- |
| 原始版 | npcs 2、locations 8 | 1 張樓層圖 | 4 |
| `…-ai-…`(實際在用)| `{}` | `{}` | `[]` |

**抽取沒有問題。** 對 AI 版自己的文字執行 `scenario_index.extract_scenario_index`,回傳 2 個 NPC、9 個地點,包含「舊科比特宅邸」及其別名。文字完好,是發布路徑從未呼叫抽取。

## 這是規格行為,不是疏漏

`scenario_source_authoring.py` 與 `scenario_source_review.py` 寫入 `('indexes', {}), ('pregens', []), ('scene_maps', {})` 並記錄 `derived_artifacts: 'invalidated: …'`。`docs/specs/enhancement/external_english_source_preparation_design_spec_zh.md` §7 明文要求:「舊 embedding、NPC 索引、pregens、推測地圖與中文版本失效,**不能複製舊值到新來源**」。測試也守著它,在父劇本放入 `{'1': {'old': True}}` 來證明重新發布不會沿用。

本次修正的**第一版曾經把樓層圖改成沿用**,理由是它衍生自頁面圖像、而重新發布保持 PDF 位元相同。**那個理由搞錯了重點,改動已撤回**:作廢針對的是出處,不是新舊——vision 模型的推測不得未經重新衍生就跨進一個經過審計的來源。

**缺口在於:規格作廢了它,卻沒有提供回去的路。** `scene_maps` 只能經由 PDF 上傳的 vision 流程或劇本庫 context 進到團隊,**沒有任何指令能重建它**;`/coc index` 重建索引但不重建地圖。因此重新發布過的來源在重新上傳 PDF 之前,移動是玩不了的,而先前沒有任何地方說出這件事。

## 範圍

在劇本被安裝或切換的時點讓這個狀況可見。本工作**不修復任何產物、不沿用任何產物、也不放寬到達檢查**。

`scenario_index.report_location_index` 在四個指派索引的位置各記錄一次數量——PDF 上傳、劇本更正、劇本庫安裝、章節切換——為空時以 WARNING、否則 INFO，並把提示字串回傳給有回覆管道的呼叫端。PDF 上傳確認訊息會顯示它，且**取自安裝後的 state 而非 extracted index**，因為劇本庫變體會提供自己的索引。

`None` 與 `0` 有別：沒有查過的呼叫端不得被讀成「查過而且沒有」。

## 測試

- 空索引出現提示、有索引不出現；兩種情況都記錄數量與來源，僅在為空時提高層級。
- 劇本標題經 `observability.safe_identifier` 雜湊，不直接外傳。
- 上傳確認在 0 時帶提示、8 時不帶、`None` 時保持沉默。
- **以兩個真實劇本庫變體驗證**：原始版回報 8 個地點且無提示，AI 準備版回報 0 個且有提示。

變異驗證：讓空索引不回傳提示會使測試失敗。

## 限制

這只說明「這份劇本無法支援移動」，**不說明 AI 準備流程為何沒產出索引，也不修復它**。兩者仍未解決。已經在遊玩受影響劇本的團隊，在重新匯入劇本或切換章節之前不會看到任何新訊息——提示在索引被指派處觸發，不是每回合都檢查。
