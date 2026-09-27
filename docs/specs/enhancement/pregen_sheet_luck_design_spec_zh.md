# 使用最終合併角色卡的可驗證 Luck

[English](pregen_sheet_luck_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 在來源協調後決定 Luck；最終卡片有效數值可避免不必要建角擲骰，缺少或未確認時仍由擁有者觸發。

2. 保留明確來源與手動更正；重匯 PDF 不得悄悄覆蓋已確認手動欄位或借用其他調查員數值。

3. PDF AI 修復刻意不把空白 Luck 當成讀不到的必要核心資料；其他未確認核心屬性可能只阻擋該張卡認領。

## 流程與介面

```text
來源角色卡 -> 依身分合併 -> 最終 Luck 依據 -> 認領且避免不必要重骰
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [tests/test_pregen_and_creation.py](../../../tests/test_pregen_and_creation.py)
- [tests/test_pregen_extra_fields.py](../../../tests/test_pregen_extra_fields.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/pregen_sheet_luck_design_spec.md)
