# 內部決策 JSON 洩漏給玩家，以及已行動完的回合被回報成 deferred

[English](leaked_decision_json_and_redeclared_turn_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `0908a16`。

## 問題

2026-10-11 的 soak 還有兩件事會傳到玩家面前。

**1. 回覆裡出現內部決策物件。** Scritch Scratch 第 91 回合，玩家收到的回覆開頭是守密人自己的回合決策 JSON（含 `"disposition"`、`"reason"` 與工具證據），後面才是敘事。log 看不出是哪個階段產生的，所以無法從源頭修。

**2. 已行動完的角色又被宣告一次攻擊。** Camp Sunny 出現三次：守密人正確地拒絕替已行動完的角色再宣告攻擊、呼叫了 `advance_combat_turn`，卻把這一回合交成 `deferred`。推進戰鬥是已提交的變更，驗證因此拒絕 `deferred`；通用的重試提示（「補上缺少的證據」）沒說要改什麼，玩家最後只看到警告句而不是敘事。

## 變更

- **決策物件在任何步驟讀到之前就移除。** 回覆流程新增第一步 `scrub_leaks`：把草稿中含 `"disposition"` 鍵的 JSON 物件整段移除，連同因此變空的程式碼圍欄，並整理多餘空行。只移除含該鍵的物件，不論在敘事之前、之間或之後；其他大括號、程式碼或 JSON 不動。它在一致性修補之前執行，後面的步驟都不必看到它，並登記在流程的順序規則裡。整則回覆只有該物件時，改為敘事失敗的提示句（「守密人暫時無法完成敘事…」），不會送出空白。
- **重試明確告訴守密人怎麼做。** 當守密人已推進回合（`advance_combat_turn` 改變了遊戲狀態，且不是卡在待處理檢定）卻回答 `deferred`，唯一的最後重試會說：推進已提交，改交 `resolved_without_check` 並引用它；敘事說清楚這位玩家這一輪已行動完畢（不重複他的攻擊），以及接著輪到誰。
- **拒絕訊息也這樣說。** 因回合已完成而拒絕宣告時，訊息現在會指出下一步（用新的 event id 推進）與回報方式（`resolved_without_check`，不是 `deferred`）。

## 不變更

- 已有提交變更後仍不允許 `deferred`；改成告訴守密人正確的答法。
- 不去追是哪個階段吐出該物件：成因未證實，在送出前移除可保護所有路徑。

## 驗證

- `tests/test_leaked_decision_and_redeclare.py`：物件不論在敘事之前、之間、之後、巢狀、在程式碼圍欄內或重複出現都會被移除；不含 `"disposition"` 的大括號保留；流程步驟會移除它；「已推進回合」判斷只對有改變遊戲狀態的 `advance_combat_turn` 為真。
- `tests/test_reply_pipeline.py`：實際順序以 `scrub_leaks` 開頭，順序規則成立。
- `tests/test_combat_flow.py`：回合已完成後再宣告，訊息會指出推進與 `resolved_without_check`。
