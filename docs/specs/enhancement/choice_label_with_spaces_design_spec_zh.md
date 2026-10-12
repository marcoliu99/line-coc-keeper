# 選項標籤要整句輸入，空格也算

[English](choice_label_with_spaces_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `9666a86`。來自 2026-10-12 Camp Sunny rerun7（第 26 輪）：一個選擇的選項是「以 STR …」和「以 SIZ …」，有人回的是整個標籤，解析卻把選項讀成「以」。

## 問題

`/coc check <選項>` 只讀整行的第三個字。選項是以標籤列給玩家看的，而標籤可以有空格（「以 STR 抓住」），所以玩家照著讀到的輸入，得到的是「沒有「以」這個選項」；代為執行（`sudo`）的檢定只要超過一個字就直接被拒。

## 變更

`narration.match_typed_choice(options, words)` 先把 `/coc check` 後面的字當成一整句去比對選項，再退回只用第一個字。它用在舊式的待處理選擇（`app/checks/service.py`）、戰鬥的選擇（`ManagedChecks._choose`），以及 `app/commands/router.py` 裡 `sudo` 檢定的比對（只限選擇）。只打第一個字仍然有效（「/coc check STR」，或後面還有其他字的「/coc check 閃躲 謝謝」）；一個字對到好幾個選項（「以」）仍然等於沒對到，會再問玩家一次。對不到時，「沒有「…」這個選項」會完整顯示玩家打的整句。待處理的技能或理智檢定仍然只讀第一個字。

## 驗證

`tests/test_choice_label_with_spaces.py`：整句和第一個字都對到正確的選項，單獨一個「以」對不到；打帶空格的標籤能解決舊式選擇；對不到的標籤會整句顯示、選擇保持待處理；代為執行的檢定可以帶整個標籤，技能檢定仍不能帶多餘的字。沒有這個變更時，服務層的測試會失敗。
