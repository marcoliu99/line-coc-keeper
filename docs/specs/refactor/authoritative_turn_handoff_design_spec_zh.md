# 權威回合交接

[English](authoritative_turn_handoff_design_spec.md)

狀態：**進行中**。基準：`main_v2` 的 `07d55a7`。

## 目前邊界

`tool_gateway` 記錄單次工具收據，`turn_resolution` 驗證 Executor 的完成裁決，`supervisor` 則在呼叫 Narrator 前重新判斷等待者與 Luck 優先序。`turn_context` 另向 Executor 提供當前 state。最後的交接編排仍留在 `supervisor`，容易把舊工具收據誤當目前等待選項。

## 介面

`app/services/turn_handoff.py` 依已驗證裁決、觀察結果、執行前快照與最新權威 `GroupState`，整理 Narrator 使用的 `MechanicResult.check_status`。它將裁決中的角色識別對應到玩家、逐位調查員選取新建或既有的 pending、讓同一等待者的 Luck 優先於未擲檢定，並附上完整的 `current_turn_state`。未變動 pending 的直接回覆也由此模組負責。`supervisor` 在 Executor 後只呼叫一次此介面，不再自行重建資料。

`turn_resolution` 仍負責證明完成及拒絕錯誤身分。交接不會只憑模型宣稱的完成狀態、不修改遊戲 state、不重放工具，也不把私有對抗資訊變成公開文字。保留現有工具觀察、部分完成語意、當前 state 投影及發送前過濾；Executor 執行前的 context 仍是另一個輸入視圖。

## 驗證

直接測試此介面：新建其他玩家檢定、既有玩家檢定、Luck 優先序、含舊 pending 的已驗證物品交接，以及無效／未完成裁決。保留 supervisor 整合測試，並執行完整 pytest、Ruff 0.16.8、mypy、compileall。
