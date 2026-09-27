# 提供所有合法且有效的 Luck 升級

[English](enhancement-luck-buyup-always-offered.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 移除歷史差距門檻；只要合法且付得起的選項能改變要求結果，就提供花費選擇。

2. 遵守要求難度、對抗防禦規則與 Luck 資格；不能提供升級後仍未達要求或禁止花 Luck 的選項。

3. 保留原骰、決定 ID、擁有者及時間線；花費／放棄須冪等，不能重骰原檢定。

## 流程與介面

```text
符合資格的已結算骰 -> 有用且負擔得起的等級 -> 所有人決定 -> 僅儲存一次
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/luck.py](../../../app/luck.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-luck-buyup-always-offered.md)
