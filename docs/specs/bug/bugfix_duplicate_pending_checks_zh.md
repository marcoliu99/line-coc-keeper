# 待處理檢定的所有權與重複防護

[English](bugfix_duplicate_pending_checks.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 建立調查員技能或 SAN 檢定前，必須同時檢查 pending_checks 與 pending_luck_decisions；不能以新擲骰悄悄覆蓋未完成決定。

2. 檢定屬於特定擁有者與時間線；重複指令或按鈕不得重複結算同一檢定，不符或過期識別必須拒絕。

3. 自動擲骰預設關閉；群組設定變更只影響新檢定，不能消耗既有待處理請求。

## 流程與介面

```text
行動 -> 現有檢定／Luck 檢查 -> 僅登記一次 -> 所有人結算 -> 儲存 -> 敘事
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)
- [tests/test_state_loss_amnesia.py](../../../tests/test_state_loss_amnesia.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/bugfix_duplicate_pending_checks.md)
