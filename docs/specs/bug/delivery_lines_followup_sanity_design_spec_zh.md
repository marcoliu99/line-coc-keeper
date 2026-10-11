# 這一擊只說一次、Luck 提示附上骰值、結算行格式一致、後續回合能擲理智、查資料要有結果

[English](delivery_lines_followup_sanity_design_spec.md)

狀態：**已實作**。基底：`main_v2` `b0c11f3`。

## 問題

來自 b0c11f3 的 2026-10-12 soak：

- **⚔️ 出現兩次。** Lightless rerun3：25 次戰鬥結算都在擲骰卡（「後續結果由守密人處理中……⚔️ …」）顯示一次，敘事後的結算行又顯示一次。
- **Luck 提示沒有骰值。** Scritch Scratch rerun2：17 次 Luck 提示中有 11 次沒寫出骰值。敘事提到 `/coc luck skip`，系統那行「骰值 N 還在等 Luck 決定」就被拿掉，玩家只能盲選。
- **敘事和引擎結果矛盾。** Lightless 第 55 輪：引擎判定攻擊被閃開、「這一擊沒有命中」，敘事卻寫拳頭打中。
- **結算行兩種格式、技能名是英文。** 有「Rachel Ward 的偵查已結算：骰值 98，等級 失敗」和「已結算檢定：…」兩種寫法，也出現「的Mechanical Repair」「的SAN」。
- **檢定發現的恐怖景象不能擲理智。** Scritch Scratch 第 7 輪：偵查的後續回合呈現了懸掛的鼠屍，但後續回合不能呼叫 `sanity_check`，理智晚了 12 輪才擲。
- **查資料沒有下文、自創阻礙。** Scritch Scratch：去圖書館八次都停在「你開始尋找相關記錄」，沒擲骰也沒結果；「鑰匙與門鎖細節仍待向議會確認」出現八次，劇本裡沒有這回事。

## 改動

- **這一擊只說一次。** 擲骰卡已附上這一擊時，`managed_checks._feedback` 標記為已告知；結算行不再重複，敘事者仍拿得到。
- **沒寫出骰值就保留系統那行。** Luck 的 `InteractionRef.told_in` 只在敘事同時給了保留指令或按鈕、也寫出引擎的骰值時，才拿掉那行。
- **命中與否以引擎為準。** 敘事者的【這一擊的結果】規則改為：攻擊、閃避或反擊擲出成功，都不代表打中或躲開；寫「沒有命中」就寫落空，不可寫打中。
- **結算行一種格式、技能用中文。** 技能檢定的觀察行改用後續回合的格式「已結算檢定：X 的Y，骰值 N，結果為「…」。」。`presentation.skill_label` 把技能與屬性對應成角色卡的中文名（Mechanical Repair → 機械維修、SAN → 理智、INT → 智力），兩種結算行都套用。
- **後續回合可擲理智。** 已結算檢定的後續回合開放 `sanity_check`。義務閘會讀這回合的工具結果（後續回合敘事者的呼叫與 Executor 的一起），敘事者已擲過的理智規則不會再套一次。
- **兩條 prompt 規則（共用）。**
  - 查資料類行動這回合就要有結果：擲檢定，或說查到、沒查到什麼。
  - 劇本沒寫的限制就不存在，不要發明阻礙。

## 驗證

- `tests/test_combat_turn_friction.py`：結算行不再重複 ⚔️，敘事者仍拿得到這一擊。
- `tests/test_rerun8_player_facing.py`：有指令沒骰值的 Luck 回覆保留系統那行；寫出骰值就拿掉；花費點數不算骰值。
- `tests/test_sanity_and_check_lines.py` 與 `tests/test_sanity_witnesses_told_skill.py`：
  - 統一格式與中文名（偵查、智力、理智）；
  - 後續回合提供 `sanity_check`；
  - prompt 裡有查資料與阻礙兩條規則。
