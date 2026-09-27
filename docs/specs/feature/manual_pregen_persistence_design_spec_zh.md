# 跨遊戲保存手動角色卡

[English](manual_pregen_persistence_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 手動角色卡資產獨立於目前劇本候選池與活躍調查員保存；重開或切換遊戲不得刪除可重用手動卡。

2. 角色卡關聯劇本／來源識別；啟用劇本時安裝正確池，修正時保留適用認領，不能混入無關劇本候選。

3. 缺少可信來源的舊資產須保守遷移；來源變更可將合併卡標成過期並要求重匯，不能猜所屬劇本。

4. 資產擷取、候選池安裝與狀態變更以交易保存；手動屬性更正須在之後 PDF 重解析仍保留。

## 流程與介面

```text
role_ 匯入 -> 持久化手動資產 -> 劇本關聯 -> 候選池 -> 認領
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/repositories/manual_pregens.py](../../../app/repositories/manual_pregens.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/db.py](../../../app/db.py)
- [tests/test_manual_pregen_persistence.py](../../../tests/test_manual_pregen_persistence.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/manual_pregen_persistence_design_spec.md)
