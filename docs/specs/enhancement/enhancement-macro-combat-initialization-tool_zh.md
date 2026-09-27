# 批次戰鬥初始化提案

[English](enhancement-macro-combat-initialization-tool.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**待實作提案**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. initialize_combat 不是可用 runtime 工具；提案合併開戰與多敵人加入以减少模型往返。

2. 每個敵人須有不同玩家可見識別及完整劇本護甲／攻擊／能力；不能默默丟棄同名陣列項，後備編號須玩家可見。

3. 重用既有戰鬥、重複／別名／HP 檢查；DEX 先攻已存在，不能新增不相干的先攻擲骰機制。

4. 實作前決定全成全敗或部分失敗、可選欄位驗證及冪等；保留已開戰的單 NPC 增援路徑。

## 流程與介面

```text
提案：有依據的遭遇 -> initialize_combat(enemies) -> 既有開始／新增邏輯 -> 先攻
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/combat.py](../../../app/combat.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/c9c1930ee206c30db067f7dca425b57112f4414d/docs/specs/enhancement-macro-combat-initialization-tool.md)
