# 戰鬥卡、效果與權威傷害

[English](combat_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 參戰者保留穩定調查員識別與不同 NPC 顯示名稱；戰鬥狀態與對應角色 HP 必須同步。

2. 敵人卡依劇本證據包含護甲、攻擊與特殊能力；支援規則包括適用／穿透標籤、次數、觸發、條件、代價、冷卻與公開提示。

3. 支援的結構行動生命週期使用 plan_enemy_turn 與 resolve_enemy_action；內部原因、隱藏數值與能力身分不自動公開。

4. apply_combat_damage 接收原始傷害並套護甲一次；apply_final_combat_damage 接收已減免總額，治療與通用調整不得繞過傷害後果。

5. 符合重傷時依檢定所有權與 autoroll 登記／結算 CON；效果在定義的回合／輪次邊界依次數／冷卻狀態執行，不能靠臨場重複敘事。

6. 推進跳過倒下／暫離參戰者；無人可行動時，不能在尋找有效角色過程推進輪次或重複觸發輪次效果。

7. 近戰閃避／反擊與遠程防禦使用不同規則；不宣稱已具備通用擒抱／繳械／Build 比較引擎或完整雙玩家對抗流程。

## 流程與介面

```text
開始 -> 新增不同戰鬥者／卡片 -> DEX 先攻 -> 規劃／結算 -> 傷害／效果 -> 推進
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/combat.py](../../../app/combat.py)
- [app/models.py](../../../app/models.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/dice.py](../../../app/dice.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)
- [tests/test_dice_resolve_opposed.py](../../../tests/test_dice_resolve_opposed.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/combat_design_spec.md)
