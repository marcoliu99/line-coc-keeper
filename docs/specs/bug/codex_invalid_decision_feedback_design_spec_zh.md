# Codex 的決定被拒時，告訴模型哪裡錯，日誌也記下是哪個欄位

[English](codex_invalid_decision_feedback_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `0b203bc`。發現於 2026-10-14 Haunting 隨機 100 輪（第 29 輪）。

## 問題

這局第一場戰鬥：執行器查了敵人、替四位調查員擲了理智檢定（已提交）、又查了武器，然後送出的決定被主機判為 `invalid_schema`。主機唯一的修復提示只說「請回傳符合實際工具與 input_schema 的決定」，模型就送出幾乎一樣的決定（1326 與 1333 位元組），再被拒一次，整回合以內部錯誤收場，共 56.8 秒，其中兩次被拒的請求佔 27.8 秒。玩家沒有得到回覆，戰鬥晚一回合才開始（又花 32 秒），而日誌也說不出是哪個欄位錯：拒絕事件只有 `reason=invalid_schema`。

## 變更

`codex_provider.run_conversation`，只改拒絕的那條分支：

- 驗證器自己的訊息與出錯欄位的路徑（例如 `arguments.enemies.0.source`；`arguments` 表示是工具自己的參數，而不是決定外層）以 `validation_error` 放進修復提示（訊息截到 300 字），並要求模型修正它指出的地方。非格式的失敗（JSON 壞掉、重複鍵、空的最終回覆）給錯誤類別與文字；
- `codex.decision.rejected` 事件多了 `error_type`、`path`、`validator`（欄位與規則，絕不含模型的內容）；
- 其他都不變：仍然只修復一次，之後才 `codex_invalid_decision`；被拒的決定不會執行任何工具。

第 29 輪被拒的決定，是不是 `initialize_combat` 的選填 `source` 少了五個必填欄位之一，是從格式推測的，原始輸出沒有記錄。新的欄位之後會直接說明。

## 驗證

`tests/test_codex_provider.py`：缺必填參數的決定，重試拿到帶路徑與「required property」訊息的 `validation_error`；型別錯誤指出 `arguments.target` 與規則 `type`，壞掉的 JSON 指出錯誤類別；日誌事件帶路徑、規則與錯誤類別，不含訊息。這三個測試在沒有這項變更時都會失敗。
