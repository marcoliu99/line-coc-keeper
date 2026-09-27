# NPC 攻擊延遲與確定性後果

[English](npc_attack_latency_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 在支援路徑合併攻擊登記與玩家防禦選項，同時保留權威攻擊骰、武器資料與 pending 識別。

2. 戰鬥中依既有 context 政策避免主動劇本 RAG，但必要時仍可明確搜尋；不等於刪除攻擊或能力的劇本查詢。

3. 近戰／遠程結算、Luck 與傷害遵守現行確定性規則；延遲優化不能靠省掉傷害、限制或待選擇來縮短回合。

4. 歷史 log 耗時是動機，不保證現行 provider 表現；須量測含工具後續與交付的完整回合耗時。

## 流程與介面

```text
NPC 攻擊工具 -> 權威攻擊／防禦狀態 -> 玩家決定 -> 確定性結算 -> 後續處理
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/dice.py](../../../app/dice.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/npc_attack_latency_design_spec.md)
