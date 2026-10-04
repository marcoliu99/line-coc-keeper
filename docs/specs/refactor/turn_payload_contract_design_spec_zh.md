# 回合交接契約

[English](turn_payload_contract_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**。基於 `main_v2` 的 `b0e875c`（2026-10-05）；2026-10-05 架構審查的第二項（「讓 Keeper 回合交接變明確」）。

一個玩家回合會經過 Supervisor、Executor、Narrator 與 delivery，它們互相交接的東西，原本放在兩個沒有型別的 dict：`AgentMessage.payload`（`dict[str, Any]`，約 25 個 key，在六個模組讀寫）與 `MechanicResult.check_status`（`dict[str, Any]`，十個 key，由四個模組寫入）。各階段的順序與各自需要的證據是隱含的介面：key 打錯字只會默默讀到預設值，也沒有任何地方說明哪個階段可以加哪個 key。

## 契約

`app/domain/models.py` 現在把兩者宣告成 `TypedDict`，並標明每個 key 的擁有者。

| 型別 | Key | 寫入者 |
| --- | --- | --- |
| `TurnPayload` | `conversation_id`、`user_id`、`display_name`、`speaker_role`、`text`、`resolved_location`、`state`、`character`、`combat_provisional`、`resolved_check_events`、`rag_context`、`memory_context`、`rag_status`、`memory_status`、`correction_context` | `context_builder`，建立 payload 時一次寫入 |
| | `turn_kind`、`intent`、`resolved_check_context`、`mechanic_result` | `supervisor` |
| | `private_messages`、`image_requests`、`observed_outcomes` | `executor`（`narrator` 會往 `observed_outcomes` 追加） |
| | `narration_requirements`、`narration_failed` | `narrator` |
| | `delivery_envelope` | `turn_delivery.finalize` |
| `CheckStatus` | `tool_called`、`pending`、`pending_luck`、`resolved`、`scenario_evidence_blocked`、`cleared` | Executor 的工具，經 tool gateway |
| | `tool_event_count`、`state_changed`、`dice_rolled` | `executor`，工具跑完之後 |
| | `pending`、`pending_luck`、`resolved`、`waiting_for_name`、`current_turn_state` | `turn_handoff.prepare_narrator_handoff`，依最新狀態 |
| | `pending`、`pending_luck`（清除） | `turn_delivery.public_mechanic`，作用在副本上 |

`PlayerTurnKind` 與 `SpeakerRole`（`"player"` 或 `"kp_assistant"`，原本是工具 registry 裡的 `Literal`）放在 payload 旁邊，型別才能引用它們；`supervisor.run_turn`、`context_builder.build_context` 與 router 裡的區域變數 `speaker_role` 都改用 `SpeakerRole`，所以拼錯的角色不會再通過 mypy、默默被當成玩家處理。`mypy app` 現在會拒絕未知的 key、型別不對的值，以及不是字串字面值的 key。`tool_gateway` 與 `turn_delivery.public_mechanic` 裡兩處用變數當 key 的寫入，改寫成字面 key，效果相同。

## 維持不變

沒有任何執行期行為改變：只有型別標註、那兩處改寫，以及庫存查詢上的一個 `isinstance` 防護；整個測試套件不用修改就通過。各角色的權限和以前一樣：Executor 裁決、交接模組讀最新狀態；Narrator 不寫檢定事實；delivery 對「什麼能顯示」有最後決定權。

## 守門

`tests/test_turn_payload_contract.py` 在以下情況失敗：宣告的 key 沒有提供者或有兩個、`context_builder` 建立的輸入 key 集合不同、或擁有者以外的模組寫入 payload key（`message.payload[...] = ...`、`setdefault`、`update`、`pop`），包含用變數當 key 的寫入。`CheckStatus` 套用同樣的檢查：每個 key 都要有負責寫入的階段，其他模組的寫入（下標、`update`，或建立它的 dict 字面值）一律失敗。

## 未更動

Narrator 需要的事實仍由 `turn_handoff` 準備；`AgentMessage.payload` 仍是 mapping，改成 dataclass 會動到約五十個測試 fixture，是另一個變更。各角色的權限（ADR 0002）沒有動。
