# 改動了遊戲的回合不是等待、空白敘事仍送出該有的行、拿掉兩種重複

[English](empty_narration_and_deferral_design_spec.md)

狀態：**已實作**。基底：`main_v2` `4e63718`。

## 問題

2026-10-11 soak：

- **該做的都做了卻顯示「這次行動尚未完整處理」。** Haunting rerun3 第 31 回合：擲了理智、開了戰鬥，Executor 卻回答 `deferred`（等下一位）。因為這回合改動了遊戲，暫緩驗證不過；模型重試同樣失敗；56 秒後玩家看到警告，但其實沒有任何待處理。
- **Luck 回合顯示「守密人暫時無法完成敘事」。** Haunting rerun3 第 32、45 回合：Narrator 只回了決策 JSON，剪掉後什麼都不剩，整則被換成失敗訊息，玩家需要的 Luck 那行也沒了，也沒有記錄 fallback。
- **包裝尾巴黏著零散字。** Camp Sunny rerun4 第 52 回合結尾 `」}}.Begin`；原本要求字前面有空白。
- **擲骰指示講兩次。** Lightless 100 則回覆中 79 則，敘事已經給了指令，結尾又多一行「已有待處理檢定／選擇，請使用檢定按鈕或 /coc check。」

## 改法

- **改動之後的暫緩，就是那個改動。** `validate_resolution` 中，驗證不過的暫緩若符合：沒有任何人多出新的待處理、行動者沒有待處理檢定或 Luck、這回合結束時遊戲確實與開始時不同，就改判為 `resolved_without_check`，引用本回合改動遊戲的工具，並照一般完成回合的所有檢查再驗一次（其他角色未經驗證的物品變更仍會失敗）。不花模型重試。開了新檢定的暫緩照舊失敗。
- **空白敘事仍送出該有的行。** `_scrub_leaks` 記錄一次 `narration_failure` fallback、保留空白；後面步驟照常加上擲骰與 Luck 行；`_finalize` 只有完全沒東西可送時才給失敗訊息。
- **零散字可以緊接或隔個句點。** `_STRAY_WORD` 接受 `}}.Begin`、`}}Begin`，也照舊接受 `}} bheidh`。
- **一般擲骰行不再重複。** `InteractionRef.told_in`：回覆裡已有 `/coc check`（回覆自己的檢定行一定會帶）就視為已告知。

## 不改的

- Executor 原有的回饋分支保留，處理這次轉換沒涵蓋的情況。
- 空白敘事不另外重試 Narrator。

## 驗證

`tests/test_turn_consistency_handoff.py`（改動後的暫緩不重試、直接判為該改動；暫緩中的敵人傷害判為已處理；補回的彈藥消耗不會變成等待）；`tests/test_leaked_decision_and_redeclare.py`（空白敘事記錄並留給管線補行、只有全空才給失敗訊息；`}}.Begin`、`}}Begin`）；`tests/test_turn_fallback.py`（擲骰行不重複）。
