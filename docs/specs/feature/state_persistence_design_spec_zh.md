# 持久狀態、checkpoint 與範圍記憶

[English](state_persistence_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. SQLite 是群組狀態、角色鏡像與持久索引／記憶權威；頁圖與劇本庫產物仍是各自目錄設定的檔案資源。

2. 存檔保留版本與時間線識別；手動資產、更正封存等伴隨寫入須與對應群組狀態共同交易提交。

3. Checkpoint 與備份用途不同；還原必須使舊行動識別失效，防止舊模型工作或記憶跨時間線洩漏。

4. 預設備份每 60 分鐘保留 48 份、場景摘要每 12 回合維護；這些是設定預設，不證明某部署確實已有備份。

5. 歷史文字依正確範圍摘要／索引；目前待檢定、Luck、背包與戰鬥狀態仍優先於摘要及檢索記憶。

6. 已提交工具不能重套 state delta；敘事後續失敗仍保留效果，空白或失敗工具結果不證明有變更。

## 流程與介面

```text
讀取 SQLite 狀態 -> 檢查後變更 -> 交易式儲存／檢查點 -> 備份 -> 可選的新時間線還原
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/db.py](../../../app/db.py)
- [app/repositories/group_state.py](../../../app/repositories/group_state.py)
- [app/checkpoints.py](../../../app/checkpoints.py)
- [app/scene_digest.py](../../../app/scene_digest.py)
- [app/memory_rag.py](../../../app/memory_rag.py)
- [tests/test_state_persistence.py](../../../tests/test_state_persistence.py)
- [tests/test_state_loss_amnesia.py](../../../tests/test_state_loss_amnesia.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/state_persistence_design_spec.md)
