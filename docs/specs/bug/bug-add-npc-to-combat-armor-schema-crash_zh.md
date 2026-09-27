# 戰鬥卡欄位的防禦式解析

[English](bug-add-npc-to-combat-armor-schema-crash.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 模型產生的護甲、攻擊與能力資料不能直接當作建構子 kwargs；先正規化支援別名，再只保留 dataclass 已知欄位。

2. 工具描述列出支援欄位；未知 key 不得造成未捕捉的 TypeError，阻止 NPC 加入戰鬥。

3. 此修正處理已撤回 PR62 回合推進調查的根因；重複推進是下游症狀，不能據此認定缺少提示詞規則。

## 流程與介面

```text
工具輸入 -> 正規化已知欄位 -> 戰鬥卡 -> 新增 NPC
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/models.py](../../../app/models.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_cards.py](../../../tests/test_combat_cards.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-add-npc-to-combat-armor-schema-crash.md)
