# 近戰防禦與遠程攻擊結算

[English](bug-dodge-counter-tie-and-ranged-mechanics.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 近戰閃避在成功等級相同時獲勝；反擊須嚴格高於攻擊方，因此同級由攻擊方勝；雙方失敗不能算命中。

2. 遠程攻擊不能套一般近戰閃避／反擊對抗；撲向掩護防禦依遠程專用結算與攻擊者懲罰規則處理。

3. UI 門檻與提示依防禦類型及攻擊結果決定；Luck 選項必須真能改善相關結果，不能提供無效成功等級。

4. 傷害只透過戰鬥服務套用一次；此 NPC／玩家路徑不等於一般雙玩家對抗引擎。

## 流程與介面

```text
NPC 攻擊 -> 近戰選擇／遠程防禦 -> 玩家檢定 -> 對抗或遠程規則 -> 傷害
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/dice.py](../../../app/dice.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_dice_resolve_opposed.py](../../../tests/test_dice_resolve_opposed.py)
- [tests/test_discord_defense_hint.py](../../../tests/test_discord_defense_hint.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-dodge-counter-tie-and-ranged-mechanics.md)
