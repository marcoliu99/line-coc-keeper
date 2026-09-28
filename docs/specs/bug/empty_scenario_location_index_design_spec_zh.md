# 劇本衍生產物為空不得無聲失敗

[English](empty_scenario_location_index_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `c340984`。

## 修訂紀錄

本規格的第一版宣稱「空的樓層圖拒絕了那二十五回合裡的每一次移動」。**這個因果是錯的,已於本版撤回**,理由與取代它的證據見下節。被保留的是另一件經查證的事實:重新發布的來源會把 `indexes`、`scene_maps`、`pregens` 全部作廢,而且沒有任何指令能把樓層圖找回來。

## 問題與證據

一個五人調查員隊伍玩了二十五個回合,從未離開起始房間。該 group 的狀態帶著:

```text
scenario_location_index: []     scene_maps: {}
current_room_id:         {}     current_location: null
```

而它載入的劇本庫裡,同一份劇本有兩個變體,實際在用的那個三樣衍生產物全空:

| 變體 | `indexes` | `scene_maps` | `pregens` |
| --- | --- | --- | --- |
| `the-haunting-scenario-trimmed-70dbe2a5` | npcs 2、locations 8 | 1 張樓層圖 | 4 |
| `…-ai-950721dc39db75cf`(實際在用)| `{}` | `{}` | `[]` |

**抽取沒有問題。** 對 AI 版自己的文字執行 `scenario_index.extract_scenario_index`,回傳 2 個 NPC、9 個地點,包含「舊科比特宅邸」及其別名。文字完好——檢索找得到樓梯、木板牆、甚至從接縫滲出的氣味——是發布路徑從未呼叫抽取。

## 那二十五回合到底發生了什麼

重新分析那份 log,得到的數字與第一版寫的完全不同:

| 事實 | 次數 |
| --- | --- |
| `commit_movement` 被呼叫 | **4**(全程 25 回合) |
| 失敗於 `movement_evidence_missing` | 3 |
| 失敗於 `passage_blocked` | 1 |
| 任何地圖解析失敗碼(`movement_destination_mismatch`、`known_map_location_requires_path` …)| **0** |
| 回合以 `incomplete` / `arrival_not_committed` 結束 | 1 |
| 回合以 `model_incomplete` 結束 | 8 |

第一版寫的「二十五回合中有十四個被拒絕」是把模型自己沒走完的回合算成了移動拒絕。實際上**移動只被嘗試過四次**,而四次都失敗在讀地圖之前:

- `movement_evidence_missing`(`app/services/movement.py:251`)——引用的原文不是來源的逐字子字串,或根本沒給。這道關卡在 `_validate` 的最前面,不碰 `scene_maps`,也不碰 `scenario_location_index`。
- `passage_blocked`(`app/services/movement.py:259`)——模型自己把 `conditions` 設成 `blocked`。同樣在地圖查詢之前。

也就是說:**隊伍卡住的直接原因是執行階段沒能提出合格的到達,不是樓層圖為空。**

## 空的樓層圖實際造成什麼

`app/services/movement.py` 透過 `scene_map.resolve_move(state.scene_maps, ...)` 與 `scene_map.find_room_by_text(...)` 解析目的地,**完全沒有讀 `scenario_location_index`**——那份索引只進提示詞。

而且沒有樓層圖時移動**並不會**一律被拒。`_validate` 的無圖分支(`app/services/movement.py:342-351`)接受不在任何地圖裡的目的地,只要求 `path` 為空、該地點不屬於任何已知地圖,且目的地本身有劇本原文支持;`tests/test_turn_routing_and_movement.py:196` 的 `test_sr_m07_mapless_supported_arrival_needs_no_new_map` 直接把 `scene_maps` 設成 `{}` 並斷言到達成立。

因此正確的說法是:

- 沒有樓層圖 → **失去的是已知房間之間的路徑移動**(`path` 必須為空,已知地點必須走路徑),有原文依據的到達仍然成立。
- 沒有地點索引 → **失去的是提示詞裡的地點對照表**,守密人會重複推導同一個地點的細節。兩者都不會讓移動一律失敗。

## 這是規格行為,不是疏漏

`scenario_source_authoring.py` 與 `scenario_source_review.py` 寫入 `('indexes', {}), ('pregens', []), ('scene_maps', {})` 並記錄 `derived_artifacts: 'invalidated: …'`。`docs/specs/enhancement/external_english_source_preparation_design_spec_zh.md` §7 明文要求:「舊 embedding、NPC 索引、pregens、推測地圖與中文版本失效,**不能複製舊值到新來源**」。測試也守著它,在父劇本放入 `{'1': {'old': True}}` 來證明重新發布不會沿用。

本次修正的**第一版曾經把樓層圖改成沿用**,理由是它衍生自頁面圖像、而重新發布保持 PDF 位元相同。**那個理由搞錯了重點,改動已撤回**:作廢針對的是出處,不是新舊——vision 模型的推測不得未經重新衍生就跨進一個經過審計的來源。

**缺口在於:規格作廢了它,卻沒有提供回去的路。** `scene_maps` 只能經由 PDF 上傳的 vision 流程或劇本庫 context 進到團隊,**沒有任何指令能重建它**;`/coc index` 重建索引但不重建地圖。

## 範圍

在劇本被安裝或切換的時點讓這個狀況可見。本工作**不修復任何產物、不沿用任何產物、也不放寬到達檢查**,也**不主張**這能解掉上述那二十五回合。

`scenario_index.report_location_index(locations, *, source, scenario_title, scene_maps)` 在四個指派衍生產物的位置各記錄一次——PDF 上傳、劇本更正、劇本庫安裝、章節切換——為空時以 WARNING、否則 INFO,並把提示字串回傳給有回覆管道的呼叫端:PDF 上傳確認、章節切換的工具結果、`/coc scenario use` 的回覆。呼叫端**照抄**回傳值,不自行判斷,否則就會重演「只看了索引、沒看見樓層圖也是空的」這個疏漏。

兩份提示各自只講自己造成的損失,不得宣稱移動會被拒絕。

## 測試

- 空索引 / 空樓層圖各自出現對應提示,兩者皆空時兩段都出現;有值時不出現。數量與來源兩種情況都記錄,僅在為空時提高層級。
- **兩份提示都不得包含「會被拒絕」**——直接對著 `test_sr_m07_mapless_supported_arrival_needs_no_new_map` 所證明的行為把假診斷釘死。
- 劇本標題經 `observability.safe_identifier` 雜湊,不直接外傳。
- 上傳確認照抄回報器的字串;空字串時保持沉默。
- 以兩個真實劇本庫變體**手動**核對過一次:原始版回報 8 個地點、1 張樓層圖且無提示,AI 準備版兩者皆 0 且兩段提示都有。這不是測試——劇本庫不在 repo 裡。

變異驗證:讓空索引不回傳提示會使測試失敗。

## 限制

這只說明「這份劇本缺了哪些衍生產物、因而少了什麼能力」,**不說明 AI 準備流程為何沒產出它們,也不修復它們**,更**不解釋那二十五回合為何卡住**——依上面的證據,那是執行階段的證據品質問題,屬於另一件事。已經在遊玩受影響劇本的團隊,在重新匯入劇本或切換章節之前不會看到任何新訊息——提示在產物被指派處觸發,不是每回合都檢查。
