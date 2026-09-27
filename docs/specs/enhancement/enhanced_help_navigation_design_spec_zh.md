# 分類 Help 導覽

[English](enhanced_help_navigation_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 以單一 registry 管理描述、分類與可用性；文字 Help 與 Discord view 的命令名稱及角色限制應一致。

2. 可執行 Help 層現在處理表單、清單與確認；早期只顯示命令的設計已由該層取代。

3. 巢狀命令大小寫正規化一致；可讀參考文件是補充，不能成為第二套可執行命令 registry。

## 流程與介面

```text
指令登錄表 -> 依權限顯示分類頁 -> 詳情 -> 可執行操作
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/help_registry.py](../../../app/help_registry.py)
- [app/help_service.py](../../../app/help_service.py)
- [app/help_docs.py](../../../app/help_docs.py)
- [app/help_registration.py](../../../app/help_registration.py)
- [tests/test_help_navigation.py](../../../tests/test_help_navigation.py)
- [tests/test_help_text.py](../../../tests/test_help_text.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/enhanced_help_navigation_design_spec.md)
