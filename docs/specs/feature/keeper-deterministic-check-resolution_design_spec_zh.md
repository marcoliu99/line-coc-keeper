# 玩家擁有的確定性檢定

[English](keeper-deterministic-check-resolution_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. skill_check 與 sanity_check 通常建立 pending；/coc check 或擁有者按鈕才產生權威骰子，裸檢定命令不能創造不存在的請求。

2. 群組 autoroll_checks 預設 false，任一玩家可切換 /coc autoroll on|off；只影響新檢定，防禦選擇與預製角色 Luck 所有權仍須明確。

3. 只要合法 Luck 花費能改善要求的結果就提供選項，不限差一點成功；保留原骰並依識別只消耗一次決定。

4. 後續走統一 Supervisor；已結算骰子不能重骰，必要後果仍可使用受限唯讀、傷害與推進工具。

## 流程與介面

```text
Keeper 登記 -> 所有人／按鈕結算 -> 可選 Luck -> 儲存最終結果 -> Supervisor 後續處理
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/luck.py](../../../app/luck.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/keeper-deterministic-check-resolution_design_spec.md)
