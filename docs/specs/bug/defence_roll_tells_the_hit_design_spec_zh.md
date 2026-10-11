# 防守骰之後那一行要說出被打中、傷害與重傷

[English](defence_roll_tells_the_hit_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c85efbd`。

## 問題

Haunting 2026-10-11 rerun2 第 41 回合：Corbitt 的匕首攻擊 Clara，她選反擊、骰 96，攻擊命中，她掉了 12 點裡的 9 點 HP（重傷），引擎替她擲了 CON 檢定（autoroll，過了）。她在 `/coc luck skip` 之後讀到的是「Clara 的反擊落空，拳頭只掠過 Corbitt 乾枯的身影…現在輪到 George Finch 行動。已結算檢定：骰值 96，結果為「失敗」。」沒有一句說她被打中。之後 60 回合也沒人提過她只剩 3 HP。

引擎其實有這句：`managed_checks._blow` 會產生「命中，Clara Bennett 受到 9 點傷害」，放在擲骰回覆和已結算事件的 `combat_receipt.blow` 裡。但經過 Luck 決定的防守骰是由後續敘事告訴玩家的，那裡唯一固定的句子只有「已結算檢定：骰值 N，結果為「…」」；Narrator 可以漏掉這一擊，也沒有任何檢查。重傷和 CON 檢定則根本不在 blow 裡。

## 變更

- **已結算檢定那行帶著這一擊。** `turn_delivery.finalize` 把 `combat_receipt.blow` 以「⚔️ …」接在「已結算檢定：骰值 N，結果為「…」。」後面，不管敘事怎麼寫，玩家都讀得到這一擊有沒有中、造成什麼。
- **blow 會說出傷勢。** `_blow` 結尾加上這一擊除了點數還造成什麼：「，倒地瀕死」、「，倒地昏迷」；重傷時若 CON 還要玩家自己擲是「——重傷，還要過 CON 檢定」，引擎已擲（autoroll 存在這一擊的傷害事件下）則是「——重傷，CON 檢定過了，仍站得住」／「——重傷，CON 檢定失敗，昏迷倒地」。

## 不變更

- Narrator 仍被要求敘述這一擊；固定句是底線，不是替代。
- 玩家按鈕自己擲的 CON 檢定照舊隨那次擲骰敘述（`_is_injury_roll`）。

## 驗證

`tests/test_combat_turn_friction.py`：閃避失敗、被 8 點的爪擊打中，擲骰回覆、已結算事件的 blow、後續的「已結算檢定」行都寫著「命中，調查員p1 受到 8 點傷害——重傷，還要過 CON 檢定」；傷勢尾句涵蓋瀕死、昏迷、無傷勢、CON 待擲、CON 過了、CON 失敗。
