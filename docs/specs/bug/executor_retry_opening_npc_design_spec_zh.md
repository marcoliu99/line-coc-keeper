# 多引用一條依據或太早 await 不再整回合重試、第一次開場照常演出、被搭話的 NPC 會回應、條件達成的觸發會發生

[English](executor_retry_opening_npc_design_spec.md)

狀態：**已實作**。基底：`main_v2` `707ad26`。

## 問題

來自 707ad26 的 2026-10-12 soak：

- **多一條引用就整回合重試。** Camp Sunny rerun5：裁決引用了 `state` 與 `scenario_context`，但這回合沒有查劇本，整份被判為 `invalid_evidence_reference`。
- **自動擲骰卻交回 await_check。** Camp Sunny rerun5 兩次：自動擲骰開著，守密人還沒擲骰就交回 `await_check`。沒有任何待擲檢定，所以判為 `pending_identity_mismatch`；通用重試提示也沒說該直接擲。
- **開場被跳過。** Haunting 第 1 回合：玩家第一句話重述了背景，敘事者「不要重演已發生的事」的規則讓它跳過了從沒演過的開場。
- **被搭話的 NPC 不回應，劇本寫的觸發從不發生。** 玩家對場上的 NPC 說話，只得到場景描寫；劇本寫「調查員做了 X 之後，Y 發生」，X 做了，Y 卻沒有開始。

## 改動

- **多餘的引用直接丟掉。** `validate_resolution` 在至少還有一條合法依據時，丟掉不存在的引用並記錄在 log；完全沒有合法依據仍然拒絕。後面的檢查照樣要求各種裁決該有的依據。
- **自動擲骰時叫它現在就擲。** 自動擲骰開著、沒有任何待處理、這回合也還沒擲骰時，`await_check` 判為 `pending_identity_mismatch` 的重試會說明：沒有檢定在等人擲，現在呼叫 `skill_check`／`sanity_check` 後交回 `resolved`，或交回 `no_mechanics`。
- **只有真的演過的才不重演。** 敘事者規則限定為對話紀錄或【過去記憶】裡已經演過的；還沒演過的（第一回合的開場、第一次抵達某地）照常完整演出。
- **兩條 prompt 規則：**
  - 放在 Executor 與 Narrator 都會讀的敘事節奏段落：玩家對在場的 NPC 說話，NPC 這則回覆就回應。可以閃躲、說謊、拒絕或反問，但不沉默，也不留到下一回合。
  - 只放在 Executor 指示（觸發可能需要戰鬥或檢定，只有 Executor 有工具）：查到的劇本段落顯示玩家處境已符合寫明的觸發條件時，這回合就用需要的工具讓事件發生；條件未滿足不提前。敘事後執行的義務閘，若這回合 Executor 已經處理過同類的理智損失或傷害，或已替同一位調查員擲過同一項強制檢定，就不再補一次，並記入帳本，之後的回合也不會重複套用同一條規則。

  不做場景時鐘，也不做字詞比對。

## 驗證

- `tests/test_turn_consistency_handoff.py`：
  - 合法依據旁的多餘引用被丟掉；只有一條不合法引用時仍拒絕；
  - 自動擲骰時太早交回 `await_check` 會收到「現在就擲」的回饋。
- `tests/test_narrative_boundary_prompts.py`：
  - 第一次開場的例外；
  - 兩條路徑都有 NPC 回應規則，觸發規則只在 Executor。
