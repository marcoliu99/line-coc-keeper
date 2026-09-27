# 明確取消登記錯誤的檢定

[English](bug-self-corrected-check-leaves-stale-pending.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 口頭撤回不會移除 pending 狀態；必須透過授權 clear_pending_check 取消登記錯誤的檢定，才能替換或宣告無需檢定。

2. 取消不等於重骰，也不能當作跳過 Luck 的捷徑；保留已結算骰子，區分待檢定與待 Luck。

3. Python 交接驗證器以真實前後狀態與工具證據核對取消宣稱；不能用已完成狀態掩蓋殘留 pending。

## 流程與介面

```text
辨識錯誤待處理檢定 -> clear_pending_check -> 驗證實際狀態 -> 敘述更正
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-self-corrected-check-leaves-stale-pending.md)
